from fastapi import APIRouter
from app.services.climatiq_service import estimate_vehicle_emissions

router = APIRouter()

@router.post("/calculate")
def calculate_emissions(distance: float):

    result = estimate_vehicle_emissions(distance)

    return {
        "distance_km": distance,
        "co2e": result["co2e"],
        "unit": result["co2e_unit"]
    }