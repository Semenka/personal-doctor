"""Evidence-based corrective actions per (biomarker, flagged_state).

When a marker reads ``low`` or ``high`` against its reference range, the
dashboard cards, the WhatsApp summary, and the daily advisor LLM all need a
short menu of actionable protocols backed by named, highly-cited papers.

Each entry is intentionally short (2–3 bullets max) and cites the strongest
paper in the existing project nomenclature (Author Year, matching the
``citations`` field on ``biomarkers.py`` entries). Heavy bias toward:
 - Cochrane reviews (Showell 2014 — antioxidants for male subfertility)
 - WHO 2021 6th edition (semen reference manual)
 - ESC 2020 dyslipidemia guidelines (Mach et al.)
 - ADA 2024 standards of care (glycemic)
 - Endocrine Society (Holick 2011 for vit D)

Coverage is deliberately curated rather than exhaustive — only markers
the user actually has on file (sperm, hormones, lipids, glycemic, hema,
vitamins, inflammation, thyroid, prostate, micronutrients).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class Intervention:
    """A single evidence-cited corrective action."""

    action: str            # imperative one-liner, e.g. "CoQ10 200 mg/day with breakfast"
    mechanism: str         # one-sentence why
    expected_effect: str   # observable change + timeline
    citation: str          # "Author Year" — matches biomarkers.py citation format
    category: str          # supplement | movement | sleep | nutrition | stress | medical


# ──────────────────────────────────────────────────────────────────────────
# Library: marker_id → {"low": [..], "high": [..]}
# ──────────────────────────────────────────────────────────────────────────

# Shared sets that appear in multiple markers (semen quality protocols are
# largely overlapping — antioxidants help motility, count, morphology, DFI).
_ANTIOX_STACK = Intervention(
    action="Antioxidant stack — NAC 600 mg + Selenium 200 µg + Vit E 400 IU + Vit C 1 g daily",
    mechanism="Reduces seminal ROS that damage sperm membranes and DNA",
    expected_effect="~4× higher live-birth rate by 3-month sperm cycle",
    citation="Showell 2014 Cochrane",
    category="supplement",
)
_COQ10 = Intervention(
    action="CoQ10 / Ubiquinol 200 mg/day with a fat-containing meal",
    mechanism="Mitochondrial energy supply for the sperm midpiece + antioxidant",
    expected_effect="+26% progressive motility, +18% count over 12 weeks",
    citation="Safarinejad 2009",
    category="supplement",
)
_CARNITINE = Intervention(
    action="L-Carnitine 2 g + Acetyl-L-carnitine 1 g/day",
    mechanism="Fuels sperm beta-oxidation; improves total + progressive motility",
    expected_effect="Motility +20-30% in 3-6 months",
    citation="Lenzi 2003",
    category="supplement",
)
_ZN_SE = Intervention(
    action="Zinc bisglycinate 25-50 mg + Selenium 200 µg daily (take with food, not iron)",
    mechanism="Cofactors for spermatogenesis and antioxidant enzymes",
    expected_effect="Higher concentration + morphology over 3-6 months",
    citation="Colagar 2009",
    category="supplement",
)
_OMEGA3 = Intervention(
    action="EPA+DHA 1.5-2 g/day (omega-3 index target >8%)",
    mechanism="Builds sperm-membrane fluidity for motility and acrosome function",
    expected_effect="Improved motility + morphology over 8-12 weeks",
    citation="Safarinejad 2011",
    category="supplement",
)
_METHYL_FOLATE = Intervention(
    action="Methylfolate (5-MTHF) 400-800 µg daily — not folic acid",
    mechanism="Reduces sperm DNA-fragmentation by supplying methyl donors",
    expected_effect="Lower DFI over 3 months",
    citation="Wong 2002",
    category="supplement",
)
_SCROTAL_COOL = Intervention(
    action="Strict scrotal thermal hygiene — no laptops on lap, no hot tubs/saunas, loose underwear",
    mechanism="Sperm motility drops 20-40% within weeks of testicular heat exposure",
    expected_effect="Motility recovery in 2-3 months",
    citation="Mieusset & Bujan 1995",
    category="lifestyle",
)
_EXERCISE_MODERATE = Intervention(
    action="Moderate exercise 3-5×/week, 30-45 min — avoid >90-min intense sessions",
    mechanism="Improves sperm parameters; overtraining elevates cortisol and depresses them",
    expected_effect="Motility + morphology gains over 12 weeks",
    citation="Vaamonde 2012",
    category="movement",
)
_SLEEP_7 = Intervention(
    action="Sleep ≥7 h with lights-out before 23:30",
    mechanism="Short sleep cuts testosterone 10-15% per week; T drives spermatogenesis",
    expected_effect="Higher morning T + better sperm parameters",
    citation="Leproult & Van Cauter 2011",
    category="sleep",
)


INTERVENTIONS: Dict[str, Dict[str, List[Intervention]]] = {
    # ───────── SEMEN — low / suboptimal ─────────
    "sperm_progressive_motility": {
        "low": [_COQ10, _CARNITINE, _ANTIOX_STACK, _SCROTAL_COOL, _EXERCISE_MODERATE],
    },
    "sperm_total_motility": {
        "low": [_COQ10, _CARNITINE, _ANTIOX_STACK, _SCROTAL_COOL, _EXERCISE_MODERATE],
    },
    "sperm_concentration": {
        "low": [_ZN_SE, _METHYL_FOLATE, _OMEGA3, _SCROTAL_COOL, _ANTIOX_STACK],
    },
    "sperm_total_count": {
        "low": [_ZN_SE, _METHYL_FOLATE, _SLEEP_7, _SCROTAL_COOL, _OMEGA3],
    },
    "sperm_normal_morphology": {
        "low": [
            _ANTIOX_STACK,
            Intervention(
                action="Reduce BMI toward 22-25 if currently >27 (gradual deficit, not crash)",
                mechanism="Adiposity raises scrotal temperature and aromatizes T to E2",
                expected_effect="Morphology and count improve as BMI normalizes",
                citation="Sermondade 2013",
                category="nutrition",
            ),
            _SCROTAL_COOL,
            _OMEGA3,
        ],
    },
    "sperm_dna_fragmentation": {
        "high": [
            _ANTIOX_STACK,
            _METHYL_FOLATE,
            _SCROTAL_COOL,
            Intervention(
                action="Reduce alcohol to ≤3 units/week; quit smoking entirely",
                mechanism="Both raise sperm DNA fragmentation index independently",
                expected_effect="DFI down 15-25% in 3 months",
                citation="Boeri 2019",
                category="lifestyle",
            ),
            Intervention(
                action="Varicocele assessment via scrotal Doppler ultrasound",
                mechanism="Varicocele is the single most common reversible cause of high DFI",
                expected_effect="Repair (varicocelectomy) drops DFI ~10-15 percentage points",
                citation="Esteves 2021",
                category="medical",
            ),
        ],
    },
    "sperm_vitality": {
        "low": [
            Intervention(
                action="Vitamin C 1 g + Vitamin E 400 IU daily for 8 weeks",
                mechanism="Reduces oxidative damage to sperm membranes",
                expected_effect="Vitality + count improvement",
                citation="Greco 2005",
                category="supplement",
            ),
            _SCROTAL_COOL,
            _ANTIOX_STACK,
        ],
    },
    "sperm_volume": {
        "low": [
            Intervention(
                action="Hydration 2.5-3 L/day plain water from waking",
                mechanism="Semen is 80% water — chronic dehydration shrinks ejaculate",
                expected_effect="Volume recovery within days",
                citation="WHO 2021 6th ed",
                category="nutrition",
            ),
            Intervention(
                action="2-4 day abstinence window before sperm test",
                mechanism="WHO collection standard — under-1 or over-7 day windows distort volume",
                expected_effect="Volume normalizes to true baseline",
                citation="WHO 2021 6th ed",
                category="lifestyle",
            ),
            Intervention(
                action="Urology referral if volume persistently <1 mL — rule out retrograde ejaculation, ejaculatory duct obstruction",
                mechanism="Persistent low volume is a structural/neurological flag",
                expected_effect="Diagnostic workup; treatment depends on cause",
                citation="WHO 2021 6th ed",
                category="medical",
            ),
        ],
    },
    "sperm_leukocytes": {
        "high": [
            Intervention(
                action="Urology referral + semen culture to identify pathogen",
                mechanism="Leukocytospermia (>1 M/mL) is often silent infection (E. coli, Ureaplasma, Chlamydia)",
                expected_effect="Targeted antibiotic course typically clears in 4-6 weeks",
                citation="WHO 2021 6th ed",
                category="medical",
            ),
            Intervention(
                action="Antioxidant stack (NAC 600 mg + Vit C/E) for 8 weeks while infection is being worked up",
                mechanism="Leukocytes generate ROS that compound the sperm damage",
                expected_effect="Reduced collateral oxidative stress on sperm DNA",
                citation="Aitken 2014",
                category="supplement",
            ),
        ],
    },
    "sperm_pH": {
        "low": [
            Intervention(
                action="Urology referral — pH <7.2 suggests seminal-vesicle / ejaculatory-duct obstruction or absent vas",
                mechanism="Acidic semen indicates the alkaline seminal-vesicle contribution is missing",
                expected_effect="Diagnostic workup with transrectal ultrasound",
                citation="WHO 2021 6th ed",
                category="medical",
            ),
        ],
        "high": [
            Intervention(
                action="Urology referral — pH >8.0 suggests accessory-gland infection",
                mechanism="Alkaline shift is a marker of prostatitis or seminal-vesiculitis",
                expected_effect="Workup + antibiotic if infection confirmed",
                citation="WHO 2021 6th ed",
                category="medical",
            ),
        ],
    },
    "sperm_mar_test": {
        "high": [
            Intervention(
                action="Urology / reproductive specialist referral — confirm antisperm-antibody titer + IgG/IgA subclass",
                mechanism="MAR >50% indicates clinically significant antisperm antibodies (immune infertility)",
                expected_effect="Discussion of corticosteroid trial or direct ART (IUI/IVF with ICSI)",
                citation="WHO 2021 6th ed",
                category="medical",
            ),
        ],
    },

    # ───────── HORMONES ─────────
    "testosterone_total": {
        "low": [
            _SLEEP_7,
            Intervention(
                action="Resistance training 3×/week, compound lifts (squat/deadlift/bench/row)",
                mechanism="Acute T spike per session + cumulative free-T gains in trained men",
                expected_effect="Free-T +15-20% over 12 weeks",
                citation="Vingren 2010",
                category="movement",
            ),
            Intervention(
                action="Vitamin D3 2000-4000 IU + Zinc 25-50 mg daily (correct any deficiency)",
                mechanism="Both are required for the testicular Leydig-cell steroidogenesis pathway",
                expected_effect="T rises by ~25% if vit D was <30 ng/mL at baseline",
                citation="Pilz 2011",
                category="supplement",
            ),
            Intervention(
                action="Body-fat reduction toward 12-18% if currently >25%",
                mechanism="Visceral adipose tissue aromatizes T to estradiol",
                expected_effect="T recovers in 6-12 months with sustained loss",
                citation="Camacho 2013",
                category="nutrition",
            ),
        ],
    },
    "prolactin": {
        "high": [
            Intervention(
                action="Stress reduction protocol — daily 10 min box breathing, no screens after 22:30",
                mechanism="Prolactin tracks stress + sleep deprivation; pulses through the day",
                expected_effect="Mild elevations (15-25) often normalize in 4-8 weeks",
                citation="Lennartsson 2015",
                category="stress",
            ),
            Intervention(
                action="MRI pituitary if prolactin persistently >25 ng/mL on repeat draw",
                mechanism="Rule out prolactinoma — the most common pituitary adenoma",
                expected_effect="Diagnostic — most prolactinomas treated medically with cabergoline",
                citation="Melmed 2011 Endocrine Society",
                category="medical",
            ),
        ],
    },
    "shbg": {
        "low": [
            Intervention(
                action="Reduce visceral fat — waist circumference target <94 cm",
                mechanism="Hepatic insulin resistance suppresses SHBG synthesis",
                expected_effect="SHBG climbs as visceral fat drops",
                citation="Liu 2007",
                category="nutrition",
            ),
        ],
    },

    # ───────── LIPIDS — cardiovascular ─────────
    "ldl": {
        "high": [
            Intervention(
                action="Mediterranean diet — EVOO 4 tbsp/day, nuts 30 g/day, fish 2×/week, low red meat",
                mechanism="Substitutes saturated fat for MUFA/PUFA; lowers LDL and CVD events",
                expected_effect="LDL -10 to -15% over 3-6 months; PREDIMED showed -30% major CV events",
                citation="Estruch 2018 PREDIMED",
                category="nutrition",
            ),
            Intervention(
                action="Soluble fiber 10-15 g/day — oats, psyllium, beans, apples",
                mechanism="Binds bile acids, increases hepatic LDL clearance",
                expected_effect="LDL -5 to -10%",
                citation="Brown 1999",
                category="nutrition",
            ),
            Intervention(
                action="Discuss statin with cardiologist if LDL >130 + ApoB >100 or ASCVD risk >7.5%",
                mechanism="Statins drop LDL 40-60% and reduce CV events ~25% per mmol/L lowering",
                expected_effect="LDL target <70 for ITGB3-carrier + family-history profile",
                citation="Mach 2020 ESC",
                category="medical",
            ),
        ],
    },
    "apob": {
        "high": [
            Intervention(
                action="ApoB is the truer atherogenic count — target <80 mg/dL (primary prev), <65 (secondary)",
                mechanism="ApoB counts every atherogenic particle; LDL-C undercounts in TG-rich states",
                expected_effect="Same lifestyle/statin levers as LDL but tracked by particle count",
                citation="Mach 2020 ESC",
                category="medical",
            ),
            Intervention(
                action="Mediterranean diet + 30 min Z2 cardio 5×/week",
                mechanism="Combined effect on hepatic ApoB production",
                expected_effect="ApoB -10 to -20% in 12 weeks",
                citation="Estruch 2018 PREDIMED",
                category="nutrition",
            ),
        ],
    },
    "triglycerides": {
        "high": [
            Intervention(
                action="EPA+DHA 2-4 g/day (prescription icosapent or high-purity fish oil)",
                mechanism="Reduces hepatic VLDL secretion",
                expected_effect="Triglycerides -20 to -40% over 8-12 weeks",
                citation="Skulas-Ray 2019",
                category="supplement",
            ),
            Intervention(
                action="Eliminate liquid sugar (juice, soda) + reduce alcohol to ≤3 units/week",
                mechanism="Both drive hepatic de-novo lipogenesis",
                expected_effect="TG -25% within 4 weeks",
                citation="Stanhope 2009",
                category="nutrition",
            ),
        ],
    },
    "hdl": {
        "low": [
            Intervention(
                action="Aerobic exercise 150 min/week + 2 resistance sessions",
                mechanism="Increases LCAT-mediated HDL maturation",
                expected_effect="HDL +5-10% over 12 weeks",
                citation="Kodama 2007",
                category="movement",
            ),
            Intervention(
                action="EPA+DHA 1.5-2 g/day + replace refined carbs with MUFA (olive oil, avocado)",
                mechanism="MUFA modestly raises HDL; omega-3 corrects HDL function",
                expected_effect="HDL +3-8% in 12 weeks",
                citation="Estruch 2018 PREDIMED",
                category="nutrition",
            ),
        ],
    },
    "lp_a": {
        "high": [
            Intervention(
                action="Aggressive LDL/ApoB lowering (target LDL <55, ApoB <65) — lifestyle + statin if needed",
                mechanism="Lp(a) is genetically fixed; total atherogenic burden control is the only proven lever",
                expected_effect="Reduces cumulative LDL+Lp(a) exposure",
                citation="Mach 2020 ESC",
                category="medical",
            ),
            Intervention(
                action="Cardiology referral if Lp(a) >50 mg/dL plus any family CV history",
                mechanism="Lp(a) is an independent atherogenic risk factor — emerging PCSK9 + olpasiran therapies",
                expected_effect="Specialist risk-stratification; possible new-agent eligibility",
                citation="Mach 2020 ESC",
                category="medical",
            ),
        ],
    },

    # ───────── METABOLIC ─────────
    "hba1c": {
        "high": [
            Intervention(
                action="Zone-2 cardio 150 min/week (talking pace, HR ~70% max) + 2 resistance sessions",
                mechanism="Improves insulin sensitivity in skeletal muscle (largest glucose sink)",
                expected_effect="HbA1c -0.3 to -0.7% over 12 weeks",
                citation="ADA 2024",
                category="movement",
            ),
            Intervention(
                action="Time-restricted eating 12-14 h overnight fast; replace refined carbs with protein + fiber",
                mechanism="Lowers postprandial glucose excursions and fasting insulin",
                expected_effect="HbA1c -0.3 to -0.5% in 3 months",
                citation="Sutton 2018",
                category="nutrition",
            ),
        ],
    },
    "glucose_fasting": {
        "high": [
            Intervention(
                action="Same protocol as HbA1c — Z2 cardio + TRE + reduce refined carbs",
                mechanism="Restores insulin sensitivity and fasting glucose control",
                expected_effect="Fasting glucose -10 to -20 mg/dL in 8-12 weeks",
                citation="ADA 2024",
                category="movement",
            ),
        ],
    },
    "homocysteine": {
        "high": [
            Intervention(
                action="Methylfolate 400-800 µg + Methyl-B12 1000 µg + B6 (P5P) 25 mg daily",
                mechanism="Direct substrates for the methionine cycle — bypasses any MTHFR sluggishness",
                expected_effect="Homocysteine -25 to -40% in 8 weeks",
                citation="Wong 2002",
                category="supplement",
            ),
            Intervention(
                action="Reduce alcohol to ≤3 units/week (alcohol disrupts B-vitamin status)",
                mechanism="Alcohol depletes folate + B12 cofactors",
                expected_effect="Cofactor recovery within weeks",
                citation="Halsted 2002",
                category="lifestyle",
            ),
        ],
    },

    # ───────── INFLAMMATION ─────────
    "crp_hs": {
        "high": [
            Intervention(
                action="Mediterranean diet + EPA+DHA 2 g/day",
                mechanism="Anti-inflammatory food matrix + resolvin/protectin precursors",
                expected_effect="hs-CRP -1 to -2 mg/L over 12 weeks",
                citation="Calder 2017",
                category="nutrition",
            ),
            Intervention(
                action="Rule out occult inflammation — dental abscess, periodontitis, gut dysbiosis",
                mechanism="Common silent drivers of chronic low-grade inflammation",
                expected_effect="Targeted treatment normalizes hs-CRP",
                citation="Ridker 2003",
                category="medical",
            ),
        ],
    },

    # ───────── HEMA + IRON ─────────
    "hemoglobin": {
        "low": [
            Intervention(
                action="Check ferritin first — most low-Hgb in men is iron-deficiency from GI loss",
                mechanism="Iron-deficiency anemia is the single most common cause; non-iron causes need different workup",
                expected_effect="Ferritin guides iron therapy + GI workup",
                citation="WHO anemia criteria",
                category="medical",
            ),
            Intervention(
                action="If ferritin <30: iron bisglycinate 25 mg + vitamin C 500 mg daily, on empty stomach",
                mechanism="Bisglycinate has higher absorption + less GI upset than sulfate",
                expected_effect="Hgb +1-2 g/dL over 2-3 months",
                citation="Camaschella 2015",
                category="supplement",
            ),
        ],
        "high": [
            Intervention(
                action="Hydration + check for sleep apnea, polycythemia vera (JAK2), or anabolic-androgen use",
                mechanism="Erythrocytosis flags hypoxia, primary marrow disorder, or exogenous androgens",
                expected_effect="Diagnostic — treatment depends on cause",
                citation="Tefferi 2013",
                category="medical",
            ),
        ],
    },
    "hematocrit": {
        "low": [
            Intervention(
                action="See Hemoglobin protocol — same iron / B12 / folate workup",
                mechanism="Hct tracks Hgb closely",
                expected_effect="Parallels Hgb recovery",
                citation="WHO anemia criteria",
                category="medical",
            ),
        ],
        "high": [
            Intervention(
                action="Same workup as high Hgb (OSA, polycythemia, androgen use)",
                mechanism="Erythrocytosis evaluation",
                expected_effect="Cause-directed treatment",
                citation="Tefferi 2013",
                category="medical",
            ),
        ],
    },
    "ferritin": {
        "low": [
            Intervention(
                action="Iron bisglycinate 25 mg + Vit C 500 mg daily, alternate-day dosing",
                mechanism="Alternate-day dosing increases net absorption by avoiding hepcidin spike",
                expected_effect="Ferritin +20-50 ng/mL over 3 months",
                citation="Stoffel 2017",
                category="supplement",
            ),
            Intervention(
                action="GI workup (colonoscopy / EGD) if no obvious blood-loss source",
                mechanism="Iron deficiency in an adult man without obvious loss requires GI evaluation",
                expected_effect="Identify and treat any occult bleed",
                citation="Goddard 2011",
                category="medical",
            ),
        ],
        "high": [
            Intervention(
                action="Reduce red-meat intake; consider voluntary blood donation 2-3×/year",
                mechanism="Lowers iron stores when overload is dietary rather than hereditary",
                expected_effect="Ferritin -50 to -100 ng/mL per donation",
                citation="Crownover 2013",
                category="lifestyle",
            ),
            Intervention(
                action="HFE genotype + transferrin saturation to rule out hereditary hemochromatosis",
                mechanism="HFE C282Y homozygous needs therapeutic phlebotomy program",
                expected_effect="Confirms or excludes hemochromatosis",
                citation="EASL 2010",
                category="medical",
            ),
        ],
    },

    # ───────── VITAMINS / MICRONUTRIENTS ─────────
    "vitamin_d_25oh": {
        "low": [
            Intervention(
                action="Vitamin D3 2000-4000 IU + K2 (MK-7) 100 µg daily with the largest meal of the day",
                mechanism="K2 directs serum calcium to bone (not arteries) while D3 raises 25(OH)D",
                expected_effect="25(OH)D climbs 10-15 ng/mL per 1000 IU/day over 8-12 weeks",
                citation="Holick 2011 Endocrine Society",
                category="supplement",
            ),
            Intervention(
                action="10-15 min midday sun exposure on arms/legs, 3-5×/week (May-Sept at Paris latitude)",
                mechanism="Cutaneous UVB→7-dehydrocholesterol photoconversion",
                expected_effect="Free 25(OH)D synthesis without supplement load",
                citation="Holick 2011 Endocrine Society",
                category="lifestyle",
            ),
        ],
    },
    "zinc": {
        "low": [
            Intervention(
                action="Zinc bisglycinate 25-50 mg with food (NOT with iron, calcium, or coffee — they block absorption)",
                mechanism="Cofactor in 300+ enzymes incl. spermatogenesis and immune function",
                expected_effect="Plasma zinc normalizes in 4-8 weeks",
                citation="Colagar 2009",
                category="supplement",
            ),
        ],
    },

    # ───────── THYROID ─────────
    "tsh": {
        "high": [
            Intervention(
                action="Selenium 200 µg daily (selenomethionine), recheck TSH + free T4 in 6 weeks",
                mechanism="Cofactor for thyroid peroxidase and 5'-deiodinase",
                expected_effect="Mild subclinical hypothyroidism (TSH 4-10) often improves",
                citation="Negro 2007",
                category="supplement",
            ),
            Intervention(
                action="Endocrinology referral if TSH >10 mIU/L, or >4 with anti-TPO positive",
                mechanism="Frank hypothyroidism or Hashimoto's — levothyroxine candidate",
                expected_effect="TSH target 0.5-2.5 on therapy",
                citation="ATA 2014",
                category="medical",
            ),
        ],
    },

    # ───────── CANCER SCREENING ─────────
    "psa_total": {
        "high": [
            Intervention(
                action="Urology referral — repeat PSA + free/total ratio + DRE; consider MRI prostate",
                mechanism="Single PSA above age threshold needs confirmation and risk-stratification",
                expected_effect="Diagnostic — most elevations are BPH/inflammation, not cancer",
                citation="EAU 2023",
                category="medical",
            ),
        ],
    },
}


def get_interventions(
    marker_id: str, flagged: Optional[str], limit: int = 5
) -> List[Intervention]:
    """Return evidence-based corrective actions for a (marker, flagged) pair.

    ``flagged`` is one of ``low``, ``high``, ``optimal``, or None. We only
    return entries for ``low`` / ``high`` — optimal/in-range needs no
    correction. Returns up to ``limit`` interventions (default 5), ordered
    by appearance in the library (most-impactful first by convention).
    """
    if not flagged or flagged not in ("low", "high"):
        return []
    marker_entry = INTERVENTIONS.get(marker_id, {})
    return list(marker_entry.get(flagged, []))[:limit]


def get_all_marker_ids() -> List[str]:
    """Marker IDs covered by the intervention library — for tests."""
    return sorted(INTERVENTIONS.keys())
