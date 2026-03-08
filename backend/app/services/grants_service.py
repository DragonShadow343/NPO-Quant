from typing import List

GRANT_CATEGORIES = {
    "electricity": "Electricity / Scope 2 (BC Hydro)",
    "natural_gas": "Natural Gas / Scope 1 (FortisBC)",
    "fuel":        "Fleet Fuel / Scope 1 (Diesel)",
    "social":      "Volunteer & Social Impact",
}

# Specific actionable recommendation for each missing category
_GAP_REMEDIATION = {
    "electricity": (
        "Upload a BC Hydro electricity invoice (PDF or image). "
        "The AI will extract kWh consumption and compute Scope 2 CO₂e automatically. "
        "Typical document: monthly utility bill naming 'BC Hydro' and showing kWh consumed."
    ),
    "natural_gas": (
        "Upload a FortisBC natural gas bill (PDF or image). "
        "The AI will extract GJ consumed and calculate Scope 1 CO₂e at 51.35 kg CO₂e/GJ. "
        "Typical document: quarterly utility invoice from FortisBC Energy Inc."
    ),
    "fuel": (
        "Upload fleet fuel receipts (PDF, image) or a fleet fuel log (CSV/XLSX). "
        "The AI will extract litres dispensed and compute Scope 1 CO₂e at 2.681 kg CO₂e/L. "
        "Petro-Canada, Esso, Shell, and Co-op receipts are all recognised automatically."
    ),
    "social": (
        "Upload a volunteer sign-in sheet (image/PDF) or a volunteer log (CSV/XLSX). "
        "The AI will extract volunteer names, hours, and compute community value at $30/hr. "
        "Typical document: a dated sign-in sheet listing names, start/end times, and activity."
    ),
}


def compute_readiness(approved: List[dict], na_categories: dict = None) -> dict:
    """
    GHG Protocol Completeness: covered data OR declared N/A both count as documented.
    Score = documented / total * 100.
    """
    na_categories = na_categories or {}
    covered: set = set()

    for r in approved:
        raw      = r.get("raw") or {}
        doc_type = raw.get("type", r.get("doc_type", ""))
        mods     = r.get("modules") or {}

        if doc_type in GRANT_CATEGORIES:
            covered.add(doc_type)
        if r.get("category") == "social" or doc_type in ("volunteers", "social"):
            covered.add("social")

        # Carbon summary CSV covers all emission categories it contains
        if doc_type == "carbon_summary":
            components = raw.get("components") or {}
            for comp in components:
                if comp in GRANT_CATEGORIES:
                    covered.add(comp)
            if not components:
                covered.add("electricity")  # conservative — mark as partial evidence

        # Also check modules dict for grant evidence
        for mod_key, mod_data in mods.items():
            ev_type = mod_data.get("covers_category", "") if isinstance(mod_data, dict) else ""
            if ev_type in GRANT_CATEGORIES:
                covered.add(ev_type)

    total      = len(GRANT_CATEGORIES)
    na_keys    = set(na_categories.keys()) & GRANT_CATEGORIES.keys()
    na_only    = na_keys - covered
    documented = covered | na_only
    score      = round(len(documented) / total * 100) if total else 100

    breakdown = [
        {
            "category":       k,
            "label":          label,
            "covered":        k in covered,
            "not_applicable": k in na_only,
            "na_reason":      na_categories.get(k),
            "missing":        k not in documented,
        }
        for k, label in GRANT_CATEGORIES.items()
    ]

    key_factors_lacking = [
        {
            "category":    k,
            "label":       label,
            "reason":      "No approved document on file",
            "remediation": _GAP_REMEDIATION.get(k, "Upload a relevant source document and re-process."),
        }
        for k, label in GRANT_CATEGORIES.items()
        if k not in documented
    ]

    return {
        "score":               score,
        "covered_count":       len(covered),
        "na_count":            len(na_only),
        "documented_count":    len(documented),
        "total_categories":    total,
        "breakdown":           breakdown,
        "key_factors_lacking": key_factors_lacking,
        "approved_docs":       len(approved),
    }