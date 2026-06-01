"""One-time Fitbit OAuth2 authorization — mint the first access+refresh token.

Fitbit Web API uses the Authorization Code Grant (with PKCE optional for a
Personal app). Run this once after creating a Personal app at dev.fitbit.com.
It writes the token pair to config.fitbit_token_path; from then on the daily
sync auto-refreshes (the refresh token rotates on each use).

Setup at https://dev.fitbit.com/apps/new:
  - Application type: Personal  (Personal gives intraday + all scopes for your own account)
  - OAuth 2.0 Application Type: Personal
  - Callback URL: http://localhost:8731/callback   (must match exactly)
  - Note the Client ID + Client Secret → put in .env as
    FITBIT_CLIENT_ID / FITBIT_CLIENT_SECRET

Then:
    cd ~/personal-doctor
    .venv/bin/python -m scripts.fitbit_auth

It opens the Fitbit consent page, captures the redirect on localhost:8731,
exchanges the code, and saves the token. Scopes requested: activity,
heartrate, sleep, respiratory_rate, oxygen_saturation, profile.
"""
from __future__ import annotations

import base64
import http.server
import json
import sys
import threading
import urllib.parse
import webbrowser
from pathlib import Path

import requests

from app.sync.config import load_config

REDIRECT_URI = "http://localhost:8731/callback"
SCOPES = "activity heartrate sleep respiratory_rate oxygen_saturation profile"
AUTH_URL = "https://www.fitbit.com/oauth2/authorize"
TOKEN_URL = "https://api.fitbit.com/oauth2/token"

_code_holder = {"code": None}


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/callback":
            qs = urllib.parse.parse_qs(parsed.query)
            _code_holder["code"] = (qs.get("code") or [None])[0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(
                b"<html><body><h2>Fitbit authorized.</h2>"
                b"You can close this tab and return to the terminal.</body></html>"
            )
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):  # silence
        pass


def main() -> int:
    config = load_config()
    if not (config.fitbit_client_id and config.fitbit_client_secret):
        print("Set FITBIT_CLIENT_ID and FITBIT_CLIENT_SECRET in .env first "
              "(create a Personal app at https://dev.fitbit.com/apps/new).")
        return 1

    # 1. Open consent page
    params = {
        "response_type": "code",
        "client_id": config.fitbit_client_id,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "expires_in": "604800",
    }
    url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"
    print("Opening Fitbit consent page…\nIf it doesn't open, visit:\n", url)
    webbrowser.open(url)

    # 2. Capture the redirect
    server = http.server.HTTPServer(("localhost", 8731), _Handler)
    t = threading.Thread(target=server.handle_request, daemon=True)
    t.start()
    t.join(timeout=300)
    code = _code_holder["code"]
    if not code:
        print("No authorization code captured (timed out).")
        return 1

    # 3. Exchange code → token pair
    basic = base64.b64encode(
        f"{config.fitbit_client_id}:{config.fitbit_client_secret}".encode()
    ).decode()
    resp = requests.post(
        TOKEN_URL,
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": config.fitbit_client_id,
        },
        timeout=30,
    )
    if resp.status_code >= 400:
        print(f"Token exchange failed {resp.status_code}: {resp.text[:400]}")
        return 1
    data = resp.json()
    tokens = {
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "expires_in": data.get("expires_in"),
        "scope": data.get("scope"),
    }
    out = Path(config.fitbit_token_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(tokens, indent=2))
    print(f"✅ Fitbit token saved to {out}")
    print("The daily 07:43 sync will now auto-refresh. Test with:")
    print("  .venv/bin/python -c \"from app.sync.config import load_config; "
          "from app.sync.pipeline import load_fitbit_daily; import datetime; "
          "print(load_fitbit_daily(load_config(), datetime.date.today()))\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
