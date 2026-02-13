from __future__ import annotations

from datetime import date
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
    url = f"{config.oura_base_url}/{collection}"
    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        params={"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        timeout=30,
    )
    if response.status_code >= 400:
        raise OuraAPIError(f"Oura API error {response.status_code}: {response.text}")
    payload = response.json()
    return payload.get("data", [])


def fetch_daily_summary(config: SyncConfig, day: date) -> Dict[str, Any]:
    if not config.oura_access_token:
        raise OuraAPIError("Missing OURA_ACCESS_TOKEN")
    token = config.oura_access_token
    sleep = _fetch_collection(config, token, "daily_sleep", day, day)
    activity = _fetch_collection(config, token, "daily_activity", day, day)
    readiness = _fetch_collection(config, token, "daily_readiness", day, day)
    return {
        "sleep": sleep[0] if sleep else {},
        "activity": activity[0] if activity else {},
        "readiness": readiness[0] if readiness else {},
    }
