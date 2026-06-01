"""Fitbit Web API connector — mirrors connectors/oura.py.

The user's Fitbit syncs to their phone via Google Health, but Health Connect
has no server-readable cloud API. The Fitbit Web API is the proper server-side
source, so this pulls directly from api.fitbit.com with OAuth2.

The one real difference from Oura: Fitbit access tokens expire after 8 hours
and the refresh token rotates on every refresh. So we persist the token pair
to ``config.fitbit_token_path`` and, on any 401, exchange the refresh token
for a new pair and retry once.

``fetch_daily_summary(config, day)`` returns a raw dict with one entry per
endpoint (missing/failed endpoints → {}), which pipeline.fitbit_to_daily_payload
normalizes into the shared 30-key daily schema.
"""
from __future__ import annotations

import base64
import json
import logging
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional

import requests

from ..config import SyncConfig

logger = logging.getLogger("personal-doctor.fitbit")


class FitbitAPIError(RuntimeError):
    pass


# ── Token persistence + refresh ────────────────────────────────────────────


def _token_file(config: SyncConfig) -> Path:
    return Path(config.fitbit_token_path)


def _load_token(config: SyncConfig) -> Dict[str, Any]:
    """Load the persisted token pair; fall back to env-seeded tokens."""
    p = _token_file(config)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    # First run: seed from env vars.
    seed: Dict[str, Any] = {}
    if config.fitbit_access_token:
        seed["access_token"] = config.fitbit_access_token
    if config.fitbit_refresh_token:
        seed["refresh_token"] = config.fitbit_refresh_token
    return seed


def _save_token(config: SyncConfig, tokens: Dict[str, Any]) -> None:
    p = _token_file(config)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(tokens, indent=2))


def has_credentials(config: SyncConfig) -> bool:
    """True if we have enough to attempt a Fitbit call (a refresh or access token)."""
    tok = _load_token(config)
    return bool(
        config.fitbit_client_id
        and config.fitbit_client_secret
        and (tok.get("refresh_token") or tok.get("access_token"))
    )


def _refresh_access_token(config: SyncConfig) -> str:
    """Exchange the refresh token for a fresh access+refresh pair; persist + return access."""
    tok = _load_token(config)
    refresh = tok.get("refresh_token") or config.fitbit_refresh_token
    if not (config.fitbit_client_id and config.fitbit_client_secret and refresh):
        raise FitbitAPIError("Missing Fitbit client credentials or refresh token")

    basic = base64.b64encode(
        f"{config.fitbit_client_id}:{config.fitbit_client_secret}".encode()
    ).decode()
    resp = requests.post(
        f"{config.fitbit_base_url}/oauth2/token",
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={"grant_type": "refresh_token", "refresh_token": refresh},
        timeout=30,
    )
    if resp.status_code >= 400:
        raise FitbitAPIError(
            f"Fitbit token refresh failed {resp.status_code}: {resp.text[:300]}"
        )
    data = resp.json()
    new_tokens = {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", refresh),
        "expires_in": data.get("expires_in"),
        "scope": data.get("scope"),
    }
    _save_token(config, new_tokens)
    logger.info("Fitbit access token refreshed.")
    return new_tokens["access_token"]


def _access_token(config: SyncConfig) -> str:
    tok = _load_token(config)
    access = tok.get("access_token")
    if access:
        return access
    # No access token yet — mint one from the refresh token.
    return _refresh_access_token(config)


# ── HTTP ─────────────────────────────────────────────────────────────────


def _get(config: SyncConfig, path: str) -> Dict[str, Any]:
    """GET an endpoint with bearer auth; on 401 refresh once and retry."""
    url = f"{config.fitbit_base_url}{path}"

    def _do(token: str):
        return requests.get(
            url, headers={"Authorization": f"Bearer {token}"}, timeout=30,
        )

    token = _access_token(config)
    resp = _do(token)
    if resp.status_code == 401:
        token = _refresh_access_token(config)
        resp = _do(token)
    if resp.status_code == 429:
        # Rate-limited — surface clearly but don't crash the pipeline.
        raise FitbitAPIError("Fitbit rate limit (429) — try later")
    if resp.status_code >= 400:
        raise FitbitAPIError(f"Fitbit API {resp.status_code} for {path}: {resp.text[:200]}")
    try:
        return resp.json()
    except Exception:
        return {}


def fetch_daily_summary(config: SyncConfig, day: date) -> Dict[str, Any]:
    """Fetch the day's data across the 6 Fitbit endpoints.

    Best-effort per endpoint: a failure on one (e.g. SpO2 not available on the
    device, or no HRV that night) leaves that sub-dict empty rather than
    failing the whole sync.
    """
    if not has_credentials(config):
        raise FitbitAPIError("Missing Fitbit credentials")

    d = day.isoformat()
    out: Dict[str, Any] = {}

    endpoints = {
        "activities": f"/1/user/-/activities/date/{d}.json",
        "sleep": f"/1.2/user/-/sleep/date/{d}.json",
        "heart": f"/1/user/-/activities/heart/date/{d}/1d.json",
        "hrv": f"/1/user/-/hrv/date/{d}.json",
        "br": f"/1/user/-/br/date/{d}.json",
        "spo2": f"/1/user/-/spo2/date/{d}.json",
    }
    for key, path in endpoints.items():
        try:
            out[key] = _get(config, path)
        except FitbitAPIError as exc:
            logger.info(f"Fitbit {key} unavailable for {d}: {exc}")
            out[key] = {}
    return out
