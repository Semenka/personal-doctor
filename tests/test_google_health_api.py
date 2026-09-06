"""Google Health API connector: mapping, provenance, and sync preference."""
from __future__ import annotations

import sys
import types
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sync.connectors import google_health_api as api  # noqa: E402
from app.sync.pipeline import (  # noqa: E402
    google_health_api_to_daily_payload,
    payload_devices,
)


def _fitbit_point(payload_key, payload, platform="FITBIT"):
    return {payload_key: payload,
            "dataSource": {"platform": platform, "recordingMethod": "PASSIVELY_MEASURED",
                           "device": {"manufacturer": "Fitbit", "displayName": "Fitbit Air",
                                      "formFactor": "FITNESS_BAND"}}}


def _pebble_point(payload_key, payload):
    return {payload_key: payload,
            "dataSource": {"platform": "HEALTH_CONNECT", "recordingMethod": "PASSIVELY_MEASURED",
                           "application": {"packageName": "coredevices.coreapp"}}}


def test_origin_strings_name_the_device():
    assert api.origin_of(_fitbit_point("steps", {})) == \
        "google_health_api:FITBIT:PASSIVELY_MEASURED:Fitbit:Fitbit Air:FITNESS_BAND"
    assert api.origin_of(_pebble_point("steps", {})) == \
        "google_health_api:HEALTH_CONNECT:PASSIVELY_MEASURED:coredevices.coreapp"
    assert api.origin_of({}) == ""


def test_fetch_daily_summary_maps_every_block(monkeypatch):
    day = date(2026, 9, 6)
    calls = []

    def fake_list(sess, data_type, filt, page_size=1000, max_pages=10):
        calls.append((data_type, filt))
        if data_type == "steps":
            return [_fitbit_point("steps", {"count": "6000"}), _pebble_point("steps", {"count": "1500"})]
        if data_type == "sleep":
            return [
                _fitbit_point("sleep", {"metadata": {"mainSleep": False},
                                        "summary": {"minutesAsleep": "40", "minutesInSleepPeriod": "45"}}),
                _fitbit_point("sleep", {
                    "metadata": {"mainSleep": True},
                    "interval": {"startTime": "2026-09-05T22:10:00Z", "endTime": "2026-09-06T05:40:00Z"},
                    "summary": {"minutesAsleep": "402", "minutesAwake": "48", "minutesInSleepPeriod": "450",
                                "stagesSummary": [{"type": "DEEP", "minutes": "70"}, {"type": "REM", "minutes": "95"},
                                                  {"type": "LIGHT", "minutes": "237"}, {"type": "AWAKE", "minutes": "48"}]},
                }),
            ]
        if data_type == "daily-resting-heart-rate":
            return [_fitbit_point("dailyRestingHeartRate", {"beatsPerMinute": "57"})]
        if data_type == "daily-heart-rate-variability":
            return [_fitbit_point("dailyHeartRateVariability", {"averageHeartRateVariabilityMilliseconds": 31.4})]
        if data_type == "daily-oxygen-saturation":
            return [_fitbit_point("dailyOxygenSaturation", {"averagePercentage": 96.2})]
        if data_type == "daily-respiratory-rate":
            return [_fitbit_point("dailyRespiratoryRate", {"breathsPerMinute": 14.6})]
        return []

    def fake_rollup(sess, data_type, d):
        calls.append((data_type, "rollup"))
        return {
            "active-minutes": {"activeMinutes": {"activeMinutesRollupByActivityLevel": [
                {"activityLevel": "LIGHT", "activeMinutesSum": "200"},
                {"activityLevel": "MODERATE", "activeMinutesSum": "25"},
                {"activityLevel": "VIGOROUS", "activeMinutesSum": "10"}]}},
            "active-zone-minutes": {"activeZoneMinutes": {"sumInFatBurnHeartZone": "20", "sumInCardioHeartZone": "8"}},
            "heart-rate": {"heartRate": {"beatsPerMinuteAvg": 71.5}},
        }.get(data_type, {})

    monkeypatch.setattr(api, "_session", lambda cfg: object())
    monkeypatch.setattr(api, "_list", fake_list)
    monkeypatch.setattr(api, "_daily_rollup", fake_rollup)
    s = api.fetch_daily_summary(types.SimpleNamespace(), day)
    assert s["steps"] == 7500 and s["active_minutes"] == 35 and s["active_zone_minutes"] == 28
    assert s["sleep_minutes"] == 402 and s["deep_min"] == 70 and s["rem_min"] == 95
    assert s["resting_hr"] == 57 and s["hrv"] == 31.4 and s["spo2"] == 96.2 and s["breathing_rate"] == 14.6
    assert s["avg_hr"] == 71.5 and s["errors"] == []
    assert any("coredevices.coreapp" in o for o in s["data_origins"])
    assert any("Fitbit Air" in o for o in s["data_origins"])
    # date filters use the documented field patterns
    filt = dict((dt, f) for dt, f in calls if f != "rollup")
    assert filt["sleep"] == 'sleep.interval.civil_end_time >= "2026-09-06" AND sleep.interval.civil_end_time < "2026-09-07"'
    assert filt["daily-heart-rate-variability"].startswith('daily_heart_rate_variability.date >= "2026-09-06"')

    p = google_health_api_to_daily_payload(day, s)
    assert p["sleep_hours"] == 6.7 and p["efficiency"] == 89 and p["via"] == "google_health_api"
    assert p["hrv"] == 31.4 and p["resting_hr"] == 57 and p["avg_breath"] == 14.6
    assert p["source"] == "fitbit" and p["active_zone_minutes"] == 28
    assert payload_devices(p) == {"fitbit", "pebble"}


def test_one_failing_block_does_not_sink_the_day(monkeypatch):
    def boom_list(sess, data_type, filt, page_size=1000, max_pages=10):
        if data_type == "sleep":
            raise api.ApiError("list sleep: HTTP 403 scope")
        if data_type == "steps":
            return [_fitbit_point("steps", {"count": "100"})]
        return []

    monkeypatch.setattr(api, "_session", lambda cfg: object())
    monkeypatch.setattr(api, "_list", boom_list)
    monkeypatch.setattr(api, "_daily_rollup", lambda s, dt, d: {})
    s = api.fetch_daily_summary(types.SimpleNamespace(), date(2026, 9, 6))
    assert s["steps"] == 100 and s["sleep_minutes"] == 0
    assert any(e.startswith("sleep:") for e in s["errors"])


def test_scheduler_prefers_google_health_api_and_folds_the_rest(monkeypatch):
    from app.sync import pipeline, scheduler
    from app.sync.connectors import fitbit as fb, google_health as gh, google_health_api as gapi

    monkeypatch.setattr(gapi, "has_credentials", lambda cfg: True)
    monkeypatch.setattr(fb, "has_credentials", lambda cfg: False)
    monkeypatch.setattr(gh, "has_credentials", lambda cfg: True)
    cloud = {"date": "2026-09-06", "source": "fitbit", "via": "google_health_api", "steps": 8000,
             "sleep_hours": 6.7, "hrv": 31.4, "resting_hr": 57, "active_zone_minutes": 0,
             "data_origins": ["google_health_api:FITBIT:Fitbit Air"], "activity_is_previous_day": False}
    relay = {"date": "2026-09-06", "source": "fitbit", "via": "google_health", "steps": 12, "sleep_hours": 0.0,
             "hrv": 0.0, "resting_hr": 0, "active_zone_minutes": 31,
             "data_origins": ["raw:com.google.step_count.cumulative:Google:Pixel 10 Pro:x:Step Counter"],
             "activity_is_previous_day": False}
    monkeypatch.setattr(pipeline, "load_fitbit_via_google_health_api", lambda cfg, d: cloud)
    monkeypatch.setattr(pipeline, "load_fitbit_via_google_health", lambda cfg, d: relay)
    written = {}
    monkeypatch.setattr(scheduler, "write_daily_json", lambda dd, day, payload, source="oura": written.setdefault(day, payload) or Path("x"))
    monkeypatch.setattr(scheduler, "_load_stored_daily", lambda *a, **k: None)
    monkeypatch.setattr(scheduler, "BACKFILL_DAYS", 0)
    cfg = types.SimpleNamespace(timezone=__import__("zoneinfo").ZoneInfo("Europe/Paris"), data_dir=Path("/nonexistent"))
    monkeypatch.setattr(scheduler, "load_config", lambda: cfg)
    scheduler.run_fitbit_sync()
    payload = next(iter(written.values()))
    assert payload["steps"] == 8000 and payload["hrv"] == 31.4 and payload["active_zone_minutes"] == 31
    assert payload["via"] == "google_health_api+google_health"
    assert len(payload["data_origins"]) == 2
