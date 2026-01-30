from __future__ import annotations

import argparse
from datetime import date, datetime
from pathlib import Path

from .config import load_config
from .pipeline import load_oura_daily
from .placeholders import load_annual_checkups, load_blood_tests, load_urine_tests
from .storage import (
    init_db,
    save_daily_payload_db,
    save_lab_document_db,
    write_daily_json,
    write_lab_document_json,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Health data sync")
    parser.add_argument(
        "--source",
        choices=["oura", "blood", "urine", "annual", "research"],
        default="oura",
    )
    parser.add_argument("--date", help="ISO date (YYYY-MM-DD)")
    parser.add_argument("--path", help="Path to lab/check-up PDF")
    args = parser.parse_args()

    day = date.fromisoformat(args.date) if args.date else datetime.now().date()
    config = load_config()

    if config.database_url:
        init_db(config)

    if args.source == "oura":
        payload = load_oura_daily(config, day)
        if config.database_url:
            save_daily_payload_db(config, payload)
            print("Saved daily payload to Postgres.")
        else:
            target = write_daily_json(config.data_dir, day.isoformat(), payload)
            print(f"Saved {target}")
        return

    if args.source == "research":
        if not config.database_url:
            raise SystemExit("DATABASE_URL is required for research sync")
        from ..research.pipeline import run_daily_research

        run_daily_research(config, day)
        print("Saved daily research recommendations to Postgres.")
        return

    if not args.path:
        raise SystemExit("--path is required for blood/urine/annual sources")

    path = Path(args.path)
    if args.source == "blood":
        payload = load_blood_tests(path)
    elif args.source == "urine":
        payload = load_urine_tests(path)
    else:
        payload = load_annual_checkups(path)

    raw_text = payload.get("text", "")
    metadata = {k: v for k, v in payload.items() if k != "text"}
    if config.database_url:
        save_lab_document_db(config, payload["kind"], day.isoformat(), raw_text, metadata)
        print("Saved lab document to Postgres.")
    else:
        target = write_lab_document_json(
            config.data_dir, payload["kind"], day.isoformat(), payload
        )
        print(f"Saved {target}")


if __name__ == "__main__":
    main()
