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
cd ~/personal-doctor-python
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

These must be set in `~/personal-doctor-python/.env`:

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
