"""LLM client resilience: fallback chain, newest codex, informative errors."""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sync import llm_client as llm  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("LLM_PROVIDER", "LLM_MODEL", "LLM_FALLBACK", "GOOGLE_API_KEY", "OPENAI_API_KEY", "CODEX_BIN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(llm, "_CODEX_PATH_CACHE", None)
    llm._LAST_GENERATION.clear()


def test_codex_failure_falls_back_to_gemini(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "g")
    monkeypatch.setattr(llm, "_codex_cli_path", lambda: "/fake/codex")

    def boom(**kw):
        raise RuntimeError("codex exec exit=1: failed to load models cache")

    monkeypatch.setattr(llm, "_generate_codex", boom)
    seen = {}
    monkeypatch.setattr(llm, "_generate_gemini", lambda **kw: seen.update(kw) or "plan from gemini")
    assert llm.provider_chain() == ["codex", "gemini"]
    assert llm.generate(system="s", user="u", model="gpt-5.5", reasoning="high") == "plan from gemini"
    assert seen["model"] == "gemini-3.1-flash-lite-preview"  # a fallback uses its own default model
    assert llm.last_generation_info() == {"provider": "gemini", "model": "gemini-3.1-flash-lite-preview", "fallback": True}


def test_all_providers_failing_names_every_error(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "g")
    monkeypatch.setattr(llm, "_codex_cli_path", lambda: "/fake/codex")
    monkeypatch.setattr(llm, "_generate_codex", lambda **kw: (_ for _ in ()).throw(RuntimeError("codex down")))
    monkeypatch.setattr(llm, "_generate_gemini", lambda **kw: (_ for _ in ()).throw(RuntimeError("key revoked")))
    with pytest.raises(RuntimeError) as exc:
        llm.generate(user="u")
    assert "codex/gpt-5.5: codex down" in str(exc.value) and "gemini/" in str(exc.value) and "key revoked" in str(exc.value)


def test_no_fallback_without_credentials_or_when_disabled(monkeypatch):
    monkeypatch.setattr(llm, "_codex_cli_path", lambda: "/fake/codex")
    monkeypatch.setattr(llm, "_generate_codex", lambda **kw: (_ for _ in ()).throw(RuntimeError("codex down")))
    monkeypatch.setattr(llm, "_generate_gemini", lambda **kw: "never")
    assert llm.provider_chain() == ["codex"]  # no GOOGLE_API_KEY → gemini not in the chain
    with pytest.raises(RuntimeError):
        llm.generate(user="u")
    monkeypatch.setenv("GOOGLE_API_KEY", "g")
    monkeypatch.setenv("LLM_FALLBACK", "")
    assert llm.provider_chain() == ["codex"]


def test_primary_success_records_no_fallback(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "g")
    monkeypatch.setattr(llm, "_codex_cli_path", lambda: "/fake/codex")
    monkeypatch.setattr(llm, "_generate_codex", lambda **kw: "plan")
    assert llm.generate(user="u") == "plan"
    assert llm.last_generation_info() == {"provider": "codex", "model": "gpt-5.5", "fallback": False}


def test_has_credentials_counts_a_credentialed_fallback(monkeypatch):
    monkeypatch.setattr(llm, "_codex_cli_path", lambda: None)
    assert llm.has_credentials() is False
    monkeypatch.setenv("GOOGLE_API_KEY", "g")
    assert llm.has_credentials() is True
    assert llm.provider_info()["chain"] == ["codex", "gemini"]


def test_error_tail_keeps_the_error_not_the_prompt_echo():
    stdout = "Reading prompt from stdin...\n[SYSTEM]\nYou are an experienced GP " * 40
    stderr = "2026-09-07T06:00:02Z ERROR codex_models_manager::cache: failed to load models cache: missing field `base_instructions`"
    assert llm._error_tail(stderr, stdout).startswith("2026-09-07T06:00:02Z ERROR")
    assert "missing field" in llm._error_tail(stderr, stdout)
    assert llm._error_tail("", stdout).endswith("You are an experienced GP")
    assert llm._error_tail("plain stderr line", stdout) == "plain stderr line"


def _fake_codex(tmp_path, name, version, exit_code=0):
    p = tmp_path / name
    p.write_text(f"#!/bin/sh\nif [ \"$1\" = \"--version\" ]; then echo 'codex-cli {version}'; exit 0; fi\n"
                 f"cat >/dev/null; echo 'prompt echo'; echo 'ERROR: usage limit reached' >&2; exit {exit_code}\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def test_newest_codex_wins_unless_overridden(tmp_path, monkeypatch):
    old = _fake_codex(tmp_path, "codex-old", "0.142.5")
    new = _fake_codex(tmp_path, "codex-new", "0.150.1")
    assert llm._codex_version(old) == (0, 142, 5) and llm._codex_version(new) == (0, 150, 1)
    assert llm._pick_newest([old, new]) == new
    assert llm._pick_newest([new, old]) == new
    monkeypatch.setenv("CODEX_BIN", old)
    monkeypatch.setattr(llm.shutil, "which", lambda name: new)
    assert llm._codex_cli_path() == old  # explicit override is respected


def test_codex_exec_error_carries_stderr_tail(tmp_path, monkeypatch):
    bad = _fake_codex(tmp_path, "codex", "0.150.1", exit_code=1)
    monkeypatch.setenv("CODEX_BIN", bad)
    with pytest.raises(RuntimeError) as exc:
        llm._generate_codex(system="s", user="u", model="gpt-5.5", reasoning="low", timeout_s=30)
    assert "usage limit reached" in str(exc.value) and "prompt echo" not in str(exc.value)


def test_transient_503_is_retried_on_the_same_provider(monkeypatch):
    """Codex quota-exhausted + Gemini momentarily 503 must still deliver."""
    from app.sync import llm_client as l

    calls = []

    def fake(provider, *, model, **kw):
        calls.append(provider)
        if provider == "codex":
            raise RuntimeError("codex exec exit=1: ERROR: You've hit your usage limit.")
        if calls.count("gemini") < 3:
            raise RuntimeError("503 UNAVAILABLE. This model is currently experiencing high demand.")
        return "PLAN"

    monkeypatch.setattr(l, "provider_chain", lambda: ["codex", "gemini"])
    monkeypatch.setattr(l, "_generate_with", fake)
    monkeypatch.setattr(l, "_TRANSIENT_BACKOFF_S", 0)
    assert l._generate_chain(system="s", user="u", model=None, reasoning="low",
                             timeout_s=5, max_output_tokens=None, temperature=None) == "PLAN"
    assert calls.count("codex") == 1  # quota is not retried
    assert calls.count("gemini") == 3


def test_quota_is_not_treated_as_transient():
    from app.sync.llm_client import _is_transient

    assert not _is_transient("ERROR: You've hit your usage limit (429)")
    assert _is_transient("503 UNAVAILABLE")
