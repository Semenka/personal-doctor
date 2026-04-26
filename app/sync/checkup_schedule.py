"""Genetics-driven check-up schedule.

Derived from the user's actual VCF findings (see genetic_analysis.py). Each
entry has: name, cadence (months), rationale tied to specific SNPs, and a
short description of what the test measures. The scheduler reads this list
and:
  1. Creates recurring Google Calendar events (one per test).
  2. Computes "next-up" tests within the coming 30 days so the advisor can
     remind the user each morning.

Cadence follows the principle: run a test often enough that a change matters
but not so often that the data gets noisy or costly.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import SyncConfig


@dataclass
class CheckUp:
    """One recurring medical test."""
    key: str
    name: str
    category: str
    cadence_months: int
    rationale: str
    markers: List[str] = field(default_factory=list)
    prep: str = ""  # e.g. "fasting 8h"
    next_due: Optional[str] = None  # ISO date, filled by scheduler

    # Lab routing — added for Paris-based monitoring (see vast-greeting-flamingo.md)
    lab_provider: Optional[str] = None  # e.g. "Laboratoire Clément Eylau-Unilabs"
    lab_panel_name: Optional[str] = None  # exact French panel name on the slip
    bundle: Optional[str] = None  # "q_fertility" | "semi_annual_broad" | "annual_deep_dive" | "specialist"


# ── Primary lab (Paris 16) and bundle defaults ──
PRIMARY_LAB = "Laboratoire Clément Eylau-Unilabs"
PRIMARY_LAB_ADDRESS = "17 avenue d'Eylau, 75016 Paris (métro Trocadéro)"
SPECIALIST = "specialist"  # placeholder for non-lab clinical visits

BUNDLE_LABELS = {
    "q_fertility": "Quarterly fertility visit (no fasting)",
    "semi_annual_broad": "Semi-annual fasting visit",
    "annual_deep_dive": "Annual deep-dive fasting visit",
    "specialist": "Specialist clinic (not a lab)",
}


# ── Schedule derived from the user's specific genetic findings ──
# Rationale references each relevant SNP from data/ingested/genetics/genetic_summary.json.
SCHEDULE: List[CheckUp] = [
    # FERTILITY — user's primary goal + FSHR het
    CheckUp(
        key="sperm_analysis",
        name="Sperm analysis (motility + morphology + count)",
        category="fertility",
        cadence_months=3,
        rationale="Primary conception goal; AMPD1 carrier + FSHR Asn/Ser intermediate "
                  "makes repeat measurement important to track protocol effects.",
        markers=["motility", "morphology", "concentration", "DNA fragmentation"],
        prep="2-4 day abstinence window",
        lab_provider=PRIMARY_LAB,
        lab_panel_name="Spermogramme + spermocytogramme (+ Fragmentation ADN spermatique tous les 6 mois)",
        bundle="q_fertility",
    ),
    CheckUp(
        key="hormone_panel",
        name="Hormone panel (testosterone, SHBG, LH, FSH, estradiol)",
        category="fertility",
        cadence_months=6,
        rationale="FSHR Asn/Ser makes serum FSH especially informative; testosterone "
                  "baseline supports fertility + energy goals.",
        markers=["total testosterone", "free testosterone", "SHBG", "LH", "FSH", "estradiol", "prolactin"],
        prep="Morning draw (8-10 AM), fasting preferred",
        lab_provider=PRIMARY_LAB,
        lab_panel_name="Bilan hormonal masculin (Testostérone tot/libre, SHBG, LH, FSH, Œstradiol, Prolactine)",
        bundle="semi_annual_broad",
    ),

    # CARDIOVASCULAR — ITGB3 PlA2 het + CDKAL1 het
    CheckUp(
        key="lipid_advanced",
        name="Advanced lipid panel (ApoB, LDL-P, Lp(a), Tg:HDL)",
        category="cardiovascular",
        cadence_months=6,
        rationale="ITGB3 PlA2 het (~1.5x MI risk) + 9p21 baseline CAD risk "
                  "warrants ApoB/Lp(a) over basic lipids; Lp(a) is checked once in life "
                  "then only if abnormal.",
        markers=["ApoB", "LDL-P", "Lp(a) once", "HDL", "TG:HDL ratio", "non-HDL-C"],
        prep="Fasting 10-12 h",
        lab_provider=PRIMARY_LAB,
        lab_panel_name="Bilan lipidique étendu (ApoB, Lp(a) une fois, ratio TG:HDL)",
        bundle="semi_annual_broad",
    ),
    CheckUp(
        key="bp_home",
        name="Blood pressure (home cuff, 7-day morning average)",
        category="cardiovascular",
        cadence_months=1,
        rationale="ITGB3 + 9p21 cumulative CAD genetic loading. Home-measured BP is "
                  "more predictive than clinic; 7-day rolling average each month.",
        markers=["systolic", "diastolic", "pulse pressure"],
        prep="Sitting 5 min, same time of day",
        lab_provider=None,  # home-measured, no lab visit
        lab_panel_name=None,
        bundle=None,
    ),
    CheckUp(
        key="ecg_baseline",
        name="ECG (12-lead, resting)",
        category="cardiovascular",
        cadence_months=36,
        rationale="ITGB3 PlA2 het elevates MI risk; baseline ECG now, re-check every 3 y.",
        markers=["rhythm", "QTc", "ST changes", "LVH pattern"],
        lab_provider=SPECIALIST,
        lab_panel_name="ECG 12 dérivations chez cardiologue",
        bundle="specialist",
    ),

    # METABOLIC — CDKAL1 het + PPARG het + MTHFR normal
    CheckUp(
        key="glycemic",
        name="Glycemic panel (HbA1c, fasting glucose, fasting insulin, HOMA-IR)",
        category="metabolic",
        cadence_months=6,
        rationale="CDKAL1 het (~1.1x T2D risk) offset by PPARG Ala carrier; "
                  "monitor HOMA-IR to catch insulin resistance early.",
        markers=["HbA1c", "fasting glucose", "fasting insulin", "HOMA-IR"],
        prep="Fasting 10-12 h",
        lab_provider=PRIMARY_LAB,
        lab_panel_name="Bilan glucidique (Glycémie à jeun, HbA1c, Insulinémie → HOMA-IR)",
        bundle="semi_annual_broad",
    ),
    CheckUp(
        key="homocysteine",
        name="Homocysteine + folate + B12",
        category="metabolic",
        cadence_months=12,
        rationale="MTHFR normal per VCF, but annual baseline protects against "
                  "B-vitamin depletion and confirms methylation pathway is working.",
        markers=["homocysteine", "serum folate", "serum B12", "MMA (if B12 borderline)"],
        lab_provider=PRIMARY_LAB,
        lab_panel_name="Homocystéine, Folates sériques, Vitamine B12 (+ Acide méthylmalonique si B12 < 300 pg/mL)",
        bundle="annual_deep_dive",
    ),

    # NUTRIENTS — CYP2R1 + GC intermediate (low vitamin D)
    CheckUp(
        key="vit_d",
        name="Serum 25-hydroxy-vitamin D",
        category="nutrients",
        cadence_months=3,
        rationale="CYP2R1 + GC both intermediate → lower baseline 25(OH)D. "
                  "Target 40-60 ng/mL for fertility + immune + mood. Quarterly during "
                  "titration, stretch to 6 months once stable.",
        markers=["25(OH)D"],
        lab_provider=PRIMARY_LAB,
        lab_panel_name="25-OH Vitamine D (calcidiol)",
        bundle="q_fertility",  # piggyback on quarterly fertility visit (no fast required)
    ),
    CheckUp(
        key="iron",
        name="Iron panel (ferritin, transferrin sat, TIBC)",
        category="nutrients",
        cadence_months=12,
        rationale="HFE C282Y and H63D both reference in VCF — no hemochromatosis risk, "
                  "but annual ferritin protects against silent iron overload from red "
                  "meat + rules out anemia affecting fatigue.",
        markers=["ferritin", "transferrin saturation", "TIBC", "serum iron"],
        lab_provider=PRIMARY_LAB,
        lab_panel_name="Bilan martial (Ferritine, Sidérémie, Transferrine, CST, TIBC)",
        bundle="annual_deep_dive",
    ),
    CheckUp(
        key="omega3_index",
        name="Omega-3 index + omega-6:3 ratio (RBC membrane)",
        category="nutrients",
        cadence_months=6,
        rationale="Sperm membrane integrity is omega-3 dependent (Safarinejad 2011); "
                  "target index >8%, ratio <4:1.",
        markers=["EPA%", "DHA%", "omega-3 index", "omega-6:3 ratio"],
        lab_provider=PRIMARY_LAB,
        lab_panel_name="Indice Oméga-3 membranaire érythrocytaire + ratio oméga-6/oméga-3",
        bundle="semi_annual_broad",
    ),
    CheckUp(
        key="micronutrients",
        name="Micronutrient panel (zinc, selenium, magnesium RBC, B-vitamins)",
        category="nutrients",
        cadence_months=12,
        rationale="Zn + Se are cornerstone fertility nutrients (Colagar 2009); RBC "
                  "magnesium is better than serum for status.",
        markers=["zinc", "selenium", "magnesium RBC", "B-vitamins panel"],
        lab_provider=PRIMARY_LAB,
        lab_panel_name="Bilan oligo-éléments + vitamines (Zinc, Sélénium, Magnésium érythrocytaire, B6 PLP, A, E)",
        bundle="annual_deep_dive",
    ),

    # INFLAMMATION + IMMUNE
    CheckUp(
        key="hs_crp",
        name="hs-CRP (high-sensitivity C-reactive protein)",
        category="inflammation",
        cadence_months=6,
        rationale="IL6 het is protective, but CRP is cheap and catches silent "
                  "inflammation that affects both sperm and cardiovascular risk.",
        markers=["hs-CRP"],
        lab_provider=PRIMARY_LAB,
        lab_panel_name="CRP ultra-sensible (CRPus / hs-CRP)",
        bundle="semi_annual_broad",
    ),

    # PROSTATE — MSMB het
    CheckUp(
        key="psa",
        name="PSA (prostate-specific antigen)",
        category="prostate",
        cadence_months=12,
        rationale="MSMB rs10993994 het (~1.25x prostate cancer risk) — annual PSA from "
                  "age 35 onwards is prudent given the elevated baseline.",
        markers=["total PSA", "free PSA ratio"],
        lab_provider=PRIMARY_LAB,
        lab_panel_name="PSA total + PSA libre (rapport free/total)",
        bundle="annual_deep_dive",
    ),

    # GENERAL PANELS
    CheckUp(
        key="cbc_cmp",
        name="CBC + Comprehensive Metabolic Panel",
        category="general",
        cadence_months=12,
        rationale="Annual baseline for kidney/liver function and blood counts.",
        markers=["WBC", "RBC", "Hgb", "platelets", "creatinine", "eGFR", "ALT", "AST", "electrolytes"],
        prep="Fasting 10-12 h",
        lab_provider=PRIMARY_LAB,
        lab_panel_name="NFS + plaquettes + Bilan métabolique complet (Créatinine/DFGe, Urée, électrolytes, ALAT/ASAT/GGT/PAL/LDH)",
        bundle="annual_deep_dive",
    ),
    CheckUp(
        key="thyroid",
        name="Thyroid panel (TSH, free T4, free T3, TPOAb)",
        category="general",
        cadence_months=12,
        rationale="Thyroid dysfunction is a common hidden cause of fatigue + fertility "
                  "issues; annual is standard even without genetic risk.",
        markers=["TSH", "free T4", "free T3", "TPO antibodies"],
        lab_provider=PRIMARY_LAB,
        lab_panel_name="Bilan thyroïdien (TSH, FT3, FT4, anti-TPO)",
        bundle="annual_deep_dive",
    ),

    # LIFESTYLE / SCREENING
    CheckUp(
        key="dental",
        name="Dental cleaning + periodontitis check",
        category="dental",
        cadence_months=6,
        rationale="2019 genetic report flagged 24% periodontitis risk (vs 23% population). "
                  "Periodontal inflammation is linked to sperm motility decline.",
        markers=[],
        lab_provider=SPECIALIST,
        lab_panel_name="Détartrage + bilan parodontal chez dentiste",
        bundle="specialist",
    ),
    CheckUp(
        key="eye",
        name="Eye exam (acuity + retinal check)",
        category="vision",
        cadence_months=24,
        rationale="2019 report flagged 47% myopia baseline. Routine retinal check also "
                  "catches early hypertensive / diabetic changes.",
        markers=[],
        lab_provider=SPECIALIST,
        lab_panel_name="Examen ophtalmologique (acuité + fond d'œil) chez ophtalmo",
        bundle="specialist",
    ),
    CheckUp(
        key="skin",
        name="Dermatology full-body skin check",
        category="dermatology",
        cadence_months=12,
        rationale="Annual full-body mole check, regardless of genetic profile.",
        markers=[],
        lab_provider=SPECIALIST,
        lab_panel_name="Examen dermatologique corps entier chez dermato",
        bundle="specialist",
    ),

    # BODY COMPOSITION + SLEEP
    CheckUp(
        key="body_comp",
        name="Body composition (DEXA or accurate bioimpedance)",
        category="body",
        cadence_months=6,
        rationale="FTO rs9939609 reference (low obesity risk) but body-fat distribution "
                  "still tracks fertility (abdominal fat → lower testosterone). DEXA every "
                  "6 months shows muscle-vs-fat trajectory.",
        markers=["total fat %", "visceral fat", "lean mass", "bone density"],
        lab_provider=SPECIALIST,
        lab_panel_name="DEXA composition corporelle en clinique radiologique",
        bundle="specialist",
    ),
    CheckUp(
        key="sleep_study",
        name="Home sleep study (polysomnography)",
        category="sleep",
        cadence_months=36,
        rationale="CLOCK rs1801260 evening-chronotype homozygous — rule out sleep apnea "
                  "once every 3 y; poor sleep architecture is a major fertility hit.",
        markers=["AHI", "REM %", "SpO2 nadir", "sleep efficiency"],
        lab_provider=SPECIALIST,
        lab_panel_name="Polysomnographie ambulatoire chez pneumologue / centre du sommeil",
        bundle="specialist",
    ),
]


def _schedule_dir(data_dir: Path) -> Path:
    d = data_dir / "checkups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def compute_next_due_dates(
    schedule: List[CheckUp] = SCHEDULE, anchor: date | None = None
) -> List[CheckUp]:
    """Fill in next_due for each checkup.

    Distributes first-run dates so not everything lands on day-one — spreads
    across the first cadence interval. Each test's next due then rolls forward
    by its cadence.
    """
    if anchor is None:
        anchor = date.today()
    out: List[CheckUp] = []
    # Spread initial dates across a 60-day rollout so not everything at once
    rollout_days = 60
    total = len(schedule)
    for i, item in enumerate(schedule):
        initial_offset = int((i / max(total - 1, 1)) * rollout_days)
        next_date = anchor + timedelta(days=initial_offset)
        new = CheckUp(
            key=item.key, name=item.name, category=item.category,
            cadence_months=item.cadence_months, rationale=item.rationale,
            markers=item.markers, prep=item.prep,
            next_due=next_date.isoformat(),
            lab_provider=item.lab_provider,
            lab_panel_name=item.lab_panel_name,
            bundle=item.bundle,
        )
        out.append(new)
    return out


def upcoming_lab_visits(
    config: SyncConfig, within_days: int = 30, today: date | None = None
) -> List[Dict[str, Any]]:
    """Return upcoming lab-bundled visits as a list, sorted by date.

    Each entry coalesces all check-ups belonging to one bundle into a single
    batched booking with the earliest date + union of French panel names.
    ``specialist`` bundles are excluded — those are individual clinic visits.
    """
    if today is None:
        today = date.today()
    items = load_schedule(config)
    horizon = today + timedelta(days=within_days)

    by_bundle: Dict[str, Dict[str, Any]] = {}
    for item in items:
        bundle = item.get("bundle")
        if not bundle or bundle == "specialist":
            continue
        next_due = item.get("next_due")
        if not next_due:
            continue
        try:
            due = date.fromisoformat(next_due)
        except Exception:
            continue
        if due > horizon or due < today:
            continue
        slot = by_bundle.setdefault(
            bundle,
            {
                "bundle": bundle,
                "label": BUNDLE_LABELS.get(bundle, bundle),
                "date": due.isoformat(),
                "lab_provider": item.get("lab_provider") or PRIMARY_LAB,
                "lab_address": PRIMARY_LAB_ADDRESS,
                "panels": [],
                "days_away": (due - today).days,
            },
        )
        if due.isoformat() < slot["date"]:
            slot["date"] = due.isoformat()
            slot["days_away"] = (due - today).days
        if item.get("lab_panel_name"):
            slot["panels"].append(item["lab_panel_name"])

    visits = list(by_bundle.values())
    # De-dupe panels per visit while preserving order
    for v in visits:
        seen = set()
        v["panels"] = [p for p in v["panels"] if not (p in seen or seen.add(p))]
    visits.sort(key=lambda b: b["date"])
    return visits


def next_lab_visit(
    config: SyncConfig, within_days: int = 21, today: date | None = None
) -> Optional[Dict[str, Any]]:
    """Convenience wrapper: return only the closest bundled lab visit, or None."""
    visits = upcoming_lab_visits(config, within_days=within_days, today=today)
    return visits[0] if visits else None


def save_schedule(config: SyncConfig, schedule: List[CheckUp]) -> Path:
    base = _schedule_dir(config.data_dir)
    out = base / "schedule.json"
    out.write_text(
        json.dumps([asdict(s) for s in schedule], indent=2, ensure_ascii=False)
    )
    return out


def load_schedule(config: SyncConfig) -> List[Dict[str, Any]]:
    p = _schedule_dir(config.data_dir) / "schedule.json"
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text())
    except Exception:
        return []


def upcoming_checkups(
    config: SyncConfig, within_days: int = 14, today: date | None = None
) -> List[Dict[str, Any]]:
    """Return checkups whose next_due falls within ``within_days``.

    Called by the daily advisor to surface a reminder in the morning email /
    WhatsApp without needing to poke the calendar API.
    """
    if today is None:
        today = date.today()
    items = load_schedule(config)
    horizon = today + timedelta(days=within_days)
    out = []
    for item in items:
        next_due = item.get("next_due")
        if not next_due:
            continue
        try:
            due = date.fromisoformat(next_due)
        except Exception:
            continue
        if today <= due <= horizon:
            out.append({**item, "days_away": (due - today).days})
    out.sort(key=lambda x: x["days_away"])
    return out


def render_schedule_markdown(schedule: List[Dict[str, Any]]) -> str:
    """Human-readable check-up schedule (email / WhatsApp / dashboard)."""
    if not schedule:
        return "_No check-up schedule on file. Run `init_checkup_schedule()`._\n"
    lines = ["# Genetics-driven check-up schedule", ""]
    by_cat: Dict[str, List[Dict[str, Any]]] = {}
    for item in schedule:
        by_cat.setdefault(item.get("category", "other"), []).append(item)
    cat_labels = {
        "fertility": "🧬 Fertility",
        "cardiovascular": "❤️ Cardiovascular",
        "metabolic": "🔥 Metabolic",
        "nutrients": "☀️ Nutrients",
        "inflammation": "🔥 Inflammation",
        "prostate": "🎯 Prostate",
        "general": "🩺 General",
        "dental": "🦷 Dental",
        "vision": "👁️ Vision",
        "dermatology": "🩹 Skin",
        "body": "💪 Body composition",
        "sleep": "😴 Sleep",
    }
    for cat, label in cat_labels.items():
        items = by_cat.get(cat, [])
        if not items:
            continue
        lines.append(f"## {label}")
        for item in items:
            cadence = item.get("cadence_months", 12)
            if cadence == 1:
                rhythm = "monthly"
            elif cadence < 12:
                rhythm = f"every {cadence} months"
            elif cadence == 12:
                rhythm = "annually"
            else:
                rhythm = f"every {cadence // 12} years"
            lines.append(f"### {item.get('name')} — _{rhythm}_")
            lines.append(f"*Why:* {item.get('rationale', '')}")
            if item.get("markers"):
                lines.append(f"*Markers:* {', '.join(item['markers'])}")
            if item.get("prep"):
                lines.append(f"*Prep:* {item['prep']}")
            if item.get("next_due"):
                lines.append(f"*Next due:* {item['next_due']}")
            lines.append("")
    return "\n".join(lines)
