"""AI is switched off in the Criterion 5 app.

Every mark in Criterion 5 comes from an NBA formula, and proofs are read with free OCR (Tesseract)
and simple text rules, so no AI tokens are used. This stub keeps the shared code paths simple.
"""


def enabled() -> bool:
    return False


def describe() -> str:
    return ""


def extract_fields(text: str) -> dict:
    return {}


def review_written(*args, **kwargs):
    return None
