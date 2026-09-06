"""Sunday reports: the brief measures its own health; non-daily emails carry their own heading."""
from __future__ import annotations

import json
import sys
import types
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PIXEL = "raw:com.google.step_count.cumulative:Google:Pixel 10 Pro:c64f62b7:Step Counter"
CLOUD = "google_health_api:FITBIT:PASSIVELY_MEASURED:Fitbit Air:FITNESS_BAND"
PEBBLE = "google_health_api:HEALTH_CONNECT:coredevices.coreapp"


def _write(tmp_path, d, **over):
    payload = {"steps": 9000, "sleep_hours": 0.0, "hrv": 0.0, "resting_hr": 0,
               "via": "google_health", "data_origins": [PIXEL]}
    payload.update(over)
    (tmp_path / f"fitbit_{d}.json").write_text(json.dumps(payload))


def test_brief_reports_recovery_gap_only_when_measured(tmp_path):
    from app.sync.health_os_brief import _system_health

    cfg = types.SimpleNamespace(data_dir=tmp_path)
    today = date(2026, 9, 6)
    for i in range(7):
        _write(tmp_path, date.fromordinal(today.toordinal() - i).isoformat())
    out = _system_health(cfg, today)
    assert "Recovery data (sleep/HRV/resting HR): 0/7 days" in out
    assert "no HRV/sleep/resting-HR reaching the system" in out
    assert "Fitbit Air never" in out

    # After the cloud link: watch origins and recovery metrics on the last two days.
    for i in range(2):
        _write(tmp_path, date.fromordinal(today.toordinal() - i).isoformat(),
               sleep_hours=7.6, hrv=20.3, resting_hr=67, via="google_health_api",
               data_origins=[PIXEL, CLOUD, PEBBLE])
    out = _system_health(cfg, today)
    assert "Watch data: Fitbit Air and Pebble both reporting" in out
    assert "Recovery data (sleep/HRV/resting HR): 2/7 days" in out
    assert "no HRV/sleep/resting-HR reaching the system" not in out
    assert "recovery data on only 2/7 days" in out

    for i in range(2, 7):
        _write(tmp_path, date.fromordinal(today.toordinal() - i).isoformat(),
               sleep_hours=7.0, hrv=22.0, resting_hr=60, data_origins=[PIXEL, CLOUD])
    out = _system_health(cfg, today)
    assert "7/7 days" in out and "Open issues" not in out


def _send(monkeypatch, advice):
    import smtplib

    from app.sync import email_sender as es

    captured = {}

    class _SMTP:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, *a):
            pass

        def sendmail(self, frm, to, body):
            captured["body"] = body

    monkeypatch.setattr(smtplib, "SMTP_SSL", _SMTP)
    monkeypatch.setattr(es, "_build_best_mover_html", lambda cfg, day: "")
    monkeypatch.setattr(es, "_build_action_buttons_html", lambda cfg, text, day: "")
    cfg = types.SimpleNamespace(
        email_to="me@example.com", smtp_host="smtp.example.com", smtp_port=465,
        smtp_user="u", smtp_password="p", server_url="", data_dir=Path("/nonexistent"),
        timezone=ZoneInfo("Europe/Paris"),
    )
    es.send_advice_email(cfg, advice)
    # Decode the MIME parts (utf-8 text parts are base64 on the wire).
    import email

    msg = email.message_from_string(captured["body"])
    parts = [str(msg["Subject"])]
    for part in msg.walk():
        if part.get_content_type().startswith("text/"):
            parts.append(part.get_payload(decode=True).decode("utf-8"))
    return "\n".join(parts)


def test_weekly_retro_email_has_its_own_heading(monkeypatch):
    body = _send(monkeypatch, {
        "report_type": "weekly_retrospective", "date": "2026-09-06", "model": "m",
        "advice": "## Weekly Retrospective\n- HRV: 20.3 ms", "context_summary": {},
    })
    assert "Weekly Retrospective" in body
    assert "Daily Health Plan" not in body
    assert "Fitbit Air data:" not in body


def test_daily_email_keeps_the_data_line(monkeypatch):
    body = _send(monkeypatch, {
        "report_type": "daily_advisor", "date": "2026-09-06", "model": "m",
        "advice": "1. **Walk**", "context_summary": {"fitbit_available": True, "lab_report_types": ["sperm_test"]},
    })
    assert "Daily Health Plan" in body and "Fitbit Air data: Yes" in body
