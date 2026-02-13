"""Google Drive health-folder sync pipeline.

Scans ``drive/me/health`` for new PDF/image files, classifies them by
report type, extracts text, and stores the results locally (JSON) or
in PostgreSQL.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List

from .config import SyncConfig
from .connectors.gdrive import (
    download_file,
    list_files_recursive,
)
from .pdf_extract import extract_pdf_text
from .report_types import classify_report
from .storage import (
    init_db,
    save_lab_document_db,
    write_lab_document_json,
)

PROCESSABLE_MIMES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
}

_SYNC_STATE_FILENAME = ".gdrive_sync_state.json"


def _load_sync_state(data_dir: Path) -> Dict[str, Any]:
    path = data_dir / _SYNC_STATE_FILENAME
    if path.exists():
        return json.loads(path.read_text())
    return {"processed_ids": []}


def _save_sync_state(data_dir: Path, state: Dict[str, Any]) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / _SYNC_STATE_FILENAME
    path.write_text(json.dumps(state, indent=2))


def sync_drive_reports(
    config: SyncConfig,
    day: date | None = None,
    report_type_override: str | None = None,
) -> List[Dict[str, Any]]:
    """Scan Google Drive health folder and ingest new reports.

    Returns a list of dicts describing each processed file.
    """
    if day is None:
        day = datetime.now(tz=config.timezone).date()

    if config.database_url:
        init_db(config)

    state = _load_sync_state(config.data_dir)
    processed_ids: List[str] = state.get("processed_ids", [])

    files = list_files_recursive(config)
    results: List[Dict[str, Any]] = []
    tmp_dir = config.data_dir / "gdrive_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    for f in files:
        file_id = f["id"]
        if file_id in processed_ids:
            continue

        mime = f.get("mimeType", "")
        name = f.get("name", "")

        if mime not in PROCESSABLE_MIMES:
            continue

        # Check if this is a medical image (MRI, X-ray, CT, etc.)
        from .image_analyzer import is_medical_image

        if is_medical_image(name, mime):
            # Analyze with Claude Vision — don't download twice
            if config.anthropic_api_key:
                try:
                    from .image_analyzer import (
                        analyze_image_from_drive,
                        save_analysis_local,
                        upload_analysis_to_drive,
                    )

                    analysis = analyze_image_from_drive(config, file_id, name, mime)
                    save_analysis_local(config, analysis)
                    severity = analysis.get("severity", "UNKNOWN")
                    print(f"  Image analysis [{severity}]: {name}")

                    if config.gdrive_credentials_dir:
                        upload_analysis_to_drive(config, analysis)

                    processed_ids.append(file_id)
                    results.append({
                        "file": name,
                        "kind": "medical_image",
                        "file_id": file_id,
                        "severity": severity,
                    })
                except Exception as exc:
                    print(f"  Image analysis failed for {name}: {exc}")
                    processed_ids.append(file_id)
                    results.append({
                        "file": name,
                        "kind": "medical_image",
                        "file_id": file_id,
                        "error": str(exc),
                    })
            else:
                print(f"  Skipped medical image {name}: ANTHROPIC_API_KEY not set")
                processed_ids.append(file_id)
                results.append({"file": name, "kind": "medical_image", "file_id": file_id})
            continue

        # Download to temp
        local_path = tmp_dir / name
        download_file(config, file_id, local_path)

        # Extract text (PDF only; other images classified above)
        raw_text = ""
        metadata: Dict[str, Any] = {
            "drive_file_id": file_id,
            "original_name": name,
            "mime_type": mime,
            "modified_time": f.get("modifiedTime", ""),
        }

        if mime == "application/pdf":
            extracted = extract_pdf_text(local_path)
            raw_text = extracted.get("text", "")
            metadata["pages"] = extracted.get("pages")

        # Classify — subfolder name takes priority (e.g. me/health/genetic/)
        subfolder_kind = f.get("subfolder_type")
        kind = report_type_override or subfolder_kind or classify_report(name, raw_text)
        if kind is None:
            kind = "unclassified"
        metadata["kind"] = kind

        # Store
        if config.database_url:
            save_lab_document_db(config, kind, day.isoformat(), raw_text, metadata)
        else:
            write_lab_document_json(config.data_dir, kind, day.isoformat(), {
                "kind": kind,
                "text": raw_text,
                **metadata,
            })

        processed_ids.append(file_id)
        results.append({"file": name, "kind": kind, "file_id": file_id})

        # Clean up temp file
        local_path.unlink(missing_ok=True)

    _save_sync_state(config.data_dir, {"processed_ids": processed_ids})
    return results
