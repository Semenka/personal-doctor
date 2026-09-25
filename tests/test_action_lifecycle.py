from __future__ import annotations

import json
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sync import auto_complete  # noqa: E402
from app.sync.action_lifecycle import action_state, enrich, parse_due_end  # noqa: E402
from app.sync.biomarker_dashboard import render_whatsapp_protocol  # noqa: E402
from app.sync.sheets_tracker import merge_sheet_status  # noqa: E402

TZ = timezone(timedelta(hours=2))


def test_parse_due_end_formats_the_advisor_uses():
    assert parse_due_end("12:45–12:47 PM") == "12:47"
    assert parse_due_end("9:30–9:42 AM") == "09:42"
    assert parse_due_end("13:00–13:05 PM, with lunch") == "13:05"
    assert parse_due_end("11:35–11:47 PM") == "23:47"
    assert parse_due_end("by 23:15") == "23:15"
    assert parse_due_end("With your largest meal of the day (dinner)") is None
    assert parse_due_end("2-3 capsules with 750 mL water") is None


def test_state_open_until_window_plus_grace_then_expired():
    a = {"done": False, "due_end": "13:05"}
    day = "2026-09-25"
    assert action_state(a, day, datetime(2026, 9, 25, 8, 0, tzinfo=TZ)) == "open"
    assert action_state(a, day, datetime(2026, 9, 25, 14, 0, tzinfo=TZ)) == "open"  # grace
    assert action_state(a, day, datetime(2026, 9, 25, 14, 10, tzinfo=TZ)) == "expired"
    assert action_state({"done": True, "due_end": "13:05"}, day,
                        datetime(2026, 9, 25, 22, 0, tzinfo=TZ)) == "done"


def test_state_untimed_and_past_days():
    now = datetime(2026, 9, 25, 23, 0, tzinfo=TZ)
    assert action_state({"done": False}, "2026-09-25", now) == "open"
    assert action_state({"done": False}, "2026-09-24", now) == "expired"


def test_state_after_midnight_window_belongs_to_the_evening():
    a = {"done": False, "due_end": "00:30"}  # "lights out by 00:30"
    assert action_state(a, "2026-09-25", datetime(2026, 9, 25, 23, 0, tzinfo=TZ)) == "open"


def test_enrich_reads_when_and_category_from_description():
    a = enrich({"title": "X", "description": " Easy | Category: Supplement\n   **When:** 13:00–13:05 PM\n"})
    assert a["due_end"] == "13:05" and a["category"] == "supplement"


def test_merge_sheet_status_keeps_local_fields_and_ors_done():
    local = [{"idx": 0, "title": "Walk", "description": "walk 12 min", "done": True, "source": "fitbit_auto"},
             {"idx": 1, "title": "CoQ10", "description": "take", "done": False, "due_end": "13:05"}]
    sheet = [{"idx": 0, "title": "Walk", "done": False, "done_at": None},
             {"idx": 1, "title": "CoQ10", "done": True, "done_at": "2026-09-25T11:00"}]
    m = merge_sheet_status(local, sheet)
    assert m[0]["done"] and m[0]["source"] == "fitbit_auto" and m[0]["description"]
    assert m[1]["done"] and m[1]["source"] == "sheet" and m[1]["due_end"] == "13:05"


def test_protocol_shows_upcoming_not_red():
    acts = [{"title": "Ubiquinol", "done": False, "due_end": "13:05"},
            {"title": "Walk", "done": True}]
    out = render_whatsapp_protocol(acts, day="2026-09-25",
                                   now=datetime(2026, 9, 25, 8, 0, tzinfo=TZ))
    assert "⏳ 1. Ubiquinol · by 13:05" in out and "🟢 2. Walk" in out and "🔴" not in out


def test_sleep_action_judged_by_the_following_night(tmp_path, monkeypatch):
    data = tmp_path
    (data / "actions").mkdir()
    day = "2026-09-23"
    (data / "actions" / f"actions_{day}.json").write_text(json.dumps({"date": day, "actions": [
        {"idx": 0, "title": "Sleep-Debt Recovery Block", "description": "lights out 23:30", "done": False}]}))
    # The action day's own file holds the PREVIOUS night (short); the next
    # day's file holds the night that follows the action (long enough).
    (data / f"fitbit_{day}.json").write_text(json.dumps({"steps": 9000, "sleep_hours": 5.0}))
    (data / "fitbit_2026-09-24.json").write_text(json.dumps({"steps": 100, "sleep_hours": 7.4}))
    marked = []
    monkeypatch.setattr("app.sync.action_tracker.mark_action_done_with_sheets",
                        lambda cfg, d, idx: marked.append((d, idx)) or True)
    cfg = types.SimpleNamespace(data_dir=data)
    out = auto_complete.auto_credit_actions(cfg, day)
    assert marked == [(day, 0)] and "7.4" in out["credited"][0]["evidence"]


def test_nap_is_not_graded_against_main_sleep():
    assert auto_complete._classify("Sleep-Debt Rescue Nap", "20 min nap after lunch") == "unsensable"


def test_declared_category_beats_keywords():
    # A Sleep block mentioning magnesium is still verifiable by the night's sleep.
    assert auto_complete._classify("Sleep-Debt Recovery Block", "take magnesium, lights out", "sleep") == "sleep"
    # Mislabelled "Sleep" actions the watch can't verify stay unrecorded, not failed.
    assert auto_complete._classify("Sleep-Debt Rescue Nap", "25-minute nap", "sleep") == "unsensable"
    assert auto_complete._classify("No-Heat Fertility Lock", "sleep without underwear", "sleep") == "unsensable"
    assert auto_complete._classify("Ubiquinol With Fat", "take with lunch", "supplement") == "unsensable"
    # No/unknown category → keyword fallback as before
    assert auto_complete._classify("Evening walk", "20 min walk", "") == "movement"


def test_display_kind_separates_missed_from_unrecorded():
    from app.sync.action_lifecycle import display_kind

    now = datetime(2026, 9, 25, 20, 0, tzinfo=TZ)
    walk = {"title": "Evening walk", "description": "20 min walk", "category": "movement", "done": False}
    pill = {"title": "Ubiquinol", "description": "take with lunch", "category": "supplement", "done": False}
    assert display_kind(walk, "2026-09-24", now) == "missed"
    assert display_kind(pill, "2026-09-24", now) == "unrecorded"
    assert display_kind(dict(pill, due_end="21:30"), "2026-09-25", now) == "open"
