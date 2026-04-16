"""Track daily action items: parse from AI advice, mark done/undone, history.

Provides both local-JSON functions (original) and Sheets-aware wrappers that
try Google Sheets first and fall back to local JSON on any error.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List

from .config import SyncConfig

logger = logging.getLogger("personal-doctor.actions")

ACTION_PATTERN = re.compile(
    # Title: content between the FIRST pair of ** (excluding * chars inside)
    # so "1. **Title** [Easy] | Category: **Supplement**" yields title="Title"
    # Body: everything after the first **...** until the next action or section.
    r"(\d+)\.\s+\*\*([^*]+?)\*\*(.*?)(?=\n\s*\d+\.\s+\*\*|\n###|\Z)",
    re.DOTALL,
)


def _actions_dir(data_dir: Path) -> Path:
    d = data_dir / "actions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _actions_path(data_dir: Path, day: str) -> Path:
    return _actions_dir(data_dir) / f"actions_{day}.json"


def parse_actions(advice_text: str, day: str) -> List[Dict[str, Any]]:
    """Extract numbered bold action items from the AI-generated markdown."""
    matches = ACTION_PATTERN.findall(advice_text)
    actions = []
    for i, (num, title, description) in enumerate(matches):
        actions.append({
            "idx": i,
            "title": title.strip(),
            "description": description.strip(),
            "done": False,
            "done_at": None,
        })
    return actions


def save_actions(data_dir: Path, day: str, actions: List[Dict[str, Any]]) -> Path:
    """Save parsed actions to JSON."""
    path = _actions_path(data_dir, day)
    payload = {"date": day, "actions": actions}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return path


def load_actions(data_dir: Path, day: str) -> List[Dict[str, Any]]:
    """Load actions for a given day. Returns empty list if not found."""
    path = _actions_path(data_dir, day)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        return data.get("actions", [])
    except (json.JSONDecodeError, OSError):
        return []


def mark_action_done(data_dir: Path, day: str, idx: int) -> bool:
    """Mark action at index as done. Returns True if successful."""
    path = _actions_path(data_dir, day)
    if not path.exists():
        return False
    data = json.loads(path.read_text())
    actions = data.get("actions", [])
    for action in actions:
        if action["idx"] == idx:
            action["done"] = True
            action["done_at"] = datetime.utcnow().isoformat() + "Z"
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            return True
    return False


def mark_action_undone(data_dir: Path, day: str, idx: int) -> bool:
    """Unmark action at index. Returns True if successful."""
    path = _actions_path(data_dir, day)
    if not path.exists():
        return False
    data = json.loads(path.read_text())
    actions = data.get("actions", [])
    for action in actions:
        if action["idx"] == idx:
            action["done"] = False
            action["done_at"] = None
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            return True
    return False


def load_action_history(
    data_dir: Path, num_days: int = 7
) -> List[Dict[str, Any]]:
    """Load the last N days of action data, most recent first."""
    today = date.today()
    history = []
    for i in range(num_days):
        day = (today - timedelta(days=i)).isoformat()
        actions = load_actions(data_dir, day)
        if actions:
            done_count = sum(1 for a in actions if a.get("done"))
            history.append({
                "date": day,
                "actions": actions,
                "completion_rate": done_count / len(actions) if actions else 0,
            })
    return history


def compute_streaks(data_dir: Path) -> Dict[str, int]:
    """Compute consecutive-day streaks of action completion."""
    today = date.today()
    any_streak = 0
    all_streak = 0

    # Walk backwards from yesterday (today's actions are still in progress)
    for i in range(1, 60):
        day = (today - timedelta(days=i)).isoformat()
        actions = load_actions(data_dir, day)
        if not actions:
            break
        done_count = sum(1 for a in actions if a.get("done"))
        if done_count > 0:
            any_streak += 1
        else:
            break

    for i in range(1, 60):
        day = (today - timedelta(days=i)).isoformat()
        actions = load_actions(data_dir, day)
        if not actions:
            break
        if all(a.get("done") for a in actions):
            all_streak += 1
        else:
            break

    return {
        "any_action": any_streak,
        "all_actions": all_streak,
    }


# ─── Sheets-aware wrappers ─────────────────────────────────────────────────
# Try Google Sheets first, fall back to local JSON on any error.

def load_actions_with_sheets(
    config: SyncConfig, day: str
) -> List[Dict[str, Any]]:
    """Load actions from Google Sheet (falls back to local JSON)."""
    try:
        from .sheets_tracker import read_action_status, sync_sheet_to_local

        actions = read_action_status(config, day)
        if actions:
            # Keep local JSON in sync for offline use / streaks
            sync_sheet_to_local(config, day)
            return actions
    except Exception as exc:
        logger.warning(f"Sheet read failed, using local: {exc}")
    return load_actions(config.data_dir, day)


def load_action_history_with_sheets(
    config: SyncConfig, num_days: int = 7
) -> List[Dict[str, Any]]:
    """Load action history from Google Sheet (falls back to local JSON)."""
    try:
        from .sheets_tracker import read_action_history as sheet_history

        history = sheet_history(config, num_days)
        if history:
            return history
    except Exception as exc:
        logger.warning(f"Sheet history read failed, using local: {exc}")
    return load_action_history(config.data_dir, num_days)


def mark_action_done_with_sheets(
    config: SyncConfig, day: str, idx: int
) -> bool:
    """Mark action done in both Google Sheet and local JSON."""
    local_ok = mark_action_done(config.data_dir, day, idx)
    try:
        from .sheets_tracker import mark_action_done_sheet

        mark_action_done_sheet(config, day, idx)
    except Exception as exc:
        logger.warning(f"Sheet mark-done failed (local OK={local_ok}): {exc}")
    return local_ok


def mark_action_undone_with_sheets(
    config: SyncConfig, day: str, idx: int
) -> bool:
    """Unmark action in both Google Sheet and local JSON."""
    local_ok = mark_action_undone(config.data_dir, day, idx)
    try:
        from .sheets_tracker import mark_action_undone_sheet

        mark_action_undone_sheet(config, day, idx)
    except Exception as exc:
        logger.warning(f"Sheet unmark failed (local OK={local_ok}): {exc}")
    return local_ok
