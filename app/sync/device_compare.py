"""Side-by-side Oura vs Fitbit comparison layer.

The user runs two wearables and wants them shown side by side (not merged),
to compare devices. Oura stays the flat-key primary in daily_<date>.json;
Fitbit is the parallel fitbit_<date>.json. This module reads both and renders
the comparison for the three surfaces: advisor prompt, email, WhatsApp.

Shared metrics compared: HRV, resting HR, sleep hours, deep sleep, steps,
breathing rate. SpO2 is Fitbit-only (shown when present).
"""
from __future__ import annotations

import logging
from datetime import date
from html import escape
from typing import Any, Dict, List, Optional

from .config import SyncConfig

logger = logging.getLogger("personal-doctor.device_compare")

# (payload_key, label, unit, fitbit_only)
_COMPARE_METRICS = [
    ("hrv", "HRV", "ms", False),
    ("resting_hr", "Resting HR", "bpm", False),
    ("sleep_hours", "Sleep", "h", False),
    ("deep_sleep_min", "Deep sleep", "min", False),
    ("steps", "Steps", "", False),
    ("avg_breath", "Breathing rate", "/min", False),
    ("temp_deviation", "Skin temp dev", "°C", False),
    # Fitbit-only bracelet metrics:
    ("spo2", "SpO2", "%", True),
    ("active_zone_minutes", "Active Zone Min", "", True),
    ("vo2max", "VO2max / cardio", "", True),
    ("distance_km", "Distance", "km", True),
    ("floors", "Floors", "", True),
]

# Per-metric AUTHORITATIVE source — which device to weight more when both
# report a value. Grounded in device validation:
#   - Oura ring: research-validated for overnight recovery — HRV (overnight
#     RMSSD), sleep duration + staging, resting HR, body temperature. Finger
#     PPG at rest is cleaner than wrist PPG.
#   - Fitbit wrist: better for daytime movement — steps, Active Zone Minutes,
#     distance, floors — and exposes SpO2 + VO2max that Oura's daily API doesn't.
# Both are KEPT and shown; the preferred one is starred and is what blended
# consumers use as the primary value.
_METRIC_PREFERENCE: Dict[str, str] = {
    "hrv": "oura",
    "resting_hr": "oura",
    "sleep_hours": "oura",
    "deep_sleep_min": "oura",
    "rem_sleep_min": "oura",
    "light_sleep_min": "oura",
    "readiness_score": "oura",
    "temp_deviation": "oura",
    "avg_breath": "oura",
    "steps": "fitbit",
    "active_minutes": "fitbit",
    "active_zone_minutes": "fitbit",
    "distance_km": "fitbit",
    "floors": "fitbit",
    "spo2": "fitbit",
    "vo2max": "fitbit",
}


def preferred_source(metric: str) -> str:
    """Authoritative device for a metric (defaults to Oura for overnight signals)."""
    return _METRIC_PREFERENCE.get(metric, "oura")


def _load(config: SyncConfig, day: str, source: str) -> Optional[Dict[str, Any]]:
    from .storage import load_wearable_payload_file

    try:
        return load_wearable_payload_file(config.data_dir, day, source=source)
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.warning(f"load {source} {day} failed: {exc}")
        return None


def compare_metrics(config: SyncConfig, day: str) -> List[Dict[str, Any]]:
    """Return side-by-side rows for the day. Empty if Fitbit data is absent.

    Each row: {metric, label, unit, oura, fitbit, delta, agree}.
    A value of 0/None means that device didn't record the metric.
    """
    oura = _load(config, day, "oura")
    fitbit = _load(config, day, "fitbit")
    if not fitbit:
        return []  # nothing to compare — Oura-only day

    rows: List[Dict[str, Any]] = []
    for key, label, unit, fitbit_only in _COMPARE_METRICS:
        o_val = (oura or {}).get(key) or 0
        f_val = fitbit.get(key) or 0
        if fitbit_only and not f_val:
            continue
        if not o_val and not f_val:
            continue
        delta = None
        agree = None
        if o_val and f_val:
            delta = round(f_val - o_val, 1)
            # "agree" within 12% of the larger value (loose device concordance)
            larger = max(abs(o_val), abs(f_val))
            agree = larger > 0 and abs(delta) <= 0.12 * larger
        pref = preferred_source(key)
        # The blended/primary value: the authoritative device's reading when
        # present, else fall back to the other (keep both, weight the better).
        if pref == "fitbit":
            primary = f_val or o_val or None
        else:
            primary = o_val or f_val or None
        rows.append({
            "metric": key, "label": label, "unit": unit,
            "oura": o_val or None, "fitbit": f_val or None,
            "delta": delta, "agree": agree, "fitbit_only": fitbit_only,
            "preferred": pref, "primary": primary,
        })
    return rows


def blended_value(config: SyncConfig, day: str, metric: str) -> Optional[float]:
    """Single best value for a metric: authoritative device, else the other.

    Lets any consumer get one trustworthy number while both sources stay on
    file. Used where a scalar is needed (e.g. trend continuity).
    """
    oura = _load(config, day, "oura") or {}
    fitbit = _load(config, day, "fitbit") or {}
    pref = preferred_source(metric)
    primary, secondary = (
        (fitbit, oura) if pref == "fitbit" else (oura, fitbit)
    )
    return primary.get(metric) or secondary.get(metric) or None


def _fmt(v: Any, unit: str) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:g}{unit}"
    return f"{v}{unit}"


def render_compare_email_html(config: SyncConfig, day: str) -> str:
    """A '⌚ Device comparison' card: metric | Oura | Fitbit."""
    rows = compare_metrics(config, day)
    if not rows:
        return ""
    body = ""
    for r in rows:
        # Green when devices agree, amber when they diverge, grey when one-sided.
        if r["agree"] is True:
            dot = "🟢"
        elif r["agree"] is False:
            dot = "🟡"
        else:
            dot = ""
        # ★ marks the authoritative device we weight more for this metric.
        o_star = " ★" if r["preferred"] == "oura" else ""
        f_star = " ★" if r["preferred"] == "fitbit" else ""
        body += (
            '<tr>'
            f'<td style="padding:5px 10px;border-bottom:1px solid #eee;font-size:13px;">'
            f'{dot} {escape(r["label"])}</td>'
            f'<td style="padding:5px 10px;border-bottom:1px solid #eee;font-size:13px;'
            f'text-align:right;">{_fmt(r["oura"], r["unit"])}{o_star}</td>'
            f'<td style="padding:5px 10px;border-bottom:1px solid #eee;font-size:13px;'
            f'text-align:right;">{_fmt(r["fitbit"], r["unit"])}{f_star}</td>'
            '</tr>'
        )
    return (
        '<div style="margin-top:28px;padding:16px 20px;background:#eff6ff;'
        'border-radius:12px;border:1px solid #bfdbfe;">'
        '<h3 style="color:#1e40af;margin:0 0 4px 0;font-size:16px;">'
        '&#x231A; Device comparison</h3>'
        '<div style="font-size:12px;color:#6b7280;margin-bottom:8px;">'
        'Oura vs Fitbit for today. 🟢 agree · 🟡 diverge. '
        '★ = the device weighted more for that metric '
        '(Oura for recovery/sleep, Fitbit for daytime activity + SpO2).</div>'
        '<table cellspacing="0" cellpadding="0" border="0" style="width:100%;">'
        '<tr><th style="text-align:left;font-size:12px;color:#64748b;padding:4px 10px;">Metric</th>'
        '<th style="text-align:right;font-size:12px;color:#64748b;padding:4px 10px;">Oura</th>'
        '<th style="text-align:right;font-size:12px;color:#64748b;padding:4px 10px;">Fitbit</th></tr>'
        f'{body}</table></div>'
    )


def render_compare_whatsapp(config: SyncConfig, day: str) -> str:
    """Compact comparison lines for the WhatsApp digest."""
    rows = compare_metrics(config, day)
    if not rows:
        return ""
    lines = ["⌚ Oura vs Fitbit (★ = weighted more)"]
    for r in rows:
        if r["agree"] is True:
            dot = "🟢"
        elif r["agree"] is False:
            dot = "🟡"
        else:
            dot = "▫️"
        o_star = "★" if r["preferred"] == "oura" else ""
        f_star = "★" if r["preferred"] == "fitbit" else ""
        lines.append(
            f"{dot} {r['label']}: "
            f"O {_fmt(r['oura'], r['unit'])}{o_star} / "
            f"F {_fmt(r['fitbit'], r['unit'])}{f_star}"
        )
    return "\n".join(lines)


def render_compare_advisor_block(config: SyncConfig, day: str) -> str:
    """Text block for the LLM prompt so advice can note device agreement/divergence."""
    rows = compare_metrics(config, day)
    if not rows:
        return ""
    lines = [
        "## ⌚ Two-wearable comparison (Oura vs Fitbit, today)",
        "Both devices are worn. WEIGHTING: trust **Oura** for recovery/overnight "
        "signals (HRV, sleep duration + staging, resting HR, body temperature, "
        "breathing) — it's the validated standard there. Trust **Fitbit** for "
        "daytime activity (steps, Active Zone Minutes, distance, floors) and for "
        "SpO2 + VO2max, which Oura's daily API doesn't provide. The starred (★) "
        "device below is the one to weight for each metric; when the two diverge "
        "materially, base the recommendation on the starred source but you may "
        "note the disagreement.",
        "",
    ]
    for r in rows:
        verdict = ""
        if r["agree"] is True:
            verdict = " (agree)"
        elif r["agree"] is False:
            verdict = " (diverge)"
        o_star = " ★" if r["preferred"] == "oura" else ""
        f_star = " ★" if r["preferred"] == "fitbit" else ""
        lines.append(
            f"- {r['label']}: Oura {_fmt(r['oura'], r['unit'])}{o_star} / "
            f"Fitbit {_fmt(r['fitbit'], r['unit'])}{f_star}{verdict}"
        )
    return "\n".join(lines)
