"""One-shot pipeline runner (OpenClaw "run my health pipeline", POST /run, Cloud Run).

Runs the full daily health pipeline in sequence:
  1. Wearable sync — Fitbit Air (+ Pebble via Health Connect) and a sweep for
     sporadic Oura ring nights, with retries
  2. Google Drive health folder scan (if configured)
  3. AI Daily Advisor generation (Gemini)
  4. Email + WhatsApp delivery

Mirrors the launchd service's 07:38 → 08:00 job chain so an on-demand run
advises on the same data the scheduled one would. Until 2026-09-06 this path
synced only the Oura ring — the Fitbit Air, the daily device since 2026-08,
was never refreshed by a manual run.
Exit code 0 on success, 1 on critical failure (email never sent).
"""
from __future__ import annotations

import sys
from datetime import datetime


def main() -> int:
    from .config import load_config

    config = load_config()
    day = datetime.now(tz=config.timezone).date()
    print(f"=== Personal Doctor Pipeline — {day} ===")
    print(f"Timezone: {config.timezone}")
    print()

    # ── Step 1: Wearable sync ───────────────────────────────────────
    # Same jobs the service runs at 07:38 / 07:40. Both are self-contained:
    # they retry, back-fill the last 3 days, and print their own outcome.
    print("[1/4] Wearable sync (Fitbit Air + Pebble, Oura ring sweep)...")
    from .scheduler import run_fitbit_sync, run_oura_weekly_sweep

    try:
        run_fitbit_sync()
    except Exception as exc:
        print(f"  WARN: Fitbit Air sync failed: {exc}. Continuing with stored data.")
    try:
        run_oura_weekly_sweep()
    except Exception as exc:
        print(f"  WARN: Oura ring sweep failed: {exc}")

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
    print("[3/4] AI Daily Advisor...")
    advice = None
    stale_banner = None
    from .daily_advisor import (
        advisor_has_credentials,
        email_advice,
        generate_daily_advice,
        save_advice_local,
        upload_advice_to_drive,
    )

    if not advisor_has_credentials(config):
        print("  FAIL: Advisor API key not set for selected model.")
    else:
        from .pipeline import check_fitbit_freshness

        # Wearable freshness: nudge, never withhold. This used to short-circuit
        # on Oura and replace the WHOLE digest with a bare "fix your ring"
        # warning — which, now that Oura is gone and always reads stale, would
        # make every manual run emit that warning instead of the digest. The
        # scheduled path stopped doing this in d6c097b; this is the same fix for
        # the manual path, retargeted at the Fitbit Air.
        freshness = check_fitbit_freshness(config, day, max_stale_days=3)
        if not freshness["fresh"] and freshness["stale_days"] >= 3:
            stale_banner = (
                "### ⚠️ Fitbit Air has not synced recently\n\n"
                "Open Fitbit and Health Connect on the phone, then confirm data "
                "sharing is active."
            )
            print(
                f"  NOTE: Fitbit stale for {freshness['stale_days']}d "
                f"(last good: {freshness['last_fresh_date']}). "
                "Generating full advice with a sync banner."
            )

        # Origin-based watch check (same as the scheduled path): phone-sensor
        # steps keep the freshness check above green while the watches
        # themselves are silent. Say so in the digest instead of implying
        # sleep / HRV are low.
        silence: dict = {}
        device_txt = ""
        try:
            from .pipeline import describe_device_silence, watch_silence

            silence = watch_silence(config, day)
            device_txt = describe_device_silence(silence)
        except Exception as exc:
            print(f"  NOTE: watch-silence check skipped: {exc}")
        silent_days = int(silence.get("silent_days") or 0)
        if (silent_days >= 3 or device_txt) and not stale_banner:
            last = silence.get("last_watch_date")
            last_txt = f"last watch data {last}" if last else "no watch data on record"
            headline = device_txt or f"{silent_days} days without watch data"
            stale_banner = (
                f"### ⚠️ Watch not syncing — {headline} ({last_txt})\n\n"
                "Steps below are the phone's own sensor; sleep, HRV, resting HR and "
                "SpO2 are missing, not low. Fitbit Air: link the Google Health cloud "
                "once (`.venv/bin/python -m scripts.google_health_api_auth`) or fix the "
                "phone's Google Fit ↔ Health Connect sync. Pebble: Pebble app → "
                "Settings → Health → *Sync to Health Connect*."
            )
            print(f"  NOTE: {headline} ({last_txt}). Generating full advice with a banner.")

        try:
            if advice is None:
                advice = generate_daily_advice(config, day)
            if stale_banner:
                advice["advice"] = stale_banner + "\n\n" + advice.get("advice", "")
                summary = advice.setdefault("context_summary", {})
                if not freshness["fresh"] and freshness["stale_days"] >= 3:
                    summary["fitbit_stale_days"] = freshness["stale_days"]
                if silent_days >= 3:
                    summary["watch_silent_days"] = silent_days
                    summary["watch_last_date"] = silence.get("last_watch_date")
                if device_txt:
                    summary["watch_devices"] = device_txt
            ctx = advice.get("context_summary", {})
            print(f"  OK: generated ({len(advice['advice'])} chars)")
            print(f"    Fitbit Air data: {'Yes' if ctx.get('fitbit_available') else 'No'}")
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
                    "Please check logs for details."
                ),
                "context_summary": {
                    "fitbit_available": False,
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
        print("  SKIP: No advice to send (advisor API key missing).")
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

    # WhatsApp delivery via OpenClaw gateway (F3)
    try:
        from .whatsapp_sender import send_whatsapp_advice

        if send_whatsapp_advice(config, advice):
            print("  OK: WhatsApp digest sent.")
        else:
            print("  WARN: WhatsApp send returned False (see logs).")
    except Exception as exc:
        print(f"  WARN: WhatsApp send errored: {exc}")

    print()
    print("=== Pipeline complete ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
