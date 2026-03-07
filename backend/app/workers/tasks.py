from app.services.climatiq_service import estimate_vehicle_emissions


def process_activity(activity):
    try:
        distance = activity.get("distance")

        if distance is None:
            return {"error": "No distance provided"}

        emissions = estimate_vehicle_emissions(distance)

        return {
            "activity_type": "vehicle_travel",
            "distance_km": distance,
            "co2e": emissions.get("co2e"),
            "unit": emissions.get("co2e_unit")
        }
    

    except Exception as e:
        return {"error": str(e)}
    