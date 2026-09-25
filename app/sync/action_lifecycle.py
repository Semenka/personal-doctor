"""Action lifecycle: when does a daily action stop being actionable?

Every dashboard used to treat an unticked action the same way forever: the
tracker Sheet kept 344 rows back to March (today's two tasks at the bottom),
the 21:00 nudge still listed a 13:00 lunch supplement, and the 08:00 WhatsApp
protocol painted not-yet-due tasks 🔴. This module gives each action a due
time and a state so every surface can agree:

  done     — ticked or auto-credited
  open     — not done, its time window has not passed yet
  expired  — not done and its window (+ grace) has passed, or its day is over

Due times come from the advisor's enforced "**When:** 12:45–12:47 PM" line.
Actions without a parseable time expire at the end of their day.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, Optional

# A task stays open this long after its window ends (late but still useful).
GRACE = timedelta(hours=1)

_WHEN_RE = re.compile(r"\*\*When:\*\*\s*(.+)")
_CATEGORY_RE = re.compile(r"Category:\s*\**\s*([A-Za-z][A-Za-z /&-]*)")
_TIME_RE = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*(AM|PM|am|pm)?")


def extract_when(description: str) -> str:
    m = _WHEN_RE.search(description or "")
    return m.group(1).strip().rstrip(" \\") if m else ""


def extract_category(description: str) -> str:
    m = _CATEGORY_RE.search(description or "")
    return m.group(1).strip().lower() if m else ""


def parse_due_end(when: str) -> Optional[str]:
    """End of the action's window as 'HH:MM' (24h), or None if no clock time.

    Handles '12:45–12:47 PM', '9:30–9:42 AM', '13:00–13:05 PM, with lunch',
    '11:35–11:47 PM', 'by 23:15'. The LAST clock time is the end; an AM/PM
    marker applies to it (and is ignored for 24h hours like 13:00).
    """
    if not when:
        return None
    # Only tokens that look like clock times (need ':' or an AM/PM marker),
    # so "2-3 capsules" or "750 mL" never parse as times.
    found = [m for m in _TIME_RE.finditer(when) if m.group(2) or m.group(3)]
    if not found:
        return None
    m = found[-1]
    hour, minute = int(m.group(1)), int(m.group(2) or 0)
    marker = (m.group(3) or "").upper()
    if not marker:
        # "12:45–12:47 PM": the marker sits after the last token; if absent
        # there, look for one anywhere after the last time.
        tail = when[m.end():].upper()
        marker = "PM" if tail.lstrip().startswith("PM") else ("AM" if tail.lstrip().startswith("AM") else "")
    if hour <= 12 and marker == "PM" and hour != 12:
        hour += 12
    if hour == 12 and marker == "AM":
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def enrich(action: Dict[str, Any]) -> Dict[str, Any]:
    """Add when / due_end / category from the description (idempotent)."""
    desc = action.get("description") or ""
    if "when" not in action:
        action["when"] = extract_when(desc)
    if "due_end" not in action:
        action["due_end"] = parse_due_end(action.get("when") or "")
    if not action.get("category"):
        action["category"] = extract_category(desc)
    return action


def action_state(action: Dict[str, Any], day: str, now: datetime) -> str:
    """'done' | 'open' | 'expired' for an action dated ``day`` at ``now``.

    ``now`` must be timezone-aware in the user's zone (or naive local).
    """
    if action.get("done"):
        return "done"
    try:
        d = date.fromisoformat(day)
    except ValueError:
        return "open"
    today = now.date()
    if d < today:
        return "expired"
    if d > today:
        return "open"
    due = action.get("due_end")
    if not due:
        return "open"  # no time given: open until the day ends
    try:
        hh, mm = (int(x) for x in due.split(":"))
    except ValueError:
        return "open"
    deadline = datetime.combine(d, time(hh, mm), tzinfo=now.tzinfo) + GRACE
    # A window ending before 04:00 belongs to the late evening of `day`
    # ("lights out 00:30"), so push it into the next calendar day.
    if hh < 4:
        deadline += timedelta(days=1)
    return "expired" if now > deadline else "open"
