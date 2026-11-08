"""Three-wearable comparison: Fitbit Air vs Pebble vs Oura (and the phone).

Every payload stores the value the analysis USES (the Air first, with
fallbacks) plus, since 2026-11-08, what each device measured on its own
(``device_steps`` / ``device_sleep_min``, from the Google Health API) and Oura's
nights in ``daily_<date>.json``. This module turns those into:

  - ``build(config, day)``: per-day rows, per-device coverage and measured
    agreement (bias / typical gap) between the devices;
  - ``render_email_html``: an email-safe dashboard (no SVG, no classes —
    Gmail strips both; bars are sized <div>s);
  - ``render_phone_line``: one line for the Telegram/WhatsApp digest;
  - ``sleep_agreement``: the measured Pebble-vs-Air sleep gap, so caveats cite
    data instead of a fixed claim (the "Pebble reads ~1 h high" note from
    September stopped being true — 13 paired nights to 2026-11-07 averaged a
    few minutes apart).
"""
from __future__ import annotations

import json
import statistics
from datetime import date, timedelta
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Optional

DEVICES = {
    "fitbit_air": {"label": "Fitbit Air", "color": "#0f766e", "soft": "#ccfbf1"},
    "pebble": {"label": "Pebble", "color": "#ea580c", "soft": "#ffedd5"},
    "oura": {"label": "Oura", "color": "#7c3aed", "soft": "#ede9fe"},
    "phone": {"label": "Phone", "color": "#64748b", "soft": "#e2e8f0"},
}
_FULL_DAY_STEPS = 3000  # a device below this didn't really record the day


def _read(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _oura_night(data_dir: Path, iso: str) -> Dict[str, Any]:
    o = _read(data_dir / f"daily_{iso}.json")
    if not (o.get("sleep_hours") or o.get("hrv")):
        return {}
    return o


def _last_seen(data_dir: Path, day: date, device: str, lookback: int = 120) -> Optional[str]:
    for i in range(lookback):
        iso = (day - timedelta(days=i)).isoformat()
        if device == "oura":
            if _oura_night(data_dir, iso):
                return iso
            continue
        fb = _read(data_dir / f"fitbit_{iso}.json")
        if (fb.get("device_steps") or {}).get(device, 0) >= 500 or \
                (fb.get("device_sleep_min") or {}).get(device):
            return iso
    return None


def build(config: Any, day: date, days: int = 7) -> Dict[str, Any]:
    """Rows for the ``days`` complete days before ``day`` (+ last night)."""
    data_dir = Path(config.data_dir)
    rows: List[Dict[str, Any]] = []
    for i in range(days, 0, -1):
        d = day - timedelta(days=i)
        iso = d.isoformat()
        fb = _read(data_dir / f"fitbit_{iso}.json")
        oura = _oura_night(data_dir, iso)
        steps = dict(fb.get("device_steps") or {})
        sleep = {k: round(v / 60, 2) for k, v in (fb.get("device_sleep_min") or {}).items()}
        if oura.get("sleep_hours"):
            sleep["oura"] = oura["sleep_hours"]
        rows.append({
            "date": iso, "steps": steps, "steps_used": fb.get("steps") or 0,
            "sleep": sleep, "sleep_used": fb.get("sleep_hours") or 0,
            "sleep_source": fb.get("sleep_source") or "",
            "hrv": {"fitbit_air": fb.get("hrv") or 0, "oura": oura.get("hrv") or 0},
            "rhr": {"fitbit_air": fb.get("resting_hr") or 0, "oura": oura.get("resting_hr") or 0},
        })

    # Last night lives in today's file (sleep is filed under the wake date).
    today_fb = _read(data_dir / f"fitbit_{day.isoformat()}.json")
    last_night = {k: round(v / 60, 2) for k, v in (today_fb.get("device_sleep_min") or {}).items()}
    oura_today = _oura_night(data_dir, day.isoformat())
    if oura_today.get("sleep_hours"):
        last_night["oura"] = oura_today["sleep_hours"]

    coverage = {
        dev: sum(1 for r in rows if r["steps"].get(dev, 0) >= 500 or r["sleep"].get(dev))
        for dev in ("fitbit_air", "pebble", "oura")
    }
    return {
        "day": day.isoformat(), "rows": rows, "coverage": coverage,
        "last_night": last_night,
        "last_night_used": today_fb.get("sleep_source") or "",
        "last_seen": {dev: _last_seen(data_dir, day, dev) for dev in ("fitbit_air", "pebble", "oura")},
        "sleep_agreement": sleep_agreement(config, day),
        "steps_agreement": _steps_agreement(rows),
    }


def sleep_agreement(config: Any, day: date, days: int = 28) -> Dict[str, Any]:
    """Measured Pebble-minus-Air sleep over recent nights both recorded."""
    data_dir = Path(config.data_dir)
    diffs = []
    for i in range(days + 1):
        fb = _read(data_dir / f"fitbit_{(day - timedelta(days=i)).isoformat()}.json")
        m = fb.get("device_sleep_min") or {}
        if m.get("fitbit_air") and m.get("pebble"):
            diffs.append(m["pebble"] - m["fitbit_air"])
    if not diffs:
        return {"n": 0}
    return {
        "n": len(diffs),
        "bias_min": round(statistics.median(diffs)),  # median: one partial night must not swing it
        "typical_gap_min": round(statistics.median(abs(x) for x in diffs)),
    }


def _steps_agreement(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    pct = []
    for r in rows:
        a, p = r["steps"].get("fitbit_air", 0), r["steps"].get("pebble", 0)
        if a >= _FULL_DAY_STEPS and p >= _FULL_DAY_STEPS:
            pct.append(100.0 * (p - a) / a)
    if not pct:
        return {"n": 0}
    return {"n": len(pct), "bias_pct": round(statistics.median(pct), 1),
            "typical_gap_pct": round(statistics.median(abs(x) for x in pct), 1)}


def sleep_caveat_phrase(config: Any, day: date) -> str:
    """'Pebble reads +4 min vs the Air (typically ±25 min, 13 nights)' — or a
    plain 'not directly comparable' when there are no paired nights yet."""
    a = sleep_agreement(config, day)
    if not a.get("n"):
        return "it is a different instrument — not directly comparable with Air nights"
    sign = "+" if a["bias_min"] >= 0 else "−"
    return (f"on {a['n']} recent nights both watches recorded, the Pebble read "
            f"{sign}{abs(a['bias_min'])} min vs the Air on average (typical gap ±{a['typical_gap_min']} min)")


# ─── Rendering ──────────────────────────────────────────────────────────────

def _bar(value: float, vmax: float, color: str, label: str, used: bool = False) -> str:
    pct = 0 if not vmax else max(2, min(100, round(100 * value / vmax)))
    star = " &#9733;" if used else ""
    weight = "700" if used else "400"
    return (
        '<table cellspacing="0" cellpadding="0" border="0" style="width:100%;">'
        '<tr><td style="padding:0;">'
        f'<div style="background:{color};width:{pct}%;height:10px;border-radius:5px;"></div>'
        '</td><td style="padding:0 0 0 8px;width:86px;white-space:nowrap;'
        f'font-size:12px;color:#334155;font-weight:{weight};">{escape(label)}{star}</td></tr></table>'
    )


def _device_card(dev: str, data: Dict[str, Any], days: int) -> str:
    meta = DEVICES[dev]
    n = data["coverage"].get(dev, 0)
    seen = data["last_seen"].get(dev)
    if n >= days - 1:
        dot, status = "#16a34a", f"Reporting &middot; {n}/{days} days"
    elif n:
        dot, status = "#d97706", f"Patchy &middot; {n}/{days} days"
    else:
        dot, status = "#94a3b8", f"Idle &middot; last {seen[5:] if seen else 'never'}"
    role = {
        "fitbit_air": "Primary: sleep, HRV, resting HR, SpO2",
        "pebble": "Backup: steps & sleep when the Air is off",
        "oura": "Second opinion on HRV & sleep when worn",
    }[dev]
    ln = data["last_night"].get(dev)
    night = f"{ln:.1f} h last night" if ln else "&mdash;"
    return (
        f'<td style="width:33%;padding:6px;vertical-align:top;">'
        f'<div style="background:#ffffff;border:1px solid #e2e8f0;border-top:4px solid {meta["color"]};'
        f'border-radius:10px;padding:10px 12px;">'
        f'<div style="font-size:14px;font-weight:700;color:#0f172a;">{meta["label"]}</div>'
        f'<div style="font-size:12px;color:#334155;margin-top:4px;">'
        f'<span style="display:inline-block;width:8px;height:8px;border-radius:4px;background:{dot};'
        f'margin-right:5px;"></span>{status}</div>'
        f'<div style="font-size:12px;color:#64748b;margin-top:4px;">{role}</div>'
        f'<div style="font-size:13px;color:#0f172a;margin-top:6px;font-weight:600;">{night}</div>'
        '</div></td>'
    )


def _legend(devs: List[str]) -> str:
    return " ".join(
        f'<span style="display:inline-block;margin-right:10px;font-size:11px;color:#475569;">'
        f'<span style="display:inline-block;width:10px;height:10px;border-radius:2px;'
        f'background:{DEVICES[d]["color"]};margin-right:4px;vertical-align:middle;"></span>'
        f'{DEVICES[d]["label"]}</span>' for d in devs
    )


def render_email_html(config: Any, day: date, days: int = 7) -> str:
    data = build(config, day, days)
    rows = data["rows"]
    if not any(r["steps"] or r["sleep"] for r in rows) and not data["last_night"]:
        return ""

    cards = "".join(_device_card(d, data, days) for d in ("fitbit_air", "pebble", "oura"))

    # Last night: one bar per device, &#9733; = the one the analysis used.
    used_src = data["last_night_used"]
    used_dev = {"fitbit": "fitbit_air", "coredevices": "pebble"}.get(used_src, used_src)
    ln = data["last_night"]
    ln_max = max(list(ln.values()) + [8.0])
    last_night_html = "".join(
        '<tr><td style="padding:3px 0;font-size:12px;color:#475569;width:78px;">'
        f'{DEVICES[d]["label"]}</td><td style="padding:3px 0;">'
        f'{_bar(ln[d], ln_max, DEVICES[d]["color"], f"{ln[d]:.1f} h", used=(d == used_dev))}</td></tr>'
        for d in ("fitbit_air", "pebble", "oura") if ln.get(d)
    ) or '<tr><td style="font-size:12px;color:#64748b;">No device has uploaded last night yet.</td></tr>'

    # Night by night: grouped bars.
    s_max = max([v for r in rows for v in r["sleep"].values()] + [8.0])
    sleep_rows = ""
    for r in rows:
        if not r["sleep"]:
            continue
        used_dev = {"fitbit": "fitbit_air", "coredevices": "pebble"}.get(r["sleep_source"], "")
        cell = ""
        for d in ("fitbit_air", "pebble", "oura"):
            hours = r["sleep"].get(d)
            if hours:
                bar = _bar(hours, s_max, DEVICES[d]["color"], f"{hours:.1f} h", used=(d == used_dev))
                cell += f'<div style="margin:1px 0;">{bar}</div>'
        sleep_rows += (
            f'<tr><td style="width:64px;padding:5px 8px 5px 0;font-size:12px;color:#64748b;white-space:nowrap;'
            f'vertical-align:top;border-bottom:1px solid #f1f5f9;">{r["date"][5:]}</td>'
            f'<td style="width:100%;padding:4px 0;border-bottom:1px solid #f1f5f9;">{cell}</td></tr>'
        )

    # Day by day steps.
    st_max = max([v for r in rows for v in r["steps"].values()] + [10000])
    step_rows = ""
    for r in rows:
        if not r["steps"]:
            continue
        cell = ""
        for d in ("fitbit_air", "pebble", "phone"):
            n = r["steps"].get(d)
            if n:
                bar = _bar(n, st_max, DEVICES[d]["color"], f"{n:,}")
                cell += f'<div style="margin:1px 0;">{bar}</div>'
        step_rows += (
            f'<tr><td style="width:64px;padding:5px 8px 5px 0;font-size:12px;color:#64748b;white-space:nowrap;'
            f'vertical-align:top;border-bottom:1px solid #f1f5f9;">{r["date"][5:]}'
            f'<div style="font-size:11px;color:#0f172a;font-weight:700;">{r["steps_used"]:,} used</div></td>'
            f'<td style="width:100%;padding:4px 0;border-bottom:1px solid #f1f5f9;">{cell}</td></tr>'
        )

    sa, sta = data["sleep_agreement"], data["steps_agreement"]
    verdict = []
    if sa.get("n"):
        sign = "+" if sa["bias_min"] >= 0 else "&minus;"
        verdict.append(f'Sleep: Pebble {sign}{abs(sa["bias_min"])} min vs Air on average, '
                       f'typical gap &plusmn;{sa["typical_gap_min"]} min ({sa["n"]} paired nights)')
    if sta.get("n"):
        sign = "+" if sta["bias_pct"] >= 0 else "&minus;"
        verdict.append(f'Steps: Pebble {sign}{abs(sta["bias_pct"])}% vs Air, typical gap '
                       f'&plusmn;{sta["typical_gap_pct"]}% ({sta["n"]} full days)')
    good = sa.get("n") and sa.get("typical_gap_min", 99) <= 30
    verdict_line = (" &middot; ".join(verdict) +
                    (" &rarr; the Pebble is a reliable backup." if good else "")) if verdict else \
        "Not enough days where both watches recorded to compare them yet."

    section = lambda title, sub: (
        f'<div style="font-size:13px;font-weight:700;color:#0f172a;margin:16px 0 2px;">{title}</div>'
        f'<div style="font-size:11px;color:#64748b;margin-bottom:6px;">{sub}</div>'
    )
    return (
        '<div style="margin-top:28px;padding:18px 18px 14px;background:#f8fafc;border-radius:14px;'
        'border:1px solid #e2e8f0;">'
        '<div style="font-size:17px;font-weight:800;color:#0f172a;">&#x231A; Your wearables, side by side</div>'
        f'<div style="font-size:12px;color:#64748b;margin:2px 0 8px;">Last {days} days &middot; &#9733; = the reading the analysis used</div>'
        f'<table cellspacing="0" cellpadding="0" border="0" style="width:100%;"><tr>{cards}</tr></table>'
        + section("Last night", "Each device's own measurement of the same night")
        + f'<table cellspacing="0" cellpadding="0" border="0" style="width:100%;">{last_night_html}</table>'
        + section("Sleep, night by night", _legend(["fitbit_air", "pebble", "oura"]))
        + f'<table cellspacing="0" cellpadding="0" border="0" style="width:100%;">{sleep_rows}</table>'
        + section("Steps, day by day", _legend(["fitbit_air", "pebble", "phone"])
                  + ' &middot; "used" = Google&rsquo;s merged count: the Air where worn, the others where not')
        + f'<table cellspacing="0" cellpadding="0" border="0" style="width:100%;">{step_rows}</table>'
        + '<div style="margin-top:12px;padding:10px 12px;background:#ffffff;border-radius:10px;'
          'border:1px solid #e2e8f0;font-size:12px;color:#0f172a;">'
          f'<b>How well do they agree?</b> {verdict_line}</div>'
        + '<div style="margin-top:8px;font-size:11px;color:#64748b;">Used for analysis: sleep, HRV, '
          'resting HR, SpO2 &rarr; Fitbit Air (Pebble if the Air missed the night) &middot; steps &rarr; '
          'Google&rsquo;s merged count &middot; Oura &rarr; shown whenever the ring is worn.</div>'
        '</div>'
    )


def render_phone_line(config: Any, day: date, days: int = 7) -> str:
    data = build(config, day, days)
    if not any(r["steps"] or r["sleep"] for r in data["rows"]):
        return ""
    parts = []
    for dev in ("fitbit_air", "pebble"):
        n = data["coverage"].get(dev, 0)
        parts.append(f"{DEVICES[dev]['label']} {n}/{days}")
    o = data["last_seen"].get("oura")
    parts.append("Oura " + (f"{data['coverage'].get('oura', 0)}/{days}" if data["coverage"].get("oura")
                            else f"idle since {o[5:] if o else 'never'}"))
    sa = data["sleep_agreement"]
    tail = ""
    if sa.get("n"):
        sign = "+" if sa["bias_min"] >= 0 else "−"
        tail = f" · Pebble vs Air sleep {sign}{abs(sa['bias_min'])} min (±{sa['typical_gap_min']})"
    return "⌚ Devices (7d): " + " · ".join(parts) + tail
