"""One-time / re-run Google Health (Fitness API) authorization.

Fitbit's standalone dev portal no longer accepts new app registrations —
bracelet data now flows through Google. This helper reuses the SAME Google
Cloud OAuth client that already powers the Drive sync (credentials.json in
GDRIVE_CREDENTIALS_DIR), requests the fitness scopes, and saves the token to
data/ingested/.google_health_token.json. After that the 07:43 daily sync
auto-refreshes it.

IMPORTANT — the OAuth client is a *web* client, so Google only accepts a
redirect URI that is registered in the Cloud Console. Port 8321 is owned by a
separate service (EmailAssistant backend), so this flow uses a dedicated port
(default 8765) and runs a tiny local server there to capture the callback.
Register http://localhost:8765/ as an Authorized redirect URI on the OAuth
client first, or the consent page returns redirect_uri_mismatch. (The old
run_local_server(port=0) used a random port Google rejects as unregistered.)

We request access_type=offline + prompt=consent so Google always returns a
refresh_token (without prompt=consent a re-auth often omits it).

NOTE: while the OAuth consent screen is in "Testing" status Google expires the
refresh token after 7 days, so this must be re-run weekly. Publish the app to
Production (console.cloud.google.com/auth/audience -> PUBLISH APP) to stop the
weekly expiry.

Before running, enable the Fitness API on the project (one click):
    https://console.cloud.google.com/apis/library/fitness.googleapis.com

Then:
    cd ~/personal-doctor
    .venv/bin/python -m scripts.google_health_auth
"""
from __future__ import annotations

import http.server
import os
import sys
import urllib.parse
from pathlib import Path

# This OAuth client already has Drive/Gmail/Calendar scopes granted, so Google
# returns the UNION of all granted scopes — not just the fitness ones we asked
# for. oauthlib treats that as a fatal "Scope has changed" error unless we
# relax it. Must be set before oauthlib is imported/used.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

from app.sync.config import load_config
from app.sync.connectors.google_health import SCOPES, _token_path

# Dedicated loopback port for the consent callback. The shared OAuth client is
# a "web" type, so this exact redirect URI must be registered in the Cloud
# Console (APIs & Services -> Credentials -> the OAuth client -> Authorized
# redirect URIs). We use a dedicated port (NOT 8321, which the EmailAssistant
# backend owns) so re-auth never collides with that service. Override with
# GOOGLE_HEALTH_AUTH_PORT if 8765 is taken.
_PORT = int(os.environ.get("GOOGLE_HEALTH_AUTH_PORT", "8770"))
REDIRECT_URI = f"http://localhost:{_PORT}/"
_CALLBACK_PATH = "/"


def main() -> int:
    config = load_config()
    if not config.gdrive_credentials_dir:
        print("GDRIVE_CREDENTIALS_DIR is not set — the Google OAuth client lives there.")
        return 1
    creds_dir = Path(config.gdrive_credentials_dir).expanduser()
    client_secrets = creds_dir / "credentials.json"
    if not client_secrets.exists():
        print(f"Missing {client_secrets} — the same OAuth client the Drive sync uses.")
        return 1

    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_secrets_file(
        str(client_secrets), scopes=SCOPES, redirect_uri=REDIRECT_URI
    )
    auth_url, _state = flow.authorization_url(
        access_type="offline",
        prompt="consent",
    )

    captured: dict[str, str | None] = {}

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != _CALLBACK_PATH:
                self.send_response(404)
                self.end_headers()
                return
            qs = urllib.parse.parse_qs(parsed.query)
            captured["code"] = (qs.get("code") or [None])[0]
            captured["error"] = (qs.get("error") or [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            body = (
                "<h2>✅ Google Health authorized.</h2>"
                "<p>You can close this tab and return to the terminal.</p>"
                if captured.get("code")
                else f"<h2>Authorization failed: {captured.get('error')}</h2>"
            )
            self.wfile.write(body.encode())

        def log_message(self, *_args):  # silence default logging
            pass

    try:
        server = http.server.HTTPServer(("localhost", _PORT), _Handler)
    except OSError as exc:
        print(f"Cannot bind localhost:{_PORT} — is something else using it? {exc}")
        return 1

    # Try to open the browser automatically; always print the URL as fallback.
    try:
        import webbrowser

        webbrowser.open(auth_url)
    except Exception:
        pass

    print("AUTH_URL_BEGIN")
    print(auth_url)
    print("AUTH_URL_END")
    print(f"Waiting for the Google consent callback on {REDIRECT_URI} …", flush=True)

    # Serve requests until we capture a code or an error.
    while "code" not in captured and "error" not in captured:
        server.handle_request()

    if captured.get("error") or not captured.get("code"):
        print(f"❌ Authorization did not complete: {captured.get('error')}")
        return 1

    flow.fetch_token(code=captured["code"])
    creds = flow.credentials
    if not creds.refresh_token:
        print("⚠️ No refresh_token returned — re-run; Google omits it without prompt=consent.")

    tok = _token_path(config)
    tok.parent.mkdir(parents=True, exist_ok=True)
    tok.write_text(creds.to_json())
    print(f"✅ Google Health token saved to {tok}")
    print("The 07:43 daily sync will pick it up automatically.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
