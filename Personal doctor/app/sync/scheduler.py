from __future__ import annotations

import time


from apscheduler.schedulers.background import BackgroundScheduler

from .config import load_config
from .pipeline import load_oura_daily
from .storage import init_db, save_daily_payload_db, write_daily_json


def start_scheduler() -> None:
    config = load_config()
    scheduler = BackgroundScheduler(timezone=config.timezone)
    scheduler.add_job(run_oura_sync, "cron", hour=7, minute=0, id="oura_daily")
    scheduler.add_job(
        run_research_sync,
        "cron",
        hour=7,
        minute=10,
        id="research_daily",
    )
    scheduler.start()
    print("Scheduler started: Oura daily at 07:00, research at 07:10")

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        scheduler.shutdown()


if __name__ == "__main__":
    start_scheduler()


def run_oura_sync() -> None:
    from datetime import datetime

    config = load_config()
    day = datetime.now(tz=config.timezone).date()
    if config.database_url:
        init_db(config)
    payload = load_oura_daily(config, day)
    if config.database_url:
        save_daily_payload_db(config, payload)
        print("Saved daily payload to Postgres.")
    else:
        target = write_daily_json(config.data_dir, day.isoformat(), payload)
        print(f"Saved {target}")


def run_research_sync() -> None:
    from datetime import datetime

    config = load_config()
    day = datetime.now(tz=config.timezone).date()
    if not config.database_url:
        raise RuntimeError("DATABASE_URL is required for research sync")
    init_db(config)
    from ..research.pipeline import run_daily_research

    run_daily_research(config, day)
    print("Saved daily research recommendations to Postgres.")
