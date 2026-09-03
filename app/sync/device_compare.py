"""Wearable comparison layer — Fitbit Air primary, Oura historical.

The user has migrated to a single wearable: the Fitbit Air, in
fitbit_<date>.json. Oura stopped syncing after 2026-07-20 and its
daily_<date>.json files are now all-zero stubs, so on current days there is
nothing to compare.

This module therefore renders in two modes, chosen per day by whether Oura
actually contributed a value:
  - single-device (the normal case now): just today's Fitbit Air readings;
  - side-by-side: kept for historical dates that genuinely have both, and for
    the case where a second device is worn again.

The distinction matters beyond cosmetics: the advisor block feeds the LLM
prompt, and the old unconditional text asserted "Both devices are worn" and
told the model to trust Oura for HRV/sleep/resting-HR/temperature — advice
weighting toward a device that has reported nothing for weeks.
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

# Per-metric AUTHORITATIVE source — which device to weight when both report a
# value. The Fitbit Air is now the user's only wearable, so it is authoritative
# for every metric and is the default for anything not listed.
#
# The table is retained rather than deleted because it still decides the
# starred device on historical dates that genuinely carry both payloads, and it
# is the single place to re-weight if a second device is ever worn again. The
# Oura entries below are the *historical* rationale, kept for that case:
# research-validated overnight recovery (HRV/sleep staging/resting HR/body
# temperature) from resting finger PPG. They no longer apply to current days.
_METRIC_PREFERENCE: Dict[str, str] = {
    "hrv": "fitbit",
    "resting_hr": "fitbit",
    "sleep_hours": "fitbit",
    "deep_sleep_min": "fitbit",
    "rem_sleep_min": "fitbit",
    "light_sleep_min": "fitbit",
    "readiness_score": "fitbit",
    "temp_deviation": "fitbit",
    "avg_breath": "fitbit",
    "steps": "fitbit",
    "active_minutes": "fitbit",
    "active_zone_minutes": "fitbit",
    "distance_km": "fitbit",
    "floors": "fitbit",
    "spo2": "fitbit",
    "vo2max": "fitbit",
}


def preferred_source(metric: str) -> str:
    """Authoritative device for a metric (defaults to the Fitbit Air)."""
    return _METRIC_PREFERENCE.get(metric, "fitbit")


def _has_oura(rows: List[Dict[str, Any]]) -> bool:
    """True if any row carries a real Oura reading for this day."""
    return any(r.get("oura") for r in rows)


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
        return []  # no primary wearable payload for this day

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


def render_compare_email_html(config: SyncConfig, day: str, label: str = "Today") -> str:
    """A '⌚ Device comparison' card: metric | Oura | Fitbit.

    ``label`` names the single-device column/header ("Today" or "Yesterday")
    so the email can show yesterday's FINALIZED day instead of the 08:00
    upload-lag stub ("Steps 12").
    """
    rows = compare_metrics(config, day)
    if not rows:
        return ""

    if not _has_oura(rows):
        # Single-device card: one value column, no phantom Oura column.
        body = ""
        for r in rows:
            body += (
                '<tr>'
                f'<td style="padding:5px 10px;border-bottom:1px solid #eee;font-size:13px;">'
                f'{escape(r["label"])}</td>'
                f'<td style="padding:5px 10px;border-bottom:1px solid #eee;font-size:13px;'
                f'text-align:right;">{_fmt(r["fitbit"], r["unit"])}</td>'
                '</tr>'
            )
        return (
            '<div style="margin-top:28px;padding:16px 20px;background:#eff6ff;'
            'border-radius:12px;border:1px solid #bfdbfe;">'
            '<h3 style="color:#1e40af;margin:0 0 4px 0;font-size:16px;">'
            '&#x231A; Fitbit Air</h3>'
            '<div style="font-size:12px;color:#6b7280;margin-bottom:8px;">'
            f'{escape(label)}&rsquo;s wearable readings. Metrics not listed were not measured.</div>'
            '<table cellspacing="0" cellpadding="0" border="0" style="width:100%;">'
            '<tr><th style="text-align:left;font-size:12px;color:#64748b;padding:4px 10px;">Metric</th>'
            f'<th style="text-align:right;font-size:12px;color:#64748b;padding:4px 10px;">{escape(label)}</th></tr>'
            f'{body}</table></div>'
        )

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
    if not _has_oura(rows):
        lines = ["⌚ Fitbit Air"]
        for r in rows:
            lines.append(f"▫️ {r['label']}: {_fmt(r['fitbit'], r['unit'])}")
        return "\n".join(lines)
    lines = ["⌚ Oura vs Fitbit Air (★ = weighted more)"]
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

    if not _has_oura(rows):
        # Single-device day (the normal case since the Fitbit Air migration).
        # Say only what is true: one wearable, these readings. Naming the
        # absent metrics matters — otherwise the model can read "no HRV line"
        # as "HRV was fine" and give recovery advice with no recovery data.
        lines = [
            "## ⌚ Today's wearable data (Health Connect — Fitbit Air / Pebble 2 / phone)",
            "These are the only wearable readings available today. Do not infer "
            "recovery status from their absence: any metric not listed below "
            "was NOT measured. If you need sleep, HRV, resting HR or body "
            "temperature and they are missing, say so plainly rather than "
            "assuming a normal value.",
            "",
        ]
        for r in rows:
            lines.append(f"- {r['label']}: {_fmt(r['fitbit'], r['unit'])}")
        return "\n".join(lines)

    lines = [
        "## ⌚ Two-wearable comparison (Oura vs Fitbit Air, today)",
        "Both devices reported today. WEIGHTING: the starred (★) device is the "
        "one to weight for each metric; when the two diverge materially, base "
        "the recommendation on the starred source but you may note the "
        "disagreement.",
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
