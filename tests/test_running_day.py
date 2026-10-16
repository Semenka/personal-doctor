"""The running day's step count is a partial total, never a trend data point."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sync.daily_advisor import _build_prompt  # noqa: E402
from app.sync.trend_analyzer import (  # noqa: E402
    compute_metric_trends,
    compute_rolling_averages,
    format_trend_section,
    settle_running_day,
)

TODAY = "2026-09-27"


def _history():
    """Six full ~10.7k-step days, then the 08:00 file for 2026-09-27."""
    days = [
        {"_date": f"2026-09-{d}", "steps": 10700, "sleep_hours": 7.5, "hrv": 21}
        for d in range(21, 27)
    ]
    days.append({"_date": TODAY, "steps": 520, "sleep_hours": 6.1, "hrv": 20})
    return days


def test_running_day_steps_do_not_drag_the_average_or_trend():
    raw = _history()
    # What 2026-09-27's plan saw: 520 counted as a day → "declining".
    assert compute_metric_trends(raw)["steps"] == "declining"

    settled = settle_running_day(raw, TODAY)
    assert compute_rolling_averages(settled)["avg_steps"] == 10700
    assert compute_metric_trends(settled)["steps"] == "stable"
    # Last night's sleep is complete and still counts.
    assert settled[-1]["sleep_hours"] == 6.1
    assert raw[-1]["steps"] == 520  # input untouched


def test_trend_line_labels_todays_steps_as_partial():
    settled = settle_running_day(_history(), TODAY)
    text = format_trend_section(
        compute_rolling_averages(settled), compute_metric_trends(settled),
        {"steps": 520, "sleep_hours": 6.1},
    )
    steps_line = next(line for line in text.splitlines() if "Steps" in line)
    assert "so far today 520" in steps_line
    assert "day in progress" in steps_line
    assert "declining" not in steps_line


def test_prompt_activity_block_says_so_far_and_gives_last_full_day():
    history = _history()
    prompt = _build_prompt({
        "date": TODAY,
        "fitbit": {"steps": 520, "sleep_hours": 6.1, "sleep_quality": 70, "hrv": 20},
        "fitbit_history": history,
        "rolling_averages": compute_rolling_averages(settle_running_day(history, TODAY)),
        "metric_trends": compute_metric_trends(settle_running_day(history, TODAY)),
    })
    assert "Activity (so far today" in prompt
    assert "Last complete day, 2026-09-26: 10700 steps" in prompt


def test_pebble_nights_do_not_feed_sleep_stage_averages(tmp_path):
    import json
    from datetime import date, timedelta

    from app.sync.trend_analyzer import compute_rolling_averages, load_primary_wearable_history

    day = date(2026, 10, 16)
    pebble_deep = [156, 96, 131, 50, 262]
    for i in range(7):
        d = (day - timedelta(days=i)).isoformat()
        if i < 2:
            p = {"sleep_hours": 7.0, "deep_sleep_min": 70, "rem_sleep_min": 95, "sleep_source": "fitbit"}
        else:
            p = {"sleep_hours": 6.5, "deep_sleep_min": pebble_deep[i - 2], "rem_sleep_min": 0,
                 "sleep_source": "coredevices.coreapp"}
        (tmp_path / f"fitbit_{d}.json").write_text(json.dumps(p))
    hist = load_primary_wearable_history(tmp_path, day, num_days=7)
    avgs = compute_rolling_averages(hist)
    assert avgs["avg_deep_sleep_min"] == 70
    assert avgs["avg_sleep_hours"] > 6.5  # totals still count; the caveat line labels them
