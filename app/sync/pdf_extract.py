"""PDF text extraction with an OCR fallback.

First try pypdf (fast, works for text-based PDFs). If the extracted text is
suspiciously short (<200 chars over a multi-page doc), the PDF is likely a
scanned image — fall back to pytesseract rendering. OCR is optional: if
``pytesseract`` or ``pdf2image`` aren't installed, we just return the empty
text and note it in the result.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict

from pypdf import PdfReader

logger = logging.getLogger("personal-doctor.pdf_extract")

_OCR_MIN_CHARS = 200


def _text_via_pypdf(path: Path) -> tuple[str, int]:
    reader = PdfReader(str(path))
    text_chunks = []
    for page in reader.pages:
        page_text = page.extract_text() or ""
        text_chunks.append(page_text)
    return "\n".join(text_chunks).strip(), len(reader.pages)


def _text_via_ocr(path: Path) -> str:
    """OCR fallback via pytesseract + pdf2image. Returns '' on any failure."""
    try:
        from pdf2image import convert_from_path
        import pytesseract
    except ImportError:
        logger.info("OCR dependencies not installed (pdf2image, pytesseract); skipping")
        return ""

    try:
        images = convert_from_path(str(path), dpi=250)
    except Exception as exc:
        logger.warning(f"pdf2image failed for {path.name}: {exc}")
        return ""

    chunks = []
    for i, img in enumerate(images):
        try:
            # English + common medical-report languages
            text = pytesseract.image_to_string(img, lang="eng")
            chunks.append(text)
        except Exception as exc:
            logger.warning(f"pytesseract failed on page {i} of {path.name}: {exc}")
            continue
    return "\n".join(chunks).strip()


def extract_pdf_text(path: Path) -> Dict[str, str]:
    text, page_count = _text_via_pypdf(path)
    used_ocr = False
    if len(text) < _OCR_MIN_CHARS and page_count > 0:
        # Likely a scanned image PDF — try OCR
        ocr_text = _text_via_ocr(path)
        if len(ocr_text) > len(text):
            text = ocr_text
            used_ocr = True
    return {
        "text": text,
        "pages": str(page_count),
        "filename": path.name,
        "ocr": "yes" if used_ocr else "no",
    }
