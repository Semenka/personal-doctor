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

    # ── ALSO walk external folder IDs (blood + spermogram archives) ──
    # The recurring cron used to only scan ``me/health``, so spermogrammes
    # uploaded to a separate Drive folder were silently invisible. Reuse the
    # ARCHIVES table defined in scripts/ingest_drive_folders.py as the single
    # source of truth, so both the one-shot script and the daily cron stay
    # in lockstep. The walk is best-effort — any failure (network, perms,
    # missing folder) is logged and we keep going with the regular scan.
    try:
        from googleapiclient.errors import HttpError  # noqa: F401

        from scripts.ingest_drive_folders import ARCHIVES, _walk_folder
        from .connectors.gdrive import _build_service

        service = _build_service(config)
        for folder_id, default_kind, label in ARCHIVES:
            try:
                extras = _walk_folder(service, folder_id)
            except Exception as walk_exc:
                print(f"  external folder walk failed [{label}]: {walk_exc}")
                continue
            seen_in_main = {x.get("id") for x in files}
            new_extras = 0
            for x in extras:
                if x.get("id") in seen_in_main:
                    continue
                # Tag with default_kind so the classifier treats every file
                # under the spermogram folder as a sperm_test (even photos
                # whose filename is just "IMG_xxxx.jpg").
                x["_external_default_kind"] = default_kind
                files.append(x)
                new_extras += 1
            if new_extras:
                print(f"  external folder [{label}]: +{new_extras} file(s) added to scan")
    except Exception as exc:
        print(f"  external archive scan skipped: {exc}")

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
            # Analyze with Gemini Vision — don't download twice
            if config.google_api_key:
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

                    # Record as "new report" for today's advisor + urgent push
                    from .report_summarizer import save_new_report

                    save_new_report(
                        config,
                        day,
                        "medical_image",
                        name,
                        file_id,
                        {
                            "summary": analysis.get("analysis", "")[:600],
                            "flags": [],
                            "severity": severity,
                            "specialist_referral": severity in ("URGENT", "MODERATE CONCERN"),
                            "follow_ups": [],
                        },
                    )
                    _maybe_urgent_push(
                        config, name, "medical_image", severity, flags=[], referral=severity == "URGENT"
                    )

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

        # Classify — priority order:
        # 1. explicit report_type_override (CLI flag)
        # 2. external archive default_kind (file came from a tagged folder
        #    like the spermogram archive — overrides filename guessing
        #    because photos like "IMG_8497.jpg" otherwise fall through)
        # 3. subfolder name under me/health (e.g. me/health/genetic/)
        # 4. classify_report heuristic on filename + text
        external_kind = f.get("_external_default_kind")
        subfolder_kind = f.get("subfolder_type")
        kind = (
            report_type_override
            or external_kind
            or subfolder_kind
            or classify_report(name, raw_text)
        )
        if kind is None:
            kind = "unclassified"
        metadata["kind"] = kind

        # AI-summarize the report text (M2). Best-effort; non-blocking on failure.
        summary = None
        if raw_text:
            try:
                from .report_summarizer import summarize_report_text

                summary = summarize_report_text(config, kind, raw_text, filename=name)
                if summary:
                    metadata["ai_summary"] = summary
            except Exception as exc:
                print(f"  Summarize failed for {name}: {exc}")

        # Structured biomarker extraction → time-series + dashboard.
        # Triggered for any lab-style document; spermograms, blood tests,
        # hormone panels, etc. Stored in data/ingested/biomarkers/results.jsonl
        # so the daily email + advisor + /biomarkers page can chart trends.
        biomarker_alerts: List[Dict[str, Any]] = []
        if raw_text and kind in (
            "blood_test", "urine_test", "sperm_test",
            "hormone_panel", "health_check", "genetic_test",
        ):
            try:
                from .biomarker_extractor import extract_and_save
                from .biomarker_trends import alert_changes

                new_readings = extract_and_save(
                    config, raw_text, source_kind=kind,
                    source_file=name, draw_date=day.isoformat(),
                )
                if new_readings:
                    metadata["biomarker_count"] = len(new_readings)
                    metadata["biomarkers"] = [r.biomarker_id for r in new_readings]
                    biomarker_alerts = alert_changes(
                        [r.to_dict() for r in new_readings], config,
                    )
            except Exception as exc:
                print(f"  Biomarker extraction failed for {name}: {exc}")

        # Store
        if config.database_url:
            save_lab_document_db(config, kind, day.isoformat(), raw_text, metadata)
        else:
            write_lab_document_json(config.data_dir, kind, day.isoformat(), {
                "kind": kind,
                "text": raw_text,
                **metadata,
            })

        # Record as "new report" for today's advisor banner (M1) + urgent push (M3)
        try:
            from .report_summarizer import save_new_report

            save_new_report(config, day, kind, name, file_id, summary)
        except Exception as exc:
            print(f"  save_new_report failed for {name}: {exc}")

        if summary:
            _maybe_urgent_push(
                config,
                name,
                kind,
                summary.get("severity"),
                flags=summary.get("flags", []),
                referral=summary.get("specialist_referral", False),
            )
            # Test-type-specific workflows (M5)
            try:
                from .report_workflows import run_post_ingestion_workflows

                run_post_ingestion_workflows(config, kind, name, summary)
            except Exception as exc:
                print(f"  report_workflow failed for {name}: {exc}")

        # Biomarker change push — fires when a tracked marker moved ≥15% since
        # the previous draw or is newly out of reference range.
        if biomarker_alerts:
            _maybe_biomarker_push(config, name, kind, biomarker_alerts)

        # On-arrival outcome intelligence (O3): if this report added new
        # biomarker readings, compute the cross-test progress note and push
        # the "since your last test" narrative — far more useful than a bare
        # "new report detected." Only for the report kinds that feed markers.
        if kind in (
            "blood_test", "urine_test", "sperm_test",
            "hormone_panel", "health_check",
        ) and metadata.get("biomarker_count"):
            try:
                from .outcomes import build_progress, render_whatsapp_note
                from .whatsapp_sender import _run_openclaw_send

                # Restrict to the kind that just landed so the narrative
                # leads with the relevant test.
                progress = build_progress(config, kinds=[kind])
                note = render_whatsapp_note(progress)
                if note:
                    _run_openclaw_send(note)
                    print(f"  Sent on-arrival progress note for {name}")
            except Exception as exc:
                print(f"  on-arrival progress note failed for {name}: {exc}")

        processed_ids.append(file_id)
        results.append({
            "file": name,
            "kind": kind,
            "file_id": file_id,
            "severity": (summary or {}).get("severity"),
            "biomarker_alerts": biomarker_alerts,
        })

        # Clean up temp file
        local_path.unlink(missing_ok=True)

    _save_sync_state(config.data_dir, {"processed_ids": processed_ids})

    # Morning summary WhatsApp ping — one message listing what's new (M1)
    if results:
        try:
            _send_new_reports_whatsapp_summary(config, results)
        except Exception as exc:
            print(f"  new-reports WhatsApp summary failed: {exc}")

    return results


def _maybe_urgent_push(
    config: SyncConfig,
    filename: str,
    kind: str,
    severity: str | None,
    flags: list,
    referral: bool,
) -> None:
    """Send immediate WhatsApp alert if the report is URGENT or needs a specialist (M3)."""
    if not severity:
        return
    sev = str(severity).upper()
    if "URGENT" not in sev and not referral:
        return
    try:
        from .whatsapp_sender import _run_openclaw_send

        tag = "🚨 URGENT" if "URGENT" in sev else "⚠️ Specialist"
        lines = [f"{tag} — new {kind} report needs attention"]
        lines.append(f"File: {filename}")
        if flags:
            lines.append("Flags: " + " · ".join(flags[:3]))
        lines.append("Full summary in today's 8 AM email.")
        _run_openclaw_send("\n".join(lines))
    except Exception as exc:
        print(f"  urgent WhatsApp push failed: {exc}")


def _send_new_reports_whatsapp_summary(
    config: SyncConfig, results: List[Dict[str, Any]]
) -> None:
    """One-message digest when new reports land during the morning Drive sync."""
    try:
        from .whatsapp_sender import _run_openclaw_send
    except Exception:
        return
    kinds = {}
    for r in results:
        kinds[r.get("kind", "report")] = kinds.get(r.get("kind", "report"), 0) + 1
    if not kinds:
        return
    parts = [f"{n} {k}" for k, n in kinds.items()]
    msg = (
        "🧪 New medical reports detected: "
        + ", ".join(parts)
        + "\nSummary in today's 8 AM plan."
    )
    _run_openclaw_send(msg)


def _maybe_biomarker_push(
    config: SyncConfig,
    filename: str,
    kind: str,
    alerts: List[Dict[str, Any]],
) -> None:
    """Immediate WhatsApp ping when a new lab moved a tracked biomarker ≥15%
    or pushed it out of reference range.
    """
    if not alerts:
        return
    try:
        from .whatsapp_sender import _run_openclaw_send
    except Exception:
        return

    lines = [f"📊 New {kind} — {filename}", ""]
    lines.append(f"{len(alerts)} biomarker(s) moved meaningfully:")
    for a in alerts[:6]:
        if a.get("kind") == "delta":
            arrow = {"improving": "✅", "declining": "⚠️"}.get(a.get("direction"), "•")
            lines.append(
                f"{arrow} {a['marker_name']}: {a['from_value']:g} → "
                f"{a['to_value']:g} {a.get('unit','')} ({a['pct_change']:+.0f}%)"
            )
        else:
            lines.append(
                f"⚠️ {a['marker_name']} {a.get('flagged','')}: "
                f"{a.get('value','?')} {a.get('unit','')} (first reading, out of range)"
            )
    lines.append("")
    lines.append("Full charts: http://localhost:8000/biomarkers")
    _run_openclaw_send("\n".join(lines))
