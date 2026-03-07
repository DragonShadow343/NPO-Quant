import fitz
import io
import csv
import os

_IMAGE_EXTS_= {".png", ".jpg", ".jpeg", ".tiff", ".tif"}

def extract_text(file_path: str) -> str:
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".csv":
        return _read_csv(file_path)
    if ext in _IMAGE_EXTS:
        return _read_image(file_path)
    return _read_pdf(file_path)

def _read_image(path: str) -> str:
    """OCR a standalone image file (PNG / JPG / TIFF)."""
    try:
        import pytesseract
        from PIL import Image
        img = Image.open(path)
        # TIFF can be multi-page; iterate frames
        frames = []
        try:
            from PIL import ImageSequence
            for frame in ImageSequence.Iterator(img):
                frames.append(pytesseract.image_to_string(frame.copy()))
        except Exception:
            frames = [pytesseract.image_to_string(img)]
        return "\n".join(frames).strip()
    except ImportError:
        return ""
    except Exception:
        return ""

def _read_pdf(path: str) -> str:
    doc = fitz.open(path)
    text = "".join(page.get_text() for page in doc)
    # If text is sparse it's a scanned image — Tesseract fallback
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
    except ImportError:
        return ""

def _read_csv(path: str) -> str:
    """Convert CSV rows → readable text so the LLM can parse it."""
    rows = []
    fieldnames = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = [c for c in (reader.fieldnames or []) if c]
        for row in reader:
            # Skip malformed trailer rows (e.g. "TOTAL LITRES: …" footer lines)
            if row.get(fieldnames[0], "").startswith(("TOTAL", "Period", "Supervisor")):
                rows.append(row.get(fieldnames[0], ""))   # keep as plain text
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
