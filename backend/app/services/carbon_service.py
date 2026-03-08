import os
from collections import defaultdict
from typing import List

SBTi_ANNUAL_REDUCTION = 0.042   # 4.2%/yr — Science Based Targets initiative

EMISSION_CATEGORIES = ["fuel", "electricity", "natural_gas", "mileage", "travel_scope3"]

# Sanity limits to avoid OCR errors, annual-vs-monthly mix-ups, or test data inflating the dashboard.
# Set via env: CARBON_MAX_KG_PER_DOCUMENT (per-document cap), CARBON_MAX_KG_PER_MONTH (monthly total cap).
# Use 0 or unset to disable a cap. Default per-doc 800 kg keeps typical single bills; 1250+ gets capped.
def _carbon_cap_per_doc() -> float | None:
    raw = os.environ.get("CARBON_MAX_KG_PER_DOCUMENT", "").strip()
    if raw == "":
        return 800.0  # default: cap any single document at 800 kg CO2e per month
    try:
        v = float(raw)
        return v if v > 0 else None
    except ValueError:
        return None

def _carbon_cap_per_month() -> float | None:
    raw = os.environ.get("CARBON_MAX_KG_PER_MONTH", "").strip()
    if raw == "":
        return None  # no default monthly cap
    try:
        v = float(raw)
        return v if v > 0 else None
    except ValueError:
        return None


# Components that contribute to Scope 1 (direct combustion)
_SCOPE1_COMPONENTS = {"fuel", "natural_gas", "mileage", "travel_scope3"}
# Components that contribute to Scope 2 (purchased electricity)
_SCOPE2_COMPONENTS = {"electricity"}


def get_summary(approved: List[dict]) -> dict:
    """
    Returns current emissions, SBTi target, scope split, and composition.
    Used by /summary endpoint and export/pdf.

    carbon_summary CSV items store their breakdown in raw.components so we
    decompose those into Scope 1 / Scope 2 proportionally.
    """
    carbon_items = [r for r in approved if r.get("category") == "carbon"]

    total   = round(sum(r.get("kg_co2e") or 0 for r in carbon_items), 3)
    target  = round(total * (1 - SBTi_ANNUAL_REDUCTION), 3)

    scope1 = 0.0
    scope2 = 0.0
    composition: dict = {}

    for r in carbon_items:
        raw     = r.get("raw") or {}
        dt      = raw.get("type", r.get("doc_type", "unknown"))
        kg      = r.get("kg_co2e") or 0
        scope_v = r.get("scope")

        # ── Standard single-scope items ─────────────────────────────────────
        if scope_v == 1:
            scope1 += kg
        elif scope_v == 2:
            scope2 += kg
        elif dt == "carbon_summary":
            # carbon_summary CSV: components dict stores kg per fuel type
            components = raw.get("components", {})
            if components:
                for comp, val in components.items():
                    if comp in _SCOPE1_COMPONENTS:
                        scope1 += val or 0
                    elif comp in _SCOPE2_COMPONENTS:
                        scope2 += val or 0
            else:
                # Fallback: treat entire value as Scope 1 (direct fuels dominate)
                scope1 += kg

        # ── Composition breakdown ────────────────────────────────────────────
        if dt == "carbon_summary":
            components = raw.get("components", {})
            if components:
                for comp, val in components.items():
                    composition[comp] = round(composition.get(comp, 0.0) + (val or 0), 3)
            else:
                composition[dt] = round(composition.get(dt, 0.0) + kg, 3)
        else:
            composition[dt] = round(composition.get(dt, 0.0) + kg, 3)

    return {
        "current_emissions_kg": total,
        "target_emissions_kg":  target,
        "scope1_kg":            round(scope1, 3),
        "scope2_kg":            round(scope2, 3),
        "composition":          composition,
    }


def aggregate_monthly(approved: List[dict]) -> List[dict]:
    """
    Normalize periods, aggregate carbon emissions, and produce frontend-ready monthly data.
    Applies sanity caps so extreme values (OCR errors, annual-vs-monthly mix-ups, test data)
    do not dominate the dashboard:
    - CARBON_MAX_KG_PER_DOCUMENT: cap per-document contribution (default 800). Set to 0 to disable.
    - CARBON_MAX_KG_PER_MONTH: cap total kg CO2e per month across all documents. Unset = no cap.
    """
    from datetime import datetime
    import re

    monthly = defaultdict(lambda: {cat: 0.0 for cat in EMISSION_CATEGORIES})

    for r in approved:
        if r.get("category") != "carbon":
            continue
        raw = r.get("raw") or {}

        # --- Normalize month/period ---
        month = raw.get("period", r.get("period", "Unknown"))
        if isinstance(month, str):
            month = month.strip()

            # Convert year-only values like "2023" → "2023-01"
            if re.fullmatch(r"\d{4}", month):
                month = f"{month}-01"
            else:
                # Convert textual month "April 2024" → "2024-04"
                try:
                    dt = datetime.strptime(month, "%B %Y")
                    month = dt.strftime("%Y-%m")
                except ValueError:
                    # Attempt abbreviated month "Dec 2023" → "2023-12"
                    try:
                        dt = datetime.strptime(month, "%b %Y")
                        month = dt.strftime("%Y-%m")
                    except ValueError:
                        pass  # leave as-is if unknown

        # --- Determine category ---
        cat = raw.get("type", r.get("doc_type", "unknown"))
        if cat not in EMISSION_CATEGORIES:
            cat = "fuel"  # fallback

        # --- Get emissions value ---
        val = float(r.get("kg_co2e") or 0)
        if val <= 0:
            continue

        # Sanity cap: no single document can contribute more than cap (avoids OCR/annual mix-up/test data)
        cap_doc = _carbon_cap_per_doc()
        if cap_doc is not None and val > cap_doc:
            val = cap_doc
        # Aggregate
        monthly[month][cat] += val

    # --- Build frontend-ready list, remove zero totals ---
    cap_month = _carbon_cap_per_month()
    result = []
    for month_key in sorted(monthly.keys()):
        vals = monthly[month_key]
        total_emissions = sum(vals.values())
        if total_emissions <= 0:
            continue
        # Optional monthly total cap (second line of defense)
        if cap_month is not None and total_emissions > cap_month:
            scale = cap_month / total_emissions
            total_emissions = cap_month
            vals = {k: v * scale for k, v in vals.items()}
        result.append({
            "month": month_key,
            "total_emissions": round(total_emissions, 3),
            "composition": {k: round(v, 3) for k, v in vals.items() if v > 0},
        })

    return result