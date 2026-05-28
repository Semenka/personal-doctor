"""Biomarker time-series + trend computation.

Reads `data/ingested/biomarkers/results.jsonl` (written by biomarker_extractor)
and produces:

- `series(marker_id)` — chronologically-ordered points for charting
- `series_by_age(marker_id)` — same, but X is age_at_test instead of date
- `key_biomarkers(min_points, kinds)` — returns only markers with ≥N readings
   AND coming from at least one of the given source_kinds. The user's request:
   include only markers that appear in **≥2 tests** (so a single one-off lab
   doesn't dilute the dashboard) AND grant the spermogram special treatment so
   semen markers surface even with 2 samples.
- `compute_trend(marker_id)` — slope, % change first→last, current-vs-optimal
- `summarize_for_advisor()` — short bullets the advisor LLM can paraphrase
   into the morning Why / nutrition focus / what-to-avoid sections.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from statistics import mean
from typing import Any, Dict, List, Optional, Tuple

from .biomarker_extractor import load_all_readings
from .biomarkers import BY_ID, Biomarker
from .config import SyncConfig


@dataclass
class SeriesPoint:
    date: str
    age: Optional[float]
    value: float
    unit: str
    flagged: Optional[str]
    source_kind: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "date": self.date, "age": self.age, "value": self.value,
            "unit": self.unit, "flagged": self.flagged,
            "source_kind": self.source_kind,
        }


def series(config: SyncConfig, marker_id: str) -> List[SeriesPoint]:
    """Return chronological readings for a marker, oldest first."""
    rows = [r for r in load_all_readings(config) if r.get("biomarker_id") == marker_id]
    rows.sort(key=lambda r: r.get("date", ""))
    return [
        SeriesPoint(
            date=r.get("date", ""), age=r.get("age_at_test"),
            value=float(r.get("value", 0)), unit=r.get("unit", ""),
            flagged=r.get("flagged"), source_kind=r.get("source_kind", ""),
        )
        for r in rows
    ]


def all_series(config: SyncConfig) -> Dict[str, List[SeriesPoint]]:
    """Bucket every reading by canonical biomarker_id, returning per-id chronological lists."""
    rows = load_all_readings(config)
    out: Dict[str, List[SeriesPoint]] = {}
    for r in rows:
        bid = r.get("biomarker_id")
        if not bid:
            continue
        out.setdefault(bid, []).append(
            SeriesPoint(
                date=r.get("date", ""), age=r.get("age_at_test"),
                value=float(r.get("value", 0)), unit=r.get("unit", ""),
                flagged=r.get("flagged"), source_kind=r.get("source_kind", ""),
            )
        )
    for v in out.values():
        v.sort(key=lambda p: p.date)
    return out


def key_biomarkers(
    config: SyncConfig, min_points: int = 2, kinds: Optional[List[str]] = None
) -> List[str]:
    """Return marker IDs with ≥``min_points`` readings (and matching kinds if set).

    Default: keep only markers present in at least 2 tests, regardless of kind —
    this matches the user's request to focus on what they can actually trend.
    Spermogram markers don't get a special bypass because the same threshold
    applies; if they only have 1 sperm test they won't show up until the next
    one is uploaded.
    """
    series_map = all_series(config)
    selected = []
    for bid, pts in series_map.items():
        if len(pts) < min_points:
            continue
        if kinds:
            if not any(p.source_kind in kinds for p in pts):
                continue
        selected.append(bid)
    # Prefer the canonical registry order so the dashboard groups by category
    order = {b.id: i for i, b in enumerate(BY_ID.values())}
    selected.sort(key=lambda b: order.get(b, 999))
    return selected


@dataclass
class Trend:
    marker_id: str
    n_points: int
    first_value: float
    last_value: float
    delta: float
    pct_change: float
    direction: str  # "improving" | "declining" | "stable"
    days_span: int
    slope_per_year: Optional[float]
    last_flagged: Optional[str]
    in_optimal: bool

    def to_dict(self) -> Dict[str, Any]:
        from dataclasses import asdict
        return asdict(self)


def _improving(marker: Biomarker, pct: float) -> str:
    """Map a % change to a direction word that respects the marker's polarity."""
    if abs(pct) < 0.05:
        return "stable"
    rising = pct > 0
    if marker.direction == "higher_better":
        return "improving" if rising else "declining"
    if marker.direction == "lower_better":
        return "improving" if not rising else "declining"
    # mid_optimal: improvement requires comparing distance-to-optimal-midpoint
    # before vs after. We don't have raw values here, so fall back to:
    # "improving" only if motion is plausibly toward the optimal range.
    # The caller has the actual values, so it should pass them via
    # _improving_with_values when accuracy matters.
    return "stable"


def _improving_with_values(
    marker: Biomarker, first: float, last: float
) -> str:
    """Direction respecting polarity and (for mid_optimal) actual distance-to-mid."""
    if first == 0:
        return "stable"
    pct = (last - first) / first
    if abs(pct) < 0.05:
        return "stable"
    if marker.direction == "higher_better":
        return "improving" if last > first else "declining"
    if marker.direction == "lower_better":
        return "improving" if last < first else "declining"
    # mid_optimal: did the value move closer to the optimal midpoint?
    mid: Optional[float] = None
    if marker.optimal_low is not None and marker.optimal_high is not None:
        mid = (marker.optimal_low + marker.optimal_high) / 2
    elif marker.ref_low is not None and marker.ref_high is not None:
        mid = (marker.ref_low + marker.ref_high) / 2
    if mid is None:
        return "stable"
    d_first = abs(first - mid)
    d_last = abs(last - mid)
    if d_last < d_first * 0.95:
        return "improving"
    if d_last > d_first * 1.05:
        return "declining"
    return "stable"


def compute_trend(config: SyncConfig, marker_id: str) -> Optional[Trend]:
    pts = series(config, marker_id)
    marker = BY_ID.get(marker_id)
    if not marker or len(pts) < 2:
        return None
    first, last = pts[0], pts[-1]
    delta = round(last.value - first.value, 3)
    pct = (last.value - first.value) / first.value if first.value else 0.0
    direction = _improving_with_values(marker, first.value, last.value)
    try:
        d0 = date.fromisoformat(first.date)
        d1 = date.fromisoformat(last.date)
        days = (d1 - d0).days
        slope = (delta / days * 365.25) if days > 0 else None
    except Exception:
        days, slope = 0, None
    in_opt = last.flagged == "optimal" or (
        marker.optimal_low is None and marker.optimal_high is None
        and last.flagged is None
    )
    return Trend(
        marker_id=marker_id, n_points=len(pts),
        first_value=first.value, last_value=last.value,
        delta=delta, pct_change=round(pct * 100, 1),
        direction=direction, days_span=days,
        slope_per_year=round(slope, 3) if slope is not None else None,
        last_flagged=last.flagged, in_optimal=in_opt,
    )


def prev_vs_new(config: SyncConfig, marker_id: str) -> Optional[Dict[str, Any]]:
    """Compare the TWO most recent readings of a marker (not first→last).

    Powers the "since your last test" outcome view: how did this marker move
    between the previous test and the new one, and did it cross a WHO/optimal
    boundary? Returns None if fewer than 2 readings exist.
    """
    pts = series(config, marker_id)
    marker = BY_ID.get(marker_id)
    if not marker or len(pts) < 2:
        return None
    prev, new = pts[-2], pts[-1]
    delta = round(new.value - prev.value, 3)
    pct = round(((new.value - prev.value) / prev.value) * 100, 1) if prev.value else 0.0
    direction = _improving_with_values(marker, prev.value, new.value)

    # WHO/ref status before vs after
    def _status(flagged: Optional[str]) -> str:
        if flagged in ("low", "high"):
            return "out_of_range"
        if flagged == "optimal":
            return "optimal"
        return "in_range"

    prev_status = _status(prev.flagged)
    new_status = _status(new.flagged)
    status_change = ""
    if prev_status != new_status:
        status_change = f"{prev_status} → {new_status}"

    return {
        "id": marker_id,
        "name": marker.name_en,
        "category": marker.category,
        "unit": marker.unit,
        "prev_value": prev.value,
        "prev_date": prev.date,
        "new_value": new.value,
        "new_date": new.date,
        "delta": delta,
        "pct_change": pct,
        "direction": direction,
        "prev_flagged": prev.flagged,
        "new_flagged": new.flagged,
        "status_change": status_change,
        "ref_range": (
            [marker.ref_low, marker.ref_high]
            if (marker.ref_low is not None or marker.ref_high is not None) else None
        ),
        "optimal_range": (
            [marker.optimal_low, marker.optimal_high]
            if (marker.optimal_low is not None or marker.optimal_high is not None) else None
        ),
        "ref_source": marker.citations[0] if marker.citations else "lab ref",
        "source_kind": new.source_kind,
    }


def summarize_for_advisor(
    config: SyncConfig, top_n: int = 6
) -> List[Dict[str, Any]]:
    """Return the top N biomarkers worth mentioning in today's advice.

    Ranking: out-of-range markers first, then declining trends, then improving,
    then stable. Within each tier, larger absolute % change wins.
    """
    keys = key_biomarkers(config, min_points=2)
    trends: List[Trend] = []
    for k in keys:
        t = compute_trend(config, k)
        if t:
            trends.append(t)

    def _rank(t: Trend) -> Tuple[int, float]:
        tier = 3
        if t.last_flagged in ("low", "high"):
            tier = 0
        elif t.direction == "declining":
            tier = 1
        elif t.direction == "improving":
            tier = 2
        return (tier, -abs(t.pct_change))

    trends.sort(key=_rank)
    out = []
    for t in trends[:top_n]:
        m = BY_ID.get(t.marker_id)
        if not m:
            continue
        # Source tag for the pill / prompt — the first citation is the
        # authority behind the reference range (WHO 2021, Mach 2020 ESC,
        # Travison 2017, etc.). Falls back to a neutral "lab ref" string.
        ref_source = m.citations[0] if m.citations else "lab ref"

        # Attach evidence-based corrective actions when the marker is
        # flagged. Limited to 3 per marker so the email + WhatsApp don't
        # bloat. Already in (Author Year) citation format that matches
        # SYSTEM_PROMPT, so the LLM can quote them verbatim.
        from .biomarker_interventions import get_interventions

        ivs = get_interventions(t.marker_id, t.last_flagged, limit=3)
        intervention_payload = [
            {
                "action": iv.action,
                "mechanism": iv.mechanism,
                "expected_effect": iv.expected_effect,
                "citation": iv.citation,
                "category": iv.category,
            }
            for iv in ivs
        ]

        out.append({
            "id": t.marker_id,
            "name": m.name_en,
            "category": m.category,
            "first_value": t.first_value,
            "last_value": t.last_value,
            "unit": m.unit,
            "delta": t.delta,
            "pct_change": t.pct_change,
            "direction": t.direction,
            "n_points": t.n_points,
            "days_span": t.days_span,
            "last_flagged": t.last_flagged,
            "optimal_range": (
                [m.optimal_low, m.optimal_high]
                if (m.optimal_low is not None or m.optimal_high is not None)
                else None
            ),
            "ref_range": (
                [m.ref_low, m.ref_high]
                if (m.ref_low is not None or m.ref_high is not None)
                else None
            ),
            "ref_source": ref_source,
            "citations": m.citations[:2],
            "interventions": intervention_payload,
        })
    return out


def alert_changes(
    new_readings: List[Dict[str, Any]], config: SyncConfig, threshold_pct: float = 15.0
) -> List[Dict[str, Any]]:
    """Compare each new reading against the prior reading for the same marker.

    Returns alerts for any marker that moved more than ``threshold_pct`` since
    the previous draw, plus any marker now flagged out-of-range. Used by
    gdrive_pipeline to push an immediate WhatsApp ping when a new lab arrives.
    """
    alerts = []
    series_map = all_series(config)
    for nr in new_readings:
        bid = nr.get("biomarker_id") if isinstance(nr, dict) else nr.biomarker_id
        if not bid:
            continue
        marker = BY_ID.get(bid)
        if not marker:
            continue
        history = series_map.get(bid, [])
        # `history` already includes the new reading after save_readings; we want the prior
        prior = [p for p in history if p.date < (nr.get("date") if isinstance(nr, dict) else nr.date)]
        if not prior:
            # First-ever reading — alert if out-of-range
            flagged = (nr.get("flagged") if isinstance(nr, dict) else nr.flagged)
            if flagged in ("low", "high"):
                alerts.append({
                    "marker_id": bid, "marker_name": marker.name_en,
                    "kind": "out_of_range_first",
                    "value": nr.get("value") if isinstance(nr, dict) else nr.value,
                    "unit": marker.unit, "flagged": flagged,
                })
            continue
        last_prior = prior[-1]
        new_value = nr.get("value") if isinstance(nr, dict) else nr.value
        try:
            pct = ((new_value - last_prior.value) / last_prior.value * 100) if last_prior.value else 0
        except Exception:
            continue
        flagged = (nr.get("flagged") if isinstance(nr, dict) else nr.flagged)
        if abs(pct) >= threshold_pct or flagged in ("low", "high"):
            alerts.append({
                "marker_id": bid, "marker_name": marker.name_en,
                "kind": "delta",
                "from_value": last_prior.value, "to_value": new_value,
                "pct_change": round(pct, 1), "unit": marker.unit,
                "flagged": flagged, "direction":
                    _improving(marker, pct / 100),
            })
    return alerts
