"""Weekly journal review — the reading layer of the Health OS.

The daily research sync (07:20) fetches papers from PubMed topic queries and
top-journal OpenAlex seeds, but its per-day output is mechanical: one canned
action per goal, impact guessed from citation count, no judgment. This module
adds the judgment: once a week it collects everything fetched over the last
seven days, puts it next to the user's actual biomarker trajectory, and asks
the LLM to act as a physician-scientist reviewing the week's literature for
THIS patient — what deserves a full read, what (if anything) should change in
the protocol, what is noise.

Honesty constraint baked into the prompt: we store titles/journals/dates, not
abstracts, so the model is told to treat titles as leads, lean on its own
knowledge of the cited literature, and flag uncertainty rather than invent
findings.

Output: data/ingested/research/journal_watch_<date>.md (+ .json meta), picked
up by the weekly Health OS brief. Runs standalone too:
    .venv/bin/python -m app.research.journal_watch
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List

from ..sync.config import SyncConfig

_SYSTEM = """You are a physician-scientist doing a weekly literature review for one
specific patient. You are rigorous about evidence quality and allergic to
overclaiming.

Patient context (stable): male, primary goals are (1) FERTILITY — sperm count,
motility, morphology and vitality are severely below WHO 2021 references and
declining; next spermogram ~2026-08-22; heat avoidance + antioxidant/zinc/
folate protocol running; AMPD1 carrier (hard exercise is disproportionately
costly), FSHR Asn/Ser intermediate — and (2) ENERGY — HRV, sleep, readiness.
Wearable: Fitbit Air, currently activity-only (no HRV/sleep data flowing).

You are given paper TITLES with journal/date/citations, not abstracts. Treat
titles as leads: where you know the underlying study or literature, say what it
found; where you don't, say "worth a read" and do not invent results. Never
fabricate effect sizes.

Write markdown with EXACTLY these sections:
## This week in the journals
3-6 bullets. Each: **finding or lead** — why it matters for THIS patient, with
the paper title + journal in parentheses. Skip anything irrelevant to his two
goals; if a famous-journal paper is irrelevant, it does not belong here.
## Protocol implications
0-3 numbered, concrete, small changes worth considering (dose, timing, a test
to add). If the week's crop justifies no change, write exactly "No change
justified this week." and one sentence why.
## Reading list
1-3 papers worth the full text, one line each: title (journal) — URL if given.
Total under 350 words. No preamble, no disclaimers."""


def _research_dir(data_dir: Path) -> Path:
    d = data_dir / "research"
    d.mkdir(parents=True, exist_ok=True)
    return d


def collect_week_papers(
    config: SyncConfig, day: date, days: int = 7
) -> List[Dict[str, Any]]:
    """All papers fetched over the trailing week, deduped by work_id."""
    seen: set[str] = set()
    papers: List[Dict[str, Any]] = []
    base = _research_dir(config.data_dir)
    for i in range(days):
        d = day - timedelta(days=i)
        path = base / f"papers_{d.isoformat()}.json"
        if not path.exists():
            continue
        try:
            day_papers = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        for p in day_papers:
            wid = p.get("work_id")
            if not wid or wid in seen:
                continue
            seen.add(wid)
            papers.append(p)
    # Most-cited first so prompt truncation drops the weakest signal.
    papers.sort(key=lambda p: -(p.get("cited_by_count") or 0))
    return papers


def _biomarker_context(config: SyncConfig) -> str:
    try:
        from ..sync.biomarker_trends import summarize_for_advisor

        rows = summarize_for_advisor(config, top_n=8)
        lines = []
        for r in rows:
            lines.append(
                f"- {r.get('name')}: {r.get('first_value')} → {r.get('last_value')} "
                f"{r.get('unit') or ''} ({r.get('direction')}, ref {r.get('ref_source')})"
            )
        return "\n".join(lines)
    except Exception:
        return "(biomarker summary unavailable)"


def run_journal_watch(config: SyncConfig, day: date) -> Dict[str, Any]:
    """Produce this week's review. Returns {date, markdown, papers_reviewed, model}."""
    papers = collect_week_papers(config, day)

    if not papers:
        markdown = (
            "## This week in the journals\n"
            "No papers were fetched this week — the 07:20 research sync may be "
            "failing; check its logs.\n"
        )
        result = {
            "date": day.isoformat(), "markdown": markdown,
            "papers_reviewed": 0, "model": "no-papers",
        }
        _save(config, day, result)
        return result

    paper_lines = [
        f"- {p.get('title')} ({p.get('journal')}, {p.get('publication_date') or 'n.d.'}"
        + (f", cited {p['cited_by_count']}" if p.get("cited_by_count") else "")
        + (f") {p.get('url')}" if p.get("url") else ")")
        for p in papers[:40]
    ]
    user_prompt = (
        f"Week ending {day.isoformat()}. Patient's current biomarker trajectory:\n"
        f"{_biomarker_context(config)}\n\n"
        f"Papers fetched this week ({len(papers)} total, top {len(paper_lines)} shown):\n"
        + "\n".join(paper_lines)
    )

    from ..sync.daily_advisor import advisor_has_credentials

    if advisor_has_credentials(config):
        try:
            from ..sync.llm_client import generate

            markdown = generate(
                system=_SYSTEM, user=user_prompt, reasoning="medium", timeout_s=600,
            )
            model = "llm"
        except Exception as exc:
            print(f"Journal watch LLM failed, falling back to listing: {exc}")
            markdown, model = _fallback_markdown(papers), "fallback-listing"
    else:
        markdown, model = _fallback_markdown(papers), "fallback-listing"

    result = {
        "date": day.isoformat(), "markdown": markdown,
        "papers_reviewed": len(papers), "model": model,
    }
    _save(config, day, result)
    return result


def _fallback_markdown(papers: List[Dict[str, Any]]) -> str:
    """No-LLM degradation: a plain most-cited reading list beats silence."""
    lines = ["## This week in the journals (no-LLM listing)"]
    for p in papers[:8]:
        lines.append(
            f"- {p.get('title')} ({p.get('journal')})"
            + (f" — {p.get('url')}" if p.get("url") else "")
        )
    return "\n".join(lines)


def _save(config: SyncConfig, day: date, result: Dict[str, Any]) -> None:
    base = _research_dir(config.data_dir)
    (base / f"journal_watch_{day.isoformat()}.md").write_text(result["markdown"])
    (base / f"journal_watch_{day.isoformat()}.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False)
    )
    (base / "journal_watch_latest.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False)
    )


def load_latest_journal_watch(config: SyncConfig) -> Dict[str, Any] | None:
    path = _research_dir(config.data_dir) / "journal_watch_latest.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


if __name__ == "__main__":
    from ..sync.config import load_config

    cfg = load_config()
    out = run_journal_watch(cfg, date.today())
    print(f"model={out['model']} papers={out['papers_reviewed']}\n")
    print(out["markdown"])
