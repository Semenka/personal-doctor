"""When the Fitbit Air is silent the pipeline must say what it is really using."""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sync import device_compare, pipeline  # noqa: E402
from app.sync.trend_analyzer import format_trend_section  # noqa: E402


def _cfg(tmp_path):
    return types.SimpleNamespace(data_dir=tmp_path, timezone=ZoneInfo("Europe/Rome"),
                                 server_url="http://x", gdrive_credentials_dir=None)


def test_advisor_block_flags_a_pebble_night_and_drops_fake_efficiency(tmp_path):
    (tmp_path / "fitbit_2026-10-09.json").write_text(json.dumps({
        "sleep_hours": 7.62, "efficiency": 100, "steps": 11429, "sleep_source": "coredevices",
        "sleep_start": "2026-10-08T22:14:00Z", "sleep_end": "2026-10-09T05:51:00Z"}))
    block = device_compare.render_compare_advisor_block(_cfg(tmp_path), "2026-10-09")
    assert "SLEEP SOURCE CAVEAT" in block and "Pebble" in block
    assert "Sleep efficiency" not in block


def test_air_night_has_no_caveat(tmp_path):
    (tmp_path / "fitbit_2026-10-04.json").write_text(json.dumps({
        "sleep_hours": 7.0, "efficiency": 96, "steps": 15826, "sleep_source": "fitbit", "hrv": 24.5}))
    block = device_compare.render_compare_advisor_block(_cfg(tmp_path), "2026-10-04")
    assert "CAVEAT" not in block and "Sleep efficiency: 96%" in block


def test_trend_section_warns_about_mixed_instruments():
    hist = [{"sleep_hours": 7.0, "sleep_source": "fitbit"}] * 2 + \
           [{"sleep_hours": 7.6, "sleep_source": "coredevices"}] * 5
    out = format_trend_section({"avg_sleep_hours": 7.4}, {"sleep_hours": "improving"},
                               {"sleep_hours": 7.6}, history=hist)
    assert "5 of the last 7 nights are Pebble estimates" in out
    assert "Sleep caveat" not in format_trend_section(
        {"avg_sleep_hours": 7.0}, {}, {"sleep_hours": 7.0}, history=hist[:2])


def test_silent_air_with_linked_cloud_gets_the_device_fix_not_the_link_fix(tmp_path, monkeypatch):
    silence = {"silent_days": 0, "devices": {
        "fitbit": {"label": "Fitbit Air", "silent_days": 5, "last_date": "2026-10-05"},
        "pebble": {"label": "Pebble", "silent_days": 0, "last_date": "2026-10-10"}}}
    monkeypatch.setattr("app.sync.connectors.google_health_api.has_credentials", lambda cfg: True)
    linked = pipeline.watch_banner(_cfg(tmp_path), silence)
    assert "cloud link is fine" in linked and "/auth/google-health" not in linked
    monkeypatch.setattr("app.sync.connectors.google_health_api.has_credentials", lambda cfg: False)
    assert "/auth/google-health" in pipeline.watch_banner(_cfg(tmp_path), silence)
