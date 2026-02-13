"""One-shot pipeline runner for Cloud Run Jobs.

Runs the full daily health pipeline in sequence:
  1. Oura Ring data sync (with retries)
  2. Google Drive health folder scan (if configured)
  3. AI Daily Advisor generation (Claude Opus 4.6)
  4. Email delivery

Designed to be triggered by Cloud Scheduler → Cloud Run Job.
Exit code 0 on success, 1 on critical failure (email never sent).
"""
from __future__ import annotations

import sys
import time
from datetime import datetime


def main() -> int:
    from .config import load_config

    config = load_config()
    day = datetime.now(tz=config.timezone).date()
    print(f"=== Personal Doctor Pipeline — {day} ===")
    print(f"Timezone: {config.timezone}")
    print()

    # ── Step 1: Oura Ring sync ──────────────────────────────────────
    print("[1/4] Oura Ring data sync...")
    oura_ok = False
    if not config.oura_access_token:
        print("  SKIP: OURA_ACCESS_TOKEN not set.")
    else:
        from .pipeline import load_oura_daily
        from .storage import init_db, save_daily_payload_db, write_daily_json

        if config.database_url:
            init_db(config)

        for attempt in range(3):
            try:
                payload = load_oura_daily(config, day)
                if config.database_url:
                    save_daily_payload_db(config, payload)
                    print("  OK: saved to Postgres.")
                else:
                    target = write_daily_json(config.data_dir, day.isoformat(), payload)
                    print(f"  OK: saved {target}")
                oura_ok = True
                break
            except Exception as exc:
                wait = 2 ** attempt
                print(f"  Attempt {attempt + 1}/3 failed: {exc}")
                if attempt < 2:
                    time.sleep(wait)

        if not oura_ok:
            print("  WARN: Oura sync failed after 3 attempts. Continuing without Oura data.")

        # Upload Oura analytics to Drive
        if oura_ok and config.gdrive_credentials_dir:
            try:
                from .oura_analytics import _build_analytics, upload_analytics_to_drive

                analytics = _build_analytics(day, payload)
                upload_analytics_to_drive(config, analytics)
                print(f"  OK: uploaded Oura data to Drive: me/health/{day.strftime('%Y/%m/%d')}/")
            except Exception as exc:
                print(f"  WARN: Drive upload failed: {exc}")

    print()

    # ── Step 2: Google Drive scan ───────────────────────────────────
    print("[2/4] Google Drive health folder scan...")
    if not config.gdrive_credentials_dir:
        print("  SKIP: GDRIVE_CREDENTIALS_DIR not set.")
    else:
        try:
            from .gdrive_pipeline import sync_drive_reports

            results = sync_drive_reports(config, day)
            if results:
                print(f"  OK: processed {len(results)} file(s).")
                for r in results:
                    print(f"    - {r['file']} → {r['kind']}")
            else:
                print("  OK: no new files.")
        except Exception as exc:
            print(f"  WARN: Drive scan failed: {exc}")

    print()

    # ── Step 3: AI Daily Advisor ────────────────────────────────────
    print("[3/4] AI Daily Advisor (Claude Opus 4.6)...")
    advice = None
    if not config.anthropic_api_key:
        print("  FAIL: ANTHROPIC_API_KEY not set.")
    else:
        from .daily_advisor import (
            email_advice,
            generate_daily_advice,
            save_advice_local,
            upload_advice_to_drive,
        )

        try:
            advice = generate_daily_advice(config, day)
            ctx = advice.get("context_summary", {})
            print(f"  OK: generated ({len(advice['advice'])} chars)")
            print(f"    Oura data: {'Yes' if ctx.get('oura_available') else 'No'}")
            print(f"    Lab reports: {ctx.get('lab_reports_count', 0)}")
            print(f"    Image scans: {ctx.get('image_analyses_count', 0)}")

            # Save locally
            local_path = save_advice_local(config, advice)
            print(f"  OK: saved {local_path}")

            # Upload to Drive
            if config.gdrive_credentials_dir:
                try:
                    file_id = upload_advice_to_drive(config, advice)
                    print(f"  OK: uploaded to Drive (id={file_id})")
                except Exception as exc:
                    print(f"  WARN: Drive upload failed: {exc}")

        except Exception as exc:
            print(f"  FAIL: {exc}")
            # Create fallback advice with error info
            advice = {
                "report_type": "daily_advisor",
                "date": day.isoformat(),
                "generated_at": datetime.utcnow().isoformat() + "Z",
                "model": "N/A",
                "advice": (
                    f"## Daily Health Plan — {day.isoformat()}\n\n"
                    f"**Advisor generation failed.**\n\n"
                    f"Error: {exc}\n\n"
                    "Please check Cloud Run logs and "
                    "https://console.anthropic.com for details."
                ),
                "context_summary": {
                    "oura_available": False,
                    "lab_reports_count": 0,
                    "lab_report_types": [],
                    "image_analyses_count": 0,
                    "image_severities": [],
                },
            }

    print()

    # ── Step 4: Email delivery ──────────────────────────────────────
    print("[4/4] Email delivery...")
    if not advice:
        print("  SKIP: No advice to send (ANTHROPIC_API_KEY missing).")
        return 1

    if not config.email_to or not config.smtp_host:
        print("  SKIP: EMAIL_TO / SMTP_HOST not configured.")
        return 1

    from .daily_advisor import email_advice

    try:
        email_advice(config, advice)
        print(f"  OK: emailed to {config.email_to}")
    except Exception as exc:
        print(f"  FAIL: {exc}")
        return 1

    print()
    print("=== Pipeline complete ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
