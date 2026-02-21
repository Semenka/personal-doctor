"""Generate a daily Oura analytics report and optionally upload it to Google Drive."""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict

from .config import SyncConfig
from .pipeline import load_oura_daily


def _build_analytics(day: date, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Build a human-readable analytics summary from an Oura daily payload."""
    sleep_hours = payload.get("sleep_hours", 0)
    sleep_quality = payload.get("sleep_quality", 0)
    readiness_score = payload.get("readiness_score", 0)
    steps = payload.get("steps", 0)
    hrv = payload.get("hrv", 0)
    resting_hr = payload.get("resting_hr", 0)
    active_minutes = payload.get("active_minutes", 0)
    deep_sleep_min = payload.get("deep_sleep_min", 0)
    rem_sleep_min = payload.get("rem_sleep_min", 0)
    efficiency = payload.get("efficiency", 0)
    temp_deviation = payload.get("temp_deviation", 0)

    # Sleep assessment (sleep_quality is 0-100 from Oura)
    if sleep_hours >= 7.5 and sleep_quality >= 75:
        sleep_assessment = "Excellent"
    elif sleep_hours >= 6.5 or sleep_quality >= 60:
        sleep_assessment = "Adequate"
    else:
        sleep_assessment = "Poor — prioritize recovery today"

    # Recovery assessment
    if hrv >= 65 and resting_hr <= 62:
        recovery_assessment = "Fully recovered"
    elif hrv < 45 or resting_hr >= 72:
        recovery_assessment = "Strained — reduce intensity"
    else:
        recovery_assessment = "Moderate"

    # Activity assessment
    if steps >= 10000 and active_minutes >= 45:
        activity_assessment = "Very active"
    elif steps >= 7000:
        activity_assessment = "Active"
    else:
        activity_assessment = "Low activity — aim for more movement"

    return {
        "report_type": "oura_daily_analytics",
        "date": day.isoformat(),
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "metrics": {
            "sleep_hours": sleep_hours,
            "sleep_quality_score": sleep_quality,
            "readiness_score": readiness_score,
            "deep_sleep_min": deep_sleep_min,
            "rem_sleep_min": rem_sleep_min,
            "efficiency": efficiency,
            "steps": steps,
            "active_minutes": active_minutes,
            "resting_heart_rate": resting_hr,
            "heart_rate_variability": hrv,
            "temp_deviation": temp_deviation,
        },
        "assessments": {
            "sleep": sleep_assessment,
            "recovery": recovery_assessment,
            "activity": activity_assessment,
        },
        "summary": (
            f"Sleep: {sleep_hours}h (score {sleep_quality}/100, deep {deep_sleep_min}min, REM {rem_sleep_min}min) — {sleep_assessment}. "
            f"Recovery: HRV {hrv}ms, RHR {resting_hr}bpm, readiness {readiness_score}/100 — {recovery_assessment}. "
            f"Activity: {steps} steps, {active_minutes} active min — {activity_assessment}."
        ),
    }


def generate_oura_analytics(config: SyncConfig, day: date | None = None) -> Dict[str, Any]:
    """Fetch Oura data for the day and generate an analytics report."""
    if day is None:
        day = datetime.now(tz=config.timezone).date()

    payload = load_oura_daily(config, day)
    return _build_analytics(day, payload)


def save_analytics_local(config: SyncConfig, analytics: Dict[str, Any]) -> Path:
    """Save analytics report as a local JSON file."""
    day = analytics["date"]
    out_dir = config.data_dir / "analytics"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"oura_analytics_{day}.json"
    target.write_text(json.dumps(analytics, indent=2))
    return target


def upload_analytics_to_drive(config: SyncConfig, analytics: Dict[str, Any]) -> str:
    """Upload analytics report to Google Drive calendar folder. Returns file ID."""
    from .connectors.gdrive import calendar_folder_path, upload_bytes

    day_str = analytics["date"]
    day_obj = date.fromisoformat(day_str)
    folder = calendar_folder_path(day_obj)

    # Human-readable text summary
    metrics = analytics.get("metrics", {})
    assessments = analytics.get("assessments", {})
    text = (
        f"Oura Ring — {day_str}\n"
        f"{'='*40}\n\n"
        f"Sleep:    {metrics.get('sleep_hours', 0)}h  (score {metrics.get('sleep_quality_score', 0)}/100) — {assessments.get('sleep', 'N/A')}\n"
        f"  Deep:   {metrics.get('deep_sleep_min', 0)} min\n"
        f"  REM:    {metrics.get('rem_sleep_min', 0)} min\n"
        f"  Efficiency: {metrics.get('efficiency', 0)}%\n"
        f"HRV:      {metrics.get('heart_rate_variability', 0)} ms\n"
        f"RHR:      {metrics.get('resting_heart_rate', 0)} bpm\n"
        f"Readiness: {metrics.get('readiness_score', 0)}/100\n"
        f"Temp dev: {metrics.get('temp_deviation', 0)}°C\n"
        f"Steps:    {metrics.get('steps', 0)}\n"
        f"Active:   {metrics.get('active_minutes', 0)} min\n\n"
        f"Recovery: {assessments.get('recovery', 'N/A')}\n"
        f"Activity: {assessments.get('activity', 'N/A')}\n"
    )
    return upload_bytes(
        config,
        text.encode("utf-8"),
        f"oura_{day_str}.txt",
        mime_type="text/plain",
        folder_path=folder,
        create_folders=True,
    )
