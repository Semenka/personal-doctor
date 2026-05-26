"""Extract structured biomarker readings from lab/spermogram report text.

Two extraction paths:
1. **AI extractor** (`extract_biomarkers_via_gemini`) — sends the raw report
   text to Gemini with the canonical biomarker registry. Gemini returns a JSON
   list `[{biomarker_id, value, unit, ref_low, ref_high, ...}]`.
2. **Heuristic fallback** (`extract_biomarkers_heuristic`) — when Gemini is
   unavailable, scans the text with the registry's alias list using regex.
   Less accurate, but works offline.

Results are stored as one record per (biomarker, draw-date) in
`data/ingested/biomarkers/results.jsonl` so the dashboard / advisor / trend
analyzer can read them without re-parsing PDFs each morning.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .biomarkers import ALIAS_INDEX, BY_ID, REGISTRY, Biomarker, find_by_alias
from .config import SyncConfig

logger = logging.getLogger("personal-doctor.biomarker_extractor")


@dataclass
class BiomarkerReading:
    biomarker_id: str
    value: float
    unit: str
    date: str  # ISO YYYY-MM-DD — date of the draw / sample, NOT ingestion date
    source_kind: str  # "blood_test" | "sperm_test" | "urine_test" | "hormone_panel" | "health_check"
    source_file: Optional[str] = None
    ref_low: Optional[float] = None
    ref_high: Optional[float] = None
    flagged: Optional[str] = None  # "low" | "high" | "optimal" | None
    age_at_test: Optional[float] = None  # years, only if HEALTH_BIRTHDATE set
    extracted_via: str = "gemini"  # "gemini" | "heuristic"
    extracted_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────────
# Persistence: append-only JSONL, deduped on (biomarker_id, date, source_file)
# ─────────────────────────────────────────────────────────────────────────────

def _store_path(data_dir: Path) -> Path:
    d = data_dir / "biomarkers"
    d.mkdir(parents=True, exist_ok=True)
    return d / "results.jsonl"


def load_all_readings(config: SyncConfig) -> List[Dict[str, Any]]:
    """Load every biomarker reading recorded so far, oldest first by date."""
    p = _store_path(config.data_dir)
    if not p.exists():
        return []
    out: List[Dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    out.sort(key=lambda r: (r.get("date", ""), r.get("biomarker_id", "")))
    return out


def save_readings(
    config: SyncConfig, readings: List[BiomarkerReading]
) -> int:
    """Append readings, deduping on (biomarker_id, date, source_file).

    Returns the number of NEW readings actually written.
    """
    if not readings:
        return 0
    existing = load_all_readings(config)
    seen = {
        (r.get("biomarker_id"), r.get("date"), r.get("source_file"))
        for r in existing
    }
    p = _store_path(config.data_dir)
    new_count = 0
    with p.open("a", encoding="utf-8") as f:
        for r in readings:
            key = (r.biomarker_id, r.date, r.source_file)
            if key in seen:
                continue
            seen.add(key)
            f.write(json.dumps(r.to_dict(), ensure_ascii=False) + "\n")
            new_count += 1
    return new_count


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _age_at(birthdate: Optional[str], when: str) -> Optional[float]:
    if not birthdate:
        return None
    try:
        b = date.fromisoformat(birthdate)
        d = date.fromisoformat(when)
        return round((d - b).days / 365.25, 2)
    except Exception:
        return None


def _flag_value(marker: Biomarker, value: float) -> Optional[str]:
    """Return 'low'|'high'|'optimal'|None given a reading + the marker spec."""
    r_lo, r_hi = marker.ref_low, marker.ref_high
    o_lo, o_hi = marker.optimal_low, marker.optimal_high
    if r_lo is not None and value < r_lo:
        return "low"
    if r_hi is not None and value > r_hi:
        return "high"
    # Inside reference range — refine with optimal
    if (o_lo is not None or o_hi is not None):
        if (o_lo is None or value >= o_lo) and (o_hi is None or value <= o_hi):
            return "optimal"
    return None


# Per-marker sanity ceilings — any reading above these is treated as an OCR /
# extraction error and dropped. Values are deliberately generous (5-10× the
# clinical upper bound) so genuine high readings still pass; only nonsense
# ("Total sperm count = 1680 M" extracted from prose) gets filtered.
_SANITY_CEILING: Dict[str, float] = {
    "semen_volume": 12.0,                 # mL — normal max ~7
    "sperm_concentration": 400.0,         # M/mL — extreme polyzoospermia ~250
    "sperm_total_count": 1500.0,          # M — extreme cases ~1000
    "sperm_progressive_motility": 100.0,  # %
    "sperm_total_motility": 100.0,        # %
    "sperm_normal_morphology": 100.0,
    "sperm_dna_fragmentation": 100.0,
    "sperm_vitality": 100.0,
    "testosterone_total": 2000.0,         # ng/dL — supraphys >1500
    "shbg": 200.0,
    "lh": 50.0,
    "fsh": 50.0,
    "estradiol": 200.0,
    "prolactin": 100.0,                   # >100 = clinical hyperprolactinemia
    "glucose_fasting": 500.0,
    "hba1c": 18.0,
    "ldl": 400.0,
    "hdl": 150.0,
    "triglycerides": 1500.0,
    "apob": 300.0,
    "lp_a": 500.0,
    "crp_hs": 50.0,
    "hemoglobin": 22.0,
    "hematocrit": 65.0,
    "ferritin": 2000.0,
    "vitamin_d_25oh": 200.0,
    "vitamin_b12": 5000.0,
    "homocysteine": 100.0,
    "zinc": 300.0,
    "tsh": 50.0,
    "psa_total": 100.0,
}

# Per-marker sanity floors (small set) — common typos like "0.5 ng/dL" for T.
_SANITY_FLOOR: Dict[str, float] = {
    "testosterone_total": 50.0,
    "hemoglobin": 5.0,
    "hba1c": 3.0,
}


def _within_sanity(marker_id: str, value: float) -> bool:
    """Reject obviously wrong values (Vision OCR, mis-paired units, etc.)."""
    if value < 0:
        return False
    ceiling = _SANITY_CEILING.get(marker_id)
    if ceiling is not None and value > ceiling:
        return False
    floor = _SANITY_FLOOR.get(marker_id)
    if floor is not None and value < floor:
        return False
    return True


# ─────────────────────────────────────────────────────────────────────────────
# AI extractor
# ─────────────────────────────────────────────────────────────────────────────

def _registry_summary_for_prompt() -> str:
    """Compact registry list to teach Gemini the canonical IDs + units."""
    lines = []
    for m in REGISTRY:
        ref = []
        if m.ref_low is not None:
            ref.append(f">{m.ref_low}")
        if m.ref_high is not None:
            ref.append(f"<{m.ref_high}")
        ref_str = " ".join(ref) if ref else "—"
        synonyms = ", ".join(m.aliases[:4])
        lines.append(
            f"- `{m.id}` ({m.unit}) — {m.name_en} / {m.name_fr}. ref: {ref_str}. "
            f"aliases: {synonyms}"
        )
    return "\n".join(lines)


_EXTRACT_SYSTEM = """\
You are a clinical lab parser. Given the raw text of a lab report (blood test,
urine test, sperm analysis / spermogramme, hormone panel, or health check-up),
extract every numeric biomarker reading you can find and map each to one of
the canonical IDs in the registry below.

REGISTRY (canonical_id (unit) — English / French names — reference range):
{registry}

Return STRICT JSON with this shape:
{{
  "draw_date": "YYYY-MM-DD",
  "readings": [
    {{
      "biomarker_id": "<one of the canonical IDs above>",
      "value": <number>,
      "unit": "<unit as printed on the report — we will normalize later>",
      "ref_low": <number or null>,
      "ref_high": <number or null>
    }},
    ...
  ]
}}

Rules:
- Only output biomarkers whose canonical_id appears in the registry above.
  Skip free-form values you cannot map.
- If you find multiple values for the same biomarker (e.g. 2 spermograms in
  one PDF), output the most recent.
- For sperm analysis: motility a+b → sperm_progressive_motility;
  motility a+b+c → sperm_total_motility; concentration in M/mL stays in M/mL
  (not /L). Volume in mL.
- For percentages, drop the trailing %. For HOMA-IR, output the unitless
  number (insulin × glucose / 405 if not directly given).
- Use the report's draw date if printed; if not, use today's date.
- Output JSON only. No markdown fences, no prose.
"""


def extract_biomarkers_via_gemini(
    config: SyncConfig,
    raw_text: str,
    source_kind: str,
    source_file: Optional[str] = None,
    fallback_date: Optional[str] = None,
) -> List[BiomarkerReading]:
    """Extract biomarker readings from raw lab text using the configured LLM.

    Name kept for backwards compatibility — actual provider is determined
    by ``llm_client`` (codex/gpt-5.5 by default).
    """
    if not raw_text or len(raw_text.strip()) < 80:
        return []

    from .llm_client import generate as llm_generate
    from .llm_client import has_credentials

    if not has_credentials():
        logger.warning("LLM has no credentials; skipping biomarker extraction")
        return []

    text_excerpt = raw_text[:8000]
    if len(raw_text) > 8000:
        text_excerpt += "\n... [truncated]"

    prompt = (
        f"Source kind: {source_kind}\n"
        f"Source file: {source_file or '(unknown)'}\n\n"
        f"Report text:\n{text_excerpt}\n\n"
        "Respond with JSON only — no prose, no markdown fences."
    )
    system = _EXTRACT_SYSTEM.format(registry=_registry_summary_for_prompt())

    try:
        text = llm_generate(
            system=system,
            user=prompt,
            max_output_tokens=2500,
            reasoning="low",
            timeout_s=600,
        ).strip()
    except Exception as exc:
        logger.warning(f"LLM extraction failed for {source_file}: {exc}")
        return []

    # Parse JSON
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            logger.warning(f"Could not parse extractor JSON for {source_file}")
            return []
        try:
            parsed = json.loads(m.group(0))
        except Exception:
            return []

    draw_date = parsed.get("draw_date") or fallback_date or date.today().isoformat()
    raw_readings = parsed.get("readings") or []

    import os
    birthdate = os.getenv("HEALTH_BIRTHDATE")
    out: List[BiomarkerReading] = []
    extracted_at = datetime.utcnow().isoformat() + "Z"
    for r in raw_readings:
        bid = (r.get("biomarker_id") or "").strip()
        if not bid or bid not in BY_ID:
            continue
        try:
            value = float(r.get("value"))
        except (TypeError, ValueError):
            continue
        marker = BY_ID[bid]
        if not _within_sanity(bid, value):
            logger.info(f"  drop sanity-fail {bid}={value} from {source_file}")
            continue
        unit = (r.get("unit") or marker.unit or "").strip()
        ref_low = r.get("ref_low")
        ref_high = r.get("ref_high")
        out.append(
            BiomarkerReading(
                biomarker_id=bid,
                value=value,
                unit=unit,
                date=draw_date,
                source_kind=source_kind,
                source_file=source_file,
                ref_low=float(ref_low) if isinstance(ref_low, (int, float)) else None,
                ref_high=float(ref_high) if isinstance(ref_high, (int, float)) else None,
                flagged=_flag_value(marker, value),
                age_at_test=_age_at(birthdate, draw_date),
                extracted_via="gemini",
                extracted_at=extracted_at,
            )
        )
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Heuristic fallback (offline / no API key)
# ─────────────────────────────────────────────────────────────────────────────

# Match: "Testosterone totale: 612.4 ng/dL (ref 264-916)"
_HEURISTIC_LINE = re.compile(
    r"^([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ0-9 \-/().]{2,60}?)\s*[:=]?\s+"
    r"([0-9]+(?:[.,][0-9]+)?)"
    r"\s*([%A-Za-zµμ/]+)?",
    re.MULTILINE,
)


_HEURISTIC_SAFE_KINDS = {
    "blood_test", "urine_test", "sperm_test",
    "hormone_panel", "health_check",
}


def extract_biomarkers_heuristic(
    raw_text: str,
    source_kind: str,
    source_file: Optional[str] = None,
    draw_date: Optional[str] = None,
) -> List[BiomarkerReading]:
    """Regex-based fallback when Gemini isn't available. Less accurate.

    Only runs on structured lab kinds — doctor conclusions and genetic test
    PDFs contain prose ('reduced motility 10-20%') that triggers false
    positives. For those kinds we rely exclusively on the AI extractor.
    """
    if not raw_text or source_kind not in _HEURISTIC_SAFE_KINDS:
        return []
    when = draw_date or date.today().isoformat()
    import os
    birthdate = os.getenv("HEALTH_BIRTHDATE")
    seen: set = set()
    out: List[BiomarkerReading] = []
    extracted_at = datetime.utcnow().isoformat() + "Z"
    for m in _HEURISTIC_LINE.finditer(raw_text):
        label, val_str, unit = m.group(1), m.group(2), m.group(3) or ""
        marker = find_by_alias(label)
        if not marker:
            continue
        if marker.id in seen:
            continue
        try:
            value = float(val_str.replace(",", "."))
        except ValueError:
            continue
        if not _within_sanity(marker.id, value):
            continue
        seen.add(marker.id)
        out.append(
            BiomarkerReading(
                biomarker_id=marker.id,
                value=value,
                unit=unit.strip() or marker.unit,
                date=when,
                source_kind=source_kind,
                source_file=source_file,
                flagged=_flag_value(marker, value),
                age_at_test=_age_at(birthdate, when),
                extracted_via="heuristic",
                extracted_at=extracted_at,
            )
        )
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point used by gdrive_pipeline
# ─────────────────────────────────────────────────────────────────────────────

def extract_and_save(
    config: SyncConfig,
    raw_text: str,
    source_kind: str,
    source_file: Optional[str] = None,
    draw_date: Optional[str] = None,
) -> List[BiomarkerReading]:
    """Extract biomarkers from text and persist them. Returns the new readings.

    Uses Gemini when available, heuristic regex otherwise. Caller decides what
    to do with the returned list (e.g. send WhatsApp alert on big change).
    """
    readings: List[BiomarkerReading] = []
    if config.google_api_key:
        readings = extract_biomarkers_via_gemini(
            config, raw_text, source_kind, source_file, draw_date
        )
    if not readings:
        readings = extract_biomarkers_heuristic(
            raw_text, source_kind, source_file, draw_date
        )
    if readings:
        n = save_readings(config, readings)
        logger.info(
            f"Extracted {len(readings)} biomarker(s), saved {n} new from {source_file}"
        )
    return readings


# ─────────────────────────────────────────────────────────────────────────────
# Vision extractor — for scanned lab reports stored as JPG/PNG (no text layer)
# ─────────────────────────────────────────────────────────────────────────────

def extract_biomarkers_via_vision(
    config: SyncConfig,
    image_path: "Path",
    source_kind: str,
    source_file: Optional[str] = None,
    fallback_date: Optional[str] = None,
) -> List[BiomarkerReading]:
    """Extract biomarkers directly from a scanned lab image using Gemini Vision.

    Many users archive labs as JPG/PNG photographs of paper sheets. Those
    files have no text layer and the regular OCR-fallback in pdf_extract.py
    wouldn't apply. This sends the raw image bytes to the same JSON-output
    extractor used for PDF text, so scanned spermograms and blood panels
    populate the dashboards too.
    """
    from pathlib import Path as _Path

    from .llm_client import generate_with_image as llm_generate_image
    from .llm_client import has_credentials

    if not has_credentials():
        logger.warning("LLM has no credentials; skipping vision extraction")
        return []

    image_path = _Path(image_path)
    if not image_path.exists():
        return []

    system = _EXTRACT_SYSTEM.format(registry=_registry_summary_for_prompt())
    user_text = (
        f"Source kind: {source_kind}\n"
        f"Source file: {source_file or image_path.name}\n\n"
        "This is a scanned image of a lab report (no text layer). Read the "
        "values directly from the image and extract every numeric biomarker "
        "you can match to the registry. If you see a date on the report, use "
        "it as draw_date.\n\n"
        "Respond with JSON only — no prose, no markdown fences."
    )

    try:
        text = llm_generate_image(
            system=system,
            user=user_text,
            image_path=image_path,
            reasoning="medium",
            timeout_s=360,
        ).strip()
    except Exception as exc:
        logger.warning(f"Vision extraction failed for {source_file}: {exc}")
        return []

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return []
        try:
            parsed = json.loads(m.group(0))
        except Exception:
            return []

    draw_date = parsed.get("draw_date") or fallback_date or date.today().isoformat()
    raw_readings = parsed.get("readings") or []

    import os
    birthdate = os.getenv("HEALTH_BIRTHDATE")
    out: List[BiomarkerReading] = []
    extracted_at = datetime.utcnow().isoformat() + "Z"
    for r in raw_readings:
        bid = (r.get("biomarker_id") or "").strip()
        if not bid or bid not in BY_ID:
            continue
        try:
            value = float(r.get("value"))
        except (TypeError, ValueError):
            continue
        marker = BY_ID[bid]
        if not _within_sanity(bid, value):
            logger.info(f"  drop sanity-fail {bid}={value} from {source_file}")
            continue
        unit = (r.get("unit") or marker.unit or "").strip()
        ref_low = r.get("ref_low")
        ref_high = r.get("ref_high")
        out.append(
            BiomarkerReading(
                biomarker_id=bid, value=value, unit=unit,
                date=draw_date, source_kind=source_kind,
                source_file=source_file or image_path.name,
                ref_low=float(ref_low) if isinstance(ref_low, (int, float)) else None,
                ref_high=float(ref_high) if isinstance(ref_high, (int, float)) else None,
                flagged=_flag_value(marker, value),
                age_at_test=_age_at(birthdate, draw_date),
                extracted_via="gemini_vision",
                extracted_at=extracted_at,
            )
        )
    return out


def extract_image_and_save(
    config: SyncConfig,
    image_path: "Path",
    source_kind: str,
    source_file: Optional[str] = None,
    draw_date: Optional[str] = None,
) -> List[BiomarkerReading]:
    """Vision-based equivalent of extract_and_save for scanned labs."""
    readings = extract_biomarkers_via_vision(
        config, image_path, source_kind, source_file, draw_date
    )
    if readings:
        save_readings(config, readings)
        logger.info(
            f"Vision-extracted {len(readings)} biomarker(s) from {source_file}"
        )
    return readings
