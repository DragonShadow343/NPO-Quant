import os
import requests

CLIMATIQ_API_URL = "https://api.climatiq.io/data/v1/estimate"
API_KEY = os.getenv("CLIMATIQ_API_KEY") or os.getenv("CLIMATE_API_KEY")

# ECCC NIR 2024 / TCR 2025 local fallback factors
_FALLBACK = {
    "electricity": 0.013,   # kg CO2e/kWh  — BC grid (ECCC NIR 2024, Annex 6)
    "natural_gas": 51.35,   # kg CO2e/GJ   — stationary combustion (TCR 2025 Table 1.2)
    "fuel":        2.681,   # kg CO2e/L    — diesel fleet (TCR 2025 Table 2.2)
}

# Climatiq activity IDs + parameter keys for each doc type
_ACTIVITY = {
    "electricity": {
        "activity_id": "electricity-supply_grid-source_residual_mix",
        "param_key":   "energy",
        "param_unit":  "kWh",
        "region":      "CA-BC",
    },
    "natural_gas": {
        "activity_id": "fuel_combustion-type_natural_gas-fuel_use_all",
        "param_key":   "energy",
        "param_unit":  "GJ",
        "region":      "CA",
    },
    "fuel": {
        "activity_id": "fuel_combustion-type_diesel-fuel_use_mobile_combustion",
        "param_key":   "volume",
        "param_unit":  "l",
        "region":      "CA",
    },
}


def estimate_emissions(doc_type: str, amount: float, unit: str) -> dict:
    """
    Call Climatiq for electricity / natural_gas / fuel.
    Falls back to ECCC local factors if the API key is missing or the call fails.
    """
    cfg = _ACTIVITY.get(doc_type)

    if cfg and API_KEY and amount > 0:
        payload = {
            "emission_factor": {
                "activity_id": cfg["activity_id"],
                "region":      cfg["region"],
                "year":        2024,
                "source":      "ECCC",
            },
            "parameters": {
                cfg["param_key"]:              amount,
                f"{cfg['param_key']}_unit":    cfg["param_unit"],
            },
        }
        try:
            resp = requests.post(
                CLIMATIQ_API_URL,
                json=payload,
                headers={"Authorization": f"Bearer {API_KEY}"},
                timeout=10,
            )
            data = resp.json()
            if resp.status_code == 200 and "co2e" in data:
                return {
                    "co2e":      data["co2e"],
                    "co2e_unit": data.get("co2e_unit", "kg"),
                    "_fallback": False,
                }
        except Exception:
            pass

    return _local_estimate(doc_type, amount)


def _local_estimate(doc_type: str, amount: float) -> dict:
    factor = _FALLBACK.get(doc_type)
    if factor is None or amount <= 0:
        return {"co2e": 0.0, "co2e_unit": "kg", "_fallback": True}
    return {
        "co2e":      round(amount * factor, 3),
        "co2e_unit": "kg",
        "_fallback": True,
    }


def estimate_vehicle_emissions(distance_km: float) -> dict:
    payload = {
        "emission_factor": {
            "activity_id": "passenger_vehicle-vehicle_type_car-fuel_source_petrol-distance_na-engine_size_na"
        },
        "parameters": {"distance": distance_km, "distance_unit": "km"},
    }
    response = requests.post(
        CLIMATIQ_API_URL,
        json=payload,
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    return response.json()