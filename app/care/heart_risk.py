"""Heart-risk status: LDL, ApoB and Lp(a) — what is known, what is missing, what to ask.

Added 2026-11-08 from the a16z "State of Longevity" report (Wolf & Steinman,
2026-11-03). Its actionable cholesterol points, checked against the primary
evidence the pipeline already cites:

- LDL "lower is better", especially at higher risk (Ference 2017 EAS; CTT
  2019); intensive lowering regresses coronary plaque (Nicholls 2016 GLAGOV).
- Lp(a) is inherited, ~1 in 5 people have it high, diet and exercise barely
  move it — measure it once in adulthood (Kronenberg 2022 EAS) and use it to
  decide how hard to push LDL.
- The question to bring to a doctor: given family history and risk, how low
  should my LDL be, and would medication (statin, PCSK9 inhibitor) get me
  there?

Why a separate block: ``summarize_for_advisor`` only ranks markers with two
or more readings, so a single LDL of 169 mg/dL (2026-04-27) never reached the
daily plan, and Lp(a)/ApoB — never measured — were invisible as gaps. The
genetic profile (ITGB3 PlA2, 9p21) is the "higher risk" that makes this
worth acting on. Nothing here prescribes: the pipeline names the test and the
question; the decision belongs to a clinician.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from ..sync.config import SyncConfig

# ESC/EAS 2019-2020: above this LDL a clinician conversation is warranted for
# anyone with added genetic risk; the general reference ceiling is 130.
LDL_DISCUSS_MG_DL = 130.0
LPA_HIGH_NMOL_L = 125.0  # Kronenberg 2022 EAS: >125 nmol/L (≈50 mg/dL) is high
APOB_HIGH_MG_DL = 100.0

DOCTOR_QUESTION_FR = (
    "Vu mes antécédents familiaux et mon profil génétique (ITGB3, 9p21), quel "
    "objectif de LDL dois-je viser, et un traitement (statine, inhibiteur de "
    "PCSK9) m'aiderait-il à l'atteindre ?"
)
DOCTOR_QUESTION_EN = (
    "Given my family history and genetic risk, how low should my LDL be — and "
    "would medication (statin, PCSK9 inhibitor) help me get there?"
)
_CARDIO_SEARCH = "https://www.doctolib.fr/cardiologue/paris-75016"


def _latest(config: SyncConfig, marker_id: str) -> Optional[Dict[str, Any]]:
    from ..sync.biomarker_trends import series

    pts = series(config, marker_id)
    if not pts:
        return None
    p = pts[-1]
    return {"value": p.value, "unit": p.unit, "date": p.date}


def heart_risk_status(config: SyncConfig, today: Optional[date] = None) -> Dict[str, Any]:
    """Latest LDL / ApoB / Lp(a), which are missing, and whether to see a doctor."""
    today = today or date.today()
    ldl, apob, lpa = (_latest(config, k) for k in ("ldl", "apob", "lp_a"))
    missing = [name for name, v in (("Lp(a)", lpa), ("ApoB", apob)) if v is None]
    reasons: List[str] = []
    if ldl and ldl["value"] >= LDL_DISCUSS_MG_DL:
        reasons.append(f"LDL {ldl['value']:g} mg/dL is above {LDL_DISCUSS_MG_DL:g}")
    if apob and apob["value"] >= APOB_HIGH_MG_DL:
        reasons.append(f"ApoB {apob['value']:g} mg/dL is above {APOB_HIGH_MG_DL:g}")
    if lpa and lpa["value"] >= LPA_HIGH_NMOL_L:
        reasons.append(f"Lp(a) {lpa['value']:g} nmol/L is high (inherited)")
    ldl_age_days = None
    if ldl:
        try:
            ldl_age_days = (today - date.fromisoformat(ldl["date"][:10])).days
        except ValueError:
            pass
    return {
        "ldl": ldl, "apob": apob, "lp_a": lpa, "missing": missing,
        "ldl_age_days": ldl_age_days,
        "discuss_with_doctor": bool(reasons), "reasons": reasons,
    }


def render_heart_risk(config: SyncConfig, today: Optional[date] = None) -> str:
    """Markdown block for the weekly brief; empty when there is nothing to act on."""
    s = heart_risk_status(config, today)
    if not (s["missing"] or s["discuss_with_doctor"]):
        return ""
    lines = ["## ❤️ Heart risk (LDL · ApoB · Lp(a))"]
    for key, label in (("ldl", "LDL"), ("apob", "ApoB"), ("lp_a", "Lp(a)")):
        v = s[key]
        lines.append(f"- {label}: {v['value']:g} {v['unit']} ({v['date']})" if v
                     else f"- {label}: **never measured**")
    if "Lp(a)" in s["missing"]:
        lines.append(
            "- Lp(a) is inherited and barely moved by diet or exercise; one test "
            "in adulthood tells you how hard to push LDL (Kronenberg 2022 EAS). "
            "It is on the lab slip — *Lipoprotéine(a), une fois*."
        )
    if s["discuss_with_doctor"]:
        lines.append(
            f"- **Worth a doctor's view:** {'; '.join(s['reasons'])}. For higher-risk "
            "profiles LDL is \"lower is better\" (Ference 2017 EAS), and intensive "
            "lowering can regress plaque (Nicholls 2016 GLAGOV)."
        )
        lines.append(f"  ask: *{DOCTOR_QUESTION_FR}*")
        lines.append(f"  ({DOCTOR_QUESTION_EN})  ·  GP or {_CARDIO_SEARCH}")
    if s["ldl_age_days"] is not None and s["ldl_age_days"] > 180:
        lines.append(f"- Last LDL is {s['ldl_age_days']} days old — re-measure with the panel above.")
    return "\n".join(lines)


def advisor_context_line(config: SyncConfig, today: Optional[date] = None) -> str:
    """One line for the daily prompt: single readings never reach the trend list."""
    s = heart_risk_status(config, today)
    if not (s["missing"] or s["discuss_with_doctor"]):
        return ""
    known = ", ".join(
        f"{label} {s[k]['value']:g} {s[k]['unit']} ({s[k]['date']})"
        for k, label in (("ldl", "LDL"), ("apob", "ApoB"), ("lp_a", "Lp(a)")) if s[k]
    ) or "no lipid results"
    parts = [f"Heart-risk status: {known}."]
    if s["missing"]:
        parts.append(f"Never measured: {', '.join(s['missing'])}.")
    if s["discuss_with_doctor"]:
        parts.append(f"Doctor conversation warranted ({'; '.join(s['reasons'])}).")
    return " ".join(parts)
