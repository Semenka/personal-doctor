"""From an anomaly to a corrective action, with the literature in between.

``anomaly_detector.find_anomalies`` says *what* is off. This module answers
the next two questions for each finding:

1. What do the top journals say about it? — a PubMed search restricted to
   top general and specialist journals (NEJM, Lancet, JAMA, BMJ, Nature
   Medicine, Sleep, Circulation, Human Reproduction, …), abstracts included,
   so a recommendation can quote a result instead of guessing from a title.
2. What should he do about it today? — the LLM reads the finding, the last
   two weeks of watch data and those abstracts, and returns likely causes,
   ONE corrective action (time, steps), what it means for energy and
   fertility, and when it stops being a self-care problem.

Every stage degrades on its own: no network → no papers but still an action;
no LLM → the curated fallback action for that anomaly. The result is saved to
``<data>/anomalies/anomaly_<date>.json``, read by the daily advisor (one of the
day's three actions must address the top anomaly) and by the phone digest.
Only ``alert``-level findings send their own message at 07:41; ``watch``-level
ones wait for the 08:00 digest, so a slightly short night is not two pings.

Boundaries: no prescription-drug dosing, nothing outside the existing
supplement protocol, and the doctor line is always present.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import SyncConfig

logger = logging.getLogger("personal-doctor.anomaly_research")

MAX_RESEARCHED = 3      # anomalies researched per day (most severe first)
REUSE_DAYS = 3          # a recurring anomaly reuses its recent research
PAPERS_PER_ANOMALY = 4

# PubMed title abbreviations ([ta]). General top-tier journals plus the
# specialist ones for the user's two goals and for what a watch measures.
TOP_JOURNALS = [
    "N Engl J Med", "Lancet", "JAMA", "BMJ", "Nat Med", "Ann Intern Med",
    "JAMA Intern Med", "JAMA Netw Open", "Lancet Digit Health",
    "Lancet Diabetes Endocrinol", "Lancet Respir Med",
    "Eur Heart J", "Circulation", "J Am Coll Cardiol",
    "Sleep", "Sleep Med Rev", "J Clin Endocrinol Metab",
    "Hum Reprod", "Hum Reprod Update", "Fertil Steril", "Andrology", "Eur Urol",
    "Br J Sports Med", "Sports Med", "Am J Respir Crit Care Med",
]

# Per anomaly: the literature query, a curated fallback action (used when the
# LLM is unavailable — every citation here is already in the advisor's
# evidence base), and the point at which it needs a clinician.
TOPICS: Dict[str, Dict[str, Any]] = {
    "sleep_short": {
        "query": '("sleep deprivation"[ti] OR "sleep restriction"[ti] OR "short sleep"[ti] OR "sleep duration"[ti] OR "insufficient sleep"[ti])',
        "fallback": {
            "title": "Protect tonight's sleep window",
            "when": "Lights out 30 min earlier than usual tonight",
            "steps": [
                "No caffeine after 13:00 today",
                "Screens off and lights dimmed 60 min before bed",
                "Magnesium glycinate 400 mg 60 min before bed (already in your protocol)",
            ],
            "why": "One short night costs alertness today; repeated short sleep lowers "
                   "testosterone 10-15% (Leproult & Van Cauter 2011).",
        },
        "doctor_if": "short nights persist beyond two weeks despite a regular schedule, "
                     "or come with loud snoring or gasping",
    },
    "sleep_debt": {
        "query": '("sleep restriction"[ti] OR "sleep debt"[ti] OR "recovery sleep"[ti] OR "sleep loss"[ti] OR "sleep extension"[ti] OR "catch-up sleep"[ti])',
        "fallback": {
            "title": "Recovery night: 8 h in bed",
            "when": "In bed 60 min earlier tonight; no alarm if possible",
            "steps": [
                "20-minute nap before 15:00 if you can",
                "Skip hard training today — walk instead",
                "No caffeine after 13:00, no alcohol tonight",
            ],
            "why": "Two short nights compound: a week of 5 h sleep lowered daytime "
                   "testosterone 10-15% in healthy men (Leproult & Van Cauter 2011).",
        },
        "doctor_if": "you cannot sleep longer even when you have the time, for more than two weeks",
    },
    "hrv_low": {
        "query": '"heart rate variability"[ti]',
        "fallback": {
            "title": "Recovery day: easy movement only",
            "when": "Today; breathing 5 min before bed",
            "steps": [
                "Replace any hard session with a 30-min easy walk",
                "5 min slow breathing (about 6 breaths/min) before bed",
                "No alcohol tonight; hydrate through the afternoon",
            ],
            "why": "A low HRV night usually reflects incomplete recovery — short sleep, "
                   "alcohol, stress or an oncoming infection.",
        },
        "doctor_if": "HRV stays low for over a week together with fever, chest pain or palpitations",
    },
    "rhr_high": {
        "query": '("resting heart rate"[tiab]) AND (wearable*[tiab] OR infection[tiab] OR illness[tiab] OR recovery[tiab] OR sleep[tiab] OR mortality[tiab])',
        "fallback": {
            "title": "Check for illness; keep today easy",
            "when": "This morning, then tonight",
            "steps": [
                "Take your temperature; note any sore throat or aches",
                "Easy walk only — no intense training today",
                "Extra 500 ml water; no alcohol; in bed 30 min early",
            ],
            "why": "A resting heart rate well above your own baseline is an early sign "
                   "of infection, dehydration or under-recovery.",
        },
        "doctor_if": "resting HR stays 10+ bpm above normal for 3 days, or you have "
                     "chest pain, breathlessness or fever above 38.5 °C",
    },
    "deep_sleep_low": {
        "query": '("slow wave sleep"[tiab] OR "slow-wave sleep"[tiab] OR "deep sleep"[tiab] OR "sleep architecture"[ti])',
        "fallback": {
            "title": "Set up tonight for deep sleep",
            "when": "From 14:00 today",
            "steps": [
                "No caffeine after 13:00 and no alcohol tonight",
                "Bedroom at 18-19 °C",
                "Magnesium glycinate 400 mg 60 min before bed (already in your protocol)",
            ],
            "why": "Deep sleep is most sensitive to alcohol, late caffeine and a warm room "
                   "(magnesium: Boyle 2017).",
        },
        "doctor_if": "deep sleep stays low for weeks with unrefreshing sleep or daytime sleepiness",
    },
    "spo2_low": {
        "query": '("nocturnal hypoxemia"[tiab] OR "oxygen saturation"[ti] OR "oxygen desaturation"[tiab]) AND (sleep[tiab])',
        "fallback": {
            "title": "Re-check oxygen tonight, sleeping on your side",
            "when": "Tonight",
            "steps": [
                "Wear the Fitbit Air snugly, one finger above the wrist bone",
                "Sleep on your side; no alcohol tonight",
                "Note any snoring, gasping or morning headache",
            ],
            "why": "A single low reading is often a loose strap; repeated low nights "
                   "point to disordered breathing in sleep.",
        },
        "doctor_if": "SpO2 reads under 92% on 3 or more nights, or you wake gasping — "
                     "ask for a sleep study",
    },
    "temp_deviation": {
        "query": '("skin temperature"[tiab] OR "body temperature"[tiab]) AND (wearable*[tiab] OR "illness detection"[tiab] OR infection[tiab])',
        "fallback": {
            "title": "Treat today as a possible sick day",
            "when": "This morning",
            "steps": [
                "Take your temperature with a thermometer",
                "No training; rest and fluids",
                "Avoid any extra heat exposure (sauna, hot bath)",
            ],
            "why": "A temperature shift of 0.5 °C or more often precedes symptoms of "
                   "infection; fever itself depresses sperm production for weeks.",
        },
        "doctor_if": "fever above 38.5 °C, or any fever lasting more than 3 days",
    },
    "steps_low": {
        "query": '("daily steps"[tiab] OR "step count"[tiab] OR "steps per day"[tiab] OR "sedentary time"[tiab] OR "sedentary behaviour"[ti] OR "sedentary behavior"[ti])',
        "fallback": {
            "title": "Three 10-minute walks",
            "when": "After breakfast, lunch and dinner",
            "steps": [
                "10 minutes at a brisk pace after each meal",
                "Stand up for 2 minutes every hour of sitting",
            ],
            "why": "Moderate regular activity improves semen parameters in sedentary men "
                   "(Hajizadeh Maleki 2018) and steadies daytime energy.",
        },
        "doctor_if": "low activity is due to pain, breathlessness or unusual fatigue",
    },
}
TOPICS["hrv_drift"] = dict(TOPICS["hrv_low"])
TOPICS["rhr_drift"] = dict(TOPICS["rhr_high"])
TOPICS["readiness_low"] = dict(TOPICS["hrv_low"])


def _dir(config: SyncConfig) -> Path:
    d = Path(config.data_dir) / "anomalies"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(config: SyncConfig, day: date) -> Path:
    return _dir(config) / f"anomaly_{day.isoformat()}.json"


def load_anomaly_review(config: SyncConfig, day: date) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(_path(config, day).read_text())
    except (OSError, ValueError):
        return None


# ── Literature ──────────────────────────────────────────────────────────

def _journal_filter() -> str:
    return "(" + " OR ".join(f'"{j}"[ta]' for j in TOP_JOURNALS) + ")"


def fetch_top_journal_papers(config: SyncConfig, key: str) -> List[Dict[str, Any]]:
    """Recent top-journal papers (with abstracts) for one anomaly type."""
    topic = TOPICS.get(key)
    if not topic:
        return []
    from ..research.pubmed import fetch_abstracts, fetch_papers_for_query

    mailto = getattr(config, "openalex_mailto", None)
    papers = fetch_papers_for_query(
        f"({topic['query']}) AND {_journal_filter()}",
        since_months=60, retmax=PAPERS_PER_ANOMALY, mailto=mailto,
    )
    out = [
        {
            "pmid": p.work_id.replace("pmid:", ""), "title": p.title,
            "journal": p.journal, "date": p.publication_date or "", "url": p.url or "",
        }
        for p in papers
    ]
    try:
        abstracts = fetch_abstracts([p["pmid"] for p in out], mailto=mailto)
    except Exception as exc:  # noqa: BLE001 — titles are still worth having
        logger.warning(f"abstract fetch failed for {key}: {exc}")
        abstracts = {}
    for p in out:
        p["abstract"] = abstracts.get(p["pmid"], "")
    return out


# ── Corrective action ───────────────────────────────────────────────────

_SYSTEM = """You are a physician reviewing one morning's wearable anomalies for a single
patient: a man trying to conceive whose two goals are (1) fertility — sperm
motility and quality — and (2) daily energy.

For each anomaly you get his numbers against his own baseline, his last 14
days of watch data, and recent papers from top journals with abstracts.

Rules:
- Ground the action in the supplied abstracts where they apply and cite the
  paper you used as "FirstAuthorOrJournal Year" plus its PMID. If no supplied
  paper supports the action, set "citation" to "" — never invent a reference
  or an effect size.
- ONE corrective action per anomaly, doable today, with an exact time and 2-3
  concrete steps. Behavioural first (sleep timing, light, activity, caffeine,
  alcohol, heat). Supplements only if already common in a fertility protocol
  (magnesium glycinate, CoQ10, zinc, omega-3, vitamin D) — never a
  prescription drug, never a dose of one.
- Name the most likely causes given HIS recent data (max 3, most likely first).
- Say in one sentence what this anomaly means for energy and for fertility.
- "doctor_if": the specific sign that makes this a clinician's problem, written
  to complete the sentence "See a doctor if …" (start lowercase, one clause).
- Be brief: each cause under 15 words, "when" under 8 words (e.g. "Tonight,
  lights out 22:00"), each step under 15 words, "why" under 40 words. Plain
  text only — no markdown, no links.

Return ONLY a JSON array, one object per anomaly, in the order given:
[{"key": "...", "likely_causes": ["..."], "action": {"title": "...", "when":
"...", "steps": ["...", "..."]}, "why": "...", "citation": "...", "pmid": "...",
"goal_impact": "...", "doctor_if": "..."}]"""


def _recent_table(config: SyncConfig, today: date, days: int = 14) -> str:
    from .trend_analyzer import load_primary_wearable_history

    rows = ["date | sleep h | deep min | HRV ms | RHR bpm | SpO2 % | steps | active min"]
    for d in load_primary_wearable_history(config.data_dir, today, num_days=days):
        def g(k: str) -> str:
            v = d.get(k) or 0
            return f"{v:g}" if v else "-"
        rows.append(
            f"{d.get('date') or d.get('_date')} | {g('sleep_hours')} | {g('deep_sleep_min')} | "
            f"{g('hrv')} | {g('resting_hr')} | {g('spo2')} | {g('steps')} | {g('active_minutes')}"
        )
    return "\n".join(rows)


def _parse_json_array(text: str) -> List[Dict[str, Any]]:
    m = re.search(r"\[.*\]", text or "", re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return []
    return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []


def _plain(text: Any) -> str:
    """Model text for a phone line: no markdown links, no doubled lead-in."""
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", str(text or "")).strip()
    t = re.sub(r"^(see a doctor if|seek [a-z ]+? if)\s+", "", t, flags=re.IGNORECASE)
    return t.rstrip(".")


def _citation(text: Any) -> str:
    """Only the reference to a supplied paper: links the model added are dropped."""
    t = re.sub(r"\[[^\]]*\]\([^)]*\)", "", str(text or ""))
    t = re.sub(r"https?://\S+", "", t)
    return re.sub(r"[\s;,.]+$", "", " ".join(t.split()))


def _fallback_research(key: str) -> Dict[str, Any]:
    topic = TOPICS.get(key) or {}
    fb = topic.get("fallback") or {}
    return {
        "likely_causes": [],
        "action": {
            "title": fb.get("title", "Take today easy"),
            "when": fb.get("when", "Today"),
            "steps": list(fb.get("steps", [])),
        },
        "why": fb.get("why", ""), "citation": "", "pmid": "", "goal_impact": "",
        "doctor_if": topic.get("doctor_if", ""), "source": "curated",
    }


def _llm_research(
    config: SyncConfig, today: date, anomalies: List[Dict[str, Any]]
) -> Dict[str, Dict[str, Any]]:
    """One LLM call for all of the day's anomalies → {key: research}."""
    from .llm_client import generate, has_credentials

    if not anomalies or not has_credentials():
        return {}
    blocks = []
    for a in anomalies:
        lines = [f"### Anomaly key={a['key']} ({a['severity']}): {a['message']}"]
        for p in a.get("papers") or []:
            lines.append(
                f"- PMID {p['pmid']} — {p['title']} ({p['journal']}, {p['date']})\n"
                f"  Abstract: {p.get('abstract') or '(not available)'}"
            )
        if not a.get("papers"):
            lines.append("- (no top-journal papers retrieved — leave citation empty)")
        blocks.append("\n".join(lines))
    user = (
        f"Date: {today.isoformat()}\n\n## Last 14 days\n{_recent_table(config, today)}\n\n"
        "## Anomalies and literature\n" + "\n\n".join(blocks)
    )
    try:
        text = generate(
            system=_SYSTEM, user=user, max_output_tokens=1400, reasoning="medium", timeout_s=300,
        )
    except Exception as exc:  # noqa: BLE001 — fall back to the curated action
        logger.warning(f"anomaly research LLM failed: {exc}")
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for item in _parse_json_array(text):
        action = item.get("action") or {}
        if not item.get("key") or not isinstance(action, dict) or not action.get("title"):
            continue
        steps = action.get("steps") or []
        out[str(item["key"])] = {
            "likely_causes": [str(c) for c in (item.get("likely_causes") or [])][:3],
            "action": {
                "title": str(action.get("title", "")).strip(),
                "when": str(action.get("when", "")).strip(),
                "steps": [str(s).strip() for s in steps if str(s).strip()][:3],
            },
            "why": str(item.get("why", "")).strip(),
            "citation": _citation(item.get("citation", "")),
            "pmid": re.sub(r"\D", "", str(item.get("pmid", ""))),
            "goal_impact": str(item.get("goal_impact", "")).strip(),
            "doctor_if": _plain(item.get("doctor_if", "")),
            "source": "llm",
        }
    return out


def _recent_research(config: SyncConfig, today: date, key: str) -> Optional[Dict[str, Any]]:
    """Research for the same anomaly from the last few days, if any."""
    for back in range(1, REUSE_DAYS + 1):
        prev = load_anomaly_review(config, today - timedelta(days=back))
        for a in (prev or {}).get("anomalies") or []:
            if a.get("key") == key and (a.get("research") or {}).get("source") == "llm":
                return {"papers": a.get("papers") or [], "research": dict(a["research"])}
    return None


def build_anomaly_review(config: SyncConfig, today: date) -> Dict[str, Any]:
    """Detect, research and attach a corrective action. Saves and returns the review."""
    from .anomaly_detector import find_anomalies

    anomalies = find_anomalies(config, today)
    fresh: List[Dict[str, Any]] = []
    for a in anomalies[:MAX_RESEARCHED]:
        reused = _recent_research(config, today, a["key"])
        if reused:
            a["papers"], a["research"] = reused["papers"], reused["research"]
            a["research"]["reused"] = True
            continue
        try:
            a["papers"] = fetch_top_journal_papers(config, a["key"])
        except Exception as exc:  # noqa: BLE001 — PubMed down ≠ no advice
            logger.warning(f"literature search failed for {a['key']}: {exc}")
            a["papers"] = []
        fresh.append(a)

    researched = _llm_research(config, today, fresh)
    for a in anomalies:
        if "research" in a:
            continue
        a["research"] = researched.get(a["key"]) or _fallback_research(a["key"])
        a.setdefault("papers", [])
        topic = TOPICS.get(a["key"]) or {}
        if not a["research"].get("doctor_if"):
            a["research"]["doctor_if"] = topic.get("doctor_if", "")
        # A citation must point at a paper we actually retrieved.
        pmids = {p["pmid"] for p in a["papers"]}
        if a["research"].get("pmid") and a["research"]["pmid"] not in pmids:
            a["research"]["pmid"] = ""
            a["research"]["citation"] = ""

    review = {
        "date": today.isoformat(),
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "anomalies": anomalies,
    }
    _path(config, today).write_text(json.dumps(review, indent=2, ensure_ascii=False))
    return review


# ── Rendering ───────────────────────────────────────────────────────────

def _paper_url(a: Dict[str, Any]) -> str:
    pmid = (a.get("research") or {}).get("pmid")
    return f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else ""


def render_alert(review: Dict[str, Any], only_alerts: bool = True) -> str:
    """Phone message for the day's anomalies ('' when there is nothing to send)."""
    items = [
        a for a in review.get("anomalies") or []
        if not only_alerts or a.get("severity") == "alert"
    ]
    if not items:
        return ""
    lines = [f"⚡ Health anomaly — {review.get('date')}"]
    for a in items:
        r = a.get("research") or {}
        act = r.get("action") or {}
        lines.append("")
        lines.append(a["message"])
        if r.get("likely_causes"):
            lines.append("Likely: " + "; ".join(r["likely_causes"]))
        if act.get("title"):
            when = f" — {act['when']}" if act.get("when") else ""
            lines.append(f"→ Do today: {act['title']}{when}")
            for s in act.get("steps") or []:
                lines.append(f"   • {s}")
        if r.get("why"):
            lines.append(f"Why: {r['why']}")
        if r.get("citation"):
            lines.append(f"📚 {r['citation']} {_paper_url(a)}".rstrip())
        if r.get("doctor_if"):
            lines.append(f"🩺 See a doctor if {r['doctor_if']}")
    return "\n".join(lines)[:3900]


def digest_lines(config: SyncConfig, day: date) -> List[str]:
    """One compact line per anomaly for the 08:00 phone digest."""
    review = load_anomaly_review(config, day)
    out: List[str] = []
    for a in (review or {}).get("anomalies") or []:
        act = ((a.get("research") or {}).get("action") or {}).get("title")
        out.append(a["message"] + (f" → {act}" if act else ""))
    return out[:3]


def advisor_section(config: SyncConfig, day: date) -> str:
    """Prompt block for the daily advisor ('' when the day is unremarkable)."""
    review = load_anomaly_review(config, day)
    if review is None:
        # The 07:41 job did not run (or failed): detect now, without research.
        from .anomaly_detector import find_anomalies

        try:
            review = {"anomalies": find_anomalies(config, day)}
        except Exception:
            return ""
    items = review.get("anomalies") or []
    if not items:
        return ""
    lines = ["## ⚠️ Anomalies in today's watch data (vs his own 28-day baseline)"]
    for a in items:
        r = a.get("research") or {}
        act = r.get("action") or {}
        lines.append(f"- [{a['severity']}] {a['message']}")
        if r.get("likely_causes"):
            lines.append(f"  Likely causes: {'; '.join(r['likely_causes'])}")
        if act.get("title"):
            lines.append(
                f"  Researched corrective action: {act['title']} ({act.get('when', '')}) — "
                + "; ".join(act.get("steps") or [])
            )
        if r.get("citation"):
            lines.append(f"  Evidence: {r['citation']} {_paper_url(a)}".rstrip())
        if r.get("doctor_if"):
            lines.append(f"  See a doctor if {r['doctor_if']}")
    lines.append(
        "\nAction #1 of today's Top 3 MUST be the corrective action for the first "
        "anomaly above (adapt the timing to today), citing its evidence if given."
    )
    return "\n".join(lines)


def run_anomaly_review(config: SyncConfig, today: date) -> Dict[str, Any]:
    """Build the review and send the alert-level findings to the phone."""
    review = build_anomaly_review(config, today)
    review["alert_sent"] = False
    message = render_alert(review)
    if message:
        try:
            from .whatsapp_sender import _run_openclaw_send

            review["alert_sent"] = bool(_run_openclaw_send(message))
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"anomaly alert send failed: {exc}")
        _path(config, today).write_text(json.dumps(review, indent=2, ensure_ascii=False))
    return review


if __name__ == "__main__":
    from .config import load_config

    cfg = load_config()
    day = datetime.now(tz=cfg.timezone).date()
    result = build_anomaly_review(cfg, day)  # build only — no message is sent
    print(render_alert(result, only_alerts=False) or f"No anomalies on {day.isoformat()}.")
