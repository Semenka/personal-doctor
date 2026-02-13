"""Daily AI health advisor powered by Claude Opus 4.6.

Gathers Oura Ring data and any available health reports, then asks Claude
to act as a general practitioner focused on maximizing sperm motility and
conception chances while maintaining high energy levels.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import anthropic

from .config import SyncConfig
from .storage import load_daily_payload, load_lab_documents

MODEL = "claude-opus-4-6"


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

    return context


def _build_prompt(context: Dict[str, Any]) -> str:
    """Build the system + user prompt for Claude."""
    oura = context.get("oura")
    labs = context.get("lab_reports", [])
    today = context["date"]

    # Format Oura section
    if oura:
        oura_section = f"""## Today's Oura Ring data ({today})
- Sleep: {oura.get('sleep_hours', 'N/A')} hours, quality score {oura.get('sleep_quality', 'N/A')}/10
- Resting heart rate: {oura.get('resting_hr', 'N/A')} bpm
- Heart rate variability (HRV): {oura.get('hrv', 'N/A')} ms
- Steps: {oura.get('steps', 'N/A')}
- Active minutes: {oura.get('active_minutes', 'N/A')}
- Calories: {oura.get('calories', 'N/A')}
- Water intake: {oura.get('water_l', 'N/A')} L
- Sitting hours: {oura.get('sitting_hours', 'N/A')}
- Mood: {oura.get('mood', 'N/A')}/10
- Stress: {oura.get('stress', 'N/A')}/10"""
    else:
        oura_section = "## Oura Ring data\nNo data available for today."

    # Format lab reports section
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

    # Format medical image analyses
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
- **MTHFR** (C677T/A1298C): affects folate metabolism → recommend methylfolate over folic acid, \
monitor homocysteine, adjust B-vitamin supplementation
- **Factor V Leiden / Prothrombin**: thrombophilia risk → advise on movement, hydration, avoid \
prolonged sitting
- **COMT**: affects dopamine/stress metabolism → tailor caffeine, exercise intensity
- **SOD2 / GPX1**: oxidative stress genes → adjust antioxidant supplementation (CoQ10, NAC, \
selenium, vitamin C/E dosing)
- **VDR / CYP2R1**: vitamin D metabolism → adjust vitamin D dosing
- **FTO / MC4R**: weight/metabolism genes → tailor diet and exercise approach
- **HFE**: iron metabolism → watch ferritin levels
- **APOE**: lipid metabolism → adjust dietary fat recommendations
- Any other fertility-relevant SNPs: SRD5A2 (DHT), AR (androgen receptor), \
ESR1/ESR2 (estrogen receptors), SHBG variants
Always connect genetic findings to specific, actionable daily changes.

Based on today's data, produce a **clear, actionable daily plan** structured exactly as:

## Daily Health Plan — {date}

### Top 3 actions today

1. **[Action title]**
   What to do, when, and why. Be specific (dosages, timings, durations). \
Explain the direct link to sperm motility or energy. If a genetic variant \
influences this recommendation, mention it.

2. **[Action title]**
   ...

3. **[Action title]**
   ...

### Key metrics to watch
Briefly note which of today's numbers are good and which need attention, \
with reference ranges for a man optimizing fertility.

### Nutrition focus
One specific meal or supplement recommendation for today, tied to the data. \
If genetic variants affect nutrient metabolism, adjust accordingly.

### What to avoid today
One concrete thing to avoid today based on the data (e.g., if sleep was poor: \
avoid intense training; if HRV is low: avoid alcohol; if MTHFR+: avoid folic acid).

### Genetic considerations
If genetic data is available, add a brief note on how today's plan accounts for \
the patient's genetic profile. Skip this section if no genetic data is on file.

Be direct, evidence-based, practical. No disclaimers — speak as the patient's trusted doctor. \
Keep the total response under 600 words."""


def generate_daily_advice(
    config: SyncConfig,
    day: date | None = None,
) -> Dict[str, Any]:
    """Generate daily health advice using Claude Opus 4.6."""
    if day is None:
        day = datetime.now(tz=config.timezone).date()

    if not config.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is required for the daily advisor. "
            "Set it in your environment."
        )

    context = _gather_context(config, day)
    user_message = _build_prompt(context)
    system = SYSTEM_PROMPT.replace("{date}", day.isoformat())

    client = anthropic.Anthropic(api_key=config.anthropic_api_key)
    response = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        system=system,
        messages=[{"role": "user", "content": user_message}],
    )

    advice_text = response.content[0].text

    result = {
        "report_type": "daily_advisor",
        "date": day.isoformat(),
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "model": MODEL,
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
        f"Daily Health Plan — {day_str}\n"
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
    print(f"  Daily Health Plan — {advice['date']}")
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
