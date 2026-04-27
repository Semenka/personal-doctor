"""One-shot: ingest blood-test + spermogram archives from explicit Drive folder IDs.

Walks each folder recursively, downloads every PDF/image, extracts text,
classifies each file as the kind passed in (so spermogrammes go in as
``sperm_test`` regardless of filename), runs the AI summary, and runs the
biomarker extractor. Persists everything as the standard lab_document JSONs
so the rest of the pipeline (advisor, /biomarkers, daily email) sees them.

Idempotent: skips files already in ``data/ingested/.gdrive_sync_state.json``.

Usage::

    cd /Users/andrey/personal-doctor
    .venv/bin/python -m scripts.ingest_drive_folders
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from googleapiclient.errors import HttpError

from app.sync.config import SyncConfig, load_config
from app.sync.connectors.gdrive import _build_service, download_file
from app.sync.pdf_extract import extract_pdf_text
from app.sync.storage import init_db, save_lab_document_db, write_lab_document_json


# (folder_id, default_kind, label)
ARCHIVES: List[Tuple[str, str, str]] = [
    ("1fxxbcePw5_OYWgljAlgelF0a0gGqHlr-", "blood_test", "Blood tests"),
    ("1b0vVsSP61sXkQ9fkoVHl15UFdwTEtCiT", "sperm_test", "Spermogrammes"),
]

PROCESSABLE_MIMES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
}

_SYNC_STATE_FILENAME = ".gdrive_sync_state.json"


def _load_state(data_dir: Path) -> Dict[str, Any]:
    p = data_dir / _SYNC_STATE_FILENAME
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return {"processed_ids": []}
    return {"processed_ids": []}


def _save_state(data_dir: Path, state: Dict[str, Any]) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / _SYNC_STATE_FILENAME).write_text(json.dumps(state, indent=2))


def _walk_folder(service, folder_id: str, depth: int = 0) -> List[Dict[str, Any]]:
    """Recursive list of every (non-folder) file under a folder ID."""
    if depth > 10:
        return []
    out: List[Dict[str, Any]] = []
    page_token = None
    while True:
        try:
            resp = (
                service.files()
                .list(
                    q=f"'{folder_id}' in parents and trashed = false",
                    fields="nextPageToken, files(id, name, mimeType, modifiedTime, size, parents)",
                    pageSize=200,
                    pageToken=page_token,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )
        except HttpError as exc:
            print(f"    ! list failed for folder {folder_id}: {exc}")
            return out
        for f in resp.get("files", []):
            if f.get("mimeType") == "application/vnd.google-apps.folder":
                out.extend(_walk_folder(service, f["id"], depth + 1))
            else:
                out.append(f)
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return out


def _extract_draw_date(filename: str, pdf_text: str, fallback: str) -> str:
    """Best-effort: find a YYYY-MM-DD or DD/MM/YYYY date in filename or first 800 chars."""
    import re

    candidates = [filename, pdf_text[:800]]
    for c in candidates:
        # ISO
        m = re.search(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", c)
        if m:
            try:
                d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                return d.isoformat()
            except Exception:
                pass
        # DD/MM/YYYY (common in FR/IT)
        m = re.search(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](20\d{2})\b", c)
        if m:
            try:
                d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
                return d.isoformat()
            except Exception:
                pass
    return fallback


def _ingest_one(
    config: SyncConfig, service, file_meta: Dict[str, Any], default_kind: str,
    tmp_dir: Path, today: str,
) -> Dict[str, Any]:
    """Download → extract text → summarize → biomarker extract → save."""
    file_id = file_meta["id"]
    name = file_meta["name"]
    mime = file_meta.get("mimeType", "")

    if mime not in PROCESSABLE_MIMES:
        return {"file": name, "kind": default_kind, "skipped": "unsupported_mime"}

    local_path = tmp_dir / name.replace("/", "_")
    try:
        download_file(config, file_id, local_path)
    except Exception as exc:
        return {"file": name, "kind": default_kind, "error": f"download: {exc}"}

    raw_text = ""
    pages = None
    if mime == "application/pdf":
        try:
            extracted = extract_pdf_text(local_path)
            raw_text = extracted.get("text", "")
            pages = extracted.get("pages")
        except Exception as exc:
            return {"file": name, "kind": default_kind, "error": f"extract: {exc}"}

    draw_date = _extract_draw_date(name, raw_text, today)

    # AI-summarize so the report shows up under "new reports"
    summary = None
    try:
        from app.sync.report_summarizer import summarize_report_text
        summary = summarize_report_text(config, default_kind, raw_text, filename=name)
    except Exception as exc:
        print(f"    ! summarizer failed for {name}: {exc}")

    metadata: Dict[str, Any] = {
        "drive_file_id": file_id,
        "original_name": name,
        "mime_type": mime,
        "modified_time": file_meta.get("modifiedTime", ""),
        "pages": pages,
        "kind": default_kind,
        "ingestion_source": "scripts.ingest_drive_folders",
    }
    if summary:
        metadata["ai_summary"] = summary

    # Persist
    if config.database_url:
        save_lab_document_db(config, default_kind, draw_date, raw_text, metadata)
    else:
        write_lab_document_json(
            config.data_dir, default_kind, draw_date,
            {"kind": default_kind, "text": raw_text, **metadata},
        )

    # Biomarker extraction (Gemini → heuristic fallback)
    biomarker_count = 0
    try:
        from app.sync.biomarker_extractor import extract_and_save

        readings = extract_and_save(
            config, raw_text, source_kind=default_kind,
            source_file=name, draw_date=draw_date,
        )
        biomarker_count = len(readings)
    except Exception as exc:
        print(f"    ! biomarker extract failed for {name}: {exc}")

    # Clean up tmp
    try:
        local_path.unlink(missing_ok=True)
    except Exception:
        pass

    return {
        "file": name,
        "kind": default_kind,
        "draw_date": draw_date,
        "pages": pages,
        "biomarker_count": biomarker_count,
        "summary_severity": (summary or {}).get("severity"),
    }


def main() -> int:
    config = load_config()
    if config.database_url:
        init_db(config)

    service = _build_service(config)
    today = datetime.now(tz=config.timezone).date().isoformat()

    state = _load_state(config.data_dir)
    processed_ids: set = set(state.get("processed_ids", []))
    tmp_dir = config.data_dir / "gdrive_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    grand_total = 0
    grand_biomarkers = 0
    for folder_id, default_kind, label in ARCHIVES:
        print(f"\n=== {label} (folder {folder_id}, kind={default_kind}) ===")
        try:
            files = _walk_folder(service, folder_id)
        except Exception as exc:
            print(f"  walk failed: {exc}")
            continue
        new_files = [f for f in files if f["id"] not in processed_ids]
        print(f"  Found {len(files)} file(s) total, {len(new_files)} new.")
        for f in new_files:
            r = _ingest_one(config, service, f, default_kind, tmp_dir, today)
            tag = "✓"
            if r.get("error"):
                tag = "✗"
            elif r.get("skipped"):
                tag = "—"
            bm = r.get("biomarker_count", 0)
            print(
                f"  {tag} [{r.get('draw_date', '?')}] {r['file']}"
                + (f" · {bm} biomarker(s)" if bm else "")
                + (f" · severity={r.get('summary_severity')}" if r.get("summary_severity") else "")
                + (f" · ERR: {r.get('error') or r.get('skipped')}"
                   if r.get('error') or r.get('skipped') else "")
            )
            processed_ids.add(f["id"])
            grand_total += 1
            grand_biomarkers += bm

    state["processed_ids"] = sorted(processed_ids)
    _save_state(config.data_dir, state)
    print(
        f"\nIngestion complete: {grand_total} file(s) processed, "
        f"{grand_biomarkers} biomarker reading(s) saved."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
