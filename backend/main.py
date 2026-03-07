import os
import shutil
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

load_dotenv()

from ocr import extract_text
from ai import extract_data
from impact_math import calculate_carbon, SOCIAL_WAGE
from app.services.climatiq_service import estimate_emissions
from export import generate_pdf

app = FastAPI(title="NPO Quant API", version="1.0")
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

ALLOWED_EXTENSIONS = {".pdf", ".csv", ".png", ".jpg", ".jpeg", ".tiff", ".xlsx"}

_SCOPE_MAP = {"electricity": 2, "natural_gas": 1, "fuel": 1}


@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Unsupported file type: {ext}")

    item_id = len(_store)
    tmp     = f"_tmp_{item_id}_{file.filename}"
    try:
        with open(tmp, "wb") as fout:
            shutil.copyfileobj(file.file, fout)
        upload_path = _UPLOADS_DIR / f"{item_id}_{file.filename}"
        shutil.copy(tmp, upload_path)
        _file_paths[item_id] = upload_path
        raw_text  = extract_text(tmp)
        extracted = extract_data(raw_text)
    except Exception as e:
        extracted = {
            "type": "unknown", "amount": 0, "unit": "", "period": "Unknown",
            "_confidence": "low", "_error": str(e),
        }
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

    confidence = extracted.pop("_confidence", "medium")
    extracted.pop("_error", None)
    doc_type   = extracted.get("type", "unknown")

    if confidence == "low" or doc_type == "unknown":
        result = {
            "filename":            file.filename,
            "id":                  item_id,
            "category":            "unknown",
            "status":              "needs_manual_review",
            "needs_manual_review": True,
            "raw_extracted":       extracted,
            "kg_co2e":             None,
            "confidence":          confidence,
        }
        _store.append(result)
        return result

    # ── Volunteer log ─────────────────────────────────────────────────────────
    if "volunteers" in extracted:
        volunteers   = extracted.get("volunteers", [])
        total_hours  = round(sum(v.get("hours", 0) for v in volunteers), 2)
        result = {
            "filename":                file.filename,
            "id":                      item_id,
            "category":                "social",
            "status":                  "pending_approval",
            "needs_manual_review":     False,
            "confidence":              confidence,
            "raw":                     extracted,
            "kg_co2e":                 None,
            "total_volunteers":        len(volunteers),
            "total_hours":             total_hours,
            "community_value_dollars": round(total_hours * SOCIAL_WAGE, 2),
        }
        _store.append(result)
        return result

    # ── Carbon document — try Climatiq, fall back to local ECCC factors ───────
    climatiq_result = estimate_emissions(
        doc_type=doc_type,
        amount=extracted.get("amount", 0),
        unit=extracted.get("unit", ""),
    )

    kg_co2e = climatiq_result.get("co2e", 0.0)
    if climatiq_result.get("co2e_unit") == "t":
        kg_co2e *= 1000

    # If Climatiq returned 0 (API miss + unknown type), use local ECCC factor
    if kg_co2e == 0 and extracted.get("amount", 0) > 0:
        local = calculate_carbon(extracted)
        kg_co2e = local.get("kg_co2e", 0.0)
        climatiq_result["_fallback"] = True

    result = {
        "filename":           file.filename,
        "id":                 item_id,
        "category":           "carbon",
        "doc_type":           doc_type,
        "scope":              _SCOPE_MAP.get(doc_type, 2),
        "raw":                extracted,
        "kg_co2e":            round(kg_co2e, 3),
        "status":             "pending_approval",
        "needs_manual_review": False,
        "confidence":         confidence,
        "climatiq_fallback":  climatiq_result.get("_fallback", False),
    }
    _store.append(result)
    return result


@app.post("/approve/{item_id}")
def approve_item(item_id: int):
    if item_id < 0 or item_id >= len(_store):
        raise HTTPException(404, "Item not found")
    if _store[item_id].get("needs_manual_review"):
        raise HTTPException(400, "Item needs manual entry before approval")
    _store[item_id]["status"] = "approved"
    return {"ok": True}


@app.get("/results")
def get_results():
    return _store


@app.get("/export/pdf")
def export_pdf():
    approved = [r for r in _store if r.get("status") == "approved"]

    total_kg = round(sum(r.get("kg_co2e") or 0 for r in approved), 3)
    scope1   = round(sum(r.get("kg_co2e") or 0 for r in approved if r.get("scope") == 1), 3)
    scope2   = round(sum(r.get("kg_co2e") or 0 for r in approved if r.get("scope") == 2), 3)

    composition: dict[str, float] = {}
    for r in approved:
        raw = r.get("raw") or {}
        dt  = raw.get("type", r.get("doc_type", "unknown"))
        composition[dt] = round(composition.get(dt, 0.0) + (r.get("kg_co2e") or 0), 3)

    n        = len(approved)
    high_c   = sum(1 for r in approved if r.get("confidence") == "high")
    med_c    = sum(1 for r in approved if r.get("confidence") == "medium")
    conf_pct = round(((high_c + med_c) / n * 100) if n else 0, 1)

    summary = {
        "total_kg_co2e":         total_kg,
        "scope1_kg_co2e":        scope1,
        "scope2_kg_co2e":        scope2,
        "composition":           composition,
        "confidence_score":      conf_pct,
        "high_conf_count":       high_c,
        "med_conf_count":        med_c,
        "total_community_value": round(sum(r.get("community_value_dollars") or 0 for r in approved), 2),
        "approved_count":        n,
        "annual_revenue_cad":    None,
        "floor_area_m2":         None,
    }

    path = generate_pdf(summary, approved)
    return FileResponse(path, media_type="application/pdf",
                        filename="NPOQuant_Carbon_Report.pdf")


@app.get("/health")
def health():
    return {
        "status":  "ok",
        "items":   len(_store),
        "flagged": sum(1 for r in _store if r.get("needs_manual_review")),
    }