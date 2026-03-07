# accounting_service.py
from collections import defaultdict
from typing import List

def aggregate_monthly(file_results: List[dict]) -> List[dict]:
    monthly_totals = defaultdict(lambda: {"inflow": 0, "expense": 0})

    for res in file_results:
        month = res.get("period", "Unknown")
        amt = res.get("amount", 0)
        if amt >= 0:
            monthly_totals[month]["inflow"] += amt
        else:
            monthly_totals[month]["expense"] += abs(amt)

    monthly_summary = []
    for month, data in sorted(monthly_totals.items()):
        profit_loss = data["inflow"] - data["expense"]
        profit_loss_percent = (profit_loss / data["inflow"] * 100) if data["inflow"] else 0
        monthly_summary.append({
            "month": month,
            "inflow": data["inflow"],
            "expense": data["expense"],
            "profit_loss": profit_loss,
            "profit_loss_percent": profit_loss_percent
        })

    return monthly_summary