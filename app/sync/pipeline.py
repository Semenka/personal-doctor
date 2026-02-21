from __future__ import annotations

from datetime import date
from typing import Any, Dict

from .config import SyncConfig
from .connectors.oura import fetch_daily_summary


def oura_to_daily_payload(day: date, summary: Dict[str, Any]) -> Dict[str, Any]:
    """Transform Oura API v2 response into the daily payload format.

    Data sources:
    - sleep_period (from /sleep endpoint): duration, HR, HRV — the detailed metrics
    - daily_sleep (from /daily_sleep endpoint): sleep score
    - daily_activity (from /daily_activity endpoint): steps, calories, active time
    - daily_readiness (from /daily_readiness endpoint): readiness score, temperature
    """
    daily_sleep = summary.get("daily_sleep", {})
    daily_activity = summary.get("daily_activity", {})
    daily_readiness = summary.get("daily_readiness", {})
    sleep_period = summary.get("sleep_period", {})

    # Sleep metrics from detailed sleep period
    deep = sleep_period.get("deep_sleep_duration") or 0
    light = sleep_period.get("light_sleep_duration") or 0
    rem = sleep_period.get("rem_sleep_duration") or 0
    total_sleep_seconds = deep + light + rem

    resting_hr = sleep_period.get("lowest_heart_rate") or 0
    hrv = sleep_period.get("average_hrv") or 0
    avg_hr = sleep_period.get("average_heart_rate") or 0
    avg_breath = sleep_period.get("average_breath") or 0
    efficiency = sleep_period.get("efficiency") or 0

    # Scores from daily summaries
    sleep_score = daily_sleep.get("score") or 0
    readiness_score = daily_readiness.get("score") or 0
    temp_deviation = daily_readiness.get("temperature_deviation") or 0

    # Activity from daily_activity (may be yesterday's if today not yet available)
    steps = daily_activity.get("steps") or 0
    active_calories = daily_activity.get("active_calories") or 0
    total_calories = daily_activity.get("total_calories") or 0
    # Convert medium + high activity time from seconds to minutes
    medium_time = daily_activity.get("medium_activity_time") or 0
    high_time = daily_activity.get("high_activity_time") or 0
    active_minutes = round((medium_time + high_time) / 60)
    activity_score = daily_activity.get("score") or 0
    sedentary_time = daily_activity.get("sedentary_time") or 0

    return {
        "date": day.isoformat(),
        "sleep_hours": round(total_sleep_seconds / 3600, 2),
        "sleep_quality": int(sleep_score or readiness_score),
        "readiness_score": int(readiness_score),
        "activity_score": int(activity_score),
        "steps": int(steps),
        "active_minutes": int(active_minutes),
        "resting_hr": int(resting_hr),
        "avg_hr": round(float(avg_hr), 1),
        "hrv": round(float(hrv), 1),
        "avg_breath": round(float(avg_breath), 1),
        "efficiency": int(efficiency),
        "temp_deviation": round(float(temp_deviation), 2),
        "calories": int(total_calories),
        "active_calories": int(active_calories),
        "sitting_hours": round(sedentary_time / 3600, 1),
        "deep_sleep_min": round(deep / 60),
        "rem_sleep_min": round(rem / 60),
        "light_sleep_min": round(light / 60),
        "protein_g": 0,
        "carbs_g": 0,
        "fat_g": 0,
        "water_l": 0,
        "mood": 0,
        "stress": 0,
        "source": "oura",
        "activity_is_previous_day": summary.get("activity_is_previous", False),
    }


def load_oura_daily(config: SyncConfig, day: date) -> Dict[str, Any]:
    summary = fetch_daily_summary(config, day)
    return oura_to_daily_payload(day, summary)
