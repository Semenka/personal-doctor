"""Local server combining FastAPI web dashboard with the background scheduler.

Designed for always-on Mac Mini deployment:
  - Background scheduler runs daily pipeline (Oura → Drive → Advisor → Email)
  - FastAPI serves the web dashboard on port 8000
  - /health endpoint for monitoring
  - /run endpoint to trigger pipeline on demand
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

# Configure logging to both stdout and file
LOG_DIR = Path(os.getenv("HEALTH_LOG_DIR", os.path.expanduser("~/personal-doctor/logs")))
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "personal-doctor.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
logger = logging.getLogger("personal-doctor")


def create_app() -> FastAPI:
    """Create FastAPI app with scheduler attached."""
    from app.sync.config import load_config

    config = load_config()

    # Import the existing web app (dashboard)
    from app.web import app as dashboard_app

    # ── Health check ──
    @dashboard_app.get("/health")
    async def health():
        return JSONResponse({
            "status": "ok",
            "timestamp": datetime.now(tz=config.timezone).isoformat(),
            "timezone": str(config.timezone),
            "services": {
                "oura": bool(config.oura_access_token),
                "anthropic": bool(config.anthropic_api_key),
                "smtp": bool(config.smtp_host and config.smtp_password),
                "gdrive": bool(config.gdrive_credentials_dir),
            },
        })

    # ── On-demand pipeline trigger ──
    @dashboard_app.post("/run")
    async def run_pipeline_now():
        """Trigger the daily pipeline immediately (non-blocking)."""
        import threading
        from app.sync.run_pipeline import main as run_pipeline

        def _run():
            try:
                logger.info("Manual pipeline trigger started")
                exit_code = run_pipeline()
                logger.info(f"Manual pipeline finished (exit={exit_code})")
            except Exception as exc:
                logger.error(f"Manual pipeline failed: {exc}")

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        return JSONResponse({
            "status": "started",
            "message": "Pipeline triggered. Check /logs for progress.",
        })

    # ── Recent logs viewer ──
    @dashboard_app.get("/logs")
    async def view_logs():
        """Return the last 100 lines of the log file."""
        if LOG_FILE.exists():
            lines = LOG_FILE.read_text(encoding="utf-8").splitlines()
            return JSONResponse({"lines": lines[-100:]})
        return JSONResponse({"lines": []})

    # ── Last advice viewer ──
    @dashboard_app.get("/advice")
    async def last_advice():
        """Return the most recent daily advice."""
        advisor_dir = config.data_dir / "advisor"
        if not advisor_dir.exists():
            return JSONResponse({"advice": None, "message": "No advice generated yet."})
        files = sorted(advisor_dir.glob("daily_advice_*.json"), reverse=True)
        if not files:
            return JSONResponse({"advice": None, "message": "No advice generated yet."})
        data = json.loads(files[0].read_text())
        return JSONResponse(data)

    return dashboard_app


def start_server():
    """Start the combined web server + background scheduler."""
    from apscheduler.schedulers.background import BackgroundScheduler

    from app.sync.config import load_config
    from app.sync.scheduler import (
        run_daily_advisor,
        run_gdrive_sync,
        run_oura_sync,
    )

    config = load_config()

    # Start background scheduler
    scheduler = BackgroundScheduler(timezone=config.timezone)
    scheduler.add_job(run_gdrive_sync, "cron", hour=7, minute=0, id="gdrive_daily")
    scheduler.add_job(run_oura_sync, "cron", hour=7, minute=20, id="oura_daily")
    scheduler.add_job(run_daily_advisor, "cron", hour=7, minute=30, id="advisor_daily")
    scheduler.start()

    logger.info("=" * 60)
    logger.info("  Personal Doctor — Local Server")
    logger.info("=" * 60)
    logger.info(f"  Timezone: {config.timezone}")
    logger.info(f"  Data dir: {config.data_dir}")
    logger.info(f"  Logs: {LOG_FILE}")
    logger.info("")
    logger.info("  Schedule:")
    logger.info("    07:00  Google Drive health folder scan")
    logger.info("    07:20  Oura Ring data sync")
    logger.info("    07:30  AI daily advisor → email")
    logger.info("")
    logger.info("  Endpoints:")
    logger.info("    http://localhost:8000         Web dashboard")
    logger.info("    http://localhost:8000/health   Health check")
    logger.info("    http://localhost:8000/advice   Last advice (JSON)")
    logger.info("    POST http://localhost:8000/run  Trigger pipeline now")
    logger.info("    http://localhost:8000/logs     Recent logs")
    logger.info("=" * 60)

    app = create_app()

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))

    try:
        uvicorn.run(app, host=host, port=port, log_level="info")
    finally:
        scheduler.shutdown()


if __name__ == "__main__":
    start_server()
