# grants_service.py
from collections import defaultdict

def aggregate_monthly(file_results: List[dict]) -> List[dict]:
    monthly_data = defaultdict(lambda: {"eligible": 0, "ineligible": 0})

    for res in file_results:
        month = res.get("period", "Unknown")
        if res.get("eligible"):
            monthly_data[month]["eligible"] += 1
        else:
            monthly_data[month]["ineligible"] += 1

    monthly_summary = []
    for month, counts in sorted(monthly_data.items()):
        total = counts["eligible"] + counts["ineligible"]
        monthly_summary.append({
            "month": month,
            "eligible_count": counts["eligible"],
            "ineligible_count": counts["ineligible"],
            "eligibility_percent": (counts["eligible"] / total * 100) if total else 0
        })
    return monthly_summary