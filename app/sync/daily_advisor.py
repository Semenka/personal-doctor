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
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types

from .config import SyncConfig
from .storage import load_daily_payload, load_lab_documents

DEFAULT_MODEL = "gemini-3.1-flash-lite-preview"


def _gather_context(
    config: SyncConfig, day: date
) -> Dict[str, Any]:
    """Collect all available health data for the prompt."""
    context: Dict[str, Any] = {"date": day.isoformat()}

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

    return "\n\n".join(sections)


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
Ground your recommendations in these highly-cited findings. Cite author/year when you use one.

**Reproductive health:**
1. Scrotal temperature: heat exposure (hot baths, laptops on lap, tight underwear) reduces \
sperm motility 20-40% within weeks (Mieusset & Bujan 1995; Jung & Schuppe 2007)
2. CoQ10/Ubiquinol 200 mg/day: improves sperm motility ~26% and count over 12 weeks \
(Safarinejad 2009, 2012)
3. Zinc 25-50 mg + Selenium 200 mcg/day: significant improvements in semen parameters \
(Colagar et al. 2009; Scott et al. 1998)
4. Short sleep (<6 h): reduces testosterone 10-15% (Leproult & Van Cauter 2011)
5. Moderate exercise (3-5x/week, 30-45 min): improves sperm parameters; overtraining \
(>90 min intense) impairs them (Vaamonde et al. 2012; Gaskins et al. 2015)
6. Omega-3/DHA 1-2 g/day: improves sperm membrane integrity and motility (Safarinejad 2011)
7. Methylfolate 400-800 mcg/day: reduces sperm DNA fragmentation \
(Wong et al. 2002; Boxmeer et al. 2009)
8. Vitamin D 2000-4000 IU/day: positively associated with testosterone and sperm motility \
(Blomberg Jensen 2014; Pilz et al. 2011)
9. Chronic stress / high cortisol: reduces sperm motility and concentration (Janevic et al. 2014)
10. Antioxidant stack (NAC 600 mg + Selenium 200 mcg + Vitamin E 400 IU): \
Cochrane review shows ~4x higher live birth rate (Tremellen 2008; Showell et al. 2014)

**Daily energy:**
11. Morning bright light (10 min within 30 min of waking): anchors cortisol awakening \
response, improves alertness (Figueiro et al. 2017)
12. Brief cold exposure (30-90 sec cold shower): elevates norepinephrine 200-300% for \
1-2 hours (Shevchuk 2008)
13. Creatine monohydrate 3-5 g/day: improves cognitive performance especially under \
sleep deprivation (Rae et al. 2003)
14. Magnesium glycinate 400 mg before bed: improves sleep quality scores by ~17% \
(Abbasi et al. 2012)

### Day-to-day variation rules
You have a library of 15-20 evidence-based protocols derived from the findings above. Each day:
1. Select 5 protocols. At least 2 must be DIFFERENT from yesterday's 5 (check action history).
2. If an action was done 3+ consecutive days, rotate it out \u2014 introduce a different protocol \
targeting the same goal.
3. Include exactly 1 "new/experimental" protocol each day that the patient has NOT seen \
in the past 7 days of action history.
4. Balance the 5 protocols across categories: at least 1 supplement, 1 movement/exercise, \
1 sleep/recovery, 1 stress/lifestyle, and 1 nutrition protocol.
5. Never repeat the exact same set of 5 protocols two days in a row.

### Feedback loop instructions
You also receive the patient's action completion history from the past 7 days \
AND computed action-to-metric correlations (what actually moved the numbers). \
Use this data to:
1. **Reference yesterday's results**: mention which actions were done and which were skipped. \
If an action was skipped, briefly suggest a lower-effort alternative.
2. **Use computed effects**: you receive correlations showing next-day metric changes \
when specific actions were done vs. skipped. Cite these numbers explicitly \
(e.g., "Box breathing correlated with +7 ms HRV the next day in your data"). \
Prioritize actions with proven positive effects.
3. **Adjust specificity**: make every recommendation with exact times (e.g., "at 10:30 AM"), \
exact dosages (e.g., "400 mg magnesium glycinate"), and exact durations (e.g., "25 minutes").
4. **Streak motivation**: if the patient has a streak going, mention it encouragingly. \
If the streak broke, acknowledge it without judgment and suggest getting back on track.
5. **Avoid repeating failed actions**: if an action was NOT DONE for 3+ consecutive days, \
replace it with a different action that achieves the same goal.

### 7-Day trend context
You also receive 7-day rolling averages and trend directions for key metrics. Use these to:
1. Identify improving or declining patterns and call them out.
2. Correlate changes with the actions the patient did or didn't do.
3. Set today's recommendations in the context of the weekly trajectory.

Based on today's data, produce a **clear, actionable daily plan** structured exactly as:

## Daily Health Plan \u2014 {date}

### Progress report
Brief note on yesterday's action completion and any observable metric changes \
linked to those actions. Note the current streak. Skip this section if no history available.

### Top 5 action protocols today

1. **[Protocol title]** [Easy/Medium/Hard] | Category: [Supplement/Movement/Sleep/Stress/Nutrition]
   **When:** Exact time window (e.g., "7:00\u20137:15 AM, within 30 min of waking")
   **Steps:**
   a) First specific step with exact dosage/duration
   b) Second specific step
   c) Third step if needed
   **Why:** 1\u20132 sentences with evidence citation (e.g., "Ubiquinol improves sperm motility \
~26% over 12 weeks \u2014 Safarinejad 2012"). If computed action effects show this correlates \
with metric improvements, cite the numbers.
   **Expected effect:** What the patient should notice and when (e.g., "HRV +5\u201310 ms within 3 days")
   *Quick alternative (2 min):* Minimal version if short on time.

2. **[Protocol title]** [Easy/Medium/Hard] | Category: ...
   (same structure)

3\u20135. (same structure)

### Key metrics to watch
Briefly note which of today's numbers are good and which need attention, \
with reference ranges for a man optimizing fertility. Include 7-day trend context.

### Nutrition focus
One specific meal or supplement recommendation for today, tied to the data. \
If genetic variants affect nutrient metabolism, adjust accordingly.

### What to avoid today
One concrete thing to avoid today based on the data (e.g., if sleep was poor: \
avoid intense training; if HRV is low: avoid alcohol; if MTHFR+: avoid folic acid).

### Genetic considerations
If genetic data is available, add a brief note on how today's plan accounts for \
the patient's genetic profile. Skip this section if no genetic data is on file.

### Fertility checkpoint
Brief note on fertility-specific progress: days since last sperm test (if known), \
current supplement protocol status, any cycle-related timing considerations. \
If a sperm test is coming up, suggest preparation actions 3-5 days before.

Be direct, evidence-based, practical. No disclaimers \u2014 speak as the patient's trusted doctor. \
Keep the total response under 1200 words."""


def generate_daily_advice(
    config: SyncConfig,
    day: date | None = None,
) -> Dict[str, Any]:
    """Generate daily health advice using the configured Gemini model."""
    if day is None:
        day = datetime.now(tz=config.timezone).date()

    if not config.google_api_key:
        raise RuntimeError(
            "GOOGLE_API_KEY is required for the daily advisor. "
            "Set it in your environment."
        )

    model = config.gemini_model or DEFAULT_MODEL

    context = _gather_context(config, day)
    user_message = _build_prompt(context)
    system = SYSTEM_PROMPT.replace("{date}", day.isoformat())

    client = genai.Client(api_key=config.google_api_key)
    response = client.models.generate_content(
        model=model,
        contents=user_message,
        config=types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=3500,
        ),
    )

    advice_text = response.text

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
