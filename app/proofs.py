"""Reading proof documents: text extraction (free OCR) and field extraction.

Order of work, cheapest first:
1. Text PDFs  -> pdfplumber (free, exact)
2. Scans/images -> Tesseract OCR (free, on the server)
3. Field extraction -> regular expressions (free)
4. Only if fields are still missing -> AI (paid, cached); see ai.py
"""
from __future__ import annotations

import hashlib
import re
from datetime import date
from pathlib import Path

MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"),
            ("may",), ("jun", "june"), ("jul", "july"), ("aug", "august"),
            ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
            ("dec", "december"),
        ],
        start=1,
    )
    for m in names
}
_MONTH_RE = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_text(path: Path) -> str:
    """Get text from a PDF or image. Falls back to OCR for scanned PDFs."""
    suffix = path.suffix.lower()
    text = ""
    if suffix == ".pdf":
        try:
            import pdfplumber

            with pdfplumber.open(path) as pdf:
                text = "\n".join((p.extract_text() or "") for p in pdf.pages)
        except Exception:
            text = ""
        if len(text.strip()) < 20:  # probably a scan: OCR each page
            text = _ocr_pdf(path)
    elif suffix in {".png", ".jpg", ".jpeg"}:
        text = _ocr_image(path)
    return text.strip()


def _ocr_image(path: Path) -> str:
    try:
        import pytesseract
        from PIL import Image

        return pytesseract.image_to_string(Image.open(path))
    except Exception:
        return ""


def _ocr_pdf(path: Path) -> str:
    try:
        import pdfplumber
        import pytesseract

        parts = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages[:20]:  # cap pages to keep it fast
                img = page.to_image(resolution=200).original
                parts.append(pytesseract.image_to_string(img))
        return "\n".join(parts)
    except Exception:
        return ""


# ---------------- field extraction (free, rule-based) ----------------

def _mk(y: int, m: int, d: int) -> date | None:
    try:
        if y < 100:
            y += 2000
        return date(y, m, d)
    except ValueError:
        return None


def find_dates(text: str) -> list[date]:
    """Find dates in common certificate formats. Returns them in order of appearance."""
    t = text.lower()
    found: list[tuple[int, date]] = []

    # 12-02-2025, 12/02/2025, 12.02.2025 (day first, Indian format)
    for m in re.finditer(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4}|\d{2})\b", t):
        d = _mk(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        if d:
            found.append((m.start(), d))
    # 2025-02-12 (ISO)
    for m in re.finditer(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", t):
        d = _mk(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d:
            found.append((m.start(), d))
    # "12th to 17th February 2025" / "12 - 17 Feb 2025"
    for m in re.finditer(
        r"\b(\d{1,2})(?:st|nd|rd|th)?\s*(?:to|-|–|till|until)\s*(\d{1,2})(?:st|nd|rd|th)?\s+"
        + _MONTH_RE + r"[,]?\s+(\d{4})", t):
        mon = MONTHS[m.group(3)[:3]]
        for g in (1, 2):
            d = _mk(int(m.group(4)), mon, int(m.group(g)))
            if d:
                found.append((m.start(g), d))
    # "12 February 2025", "12th Feb, 2025"
    for m in re.finditer(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+" + _MONTH_RE + r"[,]?\s+(\d{4})", t):
        d = _mk(int(m.group(3)), MONTHS[m.group(2)[:3]], int(m.group(1)))
        if d:
            found.append((m.start(), d))
    # "February 12, 2025"
    for m in re.finditer(_MONTH_RE + r"\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", t):
        d = _mk(int(m.group(3)), MONTHS[m.group(1)[:3]], int(m.group(2)))
        if d:
            found.append((m.start(), d))

    seen, out = set(), []
    for _, d in sorted(found, key=lambda x: x[0]):
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def find_doi(text: str) -> str:
    m = re.search(r"\b(10\.\d{4,9}/[^\s\"<>]+)", text)
    return m.group(1).rstrip(".,;)") if m else ""


def find_duration_days(text: str) -> int | None:
    m = re.search(r"\b(\d{1,2})\s*[- ]?\s*days?\b", text.lower())
    return int(m.group(1)) if m else None


def normalise_name(name: str) -> str:
    name = re.sub(r"\b(dr|prof|mr|mrs|ms|shri|smt)\b\.?", " ", name.lower())
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", name).split())


def name_in_text(name: str, text: str) -> bool:
    """True if the person's name (ignoring titles and initials order) appears in the text.
    Numbers in a name (e.g. a staff code such as 'Assistant Professor CSE-012') must match too."""
    parts = [p for p in normalise_name(name).split() if len(p) > 1 or p.isdigit()]
    if not parts:
        return False
    t = " ".join(re.sub(r"[^a-z0-9 ]", " ", text.lower()).split())
    # every name part of length > 1 must appear
    return all(re.search(rf"\b{re.escape(p)}\b", t) for p in parts)


def extract_fields(text: str) -> dict:
    dates = find_dates(text)
    out: dict = {"dates": [d.isoformat() for d in dates]}
    if dates:
        out["start_date"] = min(dates).isoformat()
        out["end_date"] = max(dates).isoformat()
        out["span_days"] = (max(dates) - min(dates)).days + 1
    dur = find_duration_days(text)
    if dur:
        out["duration_days"] = dur
    doi = find_doi(text)
    if doi:
        out["doi"] = doi
    return out
