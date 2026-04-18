"""Supplement inventory (X2).

Tracks daily consumption against a user-curated JSON of current supplements.
On each run (07:45, after Oura sync), decrements each item by 1 dose and
alerts when any has <14 days left.

The inventory file is ``data/ingested/workflows/supplement_inventory.json``:

    {
      "items": [
        {"name": "CoQ10 200mg", "remaining_doses": 45, "daily_doses": 1},
        {"name": "Zinc 25mg",    "remaining_doses": 62, "daily_doses": 1}
      ]
    }

Users edit this file directly (or via a future `/inventory` endpoint). Running
out of a key supplement triggers a WhatsApp alert so the user can re-order.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from .config import SyncConfig

logger = logging.getLogger("personal-doctor.supplements")


def _inventory_path(data_dir: Path) -> Path:
    d = data_dir / "workflows"
    d.mkdir(parents=True, exist_ok=True)
    return d / "supplement_inventory.json"


def _load(config: SyncConfig) -> Dict[str, Any]:
    p = _inventory_path(config.data_dir)
    if not p.exists():
        return {"items": []}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {"items": []}


def _save(config: SyncConfig, data: Dict[str, Any]) -> None:
    p = _inventory_path(config.data_dir)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def decrement_and_alert(config: SyncConfig, threshold_days: int = 14) -> List[str]:
    """Decrement each item by its daily_doses, return list of low-stock alerts."""
    data = _load(config)
    items = data.get("items", [])
    if not items:
        return []
    today = datetime.now(tz=config.timezone).date().isoformat()
    last_run = data.get("last_decrement_date")
    if last_run == today:
        return []  # already decremented today
    alerts: List[str] = []
    for item in items:
        daily = item.get("daily_doses", 1) or 1
        remaining = max((item.get("remaining_doses") or 0) - daily, 0)
        item["remaining_doses"] = remaining
        if 0 < remaining <= threshold_days:
            alerts.append(
                f"{item.get('name', '?')}: {remaining} day(s) left"
            )
        elif remaining == 0:
            alerts.append(f"{item.get('name', '?')}: OUT OF STOCK")
    data["last_decrement_date"] = today
    _save(config, data)
    return alerts


def run_supplement_check() -> None:
    """Called by scheduler 07:45 after Oura sync."""
    from .config import load_config

    config = load_config()
    alerts = decrement_and_alert(config, threshold_days=14)
    if not alerts:
        return
    try:
        from .whatsapp_sender import _run_openclaw_send

        _run_openclaw_send(
            "💊 Supplement re-order reminders:\n" + "\n".join(alerts)
        )
        print(f"Sent supplement reminders ({len(alerts)}).")
    except Exception as exc:
        print(f"Supplement WhatsApp failed: {exc}")
