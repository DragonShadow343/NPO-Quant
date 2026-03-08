BC_ELECTRICITY_FACTOR = 0.013    # kg CO2e/kWh  — ECCC NIR 2024 Annex 6
BC_NATGAS_FACTOR      = 51.35    # kg CO2e/GJ   — TCR 2025 Table 1.2
BC_FUEL_FACTOR        = 2.681    # kg CO2e/L    — TCR 2025 Table 2.2
BC_VEHICLE_FACTOR     = 0.21     # kg CO2e/km   — average fleet diesel (ECCC NIR 2024)
SOCIAL_WAGE           = 30.00    # CAD/hr       — BC Living Wage 2026

_FACTOR_MAP = {
    "electricity": BC_ELECTRICITY_FACTOR,
    "fuel":        BC_FUEL_FACTOR,
    "natural_gas": BC_NATGAS_FACTOR,
    "mileage":     BC_VEHICLE_FACTOR,
}

_SCOPE_MAP = {
    "electricity": 2,
    "natural_gas": 1,
    "fuel":        1,
    "mileage":     1,
}


def calculate_carbon(extracted: dict) -> dict:
    doc_type = extracted.get("type", "electricity")
    factor   = _FACTOR_MAP.get(doc_type)
    if factor is None:
        return {
            "category": "carbon", "doc_type": doc_type, "raw": extracted,
            "kg_co2e": 0.0, "scope": _SCOPE_MAP.get(doc_type, 2),
            "status": "pending_approval",
        }
    kg_co2e  = round(extracted.get("amount", 0) * factor, 3)
    return {
        "category": "carbon",
        "doc_type": doc_type,
        "raw":      extracted,
        "kg_co2e":  kg_co2e,
        "scope":    _SCOPE_MAP.get(doc_type, 2),
        "status":   "pending_approval",
    }


def route(extracted: dict) -> dict:
    if "volunteers" in extracted:
        hours = round(sum(v.get("hours", 0) for v in extracted["volunteers"]), 2)
        return {
            "category":                "social",
            "doc_type":                "volunteers",
            "raw":                     extracted,
            "kg_co2e":                 None,
            "scope":                   None,
            "status":                  "pending_approval",
            "total_volunteers":        len(extracted["volunteers"]),
            "total_hours":             hours,
            "community_value_dollars": round(hours * SOCIAL_WAGE, 2),
        }
    return calculate_carbon(extracted)