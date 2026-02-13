from __future__ import annotations

from pathlib import Path
from typing import Dict

from pypdf import PdfReader


def extract_pdf_text(path: Path) -> Dict[str, str]:
    reader = PdfReader(str(path))
    text_chunks = []
    for page in reader.pages:
        page_text = page.extract_text() or ""
        text_chunks.append(page_text)
    return {
        "text": "\n".join(text_chunks).strip(),
        "pages": str(len(reader.pages)),
        "filename": path.name,
    }
