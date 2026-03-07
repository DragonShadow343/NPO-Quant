import fitz
import io
import csv
import os

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


def _read_image(path: str) -> str:
    try:
        import pytesseract
        from PIL import Image, ImageSequence
        img = Image.open(path)
        frames = []
        try:
            for frame in ImageSequence.Iterator(img):
                frames.append(pytesseract.image_to_string(frame.copy()))
        except Exception:
            frames = [pytesseract.image_to_string(img)]
        return "\n".join(frames).strip()
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
        import pytesseract
        from PIL import Image
        pages = []
        for page in doc:
            pix = page.get_pixmap(dpi=200)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            pages.append(pytesseract.image_to_string(img))
        return "\n".join(pages)
    except Exception:
        return ""


def _read_csv(path: str) -> str:
    rows = []
    fieldnames = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = [c for c in (reader.fieldnames or []) if c]
        for row in reader:
            if row.get(fieldnames[0] if fieldnames else "", "").startswith(("TOTAL", "Period", "Supervisor")):
                rows.append(row.get(fieldnames[0], ""))
                continue
            rows.append(", ".join(f"{k}: {v}" for k, v in row.items() if k))

    fl = [c.lower() for c in fieldnames]
    if any(c in fl for c in ("litres", "liters", "vehicle", "driver")):
        doc_hint = "Fleet Fuel Log:"
    elif any(c in fl for c in ("volunteer name", "volunteer", "hours", "activity")):
        doc_hint = "Volunteer Log:"
    else:
        doc_hint = "Data Log:"
    return doc_hint + "\n" + "\n".join(rows)


def _read_xlsx(path: str) -> str:
    try:
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=True)
        ws = wb.active
        rows = []
        headers = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i == 0:
                headers = [str(c) if c is not None else "" for c in row]
                rows.append(", ".join(headers))
            else:
                if all(c is None for c in row):
                    continue
                rows.append(", ".join(
                    f"{headers[j] if j < len(headers) else j}: {v}"
                    for j, v in enumerate(row) if v is not None
                ))
        return "Data Log:\n" + "\n".join(rows)
    except Exception:
        return ""