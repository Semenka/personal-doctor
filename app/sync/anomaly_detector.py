"""Anomaly detector — the daily read of the watches (Fitbit Air, Pebble, Oura).

Runs at 07:41, right after the wearable sync. Each morning it compares last
night and yesterday against the user's OWN recent baseline (median of the
prior 28 days), and separately checks week-over-week drift:

- sleep: a short night vs baseline; two nights under 5 h
- HRV: last night well under baseline; 7-day average down ≥20% week-over-week
- resting HR: last night well over baseline; 7-day average up ≥10 bpm / 15%
- deep sleep: a night far under baseline (Air nights only — other watches'
  stage minutes are not comparable and are dropped upstream)
- SpO2: a low night
- temperature deviation ≥0.5 °C; readiness <60 two days running (ring nights)
- activity: yesterday's steps under half of baseline

``find_anomalies`` returns structured findings. What happens next — journal
research, a corrective action, the alert, the line in the digest — lives in
``anomaly_research``.

History is ordered OLDEST FIRST. The previous version sliced it as if newest
came first, so its week-over-week checks compared the weeks the wrong way
round (an HRV *rise* would have alerted as a drop) and its "last two nights"
were the two oldest in the window.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from statistics import median
from typing import Any, Dict, List, Optional

from .config import SyncConfig

logger = logging.getLogger("personal-doctor.anomaly_detector")

BASELINE_DAYS = 28
MIN_BASELINE_POINTS = 5

_ICON = {
    "sleep_short": "😴", "sleep_debt": "😴", "hrv_low": "📉", "hrv_drift": "📉",
    "rhr_high": "📈", "rhr_drift": "📈", "deep_sleep_low": "🌙", "spo2_low": "🫁",
    "temp_deviation": "🌡️", "readiness_low": "🔻", "steps_low": "🚶",
}


def _history(config: SyncConfig, today: date, days: int = BASELINE_DAYS + 1):
    from .trend_analyzer import load_primary_wearable_history

    return load_primary_wearable_history(config.data_dir, today, num_days=days)


def _num(day: Optional[Dict[str, Any]], key: str) -> float:
    try:
        return float((day or {}).get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


def _values(days: List[Dict[str, Any]], key: str) -> List[float]:
    return [v for v in (_num(d, key) for d in days) if v > 0]


def _baseline(days: List[Dict[str, Any]], key: str) -> Optional[float]:
    vals = _values(days, key)
    return median(vals) if len(vals) >= MIN_BASELINE_POINTS else None


def _avg(days: List[Dict[str, Any]], key: str, min_points: int = 3) -> Optional[float]:
    vals = _values(days, key)
    return sum(vals) / len(vals) if len(vals) >= min_points else None


def _day_key(d: Dict[str, Any]) -> str:
    return str(d.get("date") or d.get("_date") or "")


def _finding(
    key: str, label: str, severity: str, message: str, day: str,
    value: Optional[float] = None, baseline: Optional[float] = None, unit: str = "",
) -> Dict[str, Any]:
    return {
        "key": key, "label": label, "severity": severity,
        "message": f"{_ICON.get(key, '⚠️')} {message}",
        "date": day, "value": None if value is None else round(value, 2),
        "baseline": None if baseline is None else round(baseline, 2), "unit": unit,
    }


def find_anomalies(config: SyncConfig, today: Optional[date] = None) -> List[Dict[str, Any]]:
    """Structured anomalies for ``today`` (last night + yesterday's activity).

    Each item: key, label, severity ('watch' | 'alert'), message, date, value,
    baseline, unit. Empty list = nothing unusual, or not enough data to say.
    """
    if today is None:
        today = datetime.now(tz=config.timezone).date()

    hist = _history(config, today)  # oldest first
    by_day = {_day_key(d): d for d in hist}
    t_iso = today.isoformat()
    y_iso = (today - timedelta(days=1)).isoformat()
    night = by_day.get(t_iso)       # last night's sleep is filed under today
    yesterday = by_day.get(y_iso)   # the last complete day of activity
    if len(hist) < 3 or not (night or yesterday):
        return []

    prior = [d for d in hist if _day_key(d) < t_iso]          # baseline for the night
    prior_act = [d for d in hist if _day_key(d) < y_iso]      # baseline for yesterday
    recent7 = [d for d in hist if _day_key(d) > (today - timedelta(days=7)).isoformat()]
    prior7 = [
        d for d in hist
        if (today - timedelta(days=14)).isoformat() < _day_key(d) <= (today - timedelta(days=7)).isoformat()
    ]
    out: List[Dict[str, Any]] = []

    # ── Sleep ──
    sleep = _num(night, "sleep_hours")
    sleep_base = _baseline(prior, "sleep_hours")
    prev_sleep = _num(yesterday, "sleep_hours")
    if 0 < sleep < 5.0 and 0 < prev_sleep < 5.0:
        out.append(_finding(
            "sleep_debt", "Sleep debt", "alert",
            f"Two short nights in a row: {prev_sleep:.1f} h then {sleep:.1f} h (<5 h each)",
            t_iso, sleep, sleep_base, "h",
        ))
    elif sleep > 0 and sleep_base and sleep < 6.0 and sleep_base - sleep >= 1.0:
        severe = sleep < 5.0 or sleep_base - sleep >= 1.5
        out.append(_finding(
            "sleep_short", "Short night", "alert" if severe else "watch",
            f"Sleep {sleep:.1f} h last night — {sleep_base - sleep:.1f} h under your "
            f"{BASELINE_DAYS}-day median ({sleep_base:.1f} h)",
            t_iso, sleep, sleep_base, "h",
        ))

    # ── HRV ──
    hrv = _num(night, "hrv")
    hrv_base = _baseline(prior, "hrv")
    hrv_recent, hrv_prior = _avg(recent7, "hrv"), _avg(prior7, "hrv")
    if hrv_recent and hrv_prior and (hrv_recent - hrv_prior) / hrv_prior <= -0.20:
        pct = (hrv_recent - hrv_prior) / hrv_prior * 100
        out.append(_finding(
            "hrv_drift", "HRV falling", "alert",
            f"HRV down {abs(pct):.0f}% week-over-week ({hrv_recent:.0f} ms vs {hrv_prior:.0f} ms)",
            t_iso, hrv_recent, hrv_prior, "ms",
        ))
    elif hrv > 0 and hrv_base and hrv <= hrv_base * 0.80:
        pct = (hrv_base - hrv) / hrv_base * 100
        out.append(_finding(
            "hrv_low", "Low HRV", "alert" if hrv <= hrv_base * 0.70 else "watch",
            f"HRV {hrv:.0f} ms last night — {pct:.0f}% under your median ({hrv_base:.0f} ms)",
            t_iso, hrv, hrv_base, "ms",
        ))

    # ── Resting heart rate ──
    rhr = _num(night, "resting_hr")
    rhr_base = _baseline(prior, "resting_hr")
    rhr_recent, rhr_prior = _avg(recent7, "resting_hr"), _avg(prior7, "resting_hr")
    if rhr_recent and rhr_prior and (
        rhr_recent - rhr_prior >= 10 or (rhr_recent - rhr_prior) / rhr_prior >= 0.15
    ):
        out.append(_finding(
            "rhr_drift", "Resting HR rising", "alert",
            f"Resting HR up {rhr_recent - rhr_prior:.0f} bpm week-over-week "
            f"({rhr_recent:.0f} vs {rhr_prior:.0f})",
            t_iso, rhr_recent, rhr_prior, "bpm",
        ))
    elif rhr > 0 and rhr_base and rhr - rhr_base >= 5:
        out.append(_finding(
            "rhr_high", "High resting HR", "alert" if rhr - rhr_base >= 8 else "watch",
            f"Resting HR {rhr:.0f} bpm — {rhr - rhr_base:.0f} above your median ({rhr_base:.0f})",
            t_iso, rhr, rhr_base, "bpm",
        ))

    # ── Deep sleep (stage minutes survive only for comparable Air nights) ──
    deep = _num(night, "deep_sleep_min")
    deep_base = _baseline(prior, "deep_sleep_min")
    if deep > 0 and deep_base and deep < 45 and deep <= deep_base * 0.60:
        out.append(_finding(
            "deep_sleep_low", "Low deep sleep", "watch",
            f"Deep sleep {deep:.0f} min — your median is {deep_base:.0f} min",
            t_iso, deep, deep_base, "min",
        ))

    # ── Blood oxygen ──
    spo2 = _num(night, "spo2")
    spo2_base = _baseline(prior, "spo2")
    if 0 < spo2 < 92:
        out.append(_finding(
            "spo2_low", "Low SpO2", "alert",
            f"SpO2 {spo2:.0f}% overnight (below 92%)", t_iso, spo2, spo2_base, "%",
        ))
    elif spo2 > 0 and spo2_base and spo2 < 94 and spo2_base - spo2 >= 2:
        out.append(_finding(
            "spo2_low", "Low SpO2", "watch",
            f"SpO2 {spo2:.0f}% overnight — your median is {spo2_base:.0f}%",
            t_iso, spo2, spo2_base, "%",
        ))

    # ── Temperature deviation (possible illness) ──
    temp = float((night or {}).get("temp_deviation") or 0)
    if abs(temp) >= 0.5:
        direction = "elevated" if temp > 0 else "depressed"
        out.append(_finding(
            "temp_deviation", "Temperature deviation", "alert",
            f"Temperature deviation {temp:+.2f} °C ({direction}) — possible illness",
            t_iso, temp, 0.0, "°C",
        ))

    # ── Readiness (ring nights only) ──
    r_today, r_prev = _num(night, "readiness_score"), _num(yesterday, "readiness_score")
    if 0 < r_today < 60 and 0 < r_prev < 60:
        out.append(_finding(
            "readiness_low", "Low readiness", "alert",
            f"Readiness under 60 two days running ({r_prev:.0f}, {r_today:.0f})",
            t_iso, r_today, 60.0, "/100",
        ))

    # ── Activity: judged on yesterday, the last complete day ──
    steps = _num(yesterday, "steps")
    steps_base = _baseline(prior_act, "steps")
    if steps > 0 and steps_base and steps < 4000 and steps <= steps_base * 0.5:
        out.append(_finding(
            "steps_low", "Low activity", "watch",
            f"{steps:,.0f} steps yesterday — under half your median ({steps_base:,.0f})",
            y_iso, steps, steps_base, "steps",
        ))

    order = {"alert": 0, "watch": 1}
    out.sort(key=lambda a: order.get(a["severity"], 2))
    return out


def detect_anomalies(config: SyncConfig, today: date | None = None) -> List[str]:
    """Human-readable alert lines (compatibility wrapper around find_anomalies)."""
    return [a["message"] for a in find_anomalies(config, today)]


def run_anomaly_detector() -> None:
    """Scheduler entry point (07:41): detect → research → save → alert."""
    from .anomaly_research import run_anomaly_review
    from .config import load_config

    config = load_config()
    today = datetime.now(tz=config.timezone).date()
    try:
        review = run_anomaly_review(config, today)
    except Exception as exc:  # noqa: BLE001 — never take the scheduler down
        print(f"Anomaly review failed: {exc}")
        return
    n = len(review.get("anomalies") or [])
    print(
        f"Anomaly review {today.isoformat()}: {n} finding(s)"
        + (", alert sent" if review.get("alert_sent") else "")
    )
