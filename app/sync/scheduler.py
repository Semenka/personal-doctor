from __future__ import annotations

import time


from apscheduler.schedulers.background import BackgroundScheduler

from .config import load_config
from .pipeline import load_oura_daily
from .storage import init_db, save_daily_payload_db, write_daily_json


def start_scheduler() -> None:
    config = load_config()
    scheduler = BackgroundScheduler(timezone=config.timezone)

    # 07:30 — Scan Google Drive for new health reports
    scheduler.add_job(run_gdrive_sync, "cron", hour=7, minute=30,
                      id="gdrive_daily", misfire_grace_time=3600)
    # 07:40 — Oura Ring data sync
    scheduler.add_job(run_oura_sync, "cron", hour=7, minute=40,
                      id="oura_daily", misfire_grace_time=3600)
    # 08:00 — AI daily advisor (Gemini 3.1 Flash Lite): analyse Oura + reports → email
    scheduler.add_job(run_daily_advisor, "cron", hour=8, minute=0,
                      id="advisor_daily", misfire_grace_time=3600)
    # 21:00 — Evening WhatsApp nudge if actions still open (F4)
    scheduler.add_job(run_whatsapp_evening_nudge, "cron", hour=21, minute=0,
                      id="evening_nudge", misfire_grace_time=3600)

    scheduler.start()
    print(
        "Scheduler started:\n"
        "  07:30  Google Drive health folder scan\n"
        "  07:40  Oura Ring data sync\n"
        "  08:00  AI daily advisor → email + WhatsApp\n"
        "  21:00  WhatsApp evening nudge (if actions still open)"
    )

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        scheduler.shutdown()


if __name__ == "__main__":
    start_scheduler()


def run_oura_sync() -> None:
    from datetime import datetime

    config = load_config()
    day = datetime.now(tz=config.timezone).date()
    if not config.oura_access_token:
        print("Skipping Oura sync: OURA_ACCESS_TOKEN not set.")
        return
    if config.database_url:
        init_db(config)

    # Retry up to 3 times with backoff for transient network failures
    payload = None
    for attempt in range(3):
        try:
            payload = load_oura_daily(config, day)
            break
        except Exception as exc:
            wait = 2 ** attempt
            print(f"Oura sync attempt {attempt + 1}/3 failed: {exc}")
            if attempt < 2:
                print(f"  Retrying in {wait}s...")
                time.sleep(wait)

    if payload is None:
        print("Oura sync: all retries exhausted.")
        return

    if config.database_url:
        save_daily_payload_db(config, payload)
        print("Saved daily payload to Postgres.")
    else:
        target = write_daily_json(config.data_dir, day.isoformat(), payload)
        print(f"Saved {target}")

    # Upload Oura analytics to Google Drive calendar folder
    if config.gdrive_credentials_dir:
        try:
            from .oura_analytics import _build_analytics, upload_analytics_to_drive

            analytics = _build_analytics(day, payload)
            file_id = upload_analytics_to_drive(config, analytics)
            print(f"Uploaded Oura data to Drive: me/health/{day.strftime('%Y/%m/%d')}/")
        except Exception as exc:
            print(f"Google Drive Oura upload failed: {exc}")


def run_research_sync() -> None:
    from datetime import datetime

    config = load_config()
    day = datetime.now(tz=config.timezone).date()
    if not config.database_url:
        raise RuntimeError("DATABASE_URL is required for research sync")
    init_db(config)
    from ..research.pipeline import run_daily_research

    run_daily_research(config, day)
    print("Saved daily research recommendations to Postgres.")


def run_gdrive_sync() -> None:
    from datetime import datetime

    config = load_config()
    if not config.gdrive_credentials_dir:
        return
    day = datetime.now(tz=config.timezone).date()
    from .gdrive_pipeline import sync_drive_reports

    results = sync_drive_reports(config, day)
    if results:
        print(f"Google Drive sync: processed {len(results)} new file(s).")
        for r in results:
            print(f"  - {r['file']} → {r['kind']}")
    else:
        print("Google Drive sync: no new files.")


def run_daily_advisor() -> None:
    from datetime import datetime

    config = load_config()
    if not config.google_api_key:
        print("Skipping daily advisor: GOOGLE_API_KEY not set.")
        return

    day = datetime.now(tz=config.timezone).date()
    from .daily_advisor import (
        build_stale_oura_advice,
        email_advice,
        generate_daily_advice,
        print_advice,
        save_advice_local,
        upload_advice_to_drive,
    )
    from .pipeline import check_oura_freshness

    # Oura freshness short-circuit: if the last 3 days have no real sleep/HRV
    # data, skip the LLM call and send a "fix your ring" warning instead.
    freshness = check_oura_freshness(config, day, max_stale_days=3)
    if not freshness["fresh"] and freshness["stale_days"] >= 3:
        advice = build_stale_oura_advice(day, freshness)
        print(f"Oura stale for {freshness['stale_days']} days — sending warning email.")
        save_advice_local(config, advice)
        try:
            if config.email_to and config.smtp_host:
                email_advice(config, advice)
                print(f"Emailed Oura-stale warning to {config.email_to}")
        except Exception as exc:
            print(f"Stale-warning email failed: {exc}")
        try:
            from .whatsapp_sender import send_whatsapp_advice

            send_whatsapp_advice(config, advice)
        except Exception as exc:
            print(f"WhatsApp stale-warning send failed: {exc}")
        return

    try:
        advice = generate_daily_advice(config, day)
    except Exception as exc:
        print(f"Daily advisor generation failed: {exc}")
        # Send a fallback email so the user knows something went wrong
        if config.email_to and config.smtp_host:
            try:
                fallback = {
                    "report_type": "daily_advisor",
                    "date": day.isoformat(),
                    "generated_at": datetime.utcnow().isoformat() + "Z",
                    "model": "N/A",
                    "advice": (
                        f"## Daily Health Plan — {day.isoformat()}\n\n"
                        f"**Advisor generation failed.**\n\n"
                        f"Error: {exc}\n\n"
                        "The AI advisor could not generate today's plan. "
                        "This may be caused by:\n"
                        "- Google API quota exceeded\n"
                        "- Temporary API outage\n"
                        "- Invalid API key\n\n"
                        "Please check the server logs for details."
                    ),
                    "context_summary": {
                        "oura_available": False,
                        "lab_reports_count": 0,
                        "lab_report_types": [],
                        "image_analyses_count": 0,
                        "image_severities": [],
                    },
                }
                email_advice(config, fallback)
                print(f"Sent fallback error email to {config.email_to}")
            except Exception as email_exc:
                print(f"Fallback email also failed: {email_exc}")
        return

    print_advice(advice)
    local_path = save_advice_local(config, advice)
    print(f"Saved daily advice: {local_path}")

    if config.gdrive_credentials_dir:
        try:
            file_id = upload_advice_to_drive(config, advice)
            print(f"Uploaded daily advice to Google Drive (id={file_id})")
        except Exception as exc:
            print(f"Google Drive upload failed: {exc}")

    if config.email_to and config.smtp_host:
        try:
            email_advice(config, advice)
            print(f"Emailed daily advice to {config.email_to}")
        except Exception as exc:
            print(f"Email send failed: {exc}")

    # WhatsApp delivery via OpenClaw gateway (F3)
    try:
        from .whatsapp_sender import send_whatsapp_advice

        send_whatsapp_advice(config, advice)
    except Exception as exc:
        print(f"WhatsApp send failed (non-fatal): {exc}")


def run_whatsapp_evening_nudge() -> None:
    """21:00 WhatsApp nudge: if any of today's actions are still open, ping the user."""
    from datetime import datetime

    config = load_config()
    day = datetime.now(tz=config.timezone).date()
    try:
        from .action_tracker import load_actions_with_sheets
        from .whatsapp_sender import send_whatsapp_evening_nudge

        actions = load_actions_with_sheets(config, day.isoformat())
        if not actions:
            return  # nothing generated today (e.g., stale Oura)
        done_count = sum(1 for a in actions if a.get("done"))
        if done_count >= len(actions):
            return  # everything already done, skip nudge
        send_whatsapp_evening_nudge(config, day.isoformat(), actions)
    except Exception as exc:
        print(f"Evening nudge failed (non-fatal): {exc}")
