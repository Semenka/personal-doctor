"""Single source of truth for the scientific citations cited across the app.

Every paper the registry, intervention library, and advisor system prompt
references should live here as a ``Paper`` entry — a frozen dataclass with
a stable short ID, a display string, the year, the topic, and a one-line
note on why this is the canonical pick. Callers reference papers by ID
(``lookup("salas_huetos_2017")``) so refreshing the bibliography is one
line per swap, not a grep-and-replace across the codebase.

Curation policy: prefer 2015–2025 systematic reviews, consensus
statements, and large RCTs. Pre-2015 entries are kept ONLY when nothing
newer is canonical (e.g. Mieusset & Bujan 1995 stays as the *origin* RCT
on scrotal heat, but Durairajanayagam 2014 is the working reference).

The selection bar is meant to be deliberately high:
 - Cochrane reviews (Showell 2019 update)
 - ESC / EAS / Endocrine Society / ATA / ADA / AHA guidelines
 - ESHRE / Andrology / Fertility & Sterility consensus papers
 - Mendelian-randomization causal evidence (Ference 2017 on LDL)
 - High-cited systematic reviews in Human Reproduction Update
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class Paper:
    """One named bibliography entry referenced across the app."""

    ref_id: str   # stable short identifier
    citation: str # display string ("Author Year (Journal)")
    year: int
    topic: str    # broad area — "sperm_quality", "lipids", "vit_d", ...
    notes: str    # one-line: why this is the canonical pick


# ──────────────────────────────────────────────────────────────────────────
# Bibliography — 2015-2025 weighted, with deliberate pre-2015 keeps.
# ──────────────────────────────────────────────────────────────────────────

PAPERS: Dict[str, Paper] = {
    # ── REPRODUCTIVE / SEMEN QUALITY ─────────────────────────────────────
    "who_2021": Paper(
        "who_2021",
        "WHO 2021 6th ed",
        2021, "sperm_quality",
        "Sixth edition of the WHO laboratory manual for semen analysis; "
        "current reference ranges used worldwide.",
    ),
    "salas_huetos_2017": Paper(
        "salas_huetos_2017",
        "Salas-Huetos 2017 (Hum Reprod Update)",
        2017, "sperm_quality",
        "Systematic review on diet and sperm quality — the most-cited "
        "fertility-nutrition paper of the era. Anchor for nutrition advice.",
    ),
    "salas_huetos_2018": Paper(
        "salas_huetos_2018",
        "Salas-Huetos 2018 (Andrology)",
        2018, "sperm_quality",
        "Systematic review of micronutrients (Zn, Se, CoQ10, carnitine, "
        "folate) on semen parameters. Anchor for supplement recommendations.",
    ),
    "salas_huetos_2019": Paper(
        "salas_huetos_2019",
        "Salas-Huetos 2019 (Andrology)",
        2019, "sperm_quality",
        "PUFA / omega-3 systematic review for male fertility.",
    ),
    "showell_2019_cochrane": Paper(
        "showell_2019_cochrane",
        "Showell 2019 Cochrane",
        2019, "sperm_antioxidants",
        "Updated Cochrane review of antioxidants for male subfertility "
        "(replaces Showell 2014). Anchor for antioxidant-stack advice.",
    ),
    "esteves_2021": Paper(
        "esteves_2021",
        "Esteves 2021 (Andrology)",
        2021, "sperm_dna_fragmentation",
        "Consensus on sperm DNA fragmentation testing + clinical management; "
        "anchor for DFI interventions and varicocele referral.",
    ),
    "agarwal_2020": Paper(
        "agarwal_2020",
        "Agarwal 2020 (Transl Androl Urol)",
        2020, "sperm_dna_fragmentation",
        "Systematic review on sperm DNA fragmentation assays and outcomes.",
    ),
    "gatimel_2017": Paper(
        "gatimel_2017",
        "Gatimel 2017 (Andrology)",
        2017, "sperm_morphology",
        "Canonical 2010s-era review of sperm morphology assessment (strict "
        "Kruger). Anchor for morphology context.",
    ),
    "durairajanayagam_2014": Paper(
        "durairajanayagam_2014",
        "Durairajanayagam 2014 (Reprod Biomed Online)",
        2014, "sperm_heat",
        "Comprehensive review of testicular heat stress and male infertility. "
        "Working reference for scrotal-cooling advice.",
    ),
    "sharma_2013": Paper(
        "sharma_2013",
        "Sharma 2013 (Reprod Biol Endocrinol)",
        2013, "sperm_lifestyle",
        "Lifestyle factors and reproductive health review — heat, smoking, "
        "alcohol, BMI, occupational exposures.",
    ),
    "mieusset_bujan_1995": Paper(
        "mieusset_bujan_1995",
        "Mieusset & Bujan 1995",
        1995, "sperm_heat",
        "Origin study on scrotal heat exposure and sperm motility; kept as "
        "the foundational evidence even though newer reviews exist.",
    ),
    "banihani_2018": Paper(
        "banihani_2018",
        "Banihani 2018 (review)",
        2018, "sperm_coq10",
        "CoQ10 supplementation in male infertility — narrative review of "
        "mechanism + clinical evidence.",
    ),
    "safarinejad_2009": Paper(
        "safarinejad_2009",
        "Safarinejad 2009 (J Urol RCT)",
        2009, "sperm_coq10",
        "Original 200 mg CoQ10 RCT showing +26% motility over 12 weeks. "
        "Kept as the origin RCT alongside Salas-Huetos 2018.",
    ),
    "mongioi_2016": Paper(
        "mongioi_2016",
        "Mongioi 2016 (Andrologia)",
        2016, "sperm_carnitine",
        "Updated L-carnitine RCT in asthenozoospermia. Anchor for carnitine "
        "intervention.",
    ),
    "buhling_grajecki_2014": Paper(
        "buhling_grajecki_2014",
        "Buhling & Grajecki 2014 (review)",
        2014, "sperm_micronutrients",
        "Systematic review of micronutrient supplementation in male "
        "subfertility (Zn, Se, folate, vitamins).",
    ),
    "colagar_2009": Paper(
        "colagar_2009",
        "Colagar 2009 (Nutr Res)",
        2009, "sperm_zinc",
        "Zinc and selenium and semen parameters — kept as the origin "
        "intervention study.",
    ),
    "wong_2002": Paper(
        "wong_2002",
        "Wong 2002 (Fertil Steril RCT)",
        2002, "sperm_folate",
        "Folate + zinc RCT showing improved sperm concentration; kept as "
        "the origin trial alongside Salas-Huetos 2018.",
    ),
    "hosseini_2019": Paper(
        "hosseini_2019",
        "Hosseini 2019 (RCT)",
        2019, "sperm_omega3",
        "Omega-3 supplementation RCT in infertile men — improved motility "
        "and concentration.",
    ),
    "leproult_van_cauter_2011": Paper(
        "leproult_van_cauter_2011",
        "Leproult & Van Cauter 2011 (JAMA)",
        2011, "sleep_testosterone",
        "Sleep restriction (5 h/night × 1 wk) drops young-male testosterone "
        "by 10-15%. Mechanistic anchor for sleep advice — kept as canonical.",
    ),
    "wang_2018_sleep_semen": Paper(
        "wang_2018_sleep_semen",
        "Wang 2018 (Sleep Med Rev)",
        2018, "sleep_semen",
        "Meta-analysis of sleep duration and semen quality.",
    ),
    "hajizadeh_maleki_2018": Paper(
        "hajizadeh_maleki_2018",
        "Hajizadeh Maleki 2018 (Reproduction)",
        2018, "sperm_exercise",
        "RCT of moderate aerobic + resistance exercise on semen parameters "
        "in sedentary men. Anchor for exercise advice.",
    ),
    "gaskins_2015": Paper(
        "gaskins_2015",
        "Gaskins 2015 (Hum Reprod)",
        2015, "sperm_exercise",
        "Physical activity and semen quality — cohort evidence.",
    ),
    "nargund_2015": Paper(
        "nargund_2015",
        "Nargund 2015 (review)",
        2015, "sperm_stress",
        "Stress and male reproduction — narrative review of HPA axis impact "
        "on spermatogenesis.",
    ),
    "ilacqua_2018": Paper(
        "ilacqua_2018",
        "Ilacqua 2018 (Reprod Biol Endocrinol)",
        2018, "sperm_lifestyle",
        "Lifestyle and fertility review — overlap with stress, BMI, sleep, "
        "smoking, alcohol.",
    ),
    "sermondade_2013": Paper(
        "sermondade_2013",
        "Sermondade 2013 (Hum Reprod Update)",
        2013, "sperm_obesity",
        "Meta-analysis of BMI and semen quality. Working anchor for BMI / "
        "morphology counseling.",
    ),
    "boeri_2019": Paper(
        "boeri_2019",
        "Boeri 2019 (Andrology)",
        2019, "sperm_alcohol",
        "Alcohol consumption and sperm DNA fragmentation in infertile men.",
    ),
    "greco_2005": Paper(
        "greco_2005",
        "Greco 2005 (J Androl)",
        2005, "sperm_vitamins_c_e",
        "Vitamin C + E supplementation reduces sperm DNA damage; kept as "
        "the origin RCT.",
    ),

    # ── HORMONES / ENDOCRINE ──────────────────────────────────────────────
    "travison_2017": Paper(
        "travison_2017",
        "Travison 2017 (J Clin Endocrinol Metab)",
        2017, "testosterone",
        "Harmonized testosterone reference ranges across four major US "
        "cohorts. Anchor for serum-T interpretation.",
    ),
    "mulhall_2018": Paper(
        "mulhall_2018",
        "Mulhall 2018 (AUA guideline)",
        2018, "testosterone",
        "American Urological Association evaluation and management of "
        "testosterone deficiency.",
    ),
    "bhasin_2018": Paper(
        "bhasin_2018",
        "Bhasin 2018 (Endocrine Society guideline)",
        2018, "testosterone",
        "Endocrine Society Clinical Practice Guideline on testosterone "
        "therapy in adult men with androgen deficiency.",
    ),
    "vingren_2010": Paper(
        "vingren_2010",
        "Vingren 2010 (Sports Med)",
        2010, "testosterone_exercise",
        "Resistance exercise and androgenic response in trained men — "
        "mechanistic anchor for lifting recommendations.",
    ),
    "camacho_2013": Paper(
        "camacho_2013",
        "Camacho 2013 (Eur J Endocrinol)",
        2013, "testosterone_obesity",
        "European Male Ageing Study: body fat, T, and metabolic syndrome.",
    ),
    "goldman_2017": Paper(
        "goldman_2017",
        "Goldman 2017 (Endocr Rev)",
        2017, "shbg",
        "SHBG reference review including its insulin and visceral-adiposity "
        "links.",
    ),
    "finkelstein_2013": Paper(
        "finkelstein_2013",
        "Finkelstein 2013 (NEJM)",
        2013, "estradiol",
        "Gonadal steroid effects of T and E2 in men — landmark separation "
        "trial.",
    ),
    "melmed_2011": Paper(
        "melmed_2011",
        "Melmed 2011 (Endocrine Society)",
        2011, "prolactin",
        "Endocrine Society guideline on diagnosis and treatment of "
        "hyperprolactinemia.",
    ),
    "lennartsson_2015": Paper(
        "lennartsson_2015",
        "Lennartsson 2015 (PLoS ONE)",
        2015, "prolactin_stress",
        "Prolactin response to acute psychosocial stress and sleep "
        "restriction.",
    ),
    "liu_2007": Paper(
        "liu_2007",
        "Liu 2007 (J Clin Endocrinol Metab)",
        2007, "shbg_metabolism",
        "Hepatic SHBG synthesis and insulin — visceral-adiposity link.",
    ),

    # ── LIPIDS / CARDIOVASCULAR ──────────────────────────────────────────
    "mach_2020_esc": Paper(
        "mach_2020_esc",
        "Mach 2020 ESC dyslipidaemia",
        2020, "lipids",
        "ESC/EAS guideline on dyslipidaemias — anchor for LDL/ApoB/Lp(a) "
        "targets and statin discussion.",
    ),
    "sniderman_2019": Paper(
        "sniderman_2019",
        "Sniderman 2019 (Lancet)",
        2019, "apob",
        "ApoB is the truer atherogenic particle count — landmark review.",
    ),
    "kronenberg_2022_eas": Paper(
        "kronenberg_2022_eas",
        "Kronenberg 2022 EAS consensus on Lp(a)",
        2022, "lp_a",
        "EAS consensus on Lp(a) measurement, risk, and emerging therapy.",
    ),
    "ference_2017_eas": Paper(
        "ference_2017_eas",
        "Ference 2017 EAS (Mendelian randomization)",
        2017, "lipids_causality",
        "Mendelian randomization establishes the causal LDL-CVD relationship.",
    ),
    "ctt_2019": Paper(
        "ctt_2019",
        "CTT Collaboration 2019",
        2019, "statins",
        "Cholesterol Treatment Trialists' Collaboration meta-analysis — "
        "cardiovascular benefit per mmol/L LDL reduction.",
    ),
    "tsimikas_2017": Paper(
        "tsimikas_2017",
        "Tsimikas 2017 (J Am Coll Cardiol)",
        2017, "lp_a",
        "Lp(a) cardiovascular risk review — kept as a secondary anchor "
        "alongside Kronenberg 2022.",
    ),
    "estruch_2018_predimed": Paper(
        "estruch_2018_predimed",
        "Estruch 2018 PREDIMED (NEJM)",
        2018, "diet_cv",
        "Mediterranean diet primary-prevention trial — 30% reduction in "
        "major CV events.",
    ),
    "brown_1999_fiber": Paper(
        "brown_1999_fiber",
        "Brown 1999 (Am J Clin Nutr)",
        1999, "soluble_fiber",
        "Cholesterol-lowering effects of soluble fiber — kept as origin "
        "meta-analysis.",
    ),
    "ridker_2018_cantos": Paper(
        "ridker_2018_cantos",
        "Ridker 2018 CANTOS (Lancet)",
        2018, "inflammation_cv",
        "Canakinumab anti-inflammatory therapy reduces CV events even "
        "without LDL change — proves the residual-inflammation pathway.",
    ),
    "ridker_2003": Paper(
        "ridker_2003",
        "Ridker 2003",
        2003, "hs_crp",
        "Original signal that hs-CRP predicts CV events independent of "
        "LDL. Kept as the origin reference.",
    ),
    "calder_2017": Paper(
        "calder_2017",
        "Calder 2017 (Biochem Soc Trans)",
        2017, "omega3_inflammation",
        "Omega-3 fatty acids and inflammation review.",
    ),
    "mozaffarian_2018": Paper(
        "mozaffarian_2018",
        "Mozaffarian 2018 (Circulation)",
        2018, "omega3_cv",
        "Omega-3 fatty acids and cardiovascular outcomes — comprehensive "
        "review.",
    ),
    "skulas_ray_2019_aha": Paper(
        "skulas_ray_2019_aha",
        "Skulas-Ray 2019 AHA scientific statement",
        2019, "omega3_triglycerides",
        "AHA scientific statement on omega-3 EPA+DHA for hypertriglyceridemia.",
    ),
    "stanhope_2009": Paper(
        "stanhope_2009",
        "Stanhope 2009 (J Clin Invest)",
        2009, "fructose_lipogenesis",
        "Fructose vs glucose consumption and de-novo hepatic lipogenesis — "
        "kept as the canonical mechanism paper.",
    ),
    "kodama_2007": Paper(
        "kodama_2007",
        "Kodama 2007 (Arch Intern Med)",
        2007, "exercise_hdl",
        "Aerobic exercise intensity meta-analysis for HDL elevation.",
    ),

    # ── METABOLIC ────────────────────────────────────────────────────────
    "ada_2024": Paper(
        "ada_2024",
        "ADA 2024 Standards of Care",
        2024, "glycemic",
        "American Diabetes Association standards of medical care — anchor "
        "for HbA1c targets and lifestyle thresholds.",
    ),
    "selvin_2010": Paper(
        "selvin_2010",
        "Selvin 2010 (NEJM)",
        2010, "hba1c",
        "HbA1c as predictor of CV events and mortality in non-diabetics.",
    ),
    "matthews_1985": Paper(
        "matthews_1985",
        "Matthews 1985 (Diabetologia)",
        1985, "homa_ir",
        "Original HOMA-IR derivation. Kept because no replacement exists.",
    ),
    "petersen_2018": Paper(
        "petersen_2018",
        "Petersen 2018 (Cell Metab)",
        2018, "insulin_resistance",
        "Insulin resistance mechanism and reversal — review.",
    ),
    "sutton_2018": Paper(
        "sutton_2018",
        "Sutton 2018 (Cell Metab)",
        2018, "time_restricted_eating",
        "Early time-restricted feeding improves insulin sensitivity in "
        "men with prediabetes.",
    ),
    "smith_2018": Paper(
        "smith_2018",
        "Smith 2018",
        2018, "homocysteine",
        "Homocysteine, B-vitamins, and disease risk — current canonical "
        "review.",
    ),
    "halsted_2002": Paper(
        "halsted_2002",
        "Halsted 2002 (Alcohol Res Health)",
        2002, "alcohol_b_vitamins",
        "Alcohol disrupts folate and B12 — origin mechanism reference.",
    ),

    # ── VITAMINS / MICRONUTRIENTS ───────────────────────────────────────
    "holick_2011_endo_soc": Paper(
        "holick_2011_endo_soc",
        "Holick 2011 Endocrine Society",
        2011, "vit_d",
        "Endocrine Society Clinical Practice Guideline on vitamin D — "
        "still the operational guideline.",
    ),
    "pludowski_2018": Paper(
        "pludowski_2018",
        "Pludowski 2018 (Eur J Clin Nutr)",
        2018, "vit_d",
        "Vitamin D clinical practice review with dose recommendations "
        "across ages, regions, and risk groups.",
    ),
    "bouillon_2019": Paper(
        "bouillon_2019",
        "Bouillon 2019 (Endocr Rev)",
        2019, "vit_d_skeletal_extraskeletal",
        "Skeletal and extraskeletal actions of vitamin D — comprehensive "
        "review.",
    ),
    "pilz_2011": Paper(
        "pilz_2011",
        "Pilz 2011 (Horm Metab Res)",
        2011, "vit_d_testosterone",
        "Original RCT showing D3 increases testosterone in vit-D-deficient "
        "men. Kept as the origin trial.",
    ),
    "wessells_2012": Paper(
        "wessells_2012",
        "Wessells 2012 (PLoS ONE)",
        2012, "zinc_status",
        "Estimating the global prevalence of zinc deficiency — anchor for "
        "general zinc recommendations.",
    ),
    "prasad_2008": Paper(
        "prasad_2008",
        "Prasad 2008",
        2008, "zinc",
        "Zinc in human health — origin review.",
    ),
    "harris_2017": Paper(
        "harris_2017",
        "Harris 2017 (Mayo Clin Proc)",
        2017, "omega3_index",
        "Omega-3 index update — review on RBC-membrane EPA+DHA targets.",
    ),
    "boyle_2017": Paper(
        "boyle_2017",
        "Boyle 2017 (Nutrients)",
        2017, "magnesium_sleep",
        "Systematic review on magnesium status, depression, and sleep "
        "quality.",
    ),
    "abbasi_2012": Paper(
        "abbasi_2012",
        "Abbasi 2012 (J Res Med Sci RCT)",
        2012, "magnesium_sleep",
        "Original Mg-glycinate 500 mg RCT for insomnia in elderly. Kept "
        "as the origin RCT.",
    ),
    "negro_2007": Paper(
        "negro_2007",
        "Negro 2007 (J Clin Endocrinol Metab)",
        2007, "selenium_thyroid",
        "Selenium supplementation in Hashimoto's; kept as the origin RCT.",
    ),
    "ata_2014": Paper(
        "ata_2014",
        "ATA 2014 (Thyroid)",
        2014, "thyroid",
        "American Thyroid Association guideline for management of "
        "thyrotropin elevation.",
    ),

    # ── HEMA / IRON / NEPHRO / LIVER ───────────────────────────────────
    "who_anemia": Paper(
        "who_anemia",
        "WHO anemia criteria",
        2011, "hemoglobin",
        "WHO Hgb thresholds for anemia by sex and age.",
    ),
    "who_iron_status": Paper(
        "who_iron_status",
        "WHO iron status guidelines",
        2020, "iron",
        "WHO indicators for iron deficiency and overload.",
    ),
    "camaschella_2015": Paper(
        "camaschella_2015",
        "Camaschella 2015 (NEJM)",
        2015, "iron_deficiency",
        "Iron-deficiency anemia review — diagnostic and therapeutic anchor.",
    ),
    "stoffel_2017": Paper(
        "stoffel_2017",
        "Stoffel 2017 (Lancet Haematol)",
        2017, "iron_dosing",
        "Alternate-day iron dosing increases net absorption by avoiding "
        "hepcidin spike.",
    ),
    "goddard_2011_bsg": Paper(
        "goddard_2011_bsg",
        "Goddard 2011 (BSG guideline)",
        2011, "iron_gi_workup",
        "British Society of Gastroenterology guidelines for iron-deficiency "
        "anemia GI workup.",
    ),
    "crownover_2013": Paper(
        "crownover_2013",
        "Crownover 2013 (Am Fam Physician)",
        2013, "iron_overload",
        "Iron overload management — lifestyle + phlebotomy review.",
    ),
    "easl_2010": Paper(
        "easl_2010",
        "EASL 2010 hemochromatosis",
        2010, "hemochromatosis",
        "EASL clinical practice guidelines for HFE hemochromatosis.",
    ),
    "tefferi_2013": Paper(
        "tefferi_2013",
        "Tefferi 2013 (Am J Hematol)",
        2013, "polycythemia",
        "Polycythemia diagnosis + management review.",
    ),
    "levey_2009_ckd_epi": Paper(
        "levey_2009_ckd_epi",
        "Levey 2009 (CKD-EPI)",
        2009, "egfr",
        "CKD-EPI equation for eGFR. Still the standard calculator.",
    ),
    "prati_2002": Paper(
        "prati_2002",
        "Prati 2002 (Ann Intern Med)",
        2002, "alt",
        "Updated ALT reference range — kept as origin.",
    ),
    "whitfield_2001": Paper(
        "whitfield_2001",
        "Whitfield 2001",
        2001, "ggt",
        "GGT reference and clinical significance review.",
    ),

    # ── CANCER SCREENING ────────────────────────────────────────────────
    "eau_2023": Paper(
        "eau_2023",
        "EAU 2023 prostate cancer",
        2023, "psa",
        "European Association of Urology prostate cancer guideline — "
        "current PSA-elevation workup.",
    ),
    "carter_2013": Paper(
        "carter_2013",
        "Carter 2013 (AUA early detection)",
        2013, "psa",
        "AUA early-detection guideline — secondary anchor for PSA.",
    ),

    # ── DAILY ENERGY / SLEEP / RECOVERY ────────────────────────────────
    "figueiro_2017": Paper(
        "figueiro_2017",
        "Figueiro 2017 (Sleep Health)",
        2017, "morning_light",
        "Morning bright light anchors cortisol awakening response — "
        "canonical for circadian advice.",
    ),
    "buijze_2016": Paper(
        "buijze_2016",
        "Buijze 2016 (PLoS ONE)",
        2016, "cold_exposure",
        "Cold-shower RCT (n=3018) — reduced sickness absence at work. "
        "Strongest controlled cold-exposure evidence available.",
    ),
    "shevchuk_2008": Paper(
        "shevchuk_2008",
        "Shevchuk 2008",
        2008, "cold_exposure",
        "Norepinephrine response to cold — kept as origin mechanism paper.",
    ),
    "avgerinos_2018": Paper(
        "avgerinos_2018",
        "Avgerinos 2018 (Exp Gerontol)",
        2018, "creatine_cognition",
        "Creatine supplementation and cognition systematic review.",
    ),
    "roschel_2021": Paper(
        "roschel_2021",
        "Roschel 2021 (Nutrients)",
        2021, "creatine_cognition",
        "Creatine for sleep-deprived cognition + recovery — current review.",
    ),
    "rae_2003": Paper(
        "rae_2003",
        "Rae 2003",
        2003, "creatine_cognition",
        "Original creatine and cognitive performance RCT. Origin reference.",
    ),

    # ── GENETICS / SPECIALTY (preserved from existing registry) ────────
    "aitken_2014": Paper(
        "aitken_2014",
        "Aitken 2014 (review)",
        2014, "sperm_ros",
        "ROS in male reproductive function review — anchor for "
        "leukocytospermia / oxidative pathway.",
    ),
    "tremellen_2008": Paper(
        "tremellen_2008",
        "Tremellen 2008 (Hum Reprod Update)",
        2008, "sperm_antioxidants",
        "Oxidative stress and male infertility — kept as background only; "
        "use Showell 2019 Cochrane as primary.",
    ),
    "menkveld_2001": Paper(
        "menkveld_2001",
        "Menkveld 2001",
        2001, "sperm_morphology",
        "Strict Kruger criteria for sperm morphology — kept as origin only.",
    ),

    # ── SPECIFIC INTERVENTION REFERENCES ─────────────────────────────────
    "wang_2019_varicocele": Paper(
        "wang_2019_varicocele",
        "Wang 2019 (Fertil Steril)",
        2019, "varicocele_dfi",
        "Varicocele repair and sperm DNA fragmentation outcomes meta-analysis.",
    ),
    "lenzi_2003": Paper(
        "lenzi_2003",
        "Lenzi 2003",
        2003, "sperm_carnitine",
        "Original L-Carnitine + Acetyl-L-Carnitine RCT in asthenozoospermia. "
        "Kept as origin alongside Mongioi 2016.",
    ),
}


def lookup(ref_id: str) -> str:
    """Return the canonical display string for ``ref_id``.

    Falls back to ``ref_id`` itself if the paper isn't registered — keeps the
    app from crashing on a typo, surfaces the missing key in the rendered
    output so it's easy to spot and add.
    """
    paper = PAPERS.get(ref_id)
    return paper.citation if paper else ref_id


def stats() -> Dict[str, int]:
    """Quick stats — used by verification tests."""
    from collections import Counter

    return {
        "total": len(PAPERS),
        **{f"era_{era}_{era+4}": n for era, n in sorted(
            Counter(p.year // 5 * 5 for p in PAPERS.values()).items()
        )},
    }
