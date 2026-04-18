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
from typing import Optional

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

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
            "gemini_model": config.gemini_model,
            "services": {
                "oura": bool(config.oura_access_token),
                "gemini": bool(config.google_api_key),
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

    # ── Action tracking: mark done (clickable from email) ──
    @dashboard_app.get("/action/done")
    async def action_done(date: str, idx: int):
        from app.sync.action_tracker import (
            load_actions_with_sheets,
            mark_action_done_with_sheets,
        )

        success = mark_action_done_with_sheets(config, date, idx)
        actions = load_actions_with_sheets(config, date)
        return HTMLResponse(_render_action_page(date, idx, actions, done=True, success=success))

    @dashboard_app.get("/action/undo")
    async def action_undo(date: str, idx: int):
        from app.sync.action_tracker import (
            load_actions_with_sheets,
            mark_action_undone_with_sheets,
        )

        mark_action_undone_with_sheets(config, date, idx)
        actions = load_actions_with_sheets(config, date)
        return HTMLResponse(_render_action_page(date, idx, actions, done=False, success=True))

    # ── WhatsApp inbound webhook (I1, I2, I3) ──
    @dashboard_app.post("/whatsapp/inbound")
    async def whatsapp_inbound(payload: dict):
        """Receive a user WhatsApp reply and return the response string.

        Called by the OpenClaw personal-doctor agent (which binds the WhatsApp
        channel via `openclaw agents bind --agent personal-doctor --bind whatsapp`).
        Expected JSON shape: {"from": "+393491913903", "body": "1"}.
        Reply shape: {"reply": "✅ Marked #1 done: ..."}.
        """
        from app.sync.whatsapp_inbound import handle_inbound_message

        body = (payload or {}).get("body", "") or ""
        from_number = (payload or {}).get("from", "") or None
        reply = handle_inbound_message(config, body, from_number=from_number)
        return JSONResponse({"reply": reply})

    @dashboard_app.get("/actions")
    async def view_actions(date: Optional[str] = None):
        from app.sync.action_tracker import load_actions_with_sheets

        if not date:
            date = datetime.now(tz=config.timezone).strftime("%Y-%m-%d")
        actions = load_actions_with_sheets(config, date)
        return JSONResponse({"date": date, "actions": actions})

    @dashboard_app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard():
        from app.sync.action_tracker import (
            compute_streaks,
            load_action_history_with_sheets,
        )
        from app.sync.trend_analyzer import (
            compute_metric_trends,
            compute_rolling_averages,
            load_oura_history,
        )

        today = datetime.now(tz=config.timezone).date()
        history = load_action_history_with_sheets(config, num_days=7)
        streaks = compute_streaks(config.data_dir)
        oura_history = load_oura_history(config.data_dir, today)
        averages = compute_rolling_averages(oura_history)
        trends = compute_metric_trends(oura_history)

        return _render_dashboard(today, history, streaks, averages, trends, config)

    return dashboard_app


def start_server():
    """Start the combined web server + background scheduler."""
    from apscheduler.schedulers.background import BackgroundScheduler

    from app.sync.config import load_config
    from app.sync.scheduler import (
        run_anomaly_detector_job,
        run_daily_advisor,
        run_gdrive_sync,
        run_oura_sync,
        run_research_sync,
        run_supplement_check_job,
        run_weekly_retro_job,
        run_whatsapp_evening_nudge,
    )

    config = load_config()

    # Start background scheduler
    scheduler = BackgroundScheduler(timezone=config.timezone)
    scheduler.add_job(run_research_sync, "cron", hour=7, minute=20,
                      id="research_daily", misfire_grace_time=3600)
    scheduler.add_job(run_gdrive_sync, "cron", hour=7, minute=30,
                      id="gdrive_daily", misfire_grace_time=3600)
    scheduler.add_job(run_oura_sync, "cron", hour=7, minute=40,
                      id="oura_daily", misfire_grace_time=3600)
    scheduler.add_job(run_daily_advisor, "cron", hour=8, minute=0,
                      id="advisor_daily", misfire_grace_time=3600)
    scheduler.add_job(run_anomaly_detector_job, "cron", hour=7, minute=41,
                      id="anomaly_daily", misfire_grace_time=3600)
    scheduler.add_job(run_supplement_check_job, "cron", hour=7, minute=45,
                      id="supplement_daily", misfire_grace_time=3600)
    scheduler.add_job(run_whatsapp_evening_nudge, "cron", hour=21, minute=0,
                      id="evening_nudge", misfire_grace_time=3600)
    scheduler.add_job(run_weekly_retro_job, "cron", day_of_week="sun", hour=18, minute=0,
                      id="weekly_retro", misfire_grace_time=7200)
    scheduler.start()

    # Catch up if today's advisor was missed (e.g., Mac just woke from sleep)
    today = datetime.now(tz=config.timezone).date()
    advice_file = config.data_dir / "advisor" / f"daily_advice_{today}.json"
    now_hour = datetime.now(tz=config.timezone).hour
    if not advice_file.exists() and now_hour >= 8:
        import threading
        logger.info("Startup catch-up: today's advisor not yet sent, triggering now")
        threading.Thread(target=run_gdrive_sync, daemon=True).start()
        threading.Thread(target=run_oura_sync, daemon=True).start()
        # Delay advisor slightly so Oura data is fetched first
        def _delayed_advisor():
            import time
            time.sleep(30)
            run_daily_advisor()
        threading.Thread(target=_delayed_advisor, daemon=True).start()

    logger.info("=" * 60)
    logger.info("  Personal Doctor — Local Server")
    logger.info("=" * 60)
    logger.info(f"  Timezone: {config.timezone}")
    logger.info(f"  Data dir: {config.data_dir}")
    logger.info(f"  Logs: {LOG_FILE}")
    logger.info("")
    logger.info("  Schedule:")
    logger.info("    07:20  Research sync (PubMed + OpenAlex)")
    logger.info("    07:30  Google Drive health folder scan")
    logger.info("    07:40  Oura Ring data sync")
    logger.info("    07:41  Anomaly detector")
    logger.info("    07:45  Supplement inventory check")
    logger.info("    08:00  AI daily advisor → email + WhatsApp")
    logger.info("    21:00  WhatsApp evening nudge (if actions still open)")
    logger.info("    Sun 18:00  Weekly retrospective email")
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


def _render_action_page(
    date: str, idx: int, actions: list, *, done: bool, success: bool
) -> str:
    """Render HTML confirmation page when user clicks Mark Done / Undo."""
    if done and success:
        banner = (
            '<div style="background:#ecfdf5;border:1px solid #6ee7b7;border-radius:8px;'
            'padding:16px;margin-bottom:20px;text-align:center;">'
            '<span style="font-size:32px;">&#x2705;</span>'
            '<h2 style="color:#059669;margin:8px 0 0;">Action completed!</h2></div>'
        )
    elif not done:
        banner = (
            '<div style="background:#fefce8;border:1px solid #fde68a;border-radius:8px;'
            'padding:16px;margin-bottom:20px;text-align:center;">'
            '<span style="font-size:32px;">&#x21A9;</span>'
            '<h2 style="color:#d97706;margin:8px 0 0;">Action unmarked</h2></div>'
        )
    else:
        banner = (
            '<div style="background:#fef2f2;border:1px solid #fca5a5;border-radius:8px;'
            'padding:16px;margin-bottom:20px;text-align:center;">'
            '<h2 style="color:#dc2626;margin:0;">Action not found</h2></div>'
        )

    rows = ""
    for a in actions:
        check = "&#x2705;" if a.get("done") else "&#x2B1C;"
        title = a.get("title", "?")
        if a.get("done"):
            toggle_url = f"/action/undo?date={date}&idx={a['idx']}"
            toggle_label = "Undo"
        else:
            toggle_url = f"/action/done?date={date}&idx={a['idx']}"
            toggle_label = "Mark Done"
        rows += (
            f'<div style="padding:10px 0;border-bottom:1px solid #f3f4f6;">'
            f'<span style="font-size:20px;margin-right:8px;">{check}</span>'
            f'<strong>{a["idx"]+1}.</strong> {title} '
            f'<a href="{toggle_url}" style="color:#2563eb;font-size:13px;'
            f'margin-left:8px;">[{toggle_label}]</a></div>'
        )

    done_count = sum(1 for a in actions if a.get("done"))
    progress = f"{done_count}/{len(actions)}" if actions else "0/0"

    return f"""\
<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Personal Doctor - Actions</title></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
max-width:500px;margin:40px auto;padding:20px;color:#1a1a1a;">
{banner}
<h3 style="color:#1e40af;">Today's Actions ({date}) &mdash; {progress} done</h3>
{rows}
<div style="margin-top:20px;">
<a href="/dashboard" style="color:#2563eb;text-decoration:none;font-weight:600;">
&#x1F4CA; View Dashboard</a></div>
</body></html>"""


def _render_dashboard(today, history, streaks, averages, trends, config) -> str:
    """Render the full dashboard HTML page."""
    # Streak badges
    any_streak = streaks.get("any_action", 0)
    all_streak = streaks.get("all_actions", 0)

    streak_html = (
        '<div style="display:flex;gap:16px;margin-bottom:24px;">'
        f'<div style="background:#f0fdf4;border:1px solid #86efac;border-radius:8px;'
        f'padding:12px 20px;text-align:center;">'
        f'<div style="font-size:28px;font-weight:700;color:#059669;">{any_streak}</div>'
        f'<div style="font-size:12px;color:#6b7280;">day streak (any action)</div></div>'
        f'<div style="background:#eff6ff;border:1px solid #93c5fd;border-radius:8px;'
        f'padding:12px 20px;text-align:center;">'
        f'<div style="font-size:28px;font-weight:700;color:#2563eb;">{all_streak}</div>'
        f'<div style="font-size:12px;color:#6b7280;">day streak (all actions)</div></div>'
        "</div>"
    )

    # Action history table
    history_rows = ""
    for day_rec in history:
        d = day_rec["date"]
        actions = day_rec.get("actions", [])
        done_count = sum(1 for a in actions if a.get("done"))
        total = len(actions)
        rate = day_rec.get("completion_rate", 0)
        pct = f"{rate*100:.0f}%"
        bar_color = "#059669" if rate >= 0.67 else "#d97706" if rate >= 0.34 else "#dc2626"
        bar_width = max(rate * 100, 4)
        action_names = " | ".join(
            f"{'&#x2705;' if a.get('done') else '&#x2B1C;'} {a.get('title', '?')}"
            for a in actions
        )
        history_rows += (
            f'<tr><td style="padding:8px;font-weight:600;white-space:nowrap;">{d}</td>'
            f'<td style="padding:8px;">{done_count}/{total}</td>'
            f'<td style="padding:8px;"><div style="background:#e5e7eb;border-radius:4px;'
            f'height:12px;width:100px;"><div style="background:{bar_color};height:12px;'
            f'border-radius:4px;width:{bar_width}px;"></div></div></td>'
            f'<td style="padding:8px;font-size:13px;color:#6b7280;">{action_names}</td></tr>'
        )

    if not history_rows:
        history_rows = (
            '<tr><td colspan="4" style="padding:16px;text-align:center;color:#9ca3af;">'
            "No action history yet. Complete your first daily actions!</td></tr>"
        )

    # Metric trends table
    metric_labels = {
        "avg_hrv": ("HRV", "ms"),
        "avg_resting_hr": ("Resting HR", "bpm"),
        "avg_sleep_hours": ("Sleep", "hrs"),
        "avg_deep_sleep_min": ("Deep Sleep", "min"),
        "avg_steps": ("Steps", ""),
        "avg_readiness": ("Readiness", "/100"),
    }
    trend_keys = {
        "avg_hrv": "hrv",
        "avg_resting_hr": "resting_hr",
        "avg_sleep_hours": "sleep_hours",
        "avg_deep_sleep_min": "deep_sleep_min",
        "avg_steps": "steps",
        "avg_readiness": "readiness_score",
    }
    trend_arrows = {"improving": "&#x2197; improving", "declining": "&#x2198; declining", "stable": "&#x2194; stable"}

    metric_rows = ""
    for key, (label, unit) in metric_labels.items():
        avg_val = averages.get(key)
        if avg_val is None:
            continue
        trend_key = trend_keys.get(key, "")
        trend_dir = trends.get(trend_key, "stable")
        trend_text = trend_arrows.get(trend_dir, trend_dir)
        trend_color = "#059669" if trend_dir == "improving" else "#dc2626" if trend_dir == "declining" else "#6b7280"
        metric_rows += (
            f'<tr><td style="padding:8px;font-weight:500;">{label}</td>'
            f'<td style="padding:8px;">{avg_val:.1f} {unit}</td>'
            f'<td style="padding:8px;color:{trend_color};">{trend_text}</td></tr>'
        )

    if not metric_rows:
        metric_rows = (
            '<tr><td colspan="3" style="padding:16px;text-align:center;color:#9ca3af;">'
            "No Oura data available for trend analysis.</td></tr>"
        )

    return f"""\
<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Personal Doctor Dashboard</title>
<style>
body {{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
       max-width:800px;margin:0 auto;padding:20px;color:#1a1a1a;background:#fafafa; }}
h1 {{ color:#2563eb;margin-bottom:4px; }}
h2 {{ color:#1e40af;margin-top:32px;border-bottom:2px solid #dbeafe;padding-bottom:6px; }}
table {{ border-collapse:collapse;width:100%; }}
th {{ text-align:left;padding:8px;background:#f1f5f9;color:#475569;font-size:13px; }}
.subtitle {{ color:#6b7280;font-size:14px;margin-bottom:24px; }}
</style></head>
<body>
<h1>&#x1F3E5; Personal Doctor Dashboard</h1>
<div class="subtitle">{today.isoformat()} &bull; Schedule: 08:00 daily</div>

<form method="post" action="/run" style="margin-bottom:20px;"
      onsubmit="fetch('/run',{{method:'POST'}}).then(r=>r.json()).then(d=>{{document.getElementById('run-status').innerText=d.message||d.status;}});return false;">
  <button type="submit" style="background:#2563eb;color:#fff;border:none;
    padding:10px 20px;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;">
    &#x25B6; Trigger full pipeline now
  </button>
  <span id="run-status" style="margin-left:12px;color:#6b7280;font-size:13px;"></span>
</form>

{streak_html}

<h2>&#x1F4CB; Action History (7 days)</h2>
<table>
<tr><th>Date</th><th>Done</th><th>Progress</th><th>Actions</th></tr>
{history_rows}
</table>

<h2>&#x1F4C8; 7-Day Metric Trends</h2>
<table>
<tr><th>Metric</th><th>7-Day Avg</th><th>Trend</th></tr>
{metric_rows}
</table>

<div style="margin-top:32px;padding-top:16px;border-top:1px solid #e5e7eb;
font-size:12px;color:#9ca3af;">
Personal Doctor &bull; <a href="/advice" style="color:#2563eb;">Latest advice</a>
&bull; <a href="/health" style="color:#2563eb;">Health check</a></div>
</body></html>"""


if __name__ == "__main__":
    start_server()
