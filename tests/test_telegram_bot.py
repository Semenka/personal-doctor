"""Telegram digest: one checkbox per action, taps tick the tracker, email only as fallback."""
from __future__ import annotations

import sys
import types
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sync import action_tracker, telegram_bot  # noqa: E402
from app.sync import whatsapp_sender as ws  # noqa: E402

DAY = "2026-10-02"
ADVICE = (
    "### Priority (do this one thing)\n\n"
    "1. **Cool Fertility Work Block** Easy | Category: Sleep\n   **When:** 2:00–4:00 PM\n\n"
    "### Backup (if priority won't happen)\n\n"
    "2. **AMPD1 Gentle Walk Reset** Easy | Category: Movement\n   **When:** 12:40–12:52 PM\n"
)


def _bot(monkeypatch, chat="4242"):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", chat)
    calls = []
    monkeypatch.setattr(
        telegram_bot, "_call",
        lambda method, payload, timeout=20: calls.append((method, payload)) or {"message_id": 7},
    )
    return calls


def _cfg(tmp_path):
    cfg = types.SimpleNamespace(data_dir=tmp_path, email_to="", smtp_host="")
    action_tracker.save_actions(tmp_path, DAY, action_tracker.parse_actions(ADVICE, DAY))
    return cfg


def _no_sheets(monkeypatch):
    monkeypatch.setattr(action_tracker, "mark_action_done_with_sheets",
                        lambda c, d, i: action_tracker.mark_action_done(c.data_dir, d, i))
    monkeypatch.setattr(action_tracker, "mark_action_undone_with_sheets",
                        lambda c, d, i: action_tracker.mark_action_undone(c.data_dir, d, i))


def test_keyboard_has_one_checkbox_per_action(tmp_path):
    _cfg(tmp_path)
    actions = action_tracker.load_actions(tmp_path, DAY)
    actions[1]["done"] = True
    rows = telegram_bot.action_keyboard(DAY, actions)["inline_keyboard"]
    assert [r[0]["text"] for r in rows] == [
        "⬜ 1. Cool Fertility Work Block", "✅ 2. AMPD1 Gentle Walk Reset",
    ]
    assert [r[0]["callback_data"] for r in rows] == [f"d:{DAY}:0", f"u:{DAY}:1"]


def test_digest_goes_to_the_bot_with_buttons_instead_of_the_text_protocol(monkeypatch, tmp_path):
    calls = _bot(monkeypatch)
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(ws, "_openclaw_send_once", lambda *a, **k: (_ for _ in ()).throw(AssertionError("OpenClaw used")))
    monkeypatch.setattr(ws, "_yesterday_activity_line", lambda cfg, day: "")
    import app.sync.action_tracker as at
    monkeypatch.setattr(at, "load_actions_with_sheets", lambda c, d: at.load_actions(c.data_dir, d))

    assert ws.send_whatsapp_advice(cfg, {"date": DAY, "model": "gpt-5.5", "advice": ADVICE}) is True
    method, payload = calls[-1]
    assert method == "sendMessage" and payload["chat_id"] == "4242"
    assert "📋 Today's protocol" not in payload["text"]
    assert "Tap an action below" in payload["text"]
    assert len(payload["reply_markup"]["inline_keyboard"]) == 2


def test_tap_ticks_the_action_and_flips_the_button(monkeypatch, tmp_path):
    calls = _bot(monkeypatch)
    cfg = _cfg(tmp_path)
    _no_sheets(monkeypatch)
    tap = {"update_id": 1, "callback_query": {
        "id": "cq1", "data": f"d:{DAY}:0",
        "message": {"message_id": 7, "chat": {"id": 4242}},
    }}
    telegram_bot.handle_update(cfg, tap, today=date(2026, 10, 2))
    assert action_tracker.load_actions(tmp_path, DAY)[0]["done"] is True
    edit = next(p for m, p in calls if m == "editMessageReplyMarkup")
    assert edit["reply_markup"]["inline_keyboard"][0][0]["text"].startswith("✅ 1.")
    assert next(p for m, p in calls if m == "answerCallbackQuery")["text"] == "Done ✅"

    # A replayed update sets the same state again — it never unticks.
    telegram_bot.handle_update(cfg, tap, today=date(2026, 10, 2))
    assert action_tracker.load_actions(tmp_path, DAY)[0]["done"] is True


def test_taps_from_another_chat_or_an_old_day_change_nothing(monkeypatch, tmp_path):
    calls = _bot(monkeypatch)
    cfg = _cfg(tmp_path)
    _no_sheets(monkeypatch)
    stranger = {"callback_query": {"id": "x", "data": f"d:{DAY}:0",
                                   "message": {"message_id": 7, "chat": {"id": 999}}}}
    telegram_bot.handle_update(cfg, stranger, today=date(2026, 10, 2))
    old = {"callback_query": {"id": "y", "data": f"d:{DAY}:0",
                              "message": {"message_id": 7, "chat": {"id": 4242}}}}
    telegram_bot.handle_update(cfg, old, today=date(2026, 10, 9))
    assert not any(a["done"] for a in action_tracker.load_actions(tmp_path, DAY))
    answers = [p["text"] for m, p in calls if m == "answerCallbackQuery"]
    assert answers == ["Not your tracker.", "That day is closed."]


def test_start_tells_an_unconfigured_user_their_chat_id(monkeypatch):
    calls = _bot(monkeypatch, chat="")
    monkeypatch.delenv("TELEGRAM_TARGET", raising=False)
    telegram_bot.handle_update(None, {"message": {"text": "/start", "chat": {"id": 555}}})
    assert "TELEGRAM_CHAT_ID=555" in calls[-1][1]["text"]


def test_numeric_openclaw_target_doubles_as_chat_id(monkeypatch):
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setenv("TELEGRAM_TARGET", "4242")
    assert telegram_bot.chat_id() == "4242"
    monkeypatch.setenv("TELEGRAM_TARGET", "@someone")
    assert telegram_bot.chat_id() == ""


def test_bot_failure_falls_back_to_openclaw(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "4242")
    monkeypatch.setattr(telegram_bot, "send_message", lambda text, markup=None: False)
    used = []
    monkeypatch.setattr(ws, "_openclaw_send_once",
                        lambda msg, target, channel="whatsapp", timeout_s=30: used.append(channel) or (True, ""))
    assert ws._run_openclaw_send("hello") is True
    assert used == ["whatsapp"]


def test_daily_email_mode(monkeypatch):
    monkeypatch.delenv("DAILY_EMAIL", raising=False)
    assert ws.daily_email_mode() == "fallback"
    monkeypatch.setenv("DAILY_EMAIL", "Always")
    assert ws.daily_email_mode() == "always"
    monkeypatch.setenv("DAILY_EMAIL", "bogus")
    assert ws.daily_email_mode() == "fallback"


def test_long_digest_splits_and_keeps_buttons_on_the_last_part(monkeypatch):
    calls = _bot(monkeypatch)
    text = "\n".join(f"line {i} " + "x" * 90 for i in range(80))
    assert telegram_bot.send_message(text, {"inline_keyboard": []}) is True
    sends = [p for m, p in calls if m == "sendMessage"]
    assert len(sends) == 2 and all(len(p["text"]) <= 4096 for p in sends)
    assert "reply_markup" not in sends[0] and "reply_markup" in sends[1]
