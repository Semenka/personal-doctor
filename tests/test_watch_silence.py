"""Watch-silence detection is origin-based, and ring nights fill recovery gaps."""
from __future__ import annotations

import json
import sys
import types
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sync.pipeline import payload_has_watch_data, watch_silence  # noqa: E402
from app.sync.trend_analyzer import (  # noqa: E402
    load_primary_wearable_history,
    overlay_ring_recovery,
)

PIXEL = "raw:com.google.step_count.cumulative:Google:Pixel 10 Pro:c64f62b7:Step Counter"
FITBIT = "raw:com.google.sleep.segment:com.fitbit.FitbitMobile:health_platform"


def _phone_day(steps=12000):
    return {"steps": steps, "sleep_hours": 0.0, "hrv": 0.0, "resting_hr": 0,
            "spo2": 0.0, "via": "google_health", "data_origins": [PIXEL]}


def test_phone_steps_alone_are_not_watch_data():
    assert not payload_has_watch_data(_phone_day())
    assert not payload_has_watch_data(None)


def test_watch_origin_recovery_metric_or_cloud_pull_count():
    assert payload_has_watch_data(dict(_phone_day(), data_origins=[PIXEL, FITBIT]))
    assert payload_has_watch_data(dict(_phone_day(), sleep_hours=6.8))
    assert payload_has_watch_data(dict(_phone_day(), via="fitbit_web_api"))
    assert payload_has_watch_data(dict(_phone_day(), data_origins=["raw:x:Core Devices:Pebble 2"]))


def _write(tmp_path, name, payload):
    (tmp_path / name).write_text(json.dumps(payload))


def test_watch_silence_counts_back_to_last_watch_day(tmp_path):
    cfg = types.SimpleNamespace(data_dir=tmp_path)
    for i, d in enumerate(("2026-09-05", "2026-09-04", "2026-09-03")):
        _write(tmp_path, f"fitbit_{d}.json", _phone_day())
    _write(tmp_path, "fitbit_2026-09-02.json", dict(_phone_day(), sleep_hours=7.1))
    s = watch_silence(cfg, date(2026, 9, 5))
    assert s == {"silent_days": 3, "last_watch_date": "2026-09-02", "phone_only": True}


def test_watch_silence_zero_when_today_has_watch_data(tmp_path):
    cfg = types.SimpleNamespace(data_dir=tmp_path)
    _write(tmp_path, "fitbit_2026-09-05.json", dict(_phone_day(), hrv=22.0))
    s = watch_silence(cfg, date(2026, 9, 5))
    assert s["silent_days"] == 0 and s["last_watch_date"] == "2026-09-05"
    assert s["phone_only"] is False


def test_watch_silence_without_any_files(tmp_path):
    cfg = types.SimpleNamespace(data_dir=tmp_path)
    s = watch_silence(cfg, date(2026, 9, 5), lookback_days=5)
    assert s["silent_days"] == 6 and s["last_watch_date"] is None
    assert s["phone_only"] is False  # nothing arrived at all — not the masked case


def _ring_night():
    return {"sleep_hours": 6.4, "hrv": 19.0, "resting_hr": 61, "readiness_score": 71,
            "deep_sleep_min": 70, "steps": 0, "source": "oura"}


def test_overlay_fills_only_missing_recovery_from_a_real_night():
    m = overlay_ring_recovery(_phone_day(), _ring_night())
    assert m["sleep_hours"] == 6.4 and m["hrv"] == 19.0 and m["resting_hr"] == 61
    assert m["steps"] == 12000  # activity stays the phone/watch's
    assert m["recovery_source"] == "oura"


def test_overlay_ignores_stubs_and_never_overwrites_measured_values():
    stub = {"sleep_hours": 0.0, "hrv": 0.0, "resting_hr": 0, "readiness_score": 0}
    assert overlay_ring_recovery(_phone_day(), stub) == _phone_day()
    assert overlay_ring_recovery(_phone_day(), None) == _phone_day()
    watch = dict(_phone_day(), sleep_hours=7.5)
    m = overlay_ring_recovery(watch, _ring_night())
    assert m["sleep_hours"] == 7.5 and m["hrv"] == 19.0


def test_history_overlays_ring_nights_onto_fitbit_days(tmp_path):
    _write(tmp_path, "fitbit_2026-08-26.json", _phone_day())
    _write(tmp_path, "daily_2026-08-26.json", _ring_night())
    _write(tmp_path, "fitbit_2026-08-27.json", _phone_day())
    _write(tmp_path, "daily_2026-08-27.json", {"sleep_hours": 0.0, "hrv": 0.0})
    h = load_primary_wearable_history(tmp_path, date(2026, 8, 27), num_days=2)
    assert [d["_date"] for d in h] == ["2026-08-26", "2026-08-27"]
    assert h[0]["hrv"] == 19.0 and h[0]["recovery_source"] == "oura"
    assert h[1]["hrv"] == 0.0 and "recovery_source" not in h[1]
    assert h[0]["source"] == "fitbit"


def test_whatsapp_digest_carries_watch_silent_line(monkeypatch, tmp_path):
    from app.sync import whatsapp_sender as ws

    sent = {}
    monkeypatch.setattr(ws, "_run_openclaw_send", lambda msg, target=None: (sent.setdefault("msg", msg), True)[1])
    monkeypatch.setattr(ws, "_completion_footer", lambda cfg: "✅ footer")
    monkeypatch.setattr(ws, "_yesterday_activity_line", lambda cfg, day: "")
    cfg = types.SimpleNamespace(data_dir=tmp_path, email_to="", smtp_host="")
    advice = {
        "date": "2026-09-06", "model": "codex",
        "advice": "1. **Walk** — after lunch\n2. **Nap** — 14:00",
        "context_summary": {"watch_silent_days": 33, "watch_last_date": "2026-08-04"},
    }
    assert ws.send_whatsapp_advice(cfg, advice) is True
    lines = sent["msg"].splitlines()
    assert lines[0].startswith("🩺 Daily Plan — 2026-09-06")
    assert lines[1].startswith("⚠️ Watch silent 33d (phone steps only")
    assert "fitbit_auth" in lines[1]


def test_whatsapp_digest_has_no_watch_line_when_watch_reports(monkeypatch, tmp_path):
    from app.sync import whatsapp_sender as ws

    sent = {}
    monkeypatch.setattr(ws, "_run_openclaw_send", lambda msg, target=None: (sent.setdefault("msg", msg), True)[1])
    monkeypatch.setattr(ws, "_completion_footer", lambda cfg: "✅ footer")
    monkeypatch.setattr(ws, "_yesterday_activity_line", lambda cfg, day: "")
    cfg = types.SimpleNamespace(data_dir=tmp_path, email_to="", smtp_host="")
    ws.send_whatsapp_advice(cfg, {"date": "2026-09-06", "model": "codex", "advice": "1. **Walk**", "context_summary": {}})
    assert "Watch silent" not in sent["msg"]
