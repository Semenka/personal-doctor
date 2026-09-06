---
name: personal-doctor
description: AI health advisor (Health OS) that syncs the Fitbit Air + Pebble watches and sporadic Oura ring nights, scans Google Drive medical reports, generates a daily plan with Gemini, and delivers it by email + WhatsApp — focused on fertility (sperm motility) and energy.
metadata: { "openclaw": { "emoji": "🩺", "requires": { "bins": ["python3"], "env": ["GOOGLE_API_KEY"] }, "primaryEnv": "GOOGLE_API_KEY" } }
---

# Personal Doctor (Health OS)

Always-on health advisor running as a launchd service on the Mac Mini
(`~/personal-doctor`, port 8000). It syncs wearable data, scans medical
reports from Google Drive, generates a daily plan with Gemini and delivers
it by email and WhatsApp every morning. The weekly Health OS brief goes out
on Sunday evening.

**The launchd service is the scheduler.** Do not add OpenClaw cron jobs for
the pipeline — they would run the same pipeline a second time and send a
second digest. This skill is the on-demand interface.

## Quick commands

| Say | Run |
|-----|-----|
| "Check my health status" / "Is my watch data coming?" | `curl -s http://localhost:8000/health` — read the `wearables` block (see below) |
| "Run my health pipeline" | `cd ~/personal-doctor && .venv/bin/python -m app.sync.run_pipeline` |
| "Sync my Fitbit / watch data" | `cd ~/personal-doctor && .venv/bin/python -m app.sync.cli --source fitbit` |
| "Sync my Oura data" (ring is worn only occasionally) | `cd ~/personal-doctor && .venv/bin/python -m app.sync.cli --source oura` |
| "Get my health advice" | `cd ~/personal-doctor && .venv/bin/python -m app.sync.cli --source advisor --email` |
| "Show my last advice" | `curl -s http://localhost:8000/advice` |
| "Show health logs" | `curl -s http://localhost:8000/logs` or `tail -100 ~/personal-doctor/logs/personal-doctor.log` |
| "Scan my Drive for new reports" | `cd ~/personal-doctor && .venv/bin/python -m app.sync.cli --source gdrive` |
| "Analyze my MRI / X-ray" | `cd ~/personal-doctor && .venv/bin/python -m app.sync.cli --source scan --path <image>` |

Always report the command's own output back to the user (the sync jobs print
"Saved … (fresh|empty)", "Skipping … not authorized", etc.).

## Daily schedule (launchd service, Europe/Paris)

| Time | Job |
|------|-----|
| 06:50 | `sync-and-relaunch.sh` pulls `origin/main` and restarts the service |
| 07:20 | Research sync (PubMed + OpenAlex) |
| 07:30 | Google Drive health folder scan |
| 07:38 | Oura ring sweep — captures any sporadic ring night from the last 7 days |
| 07:40 | Fitbit Air (+ Pebble) sync, back-fills the last 3 days |
| 07:41 | Anomaly detector |
| 07:42 | Auto-credit yesterday's actions from watch signals |
| 07:45 | Supplement inventory check |
| 08:00 | Daily advisor → local JSON, Drive `me/health/YYYY/MM/DD/`, email, WhatsApp |
| 08:05 | Lab check-up reconcile + overdue alert (WhatsApp, throttled to twice a week) |
| 21:00 | WhatsApp evening nudge if actions are still open |
| Sun 17:50 / 18:00 / 18:15 / 18:30 | Oura sweep, weekly retro, journal watch, Health OS brief |

## Wearable data: what feeds the pipeline

| Device | Path into the pipeline | File |
|--------|------------------------|------|
| **Fitbit Air** (daily watch) | Google Health API (Fitbit's cloud, preferred) → else Fitbit Web API → else the phone's Health Connect → Google Fit relay | `data/ingested/fitbit_<date>.json` |
| **Pebble 2 / Time 2** | Pebble app → Health Connect → Google Health app → Google Health API (or the Google Fit relay) | merged into the same `fitbit_<date>.json`, attributed via `data_origins` |
| **Oura ring** (sporadic) | Oura cloud API, 07:38 sweep | `data/ingested/daily_<date>.json`, overlaid onto the day's recovery metrics |
| Pixel phone | Health Connect → Google Fit relay | steps only — **not** watch data |

### Reading `/health` → `wearables`

```json
"wearables": {
  "transports_authorized": {"google_health_api": false, "google_health_relay": true, "fitbit_web_api": false},
  "today_file": {"via": "google_health", "steps": 3120, "sleep_hours": 0, "fresh": true},
  "watch_silent_days": 33, "last_watch_date": "2026-08-04", "phone_only": true,
  "devices": {"fitbit": {"silent_days": 33, "last_date": "2026-08-04"}, "pebble": {"silent_days": 61, "last_date": null}},
  "summary": "Fitbit Air silent 33d · Pebble never"
}
```

- `phone_only: true` = steps arrive from the phone's own sensor while the
  watches send nothing. Sleep / HRV / resting HR / SpO2 are then **missing,
  not low**. The daily email and WhatsApp digest carry the same warning line.
- `summary` is empty / "all watches reporting" when every watch reported
  within 3 days.

### Repairing the watch feed (when `summary` names a silent device)

1. **Fitbit Air, permanent fix — link its cloud once** (bypasses the phone):
   - Cloud Console → enable *Google Health API* on the project the Drive
     sync uses.
   - On the Mac Mini: `cd ~/personal-doctor && .venv/bin/python -m scripts.google_health_api_auth`
     (or `--manual` to consent from a phone). The token lands in
     `data/ingested/.google_health_api_token.json` and the 07:40 sync prefers
     it automatically.
   - Publish the OAuth consent screen (Testing → Production) or the refresh
     token dies every 7 days; the sync then WhatsApps a "sync is DOWN" alert.
2. **Fitbit Air, phone relay**: Google Fit → Profile → Settings → Health
   Connect → enable sync + *read* for sleep, heart rate, SpO2; Fitbit app →
   Health Connect → *write* on.
3. **Pebble**: Pebble app → Settings → Health → *Sync to Health Connect*.
   It has never reported as of 2026-09-06.
4. Then run "Sync my Fitbit data" and re-check `/health`.

## Data sources beyond wearables

1. **Google Drive** `me/health/` typed subfolders, scanned recursively:
   `blood/`, `sperm/`, `genetic/`, `health_check/`, `prescription/`,
   `conclusion/`; root files and MRI / X-ray images are detected too.
2. **Image analysis** — MRI, X-ray, CT scans analysed for pathologies.
3. **Lab check-up schedule** — reconciled daily against ingested results.

## Completion tracking

Action completion is credited passively from the watch data (07:42 and
21:00): movement / walk actions when steps ≥ 7000 or active minutes ≥ 25,
sleep actions when sleep ≥ 6.75 h. Everything else is ticked in the tracker
Google Sheet linked from every digest. WhatsApp replies are **not** routed to
this app unless the channel is bound to the `personal-doctor` agent:

```bash
openclaw agents bind --agent personal-doctor --bind whatsapp:default   # opt in
openclaw agents bind --agent main --bind whatsapp:default              # revert
```

## Delivery reliability

`whatsapp_sender` retries, kick-starts the OpenClaw gateway on
"outbound not configured" / "no listener" errors (time-throttled), falls back
to Telegram (`TELEGRAM_TARGET`), then to email — the morning plan is never
silently dropped.

## Environment (`~/personal-doctor/.env`)

| Variable | Required | Description |
|----------|----------|-------------|
| `GOOGLE_API_KEY` | Yes | Gemini key for the advisor |
| `EMAIL_TO`, `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD` | Yes | Digest delivery |
| `GDRIVE_CREDENTIALS_DIR` | Yes | Google OAuth client (`credentials.json`); also used by the Google Health transports |
| `OURA_ACCESS_TOKEN` | No | Enables the sporadic ring sweep |
| `FITBIT_CLIENT_ID` / `FITBIT_CLIENT_SECRET` | No | Legacy Fitbit Web API fallback |
| `HEALTH_TIMEZONE` | No | Default Europe/Paris |
| `WHATSAPP_TARGET`, `TELEGRAM_TARGET` | No | Delivery targets |

## Service management

```bash
launchctl load ~/Library/LaunchAgents/com.personal-doctor.plist     # start
launchctl unload ~/Library/LaunchAgents/com.personal-doctor.plist   # stop
launchctl kickstart -k gui/$UID/ai.openclaw.gateway                 # heal WhatsApp gateway
tail -f ~/personal-doctor/logs/personal-doctor.log
```
