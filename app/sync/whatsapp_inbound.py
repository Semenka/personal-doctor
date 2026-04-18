"""WhatsApp inbound message handler (I1, I2, I3).

Parses user replies and turns them into actions. Supports:
- "1" / "2" — mark priority/backup action done
- "done" / "✅" — mark all of today's actions done
- "undo 1" — unmark an action
- "skip" — close today's nudge loop without marking anything
- "swap backup for X" — rewrite tomorrow's backup slot
- "move to evening" — suppress morning nudge, send only evening
- Free text (e.g., "why creatine?") — Q&A via Gemini with today's advice as context

The FastAPI route /whatsapp/inbound calls handle_inbound_message(body) and
returns the response string that the OpenClaw agent will reply on WhatsApp.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import SyncConfig

logger = logging.getLogger("personal-doctor.whatsapp_inbound")


def _today_actions(config: SyncConfig, day: str) -> List[Dict[str, Any]]:
    from .action_tracker import load_actions_with_sheets

    return load_actions_with_sheets(config, day)


def _mark(config: SyncConfig, day: str, idx: int, done: bool) -> bool:
    from .action_tracker import mark_action_done_with_sheets, mark_action_undone_with_sheets

    if done:
        return mark_action_done_with_sheets(config, day, idx)
    return mark_action_undone_with_sheets(config, day, idx)


def _preferences_path(data_dir: Path) -> Path:
    d = data_dir / "preferences"
    d.mkdir(parents=True, exist_ok=True)
    return d / "user_prefs.json"


def _load_prefs(config: SyncConfig) -> Dict[str, Any]:
    p = _preferences_path(config.data_dir)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _save_prefs(config: SyncConfig, prefs: Dict[str, Any]) -> None:
    p = _preferences_path(config.data_dir)
    p.write_text(json.dumps(prefs, indent=2, ensure_ascii=False))


# ── Command parsers ──────────────────────────────────────────────────────
_DONE_WORDS = {"done", "✅", "ok", "completed", "complete", "all", "all done"}
_SKIP_WORDS = {"skip", "pass", "not today"}


def _handle_number(config: SyncConfig, day: str, n: int) -> str:
    actions = _today_actions(config, day)
    if not actions:
        return f"No actions on record for {day}."
    idx = n - 1  # User uses 1-based, internal is 0-based
    if idx < 0 or idx >= len(actions):
        return f"Only {len(actions)} action(s) today. Reply 1 or 2."
    title = actions[idx].get("title", f"Action {n}")
    ok = _mark(config, day, idx, done=True)
    if ok:
        return f"✅ Marked #{n} done: {title[:100]}"
    return f"Couldn't mark #{n} done. Try the email link or Sheet."


def _handle_done_all(config: SyncConfig, day: str) -> str:
    actions = _today_actions(config, day)
    if not actions:
        return "No actions on record for today."
    marked = 0
    for a in actions:
        if not a.get("done"):
            if _mark(config, day, a["idx"], done=True):
                marked += 1
    return f"✅ Marked {marked} action(s) done. Nice work."


def _handle_undo(config: SyncConfig, day: str, n: int) -> str:
    actions = _today_actions(config, day)
    idx = n - 1
    if idx < 0 or idx >= len(actions):
        return f"Only {len(actions)} action(s) today."
    title = actions[idx].get("title", f"Action {n}")
    ok = _mark(config, day, idx, done=False)
    if ok:
        return f"↩️  Unmarked #{n}: {title[:100]}"
    return f"Couldn't unmark #{n}."


def _handle_swap(config: SyncConfig, slot: str, new_title: str) -> str:
    """Persist a one-day override so tomorrow's priority/backup slot uses the new title."""
    prefs = _load_prefs(config)
    overrides = prefs.setdefault("pending_overrides", {})
    if slot not in ("priority", "backup"):
        return 'Slot must be "priority" or "backup".'
    overrides[slot] = {
        "title": new_title.strip(),
        "requested_at": datetime.utcnow().isoformat() + "Z",
    }
    _save_prefs(config, prefs)
    return f"📝 Noted. Tomorrow's {slot} will be: {new_title[:100]}"


def _handle_move_to_evening(config: SyncConfig) -> str:
    prefs = _load_prefs(config)
    prefs["morning_mode"] = "suppressed_once"
    _save_prefs(config, prefs)
    return "🌙 Okay, skipping tomorrow's morning message. Evening nudge will still arrive."


# ── Free-text Q&A (I2) ──────────────────────────────────────────────────
def _qa_answer(config: SyncConfig, day: str, question: str) -> str:
    """Use Gemini with today's advice + Oura + action history as context."""
    if not config.google_api_key:
        return ("Q&A needs GOOGLE_API_KEY. For quick replies, use '1', '2', "
                "'done', 'skip', or 'swap backup for X'.")

    # Gather context: today's advice + Oura + action state
    advice_text = ""
    advisor_file = config.data_dir / "advisor" / f"daily_advice_{day}.json"
    if advisor_file.exists():
        try:
            advice_text = json.loads(advisor_file.read_text()).get("advice", "")[:4000]
        except Exception:
            pass

    oura_text = ""
    oura_file = config.data_dir / f"daily_{day}.json"
    if oura_file.exists():
        try:
            oura_text = oura_file.read_text()[:2000]
        except Exception:
            pass

    actions = _today_actions(config, day)
    action_summary = "\n".join(
        f"- {'[DONE]' if a.get('done') else '[OPEN]'} {a.get('title', '?')}"
        for a in actions
    )

    system = (
        "You are the user's personal health advisor. They are texting you via "
        "WhatsApp with a short question. Context you have: today's full daily "
        "plan, today's Oura data, and their action completion state. "
        "Answer in 1-3 sentences. Be direct. Cite today's data. "
        "No disclaimers. No 'consult your doctor'. "
        "If a fact isn't in the context, say so briefly."
    )
    user = (
        f"Today ({day}) daily plan:\n{advice_text}\n\n"
        f"Today's Oura:\n{oura_text}\n\n"
        f"Actions state:\n{action_summary}\n\n"
        f"User question: {question}"
    )
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=config.google_api_key)
        model = config.gemini_model or "gemini-3.1-flash-lite-preview"
        response = client.models.generate_content(
            model=model,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                max_output_tokens=400,
            ),
        )
        return (response.text or "").strip() or "(no response from model)"
    except Exception as exc:
        logger.warning(f"Q&A Gemini call failed: {exc}")
        return f"Q&A failed: {exc}"


# ── Main entry ──────────────────────────────────────────────────────────
def handle_inbound_message(
    config: SyncConfig, body: str, from_number: str | None = None
) -> str:
    """Parse a WhatsApp inbound message and return the reply string."""
    if not body or not body.strip():
        return ""
    msg = body.strip()
    low = msg.lower().strip()
    day = datetime.now(tz=config.timezone).date().isoformat()

    # "done" / "all"
    if low in _DONE_WORDS:
        return _handle_done_all(config, day)
    # "skip"
    if low in _SKIP_WORDS:
        return "Got it. No change recorded. I'll check in tomorrow at 8 AM."
    # pure digit "1" / "2" / "3"
    if low.isdigit():
        return _handle_number(config, day, int(low))
    # "undo 1"
    m = re.match(r"^\s*undo\s+(\d+)\s*$", low)
    if m:
        return _handle_undo(config, day, int(m.group(1)))
    # "swap priority|backup for <something>"
    m = re.match(r"^\s*swap\s+(priority|backup)\s+(?:for|to|with)\s+(.+)$", low)
    if m:
        return _handle_swap(config, m.group(1), m.group(2))
    # "move to evening"
    if re.search(r"\bmove (?:me )?to evening\b", low) or "suppress morning" in low:
        return _handle_move_to_evening(config)
    # Status command
    if low in ("status", "today", "?"):
        actions = _today_actions(config, day)
        done = sum(1 for a in actions if a.get("done"))
        if not actions:
            return f"{day}: no plan on record yet."
        lines = [f"{day}: {done}/{len(actions)} done"]
        for i, a in enumerate(actions, 1):
            tick = "✅" if a.get("done") else "⬜"
            lines.append(f"  {tick} {i}. {a.get('title', '?')[:60]}")
        return "\n".join(lines)

    # Fall through to Q&A (I2)
    return _qa_answer(config, day, msg)
