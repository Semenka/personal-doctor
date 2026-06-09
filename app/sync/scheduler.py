from __future__ import annotations

import time


from apscheduler.schedulers.background import BackgroundScheduler

from .config import load_config
from .pipeline import load_oura_daily
from .storage import init_db, save_daily_payload_db, write_daily_json


def start_scheduler() -> None:
    config = load_config()
    scheduler = BackgroundScheduler(timezone=config.timezone)

    # 07:20 — Fetch fresh research papers (PubMed + OpenAlex)
    scheduler.add_job(run_research_sync, "cron", hour=7, minute=20,
                      id="research_daily", misfire_grace_time=3600)
    # 07:30 — Scan Google Drive for new health reports
    scheduler.add_job(run_gdrive_sync, "cron", hour=7, minute=30,
                      id="gdrive_daily", misfire_grace_time=3600)
    # 07:40 — Oura Ring data sync
    scheduler.add_job(run_oura_sync, "cron", hour=7, minute=40,
                      id="oura_daily", misfire_grace_time=3600)
    # 07:43 — Fitbit data sync (second wearable, side-by-side with Oura)
    scheduler.add_job(run_fitbit_sync, "cron", hour=7, minute=43,
                      id="fitbit_daily", misfire_grace_time=3600)
    # 08:00 — AI daily advisor (Gemini 3.1 Flash Lite): analyse Oura + reports → email
    scheduler.add_job(run_daily_advisor, "cron", hour=8, minute=0,
                      id="advisor_daily", misfire_grace_time=3600)
    # 07:41 — Anomaly detector runs right after Oura sync (X1)
    scheduler.add_job(run_anomaly_detector_job, "cron", hour=7, minute=41,
                      id="anomaly_daily", misfire_grace_time=3600)
    # 07:45 — Supplement inventory decrement + low-stock alert (X2)
    scheduler.add_job(run_supplement_check_job, "cron", hour=7, minute=45,
                      id="supplement_daily", misfire_grace_time=3600)
    # 21:00 — Evening WhatsApp nudge if actions still open (F4)
    scheduler.add_job(run_whatsapp_evening_nudge, "cron", hour=21, minute=0,
                      id="evening_nudge", misfire_grace_time=3600)
    # Sunday 18:00 — Weekly retrospective (I4)
    scheduler.add_job(run_weekly_retro_job, "cron", day_of_week="sun", hour=18, minute=0,
                      id="weekly_retro", misfire_grace_time=7200)

    scheduler.start()
    print(
        "Scheduler started:\n"
        "  07:20  Research sync (PubMed + OpenAlex)\n"
        "  07:30  Google Drive health folder scan\n"
        "  07:40  Oura Ring data sync\n"
        "  07:43  Fitbit data sync\n"
        "  07:41  Anomaly detector\n"
        "  07:45  Supplement inventory check\n"
        "  08:00  AI daily advisor → email + WhatsApp\n"
        "  21:00  WhatsApp evening nudge (if actions still open)\n"
        "  Sun 18:00  Weekly retrospective"
    )


def run_anomaly_detector_job() -> None:
    try:
        from .anomaly_detector import run_anomaly_detector

        run_anomaly_detector()
    except Exception as exc:
        print(f"Anomaly detector failed: {exc}")


def run_supplement_check_job() -> None:
    try:
        from .supplement_inventory import run_supplement_check

        run_supplement_check()
    except Exception as exc:
        print(f"Supplement check failed: {exc}")


def run_weekly_retro_job() -> None:
    try:
        from .weekly_retro import run_weekly_retro

        run_weekly_retro()
    except Exception as exc:
        print(f"Weekly retro failed: {exc}")

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


def run_fitbit_sync() -> None:
    """07:43 — pull the day's Fitbit (bracelet) data into fitbit_<date>.json.

    Transport preference:
      1. Google Health (Fitness API) — the current registration path; Fitbit's
         standalone dev portal is closed to new apps. Uses the same Google
         Cloud OAuth client as the Drive sync.
      2. Legacy Fitbit Web API — only if its old token exists.
      3. Neither configured → graceful no-op (Oura-only pipeline).
    Mirrors run_oura_sync (retry 3× with backoff).
    """
    from datetime import datetime

    config = load_config()
    loader = None
    label = ""
    try:
        from .connectors.google_health import has_credentials as gh_has

        if gh_has(config):
            from .pipeline import load_fitbit_via_google_health

            loader, label = load_fitbit_via_google_health, "google-health"
    except Exception as exc:
        print(f"Google Health connector unavailable: {exc}")

    if loader is None:
        try:
            from .connectors.fitbit import has_credentials as fb_has

            if fb_has(config):
                from .pipeline import load_fitbit_daily

                loader, label = load_fitbit_daily, "fitbit-web-api"
        except Exception as exc:
            print(f"Fitbit connector unavailable: {exc}")

    if loader is None:
        print(
            "Skipping Fitbit sync: not authorized. Run "
            ".venv/bin/python -m scripts.google_health_auth (one-time consent)."
        )
        return

    from .pipeline import fitbit_data_is_fresh

    day = datetime.now(tz=config.timezone).date()
    payload = None
    for attempt in range(3):
        try:
            payload = loader(config, day)
            break
        except Exception as exc:
            wait = 2 ** attempt
            print(f"Fitbit sync ({label}) attempt {attempt + 1}/3 failed: {exc}")
            if attempt < 2:
                time.sleep(wait)

    if payload is None:
        print("Fitbit sync: all retries exhausted.")
        return

    target = write_daily_json(config.data_dir, day.isoformat(), payload, source="fitbit")
    fresh = "fresh" if fitbit_data_is_fresh(payload) else "empty"
    print(f"Saved {target} via {label} ({fresh})")


def run_research_sync() -> None:
    from datetime import datetime

    config = load_config()
    day = datetime.now(tz=config.timezone).date()
    if config.database_url:
        init_db(config)
    from ..research.pipeline import run_daily_research

    try:
        recs = run_daily_research(config, day)
        print(f"Research sync: generated {len(recs)} recommendation(s).")
    except Exception as exc:
        print(f"Research sync failed (non-fatal): {exc}")


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
    from .daily_advisor import advisor_has_credentials

    if not advisor_has_credentials(config):
        print("Skipping daily advisor: advisor API key not set for selected model.")
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
    from .pipeline import check_oura_freshness, oura_data_is_fresh
    from .storage import load_daily_payload

    # Race-condition guard: the cron sync at 07:40 sometimes fires before the
    # Oura ring has uploaded last night's sleep to the cloud. If today's saved
    # payload is empty but the API now has data, refetch before invoking
    # the LLM so the morning email isn't generated against zeros.
    try:
        today_iso = day.isoformat()
        try:
            current = load_daily_payload(config, today_iso)
        except FileNotFoundError:
            current = None
        if current is None or not oura_data_is_fresh(current):
            print(
                "Oura payload for today is empty/missing — running a second "
                "sync attempt before advisor (the Ring may have synced after the 07:40 cron)."
            )
            try:
                run_oura_sync()
            except Exception as resync_exc:
                print(f"  resync failed (non-fatal): {resync_exc}")
    except Exception as exc:
        print(f"  freshness re-sync check skipped: {exc}")

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


def run_auto_credit_job() -> None:
    """Auto-credit today's actions from Oura signals (07:42 + inside evening nudge)."""
    from datetime import datetime

    config = load_config()
    day = datetime.now(tz=config.timezone).date().isoformat()
    try:
        from .auto_complete import auto_credit_actions

        summary = auto_credit_actions(config, day)
        if summary.get("credited"):
            print(f"Auto-credited {len(summary['credited'])} action(s) from Oura.")
    except Exception as exc:
        print(f"Auto-credit failed (non-fatal): {exc}")


def run_overdue_checkup_alert() -> None:
    """08:05 — alert on overdue + imminent lab checkups (clears silent overdue items)."""
    from datetime import datetime

    config = load_config()
    try:
        from .checkup_schedule import upcoming_lab_visits
        from .whatsapp_sender import _run_openclaw_send

        overdue = upcoming_lab_visits(config, within_days=0)  # due or overdue
        upcoming = upcoming_lab_visits(config, within_days=7)
        # upcoming includes overdue; keep only the not-yet-due slice for the
        # "coming up" section.
        overdue_keys = {v.get("date") + (v.get("label") or "") for v in overdue}
        soon = [
            v for v in upcoming
            if (v.get("date") + (v.get("label") or "")) not in overdue_keys
        ]
        if not overdue and not soon:
            return
        lines = ["🧪 Lab check-ups"]
        for v in overdue:
            slip = " · ".join(v.get("panels", [])[:4])
            lines.append(f"⚠️ OVERDUE ({v['date']}): {v.get('label','')}")
            if slip:
                lines.append(f"   slip: {slip}")
        for v in soon:
            lines.append(f"📅 Due {v['date']}: {v.get('label','')}")
        msg = "\n".join(lines)
        _run_openclaw_send(msg)
        print(f"Overdue checkup alert sent ({len(overdue)} overdue, {len(soon)} soon).")
    except Exception as exc:
        print(f"Overdue checkup alert failed (non-fatal): {exc}")


def run_whatsapp_evening_nudge() -> None:
    """21:00 WhatsApp nudge: auto-credit from Oura first, then ping if still open."""
    from datetime import datetime

    config = load_config()
    day = datetime.now(tz=config.timezone).date()
    try:
        from .action_tracker import load_actions_with_sheets
        from .auto_complete import auto_credit_actions, render_auto_credit_line
        from .whatsapp_sender import _run_openclaw_send, send_whatsapp_evening_nudge

        # First, sweep Oura one more time (evening data is more complete).
        summary = auto_credit_actions(config, day.isoformat())
        credit_line = render_auto_credit_line(summary)

        actions = load_actions_with_sheets(config, day.isoformat())
        if not actions:
            return  # nothing generated today (e.g., stale Oura)
        done_count = sum(1 for a in actions if a.get("done"))

        if done_count >= len(actions):
            # All done (likely via auto-credit) — send a quiet confirmation.
            if summary.get("credited"):
                _run_openclaw_send(
                    f"🌙 {day.isoformat()} — all actions credited.\n{credit_line}"
                )
            return

        # Some still open: nudge, leading with what we auto-credited.
        if credit_line:
            _run_openclaw_send(f"🌙 {day.isoformat()}\n{credit_line}")
        send_whatsapp_evening_nudge(config, day.isoformat(), actions)
    except Exception as exc:
        print(f"Evening nudge failed (non-fatal): {exc}")
