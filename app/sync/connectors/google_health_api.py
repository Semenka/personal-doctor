"""Google Health API (health.googleapis.com/v4) — the Fitbit Air's cloud.

Successor of the Fitbit Web API (which Google turns off in September 2026).
This is the authoritative, phone-independent source for the Fitbit Air, and
— because the Google Health app also imports Health Connect data — for the
Pebble too (Pebble app → Health Connect → Google Health app → this API).

Auth: the same Google Cloud OAuth *web* client as the Drive/Fitness syncs,
with the googlehealth.* read-only scopes and its own token file. Never mix
these scopes with the legacy fitness.* ones in one token (Google rejects
mixed-scope tokens), hence the separate consent + token.

One-time: enable "Google Health API" on the Cloud project, then run
    .venv/bin/python -m scripts.google_health_api_auth
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from ..config import SyncConfig

logger = logging.getLogger("personal-doctor.google_health_api")

BASE = "https://health.googleapis.com/v4/users/me"

SCOPES = [
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
]


def _token_path(config: SyncConfig) -> Path:
    return Path(config.data_dir) / ".google_health_api_token.json"


def has_credentials(config: SyncConfig) -> bool:
    """True once the one-time consent has produced a token file."""
    return _token_path(config).exists() and bool(config.gdrive_credentials_dir)


def _get_credentials(config: SyncConfig):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    tok = _token_path(config)
    if not tok.exists():
        raise RuntimeError(
            "Google Health API not authorized yet — run: "
            ".venv/bin/python -m scripts.google_health_api_auth"
        )
    creds = Credentials.from_authorized_user_file(str(tok), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        tmp = tok.with_suffix(tok.suffix + ".tmp")
        tmp.write_text(creds.to_json())
        tmp.chmod(0o600)
        tmp.replace(tok)
        tok.chmod(0o600)
    elif not creds.valid and creds.refresh_token:
        creds.refresh(Request())
    return creds


def _session(config: SyncConfig) -> requests.Session:
    creds = _get_credentials(config)
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {creds.token}", "Accept": "application/json"})
    return s


class ApiError(RuntimeError):
    pass


def _raise_for(resp: requests.Response, what: str) -> None:
    if resp.status_code < 400:
        return
    body = resp.text[:600]
    hint = ""
    if resp.status_code == 403 and ("has not been used" in body or "is disabled" in body):
        hint = " — enable 'Google Health API' on the Cloud project"
    raise ApiError(f"{what}: HTTP {resp.status_code}{hint}: {body}")


def _list(sess: requests.Session, data_type: str, filter_expr: str,
          page_size: int = 1000, max_pages: int = 10) -> List[Dict[str, Any]]:
    """GET dataTypes/{type}/dataPoints with an AIP-160 filter; paginates."""
    points: List[Dict[str, Any]] = []
    token: Optional[str] = None
    for _ in range(max_pages):
        params: Dict[str, Any] = {"filter": filter_expr, "pageSize": page_size}
        if token:
            params["pageToken"] = token
        resp = sess.get(f"{BASE}/dataTypes/{data_type}/dataPoints", params=params, timeout=30)
        _raise_for(resp, f"list {data_type}")
        data = resp.json()
        points.extend(data.get("dataPoints") or [])
        token = data.get("nextPageToken")
        if not token:
            break
    return points


def _civil(d: date) -> Dict[str, Any]:
    return {"date": {"year": d.year, "month": d.month, "day": d.day}}


def _daily_rollup(sess: requests.Session, data_type: str, day: date) -> Dict[str, Any]:
    """POST dataPoints:dailyRollUp for one calendar day; returns the one window."""
    body = {"range": {"start": _civil(day), "end": _civil(day + timedelta(days=1))},
            "windowSizeDays": 1}
    resp = sess.post(f"{BASE}/dataTypes/{data_type}/dataPoints:dailyRollUp", json=body, timeout=30)
    _raise_for(resp, f"dailyRollUp {data_type}")
    rows = resp.json().get("rollupDataPoints") or []
    return rows[0] if rows else {}


def origin_of(point: Dict[str, Any]) -> str:
    """Provenance string in the same spirit as Google Fit's originDataSourceId.

    e.g. ``google_health_api:FITBIT:PASSIVELY_MEASURED:Fitbit Air:FITNESS_BAND``
    or ``google_health_api:HEALTH_CONNECT:coredevices.coreapp`` — the
    substrings the fleet table in pipeline.WATCH_DEVICES keys on.
    """
    src = point.get("dataSource") or {}
    app = src.get("application") or {}
    dev = src.get("device") or {}
    parts = [src.get("platform"), src.get("recordingMethod"), app.get("packageName"),
             dev.get("manufacturer"), dev.get("displayName"), dev.get("formFactor")]
    parts = [str(p) for p in parts if p]
    return ":".join(["google_health_api"] + parts) if parts else ""


def _num(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _day_filter(prefix: str, day: date) -> str:
    nxt = day + timedelta(days=1)
    return f'{prefix} >= "{day.isoformat()}" AND {prefix} < "{nxt.isoformat()}"'


def _pick_sleep(points: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The night that defines the day: main sleep if flagged, else the longest."""
    sessions = [p for p in points if p.get("sleep")]
    if not sessions:
        return None
    for p in sessions:
        if (p["sleep"].get("metadata") or {}).get("mainSleep"):
            return p
    return max(sessions, key=lambda p: _num((p["sleep"].get("summary") or {}).get("minutesAsleep")))


def fetch_daily_summary(config: SyncConfig, day: date) -> Dict[str, Any]:
    """Pull one day's wearable summary from the Google Health cloud.

    Best-effort per data type: every block is independent so one missing
    scope or an unsupported method never sinks the day. Missing metrics come
    back as 0 (absent — never "low"). ``data_origins`` lists every device /
    app that contributed, so the Pebble and the Fitbit Air stay tellable.
    """
    sess = _session(config)
    out: Dict[str, Any] = {
        "steps": 0, "active_minutes": 0, "active_zone_minutes": 0,
        "sleep_minutes": 0.0, "deep_min": 0.0, "light_min": 0.0, "rem_min": 0.0,
        "awake_min": 0.0, "sleep_period_min": 0.0, "resting_hr": 0, "hrv": 0.0,
        "spo2": 0.0, "breathing_rate": 0.0, "avg_hr": 0.0,
        "sleep_start": None, "sleep_end": None, "errors": [],
    }
    origins: set = set()

    def guarded(label, fn):
        try:
            fn()
        except Exception as exc:  # keep going; record why a block is empty
            msg = f"{label}: {exc}"
            out["errors"].append(msg[:300])
            logger.info(f"google_health_api {msg[:200]}")

    def steps():
        pts = _list(sess, "steps", _day_filter("steps.interval.civil_start_time", day))
        total = 0
        for p in pts:
            total += int(_num((p.get("steps") or {}).get("count")))
            o = origin_of(p)
            if o:
                origins.add(o)
        if not total:  # rollup as the fallback (reconciled across sources)
            total = int(_num((_daily_rollup(sess, "steps", day).get("steps") or {}).get("countSum")))
        out["steps"] = total

    def active_minutes():
        row = _daily_rollup(sess, "active-minutes", day).get("activeMinutes") or {}
        mod_vig = 0
        for r in row.get("activeMinutesRollupByActivityLevel") or []:
            if r.get("activityLevel") in ("MODERATE", "VIGOROUS"):
                mod_vig += int(_num(r.get("activeMinutesSum")))
        out["active_minutes"] = mod_vig

    def azm():
        row = _daily_rollup(sess, "active-zone-minutes", day).get("activeZoneMinutes") or {}
        out["active_zone_minutes"] = sum(
            int(_num(row.get(k))) for k in ("sumInFatBurnHeartZone", "sumInCardioHeartZone", "sumInPeakHeartZone")
        )

    def sleep():
        pts = _list(sess, "sleep", _day_filter("sleep.interval.civil_end_time", day), page_size=50)
        p = _pick_sleep(pts)
        if not p:
            return
        s = p["sleep"]
        summ = s.get("summary") or {}
        out["sleep_minutes"] = _num(summ.get("minutesAsleep"))
        out["awake_min"] = _num(summ.get("minutesAwake"))
        out["sleep_period_min"] = _num(summ.get("minutesInSleepPeriod"))
        for st in summ.get("stagesSummary") or []:
            key = {"DEEP": "deep_min", "LIGHT": "light_min", "REM": "rem_min"}.get(st.get("type"))
            if key:
                out[key] = _num(st.get("minutes"))
        if not out["sleep_minutes"]:  # classic sleep without a summary: from stages
            for st in s.get("stages") or []:
                if st.get("type") in ("ASLEEP", "LIGHT", "DEEP", "REM"):
                    from datetime import datetime
                    a = datetime.fromisoformat(st["startTime"].replace("Z", "+00:00"))
                    b = datetime.fromisoformat(st["endTime"].replace("Z", "+00:00"))
                    out["sleep_minutes"] += (b - a).total_seconds() / 60
        iv = s.get("interval") or {}
        out["sleep_start"], out["sleep_end"] = iv.get("startTime"), iv.get("endTime")
        o = origin_of(p)
        if o:
            origins.add(o)

    def daily(data_type: str, field_prefix: str, key: str, value_key: str, out_key: str):
        def _run():
            pts = _list(sess, data_type, _day_filter(f"{field_prefix}.date", day), page_size=10)
            for p in pts:
                val = _num((p.get(key) or {}).get(value_key))
                if val:
                    out[out_key] = val
                    o = origin_of(p)
                    if o:
                        origins.add(o)
                    break
        return _run

    def avg_hr():
        row = _daily_rollup(sess, "heart-rate", day).get("heartRate") or {}
        out["avg_hr"] = _num(row.get("beatsPerMinuteAvg"))

    guarded("steps", steps)
    guarded("active-minutes", active_minutes)
    guarded("active-zone-minutes", azm)
    guarded("sleep", sleep)
    guarded("resting-hr", daily("daily-resting-heart-rate", "daily_resting_heart_rate",
                                "dailyRestingHeartRate", "beatsPerMinute", "resting_hr"))
    guarded("hrv", daily("daily-heart-rate-variability", "daily_heart_rate_variability",
                         "dailyHeartRateVariability", "averageHeartRateVariabilityMilliseconds", "hrv"))
    guarded("spo2", daily("daily-oxygen-saturation", "daily_oxygen_saturation",
                          "dailyOxygenSaturation", "averagePercentage", "spo2"))
    guarded("breathing", daily("daily-respiratory-rate", "daily_respiratory_rate",
                               "dailyRespiratoryRate", "breathsPerMinute", "breathing_rate"))
    guarded("avg-hr", avg_hr)

    out["resting_hr"] = int(out["resting_hr"])
    out["data_origins"] = sorted(origins)
    return out
