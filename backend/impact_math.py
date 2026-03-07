BC_ELECTRICITY_FACTOR = 0.013
BC_NATGAS_FACTOR      = 51.35
BC_FUEL_FACTOR        = 2.681
SOCIAL_WAGE           = 35.00

_FACTOR_MAP = {
    "electricity": BC_ELECTRICITY_FACTOR,
    "fuel":        BC_FUEL_FACTOR,
    "natural_gas": BC_NATGAS_FACTOR,
}

_SCOPE_MAP = {"electricity": 2, "natural_gas": 1, "fuel": 1}


def calculate_carbon(extracted: dict) -> dict:
    doc_type = extracted.get("type", "electricity")
    factor   = _FACTOR_MAP.get(doc_type, BC_ELECTRICITY_FACTOR)
    kg_co2e  = round(extracted.get("amount", 0) * factor, 3)
    return {
        "raw":     extracted,
        "kg_co2e": kg_co2e,
        "scope":   _SCOPE_MAP.get(doc_type, 2),
        "status":  "pending_approval",
    }