from __future__ import annotations

import json
import sys
import types
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sync import device_dashboard as dd  # noqa: E402


def _day(tmp, iso, steps, sleep_min, used_steps, source="fitbit", **extra):
    (tmp / f"fitbit_{iso}.json").write_text(json.dumps(dict(
        device_steps=steps, device_sleep_min=sleep_min, steps=used_steps,
        sleep_hours=round(sleep_min.get("fitbit_air", 0) / 60, 2), sleep_source=source, **extra)))


def _cfg(tmp):
    return types.SimpleNamespace(data_dir=tmp)


def test_agreement_uses_medians_so_one_partial_day_does_not_swing_it(tmp_path):
    for i, (air, peb) in enumerate([(12000, 12100), (14000, 14300), (11000, 10900), (8645, 19116)], start=1):
        _day(tmp_path, f"2026-11-0{i}", {"fitbit_air": air, "pebble": peb}, {"fitbit_air": 420, "pebble": 425}, air)
    data = dd.build(_cfg(tmp_path), date(2026, 11, 5), days=4)
    assert data["coverage"]["fitbit_air"] == 4 and data["coverage"]["oura"] == 0
    assert abs(data["steps_agreement"]["bias_pct"]) < 5     # mean would be ~+30%
    assert data["sleep_agreement"] == {"n": 4, "bias_min": 5, "typical_gap_min": 5}


def test_oura_night_appears_when_the_ring_is_worn(tmp_path):
    _day(tmp_path, "2026-11-04", {"fitbit_air": 9000}, {"fitbit_air": 400}, 9000)
    (tmp_path / "daily_2026-11-04.json").write_text(json.dumps({"sleep_hours": 6.9, "hrv": 24}))
    data = dd.build(_cfg(tmp_path), date(2026, 11, 5), days=1)
    assert data["rows"][0]["sleep"]["oura"] == 6.9 and data["coverage"]["oura"] == 1


def test_email_html_is_ascii_safe_and_marks_the_used_reading(tmp_path):
    _day(tmp_path, "2026-11-04", {"fitbit_air": 9000, "pebble": 9100, "phone": 8000},
         {"fitbit_air": 400, "pebble": 410}, 9000)
    html = dd.render_email_html(_cfg(tmp_path), date(2026, 11, 5), days=1)
    assert html and all(ord(ch) < 128 for ch in html)       # entities, no mojibake
    assert "&#9733;" in html and "Fitbit Air" in html and "Pebble" in html
    assert "&amp;plusmn;" not in html                        # not double-escaped


def test_phone_line_summarises_coverage_and_gap(tmp_path):
    _day(tmp_path, "2026-11-04", {"fitbit_air": 9000, "pebble": 9100}, {"fitbit_air": 400, "pebble": 410}, 9000)
    line = dd.render_phone_line(_cfg(tmp_path), date(2026, 11, 5), days=1)
    assert line.startswith("⌚ Devices") and "Oura idle" in line and "+10 min" in line


def test_nothing_rendered_without_data(tmp_path):
    assert dd.render_email_html(_cfg(tmp_path), date(2026, 11, 5)) == ""
    assert dd.render_phone_line(_cfg(tmp_path), date(2026, 11, 5)) == ""
