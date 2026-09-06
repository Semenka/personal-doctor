"""Scheduled-job hygiene: jobs return, throttles work, manual runs mirror the service."""
from __future__ import annotations

import json
import os
import sys
import types
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TZ = ZoneInfo("Europe/Paris")


def _cfg(tmp_path, **extra):
    base = dict(
        data_dir=tmp_path, timezone=TZ, database_url=None, oura_access_token=None,
        gdrive_credentials_dir=None, email_to=None, smtp_host=None, google_api_key=None,
        fitbit_client_id=None, fitbit_client_secret=None, fitbit_access_token=None,
        fitbit_refresh_token=None, fitbit_token_path=tmp_path / ".fitbit_token.json",
    )
    base.update(extra)
    return types.SimpleNamespace(**base)


def test_health_os_brief_job_returns_after_delivery(monkeypatch, tmp_path):
    """The Sunday brief used to sleep forever after sending (a worker thread leak)."""
    from app.sync import health_os_brief, scheduler

    monkeypatch.setattr(scheduler, "load_config", lambda: _cfg(tmp_path))
    monkeypatch.setattr(
        health_os_brief, "run_health_os_brief",
        lambda cfg, day: {"delivered": {"email": True, "whatsapp": False}},
    )
    monkeypatch.setattr(scheduler.time, "sleep", lambda s: (_ for _ in ()).throw(AssertionError("job slept")))
    scheduler.run_health_os_brief_job()  # must return, not block


def test_overdue_alert_throttle_honours_marker(monkeypatch, tmp_path):
    """`date` was not imported, so the twice-a-week throttle raised and every
    morning re-sent the full overdue wall."""
    import datetime as _dt

    from app.sync import checkup_schedule as cs
    from app.sync import scheduler, whatsapp_sender

    today = _dt.datetime.now(tz=TZ).date()
    marker = tmp_path / "checkups" / ".overdue_alert.json"
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({"last_sent": today.isoformat()}))

    sent = []
    monkeypatch.setattr(scheduler, "load_config", lambda: _cfg(tmp_path))
    monkeypatch.setattr(cs, "reconcile_schedule_with_results", lambda cfg, t: [])
    monkeypatch.setattr(cs, "overdue_lab_visits", lambda cfg, t: [{"key": "psa", "days_overdue": 40}])
    monkeypatch.setattr(cs, "upcoming_lab_visits", lambda cfg, within_days, today: [])
    monkeypatch.setattr(whatsapp_sender, "_run_openclaw_send", lambda msg, target=None: sent.append(msg) or True)
    scheduler.run_overdue_checkup_alert()
    assert sent == [], "overdue block re-sent on the same day the marker says it went out"

    # Four days later the block goes out again (and the marker moves forward).
    marker.write_text(json.dumps({"last_sent": (today - _dt.timedelta(days=4)).isoformat()}))
    scheduler.run_overdue_checkup_alert()
    assert len(sent) == 1 and "OVERDUE 40d" in sent[0]
    assert json.loads(marker.read_text())["last_sent"] == today.isoformat()


def test_manual_pipeline_syncs_the_fitbit_air(monkeypatch, tmp_path):
    """run_pipeline (OpenClaw 'run my health pipeline', POST /run) must refresh
    the daily watch, not only the sporadic ring."""
    from app.sync import config as cfgmod
    from app.sync import daily_advisor, run_pipeline, scheduler

    calls = []
    monkeypatch.setattr(cfgmod, "load_config", lambda: _cfg(tmp_path))
    monkeypatch.setattr(scheduler, "run_fitbit_sync", lambda: calls.append("fitbit"))
    monkeypatch.setattr(scheduler, "run_oura_weekly_sweep", lambda: calls.append("oura"))
    monkeypatch.setattr(daily_advisor, "advisor_has_credentials", lambda cfg: False)
    assert run_pipeline.main() == 1  # no advisor key → nothing to email
    assert calls == ["fitbit", "oura"]


def test_cli_fitbit_source_runs_the_service_job(monkeypatch, tmp_path):
    from app.sync import cli, scheduler

    calls = []
    monkeypatch.setattr(cli, "load_config", lambda: _cfg(tmp_path))
    monkeypatch.setattr(scheduler, "run_fitbit_sync", lambda: calls.append("fitbit"))
    monkeypatch.setattr(sys, "argv", ["cli", "--source", "fitbit"])
    cli.main()
    assert calls == ["fitbit"]


def test_health_endpoint_reports_watch_silence(monkeypatch, tmp_path):
    os.environ["HEALTH_LOG_DIR"] = str(tmp_path / "logs")
    from app import server

    pixel = "raw:com.google.step_count.cumulative:Google:Pixel 10 Pro:c64f62b7:Step Counter"
    for d in ("2026-09-06", "2026-09-05", "2026-09-04"):
        (tmp_path / f"fitbit_{d}.json").write_text(json.dumps(
            {"steps": 9000, "sleep_hours": 0.0, "via": "google_health", "data_origins": [pixel]}
        ))
    (tmp_path / "fitbit_2026-09-03.json").write_text(json.dumps(
        {"steps": 7000, "sleep_hours": 6.5, "via": "google_health",
         "data_origins": [pixel, "raw:com.google.sleep.segment:com.fitbit.FitbitMobile:health_platform"]}
    ))
    (tmp_path / ".google_health_token.json").write_text("{}")
    cfg = _cfg(tmp_path, gdrive_credentials_dir=str(tmp_path))
    out = server.wearable_status(cfg, today=date(2026, 9, 6))
    assert out["transports_authorized"] == {
        "google_health_api": False, "google_health_relay": True, "fitbit_web_api": False,
    }
    assert out["today_file"] == {"via": "google_health", "steps": 9000, "sleep_hours": 0.0, "fresh": True}
    assert out["phone_only"] is True and out["last_watch_date"] == "2026-09-03"
    assert out["devices"]["fitbit"] == {"silent_days": 3, "last_date": "2026-09-03"}
    assert out["devices"]["pebble"]["last_date"] is None
    assert out["summary"].startswith("Fitbit Air silent 3d · Pebble never")
