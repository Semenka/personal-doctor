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


def render_email_dashboard_html(config: SyncConfig, max_markers: int = 8) -> str:
    """Compact biomarker dashboard for the daily email.

    Returns an empty string if no biomarker has ≥2 readings (no trend possible).
    """
    keys = key_biomarkers(config, min_points=2)
    if not keys:
        return ""
    series_map = all_series(config)
    cards = []
    for bid in keys[:max_markers]:
        marker = BY_ID.get(bid)
        if not marker:
            continue
        pts = series_map.get(bid, [])
        if len(pts) < 2:
            continue
        trend = compute_trend(config, bid)
        if not trend:
            continue
        spark = _svg_sparkline(pts, marker)
        arrow = _trend_arrow(trend.direction)
        last = pts[-1]
        flag_color = {
            "high": "#dc2626", "low": "#dc2626",
            "optimal": "#059669",
        }.get(last.flagged or "", "#374151")

        delta_str = (
            f"{trend.first_value:g} &rarr; {trend.last_value:g} "
            f"<span style=\"color:#6b7280;\">({'+' if trend.delta >= 0 else ''}"
            f"{trend.delta:g}, {trend.pct_change:+.1f}%)</span>"
        )

        cards.append(
            '<div style="border:1px solid #e5e7eb;border-radius:8px;padding:10px 12px;'
            'background:#ffffff;margin-bottom:10px;">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;">'
            f'<div style="font-size:13px;font-weight:700;color:#1f2937;">'
            f'{escape(marker.name_en)} <span style="color:#6b7280;font-weight:400;">'
            f'({marker.unit})</span></div>'
            f'<div style="font-size:13px;color:{flag_color};font-weight:700;">'
            f'{arrow} {trend.direction}</div></div>'
            f'<div style="margin:6px 0 4px;">{spark}</div>'
            f'<div style="font-size:12px;color:#374151;">{delta_str} '
            f'<span style="color:#9ca3af;">over {trend.days_span}d, '
            f'{trend.n_points} reading(s)</span></div>'
            '</div>'
        )

    if not cards:
        return ""

    return (
        '<div style="margin-top:28px;padding:16px 20px;background:#f9fafb;'
        'border-radius:12px;border:1px solid #e5e7eb;">'
        '<h3 style="color:#1e40af;margin:0 0 12px 0;font-size:16px;">'
        '&#x1F4CA; Biomarker dashboard</h3>'
        '<div style="font-size:12px;color:#6b7280;margin-bottom:10px;">'
        'Light grey = lab reference range. Light green = research-backed optimal range.'
        '</div>' + "".join(cards) + '</div>'
    )


def render_whatsapp_summary(config: SyncConfig, max_lines: int = 5) -> str:
    """One-message WhatsApp summary of biggest biomarker changes."""
    keys = key_biomarkers(config, min_points=2)
    if not keys:
        return ""
    rows = []
    for bid in keys:
        t = compute_trend(config, bid)
        m = BY_ID.get(bid)
        if not (t and m):
            continue
        arrow = {"improving": "⬆", "declining": "⬇", "stable": "⬌"}[t.direction]
        rows.append(
            (abs(t.pct_change), f"{arrow} {m.name_en} {t.first_value:g}→{t.last_value:g} {m.unit} ({t.pct_change:+.0f}%)")
        )
    if not rows:
        return ""
    rows.sort(key=lambda r: r[0], reverse=True)
    lines = [r[1] for r in rows[:max_lines]]
    return "📊 " + str(len(rows)) + " markers tracked.\n" + "\n".join(lines)


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
            ref_str = ""
            if marker.ref_low is not None or marker.ref_high is not None:
                ref_str = f"Ref {marker.ref_low or '—'} – {marker.ref_high or '—'} {marker.unit}"
            opt_str = ""
            if marker.optimal_low is not None or marker.optimal_high is not None:
                opt_str = (
                    f"Optimal {marker.optimal_low or '—'} – {marker.optimal_high or '—'} {marker.unit}"
                )
            trend_str = ""
            if trend:
                arrow = _trend_arrow(trend.direction)
                trend_str = (
                    f'<div style="font-size:13px;color:#374151;margin-top:4px;">'
                    f'{arrow} <strong>{trend.direction}</strong> — '
                    f'{trend.first_value:g} → {trend.last_value:g} '
                    f'({trend.pct_change:+.1f}%, {trend.n_points} readings, '
                    f'{trend.days_span} days)</div>'
                )
            age_block = (
                f'<div><div style="font-size:10px;color:#6b7280;">vs age</div>'
                f'{age_chart}</div>'
                if age_chart else ""
            )
            cards.append(
                '<div style="border:1px solid #e5e7eb;border-radius:8px;'
                'padding:14px;background:#fff;margin-bottom:14px;">'
                f'<div style="font-size:14px;font-weight:700;color:#111827;">'
                f'{escape(marker.name_en)} <span style="color:#6b7280;font-weight:400;">'
                f'/ {escape(marker.name_fr)} ({marker.unit})</span></div>'
                f'<div style="font-size:11px;color:#6b7280;margin-bottom:6px;">'
                f'{ref_str}{" · " if ref_str and opt_str else ""}{opt_str}'
                f'{" · " + escape(citations) if citations else ""}</div>'
                f'<div style="display:flex;gap:16px;flex-wrap:wrap;align-items:center;">'
                f'<div><div style="font-size:10px;color:#6b7280;">vs time</div>{time_chart}</div>'
                f'{age_block}'
                f'</div>'
                f'{trend_str}'
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
