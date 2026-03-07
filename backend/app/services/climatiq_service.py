import requests
import os

CLIMATIQ_API_URL = "https://api.climatiq.io/data/v1/estimate"
API_KEY = os.getenv("CLIMATIQ_API_KEY")


def estimate_vehicle_emissions(distance_km: float):

    payload = {
        "emission_factor": {
            "activity_id": "passenger_vehicle-vehicle_type_car-fuel_source_petrol-distance_na-engine_size_na"
        },
        "parameters": {
            "distance": distance_km,
            "distance_unit": "km"
        }
    }

    headers = {
        "Authorization": f"Bearer {API_KEY}"
    }

    response = requests.post(CLIMATIQ_API_URL, json=payload, headers=headers)

    return response.json()