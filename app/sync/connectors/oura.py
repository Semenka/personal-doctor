from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List

import requests

from ..config import SyncConfig


class OuraAPIError(RuntimeError):
    pass


def _fetch_collection(
    config: SyncConfig,
    token: str,
    collection: str,
    start_date: date,
    end_date: date,
) -> List[Dict[str, Any]]:
    """Fetch data from an Oura API v2 collection endpoint.

    NOTE: Oura API v2 uses EXCLUSIVE end_date for ALL endpoints.
    Querying start=2026-02-21, end=2026-02-21 returns NOTHING.
    We automatically add +1 day to end_date to include the target day.
    """
    url = f"{config.oura_base_url}/{collection}"
    # Oura v2 uses exclusive end_date, so bump by 1 to include the target day
    inclusive_end = end_date + timedelta(days=1)
    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        params={"start_date": start_date.isoformat(), "end_date": inclusive_end.isoformat()},
        timeout=30,
    )
    if response.status_code >= 400:
        raise OuraAPIError(f"Oura API error {response.status_code}: {response.text}")
    payload = response.json()
    return payload.get("data", [])


def fetch_daily_summary(config: SyncConfig, day: date) -> Dict[str, Any]:
    """Fetch comprehensive daily data from Oura API v2.

    Combines data from multiple endpoints:
    - daily_sleep: sleep score and contributor breakdown
    - daily_activity: steps, calories, active time (may be unavailable for today)
    - daily_readiness: readiness score and temperature deviation
    - sleep: detailed sleep periods with duration, HR, HRV (the key data source)
    """
    if not config.oura_access_token:
        raise OuraAPIError("Missing OURA_ACCESS_TOKEN")
    token = config.oura_access_token

    daily_sleep = _fetch_collection(config, token, "daily_sleep", day, day)
    daily_readiness = _fetch_collection(config, token, "daily_readiness", day, day)

    # daily_activity: try today first, fall back to yesterday
    daily_activity = _fetch_collection(config, token, "daily_activity", day, day)
    activity_is_previous = False
    if not daily_activity:
        yesterday = day - timedelta(days=1)
        daily_activity = _fetch_collection(config, token, "daily_activity", yesterday, yesterday)
        activity_is_previous = True

    # sleep periods endpoint has detailed metrics (duration, HR, HRV)
    # that are NOT available in daily_sleep.
    # Oura attributes sleep to the date it STARTED, so overnight sleep
    # (e.g. 23:00 Apr 20 -> 07:00 Apr 21) has day=Apr 20.
    # We check both the target day AND the previous day to find the
    # overnight sleep that corresponds to today's daily_sleep score.
    yesterday = day - timedelta(days=1)
    sleep_periods = (
        _fetch_collection(config, token, "sleep", yesterday, yesterday) +
        _fetch_collection(config, token, "sleep", day, day)
    )

    # Find the primary sleep period:
    # 1. Prefer type=long_sleep (avoids trusting period==0 which can be a nap)
    # 2. Fall back to the longest period by total duration
    def _sleep_duration(sp):
        return (
            (sp.get("deep_sleep_duration") or 0) +
            (sp.get("light_sleep_duration") or 0) +
            (sp.get("rem_sleep_duration") or 0)
        )

    primary_sleep = {}
    long_sleeps = [sp for sp in sleep_periods if sp.get("type") == "long_sleep"]
    if long_sleeps:
        # Pick the most recent long_sleep (closest to waking up on target day)
        primary_sleep = max(long_sleeps, key=lambda s: s.get("bedtime_end", ""))
    elif sleep_periods:
        primary_sleep = max(sleep_periods, key=_sleep_duration)

    return {
        "daily_sleep": daily_sleep[0] if daily_sleep else {},
        "daily_activity": daily_activity[0] if daily_activity else {},
        "daily_readiness": daily_readiness[0] if daily_readiness else {},
        "sleep_period": primary_sleep,
        "activity_is_previous": activity_is_previous,
    }
