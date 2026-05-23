"""Curated biomarker registry — canonical IDs, units, reference + optimal ranges,
and short citations to the highly-cited papers each marker is grounded in.

Three goals:
1. **Normalization** — every value extracted from a PDF lab report or spermogram
   is mapped to a canonical biomarker_id, regardless of the lab's free-form
   French/English/Italian label, so time-series across years/labs line up.
2. **Reference vs optimal** — labs report the wide population reference range;
   we additionally store an *optimal* range derived from the cited literature,
   which is what actually matters for fertility + energy optimization.
3. **Direction** — for trend arrows and recommendations: higher_better,
   lower_better, or mid_optimal (e.g. testosterone too low or too high is bad).

Citations are short author-year stubs intended to be expanded by the advisor
LLM when it cites a finding. Numbers reflect commonly-accepted ranges from
the cited works.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .bibliography import lookup as _cite


@dataclass
class Biomarker:
    id: str
    name_en: str
    name_fr: str
    unit: str
    category: str  # "semen" | "hormone" | "metabolic" | "lipid" | "hema" | "iron" | "liver_kidney" | "vitamin" | "thyroid" | "inflam" | "cancer"
    direction: str  # "higher_better" | "lower_better" | "mid_optimal"
    ref_low: Optional[float] = None
    ref_high: Optional[float] = None
    optimal_low: Optional[float] = None
    optimal_high: Optional[float] = None
    citations: List[str] = field(default_factory=list)
    aliases: List[str] = field(default_factory=list)
    short_label: Optional[str] = None  # for the dashboard chart axis


# ── 38 markers grounded in highly-cited 2015-2025 literature ──
# Citation IDs are resolved via app.sync.bibliography.lookup() so the
# bibliography is editable in one place.
REGISTRY: List[Biomarker] = [
    # ── 🧬 Semen analysis (WHO 2021 6th ed + Salas-Huetos 2017/2018 nutrition) ──
    Biomarker(
        id="semen_volume", name_en="Semen volume", name_fr="Volume séminal",
        unit="mL", category="semen", direction="higher_better",
        ref_low=1.4, optimal_low=2.0, optimal_high=6.0,
        citations=[_cite("who_2021")],
        aliases=["volume", "semen volume", "ejaculate volume", "volume séminal", "volume éjaculat"],
        short_label="Vol",
    ),
    Biomarker(
        id="sperm_concentration", name_en="Sperm concentration", name_fr="Concentration spermatique",
        unit="M/mL", category="semen", direction="higher_better",
        ref_low=16.0, optimal_low=40.0,
        citations=[_cite("who_2021"), _cite("salas_huetos_2018")],
        aliases=["concentration", "sperm concentration", "concentration spermatique",
                 "numération", "millions/mL"],
        short_label="Conc",
    ),
    Biomarker(
        id="sperm_total_count", name_en="Total sperm count", name_fr="Numération totale",
        unit="M", category="semen", direction="higher_better",
        ref_low=39.0, optimal_low=100.0,
        citations=[_cite("who_2021"), _cite("salas_huetos_2017")],
        aliases=["total count", "numération totale", "total sperm count", "tsc"],
        short_label="Total",
    ),
    Biomarker(
        id="sperm_progressive_motility", name_en="Progressive motility",
        name_fr="Mobilité progressive (a+b)",
        unit="%", category="semen", direction="higher_better",
        ref_low=30.0, optimal_low=50.0,
        citations=[_cite("who_2021"), _cite("showell_2019_cochrane")],
        aliases=["progressive motility", "mobilité progressive", "a+b", "pr motility",
                 "spermatozoïdes mobiles progressifs"],
        short_label="PR mot",
    ),
    Biomarker(
        id="sperm_total_motility", name_en="Total motility", name_fr="Mobilité totale (a+b+c)",
        unit="%", category="semen", direction="higher_better",
        ref_low=42.0, optimal_low=60.0,
        citations=[_cite("who_2021"), _cite("showell_2019_cochrane")],
        aliases=["total motility", "mobilité totale", "a+b+c", "motility"],
        short_label="Tot mot",
    ),
    Biomarker(
        id="sperm_normal_morphology", name_en="Normal morphology", name_fr="Formes typiques",
        unit="%", category="semen", direction="higher_better",
        ref_low=4.0, optimal_low=14.0,
        citations=[_cite("who_2021"), _cite("gatimel_2017")],
        aliases=["normal forms", "normal morphology", "morphology", "morphologie",
                 "formes typiques", "kruger"],
        short_label="Morph",
    ),
    Biomarker(
        id="sperm_dna_fragmentation", name_en="DNA fragmentation index",
        name_fr="Fragmentation ADN spermatique (DFI)",
        unit="%", category="semen", direction="lower_better",
        ref_high=15.0, optimal_high=10.0,
        citations=[_cite("esteves_2021"), _cite("agarwal_2020")],
        aliases=["dfi", "dna fragmentation", "fragmentation adn", "fragmentation index"],
        short_label="DFI",
    ),
    Biomarker(
        id="sperm_vitality", name_en="Vitality", name_fr="Vitalité",
        unit="%", category="semen", direction="higher_better",
        ref_low=54.0, optimal_low=70.0,
        citations=[_cite("who_2021")],
        aliases=["vitality", "vitalité", "live sperm"],
        short_label="Vit",
    ),
    Biomarker(
        id="sperm_leukocytes", name_en="Leukocytes in semen",
        name_fr="Leucocytes",
        unit="M/mL", category="semen", direction="lower_better",
        ref_high=1.0, optimal_high=0.5,
        citations=[_cite("who_2021"), _cite("aitken_2014")],
        aliases=[
            "leukocytes", "leucocytes", "white blood cells",
            "leukocytospermia", "wbc semen", "peroxidase positive",
            "round cells", "globules blancs",
        ],
        short_label="WBC",
    ),
    Biomarker(
        id="sperm_pH", name_en="Semen pH", name_fr="pH du sperme",
        unit="", category="semen", direction="mid_optimal",
        ref_low=7.2, ref_high=8.0, optimal_low=7.4, optimal_high=7.8,
        citations=[_cite("who_2021")],
        aliases=["ph", "semen ph", "ph sperme", "ph du sperme"],
        short_label="pH",
    ),
    Biomarker(
        id="sperm_mar_test", name_en="MAR test (antisperm antibodies)",
        name_fr="Test MAR (anticorps anti-spermatozoïdes)",
        unit="%", category="semen", direction="lower_better",
        ref_high=50.0, optimal_high=10.0,
        citations=[_cite("who_2021")],
        aliases=[
            "mar test", "mar igg", "mar iga",
            "antisperm antibody", "antisperm antibodies",
            "asab", "anticorps anti-spermatozoïdes",
            "anticorps antispermatozoides",
        ],
        short_label="MAR",
    ),

    # ── 🧪 Hormones (Travison 2017 + Bhasin 2018 Endocrine Society guideline) ──
    Biomarker(
        id="testosterone_total", name_en="Testosterone total", name_fr="Testostérone totale",
        unit="ng/dL", category="hormone", direction="mid_optimal",
        ref_low=264.0, ref_high=916.0, optimal_low=500.0, optimal_high=900.0,
        citations=[_cite("bhasin_2018"), _cite("travison_2017"), _cite("mulhall_2018")],
        aliases=["testosterone", "total testosterone", "testostérone totale", "t totale", "tt"],
        short_label="T tot",
    ),
    Biomarker(
        id="testosterone_free", name_en="Free testosterone", name_fr="Testostérone libre",
        unit="pg/mL", category="hormone", direction="mid_optimal",
        ref_low=4.6, ref_high=22.4, optimal_low=10.0, optimal_high=20.0,
        citations=[_cite("bhasin_2018")],
        aliases=["free testosterone", "testostérone libre", "free t", "ft"],
        short_label="Free T",
    ),
    Biomarker(
        id="shbg", name_en="SHBG", name_fr="SHBG",
        unit="nmol/L", category="hormone", direction="mid_optimal",
        ref_low=14.5, ref_high=48.4, optimal_low=20.0, optimal_high=40.0,
        citations=[_cite("goldman_2017"), _cite("liu_2007")],
        aliases=["shbg", "sex hormone binding globulin"],
        short_label="SHBG",
    ),
    Biomarker(
        id="lh", name_en="LH", name_fr="LH",
        unit="mIU/mL", category="hormone", direction="mid_optimal",
        ref_low=1.7, ref_high=8.6, optimal_low=2.0, optimal_high=6.0,
        citations=[_cite("mulhall_2018")],
        aliases=["lh", "luteinizing hormone", "hormone lutéinisante"],
        short_label="LH",
    ),
    Biomarker(
        id="fsh", name_en="FSH", name_fr="FSH",
        unit="mIU/mL", category="hormone", direction="mid_optimal",
        ref_low=1.5, ref_high=12.4, optimal_low=2.0, optimal_high=8.0,
        citations=[_cite("mulhall_2018")],
        aliases=["fsh", "follicle stimulating hormone"],
        short_label="FSH",
    ),
    Biomarker(
        id="estradiol", name_en="Estradiol", name_fr="Œstradiol",
        unit="pg/mL", category="hormone", direction="mid_optimal",
        ref_low=7.6, ref_high=42.6, optimal_low=15.0, optimal_high=30.0,
        citations=[_cite("finkelstein_2013")],
        aliases=["estradiol", "œstradiol", "oestradiol", "e2"],
        short_label="E2",
    ),
    Biomarker(
        id="prolactin", name_en="Prolactin", name_fr="Prolactine",
        unit="ng/mL", category="hormone", direction="mid_optimal",
        ref_low=4.0, ref_high=15.2, optimal_low=4.0, optimal_high=12.0,
        citations=[_cite("melmed_2011")],
        aliases=["prolactin", "prolactine", "prl"],
        short_label="PRL",
    ),

    # ── 🔥 Metabolic (ADA 2024 standards of care) ──
    Biomarker(
        id="glucose_fasting", name_en="Fasting glucose", name_fr="Glycémie à jeun",
        unit="mg/dL", category="metabolic", direction="lower_better",
        ref_low=70.0, ref_high=99.0, optimal_low=75.0, optimal_high=89.0,
        citations=[_cite("ada_2024")],
        aliases=["fasting glucose", "glucose", "glycémie", "glycémie à jeun"],
        short_label="Glc",
    ),
    Biomarker(
        id="hba1c", name_en="HbA1c", name_fr="HbA1c",
        unit="%", category="metabolic", direction="lower_better",
        ref_low=4.0, ref_high=5.6, optimal_high=5.3,
        citations=[_cite("ada_2024"), _cite("selvin_2010")],
        aliases=["hba1c", "hemoglobin a1c", "hémoglobine glyquée", "glycated hemoglobin"],
        short_label="HbA1c",
    ),
    Biomarker(
        id="insulin_fasting", name_en="Fasting insulin", name_fr="Insulinémie à jeun",
        unit="µIU/mL", category="metabolic", direction="lower_better",
        ref_low=2.0, ref_high=25.0, optimal_high=8.0,
        citations=[_cite("petersen_2018")],
        aliases=["insulin", "fasting insulin", "insulinémie", "insulin basal"],
        short_label="Ins",
    ),
    Biomarker(
        id="homa_ir", name_en="HOMA-IR", name_fr="HOMA-IR",
        unit="", category="metabolic", direction="lower_better",
        ref_high=2.5, optimal_high=1.5,
        citations=[_cite("matthews_1985"), _cite("petersen_2018")],
        aliases=["homa-ir", "homa", "indice homa"],
        short_label="HOMA",
    ),

    # ── ❤️ Lipids (ESC 2020 Mach + EAS consensus papers) ──
    Biomarker(
        id="ldl", name_en="LDL cholesterol", name_fr="LDL cholestérol",
        unit="mg/dL", category="lipid", direction="lower_better",
        ref_high=130.0, optimal_high=70.0,
        citations=[_cite("mach_2020_esc"), _cite("ference_2017_eas"), _cite("ctt_2019")],
        aliases=["ldl", "ldl-c", "ldl cholesterol", "ldl cholestérol", "low density"],
        short_label="LDL",
    ),
    Biomarker(
        id="hdl", name_en="HDL cholesterol", name_fr="HDL cholestérol",
        unit="mg/dL", category="lipid", direction="higher_better",
        ref_low=40.0, optimal_low=60.0,
        citations=[_cite("mach_2020_esc")],
        aliases=["hdl", "hdl-c", "hdl cholesterol", "hdl cholestérol"],
        short_label="HDL",
    ),
    Biomarker(
        id="triglycerides", name_en="Triglycerides", name_fr="Triglycérides",
        unit="mg/dL", category="lipid", direction="lower_better",
        ref_high=150.0, optimal_high=100.0,
        citations=[_cite("mach_2020_esc"), _cite("skulas_ray_2019_aha")],
        aliases=["triglycerides", "tg", "triglycérides"],
        short_label="TG",
    ),
    Biomarker(
        id="apob", name_en="ApoB", name_fr="Apolipoprotéine B",
        unit="mg/dL", category="lipid", direction="lower_better",
        ref_high=100.0, optimal_high=80.0,
        citations=[_cite("sniderman_2019"), _cite("mach_2020_esc")],
        aliases=["apob", "apo b", "apolipoprotein b", "apolipoprotéine b"],
        short_label="ApoB",
    ),
    Biomarker(
        id="lp_a", name_en="Lipoprotein(a)", name_fr="Lipoprotéine(a)",
        unit="nmol/L", category="lipid", direction="lower_better",
        ref_high=125.0, optimal_high=75.0,
        citations=[_cite("kronenberg_2022_eas"), _cite("tsimikas_2017")],
        aliases=["lp(a)", "lpa", "lipoprotein a", "lipoprotéine a"],
        short_label="Lp(a)",
    ),

    # ── 🔥 Inflammation (Ridker 2018 CANTOS — the modern lever) ──
    Biomarker(
        id="crp_hs", name_en="hs-CRP", name_fr="CRP ultra-sensible",
        unit="mg/L", category="inflam", direction="lower_better",
        ref_high=3.0, optimal_high=1.0,
        citations=[_cite("ridker_2018_cantos"), _cite("ridker_2003")],
        aliases=["hs-crp", "crpus", "crp ultra-sensible", "high sensitivity crp", "crp"],
        short_label="hsCRP",
    ),

    # ── 🩸 Hematology ──
    Biomarker(
        id="hemoglobin", name_en="Hemoglobin", name_fr="Hémoglobine",
        unit="g/dL", category="hema", direction="mid_optimal",
        ref_low=13.5, ref_high=17.5, optimal_low=14.0, optimal_high=16.5,
        citations=[_cite("who_anemia"), _cite("camaschella_2015")],
        aliases=["hemoglobin", "hgb", "hb", "hémoglobine"],
        short_label="Hgb",
    ),
    Biomarker(
        id="hematocrit", name_en="Hematocrit", name_fr="Hématocrite",
        unit="%", category="hema", direction="mid_optimal",
        ref_low=41.0, ref_high=53.0, optimal_low=42.0, optimal_high=50.0,
        citations=[_cite("who_anemia"), _cite("tefferi_2013")],
        aliases=["hematocrit", "hct", "hématocrite"],
        short_label="Hct",
    ),

    # ── 🩸 Iron ──
    Biomarker(
        id="ferritin", name_en="Ferritin", name_fr="Ferritine",
        unit="ng/mL", category="iron", direction="mid_optimal",
        ref_low=30.0, ref_high=400.0, optimal_low=80.0, optimal_high=200.0,
        citations=[_cite("who_iron_status"), _cite("camaschella_2015")],
        aliases=["ferritin", "ferritine"],
        short_label="Ferr",
    ),
    Biomarker(
        id="transferrin_saturation", name_en="Transferrin saturation",
        name_fr="Coefficient de saturation transferrine (CST)",
        unit="%", category="iron", direction="mid_optimal",
        ref_low=20.0, ref_high=50.0, optimal_low=25.0, optimal_high=45.0,
        citations=[_cite("camaschella_2015")],
        aliases=["transferrin saturation", "tsat", "cst", "saturation transferrine"],
        short_label="TSAT",
    ),

    # ── 🧪 Liver / kidney ──
    Biomarker(
        id="creatinine", name_en="Creatinine", name_fr="Créatinine",
        unit="mg/dL", category="liver_kidney", direction="mid_optimal",
        ref_low=0.74, ref_high=1.35,
        citations=[_cite("levey_2009_ckd_epi")],
        aliases=["creatinine", "créatinine", "creat"],
        short_label="Crea",
    ),
    Biomarker(
        id="egfr", name_en="eGFR", name_fr="DFG estimé",
        unit="mL/min/1.73m²", category="liver_kidney", direction="higher_better",
        ref_low=90.0, optimal_low=90.0,
        citations=[_cite("levey_2009_ckd_epi")],
        aliases=["egfr", "dfg", "estimated gfr", "dfge"],
        short_label="eGFR",
    ),
    Biomarker(
        id="alt", name_en="ALT", name_fr="ALAT",
        unit="U/L", category="liver_kidney", direction="lower_better",
        ref_high=40.0, optimal_high=25.0,
        citations=[_cite("prati_2002")],
        aliases=["alt", "alat", "sgpt"],
        short_label="ALT",
    ),
    Biomarker(
        id="ggt", name_en="GGT", name_fr="GGT",
        unit="U/L", category="liver_kidney", direction="lower_better",
        ref_high=55.0, optimal_high=25.0,
        citations=[_cite("whitfield_2001")],
        aliases=["ggt", "gamma-gt", "gamma gt"],
        short_label="GGT",
    ),

    # ── ☀️ Vitamins / minerals (Pludowski 2018, Bouillon 2019 vit D; Salas-Huetos 2019 Ω3) ──
    Biomarker(
        id="vitamin_d_25oh", name_en="Vitamin D 25-OH", name_fr="25-OH Vitamine D",
        unit="ng/mL", category="vitamin", direction="mid_optimal",
        ref_low=30.0, ref_high=100.0, optimal_low=40.0, optimal_high=60.0,
        citations=[_cite("pludowski_2018"), _cite("bouillon_2019"), _cite("holick_2011_endo_soc")],
        aliases=["25-oh vitamin d", "25(oh)d", "vitamin d", "vitamine d", "calcidiol"],
        short_label="Vit D",
    ),
    Biomarker(
        id="vitamin_b12", name_en="Vitamin B12", name_fr="Vitamine B12",
        unit="pg/mL", category="vitamin", direction="mid_optimal",
        ref_low=200.0, ref_high=900.0, optimal_low=500.0, optimal_high=900.0,
        citations=[_cite("smith_2018")],
        aliases=["vitamin b12", "vitamine b12", "b12", "cobalamin"],
        short_label="B12",
    ),
    Biomarker(
        id="folate", name_en="Folate (B9)", name_fr="Folates (B9)",
        unit="ng/mL", category="vitamin", direction="higher_better",
        ref_low=5.4, optimal_low=10.0,
        citations=[_cite("smith_2018")],
        aliases=["folate", "folates", "folic acid", "b9"],
        short_label="B9",
    ),
    Biomarker(
        id="homocysteine", name_en="Homocysteine", name_fr="Homocystéine",
        unit="µmol/L", category="vitamin", direction="lower_better",
        ref_high=13.0, optimal_high=8.0,
        citations=[_cite("smith_2018")],
        aliases=["homocysteine", "homocystéine"],
        short_label="Hcy",
    ),
    Biomarker(
        id="zinc", name_en="Zinc", name_fr="Zinc",
        unit="µg/dL", category="vitamin", direction="mid_optimal",
        ref_low=70.0, ref_high=120.0, optimal_low=90.0, optimal_high=110.0,
        citations=[_cite("wessells_2012"), _cite("salas_huetos_2018")],
        aliases=["zinc", "zn"],
        short_label="Zn",
    ),
    Biomarker(
        id="omega3_index", name_en="Omega-3 index (RBC)", name_fr="Indice Oméga-3",
        unit="%", category="vitamin", direction="higher_better",
        ref_low=4.0, optimal_low=8.0,
        citations=[_cite("harris_2017"), _cite("salas_huetos_2019")],
        aliases=["omega-3 index", "omega 3 index", "indice oméga-3", "epa+dha%"],
        short_label="Ω3",
    ),

    # ── 🩺 Thyroid (ATA 2014 management guideline) ──
    Biomarker(
        id="tsh", name_en="TSH", name_fr="TSH",
        unit="µIU/mL", category="thyroid", direction="mid_optimal",
        ref_low=0.4, ref_high=4.0, optimal_low=0.5, optimal_high=2.5,
        citations=[_cite("ata_2014")],
        aliases=["tsh", "thyréostimuline"],
        short_label="TSH",
    ),
    Biomarker(
        id="ft4", name_en="Free T4", name_fr="T4 libre (FT4)",
        unit="ng/dL", category="thyroid", direction="mid_optimal",
        ref_low=0.8, ref_high=1.8, optimal_low=1.0, optimal_high=1.5,
        citations=[_cite("ata_2014")],
        aliases=["ft4", "t4 libre", "free t4"],
        short_label="FT4",
    ),

    # ── 🎯 Cancer (EAU 2023 prostate guideline) ──
    Biomarker(
        id="psa_total", name_en="PSA total", name_fr="PSA total",
        unit="ng/mL", category="cancer", direction="lower_better",
        ref_high=4.0, optimal_high=2.5,
        citations=[_cite("eau_2023"), _cite("carter_2013")],
        aliases=["psa", "psa total", "prostate specific antigen"],
        short_label="PSA",
    ),
]


# Lookup by canonical id
BY_ID: Dict[str, Biomarker] = {b.id: b for b in REGISTRY}

# Reverse-lookup by alias (lowercased) for free-form text matching
ALIAS_INDEX: Dict[str, str] = {}
for _b in REGISTRY:
    for _name in [_b.id, _b.name_en, _b.name_fr] + _b.aliases:
        ALIAS_INDEX[_name.strip().lower()] = _b.id


def find_by_alias(label: str) -> Optional[Biomarker]:
    """Match a free-form label (e.g. 'Testostérone totale') to a canonical biomarker.

    Tries exact lowercase match first, then substring match. Returns None if no
    confident match.
    """
    if not label:
        return None
    key = label.strip().lower().rstrip(":")
    if key in ALIAS_INDEX:
        return BY_ID[ALIAS_INDEX[key]]
    # Substring fallback — pick the longest matching alias to avoid false positives
    best: Tuple[int, Optional[str]] = (0, None)
    for alias, bid in ALIAS_INDEX.items():
        if len(alias) >= 4 and alias in key:
            if len(alias) > best[0]:
                best = (len(alias), bid)
    return BY_ID[best[1]] if best[1] else None


def in_optimal(marker: Biomarker, value: float) -> str:
    """Return 'optimal' | 'in_range' | 'out_of_range' | 'unknown' for a value."""
    o_lo, o_hi = marker.optimal_low, marker.optimal_high
    r_lo, r_hi = marker.ref_low, marker.ref_high

    def _ok(lo: Optional[float], hi: Optional[float], v: float) -> bool:
        if lo is not None and v < lo:
            return False
        if hi is not None and v > hi:
            return False
        return True

    if (o_lo is not None or o_hi is not None) and _ok(o_lo, o_hi, value):
        return "optimal"
    if (r_lo is not None or r_hi is not None) and _ok(r_lo, r_hi, value):
        return "in_range"
    if r_lo is None and r_hi is None and o_lo is None and o_hi is None:
        return "unknown"
    return "out_of_range"


def all_categories() -> List[str]:
    return sorted({b.category for b in REGISTRY})


def to_dict(marker: Biomarker) -> Dict[str, Any]:
    return asdict(marker)
