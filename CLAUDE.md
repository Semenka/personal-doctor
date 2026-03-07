# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

## Project overview

**Personal Doctor** is a Python FastAPI daily health advisor that collects data from Oura Ring and Google Drive, generates AI-powered health recommendations using Gemini 3.1 Flash-Lite, and delivers them via email.

### Primary optimisation goals

Every recommendation the AI advisor produces must maximise these two parameters:

1. **Daily energy level** — the patient should feel sharp, productive, and physically ready throughout the day.
2. **Sperm motility** — the patient is actively trying to conceive; every action should improve sperm quality and motility.

These goals drive the system prompt in `app/sync/daily_advisor.py` (`SYSTEM_PROMPT`) and must be reflected in any new features, prompts, or recommendation logic.

## Tech stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.9+ |
| Web framework | FastAPI + Uvicorn + Jinja2 |
| AI model | **Gemini 3.1 Flash-Lite** (`gemini-3.1-flash-lite-preview`) via `google-genai` SDK |
| Wearable | Oura Ring API v2 |
| Storage | Google Drive API (OAuth2) · PostgreSQL (optional, falls back to JSON) |
| Scheduler | APScheduler (background cron) |
| Email | SMTP (Yahoo) |
| Deployment | Mac Mini (launchd) · OpenClaw · Google Cloud Run · Docker |

## Architecture

```
app/
  sync/
    config.py              # SyncConfig dataclass, loads from env vars
    daily_advisor.py       # Gemini-powered daily health plan generation
    image_analyzer.py      # Gemini Vision medical image analysis (MRI/X-ray/CT)
    scheduler.py           # APScheduler cron jobs (07:00 / 07:10 / 07:20 / 07:30)
    run_pipeline.py        # One-shot pipeline runner (Cloud Run)
    cli.py                 # CLI entry point: python -m app.sync.cli
    connectors/
      gdrive.py            # Google Drive OAuth + file operations
      oura.py              # Oura Ring API client
    storage.py             # JSON / PostgreSQL persistence
    gdrive_pipeline.py     # Drive folder scan + report ingestion
    email_sender.py        # HTML email rendering + SMTP delivery
  web.py                   # FastAPI web dashboard
  server.py                # Combined web server + background scheduler
  recommendations.py       # Rule-based recommendation engine
  research/                # OpenAlex research paper pipeline
openclaw/                  # OpenClaw skill + plugin config
```

## Key environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GOOGLE_API_KEY` | Yes | Gemini API key from aistudio.google.com |
| `OURA_ACCESS_TOKEN` | Yes | Oura Ring API token |
| `EMAIL_TO` | Yes | Recipient email address |
| `SMTP_HOST` | Yes | SMTP server (e.g. smtp.yahoo.com) |
| `SMTP_USER` | Yes | SMTP login |
| `SMTP_PASSWORD` | Yes | SMTP app password |
| `GDRIVE_CREDENTIALS_DIR` | No | Path to dir with Google OAuth `credentials.json` |

## Common commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the full pipeline (one-shot)
python -m app.sync.run_pipeline

# Run individual steps
python -m app.sync.cli --source oura           # Oura sync
python -m app.sync.cli --source gdrive         # Google Drive scan
python -m app.sync.cli --source advisor        # AI daily advice
python -m app.sync.cli --source advisor --email # + email delivery
python -m app.sync.cli --source scan --path img.jpg  # Image analysis

# Start the server (web dashboard + scheduler)
python -m app.server

# Start just the scheduler
python -m app.sync.scheduler
```

## Daily schedule

| Time | Job |
|------|-----|
| 07:00 | Google Drive health folder scan |
| 07:10 | Research paper recommendations |
| 07:20 | Oura Ring data sync |
| 07:30 | AI daily advisor (Gemini 3.1 Flash-Lite) → email |

## Conventions

- Config is loaded from environment variables via `app/sync/config.py` (`load_config()`).
- All AI calls use the `google-genai` SDK (`from google import genai`).
- The model constant `MODEL = "gemini-3.1-flash-lite-preview"` is defined at the top of each AI module.
- Google Drive paths use `~` — always call `.expanduser()` on `Path` objects.
- Reports are stored under `data/ingested/` (JSON) or PostgreSQL if `DATABASE_URL` is set.
- The system prompt in `daily_advisor.py` is the single source of truth for the advisor's persona and output format.
