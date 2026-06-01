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
import time
from typing import Any, Dict, List, Optional

from .config import SyncConfig

logger = logging.getLogger("personal-doctor.whatsapp")

# Default recipient (user's own WhatsApp, used self-chat mode in OpenClaw)
DEFAULT_TARGET = os.getenv("WHATSAPP_TARGET", "+393491913903")
# Telegram fallback target (chat id or @username); optional
TELEGRAM_TARGET = os.getenv("TELEGRAM_TARGET", "")

# WhatsApp hard limit is 4096 chars; leave headroom for the four dashboard
# sections (biomarkers + protocol + papers + reply hint) the user wants.
_MAX_MESSAGE_CHARS = 3800

# Substrings in CLI errors that mean "the gateway lost its outbound handler"
# and a kickstart is likely to fix it.
_GATEWAY_HEAL_SIGNATURES = (
    "outbound not configured",
    "no active whatsapp web listener",
    "protocol mismatch",
    "gateway closed",
    "gatewaytransporterror",
)

_gateway_healed_this_process = False


def _openclaw_send_once(
    message: str, target: str, channel: str = "whatsapp", timeout_s: int = 30,
) -> tuple[bool, str]:
    """One raw `openclaw message send`. Returns (ok, stderr_or_empty)."""
    cmd = [
        "openclaw", "message", "send",
        "--channel", channel,
        "--target", target,
        "--message", message,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_s, check=False,
        )
    except FileNotFoundError:
        return False, "openclaw CLI not found on PATH"
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout_s}s"
    except Exception as exc:
        return False, str(exc)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "").strip()[:400]
    return True, ""


def _kickstart_gateway() -> None:
    """Bounce the OpenClaw gateway LaunchAgent to re-register the outbound handler."""
    uid = os.getuid()
    try:
        subprocess.run(
            ["launchctl", "kickstart", "-k", f"gui/{uid}/ai.openclaw.gateway"],
            capture_output=True, text=True, timeout=20, check=False,
        )
        # Give it a few seconds to reconnect the WhatsApp channel.
        time.sleep(8)
    except Exception as exc:
        logger.warning(f"gateway kickstart failed: {exc}")


def _run_openclaw_send(message: str, target: str = DEFAULT_TARGET) -> bool:
    """Deliver a message with retry, gateway self-heal, and channel fallback.

    Order of attempts:
      1. WhatsApp send.
      2. If it failed with a gateway-handler signature, kickstart the gateway
         once (per process) and retry WhatsApp.
      3. If WhatsApp still fails, fall back to Telegram (if TELEGRAM_TARGET set).

    Returns True if any channel accepted the message. Best-effort: never raises.
    """
    global _gateway_healed_this_process

    if not message.strip():
        return False
    if len(message) > _MAX_MESSAGE_CHARS:
        message = message[: _MAX_MESSAGE_CHARS - 3] + "..."

    ok, err = _openclaw_send_once(message, target)
    if ok:
        return True
    logger.warning(f"WhatsApp send failed: {err}")

    # Self-heal: if the gateway lost its outbound handler, kickstart + retry once.
    if (not _gateway_healed_this_process
            and any(sig in err.lower() for sig in _GATEWAY_HEAL_SIGNATURES)):
        logger.warning("Gateway handler error — kickstarting OpenClaw gateway and retrying.")
        _gateway_healed_this_process = True
        _kickstart_gateway()
        ok, err = _openclaw_send_once(message, target)
        if ok:
            logger.info("WhatsApp send succeeded after gateway kickstart.")
            return True
        logger.warning(f"WhatsApp send still failing post-kickstart: {err}")

    # Fallback channel: Telegram (shows connected; separate transport).
    if TELEGRAM_TARGET:
        tg_ok, tg_err = _openclaw_send_once(message, TELEGRAM_TARGET, channel="telegram")
        if tg_ok:
            logger.info("Delivered via Telegram fallback.")
            return True
        logger.warning(f"Telegram fallback failed: {tg_err}")

    return False


def send_via_email_fallback(config: SyncConfig, subject: str, body: str) -> bool:
    """Last-resort delivery: a plain email so a message is never silently dropped.

    Used by callers that want a hard guarantee (e.g. the morning plan). Reuses
    the SMTP path from email_sender.
    """
    if not (config.email_to and config.smtp_host):
        return False
    try:
        from email.mime.text import MIMEText
        import smtplib
        import ssl

        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = config.smtp_user or f"health-advisor@{config.smtp_host}"
        msg["To"] = config.email_to
        port = config.smtp_port or 465
        ctx = ssl.create_default_context()
        if port == 465:
            with smtplib.SMTP_SSL(config.smtp_host, port, context=ctx) as s:
                if config.smtp_user and config.smtp_password:
                    s.login(config.smtp_user, config.smtp_password)
                s.sendmail(msg["From"], [config.email_to], msg.as_string())
        else:
            with smtplib.SMTP(config.smtp_host, port) as s:
                s.ehlo()
                if port != 25:
                    s.starttls(context=ctx)
                    s.ehlo()
                if config.smtp_user and config.smtp_password:
                    s.login(config.smtp_user, config.smtp_password)
                s.sendmail(msg["From"], [config.email_to], msg.as_string())
        logger.info("Delivered via email fallback.")
        return True
    except Exception as exc:
        logger.warning(f"Email fallback failed: {exc}")
        return False


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

    # Biomarker dashboard — semen + hormones + blood/hema/vitamins, with
    # 🟢/🔴/🟡 status dots. Category-balanced so blood markers aren't drowned
    # out by sperm swings.
    try:
        from .biomarker_dashboard import render_whatsapp_summary

        bm = render_whatsapp_summary(config, per_group=3)
        if bm:
            lines.append("")
            lines.append(bm)
    except Exception as exc:
        logger.warning(f"biomarker WhatsApp summary failed: {exc}")

    # Protocol (today's actions) with green/red completion status.
    try:
        from .action_tracker import load_actions_with_sheets
        from .biomarker_dashboard import render_whatsapp_protocol

        actions = load_actions_with_sheets(config, day)
        proto = render_whatsapp_protocol(actions)
        if proto:
            lines.append("")
            lines.append(proto)
    except Exception as exc:
        logger.warning(f"protocol WhatsApp block failed: {exc}")

    # Recent papers — green/red impact-coded.
    try:
        from .biomarker_dashboard import render_whatsapp_research

        papers = render_whatsapp_research(config, day)
        if papers:
            lines.append("")
            lines.append(papers)
    except Exception as exc:
        logger.warning(f"papers WhatsApp block failed: {exc}")

    # Device comparison (Oura vs Fitbit) — only when both wearables synced.
    try:
        from .device_compare import render_compare_whatsapp

        cmp_block = render_compare_whatsapp(config, day)
        if cmp_block:
            lines.append("")
            lines.append(cmp_block)
    except Exception as exc:
        logger.warning(f"device-compare WhatsApp block failed: {exc}")

    lines.append("")
    lines.append('Reply "1" for priority done, "2" for backup, "done" for both.')

    message = "\n".join(lines)
    ok = _run_openclaw_send(message)
    if ok:
        logger.info(f"Sent WhatsApp advice for {day}")
    else:
        # Hard guarantee: the morning plan must reach the user somehow. The
        # full plan also goes out by email separately, but this short-form
        # fallback ensures the WhatsApp digest content isn't lost when both
        # WhatsApp and Telegram are down.
        if send_via_email_fallback(config, f"🩺 Daily Plan — {day}", message):
            logger.info(f"Morning digest delivered via email fallback for {day}")
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
