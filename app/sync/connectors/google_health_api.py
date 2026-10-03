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


# Sleep source preference: the Fitbit Air stages sleep from continuous heart
# rate; the Pebble's (via Health Connect) is accelerometer-led and counts
# lying still in bed as sleep (2026-10-03: Pebble 382 min vs Air 327 min for
# the same night). Health Connect sources are the fallback for nights the Air
# did not record (charging / not worn).
_SLEEP_SOURCE_RANK = ("fitbit", "coredevices", "health_connect")
# A block this close to the main sleep is the same night interrupted, not a nap.
_SAME_NIGHT_GAP = timedelta(hours=3)


def _sleep_source(point: Dict[str, Any]) -> str:
    o = origin_of(point).lower()
    for name in _SLEEP_SOURCE_RANK:
        if name in o:
            return name
    return "other"


def _iv(point: Dict[str, Any]):
    from datetime import datetime

    iv = (point.get("sleep") or {}).get("interval") or {}
    try:
        return (datetime.fromisoformat(iv["startTime"].replace("Z", "+00:00")),
                datetime.fromisoformat(iv["endTime"].replace("Z", "+00:00")))
    except Exception:
        return None


def _asleep(point: Dict[str, Any]) -> float:
    return _num(((point.get("sleep") or {}).get("summary") or {}).get("minutesAsleep"))


def _select_night(points: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Choose ONE device's account of the night and stitch it together.

    Returns ``{"main", "night": [...], "naps": [...], "source"}`` or ``{}``.
    - Device: best-ranked source that recorded any sleep (never mix devices —
      they describe the same night and would double-count).
    - Main: its mainSleep-flagged session, else its longest.
    - Night: main plus every block within 3 h of it — an interrupted night
      (2026-09-28: 00:38–04:00 then 05:44–07:51, stored as 3.2 h instead of
      5.2 h because only the main block counted).
    - Naps: that device's remaining sessions.
    """
    sessions = [p for p in points if p.get("sleep") and _iv(p)]
    if not sessions:
        return {}
    by_src: Dict[str, List[Dict[str, Any]]] = {}
    for p in sessions:
        by_src.setdefault(_sleep_source(p), []).append(p)
    source = next((n for n in _SLEEP_SOURCE_RANK if n in by_src), next(iter(by_src)))
    mine = by_src[source]
    flagged = [p for p in mine if (p["sleep"].get("metadata") or {}).get("mainSleep")]
    main = flagged[0] if flagged else max(mine, key=_asleep)

    night = [main]
    rest = sorted((p for p in mine if p is not main), key=lambda p: _iv(p)[0])
    changed = True
    while changed:  # grow outward so a chain of fragments joins up
        changed = False
        start = min(_iv(p)[0] for p in night)
        end = max(_iv(p)[1] for p in night)
        for p in list(rest):
            ps, pe = _iv(p)
            if (ps >= end and ps - end <= _SAME_NIGHT_GAP) or (pe <= start and start - pe <= _SAME_NIGHT_GAP):
                night.append(p)
                rest.remove(p)
                changed = True
    return {"main": main, "night": night, "naps": rest, "source": source}


def _pick_sleep(points: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Main sleep session of the preferred device (kept for callers/tests)."""
    return _select_night(points).get("main")


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
        "sleep_segments": 0, "nap_min": 0.0, "sleep_source": "",
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
        # The same walk is recorded by several sources at once — the Fitbit
        # Air, the phone's step counter, and Health Connect's copy of that
        # phone data. Summing raw points triple-counted (2026-09-21: points
        # summed to 33,292 vs Google's reconciled 12,234). The daily rollup
        # is Google's de-duplicated total, so it is authoritative; raw points
        # are read only for provenance, and as a fallback the single largest
        # source is used (never a sum across sources).
        pts = _list(sess, "steps", _day_filter("steps.interval.civil_start_time", day))
        per_origin: Dict[str, int] = {}
        for p in pts:
            o = origin_of(p)
            per_origin[o] = per_origin.get(o, 0) + int(_num((p.get("steps") or {}).get("count")))
            if o:
                origins.add(o)
        total = int(_num((_daily_rollup(sess, "steps", day).get("steps") or {}).get("countSum")))
        if not total and per_origin:
            total = max(per_origin.values())
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
        sel = _select_night(pts)
        if not sel:
            return
        for p in sel["night"]:
            s = p["sleep"]
            summ = s.get("summary") or {}
            asleep = _num(summ.get("minutesAsleep"))
            if not asleep:  # classic sleep without a summary: from stages
                for st in s.get("stages") or []:
                    if st.get("type") in ("ASLEEP", "LIGHT", "DEEP", "REM"):
                        from datetime import datetime
                        a = datetime.fromisoformat(st["startTime"].replace("Z", "+00:00"))
                        b = datetime.fromisoformat(st["endTime"].replace("Z", "+00:00"))
                        asleep += (b - a).total_seconds() / 60
            out["sleep_minutes"] += asleep
            out["awake_min"] += _num(summ.get("minutesAwake"))
            out["sleep_period_min"] += _num(summ.get("minutesInSleepPeriod"))
            for st in summ.get("stagesSummary") or []:
                key = {"DEEP": "deep_min", "LIGHT": "light_min", "REM": "rem_min"}.get(st.get("type"))
                if key:
                    out[key] += _num(st.get("minutes"))
        out["sleep_start"] = min(_iv(p)[0] for p in sel["night"]).isoformat().replace("+00:00", "Z")
        out["sleep_end"] = max(_iv(p)[1] for p in sel["night"]).isoformat().replace("+00:00", "Z")
        out["sleep_segments"] = len(sel["night"])
        out["nap_min"] = sum(_asleep(p) for p in sel["naps"])
        out["sleep_source"] = sel["source"]
        for p in pts:  # provenance for every device that reported, used or not
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
