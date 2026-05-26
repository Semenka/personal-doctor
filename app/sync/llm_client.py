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


def has_credentials() -> bool:
    """True iff the active provider has the credentials it needs to run.

    Codex relies on the on-disk auth in ``~/.codex/auth.json`` (set up via
    ``codex login``) — we don't see an env var for it, so we treat the
    CLI's presence as the signal.
    """
    p = _provider()
    if p == "codex":
        return _codex_cli_path() is not None
    if p == "gemini":
        return bool(os.getenv("GOOGLE_API_KEY"))
    if p == "openai":
        return bool(os.getenv("OPENAI_API_KEY"))
    return False


def _codex_cli_path() -> Optional[str]:
    """Return the codex CLI path if it's on PATH, else None."""
    try:
        result = subprocess.run(
            ["which", "codex"], capture_output=True, text=True, timeout=2, check=False,
        )
        return result.stdout.strip() or None
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────────────────
# Main entry points
# ──────────────────────────────────────────────────────────────────────────


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

    Returns the model's text reply, stripped of leading/trailing whitespace.
    Raises ``RuntimeError`` if the provider failed.
    """
    mdl = model or _default_model()
    provider = _provider()

    if provider == "codex":
        return _generate_codex(
            system=system, user=user, model=mdl,
            reasoning=reasoning, timeout_s=timeout_s,
        )
    if provider == "gemini":
        return _generate_gemini(
            system=system, user=user, model=mdl,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
        )
    if provider == "openai":
        return _generate_openai(
            system=system, user=user, model=mdl,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
        )
    raise RuntimeError(f"Unknown LLM_PROVIDER: {provider!r}")


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
    base64-encoded data URL in the content array.
    """
    mdl = model or _default_model()
    provider = _provider()

    if provider == "codex":
        return _generate_codex(
            system=system, user=user, model=mdl,
            reasoning=reasoning, timeout_s=timeout_s,
            image_path=image_path,
        )
    if provider == "gemini":
        return _generate_gemini_image(
            system=system, user=user, model=mdl, image_path=image_path,
        )
    if provider == "openai":
        return _generate_openai_image(
            system=system, user=user, model=mdl, image_path=image_path,
        )
    raise RuntimeError(f"Unknown LLM_PROVIDER: {provider!r}")


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
        cmd = [
            "codex", "exec",
            "--model", model,
            "-c", f"model_reasoning_effort={reasoning}",
            "--sandbox", "read-only",
            "--skip-git-repo-check",
            "--ephemeral",
            "--output-last-message", out_path,
        ]
        if image_path is not None:
            cmd.extend(["-i", str(image_path)])
        cmd.append(prompt)

        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=timeout_s, check=False,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"codex exec timed out after {timeout_s}s")

        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(
                f"codex exec exit={proc.returncode}: {err[:400]}"
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
    }
