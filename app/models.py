from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from .research.models import ResearchRecommendation


@dataclass
class DailyData:
    date: str
    sleep_hours: float
    sleep_quality: int
    steps: int
    active_minutes: int
    resting_hr: int
    hrv: float
    calories: int
    protein_g: float
    carbs_g: float
    fat_g: float
    water_l: float
    sitting_hours: float
    mood: int
    stress: int


@dataclass
class Goals:
    energy: bool = False
    reproductive_health: bool = False
    cognition: bool = False
    sport_performance: bool = False

    @classmethod
    def from_list(cls, goals: List[str]) -> "Goals":
        normalized = {goal.strip().lower() for goal in goals}
        return cls(
            energy="energy" in normalized,
            reproductive_health="reproductive" in normalized
            or "reproductive_health" in normalized
            or "semen" in normalized,
            cognition="cognition" in normalized
            or "cognitive" in normalized
            or "focus" in normalized,
            sport_performance="sport" in normalized
            or "sport_performance" in normalized
            or "performance" in normalized,
        )


@dataclass
class Recommendation:
    category: str
    title: str
    details: List[str] = field(default_factory=list)


@dataclass
class DailyReport:
    data: DailyData
    goals: Goals
    summary: str
    recommendations: List[Recommendation]
    signals: Dict[str, str]
    research_recommendations: List[ResearchRecommendation] = field(default_factory=list)
