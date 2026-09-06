from __future__ import annotations

import time


from apscheduler.schedulers.background import BackgroundScheduler

from .config import load_config
from .pipeline import load_oura_daily
from .storage import init_db, save_daily_payload_db, write_daily_json

# How many past days each morning sync re-fetches to catch late wearable
# uploads (Oura/Fitbit can land >1 day after the night they cover).
BACKFILL_DAYS = 3


def _load_stored_daily(config, day_iso: str, source: str = "oura"):
    """Best-effort read of an already-stored daily payload (None if absent)."""
    from .storage import load_wearable_payload_file

    try:
        return load_wearable_payload_file(config.data_dir, day_iso, source=source)
    except FileNotFoundError:
        return None
    except Exception:
        return None


def start_scheduler() -> None:
    config = load_config()
    scheduler = BackgroundScheduler(timezone=config.timezone)

    # 07:20 — Fetch fresh research papers (PubMed + OpenAlex)
    scheduler.add_job(run_research_sync, "cron", hour=7, minute=20,
                      id="research_daily", misfire_grace_time=3600)
    # 07:30 — Scan Google Drive for new health reports
    scheduler.add_job(run_gdrive_sync, "cron", hour=7, minute=30,
                      id="gdrive_daily", misfire_grace_time=3600)
    # 07:40 — Fitbit Air data sync (sole wearable source)
    # NOTE: the live service registers its jobs in app/server.py, not here —
    # this function is only used by the standalone runner. Keep both lists in
    # step when adding a job.
    scheduler.add_job(run_oura_weekly_sweep, "cron", hour=7, minute=38,
                      id="oura_daily_sweep", misfire_grace_time=600)
    scheduler.add_job(run_fitbit_sync, "cron", hour=7, minute=40,
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
    # Sunday 18:15 — Weekly journal review (reads the week's fetched papers)
    scheduler.add_job(run_journal_watch_job, "cron", day_of_week="sun", hour=18, minute=15,
                      id="journal_watch_weekly", misfire_grace_time=7200)
    # Sunday 18:30 — Health OS brief. Last, so it can include the fresh
    # journal review and any claims drafted along the way.
    scheduler.add_job(run_health_os_brief_job, "cron", day_of_week="sun", hour=18, minute=30,
                      id="health_os_brief", misfire_grace_time=7200)

    scheduler.start()
    print(
        "Scheduler started:\n"
        "  07:20  Research sync (PubMed + OpenAlex)\n"
        "  07:30  Google Drive health folder scan\n"
        "  07:40  Fitbit Air data sync\n"
        "  07:41  Anomaly detector\n"
        "  07:45  Supplement inventory check\n"
        "  08:00  AI daily advisor → email + WhatsApp\n"
        "  08:05  Check-up reconcile + overdue alert\n"
        "  21:00  WhatsApp evening nudge (if actions still open)\n"
        "  Sun 18:00  Weekly retrospective\n"
        "  Sun 18:15  Journal watch (weekly literature review)\n"
        "  Sun 18:30  Health OS brief → email + WhatsApp"
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


def run_journal_watch_job() -> None:
    """Sun 18:15 — review the week's fetched papers against the user's goals."""
    from datetime import datetime

    config = load_config()
    try:
        from ..research.journal_watch import run_journal_watch

        day = datetime.now(tz=config.timezone).date()
        out = run_journal_watch(config, day)
        print(
            f"Journal watch: {out['papers_reviewed']} papers reviewed "
            f"(model={out['model']})."
        )
    except Exception as exc:
        print(f"Journal watch failed (non-fatal): {exc}")


def run_health_os_brief_job() -> None:
    """Sun 18:30 — assemble and deliver the weekly Health OS brief."""
    from datetime import datetime

    config = load_config()
    try:
        from .health_os_brief import run_health_os_brief

        day = datetime.now(tz=config.timezone).date()
        out = run_health_os_brief(config, day)
        delivered = out.get("delivered", {})
        print(
            f"Health OS brief sent (email={delivered.get('email')}, "
            f"whatsapp={delivered.get('whatsapp')})."
        )
    except Exception as exc:
        print(f"Health OS brief failed (non-fatal): {exc}")

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        scheduler.shutdown()


if __name__ == "__main__":
    start_scheduler()


def run_oura_weekly_sweep() -> None:
    """Sun 17:50 — capture SPORADIC Oura data (ring is no longer daily-worn).

    The user moved to Fitbit Air as the daily device (2026-08); the ring is
    only worn occasionally, so the daily Oura cron was removed. Without any
    ingestion path those occasional nights would be lost. This sweep pulls
    the last 7 days once a week and stores any day that has real data and
    isn't already stored fresh — silent when the ring wasn't worn at all.
    Runs before the Sunday retro/brief (18:00/18:30) so they see the data.
    """
    from datetime import datetime, timedelta

    config = load_config()
    if not config.oura_access_token:
        return
    from .pipeline import oura_data_is_fresh

    day = datetime.now(tz=config.timezone).date()
    captured = 0
    for back in range(0, 7):
        bday = day - timedelta(days=back)
        try:
            existing = _load_stored_daily(config, bday.isoformat())
            if existing and oura_data_is_fresh(existing):
                continue
            payload = load_oura_daily(config, bday)
            if not oura_data_is_fresh(payload):
                continue
            if config.database_url:
                save_daily_payload_db(config, payload)
            else:
                write_daily_json(config.data_dir, bday.isoformat(), payload)
            captured += 1
            print(f"Oura weekly sweep: captured sporadic ring data for {bday}.")
        except Exception as exc:
            print(f"Oura weekly sweep {bday} skipped: {exc}")
    if not captured:
        print("Oura weekly sweep: no new ring data this week (expected — ring is sporadic).")


def run_oura_sync() -> None:
    from datetime import datetime, timedelta

    config = load_config()
    day = datetime.now(tz=config.timezone).date()
    if not config.oura_access_token:
        print("Skipping Oura sync: OURA_ACCESS_TOKEN not set.")
        return
    if config.database_url:
        init_db(config)

    # Backfill the last few days: the Ring may upload to Oura's cloud AFTER
    # the morning cron — sometimes more than a day late (observed 2026-06-22,
    # which only landed by 06-24). A 1-day window misses those, leaving a
    # permanently-empty daily file. Re-fetch the last BACKFILL_DAYS and
    # overwrite any stored file that is still stale.
    from .pipeline import oura_data_is_fresh

    for back in range(1, BACKFILL_DAYS + 1):
        bday = day - timedelta(days=back)
        try:
            # Skip if the stored file is already fresh — avoid needless API calls.
            existing = _load_stored_daily(config, bday.isoformat())
            if existing and oura_data_is_fresh(existing):
                continue
            bpayload = load_oura_daily(config, bday)
            if oura_data_is_fresh(bpayload):
                if config.database_url:
                    save_daily_payload_db(config, bpayload)
                else:
                    write_daily_json(config.data_dir, bday.isoformat(), bpayload)
                print(f"Backfilled Oura {bday} (late upload recovered).")
        except Exception as exc:
            print(f"Oura backfill {bday} skipped: {exc}")

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


def _looks_like_auth_failure(detail: str) -> bool:
    """True when an error string signals a dead/expired OAuth grant."""
    d = (detail or "").lower()
    return any(
        s in d
        for s in (
            "invalid_grant",
            "token has been expired",
            "revoked",
            "invalid_token",
            "unauthorized",
            "invalid credentials",
            "401",
        )
    )


def _alert_fitbit_auth_failure(config, detail: str) -> None:
    """Surface a dead Google Health token to the user (throttled to 1×/day).

    Without this the 07:43 sync fails silently — a token death once went
    unnoticed for 12 days. Google expires refresh tokens after 7 days while
    the OAuth app is in "Testing" publishing status, so this recurs until the
    consent screen is published to Production.
    """
    import json as _json
    from datetime import datetime

    marker = config.data_dir / ".fitbit_auth_alert.json"
    today = datetime.now(tz=config.timezone).date().isoformat()
    try:
        if marker.exists():
            last = _json.loads(marker.read_text()).get("date")
            if last == today:
                return  # already alerted today
    except Exception:
        pass

    msg = (
        "⚠️ Fitbit Air sync is DOWN — Google Health token expired.\n\n"
        f"The 07:43 daily sync can't pull bracelet data: {detail[:140]}\n\n"
        "Two-step fix:\n"
        "1) PERMANENT — publish the OAuth app so the token stops dying every "
        "7 days:\n"
        "   console.cloud.google.com/auth/audience → 'PUBLISH APP' "
        "(Testing → Production).\n"
        "2) Re-authorize once (on the Mac Mini):\n"
        "   cd ~/personal-doctor && .venv/bin/python -m scripts.google_health_auth\n\n"
        "After step 1 you won't need to repeat step 2."
    )
    try:
        from .whatsapp_sender import _run_openclaw_send

        sent = _run_openclaw_send(msg)
        print(f"Fitbit auth-failure alert sent: {sent}")
    except Exception as exc:
        print(f"Fitbit auth-failure alert could not be sent: {exc}")

    try:
        marker.write_text(_json.dumps({"date": today, "detail": detail[:200]}))
    except Exception:
        pass


def _fitbit_payload_improves(old, new) -> bool:
    """True when the re-fetched payload holds strictly more data than stored —
    never regress a finalized day, always replace an upload-lag stub."""
    for key in ("steps", "sleep_hours", "resting_hr", "active_zone_minutes",
                "active_minutes", "calories", "spo2", "hrv"):
        if (new.get(key) or 0) > (old.get(key) or 0):
            return True
    return False


def run_fitbit_sync() -> None:
    """07:40 — pull the day's Fitbit Air data into fitbit_<date>.json.

    Transport preference (verified 2026-09-05):
      1. Fitbit Web API — reads Fitbit's own cloud, so it carries sleep
         staging, HRV, resting HR, SpO2 and breathing rate regardless of the
         phone. Authorized once via ``scripts.fitbit_auth``.
      2. Google Health (Fitness API) — the phone's Health Connect -> Google
         Fit relay. It stopped carrying any Fitbit-origin data on 2026-08-04;
         since then it only delivers the Pixel's own step counter. It is still
         the only path a Health Connect-only device (Pebble) can take, so when
         both are authorized the cloud payload is primary and this one fills
         gaps and contributes ``data_origins``.
      3. Neither configured → graceful no-op with a clear authorization warning.
    """
    from datetime import datetime

    config = load_config()
    loaders = []  # (callable, label) in preference order
    try:
        from .connectors.google_health_api import has_credentials as api_has

        if api_has(config):
            from .pipeline import load_fitbit_via_google_health_api

            loaders.append((load_fitbit_via_google_health_api, "google-health-api"))
    except Exception as exc:
        print(f"Google Health API connector unavailable: {exc}")

    try:
        from .connectors.fitbit import has_credentials as fb_has

        if fb_has(config):
            from .pipeline import load_fitbit_daily

            loaders.append((load_fitbit_daily, "fitbit-web-api"))
    except Exception as exc:
        print(f"Fitbit connector unavailable: {exc}")

    try:
        from .connectors.google_health import has_credentials as gh_has

        if gh_has(config):
            from .pipeline import load_fitbit_via_google_health

            loaders.append((load_fitbit_via_google_health, "google-health"))
    except Exception as exc:
        print(f"Google Health connector unavailable: {exc}")

    if not loaders:
        print(
            "Skipping Fitbit sync: not authorized. Run "
            ".venv/bin/python -m scripts.google_health_api_auth (Fitbit Air + Pebble "
            "cloud, preferred) or .venv/bin/python -m scripts.google_health_auth "
            "(phone relay)."
        )
        return

    if len(loaders) == 1:
        loader, label = loaders[0]
    else:
        from .pipeline import merge_fitbit_payloads

        (primary_loader, primary_label) = loaders[0]
        secondaries = loaders[1:]

        def loader(cfg, d):
            merged = primary_loader(cfg, d)
            for sec_loader, sec_label in secondaries:
                try:
                    merged = merge_fitbit_payloads(merged, sec_loader(cfg, d))
                except Exception as exc:  # a lower source must never sink the primary
                    print(f"Fitbit sync: {sec_label} merge skipped for {d}: {exc}")
            return merged

        label = "+".join([primary_label] + [lbl for _, lbl in secondaries])

    from datetime import timedelta

    from .pipeline import fitbit_data_is_fresh

    day = datetime.now(tz=config.timezone).date()

    # Backfill the last few days: at 07:43 the same-day pull only has last
    # night's sleep + early steps; daytime activity finalizes later and the
    # phone bridge can relay with >1 day of lag. Re-fetch the last
    # BACKFILL_DAYS so stored files hold final numbers and late uploads are
    # never lost. Skip days already stored fresh to save API calls.
    for back in range(1, BACKFILL_DAYS + 1):
        bday = day - timedelta(days=back)
        try:
            # Always re-fetch: a stored file can pass the freshness check yet
            # still be an upload-lag stub (e.g. 47 steps written at 07:43
            # before the phone bridge relayed the real day — observed
            # 07-01..07-03 stuck as stubs while the cloud held 17k steps).
            # Overwrite whenever the cloud now has strictly more data.
            existing = _load_stored_daily(config, bday.isoformat(), source="fitbit")
            bpayload = loader(config, bday)
            if not fitbit_data_is_fresh(bpayload):
                continue
            if existing and not _fitbit_payload_improves(existing, bpayload):
                continue
            write_daily_json(config.data_dir, bday.isoformat(), bpayload, source="fitbit")
            print(f"Backfilled Fitbit {bday} via {label} (late upload recovered).")
            # Backfilled activity (steps/AZM) often finalizes after same-day
            # auto-credit already ran and missed it. Re-credit so movement
            # actions get their adherence signal.
            try:
                from .auto_complete import auto_credit_actions

                summary = auto_credit_actions(config, bday.isoformat())
                if summary.get("credited"):
                    print(
                        f"Re-credited {len(summary['credited'])} action(s) for "
                        f"{bday} from backfilled Fitbit activity."
                    )
            except Exception as exc:
                print(f"Fitbit backfill re-credit skipped: {exc}")
        except Exception as exc:
            print(f"Fitbit backfill {bday} skipped: {exc}")

    payload = None
    last_error = ""
    for attempt in range(3):
        try:
            payload = loader(config, day)
            break
        except Exception as exc:
            last_error = str(exc)
            wait = 2 ** attempt
            print(f"Fitbit sync ({label}) attempt {attempt + 1}/3 failed: {exc}")
            if attempt < 2:
                time.sleep(wait)

    if payload is None:
        print("Fitbit sync: all retries exhausted.")
        # A dead OAuth grant fails every day until re-authorized — alert the
        # user (throttled) instead of failing silently for weeks.
        if _looks_like_auth_failure(last_error):
            _alert_fitbit_auth_failure(config, last_error)
        return

    target = write_daily_json(config.data_dir, day.isoformat(), payload, source="fitbit")
    fresh = "fresh" if fitbit_data_is_fresh(payload) else "empty"
    print(f"Saved {target} via {label} ({fresh})")
    # Sync succeeded — clear any stale auth-failure marker so a future
    # failure alerts again immediately.
    try:
        marker = config.data_dir / ".fitbit_auth_alert.json"
        if marker.exists():
            marker.unlink()
    except Exception:
        pass


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


def _recent_fitbit_activity(config, day, lookback_days: int = 2) -> bool:
    """True if a finalized Fitbit day in [day-lookback_days, day] has real steps.

    Same-day Fitbit files lag (the Google-Health phone bridge often only
    finalizes a day in the next morning's backfill), so those partial files
    show a handful of steps (e.g. 47/93). A threshold of 500 steps cleanly
    separates those upload-lag stubs from a genuinely recorded active day, so
    we treat Fitbit as "alive" only when there's real recent movement data to
    advise on.
    """
    from datetime import timedelta

    from .auto_complete import _load_fitbit

    for i in range(lookback_days + 1):
        d = (day - timedelta(days=i)).isoformat()
        fb = _load_fitbit(config.data_dir, d)
        if fb and (fb.get("steps") or 0) >= 500:
            return True
    return False


def run_daily_advisor() -> None:
    from datetime import datetime

    config = load_config()
    from .daily_advisor import advisor_has_credentials

    if not advisor_has_credentials(config):
        print("Skipping daily advisor: advisor API key not set for selected model.")
        return

    day = datetime.now(tz=config.timezone).date()
    from .daily_advisor import (
        email_advice,
        generate_daily_advice,
        print_advice,
        save_advice_local,
        upload_advice_to_drive,
    )
    from .pipeline import check_fitbit_freshness, fitbit_data_is_fresh
    from .storage import load_wearable_payload_file

    # Race-condition guard: refresh Fitbit Air before invoking the advisor.
    try:
        today_iso = day.isoformat()
        try:
            current = load_wearable_payload_file(config.data_dir, today_iso, source="fitbit")
        except FileNotFoundError:
            current = None
        if current is None or not fitbit_data_is_fresh(current):
            print(
                "Fitbit Air payload is empty/missing — running a second sync before advisor."
            )
            try:
                run_fitbit_sync()
            except Exception as resync_exc:
                print(f"  resync failed (non-fatal): {resync_exc}")
    except Exception as exc:
        print(f"  freshness re-sync check skipped: {exc}")

    # Oura freshness short-circuit: if the last 3 days have no real sleep/HRV
    # data, we would normally skip the LLM call and send a bare "fix your ring"
    # warning. But that withholds the whole health digest (fertility protocol,
    # labs, biomarker trends, heat-avoidance, activity coaching) on stale-Oura
    # mornings — exactly what happened 2026-06-29, when the user got only a
    # warning and no advice. The advisor already handles missing Oura
    # gracefully and folds in Fitbit activity + labs, so only fall back to the
    # bare warning when Fitbit activity is ALSO dead. If a recent Fitbit day
    # has real steps, generate the full advice instead.
    freshness = check_fitbit_freshness(config, day, max_stale_days=3)
    stale_banner = None
    if not freshness["fresh"] and freshness["stale_days"] >= 3:
        stale_banner = (
            "### ⚠️ Fitbit Air has not synced recently\n\n"
            "Open Fitbit and Health Connect on the phone, then confirm data sharing is active."
        )

    # Origin-based check: phone-sensor steps kept `check_fitbit_freshness`
    # green for a month (2026-08-04 → 09-05) while the watch itself sent
    # nothing. Judge the watch by watch evidence and say so in every channel.
    silence = {}
    try:
        from .pipeline import watch_silence

        silence = watch_silence(config, day)
    except Exception as exc:
        print(f"  watch-silence check skipped: {exc}")
    silent_days = int(silence.get("silent_days") or 0)
    from .pipeline import describe_device_silence

    device_txt = describe_device_silence(silence)
    if (silent_days >= 3 or device_txt) and not stale_banner:
        last = silence.get("last_watch_date")
        last_txt = f"last watch data {last}" if last else "no watch data on record"
        headline = device_txt or f"{silent_days} days without watch data"
        stale_banner = (
            f"### ⚠️ Watch not syncing — {headline} ({last_txt})\n\n"
            "Steps below are the phone's own sensor; sleep, HRV, resting HR and "
            "SpO2 are missing, not low. Google Fit's streams show the Health "
            "Connect relay died for *both* the Fitbit and Oura apps (heart rate "
            "2026-06-13, sleep 2026-08-04), so the break is on the phone's Google "
            "Fit ↔ Health Connect link: Google Fit → Profile → Settings → Health "
            "Connect → re-enable sync and *read* for sleep/heart/SpO2; then Fitbit "
            "app → Health Connect → *write* on. Or link the Google Health cloud once "
            "(`.venv/bin/python -m scripts.google_health_api_auth`) to bypass the phone. "
            "Pebble: Pebble app → Settings → Health → *Sync to Health Connect*."
        )

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
                        "fitbit_available": False,
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
    """Auto-credit today's actions from Fitbit Air signals."""
    from datetime import datetime

    config = load_config()
    day = datetime.now(tz=config.timezone).date().isoformat()
    try:
        from .auto_complete import auto_credit_actions

        summary = auto_credit_actions(config, day)
        if summary.get("credited"):
            print(f"Auto-credited {len(summary['credited'])} action(s) from Fitbit Air.")
    except Exception as exc:
        print(f"Auto-credit failed (non-fatal): {exc}")


def run_overdue_checkup_alert() -> None:
    """08:05 — alert on overdue + imminent lab checkups (clears silent overdue items)."""
    from datetime import datetime

    config = load_config()
    today = datetime.now(tz=config.timezone).date()
    try:
        from .checkup_schedule import (
            PRIMARY_LAB,
            PRIMARY_LAB_ADDRESS,
            overdue_lab_visits,
            reconcile_schedule_with_results,
            upcoming_lab_visits,
        )
        from .whatsapp_sender import _run_openclaw_send

        # Roll forward anything the ingested results prove was done, so we
        # never nag about a test whose report is already on file.
        reconciled = reconcile_schedule_with_results(config, today)
        if reconciled:
            print(f"Checkup schedule reconciled from results: {', '.join(reconciled)}")

        # upcoming_lab_visits only looks forward — overdue items need their
        # own query or they are invisible (they were, for months).
        overdue = overdue_lab_visits(config, today)
        soon = upcoming_lab_visits(config, within_days=7, today=today)
        if not overdue and not soon:
            return

        # Throttle the overdue block to twice a week: with many items overdue
        # a daily wall of ⚠️ lines trains the user to ignore the channel.
        marker = config.data_dir / "checkups" / ".overdue_alert.json"
        send_overdue = bool(overdue)
        if send_overdue and marker.exists():
            try:
                import json as _json
                last = date.fromisoformat(
                    _json.loads(marker.read_text()).get("last_sent", "1970-01-01")
                )
                send_overdue = (today - last).days >= 3
            except Exception:
                pass
        if not send_overdue and not soon:
            return

        lines = ["🧪 Lab check-ups"]
        if send_overdue:
            for v in overdue[:6]:
                lines.append(
                    f"⚠️ OVERDUE {v['days_overdue']}d: {v.get('name', v.get('key', ''))}"
                )
                if v.get("lab_panel_name"):
                    lines.append(f"   slip: {v['lab_panel_name']}")
            if len(overdue) > 6:
                lines.append(f"   …and {len(overdue) - 6} more overdue")
            lines.append(f"🏥 {PRIMARY_LAB} — {PRIMARY_LAB_ADDRESS}")
        for v in soon:
            lines.append(f"📅 Due {v['date']}: {v.get('label', '')}")
        _run_openclaw_send("\n".join(lines))
        if send_overdue:
            import json as _json
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(_json.dumps({"last_sent": today.isoformat()}))
        print(
            f"Overdue checkup alert sent ({len(overdue)} overdue, {len(soon)} soon, "
            f"overdue block {'included' if send_overdue else 'throttled'})."
        )
    except Exception as exc:
        print(f"Overdue checkup alert failed (non-fatal): {exc}")


def run_whatsapp_evening_nudge() -> None:
    """21:00 WhatsApp nudge: auto-credit from Fitbit Air, then ping if still open."""
    from datetime import datetime

    config = load_config()
    day = datetime.now(tz=config.timezone).date()
    try:
        from .action_tracker import load_actions_with_sheets
        from .auto_complete import auto_credit_actions, render_auto_credit_line
        from .whatsapp_sender import _run_openclaw_send, send_whatsapp_evening_nudge

        # First, sweep Fitbit Air one more time (evening data is more complete).
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
