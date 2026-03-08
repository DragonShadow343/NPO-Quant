import json
import os
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, File, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional

_MIME_MAP = {
    ".pdf":  "application/pdf",
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tiff": "image/tiff",
    ".tif":  "image/tiff",
    ".csv":  "text/plain",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

load_dotenv()

from ocr import extract_text
from ai import extract_data
from impact_math import route, calculate_carbon, SOCIAL_WAGE
from app.services.climatiq_service import estimate_emissions, estimate_vehicle_emissions
from app.services.carbon_service import get_summary as carbon_summary, aggregate_monthly as carbon_monthly
from app.services.accounting_service import aggregate as accounting_aggregate
from app.services.grants_service import compute_readiness, GRANT_CATEGORIES
from export import generate_pdf

app = FastAPI(title="NPO Quant API", version="2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_store: list[dict] = []
_UPLOADS_DIR = Path("_uploads")
_UPLOADS_DIR.mkdir(exist_ok=True)
_file_paths: dict[int, Path] = {}
_na_categories: dict[str, str] = {}

# Org-level configuration — set via POST /config or passed as query params
_org_config: dict = {
    "org_name":        "Your NPO Name Here",
    "registration_no": "BN / Charitable Registration No.: XXXXXXXXX RR0001",
    "address":         "Your Address, City, BC  V0V 0V0",
    "contact":         "contact@yournpo.ca  |  +1 (XXX) XXX-XXXX",
    "period_end":      "March 31, 2026",
    "fiscal_year":     "2026",
    "report_period":   "Q1 2026 — January 1 to March 31, 2026",
}

ALLOWED_EXTENSIONS = {".pdf", ".csv", ".png", ".jpg", ".jpeg", ".tiff", ".xlsx"}
_SCOPE_MAP = {"electricity": 2, "natural_gas": 1, "fuel": 1, "mileage": 1}

# Only image files can be sent to manual review (human gets a form)
_IMAGE_EXTS = {".jpg", ".jpeg", ".png"}

# All handled doc types (non-image files never go to manual review for these)
_KNOWN_TYPES = {
    "electricity", "natural_gas", "fuel", "mileage",
    "volunteers", "grant", "goods_donated", "payroll",
    "disbursement_voucher",
    "carbon_summary", "accounting_summary", "grant_applications",
}

DASHBOARD_PATH = Path("dashboard_data.json")


# ── Dashboard data builder ──────────────────────────────────────────────────────

def build_dashboard_data() -> dict:
    """
    Aggregate all approved store items into dashboard-ready JSON.
    Uses each item's `modules` dict so that a single document (e.g. an
    electricity bill) contributes to BOTH the carbon AND accounting streams.
    """
    approved = [r for r in _store if r.get("status") == "approved"]

    carbon_by_period: dict     = defaultdict(lambda: {"total_emissions": 0.0, "composition": defaultdict(float)})
    accounting_by_period: dict = defaultdict(lambda: {"inflow": 0.0, "expense": 0.0})
    category_totals: dict      = defaultdict(float)

    for r in approved:
        mods = r.get("modules") or {}

        # ── CARBON module ─────────────────────────────────────────────────
        # Pull the actual computed kg_co2e (set by climatiq / impact_math)
        # but use the module's period/doc_type for bucketing.
        carbon_mod = mods.get("carbon")
        if carbon_mod:
            period   = _clean_period(carbon_mod.get("period") or (r.get("raw") or {}).get("period"))
            doc_type = carbon_mod.get("doc_type", "other")

            if doc_type == "carbon_summary":
                # Comprehensive multi-category carbon CSV: use component breakdown
                components = carbon_mod.get("components") or {}
                total_co2e = carbon_mod.get("amount", 0) or r.get("kg_co2e") or 0.0
                if components:
                    for comp_key, comp_kg in components.items():
                        carbon_by_period[period]["total_emissions"] += comp_kg
                        carbon_by_period[period]["composition"][comp_key] += comp_kg
                elif total_co2e:
                    carbon_by_period[period]["total_emissions"] += total_co2e
                    carbon_by_period[period]["composition"]["other"] += total_co2e
            elif r.get("kg_co2e") is not None:
                kg       = r.get("kg_co2e") or 0.0
                comp_key = doc_type if doc_type in ("fuel", "electricity", "natural_gas", "mileage") else "other"
                carbon_by_period[period]["total_emissions"] += kg
                carbon_by_period[period]["composition"][comp_key] += kg

        # ── ACCOUNTING module ─────────────────────────────────────────────
        # Covers utilities, fleet, grants, donations, payroll, volunteers —
        # extracted from every doc type that has accounting data.
        acct_mod = mods.get("accounting")
        if acct_mod:
            period    = _clean_period(acct_mod.get("period"))
            flow_type = acct_mod.get("flow_type", "expense")
            category  = acct_mod.get("category", "Other")

            if flow_type == "summary":
                # Comprehensive monthly summary: has both inflow and expense
                accounting_by_period[period]["inflow"]  += acct_mod.get("total_inflow",  0.0)
                accounting_by_period[period]["expense"] += acct_mod.get("total_expense", 0.0)
                if acct_mod.get("total_inflow", 0) > 0:
                    category_totals[category] += acct_mod.get("total_inflow", 0)
            else:
                amount = acct_mod.get("amount_dollars") or 0.0
                if flow_type in ("inflow", "social_value"):
                    accounting_by_period[period]["inflow"] += amount
                else:
                    accounting_by_period[period]["expense"] += amount
                if amount > 0:
                    category_totals[category] += amount

        # ── GRANTS module ─────────────────────────────────────────────────
        # Grants readiness is computed separately by compute_readiness().
        # Nothing to aggregate here — it's a score, not a sum.

    # Build carbon list
    carbon_list = []
    for month, data in sorted(carbon_by_period.items()):
        carbon_list.append({
            "month":           month,
            "total_emissions": round(data["total_emissions"], 3),
            "composition":     {k: round(v, 3) for k, v in data["composition"].items() if v > 0},
        })

    # Build accounting list
    accounting_list = []
    for month, data in sorted(accounting_by_period.items()):
        inflow  = round(data["inflow"], 2)
        expense = round(data["expense"], 2)
        accounting_list.append({
            "month":       month,
            "inflow":      inflow,
            "expense":     expense,
            "profit_loss": round(inflow - expense, 2),
        })

    # Grants readiness score
    grants_score = compute_readiness(approved, _na_categories)
    grants_list  = [{"month": "Overall", "eligibility_percent": grants_score["score"],
                     "breakdown": grants_score.get("breakdown", [])}]

    return {
        "carbon":          carbon_list,
        "accounting":      accounting_list,
        "category_totals": {k: round(v, 2) for k, v in category_totals.items() if v > 0},
        "grants":          grants_list,
        "grants_detail":   grants_score,
        "last_updated":    datetime.now().isoformat(),
        "total_approved":  len(approved),
    }


def _clean_period(period: str | None) -> str:
    """Normalise period strings; replace blanks/sentinel values with 'Unknown'."""
    if not period or period in ("Unknown Period", "Unknown", ""):
        return "Unknown"
    return period


def save_dashboard_data():
    """Write dashboard_data.json to disk."""
    try:
        data = build_dashboard_data()
        with open(DASHBOARD_PATH, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[dashboard] save failed: {e}")


# ── Upload ─────────────────────────────────────────────────────────────────────

@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    ext      = os.path.splitext(file.filename)[1].lower()
    is_image = ext in _IMAGE_EXTS

    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Unsupported file type: {ext}")

    # If this filename was already uploaded, remove the previous entry
    # so re-uploads replace rather than duplicate.
    _store[:] = [r for r in _store if r.get("filename") != file.filename]
    item_id = len(_store)
    tmp = f"_tmp_{item_id}_{file.filename}"
    try:
        with open(tmp, "wb") as fout:
            shutil.copyfileobj(file.file, fout)
        upload_path = _UPLOADS_DIR / f"{item_id}_{file.filename}"
        shutil.copy(tmp, upload_path)
        _file_paths[item_id] = upload_path
        raw_text  = extract_text(tmp)
        extracted = extract_data(raw_text, filename=file.filename)
    except Exception as e:
        extracted = {
            "type": "unknown", "amount": 0, "unit": "", "period": "Unknown",
            "_confidence": "low", "_error": str(e),
        }
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

    confidence = extracted.pop("_confidence", "medium")
    modules    = extracted.pop("_modules", {})
    extracted.pop("_error", None)
    doc_type   = extracted.get("type", "unknown")

    # ── Flagging rules ─────────────────────────────────────────────────────
    # Image files: flag for manual review ONLY if type genuinely unknown.
    #   With a clear filename classification the AI auto-processes the doc
    #   even if OCR confidence is low — human still approves/rejects it.
    # Non-image files: NEVER flag — auto-process always.
    needs_review = is_image and (doc_type == "unknown" or doc_type is None)
    if not is_image and doc_type not in _KNOWN_TYPES:
        # Unknown non-image → show scan-error card (still flagged but no form)
        needs_review = True

    # ── Helper: base fields shared by all result dicts ───────────────────
    def _base(category, doc_t, raw_val, kg=None, scope_val=None,
              fallback=False, cv=None, vols=None, hrs=None):
        return {
            "filename":                file.filename,
            "id":                      item_id,
            "category":                category,
            "doc_type":                doc_t,
            "scope":                   scope_val,
            "raw":                     raw_val,
            "kg_co2e":                 kg,
            "status":                  "pending_approval",
            "needs_manual_review":     False,
            "confidence":              confidence,
            "climatiq_fallback":       fallback,
            "community_value_dollars": cv,
            "total_volunteers":        vols,
            "total_hours":             hrs,
            "modules":                 modules,   # multi-module assignments
        }

    if needs_review:
        result = {
            "filename":            file.filename,
            "id":                  item_id,
            "category":            "unknown",
            "doc_type":            "unknown",
            "status":              "needs_manual_review",
            "needs_manual_review": True,
            "raw_extracted":       extracted,
            "raw":                 None,
            "kg_co2e":             None,
            "scope":               None,
            "confidence":          confidence,
            "community_value_dollars": None,
            "total_volunteers":    None,
            "total_hours":         None,
            "modules":             modules,
        }
        _store.append(result)
        save_dashboard_data()
        return result

    # ── Volunteer log ──────────────────────────────────────────────────────
    if "volunteers" in extracted:
        r = route(extracted)
        result = {
            "filename":            file.filename,
            "id":                  item_id,
            "status":              "pending_approval",
            "needs_manual_review": False,
            "confidence":          confidence,
            "modules":             modules,
            **r,
        }
        _store.append(result)
        save_dashboard_data()
        return result

    # ── Grant revenue ──────────────────────────────────────────────────────
    if doc_type == "grant":
        result = _base("accounting", "grant", extracted)
        _store.append(result)
        save_dashboard_data()
        return result

    # ── Goods donated ──────────────────────────────────────────────────────
    if doc_type == "goods_donated":
        result = _base("accounting", "goods_donated", extracted)
        _store.append(result)
        save_dashboard_data()
        return result

    # ── Payroll ────────────────────────────────────────────────────────────
    if doc_type == "payroll":
        result = _base("accounting", "payroll", extracted)
        _store.append(result)
        save_dashboard_data()
        return result

    # ── Mileage ────────────────────────────────────────────────────────────
    if doc_type == "mileage":
        distance_km = extracted.get("amount", 0)
        try:
            cq      = estimate_vehicle_emissions(distance_km)
            kg_co2e = cq.get("co2e", 0.0)
            if cq.get("co2e_unit") == "t":
                kg_co2e *= 1000
            fallback = False
        except Exception:
            kg_co2e  = 0.0
            fallback = True
        if kg_co2e == 0 and distance_km > 0:
            from impact_math import BC_VEHICLE_FACTOR
            kg_co2e  = round(distance_km * BC_VEHICLE_FACTOR, 3)
            fallback = True
        result = _base("carbon", "mileage", extracted,
                       kg=round(kg_co2e, 3), scope_val=1, fallback=fallback)
        _store.append(result)
        save_dashboard_data()
        return result

    # ── Carbon summary CSV (multi-month carbon inventory) ────────────────
    if doc_type == "carbon_summary":
        total_co2e = extracted.get("amount", 0)
        result = _base("carbon", "carbon_summary", extracted,
                       kg=round(total_co2e, 3),
                       scope_val=None, fallback=True)
        _store.append(result)
        save_dashboard_data()
        return result

    # ── Accounting summary CSV (monthly financial overview) ───────────────
    if doc_type == "accounting_summary":
        result = _base("accounting", "accounting_summary", extracted)
        _store.append(result)
        save_dashboard_data()
        return result

    # ── Disbursement voucher — auto-process for non-image files ───────────
    if doc_type == "disbursement_voucher":
        items      = extracted.get("items", [])
        total_cost = sum((i.get("amount_dollars") or 0) for i in items)
        period     = (items[0].get("date") if items else None) or "Unknown"
        raw_dv = {
            "type": "disbursement_voucher", "amount": total_cost,
            "unit": "CAD", "period": period, "cost_dollars": total_cost,
            "items": items, "grant_code": extracted.get("grant_code"),
            "project": extracted.get("project"),
        }
        result = _base("accounting", "disbursement_voucher", raw_dv)
        _store.append(result)
        save_dashboard_data()
        return result

    # ── Carbon document (electricity / natural_gas / fuel) ────────────────
    cq = estimate_emissions(doc_type=doc_type,
                            amount=extracted.get("amount", 0),
                            unit=extracted.get("unit", ""))
    kg_co2e = cq.get("co2e", 0.0)
    if cq.get("co2e_unit") == "t":
        kg_co2e *= 1000
    if kg_co2e == 0 and extracted.get("amount", 0) > 0:
        local    = calculate_carbon(extracted)
        kg_co2e  = local.get("kg_co2e", 0.0)
        cq["_fallback"] = True

    result = _base("carbon", doc_type, extracted,
                   kg=round(kg_co2e, 3),
                   scope_val=_SCOPE_MAP.get(doc_type, 2),
                   fallback=cq.get("_fallback", False))
    _store.append(result)
    save_dashboard_data()
    return result


# ── Manual entry ───────────────────────────────────────────────────────────────

class ManualEntry(BaseModel):
    doc_type:     str
    amount:       float
    unit:         str
    period:       str
    provider:     Optional[str]   = ""
    cost_dollars: Optional[float] = None
    volunteers:   Optional[int]   = None
    total_hours:  Optional[float] = None

@app.post("/manual-entry/{item_id}")
def manual_entry(item_id: int, entry: ManualEntry):
    if item_id < 0 or item_id >= len(_store):
        raise HTTPException(404, "Item not found")
    item = _store[item_id]

    from ai import _assign_modules as _am
    if entry.doc_type == "social":
        hours = entry.total_hours or 0
        synth = {"type": "volunteers", "volunteers": [{"hours": hours}],
                 "period": entry.period, "cost_dollars": 0}
        item.update({
            "category":                "social",
            "doc_type":                "volunteers",
            "total_volunteers":        entry.volunteers or 1,
            "total_hours":             hours,
            "community_value_dollars": round(hours * SOCIAL_WAGE, 2),
            "kg_co2e":                 None,
            "scope":                   None,
            "raw":                     {"type": "social", "amount": hours, "unit": "hrs", "period": entry.period},
            "status":                  "pending_approval",
            "needs_manual_review":     False,
            "modules":                 _am(synth),
        })
    else:
        extracted = {
            "type": entry.doc_type, "amount": entry.amount,
            "unit": entry.unit,     "period": entry.period,
            "cost_dollars": entry.cost_dollars,
        }
        r = route(extracted)
        item.update({**r, "status": "pending_approval",
                     "needs_manual_review": False,
                     "modules": _am(extracted)})

    return item


# ── Approve / Reject ───────────────────────────────────────────────────────────

@app.post("/approve/{item_id}")
def approve_item(item_id: int):
    if item_id < 0 or item_id >= len(_store):
        raise HTTPException(404, "Item not found")
    if _store[item_id].get("needs_manual_review"):
        raise HTTPException(400, "Complete manual entry before approving")
    _store[item_id]["status"] = "approved"
    save_dashboard_data()
    return {"ok": True}

@app.post("/reject/{item_id}")
def reject_item(item_id: int):
    if item_id < 0 or item_id >= len(_store):
        raise HTTPException(404, "Item not found")
    _store[item_id]["status"] = "rejected"
    save_dashboard_data()
    return {"ok": True}


# ── Results ────────────────────────────────────────────────────────────────────

@app.get("/results")
def get_results():
    return _store


# ── Dashboard data ─────────────────────────────────────────────────────────────

@app.get("/dashboard-data")
def get_dashboard_data():
    """Return the aggregated dashboard data (for chart widgets)."""
    return build_dashboard_data()


# ── Summary ────────────────────────────────────────────────────────────────────

@app.get("/summary")
def summary(unrestricted_net_assets: Optional[float] = None):
    approved = _deduplicate([r for r in _store if r.get("status") == "approved"])
    n        = len(approved)
    high_c   = sum(1 for r in approved if r.get("confidence") == "high")
    med_c    = sum(1 for r in approved if r.get("confidence") == "medium")
    conf_pct = round(((high_c + med_c) / n * 100) if n else 0, 1)

    return {
        "carbon":           carbon_summary(approved),
        "carbon_monthly":   carbon_monthly(approved),
        "accounting":       accounting_aggregate(approved, unrestricted_net_assets),
        "grants":           compute_readiness(approved, _na_categories),
        "confidence_score": conf_pct,
        "approved_count":   n,
        "flagged_count":    sum(1 for r in _store if r.get("needs_manual_review")),
        "pending_count":    sum(1 for r in _store if r.get("status") == "pending_approval"),
    }


# ── Grant readiness ────────────────────────────────────────────────────────────

@app.get("/grant-readiness")
def grant_readiness():
    approved = [r for r in _store if r.get("status") == "approved"]
    return compute_readiness(approved, _na_categories)

class NAEntry(BaseModel):
    reason: str = "Not applicable to this organisation's operations"

@app.post("/grant-readiness/not-applicable/{category}")
def set_not_applicable(category: str, entry: NAEntry):
    if category not in GRANT_CATEGORIES:
        raise HTTPException(400, f"Valid categories: {list(GRANT_CATEGORIES)}")
    _na_categories[category] = entry.reason
    approved = [r for r in _store if r.get("status") == "approved"]
    return compute_readiness(approved, _na_categories)

@app.delete("/grant-readiness/not-applicable/{category}")
def unset_not_applicable(category: str):
    _na_categories.pop(category, None)
    approved = [r for r in _store if r.get("status") == "approved"]
    return compute_readiness(approved, _na_categories)


# ── PDF Export ─────────────────────────────────────────────────────────────────

def _deduplicate(items: list) -> list:
    """Keep only the most-recently-uploaded version of each file (by filename)."""
    seen: dict = {}
    for r in items:
        key = r.get("filename", r.get("id", id(r)))
        if key not in seen or (r.get("id") or 0) > (seen[key].get("id") or 0):
            seen[key] = r
    return list(seen.values())


@app.get("/export/pdf")
def export_pdf():
    import export as _export_mod
    _export_mod.ORG_NAME      = _org_config.get("org_name",        "Your NPO Name Here")
    _export_mod.ORG_REG       = _org_config.get("registration_no", "BN / Charitable Registration No.: XXXXXXXXX RR0001")
    _export_mod.ORG_ADDRESS   = _org_config.get("address",         "Your Address, City, BC  V0V 0V0")
    _export_mod.REPORT_PERIOD = _org_config.get("report_period",   "Q1 2026 — January 1 to March 31, 2026")
    approved = _deduplicate([r for r in _store if r.get("status") == "approved"])
    n        = len(approved)
    high_c   = sum(1 for r in approved if r.get("confidence") == "high")
    med_c    = sum(1 for r in approved if r.get("confidence") == "medium")

    carbon  = carbon_summary(approved)
    summary = {
        **carbon,
        "confidence_score":      round(((high_c + med_c) / n * 100) if n else 0, 1),
        "high_conf_count":       high_c,
        "med_conf_count":        med_c,
        "total_community_value": round(sum(
            (r.get("community_value_dollars") or 0) for r in approved
            if r.get("category") == "social" or r.get("doc_type") == "volunteers"
        ), 2),
        "approved_count":        n,
        "annual_revenue_cad":    None,
        "floor_area_m2":         None,
    }
    path = generate_pdf(summary, approved)
    return FileResponse(path, media_type="application/pdf",
                        filename="NPOQuant_Carbon_Report.pdf")


# ── Preview ────────────────────────────────────────────────────────────────────

@app.get("/preview/{item_id}")
def preview_file(item_id: int):
    saved = _file_paths.get(item_id)
    if saved is None or not saved.exists():
        raise HTTPException(404, "Preview not available for this item")
    ext       = saved.suffix.lower()
    mime_type = _MIME_MAP.get(ext, "application/octet-stream")
    filename  = saved.name.split("_", 1)[-1]
    return FileResponse(
        str(saved),
        media_type=mime_type,
        filename=filename,
        headers={"Content-Disposition": "inline"},
    )


# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {
        "status":  "ok",
        "items":   len(_store),
        "flagged": sum(1 for r in _store if r.get("needs_manual_review")),
    }


@app.get("/config")
def get_config():
    """Return current organisation configuration."""
    return _org_config


@app.post("/config")
def set_config(
    org_name:        Optional[str] = None,
    registration_no: Optional[str] = None,
    address:         Optional[str] = None,
    contact:         Optional[str] = None,
    period_end:      Optional[str] = None,
    fiscal_year:     Optional[str] = None,
    report_period:   Optional[str] = None,
):
    """Update organisation-level configuration (persists for the server session)."""
    import export as _export_mod
    if org_name        is not None: _org_config["org_name"]        = org_name;        _export_mod.ORG_NAME      = org_name
    if registration_no is not None: _org_config["registration_no"] = registration_no; _export_mod.ORG_REG       = registration_no
    if address         is not None: _org_config["address"]         = address;         _export_mod.ORG_ADDRESS   = address
    if contact         is not None: _org_config["contact"]         = contact;         _export_mod.ORG_CONTACT   = contact
    if period_end      is not None: _org_config["period_end"]      = period_end
    if fiscal_year     is not None: _org_config["fiscal_year"]     = fiscal_year
    if report_period   is not None: _org_config["report_period"]   = report_period;   _export_mod.REPORT_PERIOD = report_period
    return _org_config


# ── Financial Statements Export ────────────────────────────────────────────────

@app.get("/export/grants")
def export_grants_pdf(
    org_name:        Optional[str] = None,
    period_end:      Optional[str] = None,
    registration_no: Optional[str] = None,
):
    import export as _export_mod
    # Apply org config overrides before PDF generation
    _export_mod.ORG_NAME      = org_name        or _org_config.get("org_name",        "Your NPO Name Here")
    _export_mod.REPORT_PERIOD = _org_config.get("report_period", "Q1 2026 — January 1 to March 31, 2026")
    from app.services.grants_service import compute_readiness
    from export import generate_grant_readiness_pdf
    approved  = _deduplicate([r for r in _store if r.get("status") == "approved"])
    readiness = compute_readiness(approved, _na_categories)
    path = generate_grant_readiness_pdf(readiness, approved)
    return FileResponse(path, media_type="application/pdf",
                        filename="NPOQuant_Grant_Readiness.pdf")


@app.get("/export/financial-statements")
def export_financial_statements(
    unrestricted_net_assets: Optional[float] = None,
    org_name:        Optional[str] = None,
    period_end:      Optional[str] = None,
    fiscal_year:     Optional[str] = None,
    registration_no: Optional[str] = None,
):
    from app.services.accounting_service import aggregate as accounting_aggregate
    approved    = _deduplicate([r for r in _store if r.get("status") == "approved"])
    accounting  = accounting_aggregate(approved, unrestricted_net_assets)
    org_config  = {
        "org_name":        org_name        or _org_config.get("org_name",        "Your NPO Name Here"),
        "period_end":      period_end      or _org_config.get("period_end",      "March 31, 2026"),
        "fiscal_year":     fiscal_year     or _org_config.get("fiscal_year",     "2026"),
        "registration_no": registration_no or _org_config.get("registration_no", ""),
    }
    from export import generate_financial_statements_pdf
    path = generate_financial_statements_pdf(accounting, org_config)
    return FileResponse(path, media_type="application/pdf",
                        filename="NPOQuant_Financial_Statements.pdf")
