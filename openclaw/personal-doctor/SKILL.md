---
name: personal-doctor
description: AI health advisor that collects Oura Ring data, scans Google Drive medical reports, generates daily health plans with Claude Opus 4.6, and emails personalized recommendations focused on fertility and energy optimization.
metadata: { "openclaw": { "emoji": "🩺", "requires": { "bins": ["python3"], "env": ["ANTHROPIC_API_KEY", "OURA_ACCESS_TOKEN"] }, "primaryEnv": "ANTHROPIC_API_KEY" } }
---

# Personal Doctor

Your AI health advisor powered by Claude Opus 4.6. It collects wearable data from Oura Ring, scans medical reports from Google Drive, and generates a personalized daily health plan focused on sperm motility and energy optimization — delivered to your email every morning.

## Quick commands

- **"Run my health pipeline"** — triggers the full daily pipeline: Oura sync → Drive scan → AI advisor → email
- **"Get my health advice"** — generates and shows today's AI health plan
- **"Check my health status"** — shows the server health check (services, last run)
- **"Show my last advice"** — displays the most recent daily health plan
- **"Show health logs"** — displays recent pipeline execution logs
- **"Sync my Oura data"** — pulls today's sleep, activity, and readiness from Oura Ring
- **"Analyze my MRI/X-ray"** — analyzes a medical image for pathologies

## How it works

The Personal Doctor runs as a local server on your Mac Mini with a background scheduler:

| Time | Job |
|------|-----|
| 07:00 | Google Drive health folder scan (blood tests, genetic reports, health check-ups, prescriptions, MRI/X-ray) |
| 07:20 | Oura Ring data sync (sleep score, HRV, resting HR, steps, readiness) |
| 07:30 | AI Daily Advisor generates plan with Claude Opus 4.6 → saves locally → uploads to Drive → emails to you |

## Running the pipeline

To run the full pipeline on demand:

```bash
cd ~/personal-doctor
.venv/bin/python -m app.sync.run_pipeline
```

Or trigger specific steps:

```bash
# Oura data only
.venv/bin/python -m app.sync.cli --source oura

# AI advisor + email
.venv/bin/python -m app.sync.cli --source advisor --email

# AI advisor + email + Drive upload
.venv/bin/python -m app.sync.cli --source advisor --email --upload

# Analyze a medical image
.venv/bin/python -m app.sync.cli --source scan --path ~/Downloads/mri_knee.jpg

# Scan Google Drive for new reports
.venv/bin/python -m app.sync.cli --source gdrive
```

## Server endpoints

If the server is running (default on port 8000):

```bash
# Health check
curl http://localhost:8000/health

# Trigger pipeline now
curl -X POST http://localhost:8000/run

# Get last advice as JSON
curl http://localhost:8000/advice

# View recent logs
curl http://localhost:8000/logs
```

## Data sources

The advisor combines data from multiple sources:

1. **Oura Ring** — sleep hours, HRV, resting heart rate, steps, readiness score
2. **Google Drive** `me/health/` — auto-scans these typed subfolders (recursively):
   - `blood/` — blood test results
   - `sperm/` — sperm analysis
   - `genetic/` — genetic test results (MTHFR, COMT, VDR, etc.)
   - `health_check/` — annual medical check-ups
   - `prescription/` — prescriptions
   - `conclusion/` — doctor conclusions
   - Root files and MRI/X-ray images are also detected
3. **Image analysis** — Claude Vision analyzes MRI, X-ray, CT scans for pathologies

## Environment variables

These must be set in `~/personal-doctor/.env`:

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Claude API key from console.anthropic.com |
| `OURA_ACCESS_TOKEN` | Yes | Oura Ring API token from cloud.ouraring.com |
| `EMAIL_TO` | Yes | Recipient email address |
| `SMTP_HOST` | Yes | SMTP server (e.g. smtp.yahoo.com) |
| `SMTP_PORT` | No | Default: 465 (SSL) |
| `SMTP_USER` | Yes | SMTP login username |
| `SMTP_PASSWORD` | Yes | SMTP app password |
| `GDRIVE_CREDENTIALS_DIR` | No | Path to Google OAuth credentials directory |
| `HEALTH_TIMEZONE` | No | Default: Europe/Paris |

## Service management

```bash
# Start
launchctl load ~/Library/LaunchAgents/com.personal-doctor.plist

# Stop
launchctl unload ~/Library/LaunchAgents/com.personal-doctor.plist

# Restart
launchctl unload ~/Library/LaunchAgents/com.personal-doctor.plist && launchctl load ~/Library/LaunchAgents/com.personal-doctor.plist

# View service logs
tail -f ~/personal-doctor/logs/launchd-stdout.log
tail -f ~/personal-doctor/logs/personal-doctor.log
```

## Completion tracking: auto-credit from Oura (primary)

Action completion is derived passively from Oura — no manual reply required.
After the Oura sync (07:42) and again at the evening nudge (21:00),
`auto_complete.auto_credit_actions()` inspects today's actions and:

- **movement / walk / cardio** → credited if `steps ≥ 7000` or `active_minutes ≥ 25`
- **sleep / bedtime** → credited if `sleep_hours ≥ 6.75`
- **supplements / cold shower / daylight** → can't be sensed; left for optional
  manual confirmation, never auto-failed.

Credited actions are marked done with `source="oura_auto"` and feed the
`action_effects` correlation engine, which powers the `/outcomes` "what moved
your metrics" analysis.

## Optional: route manual WhatsApp replies to the doctor agent

By default WhatsApp inbound routes to the `main` OpenClaw agent (your general
assistant). The personal-doctor reply handler (`/whatsapp/inbound`: "1", "2",
"done", "skip", "why creatine?") only receives replies if you bind the
WhatsApp channel to the personal-doctor agent **and** forward inbound text to
the local webhook. Because rebinding would take WhatsApp away from your
general assistant, this is left opt-in:

```bash
# Route WhatsApp inbound to the personal-doctor agent (replaces main binding)
openclaw agents bind --agent personal-doctor --bind whatsapp:default

# Revert to the general assistant
openclaw agents bind --agent main --bind whatsapp:default
```

Auto-credit (above) is the recommended path and needs none of this.

## Outcome intelligence

- `GET /outcomes` — "since your last test" cross-test progress page
- New lab/spermogram in Drive → on-arrival progress note pushed to WhatsApp
  ("total motility 5% → 26% since last test, still below WHO 42%…")
- Daily email shows the "📈 Since your last test" block when a report landed
  in the last 7 days.

## Delivery reliability

`whatsapp_sender` now retries, self-heals the OpenClaw gateway on
"outbound not configured" / "no listener" errors (auto-kickstart), falls back
to Telegram (`TELEGRAM_TARGET` env), and finally to email — so the morning
plan is never silently dropped.

## Second wearable: Fitbit (side-by-side with Oura)

A Fitbit is integrated via the Fitbit Web API (Health Connect on the phone has
no server-readable cloud API). Oura stays the primary in `daily_<date>.json`;
Fitbit is written to a parallel `fitbit_<date>.json`, and a comparison layer
shows the two devices side by side in the advisor, email, and WhatsApp digest.

### One-time setup
1. Create a **Personal** app at https://dev.fitbit.com/apps/new
   - OAuth 2.0 Application Type: **Personal** (gives all scopes for your own data)
   - Callback URL: `http://localhost:8731/callback`
2. Put the Client ID + Secret in `~/personal-doctor/.env`:
   ```
   FITBIT_CLIENT_ID=...
   FITBIT_CLIENT_SECRET=...
   ```
3. Mint the first token (opens a browser consent page):
   ```bash
   cd ~/personal-doctor && .venv/bin/python -m scripts.fitbit_auth
   ```
   This writes `data/ingested/.fitbit_token.json`. From then on the daily
   07:43 sync auto-refreshes (Fitbit access tokens expire every 8 h; the
   refresh token rotates on each use and is persisted automatically).

### What it pulls
Steps, active minutes, sleep stages (deep/light/rem), resting HR, HRV (daily
RMSSD), breathing rate, and **SpO2** (Fitbit-only — Oura doesn't expose it).

### Side-by-side
The daily email shows a "⌚ Device comparison" card; the WhatsApp digest shows
compact `O 27 / F 31` lines. 🟢 = devices agree (within 12%), 🟡 = they diverge.
Until the Fitbit credentials are set, the sync logs "no credentials" and the
pipeline runs Oura-only (graceful — nothing else changes).

## UPDATE: Fitbit auth now goes through Google Health (Fitness API)

Fitbit's standalone portal (dev.fitbit.com) no longer accepts new app
registrations — bracelet data flows through Google now. The integration
reuses the SAME Google Cloud OAuth client as the Drive sync, so there is
no new app to register. Setup is two steps:

1. Enable the Fitness API on the existing project (one click):
   https://console.cloud.google.com/apis/library/fitness.googleapis.com
2. Authorize (same browser flow as the Drive setup):
   ```bash
   cd ~/personal-doctor && .venv/bin/python -m scripts.google_health_auth
   ```
   Token saved to `data/ingested/.google_health_token.json`; the 07:43
   daily sync auto-refreshes it and prefers this transport automatically.

3. **Phone-side bridge (required!)** — the Fitbit app writes to Health
   Connect, which stores data ON the phone only and never uploads to the
   Google fitness cloud this API reads (verified 2026-06-10: cloud had no
   data since May 2025 despite active devices). Install **Health Sync**
   (Play Store) and set direction Health Connect → Google Fit, enabling
   steps, heart rate, sleep, SpO2, activity. (Alternative: the Google Fit
   app connected to Health Connect.) Without this bridge the API returns
   zeros even though auth is complete.

Pulled via Google: steps, distance, calories, active minutes, Heart Points
(mapped to Active Zone Minutes), resting HR (min-HR proxy), SpO2, body temp,
sleep stages (deep/light/REM). Not exposed by the public Fitness API: HRV,
VO2max, floors, breathing rate — Oura remains the ★ source for HRV/sleep
staging regardless. The legacy Fitbit Web API path remains as a fallback if
its token ever exists.
