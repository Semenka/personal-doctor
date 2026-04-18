"""Topic-driven search queries for the research pipeline.

Each GOAL maps to a list of (query, since_months) tuples that we cycle
through day-to-day so the advisor sees fresh angles. Keep queries tight:
OpenAlex/PubMed reward specific terms.
"""
from __future__ import annotations

from datetime import date
from typing import List, Tuple

# Query shape: (free-text search, PubMed MeSH boost (or empty), since_months)
GOAL_QUERIES: dict[str, List[Tuple[str, str, int]]] = {
    "sperm_motility": [
        ("sperm motility coenzyme Q10 OR ubiquinol randomized", "sperm motility[MeSH]", 24),
        ("sperm motility zinc selenium supplementation", "sperm motility[MeSH]", 24),
        ("sperm motility omega-3 DHA supplementation", "sperm motility[MeSH]", 24),
        ("sperm DNA fragmentation methylfolate randomized", "", 24),
        ("scrotal temperature sperm quality", "scrotal temperature[MeSH]", 36),
    ],
    "testosterone": [
        ("testosterone sleep deprivation men", "testosterone[MeSH]", 24),
        ("vitamin D testosterone serum randomized", "vitamin D[MeSH]", 24),
        ("resistance training testosterone men", "", 24),
    ],
    "hrv_recovery": [
        ("heart rate variability breathing slow paced randomized", "", 18),
        ("HRV cold water immersion recovery", "", 18),
        ("HRV magnesium supplementation sleep", "", 24),
    ],
    "energy_cognition": [
        ("creatine cognitive performance sleep deprivation", "creatine[MeSH]", 24),
        ("morning bright light alertness cortisol", "", 18),
        ("caffeine timing performance meta-analysis", "", 24),
    ],
    "sleep": [
        ("sleep quality magnesium glycinate randomized", "", 24),
        ("blue light exposure melatonin suppression", "", 18),
        ("sleep hygiene intervention meta-analysis adults", "", 24),
    ],
    "fertility_conception": [
        ("male fertility lifestyle intervention pregnancy rate", "male infertility[MeSH]", 24),
        ("antioxidant supplementation male infertility Cochrane", "", 24),
        ("varicocele surgery sperm parameters outcomes", "varicocele[MeSH]", 36),
    ],
}

# Ordered list of goals. We pick N per day by rotating this list.
GOAL_ORDER = list(GOAL_QUERIES.keys())


def queries_for_day(day: date, per_day: int = 3) -> List[Tuple[str, str, str, int]]:
    """Pick a rotating subset of (goal, query, mesh, since_months) for ``day``.

    The rotation is deterministic by date so the same day two runs produces the
    same queries, but consecutive days cover different ground.
    Also picks a different sub-query within each goal so queries refresh weekly.
    """
    day_ord = day.toordinal()
    n_goals = len(GOAL_ORDER)
    selected: List[Tuple[str, str, str, int]] = []
    for i in range(per_day):
        goal = GOAL_ORDER[(day_ord + i) % n_goals]
        subqs = GOAL_QUERIES[goal]
        sub = subqs[day_ord % len(subqs)]
        selected.append((goal, sub[0], sub[1], sub[2]))
    return selected
