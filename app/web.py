from pathlib import Path
from typing import List

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .models import Goals
from .research.models import ResearchRecommendation
from .recommendations import build_daily_report

app = FastAPI()
BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/report", response_class=HTMLResponse)
async def report(
    request: Request,
    goals: List[str] = Form(default=[]),
    day: str = Form(...),
):
    from .io import load_daily_payload
    from .sync.config import load_config
    from .sync.storage import load_daily_payload as load_daily_payload_from_sync
    from .sync.storage import load_research_recommendations_db

    config = load_config()
    error_message = None
    report = None
    try:
        payload = load_daily_payload_from_sync(config, day)
        data = load_daily_payload(payload)
        report = build_daily_report(data, Goals.from_list(goals))

        if config.database_url:
            records = load_research_recommendations_db(config, data.date)
            report.research_recommendations = [
                ResearchRecommendation(
                    date=data.date,
                    goal=rec["goal"],
                    action=rec["action"],
                    expected_impact_pct=float(rec["expected_impact_pct"]),
                    evidence=rec["evidence"],
                    paper_title=rec["paper_title"],
                    journal=rec["journal"],
                    cited_by_count=int(rec["cited_by_count"]),
                    url=rec.get("url"),
                )
                for rec in records
            ]
        else:
            error_message = "DATABASE_URL is not set; research recommendations unavailable."
    except FileNotFoundError as exc:
        error_message = str(exc)
    except Exception as exc:  # noqa: BLE001 - surface error in UI
        error_message = f"Unable to generate report: {exc}"

    return templates.TemplateResponse(
        "report.html",
        {"request": request, "report": report, "error_message": error_message},
    )
