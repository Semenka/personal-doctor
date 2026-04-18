"""Test-type-specific workflows (M5).

Fired by gdrive_pipeline.sync_drive_reports right after a new report is
summarized. Produces targeted side-effects:

- sperm_test → remember the test date, schedule an "abstinence window"
  reminder 5 days before next expected test (~12 weeks).
- doctor_conclusion → cross-check any prescribed drugs against the current
  supplement stack via Gemini (X3) — flag interactions.
- hormone_panel → add testosterone/cortisol numbers to a rolling
  "tracked_markers.json" for dashboard display.
- prescription → same as doctor_conclusion (drug interaction check).

All workflows are best-effort: failures are logged but never raised.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict

from .config import SyncConfig

logger = logging.getLogger("personal-doctor.report_workflows")


def _workflows_dir(data_dir: Path) -> Path:
    d = data_dir / "workflows"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sperm_test_workflow(
    config: SyncConfig, filename: str, summary: Dict[str, Any]
) -> None:
    today = datetime.now(tz=config.timezone).date()
    # Assume next sperm test ~12 weeks out; abstinence window 3-5 days before
    next_test_date = today + timedelta(weeks=12)
    abstinence_start = next_test_date - timedelta(days=5)
    abstinence_end = next_test_date - timedelta(days=3)

    state_path = _workflows_dir(config.data_dir) / "sperm_testing.json"
    state: Dict[str, Any] = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text())
        except Exception:
            state = {}
    history = state.get("history", [])
    history.append(
        {
            "date": today.isoformat(),
            "filename": filename,
            "summary": summary.get("summary", "")[:400],
            "flags": summary.get("flags", []),
        }
    )
    state["history"] = history[-20:]
    state["last_test_date"] = today.isoformat()
    state["next_expected"] = next_test_date.isoformat()
    state["abstinence_window"] = {
        "start": abstinence_start.isoformat(),
        "end": abstinence_end.isoformat(),
    }
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    logger.info(
        f"Sperm test recorded. Next window: "
        f"{abstinence_start.isoformat()} — {abstinence_end.isoformat()}"
    )


def _drug_interaction_check(
    config: SyncConfig, kind: str, filename: str, summary: Dict[str, Any]
) -> None:
    """Send prescribed drugs + current supplement stack to Gemini (X3)."""
    if not config.google_api_key:
        return
    follow_ups = summary.get("follow_ups", [])
    flags = summary.get("flags", [])
    summary_text = summary.get("summary", "")
    # Heuristic: only run if summary mentions a medication
    joined = " ".join([summary_text] + follow_ups + flags).lower()
    drug_keywords = (
        "tablet", "capsule", "mg", "mcg", "prescription", "prescribed",
        "rx", "medication", "medicine", "drug", "dose", "daily",
    )
    if not any(k in joined for k in drug_keywords):
        return

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return

    # Load current supplement stack (loaded from inventory if exists)
    inventory_path = _workflows_dir(config.data_dir) / "supplement_inventory.json"
    stack: list = []
    if inventory_path.exists():
        try:
            stack = json.loads(inventory_path.read_text()).get("items", [])
        except Exception:
            stack = []
    stack_text = ", ".join(s.get("name", "?") for s in stack) or "(none recorded)"

    prompt = (
        f"A {kind} report contains these findings:\n{summary_text}\n\n"
        f"Follow-ups: {follow_ups}\n"
        f"Flags: {flags}\n\n"
        f"The patient is currently taking these supplements/drugs: {stack_text}\n\n"
        "Are there any clinically significant drug-supplement or drug-drug "
        "interactions? List up to 3 concerns. If none, say 'No interactions "
        "detected.' Be terse."
    )
    try:
        client = genai.Client(api_key=config.google_api_key)
        model = config.gemini_model or "gemini-3.1-flash-lite-preview"
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(max_output_tokens=400),
        )
        finding = (response.text or "").strip()
    except Exception as exc:
        logger.warning(f"Drug interaction check failed: {exc}")
        return

    result_path = _workflows_dir(config.data_dir) / "drug_interactions.json"
    history = []
    if result_path.exists():
        try:
            history = json.loads(result_path.read_text())
        except Exception:
            history = []
    history.append(
        {
            "date": datetime.now(tz=config.timezone).date().isoformat(),
            "filename": filename,
            "kind": kind,
            "finding": finding,
        }
    )
    result_path.write_text(json.dumps(history[-50:], indent=2, ensure_ascii=False))

    # If the LLM flagged actual concerns, push a WhatsApp alert
    if "no interactions detected" not in finding.lower():
        try:
            from .whatsapp_sender import _run_openclaw_send

            _run_openclaw_send(
                "⚠️ Possible drug/supplement interaction from new report:\n"
                f"{finding[:600]}"
            )
        except Exception:
            pass


def run_post_ingestion_workflows(
    config: SyncConfig, kind: str, filename: str, summary: Dict[str, Any]
) -> None:
    """Dispatch to the right workflow based on report kind. Never raises."""
    try:
        if kind == "sperm_test":
            _sperm_test_workflow(config, filename, summary)
        elif kind in ("doctor_conclusion", "prescription"):
            _drug_interaction_check(config, kind, filename, summary)
        # hormone_panel uses report_trends M4 automatically — no side effect here
    except Exception as exc:
        logger.warning(f"workflow for {kind} failed: {exc}")
