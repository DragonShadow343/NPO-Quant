# carbon_service.py
from collections import defaultdict
from typing import List, dict

EMISSION_CATEGORIES = ["fuel", "electricity", "natural_gas", "travel_scope3"]

def aggregate_monthly(file_results: List[dict]) -> dict:
    monthly_data = defaultdict(lambda: {cat: 0 for cat in EMISSION_CATEGORIES})

    for result in file_results:
        month = result.get("period", "Unknown")
        cat = result.get("type")
        co2e = result.get("amount", 0)
        if cat in EMISSION_CATEGORIES:
            monthly_data[month][cat] += co2e

    # Compute total per month
    monthly_summary = []
    for month, values in sorted(monthly_data.items()):
        total = sum(values.values())
        monthly_summary.append({
            "month": month,
            "total_emissions": total,
            "composition": values
        })

    return monthly_summary