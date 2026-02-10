# Personal Daily Health Advisor

Python CLI + web UI for daily health analysis, powered by Oura Ring data and Claude Opus 4.6 AI advisor.

## Features
- Daily AI health advisor (Claude Opus 4.6) focused on **sperm motility** and **energy**
- Top 3 actionable things to do today, generated from your real data
- Oura Ring integration: sleep, HRV, resting HR, activity
- Google Drive integration: upload reports to `drive/me/health`, auto-classify and ingest
- Supported report types: blood test, urine test, genetic test, sperm test, doctor conclusion, prescription
- **Medical image analysis** (MRI, X-ray, CT): Claude Vision detects tumours, ligament tears, meniscus damage, fractures, disc herniations, and more
- Research-backed actions from top journals (NEJM, The Lancet, JAMA)
- Rule-based recommendations engine (CLI + web UI)

## Quick start

### 1) Install
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2) Set environment variables
```bash
export OURA_ACCESS_TOKEN=YOUR_TOKEN
export ANTHROPIC_API_KEY=YOUR_KEY
# Optional
export DATABASE_URL=postgresql://user:password@localhost:5432/personal_doctor
export GDRIVE_CREDENTIALS_DIR=/path/to/dir/with/credentials.json
export OPENALEX_MAILTO=you@example.com
export HEALTH_TIMEZONE=Europe/Paris
# Email delivery (optional)
export EMAIL_TO=you@example.com
export SMTP_HOST=smtp.gmail.com
export SMTP_PORT=587
export SMTP_USER=you@gmail.com
export SMTP_PASSWORD=app-password
```

### 3) Run the AI daily advisor
```bash
# Sync Oura data first, then get your daily plan
python -m app.sync.cli --source oura
python -m app.sync.cli --source advisor

# Or with Google Drive upload
python -m app.sync.cli --source advisor --upload

# Or with email delivery
python -m app.sync.cli --source advisor --email

# Both upload and email
python -m app.sync.cli --source advisor --upload --email
```

### 4) Run the full daily scheduler
```bash
python -m app.sync.scheduler
```

Daily schedule:
| Time | Task |
|---|---|
| 07:00 | Google Drive health folder scan |
| 07:10 | Research paper recommendations |
| 07:20 | Oura Ring data sync + upload to Drive |
| 07:30 | AI daily advisor → Drive + email |

The advisor runs last so it has access to the freshest Oura data and any new health reports from Drive.

### 5) Upload health reports (PDF)
```bash
python -m app.sync.cli --source blood --date 2026-01-17 --path data/labs/blood.pdf
python -m app.sync.cli --source sperm --date 2026-01-17 --path data/labs/sperm.pdf
python -m app.sync.cli --source genetic --date 2026-01-17 --path data/labs/genetic.pdf
python -m app.sync.cli --source urine --date 2026-01-17 --path data/labs/urine.pdf
python -m app.sync.cli --source conclusion --date 2026-01-17 --path data/labs/report.pdf
python -m app.sync.cli --source prescription --date 2026-01-17 --path data/labs/rx.pdf
```

Or place PDFs in `My Drive > me > health` and run `python -m app.sync.cli --source gdrive`.

### 6) Analyze medical images (MRI / X-ray / CT)
```bash
# Analyze a local image
python -m app.sync.cli --source scan --path data/labs/knee_mri.jpg

# Analyze and upload report to Google Drive
python -m app.sync.cli --source scan --path data/labs/chest_xray.png --upload
```

Images placed in `My Drive > me > health` with medical filenames (e.g. `knee_mri.jpg`, `spine_xray.png`, `ct_scan_brain.jpg`) are **automatically detected and analyzed** during the daily Drive scan at 07:00.

The analyzer flags: tumours, ligament tears (ACL/MCL/etc.), meniscus damage, fractures, disc herniations, degenerative changes, effusions, and other abnormalities. Severity levels: NORMAL / MINOR FINDINGS / MODERATE CONCERN / URGENT.

### 7) Rule-based CLI report
```bash
python -m app.cli --data data/sample_daily.json --goals energy cognition
```

### 8) Web UI
```bash
uvicorn app.web:app --reload
```
Open http://127.0.0.1:8000

## Google Drive setup

1. Create an OAuth 2.0 credential in [Google Cloud Console](https://console.cloud.google.com/) (Desktop app)
2. Enable the **Google Drive API**
3. Download client secrets as `credentials.json`
4. Set `GDRIVE_CREDENTIALS_DIR` to the directory containing it
5. On first run a browser opens for consent; `token.json` is cached automatically
6. The folder `My Drive > me > health` must exist

### Google Drive folder structure

Reports are organized in typed subfolders. Files in subfolders are auto-classified:

```
My Drive/
  me/
    health/
      genetic/                         ← genetic test results (auto: genetic_test)
        mthfr_results.pdf
        23andme_raw_data.pdf
      blood/                           ← blood work (auto: blood_test)
      sperm/                           ← semen analysis (auto: sperm_test)
      urine/                           ← urinalysis (auto: urine_test)
      conclusion/                      ← doctor conclusions
      prescription/                    ← prescriptions
      knee_mri.jpg                     ← medical images (auto-analyzed by Claude Vision)
      2026/                            ← calendar output (auto-created)
        02/
          10/
            oura_2026-02-10.txt              ← Oura Ring daily data
            daily_advice_2026-02-10.txt      ← AI doctor recommendations
            scan_knee_mri_2026-02-10.txt     ← Medical image analysis (if any)
          11/
            oura_2026-02-11.txt
            daily_advice_2026-02-11.txt
```

## How the AI advisor works

Every day at 07:30 (or on demand via CLI), the advisor:

1. Reads today's Oura Ring data (sleep hours, HRV, resting HR, steps, activity)
2. Loads all available health reports (sperm analysis, blood work, genetic tests, etc.)
3. Sends everything to **Claude Opus 4.6** with the system prompt: *"You are an experienced GP and reproductive health specialist. Your patient is actively trying to conceive."*
4. Claude returns a structured daily plan:
   - **Top 3 actions today** — specific, timed, with dosages and direct links to sperm motility/energy
   - **Key metrics to watch** — which numbers are good, which need attention
   - **Nutrition focus** — one meal/supplement recommendation tied to the data
   - **What to avoid today** — one concrete thing to skip based on recovery state
5. The plan is saved locally (`data/ingested/advisor/daily_advice_YYYY-MM-DD.json`)
6. Both Oura data and the advice are uploaded to Google Drive in calendar folders: `me/health/YYYY/MM/DD/`
7. If `EMAIL_TO` and `SMTP_HOST` are configured, the scheduler automatically emails the plan as a styled HTML email. Use `--email` flag with the CLI for on-demand email delivery

## Storage

If `DATABASE_URL` is set, data is stored in PostgreSQL (`daily_data`, `lab_documents`, `research_papers`, `research_recommendations`). Otherwise, JSON files are written to `data/ingested/`.

## Notes
Personalize signal thresholds in `app/recommendations.py`.
