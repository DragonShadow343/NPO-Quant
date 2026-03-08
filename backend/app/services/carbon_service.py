from collections import defaultdict
from typing import List

SBTi_ANNUAL_REDUCTION = 0.042   # 4.2%/yr — Science Based Targets initiative

EMISSION_CATEGORIES = ["fuel", "electricity", "natural_gas", "mileage", "travel_scope3"]


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
    """Monthly CO2e breakdown by category — for charts."""
    monthly = defaultdict(lambda: {cat: 0.0 for cat in EMISSION_CATEGORIES})

    for r in approved:
        if r.get("category") != "carbon":
            continue
        raw   = r.get("raw") or {}
        month = raw.get("period", r.get("period", "Unknown"))
        cat   = raw.get("type", r.get("doc_type", "unknown"))
        val   = r.get("kg_co2e") or 0
        if cat in EMISSION_CATEGORIES:
            monthly[month][cat] += val

    return [
        {
            "month":            month,
            "total_emissions":  round(sum(vals.values()), 3),
            "composition":      {k: round(v, 3) for k, v in vals.items()},
        }
        for month, vals in sorted(monthly.items())
    ]