"""AI summarization of newly ingested medical reports.

Called inside `gdrive_pipeline.sync_drive_reports` the moment a new report
lands. Produces a compact structured summary so downstream consumers (advisor
prompt, email banner, severity push) don't have to re-parse the raw text each
time.

Output shape stored in metadata.ai_summary:
    {
        "summary": "<3-bullet summary>",
        "flags": ["HbA1c 6.3% (elevated)", "LDL 142 mg/dL (high)"],
        "severity": "NORMAL|MINOR|MODERATE|URGENT",
        "specialist_referral": bool,
        "follow_ups": ["Repeat lipid panel in 8 weeks", ...],
        "generated_at": "2026-04-17T...",
        "model": "gemini-3.1-flash-lite-preview"
    }
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import SyncConfig

logger = logging.getLogger("personal-doctor.report_summarizer")

_SUMMARY_SYSTEM = """\
You are a medical report summarizer. Given a doctor's report (lab result,
blood test, sperm analysis, genetic test, imaging report, doctor's
conclusion, prescription, or health check-up), produce STRICT JSON with
these keys:

{
  "summary": "<2-4 bullet points, one per line, separated by \\n. What did
   the report find? What did the doctor recommend?>",
  "flags": ["<any out-of-range value with its number and range, e.g.
   'HbA1c 6.3% (ref 4.0-5.7)'>", ...],
  "severity": "NORMAL | MINOR FINDINGS | MODERATE CONCERN | URGENT",
  "specialist_referral": <true if the report indicates a specialist
   should be seen, false otherwise>,
  "follow_ups": ["<concrete next steps with timing>"]
}

Severity rules:
- NORMAL: everything in range, no concerning findings.
- MINOR FINDINGS: marginal out-of-range values, lifestyle adjustment enough.
- MODERATE CONCERN: multiple abnormal values or a notable single finding
  that warrants planned follow-up within 1-4 weeks.
- URGENT: any finding that clinically requires action within 48 h
  (e.g., cancer suspicion, severe anemia, acute infection).

Be terse. Output JSON only. No prose. No markdown. No disclaimers.
"""


def summarize_report_text(
    config: SyncConfig, kind: str, raw_text: str, filename: str = ""
) -> Optional[Dict[str, Any]]:
    """Summarize a report's text via the configured LLM. None on failure."""
    if not raw_text or len(raw_text.strip()) < 80:
        return None

    from .llm_client import generate as llm_generate
    from .llm_client import has_credentials
    from .llm_client import provider_info

    if not has_credentials():
        logger.warning("LLM has no credentials; skipping summarization")
        return None

    # Truncate very long reports (genetic tests can be 50k+ chars)
    max_len = 12000 if kind == "genetic_test" else 8000
    text_excerpt = raw_text
    if len(text_excerpt) > max_len:
        text_excerpt = text_excerpt[:max_len] + "\n... [truncated]"

    prompt = (
        f"Report type: {kind}\n"
        f"Filename: {filename}\n\n"
        f"Report text:\n{text_excerpt}\n\n"
        "Respond with JSON only — no prose, no markdown fences."
    )

    try:
        text = llm_generate(
            system=_SUMMARY_SYSTEM,
            user=prompt,
            max_output_tokens=1200,
            reasoning="low",
            timeout_s=600,
        )
    except Exception as exc:
        logger.warning(f"LLM summarization failed for {filename}: {exc}")
        return None

    # Parse JSON response (Gemini sometimes wraps in ```json fences)
    clean = text.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*", "", clean)
        clean = re.sub(r"\s*```$", "", clean)
    try:
        parsed = json.loads(clean)
    except json.JSONDecodeError:
        # Last-ditch: try to find first {...} block
        m = re.search(r"\{.*\}", clean, re.DOTALL)
        if not m:
            logger.warning(f"Could not parse summary JSON for {filename}")
            return None
        try:
            parsed = json.loads(m.group(0))
        except Exception:
            return None

    parsed["generated_at"] = datetime.utcnow().isoformat() + "Z"
    active_llm = provider_info()
    parsed["model"] = active_llm.get("model")
    parsed["provider"] = active_llm.get("provider")

    # Normalize severity to one of the 4 canonical strings
    sev = (parsed.get("severity") or "").upper().strip()
    if "URGENT" in sev:
        parsed["severity"] = "URGENT"
    elif "MODERATE" in sev:
        parsed["severity"] = "MODERATE CONCERN"
    elif "MINOR" in sev:
        parsed["severity"] = "MINOR FINDINGS"
    elif "NORMAL" in sev:
        parsed["severity"] = "NORMAL"
    else:
        parsed["severity"] = "UNKNOWN"

    # Force-bool
    parsed["specialist_referral"] = bool(parsed.get("specialist_referral"))
    parsed["flags"] = list(parsed.get("flags") or [])[:10]
    parsed["follow_ups"] = list(parsed.get("follow_ups") or [])[:6]
    parsed["summary"] = str(parsed.get("summary") or "").strip()

    return parsed


def _reports_dir(data_dir: Path) -> Path:
    d = data_dir / "new_reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_new_report(
    config: SyncConfig,
    day: date,
    kind: str,
    filename: str,
    drive_file_id: str,
    summary: Optional[Dict[str, Any]],
) -> Path:
    """Record that a new report was ingested on this day.

    Stored in data/ingested/new_reports/YYYY-MM-DD.json as an append-only list.
    Consumed by load_new_reports_for_day() so the advisor prompt can surface
    a "new report detected" banner.
    """
    base = _reports_dir(config.data_dir)
    path = base / f"{day.isoformat()}.json"
    existing: List[Dict[str, Any]] = []
    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except Exception:
            existing = []
    entry: Dict[str, Any] = {
        "kind": kind,
        "filename": filename,
        "drive_file_id": drive_file_id,
        "detected_at": datetime.utcnow().isoformat() + "Z",
    }
    if summary:
        entry["summary"] = summary.get("summary", "")
        entry["flags"] = summary.get("flags", [])
        entry["severity"] = summary.get("severity")
        entry["specialist_referral"] = summary.get("specialist_referral", False)
        entry["follow_ups"] = summary.get("follow_ups", [])
    existing.append(entry)
    path.write_text(json.dumps(existing, indent=2, ensure_ascii=False))
    return path


def load_new_reports_for_day(
    config: SyncConfig, day: date, lookback_days: int = 1
) -> List[Dict[str, Any]]:
    """Load reports detected today (or within ``lookback_days``).

    Returns a list of the structured summary entries saved by save_new_report.
    Used by the advisor prompt builder.
    """
    base = _reports_dir(config.data_dir)
    out: List[Dict[str, Any]] = []
    for i in range(lookback_days + 1):
        d = (day - timedelta(days=i)).isoformat()
        path = base / f"{d}.json"
        if not path.exists():
            continue
        try:
            out.extend(json.loads(path.read_text()))
        except Exception:
            continue
    return out
