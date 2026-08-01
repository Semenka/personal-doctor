"""Correlate completed actions with next-day metric changes.

Compares Oura metrics on days after an action was completed vs. days after
it was skipped. Requires at least 2 data points per group to report.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List

from .config import SyncConfig

# Metrics where higher = better (resting_hr is inverted)
_METRICS = [
    # Recovery metrics. The Fitbit Air currently reports none of these — the
    # Health Connect → Google Fit bridge relays activity only — so they stay
    # here inert, ready for whenever a recovery source is reconnected, and they
    # still resolve against historical Oura days.
    ("hrv", "HRV", "ms", False),
    ("resting_hr", "Resting HR", "bpm", True),  # lower is better
    ("sleep_quality", "Sleep Score", "", False),
    ("readiness_score", "Readiness", "", False),
    ("deep_sleep_min", "Deep Sleep", "min", False),
    # Activity metrics the Fitbit Air does deliver. Without these the whole
    # outcomes engine has nothing to measure and renders empty every day.
    ("steps", "Steps", "", False),
    ("active_minutes", "Active Minutes", "min", False),
]

MIN_SAMPLES = 2


def _load_wearable_day(data_dir: Path, day: str) -> Dict[str, Any] | None:
    """Next-day wearable payload: Fitbit Air first, historical Oura as fallback.

    Reading only ``daily_<date>.json`` silently broke this module after the
    Fitbit migration — those files are now all-zero Oura stubs, every value hits
    the ``val == 0`` guard, and no effect is ever computed. Preferring the
    Fitbit payload while still falling back to Oura keeps historical outcomes
    intact across the device switch.
    """
    for name in (f"fitbit_{day}.json", f"daily_{day}.json"):
        path = data_dir / name
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if payload:
            return payload
    return None


def _load_actions_day(data_dir: Path, day: str) -> List[Dict[str, Any]]:
    path = data_dir / "actions" / f"actions_{day}.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        return data.get("actions", [])
    except (json.JSONDecodeError, OSError):
        return []


def compute_action_effects(
    data_dir: Path,
    day: date,
    lookback_days: int = 14,
) -> List[Dict[str, Any]]:
    """Compute correlations between completed actions and next-day metrics.

    For each action title seen in the lookback window:
    - Collect next-day metric values when action was DONE
    - Collect next-day metric values when action was NOT DONE
    - If both groups have >= MIN_SAMPLES, compute the difference

    Returns a list of effects sorted by absolute impact, e.g.:
    [{"action": "Box breathing", "metric": "HRV", "done_avg": 45.2,
      "skip_avg": 38.1, "delta": "+7.1 ms", "days_done": 4, "days_skipped": 3}]
    """
    # Collect (action_title, day, done) tuples
    action_days: Dict[str, List[tuple]] = defaultdict(list)

    for i in range(1, lookback_days + 1):
        d = day - timedelta(days=i)
        actions = _load_actions_day(data_dir, d.isoformat())
        for a in actions:
            title = a.get("title", "").strip()
            if title:
                action_days[title].append((d, a.get("done", False)))

    if not action_days:
        return []

    effects = []

    for title, day_records in action_days.items():
        for metric_key, metric_label, unit, inverted in _METRICS:
            done_vals = []
            skip_vals = []

            for action_date, was_done in day_records:
                # Look at next-day metrics
                next_day = action_date + timedelta(days=1)
                wearable = _load_wearable_day(data_dir, next_day.isoformat())
                if not wearable:
                    continue
                val = wearable.get(metric_key)
                if val is None or val == 0:
                    continue
                try:
                    val = float(val)
                except (TypeError, ValueError):
                    continue

                if was_done:
                    done_vals.append(val)
                else:
                    skip_vals.append(val)

            if len(done_vals) < MIN_SAMPLES or len(skip_vals) < MIN_SAMPLES:
                continue

            done_avg = sum(done_vals) / len(done_vals)
            skip_avg = sum(skip_vals) / len(skip_vals)
            raw_delta = done_avg - skip_avg

            # For inverted metrics (resting HR), negative delta = good
            impact = -raw_delta if inverted else raw_delta
            sign = "+" if raw_delta > 0 else ""

            effects.append({
                "action": title,
                "metric": metric_label,
                "unit": unit,
                "done_avg": round(done_avg, 1),
                "skip_avg": round(skip_avg, 1),
                "delta": f"{sign}{raw_delta:.1f} {unit}".strip(),
                "impact": round(impact, 1),  # positive = beneficial
                "days_done": len(done_vals),
                "days_skipped": len(skip_vals),
            })

    # Sort by absolute impact, largest first
    effects.sort(key=lambda e: abs(e["impact"]), reverse=True)
    return effects
