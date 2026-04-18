"""Cross-report marker trends (M4).

Scans all stored lab_documents that have an ``ai_summary.flags`` list and
parses values out of flag strings like ``"HbA1c 6.3% (ref 4.0-5.7)"`` or
``"LDL 142 mg/dL (high)"``. Groups by marker name and reports the latest
vs previous delta.

Best-effort regex parsing — we don't try to be clinically perfect, we just
surface "your testosterone went 542 → 610" when the user has 2+ labs.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .config import SyncConfig

logger = logging.getLogger("personal-doctor.report_trends")

# Match: "<name> <value><unit?> (...)"
_FLAG_RE = re.compile(
    r"^\s*([A-Za-z][A-Za-z0-9 \-/%]{1,40})\s+"  # marker name (letters, numbers, spaces, %, /, -)
    r"([0-9]+(?:\.[0-9]+)?)"  # numeric value
    r"\s*([%A-Za-z/µμ]+)?",  # optional unit
)


def _load_all_lab_docs(config: SyncConfig) -> List[Dict[str, Any]]:
    """Return all stored lab_document JSONs, newest-first by date."""
    data_dir = config.data_dir
    if not data_dir.exists():
        return []
    docs: List[Dict[str, Any]] = []
    for p in sorted(data_dir.glob("*.json")):
        # Our storage writes {kind}_{date}.json at the ingested root
        name = p.name
        if not any(
            name.startswith(k)
            for k in (
                "blood_test_",
                "urine_test_",
                "genetic_test_",
                "sperm_test_",
                "health_check_",
                "doctor_conclusion_",
                "hormone_panel_",
                "prescription_",
            )
        ):
            continue
        try:
            docs.append(json.loads(p.read_text()))
        except Exception:
            continue
    # Sort by date string descending
    docs.sort(key=lambda d: d.get("date", ""), reverse=True)
    return docs


def _parse_flag(flag: str) -> Tuple[str, float, str] | None:
    """Parse a flag line into (marker_name, numeric_value, unit)."""
    m = _FLAG_RE.match(flag)
    if not m:
        return None
    marker = m.group(1).strip().rstrip(":").lower()
    try:
        value = float(m.group(2))
    except ValueError:
        return None
    unit = (m.group(3) or "").strip()
    # Normalize common marker aliases
    marker = marker.replace("total ", "").replace("serum ", "")
    return marker, value, unit


def compute_marker_trends(config: SyncConfig) -> Dict[str, Dict[str, Any]]:
    """Return {marker: {latest, previous, delta, direction}} across all reports.

    Only markers with ≥2 datapoints are returned. Direction uses >5% change
    as the threshold (arbitrary but sensible for labs).
    """
    docs = _load_all_lab_docs(config)
    if len(docs) < 2:
        return {}

    series: Dict[str, List[Tuple[str, float, str]]] = {}  # marker -> [(date, value, unit)]
    for doc in docs:
        summary = (doc.get("ai_summary") or {})
        flags = summary.get("flags") or []
        doc_date = doc.get("date", "")
        for flag in flags:
            parsed = _parse_flag(flag)
            if not parsed:
                continue
            marker, value, unit = parsed
            series.setdefault(marker, []).append((doc_date, value, unit))

    out: Dict[str, Dict[str, Any]] = {}
    for marker, points in series.items():
        if len(points) < 2:
            continue
        points.sort(key=lambda t: t[0], reverse=True)
        (d1, v1, u1) = points[0]
        (d0, v0, _) = points[1]
        delta = round(v1 - v0, 2)
        pct = (v1 - v0) / v0 if v0 else 0
        if abs(pct) < 0.05:
            direction = "stable"
        elif pct > 0:
            direction = "up"
        else:
            direction = "down"
        unit_str = f" {u1}" if u1 else ""
        out[marker] = {
            "latest": f"{v1}{unit_str} ({d1})",
            "previous": f"{v0}{unit_str} ({d0})",
            "delta": f"{'+' if delta >= 0 else ''}{delta}{unit_str}",
            "direction": direction,
            "points_count": len(points),
        }
    return out
