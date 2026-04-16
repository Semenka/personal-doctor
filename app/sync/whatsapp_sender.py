"""Send daily health summaries via WhatsApp using the local OpenClaw gateway.

OpenClaw runs at 127.0.0.1:18789 with a WhatsApp channel already linked to the
user's number. We shell out to `openclaw message send` rather than reimplementing
the WebSocket RPC + auth + media protocol — the CLI handles all of that.

Two public entry points:
- ``send_whatsapp_advice``: 8:00 AM digest with the 1 priority + 1 backup.
- ``send_whatsapp_evening_nudge``: 21:00 reminder if any actions are still open.

Both are best-effort: any subprocess error is caught and logged, not raised.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
from typing import Any, Dict, List

from .config import SyncConfig

logger = logging.getLogger("personal-doctor.whatsapp")

# Default recipient (user's own WhatsApp, used self-chat mode in OpenClaw)
DEFAULT_TARGET = os.getenv("WHATSAPP_TARGET", "+393491913903")

# Cap at WhatsApp's soft limit for a comfortable single message
_MAX_MESSAGE_CHARS = 1500


def _run_openclaw_send(message: str, target: str = DEFAULT_TARGET) -> bool:
    """Invoke `openclaw message send` to deliver a raw WhatsApp message.

    Returns True on success, False on any error (subprocess, timeout, non-zero exit).
    Errors are logged but never raised — WhatsApp is best-effort alongside email.
    """
    if not message.strip():
        return False
    if len(message) > _MAX_MESSAGE_CHARS:
        message = message[: _MAX_MESSAGE_CHARS - 3] + "..."

    cmd = [
        "openclaw",
        "message",
        "send",
        "--channel",
        "whatsapp",
        "--target",
        target,
        "--message",
        message,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError:
        logger.warning("openclaw CLI not found on PATH — skipping WhatsApp send")
        return False
    except subprocess.TimeoutExpired:
        logger.warning("openclaw message send timed out after 30s")
        return False
    except Exception as exc:
        logger.warning(f"openclaw message send errored: {exc}")
        return False

    if result.returncode != 0:
        logger.warning(
            f"openclaw message send exit={result.returncode} "
            f"stderr={result.stderr.strip()[:300]}"
        )
        return False
    return True


def _extract_priority_and_backup(advice_text: str) -> Dict[str, Any]:
    """Parse the advisor markdown to get the priority + backup + micro-wins.

    The system prompt enforces a specific structure:
      ### Priority (do this one thing)
      1. **[Title]** ...
      ### Backup (if priority won't happen)
      2. **[Title]** ...
      ### 3 Micro-wins ...
      - **[...]** — ...

    This function is tolerant: if the structure is partly off, it falls back to
    whatever numbered actions it can find.
    """
    result: Dict[str, Any] = {"priority": None, "backup": None, "micro_wins": []}

    # Pull the first two numbered bold actions as priority + backup
    action_pattern = re.compile(r"^\s*(\d+)\.\s+\*\*(.+?)\*\*", re.MULTILINE)
    found = action_pattern.findall(advice_text or "")
    if found:
        result["priority"] = found[0][1].strip()
    if len(found) >= 2:
        result["backup"] = found[1][1].strip()

    # Pull bullet micro-wins from the "Micro-wins" section if present
    mw_match = re.search(
        r"###\s*3?\s*Micro[- ]?wins.*?(?=\n###|\Z)",
        advice_text or "",
        re.IGNORECASE | re.DOTALL,
    )
    if mw_match:
        section = mw_match.group(0)
        for m in re.finditer(r"^\s*-\s+\*\*(.+?)\*\*", section, re.MULTILINE):
            result["micro_wins"].append(m.group(1).strip())
        # Fallback: plain bullets without bold
        if not result["micro_wins"]:
            for m in re.finditer(r"^\s*-\s+(.+)$", section, re.MULTILINE):
                line = m.group(1).strip()
                if line and not line.lower().startswith("skip"):
                    result["micro_wins"].append(line[:80])
    result["micro_wins"] = result["micro_wins"][:3]
    return result


def send_whatsapp_advice(config: SyncConfig, advice: Dict[str, Any]) -> bool:
    """Send the morning 8 AM digest to WhatsApp.

    Message shape:
        🩺 Daily Plan — YYYY-MM-DD
        🎯 Priority: <title>
        🔁 Backup: <title>
        Quick wins: <mw1> · <mw2> · <mw3>
        Reply "1" to mark priority done, "2" for backup, "done" for both.

    If the advice is a stale-Oura short-circuit, send a different short message.
    """
    day = advice.get("date", "today")
    model = advice.get("model", "")
    advice_text = advice.get("advice", "")

    if model == "stale-data-short-circuit":
        message = (
            f"🩺 {day} — Oura data stale\n\n"
            "No fresh HRV / sleep data for 3+ days. Today's plan is skipped.\n\n"
            "Fix: open Oura app → manual sync. Charge ring if low."
        )
        return _run_openclaw_send(message)

    parsed = _extract_priority_and_backup(advice_text)
    priority = parsed.get("priority") or "(see email)"
    backup = parsed.get("backup")
    micro_wins = parsed.get("micro_wins", [])

    lines = [f"🩺 Daily Plan — {day}", f"🎯 Priority: {priority}"]
    if backup:
        lines.append(f"🔁 Backup: {backup}")
    if micro_wins:
        # Strip trailing dash/em-dash descriptions for the WhatsApp short form
        trimmed = [w.split("—")[0].strip() for w in micro_wins]
        lines.append("⚡ Quick wins: " + " · ".join(trimmed))
    lines.append("")
    lines.append('Reply "1" for priority done, "2" for backup, "done" for both.')

    message = "\n".join(lines)
    ok = _run_openclaw_send(message)
    if ok:
        logger.info(f"Sent WhatsApp advice for {day}")
    return ok


def send_whatsapp_evening_nudge(
    config: SyncConfig, day: str, actions: List[Dict[str, Any]]
) -> bool:
    """21:00 reminder if any of today's actions are still open."""
    open_actions = [a for a in actions if not a.get("done")]
    if not open_actions:
        return False

    # Only surface the priority + backup (first 2); ignore micro-wins to keep light
    top = open_actions[:2]
    lines = [
        f"🌙 {day} — still open:",
        "",
    ]
    for i, a in enumerate(top, start=1):
        lines.append(f"{i}. {a.get('title', '?')}")
    lines.append("")
    lines.append(
        'Want to knock one out tonight? Reply "1", "2", or "done".'
    )

    message = "\n".join(lines)
    ok = _run_openclaw_send(message)
    if ok:
        logger.info(f"Sent evening nudge for {day} ({len(top)} open)")
    return ok
