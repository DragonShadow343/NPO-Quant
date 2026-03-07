"""
ai.py — LLM extraction with regex heuristic fallback.

Returns a dict that always includes an `_confidence` field:
  "high"   — Ollama returned parseable JSON
  "medium" — heuristic matched a known pattern
  "low"    — could only produce a default / nothing matched

The pipeline uses this to decide whether to flag the item for manual review.

Document types handled:
  electricity        — BC Hydro or FortisBC electricity bill (kWh)
  natural_gas        — FortisBC natural gas bill (GJ)
  fuel               — fleet fuel receipt (litres)
  volunteers         — volunteer hour log
  disbursement_voucher — multi-item expense reconciliation / grant voucher
                         (always routes to manual review; items classified individually)
"""
import json
import re
try:
    import ollama
    _OLLAMA_AVAILABLE = True
except ImportError:
    _OLLAMA_AVAILABLE = False
# ── Prompt templates ──────────────────────────────────────────────────────────

_PROMPT = """You are a data extraction engine for Canadian nonprofit compliance reporting.
Extract ONLY the fields shown below from the document. Reply with raw JSON only.
No markdown fences. No explanation. No extra keys.

For BC Hydro / utility electricity bills:
{{"type": "electricity", "amount": 1247, "unit": "kWh", "period": "January 2026", "provider": "BC Hydro"}}

For FortisBC / natural gas bills (amount in GJ):
{{"type": "natural_gas", "amount": 5.41, "unit": "GJ", "period": "January 2026", "provider": "FortisBC"}}

For fuel / fleet receipts (total litres across all transactions):
{{"type": "fuel", "amount": 218.1, "unit": "L", "period": "January 2026", "provider": "Petro-Canada"}}

For volunteer logs (one entry per unique volunteer, aggregate their hours):
{{"volunteers": [{{"name": "Sarah Chen", "hours": 12, "activities": ["Food Sorting"]}}]}}

For disbursement vouchers / expense reconciliation documents (multiple line items of mixed types):
{{"type": "disbursement_voucher", "grant_code": "BC-COMM-2026-8829", "project": "Winter Warmth Initiative",
  "items": [
    {{"vendor": "Shell Kelowna", "description": "Fuel for delivery van", "date": "Jan 14",
      "amount_dollars": 84.20, "type": "fuel", "amount": 42.0, "unit": "L",
      "provider": "Shell", "_confidence": "high"}},
    {{"vendor": "Staples Kelowna", "description": "Printing brochures", "date": "Jan 18",
      "amount_dollars": 215.12, "type": "non_emission", "amount": 0, "unit": "",
      "_confidence": "medium",
      "_flag": "Administrative expense — no direct GHG emission source"}},
    {{"vendor": "WestJet", "description": "Regional Conference", "date": "Jan 22",
      "amount_dollars": 412.00, "type": "travel_scope3", "amount": 0, "unit": "",
      "_confidence": "low",
      "_flag": "Air travel is a potential Scope 3 emission — manual review required; distance/passenger data unavailable"}}
  ],
  "compliance_flags": ["Carbon Neutral Delivery claim requires science-based data per Bill C-59 — pending manual audit for Scope 3 emissions"]
}}

IMPORTANT: If the document contains multiple expense line items of different types, ALWAYS use
the disbursement_voucher format. NEVER collapse a multi-item document into a single type.
If you are unsure about any field, set "_confidence": "low" and add a "_flag" explaining why.

Document text (first 3000 chars):
{text}"""


def extract_data(raw_text: str) -> dict:
    if _OLLAMA_AVAILABLE:
        try:
            prompt = _PROMPT.format(text=raw_text[:3000])
            resp = ollama.chat(
                model="llama3",
                messages=[{"role": "user", "content": prompt}],
            )
            content = resp["message"]["content"]
            data = _parse_json(content)
            data.setdefault("_confidence", "high")
            return data
        except Exception:
            pass
    return _heuristic_parse(raw_text)


# ── JSON helpers ──────────────────────────────────────────────────────────────

def _parse_json(text: str) -> dict:
    text = re.sub(r"```[a-z]*", "", text).strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group())
    raise ValueError("No JSON in response")


# ── Heuristic parser — O(n), single pass per document ────────────────────────

# Pre-compiled patterns
_RE_KWH      = re.compile(r"([\d,]+(?:\.\d+)?)\s*kwh", re.IGNORECASE)
# Disbursement voucher / multi-item reconciliation signals
_VOUCHER_RE  = re.compile(
    r"disbursement\s+voucher|expense\s+log\s*:|grant\s+code\s*:|reconciliation",
    re.IGNORECASE,
)
# Travel vendors (potential Scope 3)
_TRAVEL_RE   = re.compile(
    r"westjet|air\s*canada|porter\s+airlines|swoop|flair\s+airlines|transat"
    r"|air\s+transat|delta\s+airlines|united\s+airlines|american\s+airlines"
    r"|via\s*rail|greyhound|enterprise\s+rent|hertz|avis|budget\s+rent"
    r"|lyft|uber\b",
    re.IGNORECASE,
)
# Administrative / non-emission vendors
_ADMIN_RE    = re.compile(
    r"staples|office\s+depot|best\s*buy|printing|brochure|stationery"
    r"|software\s+subscription|catering|restaurant|hotel|accommodation",
    re.IGNORECASE,
)
_RE_GJ       = re.compile(r"([\d,]+(?:\.\d+)?)\s*(?:gj\b|gigajoule)", re.IGNORECASE)
_RE_M3       = re.compile(r"([\d,]+(?:\.\d+)?)\s*(?:m³|m3|cubic met)", re.IGNORECASE)
# Match litres in forms: "42 L", "42L", "42 litres", "42litre", "218.1 L"
_RE_LITRES   = re.compile(
    r"([\d,]+(?:\.\d+)?)\s*(?:litres?|liters?|(?<!\w)L(?!\w))",
    re.IGNORECASE,
)
# Also match inline "NNL" with word-boundary workaround
_RE_LITRES_INLINE = re.compile(r"(\d+(?:\.\d+)?)L\b", re.IGNORECASE)

_RE_PERIOD   = re.compile(
    r"(january|february|march|april|may|june|july|august|september"
    r"|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)"
    r"[\s\-–]+(\d{4})",
    re.IGNORECASE,
)
_RE_Q_PERIOD = re.compile(r"(Q[1-4])\s*(\d{4})", re.IGNORECASE)
# Electricity-specific: "Previous Reading (Jan 01)" or "Current Reading (Jan 31)"
_RE_METER_MONTH = re.compile(
    r"(?:previous|current|start|end)\s+reading.*?\("
    r"(january|february|march|april|may|june|july|august|september"
    r"|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)"
    r"\s+\d{1,2}\)",
    re.IGNORECASE,
)
# Volunteer key-value rows: "..., Volunteer Name: Sarah Miller, Activity: X, Hours: 4.5, ..."
_RE_KV_VOL = re.compile(
    r"Volunteer\s+Name:\s*([A-Za-z][A-Za-z\-]*(?:\s+[A-Za-z][A-Za-z\-]*)+)"
    r".*?Activity:\s*([^,\n]+)"
    r".*?Hours:\s*([\d]+\.?\d*)",
    re.IGNORECASE,
)
_RE_VOL_ROW  = re.compile(
    r"([A-Z][a-zÀ-ÖØ-öø-ÿ]+(?:\s[A-Z][a-zÀ-ÖØ-öø-ÿ]+)+)"  # full name
    r"[\s,|]+(\d+(?:\.\d+)?)\s*(?:hrs?|hours?|h\b)?",         # hours
    re.IGNORECASE,
)
# Providers
_FORTISBC_RE = re.compile(r"fortisbc|fortis\s+bc|natural.?gas|gas bill|rate\s*3\b", re.IGNORECASE)
_BCHYDRO_RE  = re.compile(r"bc.?hydro|hydro.?electricity|rate\s*1[12]\d\d", re.IGNORECASE)
_FUEL_RE     = re.compile(
    r"petro.?canada|esso|shell|chevron|fleet.?card|litres?\s+dispensed|fuel\s+receipt",
    re.IGNORECASE,
)
_VOL_RE      = re.compile(r"volunteer|vol\.?\s*id|hours\s+log|time\s+in", re.IGNORECASE)
# Electricity signals (catches documents labelled electricity even from FortisBC)
_ELEC_RE     = re.compile(r"electricity\s+service|electricity\s+statement|electric\s+bill|kWh", re.IGNORECASE)

# Dollar cost patterns — covers invoices ("TOTAL AMOUNT DUE"), receipts
# ("TOTAL CHARGED"), and CSV summaries ("TOTAL COST")
_RE_COST = re.compile(
    r"(?:total\s+(?:amount\s+)?(?:due|charged|cost|paid)|amount\s+due|total\s+due)"
    r"[:\s]*\$?\s*([\d,]+(?:\.\d{1,2})?)",
    re.IGNORECASE,
)


def _extract_cost_dollars(text: str) -> float | None:
    """Return the total billed dollar amount, or None if not found."""
    m = _RE_COST.search(text)
    return float(m.group(1).replace(",", "")) if m else None


def _heuristic_parse(text: str) -> dict:

    # ── Disbursement voucher / multi-item reconciliation ───────────────────
    # Must be checked FIRST — these documents contain multiple item types and
    # would otherwise be misclassified by the single-type detectors below.
    if _VOUCHER_RE.search(text):
        return _parse_disbursement_voucher(text)

    # ── Volunteer log ──────────────────────────────────────────────────────
    if _VOL_RE.search(text):
        return _parse_volunteers(text)

    # ── Electricity — check BEFORE FortisBC natural gas because some FortisBC
    #    accounts cover electricity (e.g. "FORTIS BC — ELECTRICITY SERVICE") ──
    if _ELEC_RE.search(text) or _BCHYDRO_RE.search(text):
        kwh_matches = _RE_KWH.findall(text)
        if kwh_matches:
            consumption_m = re.search(
                r"(?:consumption|consumed|total)[:\s]+([\d,]+(?:\.\d+)?)\s*kwh",
                text, re.IGNORECASE,
            )
            amount = float(
                (consumption_m.group(1) if consumption_m else kwh_matches[-1]).replace(",", "")
            )
            # "high" when consumption is explicitly labelled; "medium" when inferred
            confidence = "high" if consumption_m else "medium"
            return {
                "type":         "electricity",
                "amount":       amount,
                "unit":         "kWh",
                "period":       _extract_elec_period(text),
                "provider":     _detect_provider(text),
                "cost_dollars": _extract_cost_dollars(text),
                "_confidence":  confidence,
            }

    # ── Fuel receipt ───────────────────────────────────────────────────────
    if _FUEL_RE.search(text):
        litres = _extract_litres(text)
        if litres is not None:
            # "TOTAL LITRES" summary or explicit "Total Litres Dispensed" → high confidence
            labelled = re.search(
                r"total\s+litres?\s*(?:dispensed)?[:\s]*([\d,]+\.?\d*)",
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
        # Fuel vendor detected but litres not parseable — flag for manual entry
        return {
            "type":         "fuel",
            "amount":       0,
            "unit":         "L",
            "period":       _extract_period(text),
            "provider":     _detect_provider(text),
            "cost_dollars": _extract_cost_dollars(text),
            "_confidence":  "low",
        }

    # ── Natural gas (FortisBC — GJ preferred, m³ fallback) ────────────────
    if _FORTISBC_RE.search(text) or _GJ_strong(text):
        gj_matches = _RE_GJ.findall(text)
        if gj_matches:
            amount = float(gj_matches[-1].replace(",", ""))
            # GJ is an explicit unit — treat as high confidence
            return {
                "type":         "natural_gas",
                "amount":       amount,
                "unit":         "GJ",
                "period":       _extract_period(text),
                "provider":     _detect_provider(text),
                "cost_dollars": _extract_cost_dollars(text),
                "_confidence":  "high",
            }
        m3_matches = _RE_M3.findall(text)
        if m3_matches:
            vol_m3 = float(m3_matches[-1].replace(",", ""))
            amount = round(vol_m3 * 0.03793, 3)
            return {
                "type":         "natural_gas",
                "amount":       amount,
                "unit":         "GJ",
                "period":       _extract_period(text),
                "provider":     "FortisBC",
                "cost_dollars": _extract_cost_dollars(text),
                "_confidence":  "medium",  # m³→GJ conversion adds uncertainty
            }

    # ── Generic natural gas fallback — any GJ value, no vendor required ───
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

    # ── Generic fuel fallback — any litre value, no vendor required ───────
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

    # ── Electricity (generic kWh fallback) ────────────────────────────────
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


def _parse_disbursement_voucher(text: str) -> dict:
    """
    Parse a multi-item disbursement voucher / expense reconciliation document.

    Each expense line is classified independently. Items that cannot be
    unambiguously identified are returned with _confidence='low' and a _flag
    explaining exactly what is uncertain — nothing is silently inferred.
    """
    # Extract header metadata
    grant_m   = re.search(r"grant\s+code\s*[:\s]+([A-Z0-9\-]+)", text, re.IGNORECASE)
    project_m = re.search(r"project\s*[:\s]+(.+)", text, re.IGNORECASE)
    grant_code = grant_m.group(1).strip() if grant_m else None
    project    = project_m.group(1).strip() if project_m else None

    # Parse each pipe-delimited expense line: "Vendor: X (desc) | Date: Y | Amount: $Z | ..."
    items: list[dict] = []
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"Vendor\s*:", stripped, re.IGNORECASE):
            item = _classify_expense_line(stripped)
            if item is not None:
                items.append(item)

    # Extract compliance / audit flags — join each contiguous block into one string
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
            # End of block — join, strip boilerplate prefix, store
            joined = " ".join(block_lines)
            joined = re.sub(r"^Compliance\s+Check:\s*", "", joined, flags=re.IGNORECASE)
            joined = re.sub(r"^Status:\s*", "", joined, flags=re.IGNORECASE)
            if joined:
                compliance_flags.append(joined)
            block_lines = []
            capture = False
    # Flush any trailing block
    if block_lines:
        joined = " ".join(block_lines)
        joined = re.sub(r"^Compliance\s+Check:\s*", "", joined, flags=re.IGNORECASE)
        joined = re.sub(r"^Status:\s*", "", joined, flags=re.IGNORECASE)
        if joined:
            compliance_flags.append(joined)

    # Document-level confidence: conservative — any low item pulls the whole doc to medium
    if not items:
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
        "compliance_flags": compliance_flags,
        "_confidence":      doc_confidence,
    }


def _classify_expense_line(line: str) -> dict | None:
    """
    Parse and classify a single pipe-delimited expense line.

    Classification rules (deterministic, no inference beyond pattern match):
      - fuel          : vendor in _FUEL_RE AND litres parseable → _confidence high
      - fuel          : vendor in _FUEL_RE but no litres          → _confidence low + _flag
      - travel_scope3 : vendor in _TRAVEL_RE                      → _confidence low + _flag
      - non_emission  : vendor in _ADMIN_RE                       → _confidence medium + _flag
      - unknown       : nothing matched                            → _confidence low + _flag

    Returns None only if the vendor field itself is completely absent (unparseable line).
    """
    # Vendor (required) and optional inline description
    vendor_m = re.search(
        r"Vendor\s*:\s*([^|()\n]+?)(?:\s*\(([^)]+)\))?\s*(?:\||$)",
        line, re.IGNORECASE,
    )
    if not vendor_m:
        return None
    vendor      = vendor_m.group(1).strip()
    description = vendor_m.group(2).strip() if vendor_m.group(2) else ""

    # Date
    date_m = re.search(r"Date\s*:\s*([A-Za-z]+\s+\d+)", line, re.IGNORECASE)
    date   = date_m.group(1).strip() if date_m else None

    # Dollar amount
    amt_m = re.search(r"Amount\s*:\s*\$?([\d,]+(?:\.\d+)?)", line, re.IGNORECASE)
    amount_dollars = float(amt_m.group(1).replace(",", "")) if amt_m else None

    # If we cannot even extract the dollar amount, flag and return what we have
    if amount_dollars is None:
        return {
            "vendor":         vendor,
            "description":    description,
            "date":           date,
            "amount_dollars": None,
            "type":           "unknown",
            "amount":         0,
            "unit":           "",
            "_confidence":    "low",
            "_flag":          "Dollar amount could not be parsed — manual entry required",
        }

    base = {
        "vendor":         vendor,
        "description":    description,
        "date":           date,
        "amount_dollars": amount_dollars,
    }

    # ── Rule 1: Fuel vendor ────────────────────────────────────────────────
    fuel_signal = _FUEL_RE.search(vendor) or re.search(
        r"\bfuel\b|\bpetrol\b|\bgasoline\b|\bdiesel\b", description, re.IGNORECASE
    )
    if fuel_signal:
        litres = _extract_litres(line)
        if litres is not None:
            return {
                **base,
                "type":        "fuel",
                "amount":      litres,
                "unit":        "L",
                "provider":    _detect_provider(f"{vendor} {description}"),
                "_confidence": "high",
            }
        return {
            **base,
            "type":        "fuel",
            "amount":      0,
            "unit":        "L",
            "provider":    _detect_provider(vendor),
            "_confidence": "low",
            "_flag": "Fuel vendor identified but litre quantity not found — manual entry required",
        }

    # ── Rule 2: Travel / Scope 3 candidate ────────────────────────────────
    travel_signal = _TRAVEL_RE.search(vendor) or re.search(
        r"\bflight\b|\bairfare\b|\btravel\b|\bconference\b|\btransit\b",
        description, re.IGNORECASE,
    )
    if travel_signal:
        return {
            **base,
            "type":        "travel_scope3",
            "amount":      0,
            "unit":        "",
            "_confidence": "low",
            "_flag":       (
                "Air travel is a potential Scope 3 emission — "
                "manual review required; distance/passenger data unavailable"
            ),
        }

    # ── Rule 3: Administrative / non-emission ─────────────────────────────
    admin_signal = _ADMIN_RE.search(vendor) or re.search(
        r"\bprinting\b|\bsupplies\b|\boffice\b|\bsubscription\b|\bsoftware\b",
        description, re.IGNORECASE,
    )
    if admin_signal:
        return {
            **base,
            "type":        "non_emission",
            "amount":      0,
            "unit":        "",
            "_confidence": "medium",
            "_flag":       "Administrative expense — no direct GHG emission source",
        }

    # ── Rule 4: Unrecognised vendor ────────────────────────────────────────
    return {
        **base,
        "type":        "unknown",
        "amount":      0,
        "unit":        "",
        "_confidence": "low",
        "_flag":       (
            f"Vendor '{vendor}' not recognised as a known emission source — "
            "cannot determine type; manual classification required"
        ),
    }


def _extract_litres(text: str) -> float | None:
    """Try multiple patterns to extract a litre value from text."""
    # Prefer any "TOTAL LITRES" summary line (covers fleet-log CSVs and receipts)
    total_m = re.search(
        r"total\s+litre[s]?\s*(?:dispensed)?[:\s]+([\d,]+\.?\d*)", text, re.IGNORECASE
    )
    if total_m:
        return float(total_m.group(1).replace(",", ""))
    # Labelled litres on a single receipt line: "Litres: 42" — but skip
    # per-row values in multi-row CSVs by requiring NO digit-only rows afterwards
    labelled_m = re.search(
        r"(?<!\w)litres?\s*[:\s]\s*([\d,]+(?:\.\d+)?)", text, re.IGNORECASE
    )
    if labelled_m:
        return float(labelled_m.group(1).replace(",", ""))
    # Standard regex (space-separated: "42 L" or "42 litres")
    m = _RE_LITRES.search(text)
    if m:
        return float(m.group(1).replace(",", ""))
    # Inline compact: "42L" (no space)
    m2 = _RE_LITRES_INLINE.search(text)
    if m2:
        return float(m2.group(1))
    return None


def _GJ_strong(text: str) -> bool:
    """True when 'GJ' appears at least once alongside 'gas'."""
    return len(_RE_GJ.findall(text)) >= 1 and "gas" in text.lower()


def _parse_volunteers(text: str) -> dict:
    """
    Aggregate multiple rows per volunteer (same First + Last).

    Handles four text shapes:
      1. Key-value  "First Name: Sarah, Last Name: Chen, ...Hours: 4.0..."
      2. Raw CSV    "Sarah,Chen,2026-01-06,...,4.0,..."
      3. Free-text  "Sarah Chen  4.0  Food Sorting"
      4. Named-col  "Date,Volunteer Name,Activity,Hours,..."
                    "2026-01-05,Sarah Miller,Food Sorting,4.5,..."
    """
    agg: dict[str, dict] = {}

    # Shape 1 — key:value lines emitted by ocr._read_csv
    kv_pattern = re.compile(
        r"First Name:\s*([A-Za-z\-]+),\s*Last Name:\s*([A-Za-z\-]+)"
        r".*?Hours:\s*([\d]+\.?\d*)",
        re.DOTALL,
    )
    kv_hits = kv_pattern.findall(text)
    if kv_hits:
        for first, last, hours in kv_hits:
            key  = f"{first.lower()} {last.lower()}"
            name = f"{first} {last}"
            agg[key] = agg.get(key) or {"name": name, "hours": 0.0, "activities": []}
            agg[key]["hours"] += float(hours)

    if not agg:
        # Shape 4 — "Volunteer Name" column: detect header then parse rows
        # Detects CSVs like: Date,Volunteer Name,Activity,Hours,...
        volname_header = re.search(
            r"(?:^|\n)[^\n]*volunteer\s+name[^\n]*hours[^\n]*\n(.*)",
            text, re.IGNORECASE | re.DOTALL,
        )
        if volname_header:
            # Determine column positions from header
            header_line = re.search(
                r"(?:^|\n)([^\n]*volunteer\s+name[^\n]*)", text, re.IGNORECASE
            )
            if header_line:
                cols = [c.strip().lower() for c in header_line.group(1).split(",")]
                try:
                    name_col  = next(i for i, c in enumerate(cols) if "volunteer" in c or "name" in c)
                    hours_col = next(i for i, c in enumerate(cols) if "hour" in c)
                    act_col   = next((i for i, c in enumerate(cols) if "activ" in c), None)
                    # Parse data rows (skip header, skip totals/summary lines)
                    for line in text.splitlines():
                        parts = [p.strip() for p in line.split(",")]
                        if len(parts) <= max(name_col, hours_col):
                            continue
                        name_val  = parts[name_col]
                        hours_val = parts[hours_col]
                        # Skip header rows, total lines, or empty
                        if not name_val or name_val.lower() in ("volunteer name", "name", "total"):
                            continue
                        if not re.match(r"^\d", parts[0]):  # first col should be a date
                            continue
                        try:
                            hrs = float(hours_val)
                        except ValueError:
                            continue
                        key = name_val.strip().lower()
                        act = parts[act_col].strip() if act_col is not None and act_col < len(parts) else ""
                        if key not in agg:
                            agg[key] = {"name": name_val.strip(), "hours": 0.0, "activities": []}
                        agg[key]["hours"] += hrs
                        if act and act not in agg[key]["activities"]:
                            agg[key]["activities"].append(act)
                except StopIteration:
                    pass

    if not agg:
        # Shape 2 — raw CSV rows: FirstName,LastName,YYYY-MM-DD,...,hours,...
        csv_row = re.compile(
            r"([A-Z][a-z\-]+),\s*([A-Z][a-z\-]+),\s*\d{4}-\d{2}-\d{2}"
            r"[^,]*(?:,[^,]*){2,4}?,\s*([\d]+\.?\d*)",
            re.MULTILINE,
        )
        for first, last, hours in csv_row.findall(text):
            key  = f"{first.lower()} {last.lower()}"
            name = f"{first} {last}"
            agg[key] = agg.get(key) or {"name": name, "hours": 0.0, "activities": []}
            agg[key]["hours"] += float(hours)

    if not agg:
        # Shape 3 — free-text "Jane Doe  12"
        for m in _RE_VOL_ROW.finditer(text):
            key  = m.group(1).strip().lower()
            name = m.group(1).strip()
            hrs  = float(m.group(2))
            agg[key] = agg.get(key) or {"name": name, "hours": 0.0, "activities": []}
            agg[key]["hours"] += hrs

    if not agg:
        # Shape 5 — ocr._read_csv key-value rows:
        # "Date: YYYY-MM-DD, Volunteer Name: Sarah Miller, Activity: Food Sorting, Hours: 4.5, ..."
        for line in text.splitlines():
            m = _RE_KV_VOL.search(line)
            if not m:
                continue
            name = m.group(1).strip()
            act  = m.group(2).strip()
            hrs  = float(m.group(3))
            key  = name.lower()
            if key not in agg:
                agg[key] = {"name": name, "hours": 0.0, "activities": []}
            agg[key]["hours"] += hrs
            if act and act not in agg[key]["activities"]:
                agg[key]["activities"].append(act)

    volunteers = list(agg.values()) if agg else [{"name": "Unknown", "hours": 0, "activities": []}]
    # "high" when at least one real volunteer was parsed; "low" for the Unknown fallback
    confidence = "high" if agg else "low"
    return {
        "volunteers":   volunteers,
        "_confidence":  confidence,
    }


def _detect_provider(text: str) -> str:
    if _BCHYDRO_RE.search(text):  return "BC Hydro"
    if _FORTISBC_RE.search(text): return "FortisBC"
    if re.search(r"petro.?canada", text, re.IGNORECASE): return "Petro-Canada"
    if re.search(r"\besso\b",      text, re.IGNORECASE): return "Esso"
    if re.search(r"\bshell\b",     text, re.IGNORECASE): return "Shell"
    return "Unknown Provider"


def _extract_elec_period(text: str) -> str:
    """
    For electricity bills, prefer the service/meter-reading period over the
    invoice date.  Bills are typically invoiced the month *after* service
    (e.g., Feb invoice for Jan service), so we look for meter-reading month
    labels first and fall back to the general extractor only when absent.
    """
    # "Previous Reading (Jan 01)" or "Current Reading (Jan 31)" → service month
    meter_m = _RE_METER_MONTH.search(text)
    if meter_m:
        month = meter_m.group(1).capitalize()
        # Pair the service month with the year found anywhere in the document
        year_m = re.search(r"\b(202[5-9]|203\d)\b", text)
        year   = year_m.group(1) if year_m else ""
        return f"{month} {year}".strip()

    # "Billing Period:" or "Service Period:" label
    bp_m = re.search(
        r"(?:billing|service)\s+period[:\s]+"
        r"(january|february|march|april|may|june|july|august|september"
        r"|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)"
        r"[\s\-–]+(\d{4})?",
        text, re.IGNORECASE,
    )
    if bp_m:
        year  = bp_m.group(2) or ""
        return f"{bp_m.group(1).capitalize()} {year}".strip()

    return _extract_period(text)


def _extract_period(text: str) -> str:
    # Match "Month YYYY" or "Month-YYYY"
    m = _RE_PERIOD.search(text)
    if m:
        return f"{m.group(1).capitalize()} {m.group(2)}"
    # Match "Month DD, YYYY" or "Month DD YYYY"
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
    y = re.search(r"\b(202[5-9]|203\d)\b", text)
    return y.group(1) if y else "Unknown Period"
