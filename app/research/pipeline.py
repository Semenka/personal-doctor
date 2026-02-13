from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Iterable, List

from .models import ResearchPaper, ResearchRecommendation
from .openalex import fetch_top_papers
from ..sync.config import SyncConfig
from ..sync.storage import (
    delete_research_papers_except,
    save_research_papers_db,
    save_research_recommendations_db,
)

JOURNALS = ["The Lancet", "The New England Journal of Medicine", "JAMA"]

GOAL_ACTIONS = {
    "energy": [
        "Prioritize 7.5-8.5 hours sleep with a consistent bedtime",
        "Add 10-15 minutes of daylight exposure within 2 hours of waking",
    ],
    "reproductive": [
        "Reduce heat exposure (no hot baths/saunas) for 24 hours",
        "Add zinc + selenium-rich foods (pumpkin seeds, eggs, seafood)",
    ],
    "cognition": [
        "Schedule a 90-minute deep work block during peak alertness",
        "Include omega-3 rich foods (salmon, chia, walnuts) today",
    ],
    "sport": [
        "Complete a 10-minute neuromuscular warm-up before training",
        "Keep training intensity moderate if recovery is low",
    ],
}


def _estimate_impact_pct(cited_by_count: int) -> float:
    return round(min(12.0, 2.0 + cited_by_count / 500), 1)


def _build_recommendations(
    day: date, papers: Iterable[ResearchPaper]
) -> List[ResearchRecommendation]:
    paper_list = list(papers)
    recommendations: List[ResearchRecommendation] = []
    if not paper_list:
        return recommendations
    for idx, (goal, actions) in enumerate(GOAL_ACTIONS.items()):
        paper = paper_list[min(idx, len(paper_list) - 1)]
        action = actions[0]
        recommendations.append(
            ResearchRecommendation(
                date=day,
                goal=goal,
                action=action,
                expected_impact_pct=_estimate_impact_pct(paper.cited_by_count),
                evidence=(
                    f"{paper.title} ({paper.journal}, cited {paper.cited_by_count})"
                ),
                paper_title=paper.title,
                journal=paper.journal,
                cited_by_count=paper.cited_by_count,
                url=paper.url,
            )
        )
    return recommendations


def run_daily_research(
    config: SyncConfig, day: date, journals: Iterable[str] = JOURNALS
) -> List[ResearchRecommendation]:
    papers = fetch_top_papers(config, journals)
    save_research_papers_db(config, day.isoformat(), [asdict(paper) for paper in papers])
    delete_research_papers_except(config, day.isoformat())
    recommendations = _build_recommendations(day, papers)
    save_research_recommendations_db(
        config,
        [
            {
                "date": rec.date.isoformat(),
                "goal": rec.goal,
                "action": rec.action,
                "expected_impact_pct": rec.expected_impact_pct,
                "evidence": rec.evidence,
                "paper_title": rec.paper_title,
                "journal": rec.journal,
                "cited_by_count": rec.cited_by_count,
                "url": rec.url,
            }
            for rec in recommendations
        ],
    )
    return recommendations
