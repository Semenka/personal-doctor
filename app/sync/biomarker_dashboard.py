"""Biomarker dashboard rendering.

Three audiences:
1. **Daily email** — compact set of SVG sparklines (one per key marker), each
   showing time-series with a reference band and an optimal band overlay,
   plus delta + arrow. Designed to render inline (no JS, no external assets).
2. **WhatsApp** — short text summary, e.g.:
   ``📊 6 markers tracked. ⬆ Testosterone 540→612 ng/dL. ⬇ ApoB 102→89 mg/dL.``
3. **/biomarkers page** — full HTML page with one chart per marker, both vs
   time and vs age, grouped by category, with reference ranges + literature
   citations.

All charts are inline SVG; no external library so emails render in any client.
"""
from __future__ import annotations

from html import escape
from typing import Any, Dict, List, Optional

from .biomarker_trends import SeriesPoint, all_series, compute_trend, key_biomarkers, series
from .biomarkers import BY_ID, REGISTRY, Biomarker
from .config import SyncConfig

# Visual constants
_SPARK_W = 200
_SPARK_H = 60
_PAD = 6


def _color_for_trend(direction: str, polarity: str) -> str:
    """Pick a chart line color based on trend direction + polarity."""
    if direction == "improving":
        return "#059669"  # green
    if direction == "declining":
        return "#dc2626"  # red
    return "#6b7280"  # neutral


def _scale(values: List[float], lo: Optional[float], hi: Optional[float]) -> tuple:
    """Pick min/max for plot axis, including reference band if known."""
    candidates = list(values)
    if lo is not None:
        candidates.append(lo)
    if hi is not None:
        candidates.append(hi)
    if not candidates:
        return 0.0, 1.0
    vmin, vmax = min(candidates), max(candidates)
    if vmin == vmax:
        vmin -= 1
        vmax += 1
    pad = (vmax - vmin) * 0.10
    return vmin - pad, vmax + pad


def _svg_sparkline(
    pts: List[SeriesPoint], marker: Biomarker, *, width: int = _SPARK_W,
    height: int = _SPARK_H,
) -> str:
    """Build a self-contained inline SVG sparkline for one marker.

    Reference range: light grey band. Optimal range: light green band.
    Line: blue. Latest point: bigger dot, color-coded by flag.
    """
    if not pts:
        return ""
    vals = [p.value for p in pts]
    vmin, vmax = _scale(
        vals + (
            [marker.ref_low] if marker.ref_low is not None else []
        ) + (
            [marker.ref_high] if marker.ref_high is not None else []
        ),
        marker.ref_low, marker.ref_high,
    )

    def _y(v: float) -> float:
        return height - _PAD - ((v - vmin) / (vmax - vmin)) * (height - 2 * _PAD) if vmax > vmin else height / 2

    def _x(i: int) -> float:
        if len(pts) == 1:
            return width / 2
        return _PAD + (i / (len(pts) - 1)) * (width - 2 * _PAD)

    # Background bands
    bands = []
    if marker.ref_low is not None and marker.ref_high is not None:
        bands.append(
            f'<rect x="0" y="{_y(marker.ref_high):.1f}" width="{width}" '
            f'height="{(_y(marker.ref_low) - _y(marker.ref_high)):.1f}" '
            f'fill="#f3f4f6"/>'
        )
    if marker.optimal_low is not None and marker.optimal_high is not None:
        bands.append(
            f'<rect x="0" y="{_y(marker.optimal_high):.1f}" width="{width}" '
            f'height="{(_y(marker.optimal_low) - _y(marker.optimal_high)):.1f}" '
            f'fill="#dcfce7" fill-opacity="0.5"/>'
        )

    # Line path
    path_d = " ".join(
        f"{'M' if i == 0 else 'L'}{_x(i):.1f},{_y(p.value):.1f}"
        for i, p in enumerate(pts)
    )

    # Latest point (color-coded by flag)
    last = pts[-1]
    flag_color = {
        "high": "#dc2626", "low": "#dc2626", "optimal": "#059669"
    }.get(last.flagged or "", "#2563eb")

    # Build dots for all points, smaller than the latest
    dots = "".join(
        f'<circle cx="{_x(i):.1f}" cy="{_y(p.value):.1f}" r="2" '
        f'fill="#2563eb" fill-opacity="0.6"/>'
        for i, p in enumerate(pts[:-1])
    )
    last_dot = (
        f'<circle cx="{_x(len(pts)-1):.1f}" cy="{_y(last.value):.1f}" r="3.5" '
        f'fill="{flag_color}" stroke="#ffffff" stroke-width="1"/>'
    )

    return (
        f'<svg width="{width}" height="{height}" '
        f'xmlns="http://www.w3.org/2000/svg">'
        f'{"".join(bands)}'
        f'<path d="{path_d}" fill="none" stroke="#2563eb" stroke-width="1.6"/>'
        f"{dots}{last_dot}"
        f'</svg>'
    )


def _trend_arrow(direction: str) -> str:
    return {
        "improving": "&#x2197;",
        "declining": "&#x2198;",
        "stable": "&#x2194;",
    }.get(direction, "")


def _status_pill(marker: Biomarker, value: float, flagged: Optional[str]) -> str:
    """Return an inline-HTML pill showing where this value sits vs reference + optimal.

    Names the authority (e.g. "WHO 2021") so the user knows whether the
    cut-off comes from a lab default or a clinical guideline.
    """
    source = marker.citations[0] if marker.citations else "lab ref"
    if flagged == "high":
        text = f"Above {source} ({value:g} > {marker.ref_high} {marker.unit})"
        bg, fg = "#fee2e2", "#991b1b"
    elif flagged == "low":
        text = f"Below {source} ({value:g} < {marker.ref_low} {marker.unit})"
        bg, fg = "#fee2e2", "#991b1b"
    elif flagged == "optimal":
        bits = []
        if marker.optimal_low is not None:
            bits.append(f"≥{marker.optimal_low}")
        if marker.optimal_high is not None:
            bits.append(f"≤{marker.optimal_high}")
        text = f"Optimal ({' & '.join(bits)} {marker.unit})"
        bg, fg = "#dcfce7", "#166534"
    else:
        # In reference range but not in research-backed optimal band
        text = "In range — not optimal"
        bg, fg = "#fef3c7", "#854d0e"
    return (
        f'<span style="display:inline-block;padding:2px 8px;border-radius:9999px;'
        f'background:{bg};color:{fg};font-size:11px;font-weight:600;'
        f'white-space:nowrap;">{text}</span>'
    )


def _intervention_block_html(interventions: List[Dict[str, Any]]) -> str:
    """Render an amber 'How to improve' card under a flagged marker.

    Empty string if no interventions. Each row is a tight bullet with the
    action in bold, the expected effect in muted text, and the citation as
    a small italic tag — designed to be skim-readable in email.
    """
    if not interventions:
        return ""
    rows = []
    for iv in interventions[:3]:
        rows.append(
            '<li style="margin:3px 0;line-height:1.45;">'
            f'<strong style="color:#1f2937;">{escape(iv["action"])}</strong>'
            f' &mdash; <span style="color:#6b7280;">{escape(iv["expected_effect"])}</span>'
            f' <span style="color:#92400e;font-style:italic;font-size:11px;">'
            f'· {escape(iv["citation"])}</span>'
            '</li>'
        )
    return (
        '<div style="margin-top:10px;padding:8px 12px;background:#fef3c7;'
        'border-left:3px solid #f59e0b;border-radius:4px;">'
        '<div style="font-size:11px;font-weight:700;color:#92400e;'
        'text-transform:uppercase;letter-spacing:0.5px;">'
        '&#x2197; How to improve</div>'
        '<ul style="margin:4px 0 0 0;padding-left:18px;font-size:12px;color:#1f2937;">'
        + "".join(rows)
        + '</ul></div>'
    )


def _ref_annotation(marker: Biomarker) -> str:
    """Compact 'Reference: X-Y · Optimal: A-B' text under each card."""
    parts = []
    if marker.ref_low is not None or marker.ref_high is not None:
        parts.append(
            f"Reference {marker.ref_low if marker.ref_low is not None else '—'}–"
            f"{marker.ref_high if marker.ref_high is not None else '—'} {marker.unit}"
        )
    if marker.optimal_low is not None or marker.optimal_high is not None:
        parts.append(
            f"Optimal {marker.optimal_low if marker.optimal_low is not None else '—'}–"
            f"{marker.optimal_high if marker.optimal_high is not None else '—'} {marker.unit}"
        )
    return " · ".join(parts)


def render_email_dashboard_html(config: SyncConfig, max_markers: int = 10) -> str:
    """Compact biomarker dashboard for the daily email.

    Ranks markers by clinical urgency (out-of-range first, then declining,
    then improving, then stable) so the most-actionable values land on top
    of the user's morning email instead of being buried by registry order.

    Returns an empty string if no biomarker has ≥2 readings.
    """
    from .biomarker_trends import summarize_for_advisor

    ranked = summarize_for_advisor(config, top_n=max_markers)
    if not ranked:
        return ""

    series_map = all_series(config)
    cards = []
    for r in ranked:
        bid = r["id"]
        marker = BY_ID.get(bid)
        pts = series_map.get(bid, [])
        if not (marker and len(pts) >= 2):
            continue
        trend = compute_trend(config, bid)
        if not trend:
            continue
        spark = _svg_sparkline(pts, marker)
        arrow = _trend_arrow(trend.direction)
        last = pts[-1]
        pill = _status_pill(marker, last.value, last.flagged)
        ref_txt = _ref_annotation(marker)
        trend_color = {
            "improving": "#059669", "declining": "#dc2626",
        }.get(trend.direction, "#6b7280")

        delta_str = (
            f"<strong>{trend.first_value:g} &rarr; {trend.last_value:g}</strong> "
            f"{marker.unit} "
            f"<span style=\"color:#6b7280;\">"
            f"({'+' if trend.delta >= 0 else ''}{trend.delta:g}, "
            f"{trend.pct_change:+.1f}%)</span>"
        )

        # Pull interventions for flagged markers — they're in the rank dict.
        interventions_html = _intervention_block_html(r.get("interventions", []))

        cards.append(
            '<div style="border:1px solid #e5e7eb;border-radius:8px;padding:10px 12px;'
            'background:#ffffff;margin-bottom:10px;">'
            # Title row + status pill
            '<div style="display:flex;justify-content:space-between;'
            'align-items:flex-start;gap:8px;flex-wrap:wrap;">'
            f'<div style="font-size:13px;font-weight:700;color:#1f2937;">'
            f'{escape(marker.name_en)}</div>'
            f'{pill}'
            '</div>'
            # Sparkline
            f'<div style="margin:6px 0 4px;">{spark}</div>'
            # Trend + delta
            f'<div style="font-size:12px;color:#374151;">'
            f'<span style="color:{trend_color};font-weight:600;">{arrow} {trend.direction}</span>'
            f' &middot; {delta_str} '
            f'<span style="color:#9ca3af;">({trend.n_points} reading(s) over {trend.days_span}d)</span>'
            '</div>'
            # Reference annotation
            f'<div style="font-size:11px;color:#6b7280;margin-top:3px;">{ref_txt}</div>'
            # Corrective-action block (only renders when interventions present)
            f'{interventions_html}'
            '</div>'
        )

    if not cards:
        return ""

    return (
        '<div style="margin-top:28px;padding:16px 20px;background:#f9fafb;'
        'border-radius:12px;border:1px solid #e5e7eb;">'
        '<h3 style="color:#1e40af;margin:0 0 6px 0;font-size:16px;">'
        '&#x1F4CA; Biomarker dashboard</h3>'
        '<div style="font-size:12px;color:#6b7280;margin-bottom:10px;">'
        'Ranked by urgency — out-of-range first, then biggest moves. '
        'Light grey band on each chart = lab reference range, '
        'light green band = research-backed optimal range. '
        'Out-of-range cards include an evidence-cited "How to improve" panel.'
        '</div>' + "".join(cards)
        + '<div style="font-size:11px;color:#9ca3af;margin-top:8px;text-align:right;">'
        f'<a href="{config.server_url}/biomarkers" style="color:#2563eb;">'
        f'See all charts (vs time + vs age) &rarr;</a></div>'
        '</div>'
    )


_BIOMARKER_GROUPS: Dict[str, List[str]] = {
    "🧬 Semen": ["semen"],
    "🧪 Hormones": ["hormone"],
    "🩸 Blood / hema / vitamins": [
        "metabolic", "lipid", "inflam", "hema", "iron",
        "liver_kidney", "vitamin", "thyroid", "cancer",
    ],
}


def render_whatsapp_summary(
    config: SyncConfig, per_group: int = 3, include_groups: Optional[List[str]] = None,
) -> str:
    """Group-balanced biomarker summary so blood markers aren't crowded out by
    sperm parameter swings.

    Categorizes every ≥2-reading marker into Semen / Hormones / Blood-Hema-
    Vitamins, ranks each group by |%change|, and shows the top ``per_group``
    in each. Result is a multi-section WhatsApp message with explicit labels
    so the user sees blood test trends as prominently as semen analysis.
    """
    keys = key_biomarkers(config, min_points=2)
    if not keys:
        return ""

    # Bucket each ≥2-reading marker by category. We track an "urgency rank"
    # tuple per item so flagged-low/high values surface above purely-large-
    # movers within a group — these are the cases that benefit most from
    # surfacing the corrective-action line beneath.
    buckets: Dict[str, List[tuple]] = {label: [] for label in _BIOMARKER_GROUPS}
    for bid in keys:
        m = BY_ID.get(bid)
        t = compute_trend(config, bid)
        if not (m and t):
            continue
        # Tier 0 = flagged out-of-range, tier 1 = everything else.
        urgency_tier = 0 if t.last_flagged in ("low", "high") else 1
        # Within tier: largest |%change| first.
        rank = (urgency_tier, -abs(t.pct_change))
        for label, cats in _BIOMARKER_GROUPS.items():
            if m.category in cats:
                buckets[label].append((rank, m, t))
                break

    out_lines = [f"📊 Biomarker dashboard — {sum(len(v) for v in buckets.values())} tracked"]
    # Lazy import — avoid a hard dep cycle if interventions module is missing.
    try:
        from .biomarker_interventions import get_interventions
    except Exception:
        get_interventions = None  # type: ignore

    for label, items in buckets.items():
        if include_groups and label not in include_groups:
            continue
        if not items:
            continue
        # Ascending sort: rank tuples are (tier, -|pct|) so smallest tier
        # AND largest |pct| float to the top in natural ascending order.
        items.sort(key=lambda r: r[0])
        out_lines.append("")
        out_lines.append(label)
        for _, m, t in items[:per_group]:
            arrow = {"improving": "⬆", "declining": "⬇", "stable": "⬌"}[t.direction]
            # Green/red status dot — explicit "where am I vs reference":
            #   🔴 out of range (low/high), 🟢 in research-optimal band,
            #   🟡 in reference range but not optimal.
            if t.last_flagged in ("low", "high"):
                dot = "🔴"
            elif t.last_flagged == "optimal":
                dot = "🟢"
            else:
                dot = "🟡"
            flag = ""
            if t.last_flagged in ("low", "high"):
                flag = f" ⚠️{t.last_flagged}"
            out_lines.append(
                f"{dot} {arrow} {m.name_en} {t.first_value:g}→{t.last_value:g} "
                f"{m.unit} ({t.pct_change:+.0f}%){flag}"
            )
            # Single most-impactful corrective action under the flagged trend.
            # Limit text to keep the WhatsApp message scannable on a phone.
            if get_interventions and t.last_flagged in ("low", "high"):
                ivs = get_interventions(t.marker_id, t.last_flagged, limit=1)
                if ivs:
                    iv = ivs[0]
                    # Trim the action to ~80 chars to stay under one wrap on phones.
                    short = iv.action
                    if len(short) > 80:
                        short = short[:77].rstrip() + "…"
                    out_lines.append(f"   → {short} ({iv.citation})")
    return "\n".join(out_lines)


def render_whatsapp_research(config: SyncConfig, day: str, limit: int = 3) -> str:
    """Recent-papers section for the WhatsApp digest, green/red impact-coded.

    Mirrors the email's "📚 Recent papers" block in plain text: a 🟢 dot +
    expected-impact % for a beneficial paper-backed action, the goal, and a
    short title. Empty string if no research for the day.
    """
    from datetime import date as _date

    try:
        from ..research.pipeline import load_research_for_day
    except Exception:
        return ""
    try:
        recs = load_research_for_day(config, _date.fromisoformat(day))
    except Exception:
        recs = []
    if not recs:
        return ""

    goal_emoji = {
        "sperm_motility": "🧬", "sperm_quality": "🧬", "testosterone": "🧪",
        "energy": "⚡", "hrv": "❤️", "sleep": "😴",
    }
    lines = ["📚 Recent papers"]
    for r in recs[:limit]:
        impact = r.get("expected_impact_pct") or 0
        dot = "🟢" if impact > 0 else "🟡"
        ge = goal_emoji.get(r.get("goal", ""), "•")
        action = r.get("action", "")
        if len(action) > 70:
            action = action[:67].rstrip() + "…"
        imp = f"+{impact:.0f}%" if impact > 0 else "—"
        lines.append(f"{dot} {ge} {imp} {action}")
    return "\n".join(lines)


def render_whatsapp_protocol(
    actions: List[Dict[str, Any]],
) -> str:
    """Today's protocol (actions) with green/red completion status for WhatsApp.

    🟢 done · 🔴 still open. Mirrors the email's action representation.
    """
    if not actions:
        return ""
    lines = ["📋 Today's protocol"]
    for i, a in enumerate(actions, 1):
        done = a.get("done")
        dot = "🟢" if done else "🔴"
        title = a.get("title", "?")
        if len(title) > 60:
            title = title[:57].rstrip() + "…"
        lines.append(f"{dot} {i}. {title}")
    return "\n".join(lines)


# ─── Full /biomarkers page (one chart per marker, vs time and vs age) ───

def _full_chart_svg(
    pts: List[SeriesPoint], marker: Biomarker, *, x_axis: str = "time",
    width: int = 480, height: int = 200,
) -> str:
    """Larger SVG chart used on the /biomarkers HTML page."""
    if not pts:
        return ""
    if x_axis == "age":
        usable = [p for p in pts if p.age is not None]
        if not usable:
            return _full_chart_svg(pts, marker, x_axis="time", width=width, height=height)
        xs = [p.age for p in usable]
        ordered = sorted(usable, key=lambda p: p.age)
    else:
        usable = pts
        xs = list(range(len(pts)))
        ordered = pts

    vals = [p.value for p in ordered]
    vmin, vmax = _scale(
        vals + ([marker.ref_low] if marker.ref_low is not None else [])
             + ([marker.ref_high] if marker.ref_high is not None else []),
        marker.ref_low, marker.ref_high,
    )

    pad_left, pad_right, pad_top, pad_bottom = 40, 12, 12, 30

    def _y(v: float) -> float:
        if vmax == vmin:
            return height / 2
        return height - pad_bottom - ((v - vmin) / (vmax - vmin)) * (height - pad_top - pad_bottom)

    if x_axis == "age" and len(ordered) >= 2:
        xmin, xmax = min(xs), max(xs)
    else:
        xmin, xmax = 0, max(len(ordered) - 1, 1)

    def _x(i: int) -> float:
        if x_axis == "age":
            xv = ordered[i].age or 0
            return pad_left + ((xv - xmin) / (xmax - xmin) if xmax > xmin else 0.5) * (width - pad_left - pad_right)
        return pad_left + (i / max(len(ordered) - 1, 1)) * (width - pad_left - pad_right)

    bands = []
    if marker.ref_low is not None and marker.ref_high is not None:
        bands.append(
            f'<rect x="{pad_left}" y="{_y(marker.ref_high):.1f}" '
            f'width="{width - pad_left - pad_right}" '
            f'height="{(_y(marker.ref_low) - _y(marker.ref_high)):.1f}" '
            f'fill="#f3f4f6"/>'
        )
    if marker.optimal_low is not None and marker.optimal_high is not None:
        bands.append(
            f'<rect x="{pad_left}" y="{_y(marker.optimal_high):.1f}" '
            f'width="{width - pad_left - pad_right}" '
            f'height="{(_y(marker.optimal_low) - _y(marker.optimal_high)):.1f}" '
            f'fill="#dcfce7" fill-opacity="0.5"/>'
        )

    path_d = " ".join(
        f"{'M' if i == 0 else 'L'}{_x(i):.1f},{_y(p.value):.1f}"
        for i, p in enumerate(ordered)
    )

    dots = "".join(
        f'<circle cx="{_x(i):.1f}" cy="{_y(p.value):.1f}" r="3" fill="#2563eb"/>'
        for i, p in enumerate(ordered)
    )

    # Y-axis labels
    y_labels = ""
    for v in (vmin, (vmin + vmax) / 2, vmax):
        y_labels += (
            f'<text x="{pad_left - 6}" y="{_y(v):.1f}" font-size="10" '
            f'text-anchor="end" fill="#6b7280">{v:.1f}</text>'
        )

    # X-axis labels
    x_labels = ""
    if x_axis == "time":
        for i, p in enumerate(ordered):
            if i == 0 or i == len(ordered) - 1 or len(ordered) <= 6:
                x_labels += (
                    f'<text x="{_x(i):.1f}" y="{height - 8}" font-size="10" '
                    f'text-anchor="middle" fill="#6b7280">{escape(p.date[:10])}</text>'
                )
    else:
        for i, p in enumerate(ordered):
            if i == 0 or i == len(ordered) - 1 or len(ordered) <= 6:
                age_str = f"{p.age:.1f}y" if p.age is not None else ""
                x_labels += (
                    f'<text x="{_x(i):.1f}" y="{height - 8}" font-size="10" '
                    f'text-anchor="middle" fill="#6b7280">{age_str}</text>'
                )

    line_path = (
        f'<path d="{path_d}" fill="none" stroke="#2563eb" stroke-width="2"/>'
        if path_d else ""
    )
    return (
        f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">'
        f'{"".join(bands)}{line_path}{dots}{y_labels}{x_labels}'
        f'</svg>'
    )


def render_full_html_page(config: SyncConfig) -> str:
    """The full /biomarkers page: every tracked marker, vs time and vs age."""
    keys = key_biomarkers(config, min_points=1)  # show even single-reading markers on the full page
    series_map = all_series(config)

    if not keys:
        return (
            '<!DOCTYPE html><html><head><meta charset="utf-8">'
            '<title>Biomarkers</title></head><body style="font-family:-apple-system;'
            'max-width:800px;margin:0 auto;padding:30px;color:#1a1a1a;">'
            '<h1>📊 Biomarker dashboard</h1>'
            '<p>No biomarkers extracted yet. Drop a blood test PDF or spermogram '
            'into your Google Drive <code>me/health/</code> folder. The next '
            'pipeline run will ingest it and these charts will populate.</p>'
            '</body></html>'
        )

    # Group by category
    by_cat: Dict[str, List[str]] = {}
    for bid in keys:
        marker = BY_ID.get(bid)
        if not marker:
            continue
        by_cat.setdefault(marker.category, []).append(bid)

    cat_labels = {
        "semen": "🧬 Semen analysis",
        "hormone": "🧪 Hormones",
        "metabolic": "🔥 Metabolic",
        "lipid": "❤️ Lipids",
        "inflam": "🔥 Inflammation",
        "hema": "🩸 Hematology",
        "iron": "🩸 Iron",
        "liver_kidney": "🧪 Liver / kidney",
        "vitamin": "☀️ Vitamins / minerals",
        "thyroid": "🩺 Thyroid",
        "cancer": "🎯 Cancer screening",
    }

    sections = []
    for cat in cat_labels:
        ids = by_cat.get(cat, [])
        if not ids:
            continue
        cards = []
        for bid in ids:
            marker = BY_ID[bid]
            pts = series_map.get(bid, [])
            if not pts:
                continue
            trend = compute_trend(config, bid) if len(pts) >= 2 else None
            time_chart = _full_chart_svg(pts, marker, x_axis="time")
            age_chart = (
                _full_chart_svg(pts, marker, x_axis="age")
                if any(p.age is not None for p in pts) else ""
            )
            citations = ", ".join(marker.citations[:2]) if marker.citations else ""
            ref_txt = _ref_annotation(marker)
            last_pt = pts[-1]
            pill = _status_pill(marker, last_pt.value, last_pt.flagged)
            # Pull evidence-cited corrective actions if this marker is flagged.
            # The full page uses the same library as the email so behavior is
            # consistent — no separate prose copy to drift.
            try:
                from .biomarker_interventions import get_interventions

                iv_objs = get_interventions(bid, last_pt.flagged, limit=3)
            except Exception:
                iv_objs = []
            iv_payload = [
                {
                    "action": iv.action,
                    "expected_effect": iv.expected_effect,
                    "citation": iv.citation,
                }
                for iv in iv_objs
            ]
            interventions_html = _intervention_block_html(iv_payload)
            latest_str = (
                f'<span style="font-size:18px;font-weight:700;color:#111827;">'
                f'{last_pt.value:g}</span> '
                f'<span style="color:#6b7280;font-size:13px;">{marker.unit}</span> '
                f'<span style="color:#9ca3af;font-size:11px;">'
                f'({last_pt.date}{" · age " + format(last_pt.age,".1f") + "y" if last_pt.age is not None else ""})'
                f'</span>'
            )
            trend_str = ""
            if trend:
                arrow = _trend_arrow(trend.direction)
                trend_color = {"improving": "#059669", "declining": "#dc2626"}.get(
                    trend.direction, "#6b7280"
                )
                trend_str = (
                    f'<div style="font-size:13px;margin-top:6px;">'
                    f'<span style="color:{trend_color};font-weight:600;">'
                    f'{arrow} {trend.direction}</span>'
                    f' <span style="color:#374151;">'
                    f'{trend.first_value:g} → {trend.last_value:g} '
                    f'({trend.pct_change:+.1f}%, {trend.n_points} readings, '
                    f'{trend.days_span} days)</span></div>'
                )
            else:
                trend_str = (
                    f'<div style="font-size:12px;color:#9ca3af;margin-top:6px;">'
                    f'Single reading — upload a follow-up test to track trend.</div>'
                )
            age_block = (
                f'<div style="flex:1 1 auto;min-width:280px;">'
                f'<div style="font-size:10px;color:#6b7280;text-transform:uppercase;'
                f'letter-spacing:0.5px;">vs age</div>{age_chart}</div>'
                if age_chart else ""
            )
            cards.append(
                '<div style="border:1px solid #e5e7eb;border-radius:8px;'
                'padding:14px;background:#fff;margin-bottom:14px;">'
                # Header: name (FR/EN) + status pill on the right
                '<div style="display:flex;justify-content:space-between;'
                'align-items:flex-start;gap:8px;flex-wrap:wrap;margin-bottom:4px;">'
                f'<div style="font-size:14px;font-weight:700;color:#111827;">'
                f'{escape(marker.name_en)} '
                f'<span style="color:#6b7280;font-weight:400;font-size:13px;">'
                f'/ {escape(marker.name_fr)} ({marker.unit})</span></div>'
                f'{pill}'
                '</div>'
                # Latest value, big
                f'<div style="margin:4px 0 6px;">{latest_str}</div>'
                # Reference / optimal / citation strip
                f'<div style="font-size:11px;color:#6b7280;margin-bottom:8px;">'
                f'{ref_txt}'
                f'{" · " + escape(citations) if citations else ""}</div>'
                # Charts
                f'<div style="display:flex;gap:16px;flex-wrap:wrap;align-items:center;">'
                f'<div style="flex:1 1 auto;min-width:280px;">'
                f'<div style="font-size:10px;color:#6b7280;text-transform:uppercase;'
                f'letter-spacing:0.5px;">vs time</div>{time_chart}</div>'
                f'{age_block}'
                f'</div>'
                f'{trend_str}'
                # Corrective-action block — only when flagged
                f'{interventions_html}'
                f'</div>'
            )
        if cards:
            sections.append(
                f'<h2 style="color:#1e40af;border-bottom:2px solid #dbeafe;'
                f'padding-bottom:6px;margin-top:32px;">{cat_labels[cat]}</h2>'
                + "".join(cards)
            )

    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<title>Biomarker dashboard</title></head>'
        '<body style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif;'
        'max-width:900px;margin:0 auto;padding:30px;color:#1a1a1a;background:#fafafa;">'
        '<h1 style="color:#2563eb;">📊 Biomarker dashboard</h1>'
        '<div style="color:#6b7280;font-size:13px;margin-bottom:24px;">'
        'Light grey band = lab reference range. Light green band = research-backed '
        'optimal range. Tracking only markers with ≥1 reading. New blood tests / '
        'spermograms appear here automatically once dropped in Google Drive.'
        '</div>'
        + "".join(sections)
        + '<div style="margin-top:40px;padding-top:16px;border-top:1px solid #e5e7eb;'
        'color:#9ca3af;font-size:12px;">'
        '<a href="/dashboard" style="color:#2563eb;">← Daily dashboard</a> · '
        '<a href="/genetics" style="color:#2563eb;">Genetics</a>'
        '</div>'
        '</body></html>'
    )
