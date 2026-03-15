"""7-day trend analysis for Oura Ring metrics."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional


def load_oura_history(
    data_dir: Path, day: date, num_days: int = 7
) -> List[Dict[str, Any]]:
    """Load the last N days of Oura daily payloads from JSON files.

    Returns list sorted by date ascending (oldest first).
    """
    history = []
    for i in range(num_days):
        d = day - timedelta(days=i)
        path = data_dir / f"daily_{d.isoformat()}.json"
        if path.exists():
            try:
                payload = json.loads(path.read_text())
                payload["_date"] = d.isoformat()
                history.append(payload)
            except (json.JSONDecodeError, OSError):
                continue
    history.reverse()  # oldest first
    return history


_METRIC_KEYS = [
    ("hrv", "avg_hrv"),
    ("resting_hr", "avg_resting_hr"),
    ("sleep_hours", "avg_sleep_hours"),
    ("sleep_quality", "avg_sleep_quality"),
    ("deep_sleep_min", "avg_deep_sleep_min"),
    ("rem_sleep_min", "avg_rem_sleep_min"),
    ("steps", "avg_steps"),
    ("readiness_score", "avg_readiness"),
    ("active_minutes", "avg_active_minutes"),
]


def compute_rolling_averages(history: List[Dict[str, Any]]) -> Dict[str, float]:
    """Compute 7-day rolling averages for key metrics.

    Skips days where the metric is zero or missing.
    """
    averages: Dict[str, float] = {}
    for src_key, avg_key in _METRIC_KEYS:
        values = []
        for day_data in history:
            val = day_data.get(src_key)
            if val is not None and val != 0:
                try:
                    values.append(float(val))
                except (TypeError, ValueError):
                    continue
        if values:
            averages[avg_key] = sum(values) / len(values)
    return averages


def compute_metric_trends(history: List[Dict[str, Any]]) -> Dict[str, str]:
    """Compare recent 3 days vs previous 4 days for each metric.

    Returns dict: {"hrv": "improving", "sleep_quality": "declining", ...}
    For metrics where higher is better (hrv, sleep, steps, readiness):
      recent > old → improving
    For resting_hr: lower is better (inverted).
    """
    if len(history) < 4:
        return {}

    inverted = {"resting_hr"}  # lower is better
    trends: Dict[str, str] = {}

    for src_key, _ in _METRIC_KEYS:
        old_vals = []
        recent_vals = []
        split = max(len(history) - 3, 1)

        for day_data in history[:split]:
            val = day_data.get(src_key)
            if val is not None and val != 0:
                try:
                    old_vals.append(float(val))
                except (TypeError, ValueError):
                    continue

        for day_data in history[split:]:
            val = day_data.get(src_key)
            if val is not None and val != 0:
                try:
                    recent_vals.append(float(val))
                except (TypeError, ValueError):
                    continue

        if not old_vals or not recent_vals:
            continue

        old_avg = sum(old_vals) / len(old_vals)
        recent_avg = sum(recent_vals) / len(recent_vals)

        if old_avg == 0:
            trends[src_key] = "stable"
            continue

        pct_change = (recent_avg - old_avg) / abs(old_avg)

        if src_key in inverted:
            pct_change = -pct_change  # invert so lower HR = improving

        if pct_change > 0.05:
            trends[src_key] = "improving"
        elif pct_change < -0.05:
            trends[src_key] = "declining"
        else:
            trends[src_key] = "stable"

    return trends


def format_trend_section(
    averages: Dict[str, float],
    trends: Dict[str, str],
    today_data: Optional[Dict[str, Any]],
) -> str:
    """Format a markdown section for the AI prompt showing 7-day context."""
    if not averages:
        return ""

    lines = ["## 7-Day Metric Trends"]

    display = [
        ("hrv", "avg_hrv", "HRV", "ms"),
        ("resting_hr", "avg_resting_hr", "Resting HR", "bpm"),
        ("sleep_hours", "avg_sleep_hours", "Sleep", "hrs"),
        ("deep_sleep_min", "avg_deep_sleep_min", "Deep Sleep", "min"),
        ("rem_sleep_min", "avg_rem_sleep_min", "REM Sleep", "min"),
        ("steps", "avg_steps", "Steps", ""),
        ("readiness_score", "avg_readiness", "Readiness", "/100"),
        ("sleep_quality", "avg_sleep_quality", "Sleep Score", "/100"),
    ]

    for src_key, avg_key, label, unit in display:
        avg_val = averages.get(avg_key)
        if avg_val is None:
            continue
        today_val = today_data.get(src_key, "N/A") if today_data else "N/A"
        trend_dir = trends.get(src_key, "N/A")
        lines.append(
            f"- {label}: today {today_val} {unit}, "
            f"7-day avg {avg_val:.1f} {unit} ({trend_dir})"
        )

    return "\n".join(lines)
