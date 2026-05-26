"""Daily AI health advisor powered by Gemini 3.1 Flash Lite.

Gathers Oura Ring data and any available health reports, then asks Gemini
to act as a general practitioner focused on maximizing sperm motility and
conception chances while maintaining high energy levels.

Includes:
- 7-day metric trend analysis (rolling averages, improving/declining)
- Action completion feedback loop (what was done yesterday, streaks)
- Parsed action items saved for tracking via email buttons
"""
from __future__ import annotations

import json
import re

from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import SyncConfig
from .llm_client import generate as llm_generate
from .llm_client import has_credentials as llm_has_credentials
from .llm_client import provider_info as llm_provider_info
from .storage import load_daily_payload, load_lab_documents

# Legacy constant — preserved so other modules can still import it. The
# active model is now resolved through llm_client.provider_info().
DEFAULT_MODEL = "gpt-5.5"


def advisor_model(config: SyncConfig) -> str:
    """Resolve the active model name via the llm_client abstraction."""
    return llm_provider_info()["model"]


def advisor_has_credentials(config: SyncConfig) -> bool:
    """Returns True iff the active LLM provider has credentials."""
    return llm_has_credentials()


def _gather_context(
    config: SyncConfig, day: date
) -> Dict[str, Any]:
    """Collect all available health data for the prompt."""
    # Weekend mode: Fri (4) and Sat (5) have historically had 0% completion
    context: Dict[str, Any] = {
        "date": day.isoformat(),
        "weekend_mode": day.weekday() in (4, 5),
    }

    # Oura daily data
    try:
        oura = load_daily_payload(config, day.isoformat())
        context["oura"] = oura
    except FileNotFoundError:
        context["oura"] = None

    # Lab documents (most recent of each kind)
    try:
        labs = load_lab_documents(config)
        context["lab_reports"] = labs
    except Exception:
        context["lab_reports"] = []

    # Medical image analyses (MRI, X-ray, CT, etc.)
    try:
        from .image_analyzer import load_image_analyses

        scans = load_image_analyses(config)
        context["image_analyses"] = scans
    except Exception:
        context["image_analyses"] = []

    # 7-day action completion history (feedback loop — reads from Sheet first)
    try:
        from .action_tracker import compute_streaks, load_action_history_with_sheets

        context["action_history"] = load_action_history_with_sheets(config, num_days=7)
        context["streaks"] = compute_streaks(config.data_dir)
    except Exception:
        context["action_history"] = []
        context["streaks"] = {}

    # Action-to-metric correlations (feedback loop — what actually works)
    try:
        from .action_effects import compute_action_effects

        context["action_effects"] = compute_action_effects(
            config.data_dir, day, lookback_days=14
        )
    except Exception:
        context["action_effects"] = []

    # 7-day Oura trend data
    try:
        from .trend_analyzer import (
            compute_metric_trends,
            compute_rolling_averages,
            format_trend_section,
            load_oura_history,
        )

        oura_history = load_oura_history(config.data_dir, day)
        context["rolling_averages"] = compute_rolling_averages(oura_history)
        context["metric_trends"] = compute_metric_trends(oura_history)
        context["oura_history"] = oura_history
    except Exception:
        context["rolling_averages"] = {}
        context["metric_trends"] = {}
        context["oura_history"] = []

    # Fresh research papers (PubMed + OpenAlex) — R2
    try:
        from ..research.pipeline import load_research_for_day

        context["research"] = load_research_for_day(config, day)
    except Exception:
        context["research"] = []

    # Newly ingested reports (M1) — for today's banner
    try:
        from .report_summarizer import load_new_reports_for_day

        context["new_reports"] = load_new_reports_for_day(config, day)
    except Exception:
        context["new_reports"] = []

    # Cross-report marker trends (M4)
    try:
        from .report_trends import compute_marker_trends

        context["marker_trends"] = compute_marker_trends(config)
    except Exception:
        context["marker_trends"] = {}

    # Curated genetic analysis (persistent, loads every day)
    try:
        from .genetic_analysis import load_summary as _load_genetic

        context["genetic_summary"] = _load_genetic(config.data_dir)
    except Exception:
        context["genetic_summary"] = None

    # Upcoming checkups (next 14 days) + batched lab visits (next 30 days)
    try:
        from .checkup_schedule import upcoming_checkups, upcoming_lab_visits

        context["upcoming_checkups"] = upcoming_checkups(config, within_days=14)
        context["upcoming_lab_visits"] = upcoming_lab_visits(config, within_days=30)
    except Exception:
        context["upcoming_checkups"] = []
        context["upcoming_lab_visits"] = []

    # Biomarker trends from blood tests + spermograms (≥2 readings)
    try:
        from .biomarker_trends import summarize_for_advisor

        context["biomarker_trends"] = summarize_for_advisor(config, top_n=6)
    except Exception:
        context["biomarker_trends"] = []

    return context


def _build_prompt(context: Dict[str, Any]) -> str:
    """Build the user prompt with today's data, trends, and action history."""
    oura = context.get("oura")
    labs = context.get("lab_reports", [])
    today = context["date"]

    # ── Oura section ──
    if oura and (oura.get("sleep_hours", 0) > 0 or oura.get("sleep_quality", 0) > 0):
        activity_note = ""
        if oura.get("activity_is_previous_day"):
            activity_note = " (previous day \u2014 today's not yet available)"

        oura_section = f"""## Today's Oura Ring data ({today})
**Sleep:**
- Total sleep: {oura.get('sleep_hours', 'N/A')} hours (score {oura.get('sleep_quality', 'N/A')}/100)
- Deep sleep: {oura.get('deep_sleep_min', 'N/A')} min
- REM sleep: {oura.get('rem_sleep_min', 'N/A')} min
- Light sleep: {oura.get('light_sleep_min', 'N/A')} min
- Efficiency: {oura.get('efficiency', 'N/A')}%

**Heart & Recovery:**
- Resting heart rate: {oura.get('resting_hr', 'N/A')} bpm
- Average heart rate (sleep): {oura.get('avg_hr', 'N/A')} bpm
- Heart rate variability (HRV): {oura.get('hrv', 'N/A')} ms
- Average breathing rate: {oura.get('avg_breath', 'N/A')} breaths/min
- Readiness score: {oura.get('readiness_score', 'N/A')}/100
- Temperature deviation: {oura.get('temp_deviation', 'N/A')}\u00b0C

**Activity{activity_note}:**
- Steps: {oura.get('steps', 'N/A')}
- Active minutes: {oura.get('active_minutes', 'N/A')}
- Active calories: {oura.get('active_calories', 'N/A')}
- Total calories: {oura.get('calories', 'N/A')}
- Activity score: {oura.get('activity_score', 'N/A')}/100
- Sitting hours: {oura.get('sitting_hours', 'N/A')}"""
    elif oura:
        oura_section = (
            "## Oura Ring data\n"
            "Oura returned scores but no detailed sleep data for today. "
            f"Sleep score: {oura.get('sleep_quality', 'N/A')}/100, "
            f"Readiness score: {oura.get('readiness_score', 'N/A')}/100."
        )
    else:
        oura_section = "## Oura Ring data\nNo data available for today."

    # ── Lab reports section ──
    if labs:
        lab_parts = []
        for lab in labs:
            kind = lab.get("kind", "unknown")
            lab_date = lab.get("date", "unknown")
            raw_text = lab.get("raw_text", "") or lab.get("text", "")
            # Genetic tests get more room (SNP data is dense)
            max_len = 5000 if kind == "genetic_test" else 3000
            if len(raw_text) > max_len:
                raw_text = raw_text[:max_len] + "\n... [truncated]"
            lab_parts.append(f"### {kind} (date: {lab_date})\n{raw_text}")
        labs_section = "## Available health reports\n" + "\n\n".join(lab_parts)
    else:
        labs_section = "## Available health reports\nNo lab reports on file."

    # ── Medical image analyses ──
    scans = context.get("image_analyses", [])
    if scans:
        scan_parts = []
        for scan in scans:
            fname = scan.get("filename", "unknown")
            scan_date = scan.get("date", "unknown")
            severity = scan.get("severity", "UNKNOWN")
            analysis = scan.get("analysis", "")
            if len(analysis) > 2000:
                analysis = analysis[:2000] + "\n... [truncated]"
            scan_parts.append(
                f"### {fname} (date: {scan_date}, severity: {severity})\n{analysis}"
            )
        scans_section = (
            "## Medical image analyses (MRI / X-ray / CT)\n"
            + "\n\n".join(scan_parts)
        )
    else:
        scans_section = ""

    sections = [oura_section, labs_section]
    if scans_section:
        sections.append(scans_section)

    # ── 7-day trend section ──
    averages = context.get("rolling_averages", {})
    trends = context.get("metric_trends", {})
    if averages:
        from .trend_analyzer import format_trend_section

        trend_text = format_trend_section(averages, trends, oura)
        if trend_text:
            sections.append(trend_text)

    # ── Action completion history (feedback loop) ──
    action_history = context.get("action_history", [])
    if action_history:
        action_section = "## Recent Action Completion History\n"
        for day_record in action_history:
            d = day_record["date"]
            actions = day_record.get("actions", [])
            completed = [a for a in actions if a.get("done")]
            not_completed = [a for a in actions if not a.get("done")]
            action_section += f"\n**{d}** ({len(completed)}/{len(actions)} completed):\n"
            for a in completed:
                action_section += f"  - DONE: {a['title']}\n"
            for a in not_completed:
                action_section += f"  - NOT DONE: {a['title']}\n"

        streaks = context.get("streaks", {})
        if streaks.get("any_action", 0) > 0:
            action_section += (
                f"\nCurrent streak: {streaks.get('any_action', 0)} consecutive days "
                f"with at least 1 action done\n"
            )
        if streaks.get("all_actions", 0) > 0:
            action_section += (
                f"Full completion streak: {streaks.get('all_actions', 0)} consecutive days "
                f"with all actions done\n"
            )
        sections.append(action_section)

    # ── Computed action effects (what actually moved metrics) ──
    action_effects = context.get("action_effects", [])
    if action_effects:
        effects_section = "## Computed Action Effects (last 14 days)\n"
        effects_section += (
            "These correlations show next-day metric changes when an action "
            "was done vs. skipped. Use them to prioritize what works.\n"
        )
        for eff in action_effects[:6]:
            direction = "beneficial" if eff["impact"] > 0 else "detrimental"
            effects_section += (
                f"- **{eff['action']}**: {eff['metric']} {eff['delta']} "
                f"(done {eff['days_done']}d avg {eff['done_avg']}, "
                f"skipped {eff['days_skipped']}d avg {eff['skip_avg']}) "
                f"— {direction}\n"
            )
        sections.append(effects_section)

    # ── Deny-list: actions skipped 3+ consecutive days (mechanical rotation) ──
    deny_list = _compute_deny_list(context.get("action_history", []))
    if deny_list:
        deny_section = (
            "## DO NOT RECOMMEND TODAY (failed 3+ consecutive days)\n"
            "These titles have been ignored for 3+ straight days. Recommending them "
            "again wastes a slot. You MUST pick something different targeting the "
            "same underlying goal.\n"
        )
        for title, skips in deny_list:
            deny_section += f"- **{title}** (skipped {skips} days in a row)\n"
        sections.append(deny_section)

    # ── Fresh research papers (R2) ──
    research = context.get("research", [])
    if research:
        lines = ["## Fresh research (last 24 months — cite when relevant)"]
        for r in research[:6]:
            bits = []
            if r.get("goal"):
                bits.append(f"**{r['goal']}**")
            bits.append(r.get("paper_title", "Untitled"))
            meta_bits = []
            if r.get("journal"):
                meta_bits.append(r["journal"])
            if r.get("cited_by_count"):
                meta_bits.append(f"cited {r['cited_by_count']}")
            if meta_bits:
                bits.append(f"({', '.join(meta_bits)})")
            line = " — ".join(bits)
            if r.get("url"):
                line += f"  {r['url']}"
            lines.append(f"- {line}")
        lines.append(
            "\nAt least one action today MUST cite one of these papers by "
            "author+year or title phrase (not the 14 static findings in your system prompt)."
        )
        sections.append("\n".join(lines))

    # ── New reports detected today (M1) ──
    new_reports = context.get("new_reports", [])
    if new_reports:
        lines = ["## 🆕 New medical reports detected today"]
        for rep in new_reports[:5]:
            kind = rep.get("kind", "report")
            summary = rep.get("summary", "").strip()
            flags = rep.get("flags", [])
            severity = rep.get("severity") or ""
            header = f"- **{kind}**"
            if severity:
                header += f" [severity: {severity}]"
            lines.append(header)
            if summary:
                lines.append(f"  {summary}")
            if flags:
                lines.append("  Flagged values: " + "; ".join(flags[:4]))
        lines.append(
            "\nOpen today's plan with a brief acknowledgement of the new report(s) "
            "and adapt the priority action to address the most important finding."
        )
        sections.append("\n".join(lines))

    # ── Biomarker time-series trends (blood + spermogram) ──
    bm_trends = context.get("biomarker_trends", [])
    if bm_trends:
        lines = [
            "## 📊 Biomarker time-series (markers with ≥2 readings)",
            "",
            "Use these in the Why / What-to-avoid / Nutrition focus sections "
            "of today's plan when relevant. Always cite the actual numbers "
            "(e.g., 'your testosterone went from 540 to 612 ng/dL') so the "
            "patient sees the data, not just the recommendation.",
            "",
            "**For any marker flagged [low] or [high], the suggested protocols "
            "list below is an evidence-cited menu. Your Priority or Backup "
            "action MUST be drawn from those protocols when applicable, and "
            "the Why field MUST cite the same paper. If multiple markers point "
            "to the same intervention (e.g. antioxidant stack helps both "
            "motility and DFI), prefer that overlap.**",
            "",
        ]
        for t in bm_trends:
            opt = t.get("optimal_range")
            opt_str = f" (optimal {opt[0]}–{opt[1]} {t['unit']})" if opt and any(opt) else ""
            flag_str = f" [**{t['last_flagged']}**]" if t.get("last_flagged") in ("low", "high") else ""
            cit = ", ".join(t.get("citations") or [])
            ref_source = t.get("ref_source") or ""
            ref_source_tag = f" — ref: {ref_source}" if ref_source else ""
            lines.append(
                f"- **{t['name']}**: {t['first_value']:g} → {t['last_value']:g} "
                f"{t['unit']} ({t['pct_change']:+.1f}% over {t['days_span']}d, "
                f"{t['n_points']} readings, {t['direction']}){flag_str}{opt_str}"
                + (f" — {cit}" if cit else "")
                + ref_source_tag
            )
            # When the marker is flagged, expose the evidence-cited menu of
            # corrective actions. The system-prompt addendum above forces
            # the LLM to pick from this list rather than freewheel.
            interv = t.get("interventions") or []
            if interv:
                lines.append("  ↗ Suggested protocols (pick one for today's plan):")
                for iv in interv:
                    lines.append(
                        f"    • {iv['action']} — {iv['expected_effect']} "
                        f"[{iv['citation']}, {iv['category']}]"
                    )
        sections.append("\n".join(lines))

    # ── Upcoming checkups (medical scheduling reminder) ──
    upcoming = context.get("upcoming_checkups", [])
    lab_visits = context.get("upcoming_lab_visits", [])
    if upcoming or lab_visits:
        lines = ["## 📅 Upcoming check-ups"]

        # Each batched lab visit is rendered as one block: lab + dated visit +
        # the union of French panel names to put on the request slip. This
        # collapses 5 separate test reminders into one bookable visit.
        for v in lab_visits[:3]:
            days = v.get("days_away", 0)
            when = "today" if days == 0 else f"in {days}d"
            lines.append(
                f"### Lab visit — {when} ({v['date']})  [{v['label']}]"
            )
            lines.append(f"**Lab:** {v['lab_provider']} — {v['lab_address']}")
            if v.get("panels"):
                lines.append("**Request slip — ask for these panels:**")
                for p in v["panels"]:
                    lines.append(f"  • {p}")
            lines.append("")

        # Specialist / non-bundled reminders (DEXA, dermatology, ECG, BP at home)
        non_lab = [
            i for i in upcoming
            if not i.get("bundle") or i.get("bundle") == "specialist"
        ]
        if non_lab:
            lines.append("### Other reminders (next 14 days)")
            for item in non_lab[:5]:
                days = item.get("days_away", 0)
                when = "today" if days == 0 else f"in {days}d" if days > 0 else f"{-days}d overdue"
                lines.append(f"- **{item.get('name')}** — {when} ({item.get('next_due')})")

        lines.append(
            "\nWhen a lab visit is within 7 days, mention it in the Progress or "
            "Fertility checkpoint section so the user actually books the appointment "
            "and brings the request slip."
        )
        sections.append("\n".join(lines))

    # ── Curated genetic analysis (actionable-only findings) ──
    genetic = context.get("genetic_summary")
    if genetic and genetic.get("results"):
        actionable = [
            r for r in genetic["results"]
            if r.get("genotype_classified") not in (None, "0/0", "unknown")
            and not str(r.get("genotype_classified", "")).startswith("mismatch")
        ]
        if actionable:
            lines = [
                "## 🧬 Curated genetic findings (actionable non-reference genotypes)",
                "These are locked facts about the patient's DNA. Factor them into every protocol when relevant.",
            ]
            for r in actionable[:20]:
                lines.append(
                    f"- **{r.get('gene')}** "
                    + (f"({r.get('variant')}) " if r.get('variant') else "")
                    + f"[{r.get('genotype_classified')}] — {r.get('interpretation')}"
                )
            sections.append("\n".join(lines))

    # ── Cross-report marker trends (M4) ──
    marker_trends = context.get("marker_trends", {})
    if marker_trends:
        lines = ["## Lab marker trends (across multiple reports)"]
        for marker, info in list(marker_trends.items())[:8]:
            arrow = {"up": "↑", "down": "↓", "stable": "→"}.get(info.get("direction", "stable"), "→")
            lines.append(
                f"- {marker}: {info.get('latest')} {arrow} "
                f"(previous {info.get('previous')}, Δ {info.get('delta')})"
            )
        sections.append("\n".join(lines))

    # ── Weekend mode hint ──
    if context.get("weekend_mode"):
        sections.append(
            "## Mode: WEEKEND (lighter plan)\n"
            "Friday/Saturday have historically had 0% completion. Respond with "
            "ONE priority action only and skip the 3 micro-actions."
        )

    return "\n\n".join(sections)


_NORM_CLEAN_PATTERNS = [
    re.compile(r"\*\*.*$"),  # trailing `**Supplement**` etc.
    re.compile(r"\[(easy|medium|hard)\].*$", re.IGNORECASE),
    re.compile(r"\|.*$"),  # "| Category: ..."
    re.compile(r"\(.*?\)$"),  # trailing parentheticals like "(The 'Loose-Fit' Protocol)"
    re.compile(r"[\s*_:;.\-\u2014]+$"),  # trailing punctuation/whitespace
]


def _normalize_title(title: str) -> str:
    """Normalize an action title so old/new format variants group together.

    Strips trailing category/difficulty junk that earlier advisor versions
    leaked into titles (e.g., "Creatine Buffer** [Easy] | Category: Supplement"
    -> "creatine buffer").
    """
    if not title:
        return ""
    t = title.strip()
    for pat in _NORM_CLEAN_PATTERNS:
        t = pat.sub("", t).strip()
    # Collapse whitespace, lowercase for matching
    t = re.sub(r"\s+", " ", t).strip().lower()
    return t


def _compute_deny_list(
    action_history: List[Dict[str, Any]], min_consecutive_skips: int = 3
) -> List[tuple]:
    """Return action titles skipped on 3+ consecutive days, newest-to-oldest.

    Input: action_history is a list of {"date": ..., "actions": [{title, done, ...}]}
    ordered newest first. We walk through each (normalized) title and count
    how many of the most recent consecutive days it was NOT done.
    Returns a list of (display_title, skip_count) tuples.
    """
    if not action_history:
        return []

    # Collect per-normalized-title: list of (done_flag, display_title), newest first
    title_states: Dict[str, List[tuple]] = {}
    for day_record in action_history:
        for a in day_record.get("actions", []):
            raw = a.get("title", "").strip()
            norm = _normalize_title(raw)
            if not norm:
                continue
            title_states.setdefault(norm, []).append((bool(a.get("done")), raw))

    deny = []
    for norm, states in title_states.items():
        skips = 0
        display = states[0][1] if states else norm
        for done_flag, _raw in states:
            if done_flag:
                break
            skips += 1
        if skips >= min_consecutive_skips:
            deny.append((display, skips))
    deny.sort(key=lambda x: x[1], reverse=True)
    return deny


SYSTEM_PROMPT = """\
You are an experienced general practitioner and reproductive health specialist. \
Your patient is a man actively trying to conceive. Your primary goals are:

1. **Maximize sperm motility** and overall sperm quality to increase chances of successful conception.
2. **Maximize daily energy** so the patient feels sharp, productive, and physically ready.

Every day you receive the patient's wearable data (Oura Ring: sleep, HRV, resting HR, \
activity), any available medical reports (blood tests, sperm analysis, genetic tests, \
urine tests, doctor conclusions, prescriptions, complete health check-up reports), \
and AI-assisted analyses of medical images (MRI, X-ray, CT scans) if any are on file.

**Genetic data**: If genetic test results are provided, you MUST factor them into \
every recommendation. Key genetic variants to watch for and act on:
- **MTHFR** (C677T/A1298C): affects folate metabolism \u2192 recommend methylfolate over folic acid, \
monitor homocysteine, adjust B-vitamin supplementation
- **Factor V Leiden / Prothrombin**: thrombophilia risk \u2192 advise on movement, hydration, avoid \
prolonged sitting
- **COMT**: affects dopamine/stress metabolism \u2192 tailor caffeine, exercise intensity
- **SOD2 / GPX1**: oxidative stress genes \u2192 adjust antioxidant supplementation (CoQ10, NAC, \
selenium, vitamin C/E dosing)
- **VDR / CYP2R1**: vitamin D metabolism \u2192 adjust vitamin D dosing
- **FTO / MC4R**: weight/metabolism genes \u2192 tailor diet and exercise approach
- **HFE**: iron metabolism \u2192 watch ferritin levels
- **APOE**: lipid metabolism \u2192 adjust dietary fat recommendations
- Any other fertility-relevant SNPs: SRD5A2 (DHT), AR (androgen receptor), \
ESR1/ESR2 (estrogen receptors), SHBG variants
Always connect genetic findings to specific, actionable daily changes.

### Scientific evidence base (cite when relevant)
Ground your recommendations in these highly-cited 2015–2025 findings. Cite author/year when \
you use one. The full canonical bibliography lives in app/sync/bibliography.py and is \
shared with the dashboard cards — citations the user sees in their "How to improve" panel \
match the ones below verbatim.

**Reproductive health:**
1. Scrotal temperature: heat exposure (hot baths, laptops on lap, tight underwear) reduces \
sperm motility 20-40% within weeks (Durairajanayagam 2014; Sharma 2013)
2. CoQ10/Ubiquinol 200 mg/day: improves sperm motility and density (Salas-Huetos 2018 \
Andrology systematic review; Safarinejad 2009 original RCT)
3. Zinc 25-50 mg + Selenium 200 mcg/day: significant improvements in semen parameters \
(Salas-Huetos 2018; Buhling & Grajecki 2014)
4. Short sleep (<6 h): reduces testosterone 10-15% (Leproult & Van Cauter 2011 — still \
canonical mechanism); meta-analysis of sleep and semen (Wang 2018)
5. Moderate aerobic + resistance training 3-5x/week, 30-45 min: improves sperm parameters \
in sedentary men (Hajizadeh Maleki 2018 RCT; Gaskins 2015 cohort)
6. Omega-3/DHA 1.5-2 g/day: improves sperm membrane integrity and motility (Hosseini 2019 \
RCT; Salas-Huetos 2019 PUFA review)
7. Methylfolate 400-800 mcg/day: reduces sperm DNA fragmentation (Salas-Huetos 2018; \
Wong 2002 original RCT)
8. Vitamin D 2000-4000 IU/day: positively associated with testosterone and sperm motility \
(Pludowski 2018; Bouillon 2019 Endocrine Reviews; Pilz 2011 original RCT)
9. Chronic stress / high cortisol: reduces sperm motility and concentration (Nargund 2015; \
Ilacqua 2018)
10. Antioxidant stack (NAC 600 mg + Selenium 200 mcg + Vitamin E 400 IU + Vit C 1 g): \
Updated Cochrane review shows improved live-birth rate (Showell 2019 Cochrane)
15. Sperm DNA fragmentation diagnostics and management consensus (Esteves 2021 Andrology; \
Agarwal 2020) — varicocele is the single most reversible cause (Wang 2019 Fertil Steril)
16. Diet & fertility umbrella systematic review (Salas-Huetos 2017 Hum Reprod Update) — \
canonical anchor for nutrition counseling.

**Daily energy:**
11. Morning bright light (10 min within 30 min of waking): anchors cortisol awakening \
response, improves alertness (Figueiro 2017)
12. Brief cold exposure (30-90 sec cold shower): RCT n=3018 showed reduced sickness \
absence and improved energy (Buijze 2016 PLoS ONE; Shevchuk 2008 mechanism)
13. Creatine monohydrate 3-5 g/day: improves cognitive performance especially under \
sleep deprivation (Avgerinos 2018 systematic review; Roschel 2021 update)
14. Magnesium glycinate 400 mg before bed: improves sleep quality (Boyle 2017 systematic \
review; Abbasi 2012 original RCT)

**Cardiovascular / lipids (for the patient's ITGB3 + 9p21 CV-risk profile):**
17. LDL is causal for ASCVD (Ference 2017 EAS Mendelian randomization); each mmol/L \
reduction in LDL drops events ~22% (CTT 2019)
18. ApoB is the truer atherogenic particle count, target ApoB <80 primary / <65 secondary \
prevention (Sniderman 2019; Mach 2020 ESC dyslipidaemia guideline)
19. Lp(a) is genetically fixed but lifetime CVD risk-modifier; lever via aggressive \
LDL/ApoB reduction (Kronenberg 2022 EAS consensus)
20. Mediterranean diet primary prevention drops major CV events ~30% (Estruch 2018 \
PREDIMED). Omega-3 EPA+DHA for hypertriglyceridemia (Skulas-Ray 2019 AHA).
21. Residual inflammation independent of LDL is a lever (Ridker 2018 CANTOS Lancet).

### Volume & variation rules (CRITICAL — past attempts with 5 actions led to 0% completion)
The patient's data shows they complete **2 actions max on a good day**. Stop overloading them.

Each weekday, output exactly:
- **1 PRIORITY action** — the most important thing they should do today
- **1 BACKUP action** — a different category, same day
- **3 MICRO-WINS** — 2-minute actions they can stack into their existing routine

On Friday/Saturday (WEEKEND mode, signaled in the input), output only the 1 priority action \
and skip micro-wins. Weekend completion has historically been 0%; keep it minimal.

Variation requirements:
1. The PRIORITY action must be DIFFERENT from yesterday's priority (check action history).
2. Respect the "DO NOT RECOMMEND TODAY" list in the input — those titles have been skipped \
3+ consecutive days and are dead to us.
3. Balance categories across days: supplement, movement, sleep/recovery, stress, nutrition.
4. Rotate: never recommend the exact same priority two days in a row, and never the same \
pair of priority+backup.

### Feedback loop instructions (mandatory)
You receive:
- The patient's 7-day action completion history
- Computed action-to-metric correlations (what actually moved the numbers)

Requirements:
1. **Cite computed effects.** If the input contains "Computed Action Effects", the Why field \
of at least one action MUST reference a specific number from that list, using this exact phrasing: \
"On the N days you did X, your [metric] [direction] by [delta]." If no effects are computed yet, \
skip this requirement.
2. **Specificity**: every action has exact time (e.g., "7:05–7:20 AM"), exact dosage/count \
(e.g., "400 mg"), and exact duration (e.g., "12 minutes").
3. **Progress line**: if any action was completed in the last 7 days, open with one neutral line \
naming the most recent completion ("Last completed: [title] on [date]"). If nothing was completed, \
skip this line entirely — do not mention zero completions, do not shame, do not use the word \
"streak" unless the streak is currently > 0.

### 7-Day trend context
You also receive 7-day rolling averages and trend directions. Use these to:
1. Call out one clear improving or declining pattern (max 1 sentence).
2. Tie today's priority action to that pattern when possible.

### Output format (STRICT)

## Daily Health Plan — {date}

### Progress
(1 line max. Last completed action + date. Skip entirely if none in the past 7 days.)

### Priority (do this one thing)

1. **[Title]** [Easy/Medium/Hard] | Category: [Supplement/Movement/Sleep/Stress/Nutrition]
   **When:** Exact time window
   **Steps:**
   a) Specific step with exact dosage/duration
   b) Second step
   **Why:** 1–2 sentences. Include a citation (author year) AND, if computed effects exist, \
cite the number from the input exactly.
   **Expected effect:** What to notice and when.
   *Quick alt (2 min):* Minimal version.

### Backup (if priority won't happen)

2. **[Title]** [Easy/Medium/Hard] | Category: [different from priority]
   (same structure as above)

### 3 Micro-wins (each < 2 min — stack into existing habits)
(SKIP this whole section on Friday/Saturday if weekend_mode is active.)

- **[Micro-win 1]** — one-sentence how, tied to existing trigger (e.g., "After first coffee: …")
- **[Micro-win 2]** — one-sentence how
- **[Micro-win 3]** — one-sentence how

### One metric to watch today
Single line: the one number to check on the Oura ring tonight, with the target range.

### What to avoid today
One concrete thing, tied to today's data.

### Fertility checkpoint
One sentence only. Days since last sperm test (if known), or next test reminder.

HARD LIMITS:
- Total response under 600 words.
- Never mention "0/N completed" or "you skipped".
- Never use the word "streak" unless the current streak is > 0.
- Never recommend anything from the DO NOT RECOMMEND TODAY list.
- The patient is a tired human with 2 actions/day of capacity. Write for that human."""


def generate_daily_advice(
    config: SyncConfig,
    day: date | None = None,
) -> Dict[str, Any]:
    """Generate daily health advice using the configured Gemini model."""
    if day is None:
        day = datetime.now(tz=config.timezone).date()

    model = advisor_model(config)

    if not advisor_has_credentials(config):
        raise RuntimeError(
            f"Active LLM provider has no credentials. "
            f"provider_info={llm_provider_info()}"
        )

    context = _gather_context(config, day)
    user_message = _build_prompt(context)
    system = SYSTEM_PROMPT.replace("{date}", day.isoformat())

    # The daily plan benefits from deeper deliberation — explicitly request
    # "high" reasoning on the Codex path. Gemini/OpenAI ignore the flag.
    advice_text = llm_generate(
        system=system,
        user=user_message,
        model=model,
        max_output_tokens=1800,
        reasoning="high",
        timeout_s=600,
    )

    # Parse and save action items for tracking (email buttons + feedback loop)
    try:
        from .action_tracker import parse_actions, save_actions

        actions = parse_actions(advice_text, day.isoformat())
        if actions:
            save_actions(config.data_dir, day.isoformat(), actions)
            print(f"Saved {len(actions)} action items for tracking.")

            # Push actions to Google Sheet (works from any device)
            try:
                from .sheets_tracker import add_daily_actions

                add_daily_actions(config, day.isoformat(), actions)
            except Exception as sheet_exc:
                print(f"Sheet push failed (non-fatal): {sheet_exc}")
    except Exception as exc:
        print(f"Action parsing/saving failed (non-fatal): {exc}")

    result = {
        "report_type": "daily_advisor",
        "date": day.isoformat(),
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "model": model,
        "advice": advice_text,
        "context_summary": {
            "oura_available": context.get("oura") is not None,
            "lab_reports_count": len(context.get("lab_reports", [])),
            "lab_report_types": [
                lab.get("kind") for lab in context.get("lab_reports", [])
            ],
            "image_analyses_count": len(context.get("image_analyses", [])),
            "image_severities": [
                f"{s.get('filename', '?')}: {s.get('severity', '?')}"
                for s in context.get("image_analyses", [])
            ],
        },
    }
    return result


def save_advice_local(config: SyncConfig, advice: Dict[str, Any]) -> Path:
    """Save the daily advice to a local JSON file."""
    day = advice["date"]
    out_dir = config.data_dir / "advisor"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"daily_advice_{day}.json"
    target.write_text(json.dumps(advice, indent=2, ensure_ascii=False))
    return target


def upload_advice_to_drive(config: SyncConfig, advice: Dict[str, Any]) -> str:
    """Upload daily advice to Google Drive calendar folder ``me/health/YYYY/MM/DD``."""
    from .connectors.gdrive import calendar_folder_path, upload_bytes

    day_str = advice["date"]
    day_obj = date.fromisoformat(day_str)
    folder = calendar_folder_path(day_obj)

    content = (
        f"Daily Health Plan \u2014 {day_str}\n"
        f"Generated: {advice['generated_at']}\n"
        f"Model: {advice['model']}\n\n"
        f"{advice['advice']}\n"
    ).encode("utf-8")
    return upload_bytes(
        config,
        content,
        f"daily_advice_{day_str}.txt",
        mime_type="text/plain",
        folder_path=folder,
        create_folders=True,
    )


def email_advice(config: SyncConfig, advice: Dict[str, Any]) -> None:
    """Send the daily advice via email."""
    from .email_sender import send_advice_email

    send_advice_email(config, advice)


def build_stale_oura_advice(day: date, freshness: Dict[str, Any]) -> Dict[str, Any]:
    """Return a short advice payload warning the user that Oura data is stale.

    Avoids wasting an LLM call and sends a clear "data is broken, fix it" signal
    instead of personalized advice based on zeros.
    """
    stale_days = freshness.get("stale_days", 0)
    last_fresh = freshness.get("last_fresh_date") or "never"
    body = (
        f"## Daily Health Plan — {day.isoformat()}\n\n"
        f"### ⚠️ Oura data is stale\n\n"
        f"No fresh sleep / HRV / readiness data for **{stale_days} day(s)**. "
        f"Last good sync: {last_fresh}.\n\n"
        "Personalized advice needs real data. Without it, any recommendation would "
        "be pure guesswork, so today's plan is intentionally minimal.\n\n"
        "### Do this instead\n"
        "1. Open the Oura app on your phone — trigger a manual sync.\n"
        "2. Charge the ring if the battery is low (< 20%).\n"
        "3. If sync still fails, reconnect the ring under Settings → My Device.\n\n"
        "### If you have 2 minutes today\n"
        "- 10 min of morning daylight within 30 min of waking (Figueiro 2017).\n"
        "- 30-second cold rinse at the end of your shower (Buijze 2016 RCT).\n\n"
        "Tomorrow's plan will be fully personalized once data is flowing."
    )
    return {
        "report_type": "daily_advisor",
        "date": day.isoformat(),
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "model": "stale-data-short-circuit",
        "advice": body,
        "context_summary": {
            "oura_available": False,
            "oura_stale_days": stale_days,
            "last_fresh_date": last_fresh,
            "lab_reports_count": 0,
            "lab_report_types": [],
            "image_analyses_count": 0,
            "image_severities": [],
        },
    }


def print_advice(advice: Dict[str, Any]) -> None:
    """Pretty-print the daily advice to the terminal."""
    print(f"\n{'='*60}")
    print(f"  Daily Health Plan \u2014 {advice['date']}")
    print(f"{'='*60}\n")
    print(advice["advice"])
    print(f"\n{'='*60}")
    ctx = advice.get("context_summary", {})
    print(f"  Oura data: {'Yes' if ctx.get('oura_available') else 'No'}")
    if ctx.get("lab_report_types"):
        print(f"  Lab reports used: {', '.join(ctx['lab_report_types'])}")
    if ctx.get("image_analyses_count"):
        print(f"  Image analyses: {ctx['image_analyses_count']}")
        for sev in ctx.get("image_severities", []):
            print(f"    {sev}")
    print(f"{'='*60}\n")
