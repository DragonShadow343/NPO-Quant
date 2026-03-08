import fitz
import io
import csv
import os
import pytesseract
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tiff", ".tif"}


def extract_text(file_path: str) -> str:
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".csv":
        return _read_csv(file_path)
    if ext == ".xlsx":
        return _read_xlsx(file_path)
    if ext in _IMAGE_EXTS:
        return _read_image(file_path)
    return _read_pdf(file_path)


def _preprocess_image(img):
    """Enhance image for better OCR: grayscale, upscale, contrast boost."""
    try:
        from PIL import ImageOps, ImageFilter, ImageEnhance
        # Convert to grayscale
        img = img.convert("L")
        # Upscale small images to at least 1200px wide for better OCR
        w, h = img.size
        if w < 1200:
            scale = 1200 / w
            img = img.resize((int(w * scale), int(h * scale)), resample=_get_lanczos())
        # Boost contrast
        img = ImageEnhance.Contrast(img).enhance(2.0)
        # Sharpen
        img = img.filter(ImageFilter.SHARPEN)
        return img
    except Exception:
        return img


def _get_lanczos():
    from PIL import Image
    return getattr(Image, "LANCZOS", getattr(Image, "ANTIALIAS", 1))


def _read_image(path: str) -> str:
    try:
        from PIL import Image, ImageSequence
        img = Image.open(path)
        frames = []
        try:
            for frame in ImageSequence.Iterator(img):
                processed = _preprocess_image(frame.copy())
                text = pytesseract.image_to_string(processed, config="--oem 3 --psm 6")
                if text.strip():
                    frames.append(text)
        except Exception:
            processed = _preprocess_image(img)
            text = pytesseract.image_to_string(processed, config="--oem 3 --psm 6")
            frames = [text]
        result = "\n".join(frames).strip()
        # Fallback: try without preprocessing if result is too short
        if len(result) < 30:
            result = pytesseract.image_to_string(img, config="--oem 3 --psm 3")
        return result
    except Exception:
        return ""


def _read_pdf(path: str) -> str:
    doc = fitz.open(path)
    text = "".join(page.get_text() for page in doc)
    if len(text.strip()) < 20:
        text = _ocr_scanned(doc)
    return text


def _ocr_scanned(doc) -> str:
    try:
        from PIL import Image
        pages = []
        for page in doc:
            pix = page.get_pixmap(dpi=300)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            img = _preprocess_image(img)
            text = pytesseract.image_to_string(img, config="--oem 3 --psm 6")
            pages.append(text)
        return "\n".join(pages)
    except Exception:
        return ""


def _compute_csv_totals(raw_rows: list, fieldnames: list) -> str:
    """Compute numeric column totals for summary-type CSVs."""
    skip = {"month", "year", "date", "period", "category", "notes",
            "grant_name", "grantor", "eligibility_criteria_met", "description"}
    totals = {}
    for fname in fieldnames:
        if fname.lower() in skip:
            continue
        values = []
        for row in raw_rows:
            val = str(row.get(fname, "")).strip().replace(",", "").replace("%", "")
            try:
                values.append(float(val))
            except ValueError:
                pass
        if values:
            totals[fname] = round(sum(values), 3)
    if not totals:
        return ""
    return "TOTALS: " + ", ".join(f"{k}: {v}" for k, v in totals.items())


def _read_csv(path: str) -> str:
    rows = []
    raw_rows = []
    fieldnames = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = [c for c in (reader.fieldnames or []) if c]
        for row in reader:
            raw_rows.append(dict(row))
            first_val = row.get(fieldnames[0] if fieldnames else "", "")
            if first_val.startswith(("TOTAL", "Period", "Supervisor", "Total")):
                rows.append(first_val)
                continue
            rows.append(", ".join(f"{k}: {v}" for k, v in row.items() if k and v is not None))

    fl = [c.strip().lower() for c in fieldnames]
    joined = " ".join(fl)

    # Check for summary/report-type CSVs first (most specific)
    if any(k in joined for k in ("kg_co2e", "co2e", "emissions", "carbon")):
        doc_hint = "Carbon Summary:"
    elif any(k in joined for k in ("amount_applied", "amount_received", "eligibility")):
        doc_hint = "Grant Applications:"
    elif any(k in joined for k in ("total_inflow", "donations_inflow", "profit_loss", "wages_expense",
                                   "q1_total", "q2_total", "q3_total", "q4_total")):
        doc_hint = "Accounting Summary:"
    elif any(c in fl for c in ("litres", "liters", "vehicle", "driver")):
        doc_hint = "Fleet Fuel Log:"
    elif any(c in fl for c in ("volunteer name", "volunteer", "hours", "activity")):
        doc_hint = "Volunteer Log:"
    elif any(k in joined for k in ("food", "item", "quantity", "weight", "lbs", "pounds", "donation")):
        doc_hint = "Food Donation Log:"
    elif any(k in joined for k in ("km", "mileage", "odometer", "distance", "trip")):
        doc_hint = "Mileage Log:"
    elif any(k in joined for k in ("expense", "amount", "cost", "category", "description", "vendor")):
        doc_hint = "Expense Log:"
    else:
        doc_hint = "Data Log:"

    # Append computed totals for summary types so AI can extract them directly
    totals_line = ""
    if doc_hint in ("Carbon Summary:", "Grant Applications:", "Accounting Summary:"):
        totals_line = "\n" + _compute_csv_totals(raw_rows, fieldnames)

    return doc_hint + "\n" + "\n".join(rows) + totals_line


def _find_xlsx_header_row(all_rows: list) -> int:
    """
    Find the index of the real column-header row by looking for a row where
    most cells are short non-numeric strings matching typical column label keywords.
    Title rows (long sentences) and data rows (numbers/dates) are skipped.
    """
    header_keywords = {
        "date", "vendor", "payee", "amount", "category", "description",
        "expense", "total", "paid", "name", "hours", "volunteer", "item",
        "quantity", "weight", "lbs", "km", "litres", "liters", "month",
        "grant", "type", "unit", "period", "provider", "cost", "gst",
        "method", "approved", "by", "no", "ref", "#",
    }
    best_row_idx = 0
    best_score   = -1
    for i, row in enumerate(all_rows[:15]):   # only scan first 15 rows
        if row is None:
            continue
        cells = [str(c).strip() for c in row if c is not None]
        if not cells:
            continue
        # Skip title rows: first cell is very long
        if len(cells[0]) > 60:
            continue
        score = sum(
            1 for c in cells
            if 1 <= len(c) <= 40 and any(kw in c.lower() for kw in header_keywords)
        )
        if score > best_score:
            best_score   = score
            best_row_idx = i
    return best_row_idx


def _read_xlsx(path: str) -> str:
    try:
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=True)
        ws = wb.active

        # Collect all rows first so we can find the real header
        all_rows = list(ws.iter_rows(values_only=True))
        header_idx = _find_xlsx_header_row(all_rows)

        raw_headers = all_rows[header_idx] if header_idx < len(all_rows) else []
        headers = [str(c).strip() if c is not None else "" for c in raw_headers]
        header_lower = " ".join(h.lower() for h in headers)

        rows = [", ".join(h for h in headers if h)]  # header row as first text line
        for i, row in enumerate(all_rows):
            if i <= header_idx:     # skip title rows and the header itself
                continue
            if all(c is None for c in row):
                continue
            rows.append(", ".join(
                f"{headers[j] if j < len(headers) else j}: {v}"
                for j, v in enumerate(row) if v is not None
            ))

        # Compute TOTALS for numeric columns (helps AI extract aggregates)
        numeric_totals: dict = {}
        for i, row in enumerate(all_rows):
            if i <= header_idx:
                continue
            for j, v in enumerate(row):
                if v is None or j >= len(headers) or not headers[j]:
                    continue
                try:
                    numeric_totals.setdefault(headers[j], 0.0)
                    numeric_totals[headers[j]] += float(v)
                except (TypeError, ValueError):
                    pass
        if numeric_totals:
            rows.append("TOTALS: " + ", ".join(
                f"{k}: {round(v, 2)}" for k, v in numeric_totals.items()
                if k.lower() not in ("expense #", "no", "ref #", "gst/hst")
            ))

        # Classify hint from the REAL headers
        if any(k in header_lower for k in ("kg_co2e", "co2e", "emissions", "carbon")):
            hint = "Carbon Summary:"
        elif any(k in header_lower for k in ("amount_applied", "amount_received", "eligibility")):
            hint = "Grant Applications:"
        elif any(k in header_lower for k in ("total_inflow", "donations_inflow", "profit_loss", "wages_expense")):
            hint = "Accounting Summary:"
        elif any(k in header_lower for k in ("volunteer", "hours", "activity")):
            hint = "Volunteer Log:"
        elif any(k in header_lower for k in ("litre", "liter", "fuel")):
            hint = "Fleet Fuel Log:"
        elif any(k in header_lower for k in ("km", "mileage", "odometer", "distance", "trip")):
            hint = "Mileage Log:"
        elif any(k in header_lower for k in ("food", "item", "quantity", "weight", "lbs", "pounds", "donation", "intake", "inventory")):
            hint = "Food Donation Log:"
        elif any(k in header_lower for k in ("expense", "amount", "cost", "category", "description", "vendor", "payee")):
            hint = "Expense Log:"
        elif any(k in header_lower for k in ("gross", "net", "salary", "wage", "deduction", "pay period", "employee")):
            hint = "Payroll Log:"
        else:
            hint = "Data Log:"
        return hint + "\n" + "\n".join(rows)
    except Exception:
        return ""
