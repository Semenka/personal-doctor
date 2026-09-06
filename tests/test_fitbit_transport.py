"""Fitbit transport preference: cloud (Web API) primary, phone relay fills gaps."""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sync.pipeline import merge_fitbit_payloads  # noqa: E402
from app.sync.daily_advisor import _device_sources  # noqa: E402


def _cloud():
    return {
        "date": "2026-09-05", "source": "fitbit", "via": "fitbit_web_api",
        "steps": 8100, "sleep_hours": 6.9, "hrv": 24.0, "resting_hr": 58,
        "spo2": 96.5, "active_zone_minutes": 0, "deep_sleep_min": 55,
        "activity_is_previous_day": False,
    }


def _relay():
    return {
        "date": "2026-09-05", "source": "fitbit", "via": "google_health",
        "steps": 12, "sleep_hours": 0.0, "hrv": 0.0, "resting_hr": 0,
        "spo2": 0.0, "active_zone_minutes": 31, "deep_sleep_min": 0,
        "data_origins": ["raw:com.google.step_count.cumulative:Google:Pixel 10 Pro:c64f62b7:Step Counter"],
        "activity_is_previous_day": False,
    }


def test_cloud_values_win_and_relay_fills_gaps():
    m = merge_fitbit_payloads(_cloud(), _relay())
    assert m["steps"] == 8100            # cloud wins over the phone stub
    assert m["hrv"] == 24.0 and m["resting_hr"] == 58 and m["sleep_hours"] == 6.9
    assert m["active_zone_minutes"] == 31  # gap filled from the relay
    assert m["via"] == "fitbit_web_api+google_health"
    assert m["source"] == "fitbit" and m["date"] == "2026-09-05"
    assert m["data_origins"] == _relay()["data_origins"]


def test_merge_never_copies_meta_or_false_values():
    relay = _relay()
    relay["activity_is_previous_day"] = True
    relay["date"] = "2026-01-01"
    m = merge_fitbit_payloads(_cloud(), relay)
    assert m["activity_is_previous_day"] is False
    assert m["date"] == "2026-09-05"


def test_merge_without_origins_is_stable():
    cloud = _cloud()
    m = merge_fitbit_payloads(cloud, {"date": "2026-09-05", "steps": 0})
    assert m["data_origins"] == []
    assert m["via"] == "fitbit_web_api"


def test_device_sources_labels_cloud_pull_and_dedupes():
    assert _device_sources(_cloud()) == "Fitbit Air"
    m = merge_fitbit_payloads(_cloud(), _relay())
    assert _device_sources(m) == "Fitbit Air, Pixel phone sensors"
    both = dict(m, data_origins=m["data_origins"] + ["raw:com.google.heart_rate.bpm:com.fitbit.FitbitMobile:health_platform"])
    assert _device_sources(both) == "Fitbit Air, Pixel phone sensors"  # no duplicate


def test_scheduler_prefers_cloud_and_merges(monkeypatch):
    from app.sync import scheduler
    from app.sync.connectors import fitbit as fb, google_health as gh
    from app.sync import pipeline

    monkeypatch.setattr(fb, "has_credentials", lambda cfg: True)
    monkeypatch.setattr(gh, "has_credentials", lambda cfg: True)
    monkeypatch.setattr(pipeline, "load_fitbit_daily", lambda cfg, d: _cloud())
    monkeypatch.setattr(pipeline, "load_fitbit_via_google_health", lambda cfg, d: _relay())
    written = {}
    monkeypatch.setattr(scheduler, "write_daily_json", lambda dd, day, payload, source="oura": written.setdefault(day, payload) or Path("x"))
    monkeypatch.setattr(scheduler, "_load_stored_daily", lambda *a, **k: None)
    monkeypatch.setattr(scheduler, "BACKFILL_DAYS", 0)
    cfg = types.SimpleNamespace(timezone=__import__("zoneinfo").ZoneInfo("Europe/Paris"), data_dir=Path("/nonexistent"))
    monkeypatch.setattr(scheduler, "load_config", lambda: cfg)

    scheduler.run_fitbit_sync()
    assert len(written) == 1
    payload = next(iter(written.values()))
    assert payload["hrv"] == 24.0 and payload["active_zone_minutes"] == 31
    assert payload["via"] == "fitbit_web_api+google_health"


def test_scheduler_relay_failure_does_not_sink_cloud(monkeypatch):
    from app.sync import scheduler
    from app.sync.connectors import fitbit as fb, google_health as gh
    from app.sync import pipeline

    monkeypatch.setattr(fb, "has_credentials", lambda cfg: True)
    monkeypatch.setattr(gh, "has_credentials", lambda cfg: True)
    monkeypatch.setattr(pipeline, "load_fitbit_daily", lambda cfg, d: _cloud())

    def boom(cfg, d):
        raise RuntimeError("relay down")

    monkeypatch.setattr(pipeline, "load_fitbit_via_google_health", boom)
    written = {}
    monkeypatch.setattr(scheduler, "write_daily_json", lambda dd, day, payload, source="oura": written.setdefault(day, payload) or Path("x"))
    monkeypatch.setattr(scheduler, "_load_stored_daily", lambda *a, **k: None)
    monkeypatch.setattr(scheduler, "BACKFILL_DAYS", 0)
    cfg = types.SimpleNamespace(timezone=__import__("zoneinfo").ZoneInfo("Europe/Paris"), data_dir=Path("/nonexistent"))
    monkeypatch.setattr(scheduler, "load_config", lambda: cfg)

    scheduler.run_fitbit_sync()
    assert next(iter(written.values()))["via"] == "fitbit_web_api"
