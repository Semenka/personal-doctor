"""Google Health (Fitness API) connector — the new transport for Fitbit data.

Fitbit's standalone developer portal (dev.fitbit.com) no longer accepts new
app registrations; bracelet data now flows through Google. The user's Fitbit
syncs to their phone's Google Health, which uploads to the Google fitness
cloud — and this connector pulls the daily aggregates server-side via the
Fitness REST API (fitness.googleapis.com) using the SAME Google Cloud OAuth
client that already powers the Drive sync (credentials.json in
GDRIVE_CREDENTIALS_DIR). No new app registration required: enable the
Fitness API on the existing project + one consent click.

One-time setup:
  1. Enable the Fitness API:
     https://console.cloud.google.com/apis/library/fitness.googleapis.com
  2. Run:  .venv/bin/python -m scripts.google_health_auth
     (opens consent; token saved to data/ingested/.google_health_token.json)

What it pulls (daily aggregates / sessions):
  steps, distance, calories, active minutes, Heart Points (≈ Active Zone
  Minutes), heart rate (min ≈ resting proxy), SpO2, body temperature, and
  sleep stages (deep/light/REM) from sleep sessions.

Not available via the public Fitness API: HRV, VO2max, floors, breathing
rate — those stay 0 here. Oura remains the ★-weighted source for HRV and
sleep staging anyway (see device_compare._METRIC_PREFERENCE).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from ..config import SyncConfig

logger = logging.getLogger("personal-doctor.google_health")

SCOPES = [
    "https://www.googleapis.com/auth/fitness.activity.read",
    "https://www.googleapis.com/auth/fitness.sleep.read",
    "https://www.googleapis.com/auth/fitness.heart_rate.read",
    "https://www.googleapis.com/auth/fitness.oxygen_saturation.read",
    "https://www.googleapis.com/auth/fitness.body_temperature.read",
]

# Sleep stage enum per Google Fit sleep segments.
_STAGE_AWAKE, _STAGE_SLEEP, _STAGE_OUT, _STAGE_LIGHT, _STAGE_DEEP, _STAGE_REM = 1, 2, 3, 4, 5, 6


def _token_path(config: SyncConfig) -> Path:
    return config.data_dir / ".google_health_token.json"


def has_credentials(config: SyncConfig) -> bool:
    """True when the one-time consent has been completed (token file exists)."""
    return _token_path(config).exists() and bool(config.gdrive_credentials_dir)


def _get_credentials(config: SyncConfig):
    """Load + refresh the Google Health OAuth token (separate from Drive's)."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    tok = _token_path(config)
    if not tok.exists():
        raise RuntimeError(
            "Google Health not authorized yet — run: "
            ".venv/bin/python -m scripts.google_health_auth"
        )
    creds = Credentials.from_authorized_user_file(str(tok), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        tok.write_text(creds.to_json())
    return creds


def _service(config: SyncConfig):
    from googleapiclient.discovery import build

    return build("fitness", "v1", credentials=_get_credentials(config))


def _day_window_ms(config: SyncConfig, day: date) -> tuple[int, int]:
    """Local-midnight → local-midnight window in epoch milliseconds."""
    tz = config.timezone
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = start + timedelta(days=1)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def _aggregate(service, data_type: str, start_ms: int, end_ms: int) -> list:
    """One daily-bucket aggregate request; returns the value dicts (or [])."""
    body = {
        "aggregateBy": [{"dataTypeName": data_type}],
        "bucketByTime": {"durationMillis": end_ms - start_ms},
        "startTimeMillis": start_ms,
        "endTimeMillis": end_ms,
    }
    try:
        resp = service.users().dataset().aggregate(userId="me", body=body).execute()
    except Exception as exc:
        logger.info(f"aggregate {data_type} unavailable: {exc}")
        return []
    out = []
    for bucket in resp.get("bucket", []) or []:
        for ds in bucket.get("dataset", []) or []:
            for pt in ds.get("point", []) or []:
                out.extend(pt.get("value", []) or [])
    return out


def _first_num(values: list, key: str = None) -> float:
    """First numeric from aggregate values — intVal or fpVal (or a mapVal key)."""
    for v in values:
        if key and "mapVal" in v:
            for mv in v["mapVal"]:
                if mv.get("key") == key:
                    inner = mv.get("value", {})
                    return inner.get("fpVal") or inner.get("intVal") or 0
        if "intVal" in v:
            return v["intVal"]
        if "fpVal" in v:
            return v["fpVal"]
    return 0


def _sleep_stages(service, config: SyncConfig, day: date) -> Dict[str, float]:
    """Sleep-stage minutes from the night ENDING on `day` (bedtime may be the
    prior evening, so the query window starts at noon the day before)."""
    tz = config.timezone
    win_start = datetime.combine(day - timedelta(days=1), time(12, 0), tzinfo=tz)
    win_end = datetime.combine(day, time(12, 0), tzinfo=tz)
    start_ms, end_ms = int(win_start.timestamp() * 1000), int(win_end.timestamp() * 1000)

    dataset_id = f"{start_ms * 1_000_000}-{end_ms * 1_000_000}"
    stages = {"deep": 0.0, "light": 0.0, "rem": 0.0, "awake": 0.0, "total": 0.0}
    try:
        resp = (
            service.users().dataSources().datasets()
            .get(
                userId="me",
                dataSourceId="derived:com.google.sleep.segment:com.google.android.gms:merged",
                datasetId=dataset_id,
            ).execute()
        )
    except Exception as exc:
        logger.info(f"sleep segments unavailable: {exc}")
        return stages

    for pt in resp.get("point", []) or []:
        try:
            stage = pt["value"][0]["intVal"]
            dur_min = (int(pt["endTimeNanos"]) - int(pt["startTimeNanos"])) / 1e9 / 60
        except Exception:
            continue
        if stage == _STAGE_DEEP:
            stages["deep"] += dur_min
        elif stage == _STAGE_LIGHT:
            stages["light"] += dur_min
        elif stage == _STAGE_REM:
            stages["rem"] += dur_min
        elif stage == _STAGE_AWAKE:
            stages["awake"] += dur_min
        if stage in (_STAGE_SLEEP, _STAGE_LIGHT, _STAGE_DEEP, _STAGE_REM):
            stages["total"] += dur_min
    return stages


def fetch_daily_summary(config: SyncConfig, day: date) -> Dict[str, Any]:
    """Pull the day's bracelet data from the Google fitness cloud.

    Returns a flat dict the pipeline maps onto the standard daily schema.
    Missing metrics come back as 0 — best-effort per data type.
    """
    service = _service(config)
    start_ms, end_ms = _day_window_ms(config, day)

    steps = _first_num(_aggregate(service, "com.google.step_count.delta", start_ms, end_ms))
    distance_m = _first_num(_aggregate(service, "com.google.distance.delta", start_ms, end_ms))
    calories = _first_num(_aggregate(service, "com.google.calories.expended", start_ms, end_ms))
    active_min = _first_num(_aggregate(service, "com.google.active_minutes", start_ms, end_ms))
    heart_points = _first_num(_aggregate(service, "com.google.heart_minutes", start_ms, end_ms))
    spo2 = _first_num(_aggregate(service, "com.google.oxygen_saturation", start_ms, end_ms))
    body_temp = _first_num(_aggregate(service, "com.google.body.temperature", start_ms, end_ms))

    # Heart rate aggregate returns average; min serves as a resting proxy.
    hr_vals = _aggregate(service, "com.google.heart_rate.bpm", start_ms, end_ms)
    resting_hr = 0.0
    if hr_vals:
        mins = [v.get("fpVal", 0) for v in hr_vals if "fpVal" in v]
        resting_hr = min(mins) if mins else 0.0

    sleep = _sleep_stages(service, config, day)

    return {
        "steps": int(steps),
        "distance_km": round(distance_m / 1000, 2),
        "calories": int(calories),
        "active_minutes": int(active_min),
        "heart_points": int(heart_points),
        "resting_hr": round(resting_hr, 0),
        "spo2": round(spo2, 1),
        "body_temp": round(body_temp, 2),
        "sleep_total_min": round(sleep["total"], 0),
        "sleep_deep_min": round(sleep["deep"], 0),
        "sleep_light_min": round(sleep["light"], 0),
        "sleep_rem_min": round(sleep["rem"], 0),
    }
