from __future__ import annotations

from pathlib import Path
from typing import Dict

from .pdf_extract import extract_pdf_text


def load_blood_tests(path: Path) -> Dict[str, str]:
    """Load blood test PDF and return raw text + metadata."""
    payload = extract_pdf_text(path)
    payload["kind"] = "blood"
    return payload


def load_urine_tests(path: Path) -> Dict[str, str]:
    """Load urine test PDF and return raw text + metadata."""
    payload = extract_pdf_text(path)
    payload["kind"] = "urine"
    return payload


def load_annual_checkups(path: Path) -> Dict[str, str]:
    """Load annual check-up PDF and return raw text + metadata."""
    payload = extract_pdf_text(path)
    payload["kind"] = "annual_checkup"
    return payload
