"""Daily research pipeline.

Aggregates papers from OpenAlex (journal-top-cited) and PubMed (topic-driven
queries) into per-day recommendation records. Storage adapts:
- If DATABASE_URL is set: persists to Postgres tables `research_papers` and
  `research_recommendations`.
- Otherwise: writes `data/ingested/research/papers_YYYY-MM-DD.json` and
  `recommendations_YYYY-MM-DD.json`, plus `latest.json` for fast loads.

The advisor pipeline reads the day's recommendations via `load_research_for_day`
and injects them into the Gemini prompt so today's plan references live papers.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List

from .goals import queries_for_day
from .models import ResearchPaper, ResearchRecommendation
from .openalex import fetch_top_papers
from .pubmed import fetch_papers_for_query
from ..sync.config import SyncConfig

# General top-tier journals plus the specialist ones that actually move the
# user's two goals (fertility: andrology/reproduction; energy: sleep/endocrine).
# A generalist-only list surfaces prestige papers with no bearing on either.
JOURNALS = [
    "The Lancet",
    "The New England Journal of Medicine",
    "JAMA",
    "BMJ",
    "Nature Medicine",
    "Human Reproduction",
    "Human Reproduction Update",
    "Fertility and Sterility",
    "Andrology",
    "European Urology",
    "Sleep",
    "The Journal of Clinical Endocrinology & Metabolism",
]

# Actions map each goal → a specific protocol (short-form, used as the
# recommendation "action" text). Keep this in sync with goals.py keys.
GOAL_ACTIONS = {
    "sperm_motility": "Start the CoQ10 / Ubiquinol 200mg AM protocol today",
    "testosterone": "Add 15 min of Zone-2 cardio plus Vitamin D 2000 IU with breakfast",
    "hrv_recovery": "4-7-8 paced breathing: 5 min before bed",
    "energy_cognition": "Creatine 5g with your largest carb meal",
    "sleep": "Magnesium glycinate 400 mg 60 min before sleep",
    "fertility_conception": "Reduce scrotal heat: no laptops on lap, no hot baths for 48 h",
    # Legacy (still used by older ingested data)
    "energy": "Prioritize 7.5-8.5 h sleep with a consistent bedtime",
    "reproductive": "Reduce heat exposure for 24 hours",
    "cognition": "Schedule a 90-min deep work block during peak alertness",
    "sport": "10-minute neuromuscular warm-up before training",
}


def _estimate_impact_pct(cited_by_count: int) -> float:
    return round(min(12.0, 2.0 + cited_by_count / 500), 1)


def _research_dir(data_dir: Path) -> Path:
    d = data_dir / "research"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_local(
    data_dir: Path,
    day: date,
    papers: List[ResearchPaper],
    recs: List[ResearchRecommendation],
) -> None:
    base = _research_dir(data_dir)
    papers_payload = [asdict(p) for p in papers]
    recs_payload = [
        {
            "date": r.date.isoformat(),
            "goal": r.goal,
            "action": r.action,
            "expected_impact_pct": r.expected_impact_pct,
            "evidence": r.evidence,
            "paper_title": r.paper_title,
            "journal": r.journal,
            "cited_by_count": r.cited_by_count,
            "url": r.url,
        }
        for r in recs
    ]
    (base / f"papers_{day.isoformat()}.json").write_text(
        json.dumps(papers_payload, indent=2, ensure_ascii=False)
    )
    (base / f"recommendations_{day.isoformat()}.json").write_text(
        json.dumps(recs_payload, indent=2, ensure_ascii=False)
    )
    # Pointer file for easy "latest" lookup
    (base / "latest.json").write_text(
        json.dumps(
            {"date": day.isoformat(), "recommendations": recs_payload},
            indent=2,
            ensure_ascii=False,
        )
    )


def _build_recommendations(
    day: date, goal_to_papers: Dict[str, List[ResearchPaper]]
) -> List[ResearchRecommendation]:
    recommendations: List[ResearchRecommendation] = []
    for goal, papers in goal_to_papers.items():
        if not papers:
            continue
        paper = papers[0]
        action = GOAL_ACTIONS.get(goal, "See today's advice for details")
        recommendations.append(
            ResearchRecommendation(
                date=day,
                goal=goal,
                action=action,
                expected_impact_pct=_estimate_impact_pct(paper.cited_by_count),
                evidence=(
                    f"{paper.title} ({paper.journal}"
                    + (f", cited {paper.cited_by_count}" if paper.cited_by_count else "")
                    + ")"
                ),
                paper_title=paper.title,
                journal=paper.journal,
                cited_by_count=paper.cited_by_count,
                url=paper.url,
            )
        )
    return recommendations


def run_daily_research(
    config: SyncConfig,
    day: date,
    journals: Iterable[str] = JOURNALS,
    per_day_topics: int = 3,
) -> List[ResearchRecommendation]:
    """Fetch and store research recommendations for ``day``.

    Topic queries drive the bulk of results (PubMed); journal lists provide
    high-quality seed papers from OpenAlex. Persists to Postgres if
    DATABASE_URL is set, otherwise to local JSON.
    """
    goal_to_papers: Dict[str, List[ResearchPaper]] = {}
    all_papers: List[ResearchPaper] = []
    seen_ids: set[str] = set()

    # PubMed topic queries (primary signal — live, topical)
    try:
        for goal, query, _mesh, since_months in queries_for_day(day, per_day=per_day_topics):
            try:
                papers = fetch_papers_for_query(
                    query, since_months=since_months, retmax=3, mailto=config.openalex_mailto
                )
            except Exception as exc:
                print(f"  PubMed fetch failed for {goal}: {exc}")
                papers = []
            deduped: List[ResearchPaper] = []
            for p in papers:
                if p.work_id in seen_ids:
                    continue
                seen_ids.add(p.work_id)
                deduped.append(p)
            if deduped:
                goal_to_papers[goal] = deduped
                all_papers.extend(deduped)
    except Exception as exc:
        print(f"PubMed pipeline errored: {exc}")

    # OpenAlex journal top-cited (quality seed)
    try:
        oa_papers = fetch_top_papers(config, journals, per_page=20)
        for p in oa_papers[:10]:
            if p.work_id in seen_ids:
                continue
            seen_ids.add(p.work_id)
            all_papers.append(p)
    except Exception as exc:
        print(f"OpenAlex fetch failed: {exc}")

    recommendations = _build_recommendations(day, goal_to_papers)

    # Persist
    if config.database_url:
        from ..sync.storage import (
            delete_research_papers_except,
            save_research_papers_db,
            save_research_recommendations_db,
        )

        save_research_papers_db(
            config, day.isoformat(), [asdict(p) for p in all_papers]
        )
        delete_research_papers_except(config, day.isoformat())
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
    else:
        _save_local(config.data_dir, day, all_papers, recommendations)

    return recommendations


def load_research_for_day(
    config: SyncConfig, day: date
) -> List[Dict[str, Any]]:
    """Load research recommendations for a given day, local-JSON or Postgres.

    Returns a list of dicts (serialized recommendations). Empty list if none.
    """
    if config.database_url:
        try:
            import psycopg

            with psycopg.connect(config.database_url) as conn:
                rows = conn.execute(
                    "select date, goal, action, expected_impact_pct, evidence, "
                    "paper_title, journal, cited_by_count, url "
                    "from research_recommendations where date = %s;",
                    (day.isoformat(),),
                ).fetchall()
                cols = [
                    "date",
                    "goal",
                    "action",
                    "expected_impact_pct",
                    "evidence",
                    "paper_title",
                    "journal",
                    "cited_by_count",
                    "url",
                ]
                return [dict(zip(cols, r)) for r in rows]
        except Exception as exc:
            print(f"load_research_for_day (pg) failed: {exc}")
            return []

    path = _research_dir(config.data_dir) / f"recommendations_{day.isoformat()}.json"
    if not path.exists():
        latest = _research_dir(config.data_dir) / "latest.json"
        if latest.exists():
            data = json.loads(latest.read_text())
            # Only return latest if within 3 days (else stale)
            from datetime import date as _date, timedelta

            try:
                latest_date = _date.fromisoformat(data.get("date", ""))
                if (day - latest_date).days <= 3:
                    return data.get("recommendations", [])
            except Exception:
                pass
        return []
    try:
        return json.loads(path.read_text())
    except Exception:
        return []
