"""
ai.py — Filename-aware extraction with LLM + regex heuristic fallback.

Pipeline:
  1. Filename classification  (strongest signal — uses file name keywords)
  2. OCR text hint detection  (hint prepended by ocr.py, e.g. "Fleet Fuel Log:")
  3. Ollama LLM               (if available; filename context is passed in prompt)
  4. Heuristic regex parser   (deterministic fallback)
  5. Targeted extraction      (when type is known from filename but content is sparse)

Returns a dict that always includes `_confidence`:
  "high"   — strong match in content (labelled value or Ollama JSON)
  "medium" — type matched but value inferred / filename-derived
  "low"    — could not reliably parse a value; flagged for manual review

Document types handled:
  electricity, natural_gas, fuel, mileage,
  volunteers, grant, goods_donated, payroll,
  disbursement_voucher
"""
import json
import os
import re

try:
    import ollama
    _OLLAMA_AVAILABLE = True
except ImportError:
    _OLLAMA_AVAILABLE = False


# ── Filename → document type mapping ─────────────────────────────────────────

_FILENAME_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Summary / report-type files — check BEFORE individual-doc patterns
    (re.compile(r"carbon.?emission|emission.?raw|co2.?summary|ghg.?summary", re.IGNORECASE), "carbon_summary"),
    (re.compile(r"accounting.?summary|financial.?summary", re.IGNORECASE), "accounting_summary"),
    (re.compile(r"grant.?application|grant.?tracking|grant.?log", re.IGNORECASE), "grant_applications"),
    (re.compile(r"revenue.?category|revenue.?total|category.?total", re.IGNORECASE), "accounting_summary"),
    # Individual-doc patterns
    (re.compile(r"electricity|electric(?!al)|elec[\s_-]|kwh|bc.?hydro|hydro.?bill|power.?bill", re.IGNORECASE), "electricity"),
    (re.compile(r"natural.?gas|nat.?gas|fortis|gas.?bill|natgas", re.IGNORECASE), "natural_gas"),
    (re.compile(r"fuel.?receipt|receipt.?fuel|fleet.?fuel|fuel.?log|petro|esso|diesel|gasoline", re.IGNORECASE), "fuel"),
    (re.compile(r"mileage|mile|trip.?log|odometer|vehicle.?log|km.?log", re.IGNORECASE), "mileage"),
    (re.compile(r"volunteer|vol.?log|hours.?log|vol_", re.IGNORECASE), "volunteers"),
    (re.compile(r"grant|award|funding|bc.?gaming|community.?gaming|contribution.?letter", re.IGNORECASE), "grant"),
    (re.compile(r"food|donation|donated|goods|intake|inventory|in.?kind|food.?bank", re.IGNORECASE), "goods_donated"),
    (re.compile(r"expense|disbursement|voucher|reconciliation|expense.?log", re.IGNORECASE), "disbursement_voucher"),
    (re.compile(r"paystub|pay.?stub|payroll|salary|wage|payslip", re.IGNORECASE), "payroll"),
    (re.compile(r"bill_", re.IGNORECASE), "electricity"),  # generic "bill_" prefix → likely utility bill
]

# OCR hint prefixes (prepended by ocr.py) → document type
_OCR_HINT_MAP = {
    "fleet fuel log":    "fuel",
    "volunteer log":     "volunteers",
    "food donation log": "goods_donated",
    "expense log":       "disbursement_voucher",
    "mileage log":       "mileage",
    "payroll log":       "payroll",
    "carbon summary":    "carbon_summary",
    "accounting summary":"accounting_summary",
    "grant applications":"grant_applications",
    "data log":          None,  # ambiguous; fall through
}


def _classify_by_filename(filename: str) -> str | None:
    """Return doc type from filename keywords, or None if ambiguous."""
    if not filename:
        return None
    base = os.path.splitext(os.path.basename(filename))[0]
    for pattern, doc_type in _FILENAME_PATTERNS:
        if pattern.search(base):
            return doc_type
    return None


def _classify_by_ocr_hint(text: str) -> str | None:
    """Return doc type from the hint line prepended by ocr.py."""
    first_line = text.split("\n", 1)[0].strip().lower().rstrip(":")
    return _OCR_HINT_MAP.get(first_line)


# ── LLM prompt ────────────────────────────────────────────────────────────────

_PROMPT = """You are a comprehensive data extraction engine for Canadian nonprofit compliance reporting.
Your job is to extract ALL information from the document — not just emissions data.
Every document contributes to one or more of three modules: CARBON, ACCOUNTING, GRANTS.
Reply with raw JSON only. No markdown fences. No explanation.

DOCUMENT TYPES — return exactly one of these formats:

Electricity bill (BC Hydro / FortisBC Electricity, kWh):
{{"type": "electricity", "amount": 1247, "unit": "kWh", "period": "January 2026", "provider": "BC Hydro", "cost_dollars": 145.80}}
→ Modules: CARBON (Scope 2 emissions) + ACCOUNTING (utility expense) + GRANTS (electricity evidence)

Natural gas bill (FortisBC, GJ):
{{"type": "natural_gas", "amount": 5.41, "unit": "GJ", "period": "February 2026", "provider": "FortisBC", "cost_dollars": 98.20}}
→ Modules: CARBON (Scope 1 emissions) + ACCOUNTING (utility expense) + GRANTS (natural gas evidence)

Fuel / fleet receipt (total litres across all transactions):
{{"type": "fuel", "amount": 218.1, "unit": "L", "period": "March 2026", "provider": "Petro-Canada", "cost_dollars": 412.50}}
→ Modules: CARBON (Scope 1 emissions) + ACCOUNTING (fleet expense) + GRANTS (fuel evidence)

Fleet mileage / vehicle trip log (total km driven):
{{"type": "mileage", "amount": 450.0, "unit": "km", "period": "March 2026", "cost_dollars": 0}}
→ Modules: CARBON (Scope 1 emissions) + ACCOUNTING (vehicle expense) + GRANTS (fleet evidence)

Grant award / approval letter (total grant amount in CAD):
{{"type": "grant", "amount": 15000.00, "unit": "CAD", "period": "Q1 2026", "provider": "BC Community Gaming", "cost_dollars": 15000.00}}
→ Modules: ACCOUNTING (grant revenue inflow) + GRANTS (grant received evidence)

Goods donated / food donation / food intake inventory (value in CAD or weight):
{{"type": "goods_donated", "amount": 3500.00, "unit": "CAD", "period": "FY2024", "cost_dollars": 3500.00}}
→ Modules: ACCOUNTING (in-kind donation inflow) + GRANTS (social impact evidence)

Volunteer hour log (aggregate hours per unique volunteer):
{{"volunteers": [{{"name": "Sarah Chen", "hours": 12, "activities": ["Food Sorting"]}}, {{"name": "Tom Lee", "hours": 8, "activities": ["Delivery"]}}]}}
→ Modules: ACCOUNTING (community value @ $35/hr) + GRANTS (social impact evidence)

Payroll / pay stub (employee compensation):
{{"type": "payroll", "amount": 2450.00, "unit": "CAD", "period": "December 2023", "cost_dollars": 2450.00}}
→ Modules: ACCOUNTING (payroll expense)

Expense tracking / disbursement voucher / multi-item reconciliation:
{{"type": "disbursement_voucher", "grant_code": null, "project": "General Operations",
  "items": [
    {{"vendor": "Shell", "description": "Fuel", "date": "Jan 14", "amount_dollars": 84.20, "type": "fuel", "amount": 42.0, "unit": "L", "_confidence": "high"}},
    {{"vendor": "BC Hydro", "description": "Electricity", "date": "Jan 20", "amount_dollars": 145.00, "type": "electricity", "amount": 1200, "unit": "kWh", "_confidence": "high"}}
  ],
  "compliance_flags": []
}}
→ Modules: CARBON (any fuel/electricity line items) + ACCOUNTING (total expenses) + GRANTS (if grant_code present)

CRITICAL RULES:
- Use the [Filename] hint at the top STRONGLY — it almost always tells you the document type.
- ALWAYS extract cost_dollars (total billed/awarded dollar amount) — this is required for ACCOUNTING.
- For CSV/spreadsheet data, sum all rows to get the total amount.
- If a volunteer CSV has multiple rows, aggregate hours per unique name.
- Use "disbursement_voucher" ONLY for documents with multiple expense line items of DIFFERENT types.
- A grant voucher with a single amount is type "grant", not "disbursement_voucher".
- Set period to the billing/service month and year (e.g. "March 2026" or "FY2024").
- Payroll/paystub documents are type "payroll" — never flag them as electricity or fuel.

Document text:
{text}"""

_OLLAMA_MODELS = ["llama3.2", "llama3", "llama3.1", "llama3:latest", "llama3.2:latest"]


# ── Entry point ───────────────────────────────────────────────────────────────

def _apply_filename_year_override(result: dict, filename: str) -> dict:
    """
    Correct the period year using the year embedded in the filename.
    Filenames like volunteer_signin_Dec09_2023.jpg are authoritative;
    OCR noise should not override them.
    Uses lookahead/lookbehind instead of \\b to handle underscore delimiters.
    """
    if not filename:
        return result
    # Use (?<!\d) / (?!\d) so "2023" in "Dec09_2023.jpg" is found correctly
    _YRE = r'(?<!\d)(201[5-9]|202[0-9])(?!\d)'
    fname_year_m = re.search(_YRE, filename)
    if not fname_year_m:
        return result
    fname_year    = fname_year_m.group(1)
    period        = result.get("period") or ""
    period_year_m = re.search(_YRE, period)
    if period_year_m:
        if period_year_m.group(1) != fname_year:
            result["period"] = re.sub(_YRE, fname_year, period)
    elif period and period not in ("Unknown", "Unknown Period"):
        result["period"] = f"{period} {fname_year}"
    return result


def extract_data(raw_text: str, filename: str = "") -> dict:
    """
    Main extraction pipeline.
    filename is used as the strongest classification signal.
    Returns a dict that includes `_modules` — a mapping of which compliance
    modules (carbon / accounting / grants) this document contributes to.
    """
    filename_type = _classify_by_filename(filename)
    ocr_hint_type = _classify_by_ocr_hint(raw_text)

    # Merge hints: filename wins over OCR hint
    hint_type = filename_type or ocr_hint_type

    # ── Try Ollama LLM first (pass filename context in prompt) ──────────────
    if _OLLAMA_AVAILABLE:
        context = f"[Filename: {filename}]\n\n" if filename else ""
        prompt = _PROMPT.format(text=context + raw_text[:6000])
        for model in _OLLAMA_MODELS:
            try:
                resp = ollama.chat(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                )
                content = resp["message"]["content"]
                data = _parse_json(content)
                # If LLM returns unknown but hint is clear, trust the hint
                if data.get("type") in ("unknown", None, "") and hint_type and hint_type != "disbursement_voucher":
                    data["type"] = hint_type
                data.setdefault("_confidence", "high")
                data["_modules"] = _assign_modules(data)
                _apply_filename_year_override(data, filename)
                return data
            except Exception:
                continue

    # ── Heuristic parse ──────────────────────────────────────────────────────
    result = _heuristic_parse(raw_text, hint_type=hint_type)

    # ── If heuristic returned unknown but we have a hint, do targeted extraction
    if result.get("type") in ("unknown", None) and hint_type:
        result = _extract_by_type(raw_text, hint_type)

    # ── Assign multi-module classification ──────────────────────────────────
    result["_modules"] = _assign_modules(result)
    _apply_filename_year_override(result, filename)
    return result


# ── JSON helper ───────────────────────────────────────────────────────────────

def _parse_json(text: str) -> dict:
    text = re.sub(r"```[a-z]*", "", text).strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group())
    raise ValueError("No JSON in response")


# ── Pre-compiled patterns ─────────────────────────────────────────────────────

_RE_KWH      = re.compile(r"([\d,]+(?:\.\d+)?)\s*kwh", re.IGNORECASE)
_RE_GJ       = re.compile(r"([\d,]+(?:\.\d+)?)\s*(?:gj\b|gigajoule)", re.IGNORECASE)
_RE_M3       = re.compile(r"([\d,]+(?:\.\d+)?)\s*(?:m³|m3|cubic met)", re.IGNORECASE)
_RE_LITRES   = re.compile(
    r"([\d,]+(?:\.\d+)?)\s*(?:litres?|liters?|(?<!\w)L(?!\w))",
    re.IGNORECASE,
)
_RE_LITRES_INLINE = re.compile(r"(\d+(?:\.\d+)?)L\b", re.IGNORECASE)
_RE_KM       = re.compile(
    r"(?:total|this\s+month|monthly|odometer|distance)[\s\w]{0,20}?(\d+(?:\.\d+)?)\s*(?:km\b|kilometres?|kilometers?)",
    re.IGNORECASE,
)
# Matches "TOTAL KILOMETRES THIS MONTH: 143" (number AFTER the unit label)
_RE_KM_TOTAL_LABELED = re.compile(
    r"TOTAL\s+(?:KM|KILOMETRES?|KILOMETERS?)[^:0-9\n]*:?\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_RE_KM_ANY   = re.compile(r"(\d+(?:\.\d+)?)\s*(?:km\b|kilometres?|kilometers?)", re.IGNORECASE)
_RE_PERIOD   = re.compile(
    r"(january|february|march|april|may|june|july|august|september"
    r"|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)"
    r"[\s\-–/]+(\d{4})",
    re.IGNORECASE,
)
_RE_Q_PERIOD = re.compile(r"(Q[1-4])\s*(\d{4})", re.IGNORECASE)
_RE_METER_MONTH = re.compile(
    r"(?:previous|current|start|end)\s+reading.*?\("
    r"(january|february|march|april|may|june|july|august|september"
    r"|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)"
    r"\s+\d{1,2}\)",
    re.IGNORECASE,
)
_RE_KV_VOL = re.compile(
    r"Volunteer\s+Name:\s*([A-Za-z][A-Za-z\-]*(?:\s+[A-Za-z][A-Za-z\-]*)+)"
    r"(?:.*?Activity:\s*([^,\n]+))?"
    r".*?Hours:\s*([\d]+\.?\d*)",
    re.IGNORECASE,
)
_RE_VOL_ROW  = re.compile(
    r"([A-Z][a-zÀ-ÖØ-öø-ÿ]+(?:\s[A-Z][a-zÀ-ÖØ-öø-ÿ]+)+)"
    r"[\s,|]+(\d+(?:\.\d+)?)\s*(?:hrs?|hours?|h\b)?",
    re.IGNORECASE,
)
_FORTISBC_RE = re.compile(r"fortisbc|fortis\s+bc|natural.?gas|gas bill|rate\s*3\b", re.IGNORECASE)
_BCHYDRO_RE  = re.compile(r"bc.?hydro|hydro.?electricity|rate\s*1[12]\d\d", re.IGNORECASE)
_FUEL_RE     = re.compile(
    r"petro.?canada|esso|shell|chevron|fleet.?card|litres?\s+dispensed|fuel\s+receipt"
    r"|fleet\s+fuel\s+log|fuel\s+log",
    re.IGNORECASE,
)
_VOL_RE      = re.compile(r"volunteer|vol\.?\s*id|hours\s+log|time\s+in", re.IGNORECASE)
_MILEAGE_RE  = re.compile(r"mileage\s+log|trip\s+log|vehicle.*mileage|km\s+log|odometer|mileage.*sheet", re.IGNORECASE)
_GRANT_RE    = re.compile(
    r"grant\s+(?:award|approval|agreement|amount|total|disbursement|confirmed)"
    r"|awarded\s+to|grant\s+code|bc\s+gaming|bc\s+community\s+gaming"
    r"|community\s+gaming\s+grant|united\s+way|heritage\s+canada"
    r"|government\s+of\s+canada.*grant|province\s+of\s+bc.*grant",
    re.IGNORECASE,
)
_GOODS_DONATED_RE = re.compile(
    r"goods\s+donated|food\s+donated|donation\s+receipt|in.kind\s+donation"
    r"|pounds\s+received|lbs\s+received|weight.*received|food\s+bank\s+receipt"
    r"|quad\s+banks|food\s+rescue|food\s+donation\s+log|food\s+intake"
    r"|intake\s+inventory|inventory.*food|items?\s+received",
    re.IGNORECASE,
)
_PAYROLL_RE  = re.compile(
    r"paystub|pay\s+stub|payroll|pay\s+period|gross\s+pay|net\s+pay|deductions?"
    r"|employee\s+id|sin\s*:|social\s+insurance|t4\s+slip|payslip",
    re.IGNORECASE,
)
_EXPENSE_LOG_RE = re.compile(
    r"expense\s+log|expense\s+tracking|expense\s+report|expense\s+category"
    r"|disbursement|reconciliation|voucher",
    re.IGNORECASE,
)
_VOUCHER_RE  = re.compile(
    r"disbursement\s+voucher|expense\s+log\s*:|grant\s+code\s*:|reconciliation",
    re.IGNORECASE,
)
_TRAVEL_RE   = re.compile(
    r"westjet|air\s*canada|porter\s+airlines|swoop|flair\s+airlines|transat"
    r"|air\s+transat|delta\s+airlines|united\s+airlines|american\s+airlines"
    r"|via\s*rail|greyhound|enterprise\s+rent|hertz|avis|budget\s+rent"
    r"|lyft|uber\b",
    re.IGNORECASE,
)
_ADMIN_RE    = re.compile(
    r"staples|office\s+depot|best\s*buy|printing|brochure|stationery"
    r"|software\s+subscription|catering|restaurant|hotel|accommodation",
    re.IGNORECASE,
)
_ELEC_RE     = re.compile(r"electricity\s+service|electricity\s+statement|electric\s+bill|kWh", re.IGNORECASE)
_RE_GRANT_AMT = re.compile(
    r"(?:grant|award|approved|total)[:\s]*\$?\s*([\d,]+(?:\.\d{1,2})?)",
    re.IGNORECASE,
)
_RE_LBS = re.compile(r"([\d,]+(?:\.\d+)?)\s*(?:lbs?|pounds?)", re.IGNORECASE)
_RE_COST = re.compile(
    r"(?:total\s+(?:amount\s+)?(?:due|charged|cost|paid)|amount\s+due|total\s+due"
    r"|gross\s+pay|net\s+pay|total\s+value|total\s+donated|total\s+amount)"
    r"[:\s]*\$?\s*([\d,]+(?:\.\d{1,2})?)",
    re.IGNORECASE,
)
_RE_DOLLAR_ANY = re.compile(r"\$\s*([\d,]+(?:\.\d{1,2})?)", re.IGNORECASE)


# ── Targeted extraction (when type is known from filename/hint) ──────────────

def _extract_by_type(text: str, doc_type: str) -> dict:
    """
    Produce a best-effort extraction when we know the document type
    but the general heuristic failed to match (sparse/noisy OCR text).
    """
    if doc_type == "electricity":
        kwh_all = _RE_KWH.findall(text)
        amount  = float(kwh_all[-1].replace(",", "")) if kwh_all else 0.0
        return {
            "type":         "electricity",
            "amount":       amount,
            "unit":         "kWh",
            "period":       _extract_elec_period(text),
            "provider":     _detect_provider(text),
            "cost_dollars": _extract_cost_dollars(text),
            "_confidence":  "medium" if amount > 0 else "low",
        }

    if doc_type == "natural_gas":
        gj_all = _RE_GJ.findall(text)
        amount = float(gj_all[-1].replace(",", "")) if gj_all else 0.0
        if amount == 0:
            m3_all = _RE_M3.findall(text)
            if m3_all:
                amount = round(float(m3_all[-1].replace(",", "")) * 0.03793, 3)
        return {
            "type":         "natural_gas",
            "amount":       amount,
            "unit":         "GJ",
            "period":       _extract_period(text),
            "provider":     _detect_provider(text),
            "cost_dollars": _extract_cost_dollars(text),
            "_confidence":  "medium" if amount > 0 else "low",
        }

    if doc_type == "fuel":
        litres = _extract_litres(text)
        amount = litres if litres is not None else 0.0
        return {
            "type":         "fuel",
            "amount":       amount,
            "unit":         "L",
            "period":       _extract_period(text),
            "provider":     _detect_provider(text),
            "cost_dollars": _extract_cost_dollars(text),
            "_confidence":  "medium" if amount > 0 else "low",
        }

    if doc_type == "mileage":
        amount = _extract_km_total(text)
        return {
            "type":        "mileage",
            "amount":      amount,
            "unit":        "km",
            "period":      _extract_period(text) or "Unknown",
            "_confidence": "high" if amount > 0 else "low",
        }

    if doc_type == "volunteers":
        return _parse_volunteers(text)

    if doc_type == "grant":
        amt_m = _RE_GRANT_AMT.findall(text)
        cost_m = _RE_COST.search(text)
        dollar_m = _RE_DOLLAR_ANY.findall(text)
        amount = 0.0
        if amt_m:
            amount = float(amt_m[-1].replace(",", ""))
        elif cost_m:
            amount = float(cost_m.group(1).replace(",", ""))
        elif dollar_m:
            # Take the largest dollar amount in the document as the grant value
            amount = max(float(v.replace(",", "")) for v in dollar_m)
        return {
            "type":         "grant",
            "amount":       amount,
            "unit":         "CAD",
            "period":       _extract_period(text) or "Unknown",
            "provider":     _detect_provider(text),
            "cost_dollars": amount,
            "_confidence":  "medium" if amount > 0 else "low",
        }

    if doc_type == "goods_donated":
        cost_m   = _RE_COST.search(text)
        lbs_m    = _RE_LBS.findall(text)
        dollar_m = _RE_DOLLAR_ANY.findall(text)
        # Also match XLSX-style "Column Name ($): 1234.56" ($ in column header, not before value)
        dollar_col_m = re.findall(
            r"[\w\s]+\(\$\)[^:]*:\s*([\d,]+(?:\.\d+)?)", text, re.IGNORECASE
        )
        # And TOTALS line aggregates: TOTALS: ..., Retail Est. Value ($): 5280.00
        totals_val_m = re.search(
            r"TOTALS:.*?(?:value|amount|total)\s*\(\$\)[^\d\n$]*(\d[\d,]*(?:\.\d+)?)",
            text, re.IGNORECASE | re.DOTALL,
        )
        if totals_val_m:
            dollar_val = float(totals_val_m.group(1).replace(",", ""))
        elif cost_m:
            dollar_val = float(cost_m.group(1).replace(",", ""))
        elif dollar_col_m:
            dollar_val = max(float(v.replace(",", "")) for v in dollar_col_m)
        elif dollar_m:
            dollar_val = max(float(v.replace(",", "")) for v in dollar_m)
        elif lbs_m:
            lbs = float(lbs_m[-1].replace(",", ""))
            dollar_val = round(lbs * 3.58, 2)
        else:
            dollar_val = 0.0
        return {
            "type":         "goods_donated",
            "amount":       dollar_val,
            "unit":         "CAD",
            "period":       _extract_period(text) or "Unknown",
            "provider":     _detect_provider(text),
            "cost_dollars": dollar_val,
            "_confidence":  "medium" if dollar_val > 0 else "low",
        }

    if doc_type == "payroll":
        cost_m   = _RE_COST.search(text)
        dollar_m = _RE_DOLLAR_ANY.findall(text)
        amount   = 0.0
        if cost_m:
            amount = float(cost_m.group(1).replace(",", ""))
        elif dollar_m:
            amount = max(float(v.replace(",", "")) for v in dollar_m)
        return {
            "type":         "payroll",
            "amount":       amount,
            "unit":         "CAD",
            "period":       _extract_period(text) or "Unknown",
            "cost_dollars": amount,
            "_confidence":  "medium",
        }

    if doc_type == "disbursement_voucher":
        return _parse_disbursement_voucher(text)

    if doc_type == "carbon_summary":
        return _parse_carbon_summary(text)

    if doc_type == "accounting_summary":
        return _parse_accounting_summary(text)

    if doc_type == "grant_applications":
        return _parse_grant_applications(text)

    # Absolute fallback
    return {
        "type":        "unknown",
        "amount":      0,
        "unit":        "",
        "period":      _extract_period(text) or "Unknown",
        "_confidence": "low",
    }


# ── Mileage total extractor ───────────────────────────────────────────────────

def _extract_km_total(text: str) -> float:
    """
    Extract the total kilometres from a mileage log.
    Handles both 'TOTAL KILOMETRES THIS MONTH: 143' and '143 km' formats.
    For trip logs, sums individual row values when no explicit total is found.
    """
    # 1. Explicit labelled total: "TOTAL KILOMETRES THIS MONTH: 143"
    m = _RE_KM_TOTAL_LABELED.search(text)
    if m:
        return float(m.group(1).replace(",", ""))

    # 2. Standard pattern: "total: 143 km" / "distance: 143 km"
    km_all = _RE_KM.findall(text)
    if km_all:
        return max(float(v.replace(",", "")) for v in km_all)

    # 3. Any inline "143 km" value (take the largest — likely the total/month)
    any_km = _RE_KM_ANY.findall(text)
    if any_km:
        return max(float(v.replace(",", "")) for v in any_km)

    # 4. Fallback: look for trip rows in a log table and sum them.
    # Trip log rows look like: "Apr 01 ... 8\n" where the last digit group on
    # each line is the km distance. We scan non-header rows only.
    trip_km: list[float] = []
    for line in text.splitlines():
        line = line.strip()
        # Skip header-like lines
        if not line or any(k in line.lower() for k in ("date", "origin", "dest", "purpose", "km", "total", "odometer", "supervisor")):
            continue
        # Last number on the line is likely the km value for that trip
        nums = re.findall(r"\b(\d+(?:\.\d+)?)\b", line)
        if nums:
            try:
                val = float(nums[-1])
                # Reasonable km per trip: 1–500 km
                if 1 <= val <= 500:
                    trip_km.append(val)
            except ValueError:
                pass
    if trip_km:
        return round(sum(trip_km), 1)

    return 0.0


# ── Summary CSV parsers ───────────────────────────────────────────────────────

_RE_TOTALS_KV = re.compile(r"(\w+):\s*([\d.]+)", re.IGNORECASE)


def _parse_totals_line(text: str) -> dict:
    """Extract key:value pairs from a TOTALS: ... line."""
    m = re.search(r"TOTALS:\s*(.*)", text, re.IGNORECASE)
    if not m:
        return {}
    result = {}
    for key, val in _RE_TOTALS_KV.findall(m.group(1)):
        try:
            result[key.lower()] = float(val)
        except ValueError:
            pass
    return result


def _parse_carbon_summary(text: str) -> dict:
    """
    Parse a carbon_emissions_raw CSV that has columns for fuel, electricity,
    natural gas, travel in monthly rows, with TOTALS appended by ocr.py.
    """
    t = _parse_totals_line(text)

    # Sum all CO2e components
    total_co2e = t.get("total_kg_co2e", 0.0)
    components: dict = {}

    # Map column name suffixes to emission categories
    mapping = [
        ("fuel_kg_co2e",          "fuel"),
        ("electricity_kg_co2e",   "electricity"),
        ("natural_gas_kg_co2e",   "natural_gas"),
        ("travel_kg_co2e",        "mileage"),
    ]
    computed_total = 0.0
    for col, cat in mapping:
        val = t.get(col, 0.0)
        if val:
            components[cat] = round(val, 3)
            computed_total += val

    if total_co2e == 0.0 and computed_total > 0:
        total_co2e = round(computed_total, 3)

    # Also keep physical amounts for drill-down
    amounts = {
        "fuel_litres":      t.get("fuel_litres", 0.0),
        "electricity_kwh":  t.get("electricity_kwh", 0.0),
        "natural_gas_gj":   t.get("natural_gas_gj", 0.0),
        "travel_km":        t.get("travel_km", 0.0),
    }

    period = _extract_period(text) or "Unknown"
    return {
        "type":        "carbon_summary",
        "amount":      round(total_co2e, 3),
        "unit":        "kg_co2e",
        "period":      period,
        "cost_dollars": 0,
        "components":  components,
        "amounts":     amounts,
        "_confidence": "high" if total_co2e > 0 else "low",
    }


def _parse_accounting_summary(text: str) -> dict:
    """
    Parse an accounting_summary CSV with monthly inflow/expense rows.
    Totals are computed by ocr.py and appended.
    Also handles revenue_category_totals CSV (category,q1_total,...).
    """
    t = _parse_totals_line(text)

    total_inflow  = t.get("total_inflow", 0.0)
    total_expense = t.get("total_expense", 0.0)

    # revenue_category_totals.csv: no inflow/expense columns → use q1_total
    if total_inflow == 0.0 and total_expense == 0.0:
        q1_total = t.get("q1_total", 0.0)
        if q1_total:
            total_inflow = q1_total

    # Also try to sum individual inflow columns if present
    if total_inflow == 0.0:
        for col in ("donations_inflow", "grants_inflow", "fundraising_inflow"):
            total_inflow += t.get(col, 0.0)

    period = _extract_period(text) or "Unknown"
    return {
        "type":          "accounting_summary",
        "amount":        round(total_inflow, 2),
        "unit":          "CAD",
        "period":        period,
        "cost_dollars":  round(total_expense, 2),
        "total_inflow":  round(total_inflow, 2),
        "total_expense": round(total_expense, 2),
        "_confidence":   "high" if (total_inflow + total_expense) > 0 else "low",
    }


def _parse_grant_applications(text: str) -> dict:
    """
    Parse a grant_applications CSV with columns:
    month, grant_name, grantor, amount_applied, amount_received, eligibility_criteria_met
    """
    t = _parse_totals_line(text)

    total_received = t.get("amount_received", 0.0)
    total_applied  = t.get("amount_applied",  0.0)

    # Parse individual rows to get eligibility percentages
    elig_matches = re.findall(
        r"eligibility_criteria_met:\s*([\d.]+)%?", text, re.IGNORECASE
    )
    avg_elig = 0.0
    if elig_matches:
        avg_elig = round(
            sum(float(e) for e in elig_matches) / len(elig_matches), 1
        )

    # Also collect grant names for the report
    grant_names = re.findall(r"grant_name:\s*([^,\n]+)", text, re.IGNORECASE)

    period = _extract_period(text) or "Unknown"
    return {
        "type":            "grant",
        "amount":          round(total_received, 2),
        "unit":            "CAD",
        "period":          period,
        "provider":        "Multiple Grantors",
        "cost_dollars":    round(total_received, 2),
        "total_applied":   round(total_applied, 2),
        "avg_eligibility": avg_elig,
        "grant_names":     grant_names,
        "_confidence":     "high" if total_received > 0 else "low",
    }


# ── Main heuristic parser ─────────────────────────────────────────────────────

def _heuristic_parse(text: str, hint_type: str | None = None) -> dict:
    """
    Determine document type + extract values using regex patterns.
    hint_type (from filename or OCR hint) guides which parser to try first.
    """

    # ── Summary CSV/XLSX types — dispatch immediately when hint is set ──────
    if hint_type == "carbon_summary":
        return _parse_carbon_summary(text)
    if hint_type == "accounting_summary":
        return _parse_accounting_summary(text)
    if hint_type == "grant_applications":
        return _parse_grant_applications(text)

    # If the filename already identifies a utility/carbon document, text-based
    # social/admin pattern checks must not override it.
    _CARBON_HINTS = {"electricity", "natural_gas", "fuel", "mileage"}
    _skip_social  = hint_type in _CARBON_HINTS

    # ── Payroll / paystub — check early (it often contains dollar amounts
    #    that confuse other parsers) ─────────────────────────────────────────
    if hint_type == "payroll" or (not _skip_social and _PAYROLL_RE.search(text)):
        return _extract_by_type(text, "payroll")

    # ── Disbursement voucher / multi-item reconciliation ───────────────────
    if hint_type == "disbursement_voucher" or (not _skip_social and _VOUCHER_RE.search(text)):
        return _parse_disbursement_voucher(text)

    # ── Volunteer log ──────────────────────────────────────────────────────
    if hint_type == "volunteers" or (not _skip_social and _VOL_RE.search(text)):
        return _parse_volunteers(text)

    # ── Goods donated / food donation ─────────────────────────────────────
    if hint_type == "goods_donated" or _GOODS_DONATED_RE.search(text):
        result = _extract_by_type(text, "goods_donated")
        if result.get("_confidence") != "low" or hint_type == "goods_donated":
            return result

    # ── Grant document ─────────────────────────────────────────────────────
    if hint_type == "grant" or _GRANT_RE.search(text):
        result = _extract_by_type(text, "grant")
        if result.get("_confidence") != "low" or hint_type == "grant":
            return result

    # ── Electricity (BC Hydro / FortisBC electricity) ─────────────────────
    elec_signal = hint_type == "electricity" or _ELEC_RE.search(text) or _BCHYDRO_RE.search(text)
    if elec_signal:
        kwh_matches = _RE_KWH.findall(text)
        if kwh_matches:
            consumption_m = re.search(
                r"(?:consumption|consumed|total)[:\s]+([\d,]+(?:\.\d+)?)\s*kwh",
                text, re.IGNORECASE,
            )
            amount = float(
                (consumption_m.group(1) if consumption_m else kwh_matches[-1]).replace(",", "")
            )
            return {
                "type":         "electricity",
                "amount":       amount,
                "unit":         "kWh",
                "period":       _extract_elec_period(text),
                "provider":     _detect_provider(text),
                "cost_dollars": _extract_cost_dollars(text),
                "_confidence":  "high" if consumption_m else "medium",
            }
        # Electricity signalled but no kWh found — try targeted
        if elec_signal:
            return _extract_by_type(text, "electricity")

    # ── Fuel receipt — checked BEFORE mileage so fuel hints are not
    #    overridden by _MILEAGE_RE matching vehicle names in receipts ────────
    if hint_type == "fuel" or _FUEL_RE.search(text):
        litres = _extract_litres(text)
        if litres is not None:
            labelled = re.search(
                r"total\s+litre[s]?\s*(?:dispensed)?[:\s]*([\d,]+\.?\d*)",
                text, re.IGNORECASE,
            )
            return {
                "type":         "fuel",
                "amount":       litres,
                "unit":         "L",
                "period":       _extract_period(text),
                "provider":     _detect_provider(text),
                "cost_dollars": _extract_cost_dollars(text),
                "_confidence":  "high" if labelled else "medium",
            }
        if hint_type == "fuel":
            return _extract_by_type(text, "fuel")
        return {
            "type":         "fuel",
            "amount":       0,
            "unit":         "L",
            "period":       _extract_period(text),
            "provider":     _detect_provider(text),
            "cost_dollars": _extract_cost_dollars(text),
            "_confidence":  "low",
        }

    # ── Mileage / vehicle trip log ─────────────────────────────────────────
    if hint_type == "mileage" or _MILEAGE_RE.search(text):
        km = _extract_km_total(text)
        return {
            "type":        "mileage",
            "amount":      km,
            "unit":        "km",
            "period":      _extract_period(text) or "Unknown",
            "_confidence": "high" if km > 0 else "low",
        }

    # ── Natural gas (FortisBC — GJ preferred, m³ fallback) ────────────────
    if hint_type == "natural_gas" or _FORTISBC_RE.search(text) or _GJ_strong(text):
        gj_matches = _RE_GJ.findall(text)
        if gj_matches:
            return {
                "type":         "natural_gas",
                "amount":       float(gj_matches[-1].replace(",", "")),
                "unit":         "GJ",
                "period":       _extract_period(text),
                "provider":     _detect_provider(text),
                "cost_dollars": _extract_cost_dollars(text),
                "_confidence":  "high",
            }
        m3_matches = _RE_M3.findall(text)
        if m3_matches:
            return {
                "type":         "natural_gas",
                "amount":       round(float(m3_matches[-1].replace(",", "")) * 0.03793, 3),
                "unit":         "GJ",
                "period":       _extract_period(text),
                "provider":     "FortisBC",
                "cost_dollars": _extract_cost_dollars(text),
                "_confidence":  "medium",
            }
        if hint_type == "natural_gas":
            return _extract_by_type(text, "natural_gas")

    # ── Generic GJ fallback ────────────────────────────────────────────────
    gj_generic = _RE_GJ.findall(text)
    if gj_generic:
        amount = float(gj_generic[-1].replace(",", ""))
        if amount > 0:
            return {
                "type":         "natural_gas",
                "amount":       amount,
                "unit":         "GJ",
                "period":       _extract_period(text),
                "provider":     _detect_provider(text),
                "cost_dollars": _extract_cost_dollars(text),
                "_confidence":  "medium",
            }

    # ── Generic fuel fallback — any litre value ────────────────────────────
    generic_litres = _extract_litres(text)
    if generic_litres is not None and generic_litres > 0:
        return {
            "type":         "fuel",
            "amount":       generic_litres,
            "unit":         "L",
            "period":       _extract_period(text),
            "provider":     _detect_provider(text),
            "cost_dollars": _extract_cost_dollars(text),
            "_confidence":  "medium",
        }

    # ── Generic kWh fallback ───────────────────────────────────────────────
    kwh_matches = _RE_KWH.findall(text)
    if kwh_matches:
        consumption_m = re.search(
            r"(?:consumption|consumed|total)[:\s]+([\d,]+(?:\.\d+)?)\s*kwh",
            text, re.IGNORECASE,
        )
        amount = float(
            (consumption_m.group(1) if consumption_m else kwh_matches[-1]).replace(",", "")
        )
        return {
            "type":         "electricity",
            "amount":       amount,
            "unit":         "kWh",
            "period":       _extract_elec_period(text),
            "provider":     _detect_provider(text),
            "cost_dollars": _extract_cost_dollars(text),
            "_confidence":  "high" if consumption_m else "medium",
        }

    # ── Nothing matched — flag for manual review ───────────────────────────
    return {
        "type":        "unknown",
        "amount":      0,
        "unit":        "",
        "period":      _extract_period(text) or "Unknown",
        "_confidence": "low",
    }


# ── Disbursement voucher parser ───────────────────────────────────────────────

def _parse_disbursement_voucher(text: str) -> dict:
    """Parse a multi-item disbursement voucher / expense reconciliation document."""
    grant_m   = re.search(r"grant\s+code\s*[:\s]+([A-Z0-9\-]+)", text, re.IGNORECASE)
    project_m = re.search(r"project\s*[:\s]+(.+)", text, re.IGNORECASE)
    grant_code = grant_m.group(1).strip()  if grant_m   else None
    project    = project_m.group(1).strip() if project_m else None

    items: list[dict] = []
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"Vendor\s*:", stripped, re.IGNORECASE):
            item = _classify_expense_line(stripped)
            if item is not None:
                items.append(item)

    # Also try to parse rows from an expense log spreadsheet
    if not items:
        items = _parse_expense_log_rows(text)

    compliance_flags: list[str] = []
    block_lines: list[str] = []
    capture = False
    for line in text.splitlines():
        stripped = line.strip()
        if re.search(
            r"compliance\s+check|bill\s+c-59|scope\s+[123]\b|pending\s+.*audit"
            r"|carbon\s+neutral|science.based",
            stripped, re.IGNORECASE,
        ):
            capture = True
        if capture and stripped:
            block_lines.append(stripped)
        elif capture and not stripped:
            joined = " ".join(block_lines)
            joined = re.sub(r"^Compliance\s+Check:\s*", "", joined, flags=re.IGNORECASE)
            joined = re.sub(r"^Status:\s*", "", joined, flags=re.IGNORECASE)
            if joined:
                compliance_flags.append(joined)
            block_lines = []
            capture = False
    if block_lines:
        joined = " ".join(block_lines)
        joined = re.sub(r"^Compliance\s+Check:\s*", "", joined, flags=re.IGNORECASE)
        joined = re.sub(r"^Status:\s*", "", joined, flags=re.IGNORECASE)
        if joined:
            compliance_flags.append(joined)

    # Try extracting grand total from the TOTALS line computed by ocr._read_xlsx/_read_csv
    grand_total = sum(i.get("amount_dollars", 0) or 0 for i in items)
    totals_m = re.search(
        r"TOTALS:.*?(?:Total\s+Paid|Amount\s+excl|amount|cost|total)[^\d\n$]*(\d[\d,]*(?:\.\d+)?)",
        text, re.IGNORECASE | re.DOTALL,
    )
    if totals_m and grand_total == 0:
        grand_total = float(totals_m.group(1).replace(",", ""))

    # Extract period from first data row
    period_m = re.search(r"\b(20\d\d-\d\d-\d\d|\w+ \d{4})\b", text, re.IGNORECASE)
    period = period_m.group(1) if period_m else _extract_period(text) or "Unknown"

    if not items and grand_total == 0:
        doc_confidence = "low"
    elif all(i.get("_confidence") == "high" for i in items):
        doc_confidence = "high"
    else:
        doc_confidence = "medium"

    return {
        "type":             "disbursement_voucher",
        "grant_code":       grant_code,
        "project":          project,
        "items":            items,
        "cost_dollars":     round(grand_total, 2),
        "amount":           round(grand_total, 2),
        "period":           period,
        "compliance_flags": compliance_flags,
        "_confidence":      doc_confidence,
    }


_RE_AMT_FLEX = re.compile(
    r"(?:Total\s+Paid|Amount\s+excl|amount|cost|total)[^\d\n$]*(\d[\d,]*(?:\.\d+)?)",
    re.IGNORECASE,
)

def _parse_expense_log_rows(text: str) -> list[dict]:
    """
    Parse rows from an expense-log spreadsheet (key: value format from ocr._read_xlsx).
    Handles formats like:
      'Category: Fuel, Total Paid ($): 134.53, Date: 2023-07-12'
      'category: Fuel, Amount (excl. tax): 128.12'
    """
    items = []
    for line in text.splitlines():
        if line.startswith("TOTALS:"):   # skip the aggregate totals row
            continue
        cat_m    = re.search(r"(?:category|type|description)[:\s]+([^,\n]+)", line, re.IGNORECASE)
        amt_m    = _RE_AMT_FLEX.search(line)
        date_m   = re.search(r"(?:date)[:\s]+([A-Za-z\s\d\-/,]+?)(?:,|$)", line, re.IGNORECASE)
        vendor_m = re.search(r"(?:vendor|payee|supplier)[:\s]+([^,\n]+)", line, re.IGNORECASE)

        if amt_m and cat_m:
            desc = cat_m.group(1).strip()
            amount_dollars = float(amt_m.group(1).replace(",", ""))
            if amount_dollars <= 0:
                continue
            vendor = vendor_m.group(1).strip() if vendor_m else desc
            date   = date_m.group(1).strip() if date_m else None
            item = _classify_expense_line(
                f"Vendor: {vendor} ({desc}) | Date: {date or 'Unknown'} | Amount: ${amount_dollars}"
            )
            if item:
                items.append(item)
    return items


def _classify_expense_line(line: str) -> dict | None:
    """Parse and classify a single pipe-delimited expense line."""
    vendor_m = re.search(
        r"Vendor\s*:\s*([^|()\n]+?)(?:\s*\(([^)]+)\))?\s*(?:\||$)",
        line, re.IGNORECASE,
    )
    if not vendor_m:
        return None
    vendor      = vendor_m.group(1).strip()
    description = vendor_m.group(2).strip() if vendor_m.group(2) else ""

    date_m = re.search(r"Date\s*:\s*([A-Za-z]+\s+\d+)", line, re.IGNORECASE)
    date   = date_m.group(1).strip() if date_m else None

    amt_m = re.search(r"Amount\s*:\s*\$?([\d,]+(?:\.\d+)?)", line, re.IGNORECASE)
    amount_dollars = float(amt_m.group(1).replace(",", "")) if amt_m else None

    if amount_dollars is None:
        return {
            "vendor": vendor, "description": description, "date": date,
            "amount_dollars": None, "type": "unknown", "amount": 0, "unit": "",
            "_confidence": "low",
            "_flag": "Dollar amount could not be parsed — manual entry required",
        }

    base = {"vendor": vendor, "description": description, "date": date, "amount_dollars": amount_dollars}

    fuel_signal = _FUEL_RE.search(vendor) or re.search(
        r"\bfuel\b|\bpetrol\b|\bgasoline\b|\bdiesel\b", description, re.IGNORECASE
    )
    if fuel_signal:
        litres = _extract_litres(line)
        if litres is not None:
            return {**base, "type": "fuel", "amount": litres, "unit": "L",
                    "provider": _detect_provider(f"{vendor} {description}"), "_confidence": "high"}
        return {**base, "type": "fuel", "amount": 0, "unit": "L",
                "provider": _detect_provider(vendor), "_confidence": "low",
                "_flag": "Fuel vendor identified but litre quantity not found — manual entry required"}

    travel_signal = _TRAVEL_RE.search(vendor) or re.search(
        r"\bflight\b|\bairfare\b|\btravel\b|\bconference\b|\btransit\b", description, re.IGNORECASE
    )
    if travel_signal:
        return {**base, "type": "travel_scope3", "amount": 0, "unit": "", "_confidence": "low",
                "_flag": "Air travel is a potential Scope 3 emission — manual review required"}

    admin_signal = _ADMIN_RE.search(vendor) or re.search(
        r"\bprinting\b|\bsupplies\b|\boffice\b|\bsubscription\b|\bsoftware\b", description, re.IGNORECASE
    )
    if admin_signal:
        return {**base, "type": "non_emission", "amount": 0, "unit": "", "_confidence": "medium",
                "_flag": "Administrative expense — no direct GHG emission source"}

    return {**base, "type": "unknown", "amount": 0, "unit": "", "_confidence": "low",
            "_flag": f"Vendor '{vendor}' not recognised as a known emission source"}


# ── Volunteer parser ──────────────────────────────────────────────────────────

def _parse_volunteers(text: str) -> dict:
    """Aggregate volunteer hours from multiple text shapes."""
    agg: dict[str, dict] = {}

    # Shape 1 — key:value lines: "First Name: X, Last Name: Y, Hours: Z"
    kv_pattern = re.compile(
        r"First Name:\s*([A-Za-z\-]+),\s*Last Name:\s*([A-Za-z\-]+)"
        r".*?Hours:\s*([\d]+\.?\d*)",
        re.DOTALL,
    )
    for first, last, hours in kv_pattern.findall(text):
        key = f"{first.lower()} {last.lower()}"
        agg.setdefault(key, {"name": f"{first} {last}", "hours": 0.0, "activities": []})
        agg[key]["hours"] += float(hours)

    if not agg:
        # Shape 4 — "Volunteer Name" column header
        volname_header = re.search(
            r"(?:^|\n)[^\n]*volunteer\s+name[^\n]*hours[^\n]*\n(.*)",
            text, re.IGNORECASE | re.DOTALL,
        )
        if volname_header:
            header_line = re.search(r"(?:^|\n)([^\n]*volunteer\s+name[^\n]*)", text, re.IGNORECASE)
            if header_line:
                cols = [c.strip().lower() for c in header_line.group(1).split(",")]
                try:
                    name_col  = next(i for i, c in enumerate(cols) if "volunteer" in c or "name" in c)
                    hours_col = next(i for i, c in enumerate(cols) if "hour" in c)
                    act_col   = next((i for i, c in enumerate(cols) if "activ" in c), None)
                    for line in text.splitlines():
                        parts = [p.strip() for p in line.split(",")]
                        if len(parts) <= max(name_col, hours_col):
                            continue
                        name_val  = parts[name_col]
                        hours_val = parts[hours_col]
                        if not name_val or name_val.lower() in ("volunteer name", "name", "total"):
                            continue
                        if not re.match(r"^\d", parts[0]):
                            continue
                        try:
                            hrs = float(hours_val)
                        except ValueError:
                            continue
                        key = name_val.strip().lower()
                        act = parts[act_col].strip() if act_col is not None and act_col < len(parts) else ""
                        agg.setdefault(key, {"name": name_val.strip(), "hours": 0.0, "activities": []})
                        agg[key]["hours"] += hrs
                        if act and act not in agg[key]["activities"]:
                            agg[key]["activities"].append(act)
                except StopIteration:
                    pass

    if not agg:
        # Shape 2 — raw CSV rows: FirstName,LastName,YYYY-MM-DD,...,hours
        csv_row = re.compile(
            r"([A-Z][a-z\-]+),\s*([A-Z][a-z\-]+),\s*\d{4}-\d{2}-\d{2}"
            r"[^,]*(?:,[^,]*){2,4}?,\s*([\d]+\.?\d*)",
            re.MULTILINE,
        )
        for first, last, hours in csv_row.findall(text):
            key = f"{first.lower()} {last.lower()}"
            agg.setdefault(key, {"name": f"{first} {last}", "hours": 0.0, "activities": []})
            agg[key]["hours"] += float(hours)

    if not agg:
        # Shape 3 — free-text "Jane Doe  12"
        for m in _RE_VOL_ROW.finditer(text):
            key  = m.group(1).strip().lower()
            name = m.group(1).strip()
            hrs  = float(m.group(2))
            agg.setdefault(key, {"name": name, "hours": 0.0, "activities": []})
            agg[key]["hours"] += hrs

    if not agg:
        # Shape 5 — "Volunteer Name: Sarah Miller, Activity: Food Sorting, Hours: 4.5"
        for line in text.splitlines():
            m = _RE_KV_VOL.search(line)
            if not m:
                continue
            name = m.group(1).strip()
            act  = (m.group(2) or "").strip()
            hrs  = float(m.group(3))
            key  = name.lower()
            agg.setdefault(key, {"name": name, "hours": 0.0, "activities": []})
            agg[key]["hours"] += hrs
            if act and act not in agg[key]["activities"]:
                agg[key]["activities"].append(act)

    volunteers = list(agg.values()) if agg else []
    total_hrs  = round(sum(v.get("hours", 0) for v in volunteers), 2)
    period     = _extract_period(text) or "Unknown"
    return {
        "type":        "volunteers",
        "volunteers":  volunteers,
        "amount":      total_hrs,
        "unit":        "hrs",
        "period":      period,
        "cost_dollars": 0,
        "_confidence": "high" if agg else "low",
    }


# ── Helper functions ──────────────────────────────────────────────────────────

def _extract_litres(text: str) -> float | None:
    total_m = re.search(
        r"total\s+litre[s]?\s*(?:dispensed)?[:\s]+([\d,]+\.?\d*)", text, re.IGNORECASE
    )
    if total_m:
        return float(total_m.group(1).replace(",", ""))
    labelled_m = re.search(r"(?<!\w)litres?\s*[:\s]\s*([\d,]+(?:\.\d+)?)", text, re.IGNORECASE)
    if labelled_m:
        return float(labelled_m.group(1).replace(",", ""))
    m = _RE_LITRES.search(text)
    if m:
        return float(m.group(1).replace(",", ""))
    m2 = _RE_LITRES_INLINE.search(text)
    if m2:
        return float(m2.group(1))
    return None


def _GJ_strong(text: str) -> bool:
    return len(_RE_GJ.findall(text)) >= 1 and "gas" in text.lower()


def _extract_cost_dollars(text: str) -> float | None:
    m = _RE_COST.search(text)
    return float(m.group(1).replace(",", "")) if m else None


def _detect_provider(text: str) -> str:
    if _BCHYDRO_RE.search(text):                                return "BC Hydro"
    if _FORTISBC_RE.search(text):                               return "FortisBC"
    if re.search(r"petro.?canada", text, re.IGNORECASE):        return "Petro-Canada"
    if re.search(r"\besso\b",      text, re.IGNORECASE):        return "Esso"
    if re.search(r"\bshell\b",     text, re.IGNORECASE):        return "Shell"
    if re.search(r"\bchevron\b",   text, re.IGNORECASE):        return "Chevron"
    return "Unknown Provider"


def _extract_elec_period(text: str) -> str:
    meter_m = _RE_METER_MONTH.search(text)
    if meter_m:
        month  = meter_m.group(1).capitalize()
        year_m = re.search(r"\b(202[3-9]|203\d)\b", text)
        year   = year_m.group(1) if year_m else ""
        return f"{month} {year}".strip()
    bp_m = re.search(
        r"(?:billing|service)\s+period[:\s]+"
        r"(january|february|march|april|may|june|july|august|september"
        r"|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)"
        r"[\s\-–]+(\d{4})?",
        text, re.IGNORECASE,
    )
    if bp_m:
        year = bp_m.group(2) or ""
        return f"{bp_m.group(1).capitalize()} {year}".strip()
    return _extract_period(text)


def _extract_period(text: str) -> str:
    m = _RE_PERIOD.search(text)
    if m:
        return f"{m.group(1).capitalize()} {m.group(2)}"
    m2 = re.search(
        r"(january|february|march|april|may|june|july|august|september"
        r"|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)"
        r"\s+\d{1,2}[,\s]+(\d{4})",
        text, re.IGNORECASE,
    )
    if m2:
        return f"{m2.group(1).capitalize()} {m2.group(2)}"
    q = _RE_Q_PERIOD.search(text)
    if q:
        return f"{q.group(1).upper()} {q.group(2)}"
    # FY pattern: "FY2024" or "FY 2024"
    fy = re.search(r"FY\s*(\d{4})", text, re.IGNORECASE)
    if fy:
        return f"FY{fy.group(1)}"
    y = re.search(r"\b(202[3-9]|203\d)\b", text)
    return y.group(1) if y else "Unknown Period"


# ── Multi-module assignment ────────────────────────────────────────────────────
# Each document contributes to Carbon, Accounting, and/or Grants simultaneously.
# This is the core of the multi-module approach: a single document (e.g. an
# electricity bill) contributes kWh to Carbon, the dollar cost to Accounting,
# and serves as evidence in Grants — all at once.

_CARBON_TYPES  = {"electricity", "natural_gas", "fuel", "mileage"}
_SCOPE_ASSIGN  = {"electricity": 2, "natural_gas": 1, "fuel": 1, "mileage": 1}
_ACCT_CATEGORY = {
    "electricity":          "Utilities",
    "natural_gas":          "Utilities",
    "fuel":                 "Fleet Operations",
    "mileage":              "Fleet Operations",
    "grant":                "Grant Revenue",
    "goods_donated":        "In-Kind Donations",
    "payroll":              "Payroll",
    "disbursement_voucher": "Disbursements",
    "volunteers":           "Volunteer Value",
}
_SOCIAL_WAGE_AI = 35.0  # CAD/hr — community value rate


def _assign_modules(extracted: dict) -> dict:
    """
    Analyse extracted data and return which compliance modules this document
    contributes to, with the relevant data for each module:

      modules["carbon"]     — GHG inventory (kWh / GJ / L / km → CO2e)
      modules["accounting"] — Financial flow (expense or inflow in dollars)
      modules["grants"]     — Evidence for grant readiness scoring

    One document can populate one, two, or all three modules.
    """
    doc_type = extracted.get("type", "unknown")
    period   = extracted.get("period") or "Unknown"
    amount   = extracted.get("amount") or 0
    unit     = extracted.get("unit", "")
    cost     = extracted.get("cost_dollars") or 0
    provider = extracted.get("provider", "Unknown Provider")
    modules: dict = {}

    # ── Emission-source documents → Carbon + Accounting (expense) + Grants ─
    if doc_type in _CARBON_TYPES:
        modules["carbon"] = {
            "doc_type": doc_type,
            "amount":   amount,
            "unit":     unit,
            "period":   period,
            "provider": provider,
            "scope":    _SCOPE_ASSIGN[doc_type],
        }
        if cost > 0:
            modules["accounting"] = {
                "flow_type":      "expense",
                "category":       _ACCT_CATEGORY.get(doc_type, "Operations"),
                "amount_dollars": cost,
                "period":         period,
            }
        # Having verified emission data proves monitoring → supports grant apps
        modules["grants"] = {
            "evidence_type":   doc_type,
            "covers_category": doc_type,
            "period":          period,
        }

    # ── Grant documents → Accounting (revenue inflow) + Grants (evidence) ─
    elif doc_type == "grant":
        g = cost or amount
        modules["accounting"] = {
            "flow_type":      "inflow",
            "category":       "Grant Revenue",
            "amount_dollars": g,
            "period":         period,
        }
        modules["grants"] = {
            "evidence_type":   "grant_received",
            "covers_category": "grant",
            "amount_dollars":  g,
            "provider":        provider,
            "period":          period,
        }

    # ── Goods donated → Accounting (in-kind inflow) + Grants (social impact)
    elif doc_type == "goods_donated":
        d = cost or amount
        modules["accounting"] = {
            "flow_type":      "inflow",
            "category":       "In-Kind Donations",
            "amount_dollars": d,
            "period":         period,
        }
        modules["grants"] = {
            "evidence_type":   "social_impact",
            "covers_category": "social",
            "amount_dollars":  d,
            "period":          period,
        }

    # ── Volunteers → Accounting (community value) + Grants (social impact) ─
    elif doc_type == "volunteers" or "volunteers" in extracted:
        vols = extracted.get("volunteers") or []
        hrs  = sum(v.get("hours", 0) for v in vols) if isinstance(vols, list) else 0
        cv   = round(hrs * _SOCIAL_WAGE_AI, 2)
        modules["accounting"] = {
            "flow_type":       "social_value",
            "category":        "Volunteer Value",
            "amount_dollars":  cv,
            "period":          period,
            "total_hours":     hrs,
            "volunteer_count": len(vols) if isinstance(vols, list) else 0,
        }
        modules["grants"] = {
            "evidence_type":   "social_impact",
            "covers_category": "social",
            "total_hours":     hrs,
            "community_value": cv,
            "period":          period,
        }

    # ── Carbon summary (multi-month comprehensive CSV) → Carbon + Grants ──
    elif doc_type == "carbon_summary":
        total_co2e = extracted.get("amount", 0)
        components = extracted.get("components", {})
        modules["carbon"] = {
            "doc_type":   "carbon_summary",
            "amount":     total_co2e,
            "unit":       "kg_co2e",
            "period":     period,
            "components": components,
        }
        modules["grants"] = {
            "evidence_type":   "carbon_monitoring",
            "covers_category": "electricity",   # counts as emission evidence
            "total_co2e":      total_co2e,
            "period":          period,
        }

    # ── Accounting summary (monthly financial CSV) → Accounting ────────────
    elif doc_type == "accounting_summary":
        total_inflow  = extracted.get("total_inflow",  0)
        total_expense = extracted.get("total_expense", extracted.get("cost_dollars", 0))
        modules["accounting"] = {
            "flow_type":      "summary",
            "category":       "Operations Summary",
            "amount_dollars": total_inflow,
            "total_inflow":   total_inflow,
            "total_expense":  total_expense,
            "period":         period,
        }
        if total_inflow > 0:
            modules["grants"] = {
                "evidence_type":   "financial_records",
                "covers_category": "social",
                "amount_dollars":  total_inflow,
                "period":          period,
            }

    # ── Payroll → Accounting (expense) only ──────────────────────────────
    elif doc_type == "payroll":
        modules["accounting"] = {
            "flow_type":      "expense",
            "category":       "Payroll",
            "amount_dollars": cost or amount,
            "period":         period,
        }

    # ── Disbursement voucher → Accounting + maybe Carbon + maybe Grants ──
    elif doc_type == "disbursement_voucher":
        items      = extracted.get("items") or []
        total_cost = sum(i.get("amount_dollars", 0) or 0 for i in items) or cost
        modules["accounting"] = {
            "flow_type":      "expense",
            "category":       "Disbursements",
            "amount_dollars": total_cost,
            "period":         period,
            "items":          items,
        }
        carbon_items = [i for i in items if i.get("type") in _CARBON_TYPES]
        if carbon_items:
            modules["carbon"] = {
                "doc_type": "disbursement_voucher",
                "items":    carbon_items,
                "period":   period,
            }
        if extracted.get("grant_code"):
            modules["grants"] = {
                "evidence_type":   "grant_disbursement",
                "covers_category": "grant",
                "grant_code":      extracted.get("grant_code"),
                "period":          period,
            }

    return modules
