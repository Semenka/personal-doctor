"""One-time Google Health API authorization (Fitbit Air + Pebble cloud).

Uses the SAME Google Cloud OAuth web client as the Drive / Fitness syncs
(credentials.json in GDRIVE_CREDENTIALS_DIR) but asks ONLY for the
googlehealth.* read-only scopes and saves a SEPARATE token
(data/ingested/.google_health_api_token.json). Google rejects tokens that mix
googlehealth.* with the legacy fitness.* scopes, so never merge the two.

Before running, once, in the Cloud Console for this project:
  1. Enable the API:  APIs & Services → Library → "Google Health API" → Enable
     https://console.cloud.google.com/apis/library/health.googleapis.com
  2. Credentials → the OAuth web client → Authorized redirect URIs must contain
       http://localhost:8770/          (default, local browser flow)
       https://www.google.com          (only for --manual, see below)

Two ways to consent:
  Local (a browser on this Mac):
      .venv/bin/python -m scripts.google_health_api_auth
  Manual (you are away from the Mac): prints a URL to open on any device;
  after "Allow", Google lands you on google.com with ?code=... in the address
  bar. Run the script again with that full URL:
      .venv/bin/python -m scripts.google_health_api_auth --manual
      .venv/bin/python -m scripts.google_health_api_auth --manual --redirect "https://www.google.com/?code=...&scope=..."
"""
from __future__ import annotations

import argparse
import http.server
import os
import sys
import urllib.parse
from pathlib import Path

os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

from app.sync.config import load_config
from app.sync.connectors.google_health_api import SCOPES, _token_path

_PORT = int(os.environ.get("GOOGLE_HEALTH_AUTH_PORT", "8770"))
LOCAL_REDIRECT = f"http://localhost:{_PORT}/"
MANUAL_REDIRECT = "https://www.google.com"


def _flow(config, redirect_uri):
    from google_auth_oauthlib.flow import Flow

    creds_dir = Path(config.gdrive_credentials_dir).expanduser()
    client_secrets = creds_dir / "credentials.json"
    if not client_secrets.exists():
        raise SystemExit(f"Missing {client_secrets} — the OAuth client the Drive sync uses.")
    return Flow.from_client_secrets_file(str(client_secrets), scopes=SCOPES, redirect_uri=redirect_uri)


def _save(config, creds) -> Path:
    tok = _token_path(config)
    tok.parent.mkdir(parents=True, exist_ok=True)
    tok.write_text(creds.to_json())
    tok.chmod(0o600)
    return tok


def _verify(config) -> None:
    from datetime import datetime

    from app.sync.connectors.google_health_api import fetch_daily_summary

    day = datetime.now(tz=config.timezone).date()
    s = fetch_daily_summary(config, day)
    print(f"Today so far: steps={s['steps']} sleep={s['sleep_minutes']/60:.1f}h "
          f"RHR={s['resting_hr']} HRV={s['hrv']} SpO2={s['spo2']}")
    print("Origins:", s["data_origins"] or "none yet")
    if s["errors"]:
        print("Per-block errors:", *s["errors"], sep="\n  ")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manual", action="store_true", help="print URL; redirect to google.com")
    ap.add_argument("--redirect", help="full redirect URL (or bare code) after --manual consent")
    args = ap.parse_args()

    config = load_config()
    if not config.gdrive_credentials_dir:
        print("GDRIVE_CREDENTIALS_DIR is not set — the Google OAuth client lives there.")
        return 1

    if args.manual:
        flow = _flow(config, MANUAL_REDIRECT)
        if not args.redirect:
            url, _ = flow.authorization_url(access_type="offline", prompt="consent")
            print("Open this URL on any device, sign in, click Allow, then copy the final URL:")
            print("AUTH_URL_BEGIN")
            print(url)
            print("AUTH_URL_END")
            print("Then run: .venv/bin/python -m scripts.google_health_api_auth --manual --redirect '<final url>'")
            return 0
        raw = args.redirect.strip()
        code = urllib.parse.parse_qs(urllib.parse.urlparse(raw).query).get("code", [raw])[0] if "://" in raw else raw
        flow.fetch_token(code=code)
        tok = _save(config, flow.credentials)
        print(f"✅ Google Health API token saved to {tok}")
        _verify(config)
        return 0

    flow = _flow(config, LOCAL_REDIRECT)
    auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent")
    captured: dict = {}

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            captured["code"] = (qs.get("code") or [None])[0]
            captured["error"] = (qs.get("error") or [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            ok = bool(captured.get("code"))
            self.wfile.write((
                "<h2>✅ Google Health API authorized.</h2><p>Close this tab.</p>" if ok
                else f"<h2>Authorization failed: {captured.get('error')}</h2>"
            ).encode())

        def log_message(self, *_a):
            pass

    try:
        server = http.server.HTTPServer(("localhost", _PORT), _Handler)
    except OSError as exc:
        print(f"Cannot bind localhost:{_PORT}: {exc}")
        return 1
    try:
        import webbrowser

        webbrowser.open(auth_url)
    except Exception:
        pass
    print("AUTH_URL_BEGIN"); print(auth_url); print("AUTH_URL_END")
    print(f"Waiting for the consent callback on {LOCAL_REDIRECT} …", flush=True)
    while "code" not in captured and "error" not in captured:
        server.handle_request()
    if captured.get("error") or not captured.get("code"):
        print(f"❌ Authorization did not complete: {captured.get('error')}")
        return 1
    flow.fetch_token(code=captured["code"])
    if not flow.credentials.refresh_token:
        print("⚠️ No refresh_token returned — re-run (prompt=consent should force it).")
    tok = _save(config, flow.credentials)
    print(f"✅ Google Health API token saved to {tok}")
    _verify(config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
