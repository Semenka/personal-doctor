from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class ResearchPaper:
    work_id: str
    title: str
    journal: str
    cited_by_count: int
    publication_date: str | None
    url: str | None


@dataclass(frozen=True)
class ResearchRecommendation:
    date: date
    goal: str
    action: str
    expected_impact_pct: float
    evidence: str
    paper_title: str
    journal: str
    cited_by_count: int
    url: str | None
