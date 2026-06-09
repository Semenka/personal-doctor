"""One-time Google Health (Fitness API) authorization.

Fitbit's standalone dev portal no longer accepts new app registrations —
bracelet data now flows through Google. This helper reuses the SAME Google
Cloud OAuth client that already powers the Drive sync (credentials.json in
GDRIVE_CREDENTIALS_DIR), requests the fitness scopes, and saves the token to
data/ingested/.google_health_token.json. After that the 07:43 daily sync
auto-refreshes forever.

Before running, enable the Fitness API on the project (one click):
    https://console.cloud.google.com/apis/library/fitness.googleapis.com

Then:
    cd ~/personal-doctor
    .venv/bin/python -m scripts.google_health_auth

A browser consent page opens (same flow as the Drive setup you've already
done once). Approve the fitness scopes and you're done.
"""
from __future__ import annotations

import sys
from pathlib import Path

from app.sync.config import load_config
from app.sync.connectors.google_health import SCOPES, _token_path


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

    from google_auth_oauthlib.flow import InstalledAppFlow

    print("Opening Google consent page for the fitness scopes…")
    print("(Same flow as the Drive authorization you've done before.)")
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), SCOPES)
    creds = flow.run_local_server(port=0)

    tok = _token_path(config)
    tok.parent.mkdir(parents=True, exist_ok=True)
    tok.write_text(creds.to_json())
    print(f"✅ Google Health token saved to {tok}")
    print("Test the live pull now with:")
    print('  .venv/bin/python -c "from app.sync.config import load_config; '
          "from app.sync.pipeline import load_fitbit_via_google_health; import datetime, json; "
          'print(json.dumps(load_fitbit_via_google_health(load_config(), datetime.date.today()), indent=2))"')
    print("The 07:43 daily sync will pick it up automatically from tomorrow.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
