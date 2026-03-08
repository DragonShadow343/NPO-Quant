import re
from collections import defaultdict
from datetime import datetime
from typing import List, Optional

# Quad Banks Canada standard cost per pound for food valuation
FOOD_COST_PER_LB = 3.58
# BC volunteer rate
VOLUNTEER_WAGE = 30.00


def _normalize_period(period: str) -> str:
    """Normalize period to YYYY-MM for consistent aggregation (e.g. 'Dec 2023' and 'December 2023' → same key)."""
    if not period or not isinstance(period, str):
        return "Unknown"
    period = period.strip()
    if period in ("Unknown Period", "Unknown", ""):
        return "Unknown"
    # Year only: "2024" → "2024-01"
    if re.fullmatch(r"\d{4}", period):
        return f"{period}-01"
    # Date: "2023-07-12" → "2023-07"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", period):
        return period[:7]
    # Already YYYY-MM
    if re.fullmatch(r"\d{4}-\d{2}", period):
        return period
    # "January 2026", "April 2024"
    try:
        dt = datetime.strptime(period, "%B %Y")
        return dt.strftime("%Y-%m")
    except ValueError:
        pass
    try:
        dt = datetime.strptime(period, "%b %Y")
        return dt.strftime("%Y-%m")
    except ValueError:
        pass
    return period  # leave as-is if unparseable (will not merge with others)


def _format_month_display(month_key: str) -> str:
    """Format YYYY-MM as 'Month YYYY' for dashboard display."""
    if not month_key or month_key == "Unknown":
        return "Unknown"
    if len(month_key) < 7:
        return month_key
    try:
        dt = datetime.strptime(month_key[:7], "%Y-%m")
        return dt.strftime("%B %Y")
    except ValueError:
        return month_key


def aggregate(
    approved: List[dict],
    unrestricted_net_assets: Optional[float] = None,
    manual_overrides: Optional[dict] = None,
) -> dict:
    """
    Computes all accounting dashboard numbers AND a COFB-style financial
    statement data structure from approved document items.

    Pass unrestricted_net_assets (CAD) to get months_of_reserve.
    Pass manual_overrides dict to fill in values not derivable from documents
    (e.g. opening balances, TCA, payables).
    """
    mo = manual_overrides or {}

    monthly      = defaultdict(lambda: {"inflow": 0.0, "expense": 0.0})
    category_exp = defaultdict(float)
    category_rev = defaultdict(float)
    total_expense = 0.0
    total_inflow  = 0.0

    # Financial statement line items — populated from documents
    revenue_lines = defaultdict(float)
    expense_lines = defaultdict(float)
    volunteer_hours_by_period = defaultdict(float)
    total_volunteer_hours = 0.0
    total_volunteers      = 0

    for r in approved:
        raw      = r.get("raw") or {}
        doc_type = raw.get("type", r.get("doc_type", "unknown"))
        period   = raw.get("period", r.get("period", "Unknown"))
        norm     = _normalize_period(period)
        cost     = raw.get("cost_dollars") or 0.0
        category = r.get("category", "")

        # ── Revenue documents ──────────────────────────────────────────────
        if doc_type == "grant" and cost > 0:
            revenue_lines["grants"]              += cost
            category_rev["grants"]               += cost
            monthly[norm]["inflow"]            += cost
            total_inflow                         += cost

        elif doc_type == "goods_donated" and cost > 0:
            revenue_lines["goods_donated"]       += cost
            category_rev["goods_donated"]        += cost
            monthly[norm]["inflow"]            += cost
            total_inflow                         += cost

        # ── Accounting summary CSV (overall financial overview) ────────────
        elif doc_type == "accounting_summary":
            inflow_val  = raw.get("total_inflow",  raw.get("amount",       0)) or 0
            expense_val = raw.get("total_expense", raw.get("cost_dollars", 0)) or 0
            if inflow_val > 0:
                revenue_lines["grants"]          += inflow_val   # treated as operating revenue
                category_rev["operating_revenue"] += inflow_val
                monthly[norm]["inflow"]         += inflow_val
                total_inflow                      += inflow_val
            if expense_val > 0:
                expense_lines["supplies_occupancy"] += expense_val
                category_exp["operating_expenses"]  += expense_val
                monthly[norm]["expense"]          += expense_val
                total_expense                       += expense_val

        # ── Payroll / paystub ─────────────────────────────────────────────
        elif doc_type == "payroll":
            wage_val = raw.get("amount", cost) or 0
            if wage_val > 0:
                expense_lines["wages_benefits"]  += wage_val
                category_exp["wages"]            += wage_val
                monthly[norm]["expense"]       += wage_val
                total_expense                    += wage_val

        # ── Disbursement voucher / expense log XLSX ────────────────────────
        elif doc_type == "disbursement_voucher":
            dv_items = raw.get("items", [])
            for item in dv_items:
                item_amt  = float(item.get("amount_dollars") or 0)
                item_desc = (item.get("description") or "").lower()
                item_cat  = item.get("type", "")
                if item_amt <= 0:
                    continue
                # Map item category/description → FS expense line
                if item_cat == "fuel" or "fuel" in item_desc or "diesel" in item_desc or "gasoline" in item_desc:
                    expense_lines["automotive"] += item_amt
                elif item_cat in ("electricity", "natural_gas") or "utilities" in item_desc or "electricity" in item_desc:
                    expense_lines["telephone_utilities"] += item_amt
                elif "telephone" in item_desc or "internet" in item_desc or "telus" in item_desc:
                    expense_lines["telephone_utilities"] += item_amt
                elif "repair" in item_desc or "maintenance" in item_desc or "mechanical" in item_desc:
                    expense_lines["repairs_maintenance"] += item_amt
                elif "insurance" in item_desc:
                    expense_lines["insurance"] += item_amt
                elif "wage" in item_desc or "salary" in item_desc or "payroll" in item_desc:
                    expense_lines["wages_benefits"] += item_amt
                elif "office" in item_desc or "supplies" in item_desc or "staples" in item_desc or "amazon" in item_desc:
                    expense_lines["office_admin"] += item_amt
                elif "professional" in item_desc or "legal" in item_desc or "accounting" in item_desc:
                    expense_lines["professional_fees"] += item_amt
                elif "fundraising" in item_desc or "event" in item_desc or "print" in item_desc:
                    expense_lines["fundraising"] += item_amt
                elif "food" in item_desc or "sysco" in item_desc or "grocery" in item_desc:
                    expense_lines["supplies_occupancy"] += item_amt
                else:
                    expense_lines["supplies_occupancy"] += item_amt
                category_exp["expenses"] += item_amt
                total_expense            += item_amt
            monthly[norm]["expense"] += sum(float(i.get("amount_dollars") or 0) for i in dv_items)

        # ── Expense documents ──────────────────────────────────────────────
        elif doc_type in ("electricity", "natural_gas") and cost > 0:
            expense_lines["telephone_utilities"] += cost
            category_exp[doc_type]               += cost
            monthly[norm]["expense"]           += cost
            total_expense                        += cost

        elif doc_type in ("fuel", "mileage") and cost > 0:
            expense_lines["automotive"]          += cost
            category_exp[doc_type]               += cost
            monthly[norm]["expense"]           += cost
            total_expense                        += cost

        # ── Volunteer / social documents ───────────────────────────────────
        elif category == "social" or doc_type in ("volunteers", "social"):
            hours = r.get("total_hours") or 0.0
            vols  = r.get("total_volunteers") or 0
            total_volunteer_hours += hours
            total_volunteers      += vols
            volunteer_hours_by_period[norm] += hours
            # Volunteer value is treated as in-kind goods donated revenue
            val = round(hours * VOLUNTEER_WAGE, 2)
            revenue_lines["goods_donated"]       += val
            category_rev["volunteer_service"]    += val
            monthly[norm]["inflow"]            += val
            total_inflow                         += val

    # ── Monthly summary (one row per normalized month, display-friendly labels) ─
    # Skip "Unknown" so we don't show a single misleading row mixing unrelated docs with no period.
    monthly_summary = []
    for month_key, data in sorted(monthly.items()):
        if month_key == "Unknown":
            continue
        net = data["inflow"] - data["expense"]
        pct = round(net / data["inflow"] * 100, 1) if data["inflow"] else 0.0
        monthly_summary.append({
            "month":               _format_month_display(month_key),
            "inflow":              round(data["inflow"],  2),
            "expense":             round(data["expense"], 2),
            "profit_loss":         round(net, 2),
            "profit_loss_percent": pct,
        })

    # ── Top categories ─────────────────────────────────────────────────────
    all_categories = {**category_exp, **category_rev}
    top_categories = sorted(all_categories.items(), key=lambda x: -x[1])[:5]

    avg_monthly_expense = (total_expense / len(monthly_summary)) if monthly_summary else 0.0
    months_of_reserve = (
        round(unrestricted_net_assets / avg_monthly_expense, 1)
        if unrestricted_net_assets and avg_monthly_expense > 0
        else None
    )

    donated_goods_value = round(
        revenue_lines.get("goods_donated", 0.0), 2
    )

    # ── Financial statement data structure (COFB-style ASNPO) ─────────────
    # Values from documents are merged with manual overrides.
    # Manual overrides take precedence where both exist.

    fs_revenue = {
        "goods_donated":             round(mo.get("rev_goods_donated",      revenue_lines.get("goods_donated", 0.0)),     2),
        "fundraising_campaigns":     round(mo.get("rev_fundraising",        revenue_lines.get("fundraising", 0.0)),        2),
        "third_party_events":        round(mo.get("rev_third_party",        revenue_lines.get("third_party", 0.0)),        2),
        "grants":                    round(mo.get("rev_grants",              revenue_lines.get("grants", 0.0)),             2),
        "amort_deferred_contrib":    round(mo.get("rev_amort_deferred",      0.0),                                          2),
    }
    total_revenue = sum(fs_revenue.values())

    fs_direct_costs = {
        "goods_distributed": round(mo.get("direct_goods_distributed",       0.0), 2),
    }

    fs_expenses = {
        "amortization_tca":          round(mo.get("exp_amortization_tca",    expense_lines.get("amortization_tca",    0.0)), 2),
        "automotive":                round(mo.get("exp_automotive",          expense_lines.get("automotive",          0.0)), 2),
        "fundraising":               round(mo.get("exp_fundraising",         expense_lines.get("fundraising",         0.0)), 2),
        "insurance":                 round(mo.get("exp_insurance",           expense_lines.get("insurance",           0.0)), 2),
        "interest_charges":          round(mo.get("exp_interest_charges",    expense_lines.get("interest_charges",    0.0)), 2),
        "office_admin":              round(mo.get("exp_office_admin",        expense_lines.get("office_admin",        0.0)), 2),
        "professional_fees":         round(mo.get("exp_professional_fees",   expense_lines.get("professional_fees",   0.0)), 2),
        "repairs_maintenance":       round(mo.get("exp_repairs",             expense_lines.get("repairs_maintenance", 0.0)), 2),
        "staff_volunteer_recognition": round(mo.get("exp_staff_volunteer",   expense_lines.get("staff_volunteer_recognition", 0.0)), 2),
        "supplies_occupancy":        round(mo.get("exp_supplies",            expense_lines.get("supplies_occupancy",  0.0)), 2),
        "telephone_utilities":       round(mo.get("exp_telephone_utilities", expense_lines.get("telephone_utilities", 0.0)), 2),
        "wages_benefits":            round(mo.get("exp_wages_benefits",      expense_lines.get("wages_benefits",      0.0)), 2),
    }
    total_expenses = sum(fs_expenses.values())
    total_direct   = sum(fs_direct_costs.values())
    gross_rev_over_dist = total_revenue - total_direct

    fs_other_income = {
        "interest_income":           round(mo.get("other_interest",         0.0), 2),
        "investment_income":         round(mo.get("other_investment",       0.0), 2),
        "gain_on_sale_tca":          round(mo.get("other_gain_tca",         0.0), 2),
    }
    total_other = sum(fs_other_income.values())
    excess_revenue = gross_rev_over_dist - total_expenses + total_other

    # ── Balance sheet estimates derived from available documents ─────────────
    # Cash unrestricted = estimated operating surplus (inflow − expense).
    # Inventory = donated goods value (food inventory on hand).
    # Unrestricted net assets = excess of revenue over expenses from operations.
    # Fields not inferable from documents default to 0 and show as "—" in the PDF.
    _est_cash        = round(max(0.0, total_inflow - total_expense), 2)
    _est_inventory   = donated_goods_value   # food/goods inventory value
    _est_unrestricted = round(excess_revenue, 2)

    fs_assets = {
        "cash_unrestricted":           round(mo.get("asset_cash_unrestricted",           _est_cash),       2),
        "cash_internally_restricted":  round(mo.get("asset_cash_int_restricted",         0.0),             2),
        "cash_externally_restricted":  round(mo.get("asset_cash_ext_restricted",         0.0),             2),
        "investments_unrestricted":    round(mo.get("asset_invest_unrestricted",         0.0),             2),
        "investments_restricted":      round(mo.get("asset_invest_restricted",           0.0),             2),
        "inventory":                   round(mo.get("asset_inventory",                  _est_inventory),   2),
        "prepaid_deposits":            round(mo.get("asset_prepaid",                    0.0),             2),
        "gst_recoverable":             round(mo.get("asset_gst_recoverable",            0.0),             2),
        "tangible_capital_assets_net": round(mo.get("asset_tca_net",                   0.0),             2),
    }
    total_assets = sum(fs_assets.values())

    # Payables = total expenses outstanding (estimated)
    _est_payables = round(total_expense * 0.05, 2)  # ~5% outstanding at period end
    fs_liabilities = {
        "payables_accruals":           round(mo.get("liab_payables",                   _est_payables), 2),
        "unearned_revenue":            round(mo.get("liab_unearned_revenue",           0.0),           2),
        "deferred_capital_contrib":    round(mo.get("liab_deferred_capital_contrib",   0.0),           2),
    }
    total_liabilities = sum(fs_liabilities.values())

    fs_net_assets = {
        "internally_restricted_food":          round(mo.get("na_food_restricted",       _est_inventory), 2),
        "internally_restricted_infrastructure":round(mo.get("na_infra_restricted",      0.0),            2),
        "invested_in_tca":                     round(mo.get("na_invested_tca",          0.0),            2),
        "unrestricted":                        round(unrestricted_net_assets or mo.get("na_unrestricted", _est_unrestricted), 2),
    }
    total_net_assets = sum(fs_net_assets.values())

    return {
        # ── Dashboard quick numbers ────────────────────────────────────────
        "total_inflow":          round(total_inflow,  2),
        "total_costs":           round(total_expense, 2),
        "top_asset_categories":  [{"category": k, "amount": round(v, 2)} for k, v in top_categories],
        "monthly":               monthly_summary,
        "donated_goods_value":   donated_goods_value,
        "months_of_reserve":     months_of_reserve,
        "volunteer_hours":       round(total_volunteer_hours, 2),
        "volunteer_count":       total_volunteers,
        "volunteer_value":       round(total_volunteer_hours * VOLUNTEER_WAGE, 2),
        "volunteer_wage_rate":   VOLUNTEER_WAGE,

        # ── Full financial statement data (ASNPO / COFB-style) ────────────
        "financial_statements": {
            "statement_of_operations": {
                "revenue":        fs_revenue,
                "total_revenue":  round(total_revenue, 2),
                "direct_costs":   fs_direct_costs,
                "total_direct":   round(total_direct, 2),
                "gross_rev_over_distributions": round(gross_rev_over_dist, 2),
                "expenses":       fs_expenses,
                "total_expenses": round(total_expenses, 2),
                "other_income":   fs_other_income,
                "total_other":    round(total_other, 2),
                "excess_revenue_over_expenses": round(excess_revenue, 2),
            },
            "statement_of_financial_position": {
                "assets":            fs_assets,
                "total_assets":      round(total_assets, 2),
                "liabilities":       fs_liabilities,
                "total_liabilities": round(total_liabilities, 2),
                "net_assets":        fs_net_assets,
                "total_net_assets":  round(total_net_assets, 2),
            },
            "statement_of_changes_in_net_assets": {
                "food_restricted": {
                    "opening": round(mo.get("na_food_restricted_opening",  0.0), 2),
                    "closing": fs_net_assets["internally_restricted_food"],
                },
                "infra_restricted": {
                    "opening": round(mo.get("na_infra_restricted_opening", 0.0), 2),
                    "closing": fs_net_assets["internally_restricted_infrastructure"],
                },
                "invested_tca": {
                    "opening": round(mo.get("na_invested_tca_opening",     0.0), 2),
                    "closing": fs_net_assets["invested_in_tca"],
                },
                "unrestricted": {
                    "opening": round(mo.get("na_unrestricted_opening",     0.0), 2),
                    "closing": fs_net_assets["unrestricted"],
                },
                "total_opening": round(
                    mo.get("na_food_restricted_opening", 0.0) +
                    mo.get("na_infra_restricted_opening", 0.0) +
                    mo.get("na_invested_tca_opening", 0.0) +
                    mo.get("na_unrestricted_opening", 0.0), 2
                ),
                "total_closing": round(total_net_assets, 2),
                "excess_revenue": round(excess_revenue, 2),
            },
        },
    }