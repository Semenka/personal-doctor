"""Link the Google Health API (Fitbit Air + Pebble cloud) — from a phone.

The consent has to be given by the account owner in a browser, and the
resulting token has to live on the Mac Mini. Doing both from a phone means:

  1. GET  {SERVER_URL}/auth/google-health  → a page with an "Authorize" link
     (the Google consent URL). Google then redirects to
     http://localhost:8770/?code=… — that page cannot load on a phone, but the
     address bar still holds the code.
  2. POST {SERVER_URL}/auth/google-health  with that URL pasted in → the code
     is exchanged here, the token saved, and a verification pull is shown.

PKCE: google-auth-oauthlib generates a code_verifier for every consent URL
and Google refuses the exchange without it. The verifier is therefore
persisted next to the token between the two steps (the CLI's ``--manual``
two-step used a fresh Flow for the exchange and could never have succeeded).
"""
from __future__ import annotations

import html
import json
import os
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .config import SyncConfig
from .connectors.google_health_api import SCOPES, _token_path

os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

_PORT = int(os.environ.get("GOOGLE_HEALTH_AUTH_PORT", "8770"))
LOCAL_REDIRECT = f"http://localhost:{_PORT}/"
PAGE_PATH = "/auth/google-health"


def _state_path(config: SyncConfig) -> Path:
    return Path(config.data_dir) / ".google_health_api_flow.json"


def client_secrets_path(config: SyncConfig) -> Optional[Path]:
    if not config.gdrive_credentials_dir:
        return None
    return Path(config.gdrive_credentials_dir).expanduser() / "credentials.json"


def _make_flow(config: SyncConfig, redirect_uri: str, code_verifier: Optional[str] = None):
    """One Flow on the Drive sync's OAuth client. Module-level so tests can stub it."""
    from google_auth_oauthlib.flow import Flow

    secrets = client_secrets_path(config)
    if not secrets or not secrets.exists():
        raise RuntimeError(
            "credentials.json (the Google OAuth client the Drive sync uses) is missing — "
            "set GDRIVE_CREDENTIALS_DIR and put the client's credentials.json there."
        )
    kwargs: Dict[str, Any] = {"scopes": SCOPES, "redirect_uri": redirect_uri}
    if code_verifier:
        kwargs["code_verifier"] = code_verifier
        kwargs["autogenerate_code_verifier"] = False
    return Flow.from_client_secrets_file(str(secrets), **kwargs)


def is_linked(config: SyncConfig) -> bool:
    return _token_path(config).exists()


def build_consent_url(config: SyncConfig, redirect_uri: str = LOCAL_REDIRECT) -> str:
    """The Google consent URL; persists the PKCE verifier for the exchange step."""
    flow = _make_flow(config, redirect_uri)
    url, _state = flow.authorization_url(access_type="offline", prompt="consent")
    state = {
        "redirect_uri": redirect_uri,
        "code_verifier": getattr(flow, "code_verifier", None),
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
    sp = _state_path(config)
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(state))
    try:
        sp.chmod(0o600)
    except OSError:
        pass
    return url


def code_from_redirect(raw: str) -> str:
    """Accept the full redirect URL, its query string, or the bare code."""
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("paste the URL Google redirected you to (it contains code=…)")
    if "://" in raw:
        qs = urllib.parse.urlparse(raw).query
    elif "code=" in raw:
        qs = raw.lstrip("?")
    else:
        return raw
    code = urllib.parse.parse_qs(qs).get("code", [""])[0]
    if not code:
        raise ValueError("no code= parameter in that URL — copy the whole address bar")
    return code


def exchange(config: SyncConfig, redirect_or_code: str) -> Path:
    """Turn the consent code into a saved refresh token; returns the token path."""
    code = code_from_redirect(redirect_or_code)
    state: Dict[str, Any] = {}
    sp = _state_path(config)
    if sp.exists():
        try:
            state = json.loads(sp.read_text())
        except ValueError:
            state = {}
    flow = _make_flow(
        config,
        state.get("redirect_uri") or LOCAL_REDIRECT,
        code_verifier=state.get("code_verifier"),
    )
    flow.fetch_token(code=code)
    creds = flow.credentials
    if not getattr(creds, "refresh_token", None):
        raise RuntimeError(
            "Google returned no refresh_token — open the Authorize link again "
            "(it forces the consent screen) and redo the paste."
        )
    tok = _token_path(config)
    tok.parent.mkdir(parents=True, exist_ok=True)
    tmp = tok.with_suffix(tok.suffix + ".tmp")
    tmp.write_text(creds.to_json())
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    tmp.replace(tok)
    try:
        sp.unlink()
    except OSError:
        pass
    return tok


def verify(config: SyncConfig) -> Dict[str, Any]:
    """One real pull for today so the page can show what the cloud holds."""
    from .connectors.google_health_api import fetch_daily_summary

    day = datetime.now(tz=config.timezone).date()
    s = fetch_daily_summary(config, day)
    return {
        "date": day.isoformat(),
        "steps": s.get("steps"),
        "sleep_hours": round((s.get("sleep_minutes") or 0) / 60, 1),
        "resting_hr": s.get("resting_hr"),
        "hrv": s.get("hrv"),
        "spo2": s.get("spo2"),
        "origins": list(s.get("data_origins") or []),
        "errors": list(s.get("errors") or []),
    }


def status(config: SyncConfig) -> Dict[str, Any]:
    secrets = client_secrets_path(config)
    return {
        "linked": is_linked(config),
        "token_path": str(_token_path(config)),
        "client_secrets_present": bool(secrets and secrets.exists()),
        "redirect_uri": LOCAL_REDIRECT,
        "page": f"{config.server_url}{PAGE_PATH}",
    }


def nudge_if_unlinked(config: SyncConfig) -> bool:
    """WhatsApp the link page once a day while the cloud is not linked."""
    if is_linked(config):
        return False
    marker = Path(config.data_dir) / ".google_health_link_nudge.json"
    today = datetime.now(tz=config.timezone).date().isoformat()
    try:
        if marker.exists() and json.loads(marker.read_text()).get("date") == today:
            return False
    except Exception:
        pass
    msg = (
        "⌚ Fitbit Air + Pebble cloud not linked yet — the watches' sleep/HRV/SpO2 "
        "can't reach the pipeline.\n"
        f"Link it from this phone (2 min): {config.server_url}{PAGE_PATH}\n"
        "Tap Authorize → Allow → copy the address-bar URL → paste it back on that page."
    )
    try:
        from .whatsapp_sender import _run_openclaw_send

        sent = bool(_run_openclaw_send(msg))
    except Exception:
        sent = False
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({"date": today, "sent": sent}))
    except Exception:
        pass
    return sent


def render_page(
    config: SyncConfig,
    *,
    consent_url: Optional[str] = None,
    result: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
) -> str:
    st = status(config)
    esc = html.escape
    parts = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Link Google Health</title>"
        "<style>body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;"
        "max-width:560px;margin:24px auto;padding:0 16px;color:#1a1a1a;line-height:1.45}"
        ".btn{display:inline-block;background:#2563eb;color:#fff;padding:12px 18px;border-radius:8px;"
        "text-decoration:none;font-weight:600;font-size:16px}"
        "textarea{width:100%;min-height:96px;font-size:14px;padding:8px;box-sizing:border-box}"
        ".ok{background:#ecfdf5;border:1px solid #6ee7b7;padding:12px;border-radius:8px}"
        ".err{background:#fef2f2;border:1px solid #fca5a5;padding:12px;border-radius:8px}"
        ".note{background:#fefce8;border:1px solid #fde68a;padding:12px;border-radius:8px;font-size:14px}"
        "code{background:#f3f4f6;padding:1px 4px;border-radius:4px}</style></head><body>"
        "<h2>⌚ Link the Fitbit Air + Pebble cloud</h2>"
    ]
    if error:
        parts.append(f"<div class='err'><strong>Not linked.</strong> {esc(error)}</div>")
    if result is not None:
        origins = ", ".join(result.get("origins") or []) or "none yet"
        parts.append(
            "<div class='ok'><strong>✅ Linked and saved.</strong> Today so far: "
            f"steps {esc(str(result.get('steps')))}, sleep {esc(str(result.get('sleep_hours')))} h, "
            f"RHR {esc(str(result.get('resting_hr')))}, HRV {esc(str(result.get('hrv')))}, "
            f"SpO2 {esc(str(result.get('spo2')))}.<br>Devices seen: {esc(origins)}</div>"
        )
        if result.get("errors"):
            parts.append(
                "<div class='note'>Per-block notes (a 403 here means the Google Health API "
                "is not enabled on the Cloud project yet):<br>"
                + "<br>".join(esc(e) for e in result["errors"])
                + "</div>"
            )
        parts.append(
            "<p>The 07:40 sync now prefers this transport. Tomorrow's digest will show "
            "the watches instead of the phone's step counter.</p>"
        )
    elif st["linked"]:
        parts.append(
            "<div class='ok'><strong>✅ Already linked.</strong> Token: "
            f"<code>{esc(st['token_path'])}</code>. Re-authorize below only if the daily "
            "sync reports a dead token.</div>"
        )
    if not st["client_secrets_present"]:
        parts.append(
            "<div class='err'>credentials.json is missing on the Mac Mini "
            "(GDRIVE_CREDENTIALS_DIR). The Drive sync's OAuth client is required.</div>"
        )
    parts.append(
        "<h3>Before the first time (Cloud Console, once)</h3><ol>"
        "<li>Enable <em>Google Health API</em> on the project the Drive sync uses: "
        "<a href='https://console.cloud.google.com/apis/library/health.googleapis.com'>APIs &amp; Services → Library</a>.</li>"
        f"<li>Credentials → the OAuth web client → Authorized redirect URIs contains "
        f"<code>{esc(st['redirect_uri'])}</code>.</li>"
        "<li>OAuth consent screen → <strong>Publish app</strong> (Testing → Production), "
        "or the refresh token dies every 7 days.</li></ol>"
    )
    parts.append("<h3>Step 1 — authorize</h3>")
    if consent_url:
        parts.append(
            f"<p><a class='btn' href='{esc(consent_url)}'>Authorize with Google</a></p>"
            "<p class='note'>After <em>Allow</em>, the browser opens "
            f"<code>{esc(st['redirect_uri'])}?code=…</code> — that page will not load on a phone. "
            "That is fine: copy the whole address-bar URL.</p>"
        )
    else:
        parts.append("<p class='err'>Could not build the consent link (see the error above).</p>")
    parts.append(
        "<h3>Step 2 — paste the redirect URL</h3>"
        f"<form method='post' action='{PAGE_PATH}'>"
        "<textarea name='redirect' placeholder='http://localhost:8770/?code=4/0A…&scope=…'></textarea>"
        "<p><button class='btn' type='submit'>Save token</button></p></form>"
        "<p style='font-size:13px;color:#6b7280'>Same as running "
        "<code>.venv/bin/python -m scripts.google_health_api_auth --manual</code> on the Mac Mini.</p>"
        "</body></html>"
    )
    return "".join(parts)
