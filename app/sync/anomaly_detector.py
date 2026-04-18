"""Anomaly detector (X1).

Runs right after the Oura sync at 07:41. Catches clinically notable changes
that would otherwise be buried in tomorrow's email:

- HRV drops >20% week-over-week
- Resting HR jumps >10 bpm or >15% week-over-week
- 2+ consecutive nights with < 5 h sleep
- Readiness score <60 two days in a row
- Temperature deviation >0.5 °C (possible illness/infection)

Sends an immediate WhatsApp alert when any trigger fires. Lightweight —
doesn't call the LLM.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import List

from .config import SyncConfig

logger = logging.getLogger("personal-doctor.anomaly_detector")


def _history(config: SyncConfig, today: date, days: int = 14):
    from .trend_analyzer import load_oura_history

    return load_oura_history(config.data_dir, today, days=days)


def _week_avg(history, key: str) -> float | None:
    vals = [d.get(key, 0) or 0 for d in history if (d.get(key, 0) or 0) > 0]
    if not vals:
        return None
    return sum(vals) / len(vals)


def detect_anomalies(config: SyncConfig, today: date | None = None) -> List[str]:
    """Return a list of human-readable alert lines. Empty list = no issues."""
    if today is None:
        today = datetime.now(tz=config.timezone).date()

    hist = _history(config, today, days=14)
    if len(hist) < 3:
        return []

    alerts: List[str] = []

    today_data = next((d for d in hist if d.get("date") == today.isoformat()), None)
    if not today_data:
        return []

    # Compute 7-day averages (today + past 6) and prior 7-day (days 7-13 back)
    recent7 = hist[:7]
    prior7 = hist[7:14]
    hrv_recent = _week_avg(recent7, "hrv")
    hrv_prior = _week_avg(prior7, "hrv")
    rhr_recent = _week_avg(recent7, "resting_hr")
    rhr_prior = _week_avg(prior7, "resting_hr")

    if hrv_recent and hrv_prior and hrv_prior > 0:
        pct = (hrv_recent - hrv_prior) / hrv_prior
        if pct <= -0.20:
            alerts.append(
                f"📉 HRV down {pct*100:.0f}% WoW ({hrv_recent:.0f} ms vs {hrv_prior:.0f} ms prior week)"
            )

    if rhr_recent and rhr_prior and rhr_prior > 0:
        delta_bpm = rhr_recent - rhr_prior
        pct = (rhr_recent - rhr_prior) / rhr_prior
        if delta_bpm >= 10 or pct >= 0.15:
            alerts.append(
                f"📈 Resting HR up {delta_bpm:.0f} bpm WoW ({rhr_recent:.0f} vs {rhr_prior:.0f} prior)"
            )

    # Two consecutive short nights
    last_two = hist[:2]
    short_nights = [d for d in last_two if 0 < (d.get("sleep_hours", 0) or 0) < 5.0]
    if len(short_nights) >= 2:
        alerts.append(
            f"😴 Two consecutive short sleep nights (<5h): {short_nights[0]['date']}, {short_nights[1]['date']}"
        )

    # Readiness < 60 two days in a row
    low_ready = [d for d in last_two if 0 < (d.get("readiness_score", 0) or 0) < 60]
    if len(low_ready) >= 2:
        alerts.append(
            f"🔻 Readiness <60 for 2 days: {low_ready[0]['date']} ({low_ready[0].get('readiness_score')}), "
            f"{low_ready[1]['date']} ({low_ready[1].get('readiness_score')})"
        )

    # Temperature deviation > 0.5 °C — possible illness
    temp = today_data.get("temp_deviation", 0) or 0
    if abs(temp) >= 0.5:
        direction = "elevated" if temp > 0 else "depressed"
        alerts.append(
            f"🌡️ Temperature deviation {temp:+.2f} °C today ({direction}) — consider rest / possible illness"
        )

    return alerts


def run_anomaly_detector() -> None:
    """Called from the scheduler after the Oura sync."""
    from .config import load_config
    from .whatsapp_sender import _run_openclaw_send

    config = load_config()
    today = datetime.now(tz=config.timezone).date()
    alerts = detect_anomalies(config, today)
    if not alerts:
        return
    body = "⚡ Anomaly alert — " + today.isoformat() + "\n" + "\n".join(alerts)
    try:
        _run_openclaw_send(body)
        print(f"Anomaly alert sent ({len(alerts)} item(s)).")
    except Exception as exc:
        print(f"Anomaly alert send failed: {exc}")
