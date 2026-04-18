"""Genetic analysis: cross-reference raw VCF against clinically-actionable SNP catalog.

Input: raw VCF file at data/genetics/yb1153_chip.vcf (~635k SNPs).
Output: data/genetics/genetic_summary.json with per-SNP genotype + interpretation,
organized by category (pharmacogenomics, cardiometabolic, fertility, cancer, etc.).

The catalog below is curated from current-evidence sources (2023-2026):
- PharmGKB CPIC Level A/B guidelines
- ClinVar pathogenic/likely-pathogenic consensus calls
- ACMG 73-gene actionable list
- GWAS Catalog top-hits for common diseases (fertility focus given the user's goals)
- SNPedia community-validated entries

Each entry has: rsid, gene, category, risk_allele, interpretation_per_genotype,
evidence_level, source_urls. The consumer parses the VCF once, joins on rsid,
and emits a JSON summary keyed by category.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("personal-doctor.genetic_analysis")


# ─── CURATED CLINICALLY-ACTIONABLE SNP CATALOG ──────────────────────────
# Genotype interpretations use the convention:
#   "0/0" = homozygous reference (ref/ref)
#   "0/1" = heterozygous (ref/alt)
#   "1/1" = homozygous alt (alt/alt)
# The "risk_genotype" field indicates which genotype carries the risk/effect.

CATALOG: List[Dict[str, Any]] = [
    # ── PHARMACOGENOMICS (CPIC Level A — actionable drug-gene pairs) ──
    {
        "rsid": "rs4244285",
        "gene": "CYP2C19",
        "variant": "*2",
        "category": "pharmacogenomics",
        "disease": "Drug metabolism (clopidogrel, PPIs, SSRIs)",
        "ref": "G", "alt": "A",
        "interpretation": {
            "0/0": "Normal CYP2C19 activity (*1/*1) — standard dosing for clopidogrel, PPIs, citalopram, sertraline.",
            "0/1": "Intermediate metabolizer (*1/*2) — clopidogrel: consider prasugrel/ticagrelor. PPIs: may need higher dose for H. pylori eradication.",
            "1/1": "Poor metabolizer (*2/*2) — clopidogrel: DO NOT USE, switch to alternative antiplatelet. SSRIs: reduce starting dose."
        },
        "evidence": "CPIC Level A",
        "sources": ["https://cpicpgx.org/genes/#CYP2C19", "https://www.pharmgkb.org/gene/PA124"]
    },
    {
        "rsid": "rs12248560",
        "gene": "CYP2C19",
        "variant": "*17",
        "category": "pharmacogenomics",
        "disease": "Drug metabolism (ultra-rapid)",
        "ref": "C", "alt": "T",
        "interpretation": {
            "0/0": "Normal metabolizer",
            "0/1": "Rapid metabolizer (*1/*17) — may reduce efficacy of PPIs, citalopram, escitalopram.",
            "1/1": "Ultra-rapid metabolizer (*17/*17) — PPIs less effective; increased bleeding risk on clopidogrel."
        },
        "evidence": "CPIC Level A",
        "sources": ["https://cpicpgx.org/genes/#CYP2C19"]
    },
    {
        "rsid": "rs1799853",
        "gene": "CYP2C9",
        "variant": "*2",
        "category": "pharmacogenomics",
        "disease": "Warfarin, NSAIDs",
        "ref": "C", "alt": "T",
        "interpretation": {
            "0/0": "Normal CYP2C9 activity",
            "0/1": "Intermediate metabolizer (*1/*2) — reduce warfarin dose 20-30%, increased bleeding risk on NSAIDs",
            "1/1": "Poor metabolizer (*2/*2) — reduce warfarin dose 40-60%, caution on NSAIDs"
        },
        "evidence": "CPIC Level A",
        "sources": ["https://cpicpgx.org/genes/#CYP2C9"]
    },
    {
        "rsid": "rs1057910",
        "gene": "CYP2C9",
        "variant": "*3",
        "category": "pharmacogenomics",
        "disease": "Warfarin (strong effect)",
        "ref": "A", "alt": "C",
        "interpretation": {
            "0/0": "Normal",
            "0/1": "Intermediate (*1/*3) — reduce warfarin ~40%",
            "1/1": "Poor metabolizer (*3/*3) — reduce warfarin ~70%, consider alternative"
        },
        "evidence": "CPIC Level A",
        "sources": ["https://cpicpgx.org/genes/#CYP2C9"]
    },
    {
        "rsid": "rs9923231",
        "gene": "VKORC1",
        "category": "pharmacogenomics",
        "disease": "Warfarin sensitivity",
        "ref": "C", "alt": "T",
        "interpretation": {
            "0/0": "Low warfarin sensitivity — standard dose",
            "0/1": "Intermediate sensitivity — ~30% lower dose than average",
            "1/1": "High warfarin sensitivity — ~50% lower starting dose needed"
        },
        "evidence": "CPIC Level A",
        "sources": ["https://cpicpgx.org/genes/#VKORC1"]
    },
    {
        "rsid": "rs4149056",
        "gene": "SLCO1B1",
        "variant": "*5",
        "category": "pharmacogenomics",
        "disease": "Statin myopathy",
        "ref": "T", "alt": "C",
        "interpretation": {
            "0/0": "Normal function — standard simvastatin/atorvastatin dosing",
            "0/1": "Intermediate — elevated myopathy risk on simvastatin 80mg; prefer rosuvastatin or pravastatin",
            "1/1": "Low function — avoid simvastatin; switch to rosuvastatin/pravastatin"
        },
        "evidence": "CPIC Level A",
        "sources": ["https://cpicpgx.org/guideline/simvastatin/"]
    },
    {
        "rsid": "rs116855232",
        "gene": "NUDT15",
        "category": "pharmacogenomics",
        "disease": "Thiopurine toxicity (azathioprine, 6-MP)",
        "ref": "C", "alt": "T",
        "interpretation": {
            "0/0": "Normal function",
            "0/1": "Intermediate — reduce thiopurine dose ~50%",
            "1/1": "Poor metabolizer — severely reduce thiopurine or avoid"
        },
        "evidence": "CPIC Level A",
        "sources": ["https://cpicpgx.org/genes/#NUDT15"]
    },
    {
        "rsid": "rs1800462",
        "gene": "TPMT",
        "variant": "*2",
        "category": "pharmacogenomics",
        "disease": "Thiopurine toxicity",
        "ref": "C", "alt": "G",
        "interpretation": {
            "0/0": "Normal TPMT activity",
            "0/1": "Intermediate — thiopurine 30-70% of standard dose",
            "1/1": "Poor — avoid thiopurines or reduce by 90%"
        },
        "evidence": "CPIC Level A",
        "sources": ["https://cpicpgx.org/genes/#TPMT"]
    },
    {
        "rsid": "rs1800460",
        "gene": "TPMT",
        "variant": "*3B",
        "category": "pharmacogenomics",
        "disease": "Thiopurine toxicity",
        "ref": "C", "alt": "T",
        "interpretation": {
            "0/0": "Normal",
            "0/1": "Intermediate TPMT activity",
            "1/1": "Poor TPMT — avoid thiopurines"
        },
        "evidence": "CPIC Level A",
        "sources": ["https://cpicpgx.org/genes/#TPMT"]
    },
    {
        "rsid": "rs1142345",
        "gene": "TPMT",
        "variant": "*3C",
        "category": "pharmacogenomics",
        "disease": "Thiopurine toxicity",
        "ref": "T", "alt": "C",
        "interpretation": {
            "0/0": "Normal",
            "0/1": "Intermediate",
            "1/1": "Poor TPMT — avoid thiopurines"
        },
        "evidence": "CPIC Level A",
        "sources": ["https://cpicpgx.org/genes/#TPMT"]
    },
    {
        "rsid": "rs3892097",
        "gene": "CYP2D6",
        "variant": "*4",
        "category": "pharmacogenomics",
        "disease": "Codeine, tramadol, tamoxifen, many antidepressants",
        "ref": "G", "alt": "A",
        "interpretation": {
            "0/0": "Likely normal metabolizer",
            "0/1": "Intermediate — reduced conversion of codeine to morphine; tamoxifen efficacy reduced",
            "1/1": "Poor metabolizer (*4/*4) — codeine ineffective, tamoxifen may be less effective, many antidepressants need dose adjustment"
        },
        "evidence": "CPIC Level A",
        "sources": ["https://cpicpgx.org/genes/#CYP2D6"]
    },
    {
        "rsid": "rs3918290",
        "gene": "DPYD",
        "variant": "*2A",
        "category": "pharmacogenomics",
        "disease": "5-FU, capecitabine toxicity (chemo)",
        "ref": "C", "alt": "T",
        "interpretation": {
            "0/0": "Normal DPD activity",
            "0/1": "Partial DPD deficiency — reduce 5-FU/capecitabine 50%",
            "1/1": "Complete DPD deficiency — AVOID 5-FU/capecitabine, fatal toxicity risk"
        },
        "evidence": "CPIC Level A",
        "sources": ["https://cpicpgx.org/guideline/5-fluorouracil-and-capecitabine/"]
    },

    # ── CARDIOVASCULAR / METABOLIC ──
    {
        "rsid": "rs7412",
        "gene": "APOE",
        "category": "cardiovascular",
        "disease": "Cholesterol, Alzheimer's risk",
        "ref": "C", "alt": "T",
        "interpretation": {
            "0/0": "No APOE*2 allele at this position",
            "0/1": "Carries one *2 allele — lower LDL, lower Alzheimer's risk",
            "1/1": "Two *2 alleles — lowest Alzheimer's risk, but rare type III hyperlipoproteinemia risk if ε2/ε2"
        },
        "evidence": "GWAS consensus",
        "sources": ["https://www.ncbi.nlm.nih.gov/clinvar/variation/441246/"]
    },
    {
        "rsid": "rs429358",
        "gene": "APOE",
        "category": "cardiovascular",
        "disease": "Alzheimer's disease risk",
        "ref": "T", "alt": "C",
        "interpretation": {
            "0/0": "No APOE*4 allele — baseline Alzheimer's risk",
            "0/1": "One *4 allele — ~3x Alzheimer's risk, earlier onset",
            "1/1": "Two *4 alleles — ~12x Alzheimer's risk; strongly consider lifestyle + cognitive exercise + cardiometabolic optimization"
        },
        "evidence": "GWAS well-established (Corder 1993, multiple meta-analyses)",
        "sources": ["https://www.ncbi.nlm.nih.gov/clinvar/variation/17849/"]
    },
    {
        "rsid": "rs6025",
        "gene": "F5",
        "variant": "Factor V Leiden",
        "category": "cardiovascular",
        "disease": "Venous thromboembolism",
        "ref": "C", "alt": "T",
        "interpretation": {
            "0/0": "No Factor V Leiden — baseline VTE risk",
            "0/1": "Heterozygous — ~5-8x DVT/PE risk; avoid combined oral contraceptives; hydrate on long flights",
            "1/1": "Homozygous — ~80x VTE risk; lifelong movement + hydration vigilance; consider anticoagulation planning for surgery/immobility"
        },
        "evidence": "ACMG pharmacogenomic panel",
        "sources": ["https://www.ncbi.nlm.nih.gov/clinvar/variation/642/"]
    },
    {
        "rsid": "rs1799963",
        "gene": "F2",
        "variant": "Prothrombin G20210A",
        "category": "cardiovascular",
        "disease": "Venous thromboembolism",
        "ref": "G", "alt": "A",
        "interpretation": {
            "0/0": "No prothrombin variant",
            "0/1": "Heterozygous G20210A — ~2-3x VTE risk, synergistic with FVL",
            "1/1": "Homozygous — higher VTE risk, consider prophylaxis in high-risk settings"
        },
        "evidence": "Well-established",
        "sources": ["https://www.ncbi.nlm.nih.gov/clinvar/variation/13310/"]
    },
    {
        "rsid": "rs5918",
        "gene": "ITGB3",
        "variant": "PlA2",
        "category": "cardiovascular",
        "disease": "Platelet aggregation, MI risk",
        "ref": "T", "alt": "C",
        "interpretation": {
            "0/0": "Common genotype",
            "0/1": "Heterozygous — modest ~1.5x MI risk increase",
            "1/1": "Homozygous PlA2 — increased MI risk, especially in young patients"
        },
        "evidence": "Meta-analyses",
        "sources": []
    },
    {
        "rsid": "rs1333049",
        "gene": "CDKN2B-AS1 (9p21)",
        "category": "cardiovascular",
        "disease": "Coronary artery disease",
        "ref": "G", "alt": "C",
        "interpretation": {
            "0/0": "Baseline CAD risk",
            "0/1": "~1.25x CAD risk",
            "1/1": "~1.6x CAD risk — prioritize lifelong lipid + BP control"
        },
        "evidence": "GWAS well-replicated",
        "sources": ["https://www.ebi.ac.uk/gwas/variants/rs1333049"]
    },
    {
        "rsid": "rs10757274",
        "gene": "CDKN2B-AS1 (9p21)",
        "category": "cardiovascular",
        "disease": "Coronary artery disease",
        "ref": "A", "alt": "G",
        "interpretation": {
            "0/0": "Baseline",
            "0/1": "Modest CAD risk",
            "1/1": "~1.5x CAD risk"
        },
        "evidence": "GWAS",
        "sources": []
    },
    {
        "rsid": "rs1801133",
        "gene": "MTHFR",
        "variant": "C677T",
        "category": "metabolic",
        "disease": "Folate metabolism, homocysteine",
        "ref": "G", "alt": "A",
        "interpretation": {
            "0/0": "Normal MTHFR activity",
            "0/1": "Heterozygous — ~35% reduced enzyme activity; prefer methylfolate over folic acid, monitor homocysteine",
            "1/1": "Homozygous (TT) — ~70% reduced activity; elevated homocysteine risk; use methylfolate (L-5-MTHF) 400-800 mcg/day, B12, B6; avoid high folic acid supplements"
        },
        "evidence": "Well-established nutrigenomic",
        "sources": ["https://www.ncbi.nlm.nih.gov/clinvar/variation/3520/"]
    },
    {
        "rsid": "rs1801131",
        "gene": "MTHFR",
        "variant": "A1298C",
        "category": "metabolic",
        "disease": "Folate metabolism (milder)",
        "ref": "T", "alt": "G",
        "interpretation": {
            "0/0": "Normal",
            "0/1": "Heterozygous — ~15% reduced activity",
            "1/1": "Homozygous — ~30% reduced; methylfolate preferred"
        },
        "evidence": "Established",
        "sources": []
    },
    {
        "rsid": "rs1800562",
        "gene": "HFE",
        "variant": "C282Y",
        "category": "metabolic",
        "disease": "Hereditary hemochromatosis",
        "ref": "G", "alt": "A",
        "interpretation": {
            "0/0": "No C282Y — no HH risk from this variant",
            "0/1": "Carrier — usually asymptomatic, monitor ferritin if iron supplementing",
            "1/1": "Homozygous — ~60-80% risk of iron overload; periodic ferritin + transferrin saturation, avoid iron supplements unless deficient, limit red meat if iron elevated"
        },
        "evidence": "Well-established, ACMG actionable",
        "sources": ["https://www.ncbi.nlm.nih.gov/clinvar/variation/9/"]
    },
    {
        "rsid": "rs1799945",
        "gene": "HFE",
        "variant": "H63D",
        "category": "metabolic",
        "disease": "Hereditary hemochromatosis (milder)",
        "ref": "C", "alt": "G",
        "interpretation": {
            "0/0": "No H63D",
            "0/1": "Carrier — minimal risk alone, compound with C282Y increases risk",
            "1/1": "Homozygous H63D — mild iron overload possible; monitor ferritin"
        },
        "evidence": "Established",
        "sources": []
    },

    # ── METABOLIC / WEIGHT / DIABETES ──
    {
        "rsid": "rs9939609",
        "gene": "FTO",
        "category": "metabolic",
        "disease": "Obesity, type 2 diabetes",
        "ref": "T", "alt": "A",
        "interpretation": {
            "0/0": "Baseline weight/obesity risk",
            "0/1": "~1.3x obesity risk — emphasize protein/fiber to control satiety, strength training",
            "1/1": "~1.67x obesity risk — protein-forward diet, resistance training offsets most of the effect (Kilpelainen 2012)"
        },
        "evidence": "Most-replicated obesity GWAS",
        "sources": ["https://www.ebi.ac.uk/gwas/variants/rs9939609"]
    },
    {
        "rsid": "rs7903146",
        "gene": "TCF7L2",
        "category": "metabolic",
        "disease": "Type 2 diabetes",
        "ref": "C", "alt": "T",
        "interpretation": {
            "0/0": "Baseline T2D risk",
            "0/1": "~1.4x T2D risk — low-glycemic diet, resistance training, regular fasting glucose",
            "1/1": "~2x T2D risk — strict low-carb pattern + strength training + 5+ hrs/wk exercise dramatically reduces penetrance"
        },
        "evidence": "Strongest non-HLA diabetes GWAS",
        "sources": ["https://www.ebi.ac.uk/gwas/variants/rs7903146"]
    },
    {
        "rsid": "rs7754840",
        "gene": "CDKAL1",
        "category": "metabolic",
        "disease": "Type 2 diabetes, insulin secretion",
        "ref": "C", "alt": "G",
        "interpretation": {
            "0/0": "Baseline",
            "0/1": "~1.1x T2D risk",
            "1/1": "~1.25x T2D risk"
        },
        "evidence": "GWAS",
        "sources": []
    },

    # ── LACTOSE, CAFFEINE, ALCOHOL ──
    {
        "rsid": "rs4988235",
        "gene": "MCM6 / LCT",
        "category": "diet",
        "disease": "Lactose tolerance",
        "ref": "G", "alt": "A",
        "interpretation": {
            "0/0": "Adult lactose INTOLERANT (European mutation absent) — avoid milk or use lactase",
            "0/1": "Lactose tolerant (one copy sufficient)",
            "1/1": "Lactose tolerant"
        },
        "evidence": "Well-established",
        "sources": []
    },
    {
        "rsid": "rs762551",
        "gene": "CYP1A2",
        "category": "diet",
        "disease": "Caffeine metabolism",
        "ref": "A", "alt": "C",
        "interpretation": {
            "0/0": "Fast caffeine metabolizer (*1A/*1A) — caffeine CV benefits likely preserved",
            "0/1": "Intermediate (*1A/*1F)",
            "1/1": "Slow caffeine metabolizer (*1F/*1F) — >2 cups/day associated with increased MI risk (Cornelis 2006); limit afternoon caffeine for sleep"
        },
        "evidence": "Well-established",
        "sources": []
    },
    {
        "rsid": "rs671",
        "gene": "ALDH2",
        "category": "diet",
        "disease": "Alcohol metabolism (Asian flush)",
        "ref": "G", "alt": "A",
        "interpretation": {
            "0/0": "Normal ALDH2 — normal alcohol tolerance",
            "0/1": "Heterozygous — ~50% ALDH2 activity; increased esophageal cancer risk from alcohol, flushing",
            "1/1": "Homozygous — near-zero ALDH2; avoid alcohol entirely (strong cancer risk)"
        },
        "evidence": "Very strong in East Asian populations",
        "sources": []
    },

    # ── FERTILITY / REPRODUCTIVE (user's top goal) ──
    {
        "rsid": "rs6166",
        "gene": "FSHR",
        "category": "fertility",
        "disease": "FSH receptor sensitivity (Asn680Ser)",
        "ref": "A", "alt": "G",
        "interpretation": {
            "0/0": "Asn/Asn — higher sensitivity to FSH; usually normal response",
            "0/1": "Asn/Ser — intermediate FSH sensitivity",
            "1/1": "Ser/Ser — reduced FSHR sensitivity; if fertility workup needed, higher FSH doses may be required"
        },
        "evidence": "Meta-analyses in fertility contexts",
        "sources": []
    },
    {
        "rsid": "rs6165",
        "gene": "FSHR",
        "category": "fertility",
        "disease": "FSH receptor (Thr307Ala)",
        "ref": "A", "alt": "G",
        "interpretation": {
            "0/0": "Thr/Thr",
            "0/1": "Thr/Ala",
            "1/1": "Ala/Ala — reduced FSHR activity, often linked with rs6166"
        },
        "evidence": "Multiple fertility studies",
        "sources": []
    },
    {
        "rsid": "rs10835638",
        "gene": "FSHB",
        "category": "fertility",
        "disease": "FSH beta expression (serum FSH)",
        "ref": "G", "alt": "T",
        "interpretation": {
            "0/0": "Higher FSHB expression, higher serum FSH",
            "0/1": "Intermediate",
            "1/1": "Lower serum FSH — linked with lower sperm count in some studies"
        },
        "evidence": "GWAS",
        "sources": []
    },

    # ── CLOCK / SLEEP / CIRCADIAN ──
    {
        "rsid": "rs73598374",
        "gene": "ADA",
        "category": "sleep",
        "disease": "Sleep depth / deep-sleep %",
        "ref": "G", "alt": "A",
        "interpretation": {
            "0/0": "Average deep sleep",
            "0/1": "Carrier — longer, deeper slow-wave sleep reported (Retey 2005)",
            "1/1": "Deeper sleep / higher SWS"
        },
        "evidence": "Replicated",
        "sources": []
    },
    {
        "rsid": "rs1801260",
        "gene": "CLOCK",
        "category": "sleep",
        "disease": "Chronotype (evening preference)",
        "ref": "A", "alt": "G",
        "interpretation": {
            "0/0": "Morning-leaning chronotype",
            "0/1": "Intermediate",
            "1/1": "Evening chronotype — align sleep window later if possible"
        },
        "evidence": "Multiple studies",
        "sources": []
    },
    {
        "rsid": "rs12413112",
        "gene": "MTNR1B",
        "category": "sleep",
        "disease": "Fasting glucose, diabetes (circadian)",
        "ref": "G", "alt": "A",
        "interpretation": {
            "0/0": "Baseline",
            "0/1": "Slightly elevated fasting glucose",
            "1/1": "Higher fasting glucose — avoid late-night carbs (melatonin suppresses insulin)"
        },
        "evidence": "GWAS",
        "sources": []
    },

    # ── STRESS / COMT / DOPAMINE ──
    {
        "rsid": "rs4680",
        "gene": "COMT",
        "variant": "Val158Met",
        "category": "stress_cognition",
        "disease": "Catecholamine metabolism, stress reactivity",
        "ref": "G", "alt": "A",
        "interpretation": {
            "0/0": "Val/Val (\"Warrior\") — faster dopamine clearance; better under stress, less in calm/abstract tasks; caffeine tolerated well",
            "0/1": "Val/Met — balanced",
            "1/1": "Met/Met (\"Worrier\") — slower dopamine clearance; higher baseline PFC dopamine, better in calm focused work, more anxiety under stress; go easy on caffeine late in day"
        },
        "evidence": "Very well-studied",
        "sources": ["https://www.snpedia.com/index.php/Rs4680"]
    },
    {
        "rsid": "rs6265",
        "gene": "BDNF",
        "variant": "Val66Met",
        "category": "stress_cognition",
        "disease": "Neuroplasticity, depression risk",
        "ref": "C", "alt": "T",
        "interpretation": {
            "0/0": "Val/Val — baseline BDNF secretion",
            "0/1": "Val/Met — reduced activity-dependent BDNF secretion; exercise especially beneficial",
            "1/1": "Met/Met — further reduced BDNF; aerobic exercise + learning novel skills particularly important"
        },
        "evidence": "Well-studied",
        "sources": []
    },

    # ── OXIDATIVE STRESS / ANTIOXIDANTS ──
    {
        "rsid": "rs4880",
        "gene": "SOD2",
        "category": "antioxidant",
        "disease": "Mitochondrial antioxidant capacity",
        "ref": "T", "alt": "C",
        "interpretation": {
            "0/0": "Val/Val — less efficient mitochondrial targeting",
            "0/1": "Val/Ala — intermediate",
            "1/1": "Ala/Ala — best mitochondrial targeting; higher efficiency"
        },
        "evidence": "Well-studied",
        "sources": []
    },
    {
        "rsid": "rs1050450",
        "gene": "GPX1",
        "category": "antioxidant",
        "disease": "Selenium-dependent glutathione peroxidase",
        "ref": "C", "alt": "T",
        "interpretation": {
            "0/0": "Pro/Pro — full activity",
            "0/1": "Pro/Leu — reduced activity; selenium-rich diet beneficial",
            "1/1": "Leu/Leu — reduced activity; selenium 100-200 mcg/day may help"
        },
        "evidence": "Established",
        "sources": []
    },

    # ── VITAMIN D / BONE ──
    {
        "rsid": "rs2282679",
        "gene": "GC",
        "category": "nutrient",
        "disease": "Vitamin D binding protein (serum 25(OH)D)",
        "ref": "A", "alt": "C",
        "interpretation": {
            "0/0": "Higher 25(OH)D",
            "0/1": "Intermediate",
            "1/1": "Lower 25(OH)D at baseline — monitor serum vitamin D, may need higher supplementation"
        },
        "evidence": "GWAS-replicated",
        "sources": []
    },
    {
        "rsid": "rs10741657",
        "gene": "CYP2R1",
        "category": "nutrient",
        "disease": "Vitamin D 25-hydroxylation",
        "ref": "G", "alt": "A",
        "interpretation": {
            "0/0": "Higher 25(OH)D",
            "0/1": "Intermediate",
            "1/1": "Lower — higher D3 dose may be needed"
        },
        "evidence": "GWAS",
        "sources": []
    },

    # ── AMPD1 / EXERCISE (user-specific) ──
    {
        "rsid": "rs17602729",
        "gene": "AMPD1",
        "variant": "c.34C>T / Q12X",
        "category": "exercise",
        "disease": "Exercise-induced myopathy / fatigue",
        "ref": "C", "alt": "T",
        "interpretation": {
            "0/0": "Normal AMPD1 — full muscle adenosine deaminase activity",
            "0/1": "Carrier — partial deficiency; more post-exercise fatigue; creatine monohydrate 3-5 g/day and adequate recovery especially helpful",
            "1/1": "Homozygous deficiency — marked exercise intolerance; creatine strongly indicated; longer recovery windows; avoid back-to-back intense sessions"
        },
        "evidence": "Well-documented (Morisaki 1992, multiple studies)",
        "sources": ["https://www.ncbi.nlm.nih.gov/clinvar/variation/1866/"]
    },

    # ── PSA / PROSTATE ──
    {
        "rsid": "rs10993994",
        "gene": "MSMB",
        "category": "prostate",
        "disease": "Prostate cancer, PSA level",
        "ref": "C", "alt": "T",
        "interpretation": {
            "0/0": "Baseline prostate risk",
            "0/1": "~1.25x prostate cancer risk",
            "1/1": "~1.6x prostate cancer risk — consider earlier / more frequent PSA monitoring"
        },
        "evidence": "GWAS",
        "sources": []
    },

    # ── ANTI-COAGULATION / MI related extras ──
    {
        "rsid": "rs1801282",
        "gene": "PPARG",
        "variant": "Pro12Ala",
        "category": "metabolic",
        "disease": "Insulin sensitivity, T2D (protective)",
        "ref": "C", "alt": "G",
        "interpretation": {
            "0/0": "Baseline",
            "0/1": "Ala carrier — ~15-25% reduced T2D risk (protective)",
            "1/1": "Rare — further protection"
        },
        "evidence": "Meta-analyses",
        "sources": []
    },

    # ── INFLAMMATION ──
    {
        "rsid": "rs1800629",
        "gene": "TNF",
        "category": "inflammation",
        "disease": "Pro-inflammatory cytokine",
        "ref": "G", "alt": "A",
        "interpretation": {
            "0/0": "Baseline TNF-α",
            "0/1": "Higher TNF-α production",
            "1/1": "Highest TNF-α — anti-inflammatory diet (omega-3, polyphenols) may help"
        },
        "evidence": "Multiple studies",
        "sources": []
    },
    {
        "rsid": "rs1800795",
        "gene": "IL6",
        "category": "inflammation",
        "disease": "IL-6 expression",
        "ref": "G", "alt": "C",
        "interpretation": {
            "0/0": "Baseline IL-6",
            "0/1": "Reduced IL-6 (protective for inflammation)",
            "1/1": "Lowest IL-6"
        },
        "evidence": "Multiple studies",
        "sources": []
    },
]


# ─── VCF PARSER ──────────────────────────────────────────────────────────
def parse_vcf(vcf_path: Path, rsid_set: set[str]) -> Dict[str, Dict[str, str]]:
    """One-pass scan of a VCF, returning {rsid: {chrom, pos, ref, alt, gt}} for each hit."""
    out: Dict[str, Dict[str, str]] = {}
    with open(vcf_path, "r", errors="ignore") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            parts = line.rstrip().split("\t")
            if len(parts) < 10:
                continue
            chrom, pos, rsid, ref, alt = parts[0], parts[1], parts[2], parts[3], parts[4]
            if rsid not in rsid_set:
                continue
            fmt = parts[8].split(":")
            sample = parts[9].split(":")
            gt_idx = fmt.index("GT") if "GT" in fmt else 0
            gt = sample[gt_idx] if gt_idx < len(sample) else "./."
            # Normalize phased to unphased for simpler comparison
            gt_norm = gt.replace("|", "/")
            out[rsid] = {
                "chrom": chrom, "pos": pos, "ref": ref, "alt": alt, "gt": gt_norm,
            }
            if len(out) == len(rsid_set):
                break
    return out


_COMPLEMENT = {"A": "T", "T": "A", "C": "G", "G": "C", "N": "N"}


def _classify_genotype(ref_in_vcf: str, alt_in_vcf: str, gt: str,
                       expected_ref: str, expected_alt: str) -> str:
    """Classify a VCF genotype into '0/0', '0/1', '1/1' in the catalog's convention.

    Handles strand flips: if VCF ref/alt match the complements of catalog
    ref/alt, treat as same SNP on the opposite strand.
    """
    if not gt or "." in gt:
        return "unknown"
    try:
        a1, a2 = gt.split("/")
    except ValueError:
        return "unknown"

    def _base(idx: str) -> str:
        if idx == "0":
            return ref_in_vcf
        if idx == "1":
            return alt_in_vcf
        return "N"

    b1, b2 = _base(a1), _base(a2)

    # Determine orientation: forward if catalog ref/alt == VCF ref/alt
    #                        reverse if catalog ref/alt == complement(VCF ref/alt)
    def _match_forward(b):
        if b == expected_ref:
            return 0
        if b == expected_alt:
            return 1
        return None

    def _match_reverse(b):
        c = _COMPLEMENT.get(b, "N")
        if c == expected_ref:
            return 0
        if c == expected_alt:
            return 1
        return None

    # Prefer the orientation that classifies both alleles
    fwd = (_match_forward(b1), _match_forward(b2))
    rev = (_match_reverse(b1), _match_reverse(b2))
    if None not in fwd:
        i1, i2 = fwd
    elif None not in rev:
        i1, i2 = rev
    else:
        return f"mismatch({b1}/{b2})"
    count_alt = i1 + i2
    if count_alt == 0:
        return "0/0"
    if count_alt == 1:
        return "0/1"
    return "1/1"


def analyze_vcf(vcf_path: Path, catalog: List[Dict[str, Any]] = CATALOG
                ) -> Dict[str, Any]:
    """Run the full analysis and return the structured summary."""
    rsid_set = {entry["rsid"] for entry in catalog}
    matches = parse_vcf(vcf_path, rsid_set)

    results: List[Dict[str, Any]] = []
    by_category: Dict[str, List[Dict[str, Any]]] = {}
    for entry in catalog:
        rsid = entry["rsid"]
        vcf_row = matches.get(rsid)
        if not vcf_row:
            continue
        # Use genotype-level classification
        classified = _classify_genotype(
            vcf_row["ref"], vcf_row["alt"], vcf_row["gt"],
            entry.get("ref", ""), entry.get("alt", ""),
        )
        interpretation = entry["interpretation"].get(classified, "")
        if not interpretation and classified.startswith("mismatch"):
            interpretation = (
                f"Genotype {vcf_row['gt']} ({vcf_row['ref']}/{vcf_row['alt']} in "
                f"VCF) doesn't match catalog expected {entry.get('ref')}/{entry.get('alt')}. "
                "Likely different genome build or strand — re-check manually."
            )
        row = {
            "rsid": rsid,
            "gene": entry.get("gene"),
            "variant": entry.get("variant"),
            "category": entry.get("category"),
            "disease": entry.get("disease"),
            "genotype": vcf_row["gt"],
            "genotype_classified": classified,
            "interpretation": interpretation,
            "evidence": entry.get("evidence"),
            "sources": entry.get("sources", []),
        }
        results.append(row)
        by_category.setdefault(row["category"], []).append(row)

    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "vcf_path": str(vcf_path),
        "total_catalog_snps": len(catalog),
        "snps_found_in_vcf": len(results),
        "results": results,
        "by_category": by_category,
    }


def save_summary(summary: Dict[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "genetic_summary.json"
    json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    return json_path


def load_summary(data_dir: Path) -> Optional[Dict[str, Any]]:
    p = data_dir / "genetics" / "genetic_summary.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def render_markdown(summary: Dict[str, Any]) -> str:
    """Render a compact markdown summary (for human reading + email + advisor prompt)."""
    lines = [
        "# Genetic Analysis — Current Evidence Summary",
        f"_Generated {summary.get('generated_at', '?')} — "
        f"{summary.get('snps_found_in_vcf', 0)}/{summary.get('total_catalog_snps', 0)} catalog SNPs found in VCF_",
        "",
    ]
    cat_labels = {
        "pharmacogenomics": "💊 Pharmacogenomics (drug response)",
        "cardiovascular": "❤️ Cardiovascular",
        "metabolic": "🔥 Metabolic / Diabetes",
        "diet": "🥛 Diet / Caffeine / Alcohol",
        "fertility": "🧬 Fertility / Reproductive",
        "sleep": "🌙 Sleep / Circadian",
        "stress_cognition": "🧠 Stress / Cognition",
        "antioxidant": "🛡️ Antioxidant / Oxidative Stress",
        "nutrient": "☀️ Nutrient Metabolism",
        "exercise": "🏋️ Exercise / Muscle",
        "prostate": "🎯 Prostate",
        "inflammation": "🔥 Inflammation",
    }
    for cat, label in cat_labels.items():
        rows = summary.get("by_category", {}).get(cat, [])
        if not rows:
            continue
        lines.append(f"## {label}")
        for r in rows:
            gene = r.get("gene", "")
            variant = r.get("variant", "") or ""
            header = f"### {gene}" + (f" ({variant})" if variant else "") + f" — `{r.get('rsid')}` [{r.get('genotype_classified')}]"
            lines.append(header)
            lines.append(f"*{r.get('disease', '')}*")
            lines.append("")
            lines.append(r.get("interpretation", ""))
            lines.append(f"_Evidence: {r.get('evidence', 'n/a')}_")
            lines.append("")
    return "\n".join(lines)
