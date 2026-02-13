from __future__ import annotations

from datetime import date
from typing import Any, Dict

from .config import SyncConfig
from .connectors.oura import fetch_daily_summary


def oura_to_daily_payload(day: date, summary: Dict[str, Any]) -> Dict[str, Any]:
    sleep = summary.get("sleep", {})
    activity = summary.get("activity", {})
    readiness = summary.get("readiness", {})

    sleep_seconds = sleep.get("total_sleep_duration") or 0
    sleep_score = sleep.get("score") or 0
    readiness_score = readiness.get("score") or 0
    activity_steps = activity.get("steps") or 0
    active_minutes = activity.get("active_calories") or 0
    resting_hr = sleep.get("lowest_heart_rate") or 0
    hrv = sleep.get("average_hrv") or 0

    return {
        "date": day.isoformat(),
        "sleep_hours": round(sleep_seconds / 3600, 2),
        "sleep_quality": int(sleep_score or readiness_score),
        "steps": int(activity_steps),
        "active_minutes": int(active_minutes),
        "resting_hr": int(resting_hr),
        "hrv": float(hrv),
        "calories": 0,
        "protein_g": 0,
        "carbs_g": 0,
        "fat_g": 0,
        "water_l": 0,
        "sitting_hours": 0,
        "mood": 0,
        "stress": 0,
        "source": "oura",
    }


def load_oura_daily(config: SyncConfig, day: date) -> Dict[str, Any]:
    summary = fetch_daily_summary(config, day)
    return oura_to_daily_payload(day, summary)
