from __future__ import annotations

import argparse
from datetime import date, datetime
from pathlib import Path

from .config import load_config
from .pipeline import load_oura_daily
from .placeholders import load_blood_tests, load_urine_tests
from .report_types import REPORT_TYPES
from .storage import (
    init_db,
    save_daily_payload_db,
    save_lab_document_db,
    write_daily_json,
    write_lab_document_json,
)


def main() -> None:
    all_sources = [
        "oura", "blood", "urine", "annual",
        "genetic", "sperm", "conclusion", "prescription",
        "health_check", "research", "gdrive", "oura-analytics",
        "advisor", "scan",
    ]
    parser = argparse.ArgumentParser(description="Health data sync")
    parser.add_argument("--source", choices=all_sources, default="oura")
    parser.add_argument("--date", help="ISO date (YYYY-MM-DD)")
    parser.add_argument("--path", help="Path to lab/report PDF")
    parser.add_argument(
        "--upload", action="store_true",
        help="Upload to Google Drive (with --source oura-analytics or advisor)",
    )
    parser.add_argument(
        "--email", action="store_true",
        help="Email the daily advice (with --source advisor)",
    )
    args = parser.parse_args()

    day = date.fromisoformat(args.date) if args.date else datetime.now().date()
    config = load_config()

    if config.database_url:
        init_db(config)

    # --- Oura daily sync ---
    if args.source == "oura":
        payload = load_oura_daily(config, day)
        if config.database_url:
            save_daily_payload_db(config, payload)
            print("Saved daily payload to Postgres.")
        else:
            target = write_daily_json(config.data_dir, day.isoformat(), payload)
            print(f"Saved {target}")
        if args.upload and config.gdrive_credentials_dir:
            from .oura_analytics import _build_analytics, upload_analytics_to_drive

            analytics = _build_analytics(day, payload)
            file_id = upload_analytics_to_drive(config, analytics)
            print(f"Uploaded Oura data to Drive: me/health/{day.strftime('%Y/%m/%d')}/")
        elif args.upload:
            print("Warning: --upload requested but GDRIVE_CREDENTIALS_DIR not set.")
        return

    # --- Research sync ---
    if args.source == "research":
        if not config.database_url:
            raise SystemExit("DATABASE_URL is required for research sync")
        from ..research.pipeline import run_daily_research

        run_daily_research(config, day)
        print("Saved daily research recommendations to Postgres.")
        return

    # --- Google Drive scan ---
    if args.source == "gdrive":
        if not config.gdrive_credentials_dir:
            raise SystemExit("GDRIVE_CREDENTIALS_DIR is required for Google Drive sync")
        from .gdrive_pipeline import sync_drive_reports

        results = sync_drive_reports(config, day)
        if results:
            print(f"Processed {len(results)} new file(s) from Google Drive:")
            for r in results:
                print(f"  - {r['file']} → {r['kind']}")
        else:
            print("No new files in Google Drive health folder.")
        return

    # --- AI daily advisor ---
    if args.source == "advisor":
        from .daily_advisor import (
            email_advice,
            generate_daily_advice,
            print_advice,
            save_advice_local,
            upload_advice_to_drive,
        )

        if not config.anthropic_api_key:
            raise SystemExit("ANTHROPIC_API_KEY is required for the daily advisor")
        advice = generate_daily_advice(config, day)
        print_advice(advice)
        local = save_advice_local(config, advice)
        print(f"Saved: {local}")
        if args.upload and config.gdrive_credentials_dir:
            file_id = upload_advice_to_drive(config, advice)
            print(f"Uploaded to Google Drive (id={file_id})")
        elif args.upload:
            print("Warning: --upload requested but GDRIVE_CREDENTIALS_DIR not set.")
        if args.email and config.email_to and config.smtp_host:
            email_advice(config, advice)
            print(f"Emailed daily advice to {config.email_to}")
        elif args.email:
            print("Warning: --email requested but EMAIL_TO / SMTP_HOST not set.")
        return

    # --- Oura analytics ---
    if args.source == "oura-analytics":
        from .oura_analytics import (
            generate_oura_analytics,
            save_analytics_local,
            upload_analytics_to_drive,
        )

        analytics = generate_oura_analytics(config, day)
        local = save_analytics_local(config, analytics)
        print(f"Saved Oura analytics: {local}")
        if args.upload and config.gdrive_credentials_dir:
            file_id = upload_analytics_to_drive(config, analytics)
            print(f"Uploaded to Google Drive (id={file_id})")
        elif args.upload:
            print("Warning: --upload requested but GDRIVE_CREDENTIALS_DIR not set.")
        return

    # --- Medical image analysis (MRI, X-ray, CT) ---
    if args.source == "scan":
        if not args.path:
            raise SystemExit("--path is required for scan source (path to image file)")
        if not config.anthropic_api_key:
            raise SystemExit("ANTHROPIC_API_KEY is required for image analysis")

        from .image_analyzer import (
            analyze_image,
            save_analysis_local,
            upload_analysis_to_drive,
        )

        path = Path(args.path)
        if not path.exists():
            raise SystemExit(f"File not found: {path}")

        print(f"Analyzing {path.name} with Claude Vision...")
        analysis = analyze_image(config, path)
        severity = analysis.get("severity", "UNKNOWN")

        print(f"\n{'='*60}")
        print(f"  Image Analysis — {path.name}")
        print(f"  Severity: {severity}")
        print(f"{'='*60}\n")
        print(analysis["analysis"])
        print(f"\n{'='*60}\n")

        local = save_analysis_local(config, analysis)
        print(f"Saved: {local}")

        if args.upload and config.gdrive_credentials_dir:
            file_id = upload_analysis_to_drive(config, analysis)
            print(f"Uploaded analysis to Google Drive (id={file_id})")
        elif args.upload:
            print("Warning: --upload requested but GDRIVE_CREDENTIALS_DIR not set.")
        return

    # --- PDF-based reports (blood, urine, annual, genetic, sperm, conclusion, prescription) ---
    if not args.path:
        raise SystemExit(f"--path is required for {args.source} source")

    path = Path(args.path)
    from .pdf_extract import extract_pdf_text

    extracted = extract_pdf_text(path)
    # Map source name to report kind
    kind = REPORT_TYPES.get(args.source, args.source)
    if args.source == "annual":
        kind = "annual_checkup"

    raw_text = extracted.get("text", "")
    metadata = {k: v for k, v in extracted.items() if k != "text"}
    metadata["kind"] = kind

    if config.database_url:
        save_lab_document_db(config, kind, day.isoformat(), raw_text, metadata)
        print(f"Saved {kind} document to Postgres.")
    else:
        target = write_lab_document_json(
            config.data_dir, kind, day.isoformat(), {"kind": kind, "text": raw_text, **metadata}
        )
        print(f"Saved {target}")


if __name__ == "__main__":
    main()
