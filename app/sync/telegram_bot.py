"""Direct Telegram Bot API channel: the phone digest with one-tap action checkboxes.

Why not OpenClaw's Telegram channel: it is only reached after a WhatsApp
failure, through the same gateway, so a gateway outage takes both down — in the
week to 2026-10-02 the digest fell back to email on 5 of 7 mornings. And a
checkbox needs the app to *receive* the tap (a callback query), which OpenClaw
already consumes for its own bot. So the app talks to its own bot over HTTPS:

- ``TELEGRAM_BOT_TOKEN``: a bot made with @BotFather for this app alone (a
  token OpenClaw also polls would make both pollers fail with 409 Conflict).
- ``TELEGRAM_CHAT_ID``: your chat with that bot. Optional when
  ``TELEGRAM_TARGET`` is already your numeric id. Unknown? Send /start to the
  bot — it replies with the id.

Each recommended action gets a button under the digest: ⬜ → tap → ✅. A tap
marks the action done in the local record and the tracker Sheet, then the
button flips. Button data names the state to set ("d" done / "u" undone), not
a toggle, so a replayed update can never flip an action back.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger("personal-doctor.telegram")

_API = "https://api.telegram.org/bot{token}/{method}"
_MAX_TEXT = 4096
_BUTTON_TITLE_CHARS = 48
# Taps on a digest older than this are refused: the tracker has archived it.
_MAX_TAP_AGE_DAYS = 2
_CALLBACK_RE = re.compile(r"^([du]):(\d{4}-\d{2}-\d{2}):(\d{1,3})$")


# The bot the user picked for this app (2026-10-02). Checked against getMe so
# a token from some other bot is caught at startup instead of messaging the
# wrong chat. TELEGRAM_BOT_USERNAME overrides it.
DEFAULT_BOT_USERNAME = "Cosmo_Ale_bot"

# Bot API error code of the last failed call (409 = another poller owns the bot).
_last_error_code: Optional[int] = None


def expected_username() -> str:
    return os.getenv("TELEGRAM_BOT_USERNAME", DEFAULT_BOT_USERNAME).strip().lstrip("@")


def bot_token() -> str:
    return os.getenv("TELEGRAM_BOT_TOKEN", "").strip()


def chat_id() -> str:
    explicit = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if explicit:
        return explicit
    # OpenClaw's target is usually the numeric user id, which is also the
    # private-chat id with any bot the user has started.
    target = os.getenv("TELEGRAM_TARGET", "").strip()
    return target if re.fullmatch(r"-?\d+", target) else ""


def configured() -> bool:
    return bool(bot_token() and chat_id())


def _call(method: str, payload: Dict[str, Any], timeout: float = 20) -> Optional[Dict[str, Any]]:
    """POST one Bot API method. Returns ``result`` or None; never raises, never logs the token."""
    global _last_error_code
    token = bot_token()
    if not token:
        return None
    _last_error_code = None
    try:
        resp = requests.post(
            _API.format(token=token, method=method), json=payload, timeout=timeout
        )
        body = resp.json()
    except Exception as exc:
        logger.warning(f"Telegram {method} failed: {type(exc).__name__}")
        return None
    if not body.get("ok"):
        _last_error_code = body.get("error_code")
        logger.warning(
            f"Telegram {method} rejected: {body.get('error_code')} {body.get('description', '')[:200]}"
        )
        return None
    return body.get("result")


def verify_bot() -> Dict[str, Any]:
    """getMe, compared with the expected bot: {"ok", "username", "expected"}."""
    me = _call("getMe", {}) or {}
    username = str(me.get("username") or "")
    expected = expected_username()
    return {
        "ok": bool(username) and username.lower() == expected.lower(),
        "username": username,
        "expected": expected,
    }


# ─── Outbound ──────────────────────────────────────────────────────────────

def action_keyboard(day: str, actions: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """One checkbox row per action: ``⬜ 1. Title`` / ``✅ 1. Title``."""
    rows = []
    for n, a in enumerate(actions, 1):
        idx = a.get("idx", n - 1)
        title = str(a.get("title") or "?").strip()
        if len(title) > _BUTTON_TITLE_CHARS:
            title = title[: _BUTTON_TITLE_CHARS - 1].rstrip() + "…"
        done = bool(a.get("done"))
        rows.append([{
            "text": f"{'✅' if done else '⬜'} {n}. {title}",
            "callback_data": f"{'u' if done else 'd'}:{day}:{idx}",
        }])
    return {"inline_keyboard": rows} if rows else None


def _chunks(text: str) -> List[str]:
    if len(text) <= _MAX_TEXT:
        return [text]
    out, cur = [], ""
    for line in text.split("\n"):
        while len(line) > _MAX_TEXT:  # a single monster line
            if cur:
                out.append(cur)
                cur = ""
            out.append(line[:_MAX_TEXT])
            line = line[_MAX_TEXT:]
        if len(cur) + len(line) + 1 > _MAX_TEXT:
            out.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        out.append(cur)
    return out


def send_message(text: str, reply_markup: Optional[Dict[str, Any]] = None) -> bool:
    """Send plain text (no parse mode: digests carry *, _ and links verbatim).

    Long text is split on line breaks; the buttons ride on the last part.
    """
    if not configured() or not text.strip():
        return False
    parts = _chunks(text)
    for i, part in enumerate(parts):
        payload: Dict[str, Any] = {
            "chat_id": chat_id(), "text": part, "disable_web_page_preview": True,
        }
        if reply_markup and i == len(parts) - 1:
            payload["reply_markup"] = reply_markup
        if _call("sendMessage", payload) is None:
            return False
    return True


# ─── Inbound (button taps) ─────────────────────────────────────────────────

def _offset_path(data_dir: Path) -> Path:
    return data_dir / "telegram_offset.json"


def _load_offset(data_dir: Path) -> int:
    try:
        return int(json.loads(_offset_path(data_dir).read_text()).get("offset", 0))
    except Exception:
        return 0


def _save_offset(data_dir: Path, offset: int) -> None:
    try:
        p = _offset_path(data_dir)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"offset": offset}))
    except Exception as exc:
        logger.warning(f"Could not persist Telegram offset: {exc}")


def _handle_callback(config: Any, cq: Dict[str, Any], today: date) -> None:
    from .action_tracker import (
        load_actions,
        mark_action_done_with_sheets,
        mark_action_undone_with_sheets,
    )

    msg = cq.get("message") or {}
    chat = str((msg.get("chat") or {}).get("id", ""))
    reply = "Not your tracker."
    m = _CALLBACK_RE.match(cq.get("data") or "")
    if chat == chat_id() and m:
        verb, day, idx = m.group(1), m.group(2), int(m.group(3))
        try:
            age = (today - date.fromisoformat(day)).days
        except ValueError:
            age = 99
        if age > _MAX_TAP_AGE_DAYS:
            reply = "That day is closed."
        elif not any(a.get("idx") == idx for a in load_actions(config.data_dir, day)):
            reply = "Action not found."
        else:
            if verb == "d":
                mark_action_done_with_sheets(config, day, idx)
                reply = "Done ✅"
            else:
                mark_action_undone_with_sheets(config, day, idx)
                reply = "Unticked"
            keyboard = action_keyboard(day, load_actions(config.data_dir, day))
            if keyboard and msg.get("message_id"):
                _call("editMessageReplyMarkup", {
                    "chat_id": chat, "message_id": msg["message_id"],
                    "reply_markup": keyboard,
                })
    _call("answerCallbackQuery", {"callback_query_id": cq.get("id"), "text": reply})


def _handle_message(message: Dict[str, Any]) -> None:
    text = (message.get("text") or "").strip()
    if not text.startswith("/start") and not text.startswith("/id"):
        return
    chat = str((message.get("chat") or {}).get("id", ""))
    if chat and chat == chat_id():
        body = "✅ Connected — daily digests and action checkboxes come here."
    else:
        body = (
            f"Your chat id is {chat}.\n"
            f"Add TELEGRAM_CHAT_ID={chat} to ~/personal-doctor/.env and restart the service."
        )
    _call("sendMessage", {"chat_id": chat, "text": body})


def handle_update(config: Any, update: Dict[str, Any], today: Optional[date] = None) -> None:
    today = today or date.today()
    try:
        if "callback_query" in update:
            _handle_callback(config, update["callback_query"], today)
        elif "message" in update:
            _handle_message(update["message"])
    except Exception as exc:
        logger.warning(f"Telegram update {update.get('update_id')} failed: {exc}")


def poll_once(config: Any, timeout_s: int = 50) -> bool:
    """One long-poll round. Returns False when polling should back off."""
    offset = _load_offset(config.data_dir)
    updates = _call(
        "getUpdates",
        {"offset": offset, "timeout": timeout_s,
         "allowed_updates": ["callback_query", "message"]},
        timeout=timeout_s + 15,
    )
    if updates is None:
        return False
    for upd in updates:
        handle_update(config, upd)
        offset = max(offset, int(upd.get("update_id", 0)) + 1)
        _save_offset(config.data_dir, offset)
    return True


def _status_path(data_dir: Path) -> Path:
    return data_dir / "telegram_status.json"


def _write_status(data_dir: Path, ok: bool, error_code: Optional[int]) -> None:
    from datetime import datetime, timezone

    try:
        _status_path(data_dir).write_text(json.dumps({
            "polling_ok": ok, "error_code": error_code,
            "since": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }))
    except Exception:
        pass


def read_status(data_dir: Path) -> Dict[str, Any]:
    try:
        return json.loads(_status_path(data_dir).read_text())
    except Exception:
        return {}


_poller_started = False


def start_poller(config: Any) -> bool:
    """Listen for button taps on a daemon thread (no-op without a bot token)."""
    global _poller_started
    if _poller_started or not bot_token():
        return False
    _poller_started = True

    check = verify_bot()
    if check["username"] and not check["ok"]:
        logger.warning(
            f"TELEGRAM_BOT_TOKEN belongs to @{check['username']}, not "
            f"@{check['expected']} — digests will come from @{check['username']}."
        )

    def _loop() -> None:
        backoff = 5
        last_conflict_log = 0.0
        last_state: Optional[str] = None
        while True:
            ok = poll_once(config)
            state = "ok" if ok else f"error {_last_error_code}"
            if state != last_state:
                _write_status(config.data_dir, ok, _last_error_code)
                last_state = state
            if ok:
                backoff = 5
                continue
            if _last_error_code == 409 and time.time() - last_conflict_log > 3600:
                # Sending still works; only taps are lost. Usually OpenClaw (or
                # another agent) is polling the same bot.
                logger.error(
                    f"Another program is reading updates for @{expected_username()} "
                    "(Telegram 409), so action-checkbox taps can't reach this app. "
                    "Remove the bot from that program, or give this app its own bot."
                )
                last_conflict_log = time.time()
            time.sleep(backoff)
            backoff = min(backoff * 2, 300)

    threading.Thread(target=_loop, name="telegram-poller", daemon=True).start()
    logger.info("Telegram poller started.")
    return True


if __name__ == "__main__":  # python -m app.sync.telegram_bot — setup check
    check = verify_bot()
    if not check["username"]:
        print("TELEGRAM_BOT_TOKEN missing or rejected.")
    else:
        mark = "✅" if check["ok"] else f"⚠️ expected @{check['expected']}"
        print(f"Bot: @{check['username']} {mark}")
        print(f"Chat id: {chat_id() or '(none — send /start to the bot)'}")
        if configured():
            print("Test message sent." if send_message("🩺 Personal Doctor test message.") else "Send failed.")
        from .config import load_config

        st = read_status(load_config().data_dir)
        if not st:
            print("Tap listener: not seen yet (restart the service after setting the token).")
        elif st.get("polling_ok"):
            print(f"Tap listener: ✅ receiving since {st.get('since')}")
        elif st.get("error_code") == 409:
            print("Tap listener: ⚠️ another program is polling this bot (Telegram 409) — "
                  "checkbox taps can't reach the app until it stops.")
        else:
            print(f"Tap listener: ⚠️ error {st.get('error_code')} since {st.get('since')}")
