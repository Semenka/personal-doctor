"""Provider-agnostic LLM wrapper.

Personal Doctor was hard-wired to ``google.genai`` (Gemini) — the daily
advisor, the report summarizer, the biomarker extractor, and the image
analyzer all built ``genai.Client(...)`` directly. After Google revoked the
project's API key as "leaked," the user asked for the agent's actions to
be executed by OpenAI's Codex CLI with ``gpt-5.5``.

This module is the indirection. Every LLM call site goes through one
function — ``generate(...)`` — and the underlying provider is chosen by
env var:

    LLM_PROVIDER = "codex" (default) | "gemini" | "openai"

The default switched to ``codex`` once this module landed. To return to
Gemini, set ``LLM_PROVIDER=gemini`` in ``.env`` and restart.

Provider-specific notes
-----------------------

**codex** — invokes ``codex exec --model <LLM_MODEL>`` as a subprocess,
captures the last message via ``--output-last-message``. System and user
text are concatenated with a clear separator since the CLI takes a single
prompt. Reasoning effort defaults to ``low`` for routine extraction and
can be raised per-call (the daily advisor uses ``high`` so the morning
plan benefits from deeper deliberation). Sandbox mode is ``read-only`` so
the CLI can't accidentally touch the filesystem.

**gemini** — the legacy path, kept verbatim so a rollback is one env var.

**openai** — if the user later wires an OpenAI org key we use the
standard SDK directly (no CLI overhead). Auto-selected if Codex CLI is
missing but ``OPENAI_API_KEY`` is set.

Vision (image) calls are routed separately via ``generate_with_image``
because the codex CLI accepts ``-i FILE`` for image attachments.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger("personal-doctor.llm")


# ──────────────────────────────────────────────────────────────────────────
# Provider selection
# ──────────────────────────────────────────────────────────────────────────


def _provider() -> str:
    """Return the active provider, lowercase."""
    return (os.getenv("LLM_PROVIDER") or "codex").strip().lower()


def _default_model() -> str:
    """Default model for the active provider, overridable via LLM_MODEL."""
    if env := os.getenv("LLM_MODEL"):
        return env
    if _provider() == "codex":
        return "gpt-5.5"
    if _provider() == "gemini":
        return os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite-preview")
    if _provider() == "openai":
        return "gpt-5.5"
    return "gpt-5.5"


def _provider_has_credentials(p: str) -> bool:
    """Codex relies on the on-disk auth in ``~/.codex/auth.json`` (set up via
    ``codex login``) — we don't see an env var for it, so we treat the
    CLI's presence as the signal."""
    if p == "codex":
        return _codex_cli_path() is not None
    if p == "gemini":
        return bool(os.getenv("GOOGLE_API_KEY"))
    if p == "openai":
        return bool(os.getenv("OPENAI_API_KEY"))
    return False


def _fallback_providers() -> list:
    """Providers to try, in order, when the primary one fails.

    ``LLM_FALLBACK`` (comma-separated) overrides the default ``gemini,openai``;
    set it to an empty string to disable fallback. A provider without
    credentials is skipped. Added 2026-09-07 after the 08:00 advisor died on
    a codex CLI error and the whole digest went out as an error report.
    """
    raw = os.getenv("LLM_FALLBACK")
    names = [n.strip().lower() for n in (raw if raw is not None else "gemini,openai").split(",")]
    primary = _provider()
    return [n for n in names if n and n != primary and n in ("codex", "gemini", "openai")]


def provider_chain() -> list:
    """The primary provider followed by every credentialed fallback."""
    return [_provider()] + [p for p in _fallback_providers() if _provider_has_credentials(p)]


def has_credentials() -> bool:
    """True iff some provider in the chain can run (primary or a fallback)."""
    return any(_provider_has_credentials(p) for p in provider_chain())


def _model_for(provider: str, requested: Optional[str]) -> str:
    """The model to use with ``provider``: the caller's choice only if it is
    the primary provider's; a fallback gets its own default."""
    if provider == _provider() and requested:
        return requested
    if provider == "gemini":
        return os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite-preview")
    return "gpt-5.5"


_LAST_GENERATION: dict = {}


def last_generation_info() -> dict:
    """Which provider/model produced the most recent reply (for reports)."""
    return dict(_LAST_GENERATION)


_CODEX_PATH_CACHE: Optional[str] = None


def _codex_cli_path() -> Optional[str]:
    """Return an absolute path to the codex CLI, or None if not found.

    A bare ``which codex`` is NOT enough: launchd gives the service a minimal
    PATH (/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin) where codex
    (installed via nvm / the Codex.app bundle) is invisible — which silently
    disabled the daily advisor ("API key not set") for days. So we also honor
    a CODEX_BIN override and probe the well-known install locations.
    """
    global _CODEX_PATH_CACHE
    if _CODEX_PATH_CACHE and Path(_CODEX_PATH_CACHE).is_file():
        return _CODEX_PATH_CACHE

    candidates: list[str] = []
    # 1) Explicit override wins.
    if env := os.getenv("CODEX_BIN"):
        candidates.append(env)
    # 2) Anything already on PATH.
    if found := shutil.which("codex"):
        candidates.append(found)
    # 3) Every nvm-installed node version (newest first).
    nvm_root = Path.home() / ".nvm/versions/node"
    if nvm_root.is_dir():
        for c in sorted(nvm_root.glob("*/bin/codex"), reverse=True):
            candidates.append(str(c))
    # 4) Other known absolute locations.
    candidates += [
        "/Applications/Codex.app/Contents/Resources/codex",
        "/opt/homebrew/bin/codex",
        "/usr/local/bin/codex",
        str(Path.home() / ".local/bin/codex"),
    ]

    present = []
    for c in candidates:
        if c and Path(c).is_file() and c not in present:
            present.append(c)
    if not present:
        return None
    # An explicit override is taken as-is. Otherwise prefer the NEWEST
    # install: on 2026-09-07 launchd's PATH resolved to codex 0.142.5 while
    # the user's shell ran 0.150.1; the old binary refused the new one's
    # ~/.codex models cache ("missing field base_instructions") and the
    # morning digest went out as an error report.
    chosen = present[0] if os.getenv("CODEX_BIN") else _pick_newest(present)
    _CODEX_PATH_CACHE = chosen
    return chosen


def _codex_version(path: str) -> tuple:
    """Version tuple of a codex binary, () when it cannot be determined."""
    import re

    try:
        env = dict(os.environ)
        env["PATH"] = f"{Path(path).parent}:{Path(path).resolve().parent}:" + env.get("PATH", "")
        out = subprocess.run(
            [path, "--version"], capture_output=True, text=True, timeout=15, check=False, env=env,
        )
        m = re.search(r"(\d+)\.(\d+)\.(\d+)", (out.stdout or "") + (out.stderr or ""))
        return tuple(int(x) for x in m.groups()) if m else ()
    except Exception:
        return ()


def _pick_newest(paths: list) -> str:
    """The candidate with the highest version; ties keep candidate order."""
    best, best_v = paths[0], _codex_version(paths[0])
    for c in paths[1:]:
        v = _codex_version(c)
        if v > best_v:
            best, best_v = c, v
    return best


def _codex_exec_env() -> dict:
    """Subprocess env for codex with node guaranteed on PATH.

    The codex binary is a node script (#!/usr/bin/env node). Under launchd's
    minimal PATH, node — installed alongside codex in the nvm bin dir — isn't
    on PATH, so exec'ing codex by absolute path still fails. Prepend the codex
    binary's own directory (which contains node) to PATH.
    """
    env = dict(os.environ)
    codex = _codex_cli_path()
    if codex:
        bin_dir = str(Path(codex).resolve().parent)
        # The resolved codex.js lives under lib/node_modules; node is in the
        # symlink's directory, so prepend the *symlink* dir, not the realpath.
        link_dir = str(Path(codex).parent)
        env["PATH"] = f"{link_dir}:{bin_dir}:" + env.get("PATH", "")
    return env


# ──────────────────────────────────────────────────────────────────────────
# Main entry points
# ──────────────────────────────────────────────────────────────────────────


def _generate_with(provider: str, *, system: str, user: str, model: str,
                   max_output_tokens: Optional[int], temperature: Optional[float],
                   reasoning: str, timeout_s: int, image_path: Optional[Path]) -> str:
    if provider == "codex":
        return _generate_codex(
            system=system, user=user, model=model,
            reasoning=reasoning, timeout_s=timeout_s, image_path=image_path,
        )
    if provider == "gemini":
        if image_path is not None:
            return _generate_gemini_image(system=system, user=user, model=model, image_path=image_path)
        return _generate_gemini(
            system=system, user=user, model=model,
            max_output_tokens=max_output_tokens, temperature=temperature,
        )
    if provider == "openai":
        if image_path is not None:
            return _generate_openai_image(system=system, user=user, model=model, image_path=image_path)
        return _generate_openai(
            system=system, user=user, model=model,
            max_output_tokens=max_output_tokens, temperature=temperature,
        )
    raise RuntimeError(f"Unknown LLM_PROVIDER: {provider!r}")


def _generate_chain(**kw) -> str:
    """Try the primary provider, then each credentialed fallback.

    Raises ``RuntimeError`` naming every failure only when the whole chain
    is exhausted. Records the provider that answered in ``_LAST_GENERATION``.
    """
    requested = kw.pop("model")
    errors = []
    for provider in provider_chain():
        model = _model_for(provider, requested)
        try:
            text = _generate_with(provider, model=model, **kw)
        except Exception as exc:
            errors.append(f"{provider}/{model}: {exc}")
            logger.warning(f"LLM {provider} ({model}) failed: {str(exc)[:300]}")
            continue
        _LAST_GENERATION.clear()
        _LAST_GENERATION.update({"provider": provider, "model": model, "fallback": bool(errors)})
        if errors:
            logger.warning(f"LLM answered by fallback {provider} after: " + " | ".join(errors)[:600])
        return text
    raise RuntimeError("all LLM providers failed — " + " | ".join(errors))


def generate(
    *,
    system: str = "",
    user: str,
    model: Optional[str] = None,
    max_output_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    reasoning: str = "low",
    timeout_s: int = 600,
) -> str:
    """Generate text for a (system, user) pair using the active provider.

    ``reasoning`` is one of ``"low" | "medium" | "high" | "xhigh"`` and only
    affects Codex (Gemini and OpenAI ignore it). ``max_output_tokens`` and
    ``temperature`` are honored by Gemini / OpenAI. For Codex they're
    silently ignored — the CLI doesn't expose either as a per-call flag.

    When the primary provider fails, every credentialed fallback (see
    ``LLM_FALLBACK``) is tried before giving up.

    Returns the model's text reply, stripped of leading/trailing whitespace.
    Raises ``RuntimeError`` if every provider failed.
    """
    return _generate_chain(
        system=system, user=user, model=model or _default_model(),
        max_output_tokens=max_output_tokens, temperature=temperature,
        reasoning=reasoning, timeout_s=timeout_s, image_path=None,
    )


def generate_with_image(
    *,
    system: str = "",
    user: str,
    image_path: Path,
    model: Optional[str] = None,
    reasoning: str = "medium",
    timeout_s: int = 600,
) -> str:
    """Vision call — pass a local image alongside the prompt.

    Codex CLI supports image attachments via ``-i FILE``. Gemini routes
    through the legacy ``google.genai`` Part API. OpenAI sends a
    base64-encoded data URL in the content array. Same fallback chain as
    ``generate``.
    """
    return _generate_chain(
        system=system, user=user, model=model or _default_model(),
        max_output_tokens=None, temperature=None,
        reasoning=reasoning, timeout_s=timeout_s, image_path=image_path,
    )


# ──────────────────────────────────────────────────────────────────────────
# Provider implementations — Codex CLI
# ──────────────────────────────────────────────────────────────────────────


def _generate_codex(
    *, system: str, user: str, model: str, reasoning: str, timeout_s: int,
    image_path: Optional[Path] = None,
) -> str:
    """Run codex CLI non-interactively and capture the last message."""
    prompt = _merge_system_user(system, user)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8",
    ) as out_f:
        out_path = out_f.name

    try:
        # Use the resolved absolute path so it works under launchd's minimal
        # PATH (a bare "codex" wouldn't be found by the service).
        codex_bin = _codex_cli_path() or "codex"
        cmd = [
            codex_bin, "exec",
            "--model", model,
            "-c", f"model_reasoning_effort={reasoning}",
            "--sandbox", "read-only",
            "--skip-git-repo-check",
            "--ephemeral",
            "--output-last-message", out_path,
        ]
        if image_path is not None:
            cmd.extend(["-i", str(image_path)])

        try:
            proc = subprocess.run(
                cmd, input=prompt, capture_output=True, text=True,
                timeout=timeout_s, check=False,
                env=_codex_exec_env(),  # node on PATH for the codex shebang
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"codex exec timed out after {timeout_s}s")

        if proc.returncode != 0:
            # The CLI echoes the whole prompt on stdout before it fails, so
            # the first 400 chars were always "[SYSTEM] You are an
            # experienced…" and the actual error never reached the report.
            # Keep the tail of stderr (the error lines), then of stdout.
            raise RuntimeError(
                f"codex exec exit={proc.returncode}: {_error_tail(proc.stderr, proc.stdout)}"
            )

        try:
            text = Path(out_path).read_text(encoding="utf-8").strip()
        except Exception as exc:
            raise RuntimeError(f"codex output read failed: {exc}")

        if not text:
            # Fallback: try to fish the answer out of stdout (after the
            # "codex" marker line printed by the CLI before its reply).
            stdout = proc.stdout or ""
            marker = "\ncodex\n"
            idx = stdout.rfind(marker)
            if idx >= 0:
                tail = stdout[idx + len(marker):]
                # Cut at "tokens used" footer if present
                cut = tail.find("\ntokens used")
                if cut > 0:
                    tail = tail[:cut]
                text = tail.strip()

        if not text:
            raise RuntimeError("codex exec returned empty output")
        return text
    finally:
        try:
            Path(out_path).unlink(missing_ok=True)
        except Exception:
            pass


def _error_tail(stderr: Optional[str], stdout: Optional[str], limit: int = 600) -> str:
    """The most informative slice of a failed CLI run: error-looking stderr
    lines first, else the last ``limit`` chars of stderr, else of stdout."""
    err = (stderr or "").strip()
    hits = [ln for ln in err.splitlines() if "error" in ln.lower() or "fail" in ln.lower()]
    if hits:
        return " | ".join(hits)[-limit:]
    if err:
        return err[-limit:]
    return (stdout or "").strip()[-limit:]


def _merge_system_user(system: str, user: str) -> str:
    """Concatenate a system + user message into a single Codex prompt.

    Codex CLI takes one prompt and doesn't have a dedicated system slot.
    Marking the boundary explicitly helps the model keep the role separation.
    """
    system = (system or "").strip()
    user = (user or "").strip()
    if not system:
        return user
    return (
        "[SYSTEM]\n"
        f"{system}\n\n"
        "[END SYSTEM]\n\n"
        "[USER]\n"
        f"{user}"
    )


# ──────────────────────────────────────────────────────────────────────────
# Provider implementations — legacy Gemini (kept for rollback)
# ──────────────────────────────────────────────────────────────────────────


def _generate_gemini(
    *, system: str, user: str, model: str,
    max_output_tokens: Optional[int], temperature: Optional[float],
) -> str:
    from google import genai
    from google.genai import types

    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY required for gemini provider")

    cfg_kwargs = {}
    if system:
        cfg_kwargs["system_instruction"] = system
    if max_output_tokens is not None:
        cfg_kwargs["max_output_tokens"] = max_output_tokens
    if temperature is not None:
        cfg_kwargs["temperature"] = temperature

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model,
        contents=user,
        config=types.GenerateContentConfig(**cfg_kwargs) if cfg_kwargs else None,
    )
    return (response.text or "").strip()


def _generate_gemini_image(
    *, system: str, user: str, model: str, image_path: Path,
) -> str:
    from google import genai
    from google.genai import types

    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY required for gemini provider")

    client = genai.Client(api_key=api_key)
    img_bytes = Path(image_path).read_bytes()
    mime = "image/jpeg" if str(image_path).lower().endswith((".jpg", ".jpeg")) else "image/png"

    cfg_kwargs = {}
    if system:
        cfg_kwargs["system_instruction"] = system

    response = client.models.generate_content(
        model=model,
        contents=[
            types.Part.from_bytes(data=img_bytes, mime_type=mime),
            user,
        ],
        config=types.GenerateContentConfig(**cfg_kwargs) if cfg_kwargs else None,
    )
    return (response.text or "").strip()


# ──────────────────────────────────────────────────────────────────────────
# Provider implementations — OpenAI SDK direct (no CLI)
# ──────────────────────────────────────────────────────────────────────────


def _generate_openai(
    *, system: str, user: str, model: str,
    max_output_tokens: Optional[int], temperature: Optional[float],
) -> str:
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY required for openai provider")
    client = OpenAI(api_key=api_key)

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})

    kwargs = {"model": model, "messages": messages}
    if max_output_tokens is not None:
        kwargs["max_completion_tokens"] = max_output_tokens
    if temperature is not None:
        kwargs["temperature"] = temperature

    completion = client.chat.completions.create(**kwargs)
    return (completion.choices[0].message.content or "").strip()


def _generate_openai_image(
    *, system: str, user: str, model: str, image_path: Path,
) -> str:
    import base64

    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY required for openai provider")
    client = OpenAI(api_key=api_key)

    img_b64 = base64.b64encode(Path(image_path).read_bytes()).decode("ascii")
    mime = "image/jpeg" if str(image_path).lower().endswith((".jpg", ".jpeg")) else "image/png"

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({
        "role": "user",
        "content": [
            {"type": "text", "text": user},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{img_b64}"}},
        ],
    })

    completion = client.chat.completions.create(model=model, messages=messages)
    return (completion.choices[0].message.content or "").strip()


# ──────────────────────────────────────────────────────────────────────────
# Diagnostics
# ──────────────────────────────────────────────────────────────────────────


def provider_info() -> dict:
    """Return a description of the active provider — used by /health."""
    return {
        "provider": _provider(),
        "model": _default_model(),
        "has_credentials": has_credentials(),
        "chain": provider_chain(),
        "codex_bin": _codex_cli_path() if _provider() == "codex" else None,
    }
