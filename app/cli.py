import argparse
from datetime import date, datetime

from .io import load_daily_data, load_daily_payload as load_daily_payload_from_dict
from .models import Goals
from .recommendations import build_daily_report
from .research.models import ResearchRecommendation
from .sync.config import load_config
from .sync.storage import (
    load_daily_payload as load_daily_payload_from_sync,
    load_research_recommendations_db,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily health advisor")
    parser.add_argument(
        "--data",
        help="Optional path to daily JSON data (overrides synced data)",
    )
    parser.add_argument("--date", help="ISO date (YYYY-MM-DD) for synced data")
    parser.add_argument(
        "--goals",
        nargs="+",
        default=[],
        help="Goals: energy, reproductive, cognition, sport",
    )
    args = parser.parse_args()

    if args.data:
        data = load_daily_data(args.data)
    else:
        config = load_config()
        day = date.fromisoformat(args.date) if args.date else datetime.now().date()
        payload = load_daily_payload_from_sync(config, day.isoformat())
        data = load_daily_payload_from_dict(payload)
    goals = Goals.from_list(args.goals)
    report = build_daily_report(data, goals)

    config = load_config()
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

    print(f"Summary: {report.summary}")
    print("\nRecommendations:")
    for rec in report.recommendations:
        print(f"- [{rec.category}] {rec.title}")
        for item in rec.details:
            print(f"  - {item}")

    if report.research_recommendations:
        print("\nResearch-backed actions:")
        for rec in report.research_recommendations:
            print(f"- [{rec.goal}] {rec.action} ({rec.expected_impact_pct}% expected)")


if __name__ == "__main__":
    main()
