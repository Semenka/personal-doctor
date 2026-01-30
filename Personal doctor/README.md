# Personal Daily Health Advisor

Minimal Python CLI + web UI for daily health analysis and recommendations.

## Features
- Load daily metrics from JSON
- Choose optimization goals (energy, reproductive health, cognition, sport)
- Generate recommendations for diet, exercise, sleep, posture, hydration
- CLI and minimal web UI
- Daily sync module for Oura (7am scheduler)
- Research-backed actions from top journals (NEJM, The Lancet, JAMA)
- Quantified expected impact as % change per recommendation
- Daily recommendations surfaced in the health advisor dashboard

## Quick start

### 1) Create virtualenv + install
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2) Run CLI
```bash
python -m app.cli --data data/sample_daily.json --goals energy cognition
```

### 2a) Run Oura sync (manual)
```bash
export OURA_ACCESS_TOKEN=YOUR_TOKEN
# Optional Postgres storage
export DATABASE_URL=postgresql://user:password@localhost:5432/personal_doctor
python -m app.sync.cli --source oura --date 2026-01-17
```

### 2b) Run Oura daily scheduler (07:00)
```bash
export OURA_ACCESS_TOKEN=YOUR_TOKEN
export HEALTH_TIMEZONE=Europe/Berlin
export DATABASE_URL=postgresql://user:password@localhost:5432/personal_doctor
python -m app.sync.scheduler
```

### 3) Run web UI
```bash
uvicorn app.web:app --reload
```
Open http://127.0.0.1:8000

### 4) Run scheduled sync (Oura + research)
```bash
python -m app.sync.scheduler
```

Required env vars for research sync:
- `DATABASE_URL` (Postgres connection string)
- `OPENALEX_MAILTO` (your email for OpenAlex rate limiting/contact)

Optional:
- `HEALTH_TIMEZONE` (defaults to UTC)

## Daily data format (JSON)
See `data/sample_daily.json` for example fields.

## Data sources
### Oura
The sync pulls daily_sleep, daily_activity, and daily_readiness from Oura v2 API.
Requires `OURA_ACCESS_TOKEN`.

### Blood tests / Urine tests / Annual check-ups (PDF)
Use the sync CLI with a PDF path. Example:
```bash
python -m app.sync.cli --source blood --date 2026-01-17 --path data/labs/blood.pdf
python -m app.sync.cli --source urine --date 2026-01-17 --path data/labs/urine.pdf
python -m app.sync.cli --source annual --date 2026-01-17 --path data/labs/checkup.pdf
```

### Storage
If `DATABASE_URL` is set, payloads are stored in Postgres tables `daily_data` and
`lab_documents`. If not set, JSON files are written to `data/ingested`.

## Notes
This is a starter framework; personalize thresholds in `app/recommendations.py`.
