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
import shutil
import subprocess
import time
from pathlib import Path
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

# The gateway self-heal used to be a once-per-process boolean latch. The
# service process lives for weeks, so the first heal consumed it forever:
# on 2026-08-21 21:00 a kickstart fired (and that night didn't help), after
# which the gateway failures of 08-22, 08-26 and 08-27 — 9 failed sends, two
# lost morning digests — never triggered another attempt, even though
# kickstart has a proven record against the 1006 transport error (06-08,
# 06-15). A time throttle keeps the original intent (no kickstart loops
# within one burst of sends) while allowing recovery across days.
_GATEWAY_HEAL_MIN_INTERVAL_S = 30 * 60
_gateway_last_heal_ts: float = 0.0

_OPENCLAW_PATH_CACHE: Optional[str] = None


def _openclaw_runs(candidate: str) -> bool:
    """True if this openclaw actually executes (right Node major/minor, deps present)."""
    try:
        probe = subprocess.run(
            [candidate, "--version"],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except Exception:
        return False
    return probe.returncode == 0


def _resolve_openclaw_bin() -> Optional[str]:
    """Absolute path to an openclaw CLI that RUNS, or None.

    A bare ``which openclaw`` is NOT enough. launchd pins the service to a
    single nvm bin dir (see com.personal-doctor.plist PATH), and when the user
    upgrades node, that pin goes stale: PATH still resolves openclaw to the old
    version's shim, whose ``#!/usr/bin/env node`` then picks the OLD node and
    openclaw refuses to start ("Node.js >=24.15.0 is required (current:
    v24.14.1)"). That killed every WhatsApp send for 12+ days while email kept
    working, so it never looked like an outage.

    So we don't just find a file — we probe each candidate with ``--version``
    and take the first that exits 0. That also self-heals the next time node
    moves, without editing the plist.
    """
    global _OPENCLAW_PATH_CACHE
    if _OPENCLAW_PATH_CACHE and Path(_OPENCLAW_PATH_CACHE).exists():
        return _OPENCLAW_PATH_CACHE

    candidates: list[str] = []
    # 1) Explicit override wins.
    if env := os.getenv("OPENCLAW_BIN"):
        candidates.append(env)
    # 2) The installer's own shim — it hardcodes the node it was installed
    #    against, so it survives a stale PATH.
    candidates.append(str(Path.home() / ".openclaw/bin/openclaw"))
    # 3) Whatever PATH offers (correct in an interactive shell).
    if found := shutil.which("openclaw"):
        candidates.append(found)
    # 4) Every nvm-installed node version, newest first.
    nvm_root = Path.home() / ".nvm/versions/node"
    if nvm_root.is_dir():
        for c in sorted(nvm_root.glob("*/bin/openclaw"), reverse=True):
            candidates.append(str(c))
    # 5) Other known absolute locations.
    candidates += ["/opt/homebrew/bin/openclaw", "/usr/local/bin/openclaw"]

    seen: set[str] = set()
    for c in candidates:
        if not c or c in seen:
            continue
        seen.add(c)
        if Path(c).exists() and _openclaw_runs(c):
            if c != shutil.which("openclaw"):
                logger.info(f"Resolved openclaw to {c} (PATH copy unusable).")
            _OPENCLAW_PATH_CACHE = c
            return c
    return None


def _openclaw_send_once(
    message: str, target: str, channel: str = "whatsapp", timeout_s: int = 30,
) -> tuple[bool, str]:
    """One raw `openclaw message send`. Returns (ok, stderr_or_empty)."""
    binary = _resolve_openclaw_bin()
    if not binary:
        return False, "no runnable openclaw CLI found (checked PATH, ~/.openclaw, nvm)"
    cmd = [
        binary, "message", "send",
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
    global _gateway_last_heal_ts

    if not message.strip():
        return False
    if len(message) > _MAX_MESSAGE_CHARS:
        message = message[: _MAX_MESSAGE_CHARS - 3] + "..."

    ok, err = _openclaw_send_once(message, target)
    if ok:
        return True
    logger.warning(f"WhatsApp send failed: {err}")

    # Self-heal: if the gateway lost its outbound handler, kickstart + retry —
    # at most once per _GATEWAY_HEAL_MIN_INTERVAL_S across the process.
    heal_due = (time.time() - _gateway_last_heal_ts) >= _GATEWAY_HEAL_MIN_INTERVAL_S
    if (heal_due
            and any(sig in err.lower() for sig in _GATEWAY_HEAL_SIGNATURES)):
        logger.warning("Gateway handler error — kickstarting OpenClaw gateway and retrying.")
        _gateway_last_heal_ts = time.time()
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
        # smtplib defaults to the global socket timeout, which is None — a
        # stalled connection would block this scheduled job forever and hold
        # an APScheduler worker thread indefinitely.
        timeout_s = 30
        if port == 465:
            with smtplib.SMTP_SSL(
                config.smtp_host, port, context=ctx, timeout=timeout_s
            ) as s:
                if config.smtp_user and config.smtp_password:
                    s.login(config.smtp_user, config.smtp_password)
                s.sendmail(msg["From"], [config.email_to], msg.as_string())
        else:
            with smtplib.SMTP(config.smtp_host, port, timeout=timeout_s) as s:
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


def _action_when_and_step(advice_text: str, number: int) -> Dict[str, str]:
    """Pull the **When:** time and step a) for numbered action ``number``.

    The phone digest used to show titles only ("Lunch Antioxidant Motility
    Stack"), which forced a trip to the email to learn WHAT to take and WHEN.
    The advisor's enforced structure carries both, so surface them inline.
    """
    out = {"when": "", "step": ""}
    block = re.search(
        rf"^\s*{number}\.\s+\*\*.+?(?=^\s*\d+\.\s+\*\*|^###|\Z)",
        advice_text or "",
        re.MULTILINE | re.DOTALL,
    )
    if not block:
        return out
    text = block.group(0)
    when = re.search(r"\*\*When:\*\*\s*(.+)", text)
    if when:
        out["when"] = when.group(1).strip().rstrip(" \\")
    step = re.search(r"^\s*a\)\s*(.+)", text, re.MULTILINE)
    if step:
        out["step"] = step.group(1).strip().rstrip(" \\")[:140]
    return out


def _yesterday_activity_line(config: SyncConfig, day: str) -> str:
    """One truthful wearable line for the morning digest.

    At 08:00 today's file is an upload-lag stub ("Steps: 12"), which is
    noise. Yesterday's file is finalized by the 07:40 backfill, so that is the
    number that actually informs the morning. Includes sleep only when a
    device measured it (sporadic ring or bridge sleep) — zeros mean absent.
    """
    import json
    from datetime import date, timedelta

    try:
        yday = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    except ValueError:
        return ""
    fb: Dict[str, Any] = {}
    oura: Dict[str, Any] = {}
    for name, target in ((f"fitbit_{yday}.json", "fb"), (f"daily_{yday}.json", "oura")):
        path = config.data_dir / name
        if path.exists():
            try:
                loaded = json.loads(path.read_text())
                if target == "fb":
                    fb = loaded
                else:
                    oura = loaded
            except Exception:
                pass
    steps = fb.get("steps") or 0
    if steps < 500 and not (oura.get("sleep_hours") or 0):
        return ""
    parts = []
    if steps >= 500:
        parts.append(f"{steps:,} steps")
        if fb.get("active_minutes"):
            parts.append(f"{fb['active_minutes']} active min")
        if fb.get("active_zone_minutes"):
            parts.append(f"AZM {fb['active_zone_minutes']}")
    sleep = fb.get("sleep_hours") or oura.get("sleep_hours") or 0
    if sleep:
        src = "ring" if not fb.get("sleep_hours") and oura.get("sleep_hours") else "watch"
        parts.append(f"sleep {sleep:.1f}h ({src})")
    if oura.get("hrv"):
        parts.append(f"HRV {oura['hrv']:.0f}")
    return "⌚ Yesterday: " + " · ".join(parts) if parts else ""


def _completion_footer(config: SyncConfig) -> str:
    """Truthful 'how to mark done' line: the tracker Sheet (one tap on the
    phone), with the reply flow mentioned only if it is actually bound."""
    try:
        from .sheets_tracker import get_tracker_sheet_url_cached

        url = get_tracker_sheet_url_cached(config)
    except Exception:
        url = None
    if url:
        return f"✅ Tick actions done (1 tap): {url}"
    return "✅ Mark actions done via the buttons in today's email."


def send_whatsapp_advice(config: SyncConfig, advice: Dict[str, Any]) -> bool:
    """Send the morning 8 AM digest to WhatsApp.

    Message shape:
        🩺 Daily Plan — YYYY-MM-DD
        🎯 Priority: <title> — <when>
           ↳ <step a>
        🔁 Backup: <title> — <when>
           ↳ <step a>
        ⚡ Quick wins: <mw1> · <mw2> · <mw3>
        📊 biomarkers · 📋 protocol · 📚 papers
        ⌚ Yesterday: <finalized steps / active min / sleep if measured>
        ✅ Tick actions done (1 tap): <tracker Sheet URL>

    If the advice is a stale-wearable short-circuit, send a different short message.
    """
    day = advice.get("date", "today")
    model = advice.get("model", "")
    advice_text = advice.get("advice", "")

    if model == "stale-data-short-circuit":
        message = (
            f"🩺 {day} — Fitbit Air data stale\n\n"
            "No fresh HRV / sleep data for 3+ days. Today's plan is skipped.\n\n"
            "Fix: open Fitbit and Health Connect, then confirm data sharing is active."
        )
        return _run_openclaw_send(message)

    parsed = _extract_priority_and_backup(advice_text)
    priority = parsed.get("priority") or "(see email)"
    backup = parsed.get("backup")
    micro_wins = parsed.get("micro_wins", [])

    # Priority/backup carry their time + first concrete step so the phone
    # digest is actionable on its own (titles alone forced a trip to email).
    def _action_lines(icon: str, label: str, title: str, number: int) -> List[str]:
        d = _action_when_and_step(advice_text, number)
        head = f"{icon} {label}: {title}"
        if d["when"]:
            head += f" — {d['when']}"
        out = [head]
        if d["step"]:
            out.append(f"   ↳ {d['step']}")
        return out

    lines = [f"🩺 Daily Plan — {day}"] + _action_lines("🎯", "Priority", priority, 1)

    # If Fitbit Air hadn't synced by 8 AM, surface a one-line nudge on the phone channel
    # too (the full plan below still ships from labs + protocol). Drives the user
    # to restore the phone-side bridge the same day.
    summary = advice.get("context_summary") or {}
    stale_days = summary.get("fitbit_stale_days") or 0
    silent_days = summary.get("watch_silent_days") or 0
    if stale_days:
        lines.insert(1, f"⚠️ Fitbit Air not synced ({stale_days}d) — check Fitbit + Health Connect")
    elif silent_days:
        # Phone steps still arrive, so the old check stays green; the watch
        # itself is silent. Name it and give the one-line fix.
        lines.insert(
            1,
            f"⚠️ Watch silent {silent_days}d (phone steps only, no sleep/HRV) — "
            "Fitbit app → Health Connect → allow sleep+heart, or run fitbit_auth",
        )
    if backup:
        lines += _action_lines("🔁", "Backup", backup, 2)
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

    # Wearable line: yesterday's FINALIZED day, not today's 08:00 stub
    # ("Steps: 12"). Falls back to the two-device comparison only when both
    # wearables genuinely reported today.
    try:
        from .device_compare import _has_oura, compare_metrics, render_compare_whatsapp

        rows = compare_metrics(config, day)
        if rows and _has_oura(rows):
            lines.append("")
            lines.append(render_compare_whatsapp(config, day))
        else:
            yline = _yesterday_activity_line(config, day)
            if yline:
                lines.append("")
                lines.append(yline)
    except Exception as exc:
        logger.warning(f"wearable WhatsApp line failed: {exc}")

    # Completion footer — must be TRUTHFUL. WhatsApp inbound is bound to the
    # general OpenClaw agent, so "reply 1/2/done" never reached this app (the
    # only /whatsapp/inbound hits ever were a local test on 2026-04-18). Point
    # at the tracker Sheet, which works from the phone with one tap.
    lines.append("")
    lines.append(_completion_footer(config))

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
        "Want to knock one out tonight? " + _completion_footer(config)
    )

    message = "\n".join(lines)
    ok = _run_openclaw_send(message)
    if ok:
        logger.info(f"Sent evening nudge for {day} ({len(top)} open)")
    return ok
