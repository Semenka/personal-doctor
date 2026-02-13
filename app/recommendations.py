from __future__ import annotations

from typing import Dict, List

from .models import DailyData, DailyReport, Goals, Recommendation


def _signal_sleep(data: DailyData) -> str:
    if data.sleep_hours >= 7.5 and data.sleep_quality >= 7:
        return "solid"
    if data.sleep_hours < 6 or data.sleep_quality <= 5:
        return "poor"
    return "ok"


def _signal_recovery(data: DailyData) -> str:
    if data.hrv >= 65 and data.resting_hr <= 62:
        return "ready"
    if data.hrv < 45 or data.resting_hr >= 72:
        return "strained"
    return "moderate"


def _signal_hydration(data: DailyData) -> str:
    if data.water_l >= 2.5:
        return "good"
    if data.water_l < 1.6:
        return "low"
    return "ok"


def _signal_mobility(data: DailyData) -> str:
    if data.sitting_hours <= 6 and data.active_minutes >= 45:
        return "balanced"
    if data.sitting_hours >= 9:
        return "sedentary"
    return "needs_breaks"


def build_daily_report(data: DailyData, goals: Goals) -> DailyReport:
    signals = {
        "sleep": _signal_sleep(data),
        "recovery": _signal_recovery(data),
        "hydration": _signal_hydration(data),
        "mobility": _signal_mobility(data),
    }

    summary = (
        f"Sleep {signals['sleep']}, recovery {signals['recovery']}, "
        f"hydration {signals['hydration']}, mobility {signals['mobility']}."
    )

    recommendations: List[Recommendation] = []

    recommendations.append(
        Recommendation(
            category="diet",
            title="Calorie + macro alignment",
            details=[
                f"Target protein 1.6 g/kg body weight; current {data.protein_g} g.",
                "Add 1-2 servings of high-fiber carbs if energy dips.",
                "Keep saturated fat < 10% calories to support cardiovascular health.",
            ],
        )
    )

    if signals["sleep"] == "poor":
        recommendations.append(
            Recommendation(
                category="sleep",
                title="Sleep recovery reset",
                details=[
                    "Wind down 60 minutes before bed; no bright screens.",
                    "Aim for 7.5-8.5 hours tonight; shift bedtime 30-45 min earlier.",
                    "Stop caffeine by 2pm to improve sleep latency.",
                ],
            )
        )

    if signals["hydration"] == "low":
        recommendations.append(
            Recommendation(
                category="hydration",
                title="Hydration boost",
                details=[
                    "Add 0.8-1.2 L water today with electrolytes if training.",
                    "Front-load 500 ml before noon.",
                ],
            )
        )

    if signals["mobility"] != "balanced":
        recommendations.append(
            Recommendation(
                category="posture",
                title="Posture & mobility breaks",
                details=[
                    "Stand + stretch for 3-5 minutes every 45-60 min.",
                    "Add 8-12 min thoracic + hip mobility in the afternoon.",
                ],
            )
        )

    if goals.energy:
        recommendations.append(
            Recommendation(
                category="energy",
                title="Energy focus",
                details=[
                    "Prioritize slow-digesting carbs at breakfast (oats, berries).",
                    "Take a 10-15 min daylight walk after lunch.",
                ],
            )
        )

    if goals.cognition:
        recommendations.append(
            Recommendation(
                category="cognition",
                title="Cognitive performance",
                details=[
                    "Schedule 1-2 deep work blocks during peak alertness.",
                    "Add 1 serving omega-3 rich food (salmon, chia, walnuts).",
                ],
            )
        )

    if goals.sport_performance:
        recommendations.append(
            Recommendation(
                category="training",
                title="Training optimization",
                details=[
                    "If recovery is moderate/strained, keep intensity at RPE 6-7.",
                    "Add 8-10 min activation warm-up (glutes, hamstrings, shoulders).",
                ],
            )
        )

    if goals.reproductive_health:
        recommendations.append(
            Recommendation(
                category="reproductive",
                title="Reproductive health",
                details=[
                    "Aim for 7.5+ hours sleep; poor sleep reduces motility.",
                    "Add zinc/selenium rich foods (pumpkin seeds, eggs, seafood).",
                    "Limit heat exposure (saunas, hot baths) today.",
                ],
            )
        )

    return DailyReport(data=data, goals=goals, summary=summary, recommendations=recommendations, signals=signals)
