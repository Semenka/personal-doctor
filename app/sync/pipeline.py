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
        "spo2": 0,  # Oura doesn't expose SpO2 in the daily payload; Fitbit does.
        # Fitbit-only metrics — kept at 0 in the Oura payload for schema parity.
        "active_zone_minutes": 0,
        "vo2max": 0.0,
        "distance_km": 0.0,
        "floors": 0,
        "source": "oura",
        "activity_is_previous_day": summary.get("activity_is_previous", False),
    }


def load_oura_daily(config: SyncConfig, day: date) -> Dict[str, Any]:
    summary = fetch_daily_summary(config, day)
    return oura_to_daily_payload(day, summary)


def fitbit_to_daily_payload(day: date, summary: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize the Fitbit Web API response into the shared 30-key schema.

    Same keys as oura_to_daily_payload so dashboards/consumers can read it the
    same way, with source="fitbit". Fields Fitbit doesn't provide map to 0.
    Adds a real spo2 value (Fitbit-only).
    """
    activities = (summary.get("activities") or {}).get("summary", {}) or {}
    sleep_doc = summary.get("sleep") or {}
    heart_doc = summary.get("heart") or {}
    hrv_doc = summary.get("hrv") or {}
    br_doc = summary.get("br") or {}
    spo2_doc = summary.get("spo2") or {}
    azm_doc = summary.get("azm") or {}
    temp_doc = summary.get("temp") or {}
    cardio_doc = summary.get("cardioscore") or {}

    # ── Sleep ──
    # Fitbit /1.2 sleep: top-level "summary" has stage totals; main sleep log
    # has minutesAsleep + efficiency.
    sleep_summary = sleep_doc.get("summary", {}) or {}
    stages = sleep_summary.get("stages", {}) or {}
    deep_min = stages.get("deep") or 0
    light_min = stages.get("light") or 0
    rem_min = stages.get("rem") or 0
    total_asleep_min = sleep_summary.get("totalMinutesAsleep") or 0
    # Find the main sleep log for efficiency
    efficiency = 0
    for s in sleep_doc.get("sleep", []) or []:
        if s.get("isMainSleep"):
            efficiency = s.get("efficiency") or 0
            if not total_asleep_min:
                total_asleep_min = s.get("minutesAsleep") or 0
            break

    # ── Heart ──
    resting_hr = 0
    heart_list = heart_doc.get("activities-heart", []) or []
    if heart_list:
        resting_hr = (heart_list[0].get("value", {}) or {}).get("restingHeartRate") or 0

    # ── HRV (daily RMSSD) ──
    hrv_val = 0.0
    hrv_list = hrv_doc.get("hrv", []) or []
    if hrv_list:
        hrv_val = (hrv_list[0].get("value", {}) or {}).get("dailyRmssd") or 0.0

    # ── Breathing rate ──
    avg_breath = 0.0
    br_list = br_doc.get("br", []) or []
    if br_list:
        avg_breath = (br_list[0].get("value", {}) or {}).get("breathingRate") or 0.0

    # ── SpO2 (daily average) ──
    spo2_val = 0.0
    if isinstance(spo2_doc, dict):
        spo2_val = (spo2_doc.get("value", {}) or {}).get("avg") or 0.0
    elif isinstance(spo2_doc, list) and spo2_doc:
        spo2_val = (spo2_doc[0].get("value", {}) or {}).get("avg") or 0.0

    # ── Activity ──
    steps = activities.get("steps") or 0
    active_minutes = (activities.get("fairlyActiveMinutes") or 0) + (
        activities.get("veryActiveMinutes") or 0
    )
    calories = activities.get("caloriesOut") or 0
    active_calories = activities.get("activityCalories") or 0
    sedentary_min = activities.get("sedentaryMinutes") or 0
    floors = activities.get("floors") or 0
    # Total distance (sum the "total" distance entry if present)
    distance_km = 0.0
    for dist in activities.get("distances", []) or []:
        if dist.get("activity") == "total":
            distance_km = dist.get("distance") or 0.0
            break

    # ── Active Zone Minutes (Fitbit's headline cardio metric) ──
    azm_total = 0
    azm_list = azm_doc.get("activities-active-zone-minutes", []) or []
    if azm_list:
        azm_total = (azm_list[0].get("value", {}) or {}).get("activeZoneMinutes") or 0

    # ── Skin temperature nightly variation (comparable to Oura temp_deviation) ──
    skin_temp_dev = 0.0
    temp_list = temp_doc.get("tempSkin", []) or []
    if temp_list:
        skin_temp_dev = (temp_list[0].get("value", {}) or {}).get("nightlyRelative") or 0.0

    # ── VO2max / cardio fitness score ──
    vo2max = 0.0
    cardio_list = cardio_doc.get("cardioScore", []) or []
    if cardio_list:
        vo2 = (cardio_list[0].get("value", {}) or {}).get("vo2Max")
        if isinstance(vo2, str):
            # Fitbit sometimes returns a range like "42-46"; take the midpoint.
            parts = [float(x) for x in vo2.replace("–", "-").split("-") if x.strip().replace(".", "").isdigit()]
            vo2max = round(sum(parts) / len(parts), 1) if parts else 0.0
        elif vo2 is not None:
            vo2max = float(vo2)

    return {
        "date": day.isoformat(),
        "sleep_hours": round(total_asleep_min / 60, 2),
        "sleep_quality": int(efficiency),  # efficiency as a sleep-quality proxy
        "readiness_score": 0,  # Fitbit Daily Readiness is Premium-gated, not in std API
        "activity_score": 0,
        "steps": int(steps),
        "active_minutes": int(active_minutes),
        "resting_hr": int(resting_hr),
        "avg_hr": 0.0,
        "hrv": round(float(hrv_val), 1),
        "avg_breath": round(float(avg_breath), 1),
        "efficiency": int(efficiency),
        "temp_deviation": 0.0,
        "calories": int(calories),
        "active_calories": int(active_calories),
        "sitting_hours": round(sedentary_min / 60, 1),
        "deep_sleep_min": int(deep_min),
        "rem_sleep_min": int(rem_min),
        "light_sleep_min": int(light_min),
        "protein_g": 0,
        "carbs_g": 0,
        "fat_g": 0,
        "water_l": 0,
        "mood": 0,
        "stress": 0,
        "spo2": round(float(spo2_val), 1),
        "temp_deviation": round(float(skin_temp_dev), 2),
        "active_zone_minutes": int(azm_total),
        "vo2max": float(vo2max),
        "distance_km": round(float(distance_km), 2),
        "floors": int(floors),
        "source": "fitbit",
        "activity_is_previous_day": False,
    }


def load_fitbit_daily(config: SyncConfig, day: date) -> Dict[str, Any]:
    from .connectors.fitbit import fetch_daily_summary as fitbit_fetch

    summary = fitbit_fetch(config, day)
    return fitbit_to_daily_payload(day, summary)


def google_health_to_daily_payload(day: date, gh: Dict[str, Any]) -> Dict[str, Any]:
    """Map the Google Health (Fitness API) summary onto the standard schema.

    The bracelet is still a Fitbit — only the transport changed (Fitbit's dev
    portal closed to new apps; data now flows via Google). So ``source`` stays
    "fitbit" and the file stays fitbit_<date>.json, keeping every dashboard
    and the device-comparison layer working unchanged; ``via`` records the
    actual transport. Google's Heart Points map to active_zone_minutes (same
    concept: minutes in elevated heart-rate zones). HRV / VO2max / floors /
    breathing aren't exposed by the public Fitness API → 0 (Oura is the
    ★-weighted source for HRV regardless).
    """
    total_sleep_min = gh.get("sleep_total_min") or 0
    return {
        "date": day.isoformat(),
        "sleep_hours": round(total_sleep_min / 60, 2),
        "sleep_quality": 0,
        "readiness_score": 0,
        "activity_score": 0,
        "steps": int(gh.get("steps") or 0),
        "active_minutes": int(gh.get("active_minutes") or 0),
        "resting_hr": int(gh.get("resting_hr") or 0),
        "avg_hr": 0.0,
        "hrv": 0.0,
        "avg_breath": 0.0,
        "efficiency": 0,
        "temp_deviation": round(float(gh.get("body_temp") or 0), 2),
        "calories": int(gh.get("calories") or 0),
        "active_calories": 0,
        "sitting_hours": 0.0,
        "deep_sleep_min": int(gh.get("sleep_deep_min") or 0),
        "rem_sleep_min": int(gh.get("sleep_rem_min") or 0),
        "light_sleep_min": int(gh.get("sleep_light_min") or 0),
        "protein_g": 0,
        "carbs_g": 0,
        "fat_g": 0,
        "water_l": 0,
        "mood": 0,
        "stress": 0,
        "spo2": round(float(gh.get("spo2") or 0), 1),
        "active_zone_minutes": int(gh.get("heart_points") or 0),
        "vo2max": 0.0,
        "distance_km": round(float(gh.get("distance_km") or 0), 2),
        "floors": 0,
        "source": "fitbit",
        "via": "google_health",
        "activity_is_previous_day": False,
    }


def load_fitbit_via_google_health(config: SyncConfig, day: date) -> Dict[str, Any]:
    from .connectors.google_health import fetch_daily_summary as gh_fetch

    return google_health_to_daily_payload(day, gh_fetch(config, day))


def fitbit_data_is_fresh(payload: Dict[str, Any]) -> bool:
    """True if the Fitbit payload has real data (≥2 of the core signals non-zero)."""
    signals = [
        (payload.get("steps") or 0) > 0,
        (payload.get("sleep_hours") or 0) > 0,
        (payload.get("resting_hr") or 0) > 0,
        (payload.get("hrv") or 0) > 0,
    ]
    return sum(signals) >= 2


def oura_data_is_fresh(payload: Dict[str, Any]) -> bool:
    """Return True if the Oura payload contains real (non-zero) sleep/recovery data.

    Historically, HRV/sleep/readiness dropping to 0 for days in a row means the
    ring hasn't synced (dead battery, not charging, Oura app not opened, etc.).
    Advisor output based on zero metrics is misleading — we should warn instead.
    """
    # Primary signals that should always have a value if the ring synced
    sleep_hours = payload.get("sleep_hours", 0) or 0
    hrv = payload.get("hrv", 0) or 0
    readiness = payload.get("readiness_score", 0) or 0
    resting_hr = payload.get("resting_hr", 0) or 0
    sleep_quality = payload.get("sleep_quality", 0) or 0

    # If at least 2 of these 5 signals are non-zero, the ring synced something.
    signals = [sleep_hours > 0, hrv > 0, readiness > 0, resting_hr > 0, sleep_quality > 0]
    return sum(signals) >= 2


def check_oura_freshness(
    config: SyncConfig, day: date, max_stale_days: int = 3
) -> Dict[str, Any]:
    """Check if Oura data has been fresh in the last N days.

    Returns {"fresh": bool, "stale_days": int, "last_fresh_date": str|None}.
    """
    from datetime import timedelta

    from .storage import load_daily_payload

    stale_days = 0
    last_fresh: str | None = None
    for i in range(max_stale_days + 1):
        d = (day - timedelta(days=i)).isoformat()
        try:
            payload = load_daily_payload(config, d)
        except FileNotFoundError:
            stale_days += 1
            continue
        if oura_data_is_fresh(payload):
            last_fresh = d
            break
        stale_days += 1

    return {
        "fresh": last_fresh is not None and stale_days == 0,
        "stale_days": stale_days,
        "last_fresh_date": last_fresh,
    }
