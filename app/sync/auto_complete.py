"""Auto-credit daily actions from Oura signals — no manual reply needed.

For 30+ days the agent recorded 0% completion: the user wasn't self-reporting
and the inbound reply path was broken. Rather than nag harder, derive what we
can sense. If today's action is "morning walk" and Oura logged 8,400 steps,
credit it automatically. Only un-sensable actions (supplements, cold shower,
daylight) are left for an optional one-tap confirmation — they're never
auto-failed.

This is what finally produces a real adherence series, which feeds
action_effects → the outcomes-correlation engine.

Wired into the pipeline:
  - after run_oura_sync (morning), and
  - at the evening nudge,
both call ``auto_credit_actions(config, day)``.

Sensing rules (intentionally conservative — only credit on clear evidence):
  movement/walk/cardio/exercise/steps  -> steps >= STEP_THRESHOLD
                                          OR active_minutes >= ACTIVE_MIN
  sleep / bedtime / "lights out"        -> sleep_hours >= SLEEP_HOURS_TARGET
  daylight/light/sun                    -> not sensable (skip)
  cold shower/plunge                    -> not sensable (skip)
  supplement/CoQ10/zinc/magnesium/...   -> not sensable (skip)
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import SyncConfig

logger = logging.getLogger("personal-doctor.auto_complete")

# Thresholds for "did it" inference. Deliberately moderate so we don't
# over-credit; the user can always manually confirm the rest.
STEP_THRESHOLD = 7000          # steps in the day
ACTIVE_MINUTES_THRESHOLD = 25  # medium+high activity minutes
AZM_THRESHOLD = 25             # Fitbit Active Zone Minutes (~AHA 150/wk ÷ 6)
SLEEP_HOURS_TARGET = 6.75      # hours of actual sleep


# Keyword → sensing-category mapping. Checked against title + description.
_MOVEMENT_KW = (
    "walk", "cardio", "exercise", "movement", "zone 2", "zone-2", "z2",
    "run", "jog", "training", "workout", "steps", "aerobic", "resistance",
    "lift", "gym", "bike", "cycling", "swim",
)
_SLEEP_KW = (
    "sleep", "bedtime", "lights out", "lights-out", "lights off",
    "in bed", "wind down", "wind-down", "nsdr", "nap",
)
# Categories we explicitly cannot sense from Oura — never auto-fail these.
_UNSENSABLE_KW = (
    "supplement", "coq10", "ubiquinol", "zinc", "selenium", "magnesium",
    "folate", "methylfolate", "omega", "vitamin", "creatine", "carnitine",
    "cold shower", "cold rinse", "cold plunge", "ice bath",
    "daylight", "sunlight", "sun exposure", "morning light", "bright light",
    "breathing", "box breath", "meditat", "scrotal", "underwear", "laptop",
)


def _load_oura(data_dir: Path, day: str) -> Optional[Dict[str, Any]]:
    p = data_dir / f"daily_{day}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _load_fitbit(data_dir: Path, day: str) -> Optional[Dict[str, Any]]:
    p = data_dir / f"fitbit_{day}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _merge_activity(
    oura: Optional[Dict[str, Any]], fitbit: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """Combine activity signals from both wearables, taking the strongest evidence.

    Fitbit is the authoritative (★) daytime-activity source
    (device_compare._METRIC_PREFERENCE: steps, active minutes, Active Zone
    Minutes), but its same-day file lags — the Google-Health phone bridge often
    only finalizes a day's activity in the *next morning's* backfill. So for
    steps/active-minutes we take the MAX across both devices rather than
    strictly preferring one, which avoids both an empty-Fitbit miss and an
    absent-Oura miss. Active Zone Minutes is Fitbit-only. Sleep stays Oura-led
    (validated for overnight).
    """
    o = oura or {}
    f = fitbit or {}
    return {
        "steps": max(o.get("steps") or 0, f.get("steps") or 0),
        "active_minutes": max(o.get("active_minutes") or 0, f.get("active_minutes") or 0),
        "active_zone_minutes": f.get("active_zone_minutes") or 0,
        "sleep_hours": o.get("sleep_hours") or f.get("sleep_hours") or 0,
    }


def _classify(title: str, description: str) -> str:
    """Return 'movement' | 'sleep' | 'unsensable' | 'unknown'."""
    blob = f"{title} {description}".lower()
    # Unsensable wins if present — we don't want to mis-credit a supplement
    # just because the description happens to mention 'walk'.
    if any(k in blob for k in _UNSENSABLE_KW):
        return "unsensable"
    if any(k in blob for k in _MOVEMENT_KW):
        return "movement"
    if any(k in blob for k in _SLEEP_KW):
        return "sleep"
    return "unknown"


def _activity_confirms(category: str, signals: Dict[str, Any]) -> Tuple[bool, str]:
    """Return (confirmed, evidence_string) for a sensing category.

    ``signals`` is the merged Oura+Fitbit activity dict from ``_merge_activity``.
    """
    if category == "movement":
        steps = signals.get("steps") or 0
        active = signals.get("active_minutes") or 0
        azm = signals.get("active_zone_minutes") or 0
        if steps >= STEP_THRESHOLD:
            return True, f"{int(steps):,} steps"
        if active >= ACTIVE_MINUTES_THRESHOLD:
            return True, f"{int(active)} active min"
        if azm >= AZM_THRESHOLD:
            return True, f"{int(azm)} active-zone min"
        return False, f"only {int(steps):,} steps / {int(active)} active min / {int(azm)} AZM"
    if category == "sleep":
        hrs = signals.get("sleep_hours") or 0
        if hrs >= SLEEP_HOURS_TARGET:
            return True, f"{hrs:.1f} h sleep"
        return False, f"only {hrs:.1f} h sleep"
    return False, ""


def auto_credit_actions(config: SyncConfig, day: Optional[str] = None) -> Dict[str, Any]:
    """Inspect a day's actions, auto-credit the ones the wearables confirm.

    Senses activity from BOTH wearables (Oura + Fitbit) — Fitbit is the
    authoritative daytime-activity source, and re-running this after the Fitbit
    yesterday-backfill lands lets the lagged activity finally credit actions.

    Returns a summary:
      {credited: [{idx,title,evidence}], unsensable: [...], unmet: [...]}
    Marks credited actions done (local JSON + Sheet) with source="oura_auto".
    """
    from .action_tracker import load_actions, mark_action_done_with_sheets

    if day is None:
        day = date.today().isoformat()

    result: Dict[str, Any] = {"day": day, "credited": [], "unsensable": [], "unmet": []}

    actions = load_actions(config.data_dir, day)
    if not actions:
        return result

    oura = _load_oura(config.data_dir, day)
    fitbit = _load_fitbit(config.data_dir, day)
    if not oura and not fitbit:
        logger.info(f"auto_credit: no wearable payload for {day} yet")
        return result
    signals = _merge_activity(oura, fitbit)

    for a in actions:
        if a.get("done"):
            continue  # already credited (manually or earlier auto pass)
        title = a.get("title", "")
        desc = a.get("description", "")
        category = _classify(title, desc)

        if category in ("unsensable", "unknown"):
            result["unsensable"].append({"idx": a["idx"], "title": title})
            continue

        confirmed, evidence = _activity_confirms(category, signals)
        if confirmed:
            ok = mark_action_done_with_sheets(config, day, a["idx"])
            # Tag the source on the local JSON so adherence analytics can
            # distinguish auto-credit from manual confirmation.
            _tag_source(config.data_dir, day, a["idx"], "oura_auto")
            if ok:
                result["credited"].append(
                    {"idx": a["idx"], "title": title, "evidence": evidence}
                )
                logger.info(f"auto-credited '{title[:50]}' — {evidence}")
        else:
            result["unmet"].append(
                {"idx": a["idx"], "title": title, "evidence": evidence}
            )

    return result


def _tag_source(data_dir: Path, day: str, idx: int, source: str) -> None:
    """Annotate the local actions JSON with a completion source tag."""
    from .action_tracker import _actions_path

    path = _actions_path(data_dir, day)
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text())
        for action in data.get("actions", []):
            if action.get("idx") == idx:
                action["source"] = source
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    except Exception as exc:
        logger.warning(f"source tag failed: {exc}")


def render_auto_credit_line(summary: Dict[str, Any]) -> str:
    """Short human line for the evening nudge."""
    credited = summary.get("credited", [])
    unsensable = summary.get("unsensable", [])
    if not credited and not unsensable:
        return ""
    parts = []
    for c in credited:
        parts.append(f"✅ Auto-credited: {c['title'][:45]} ({c['evidence']})")
    if unsensable:
        names = ", ".join(u["title"][:30] for u in unsensable[:2])
        parts.append(f"❓ Can't sense {len(unsensable)}: {names}. Reply the number if you did it.")
    return "\n".join(parts)
