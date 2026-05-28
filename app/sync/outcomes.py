"""Cross-test outcome intelligence — "what changed and what correlates."

The agent generated advice for months but never told the user whether any of
it worked. This module closes that gap. For every marker with ≥2 readings it
computes the move between the two most recent tests, classifies it against the
WHO / optimal band, ties the interval to the adherence + interventions the
user actually did, and asks the LLM (Codex GPT-5.5) to synthesize a short
progress note.

Public API:
  build_progress(config, kinds=None) -> dict
      Compute the full progress payload (deltas + window + narrative) and
      persist it to data/ingested/outcomes/progress_<date>.json.
  latest_progress(config) -> dict | None
      Load the most recent saved progress payload.
  render_whatsapp_note(progress) -> str
      Short text suitable for a WhatsApp/Telegram push.

The heavy lifting reuses:
  - biomarker_trends.prev_vs_new()  — two-most-recent delta per marker
  - action_effects.compute_action_effects()  — adherence→metric correlations
  - llm_client.generate(reasoning="high")  — narrative synthesis
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import SyncConfig

logger = logging.getLogger("personal-doctor.outcomes")


def _outcomes_dir(data_dir: Path) -> Path:
    d = data_dir / "outcomes"
    d.mkdir(parents=True, exist_ok=True)
    return d


_PROGRESS_SYSTEM = """\
You are the patient's longitudinal health analyst. You are handed:
  - Per-marker deltas between their two most recent lab/sperm tests
  - The adherence + intervention correlations over the interval
The patient's goals are sperm quality (fertility) and daily energy.

Write a single progress note, <=170 words, plain prose (no markdown headers).
Rules:
  - Lead with the most important MOVE (biggest clinically-relevant change).
  - Always cite the actual numbers and the WHO/optimal context
    (e.g. "total motility 5% -> 26%, still below WHO 42%").
  - If an adherence correlation plausibly explains a change, say so explicitly
    but hedge honestly ("may be linked to", not "caused by").
  - Name 1-2 concrete next steps tied to whatever is still out of range.
  - No disclaimers, no "consult your doctor". Talk like a sharp clinician
    who knows this patient's history.
"""


def build_progress(
    config: SyncConfig, kinds: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Compute + persist the cross-test progress payload.

    ``kinds`` optionally restricts to marker source_kinds (e.g. ["sperm_test"]
    when a new spermogram lands). Default: all markers with >=2 readings.
    """
    from .biomarker_trends import key_biomarkers, prev_vs_new

    marker_ids = key_biomarkers(config, min_points=2, kinds=kinds)
    deltas: List[Dict[str, Any]] = []
    for mid in marker_ids:
        pv = prev_vs_new(config, mid)
        if pv:
            deltas.append(pv)

    # Rank: status changes first, then biggest |pct_change|.
    def _rank(d: Dict[str, Any]) -> tuple:
        tier = 0 if d.get("status_change") else 1
        return (tier, -abs(d.get("pct_change") or 0))

    deltas.sort(key=_rank)

    # Adherence / intervention correlations over the recent window.
    effects: List[Dict[str, Any]] = []
    try:
        from .action_effects import compute_action_effects

        effects = compute_action_effects(
            config.data_dir, date.today(), lookback_days=90
        )
    except Exception as exc:
        logger.warning(f"action_effects unavailable: {exc}")

    narrative = _synthesize_narrative(deltas, effects)

    payload = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "date": date.today().isoformat(),
        "kinds": kinds or "all",
        "deltas": deltas,
        "effects": effects[:6],
        "narrative": narrative,
    }

    try:
        out = _outcomes_dir(config.data_dir) / f"progress_{payload['date']}.json"
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    except Exception as exc:
        logger.warning(f"failed to persist progress: {exc}")

    return payload


def _synthesize_narrative(
    deltas: List[Dict[str, Any]], effects: List[Dict[str, Any]],
) -> str:
    """One LLM call → a <=170-word progress note. Empty string on failure."""
    if not deltas:
        return ""
    from .llm_client import generate as llm_generate
    from .llm_client import has_credentials

    if not has_credentials():
        # Deterministic fallback so the feature degrades gracefully.
        top = deltas[0]
        return (
            f"Since your last test, {top['name']} moved "
            f"{top['prev_value']:g} → {top['new_value']:g} {top['unit']} "
            f"({top['pct_change']:+.0f}%, {top['direction']})."
        )

    delta_lines = []
    for d in deltas[:10]:
        sc = f" [{d['status_change']}]" if d.get("status_change") else ""
        flag = f" ({d['new_flagged']})" if d.get("new_flagged") in ("low", "high") else ""
        delta_lines.append(
            f"- {d['name']}: {d['prev_value']:g} → {d['new_value']:g} {d['unit']} "
            f"({d['pct_change']:+.0f}%, {d['direction']}){flag}{sc} "
            f"[{d['prev_date']} → {d['new_date']}]"
        )
    effect_lines = [
        f"- {e['action']}: {e['metric']} {e['delta']} "
        f"({e['days_done']}d done vs {e['days_skipped']}d skipped)"
        for e in effects[:6]
    ]
    user = (
        "Marker deltas (two most recent tests):\n"
        + "\n".join(delta_lines)
        + "\n\nAdherence correlations over the window:\n"
        + ("\n".join(effect_lines) if effect_lines else "(no adherence data yet)")
    )
    try:
        return llm_generate(
            system=_PROGRESS_SYSTEM, user=user,
            max_output_tokens=400, reasoning="high", timeout_s=600,
        ).strip()
    except Exception as exc:
        logger.warning(f"narrative synthesis failed: {exc}")
        top = deltas[0]
        return (
            f"Since your last test, {top['name']} moved "
            f"{top['prev_value']:g} → {top['new_value']:g} {top['unit']} "
            f"({top['pct_change']:+.0f}%, {top['direction']})."
        )


def latest_progress(config: SyncConfig) -> Optional[Dict[str, Any]]:
    """Load the most recent persisted progress payload, or None."""
    d = _outcomes_dir(config.data_dir)
    files = sorted(d.glob("progress_*.json"), reverse=True)
    if not files:
        return None
    try:
        return json.loads(files[0].read_text())
    except Exception:
        return None


def render_whatsapp_note(progress: Dict[str, Any]) -> str:
    """Short push text for WhatsApp/Telegram from a progress payload."""
    if not progress:
        return ""
    narrative = progress.get("narrative") or ""
    head = "📈 Since your last test\n\n"
    return (head + narrative).strip()


# ── HTML surfaces ─────────────────────────────────────────────────────────

from html import escape as _esc  # noqa: E402


def _delta_row_html(d: Dict[str, Any]) -> str:
    """One marker's prev→new row for the /outcomes page + email block."""
    direction = d.get("direction", "stable")
    color = {"improving": "#059669", "declining": "#dc2626"}.get(direction, "#6b7280")
    arrow = {"improving": "&#x2197;", "declining": "&#x2198;", "stable": "&#x2194;"}.get(direction, "")
    flag = d.get("new_flagged")
    flag_badge = ""
    if flag in ("low", "high"):
        flag_badge = (
            f'<span style="background:#fee2e2;color:#991b1b;border-radius:9999px;'
            f'padding:1px 7px;font-size:11px;font-weight:600;margin-left:6px;">'
            f'{flag} vs {_esc(str(d.get("ref_source","ref")))}</span>'
        )
    elif flag == "optimal":
        flag_badge = (
            '<span style="background:#dcfce7;color:#166534;border-radius:9999px;'
            'padding:1px 7px;font-size:11px;font-weight:600;margin-left:6px;">optimal</span>'
        )
    status_change = d.get("status_change") or ""
    sc_html = (
        f'<span style="color:#92400e;font-size:11px;margin-left:6px;">({_esc(status_change)})</span>'
        if status_change else ""
    )
    return (
        '<div style="border:1px solid #e5e7eb;border-radius:8px;padding:10px 12px;'
        'margin-bottom:8px;background:#fff;">'
        f'<div style="font-size:13px;font-weight:700;color:#1f2937;">'
        f'{_esc(d.get("name",""))}{flag_badge}{sc_html}</div>'
        f'<div style="font-size:14px;margin-top:3px;">'
        f'<strong>{d.get("prev_value")!s} &rarr; {d.get("new_value")!s}</strong> '
        f'<span style="color:#6b7280;">{_esc(d.get("unit",""))}</span> '
        f'<span style="color:{color};font-weight:600;">{arrow} {direction} '
        f'({d.get("pct_change"):+.0f}%)</span></div>'
        f'<div style="font-size:11px;color:#9ca3af;margin-top:2px;">'
        f'{_esc(str(d.get("prev_date","")))} &rarr; {_esc(str(d.get("new_date","")))}</div>'
        '</div>'
    )


def render_outcomes_email_block(progress: Dict[str, Any]) -> str:
    """An "📈 Since your last test" HTML block for the daily email.

    Returns "" if no progress or no recent test (caller decides recency).
    """
    if not progress or not progress.get("deltas"):
        return ""
    narrative = progress.get("narrative") or ""
    rows = "".join(_delta_row_html(d) for d in progress["deltas"][:8])
    narrative_html = (
        f'<div style="font-size:13px;color:#374151;line-height:1.55;'
        f'margin-bottom:12px;">{_esc(narrative)}</div>' if narrative else ""
    )
    return (
        '<div style="margin-top:28px;padding:16px 20px;background:#f0fdf4;'
        'border-radius:12px;border:1px solid #86efac;">'
        '<h3 style="color:#059669;margin:0 0 10px 0;font-size:16px;">'
        '&#x1F4C8; Since your last test</h3>'
        f'{narrative_html}{rows}'
        '</div>'
    )


def render_outcomes_page(config: SyncConfig) -> str:
    """Full /outcomes HTML page: narrative + every prev→new marker card."""
    progress = latest_progress(config)
    if not progress:
        # Build it on demand if nothing persisted yet.
        try:
            progress = build_progress(config)
        except Exception:
            progress = None
    if not progress or not progress.get("deltas"):
        return (
            '<!DOCTYPE html><html><head><meta charset="utf-8">'
            '<title>Outcomes</title></head><body style="font-family:-apple-system;'
            'max-width:760px;margin:0 auto;padding:30px;color:#1a1a1a;">'
            '<h1>📈 Outcomes</h1><p>Need at least two tests of the same marker to '
            'show a change. Upload another lab/spermogram and this will populate.</p>'
            '</body></html>'
        )
    narrative = progress.get("narrative") or ""
    rows = "".join(_delta_row_html(d) for d in progress["deltas"])
    effects = progress.get("effects") or []
    eff_html = ""
    if effects:
        eff_rows = "".join(
            f'<li style="margin:3px 0;">{_esc(e["action"])}: '
            f'<strong>{_esc(e["metric"])} {_esc(e["delta"])}</strong> '
            f'({e["days_done"]}d done vs {e["days_skipped"]}d skipped)</li>'
            for e in effects
        )
        eff_html = (
            '<h2 style="color:#1e40af;margin-top:28px;">What moved your metrics</h2>'
            f'<ul style="font-size:13px;color:#374151;">{eff_rows}</ul>'
        )
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Outcomes — Since your last test</title></head>'
        '<body style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;'
        'max-width:760px;margin:0 auto;padding:24px;color:#1a1a1a;background:#fafafa;">'
        '<h1 style="color:#059669;">📈 Since your last test</h1>'
        f'<div style="font-size:14px;color:#374151;line-height:1.6;'
        f'background:#fff;border-radius:10px;padding:16px 18px;border:1px solid #d1fae5;'
        f'margin-bottom:20px;">{_esc(narrative)}</div>'
        f'{rows}{eff_html}'
        f'<div style="margin-top:28px;font-size:12px;color:#9ca3af;">'
        f'Generated {_esc(str(progress.get("generated_at","")))} · '
        f'<a href="/biomarkers" style="color:#2563eb;">All biomarker charts &rarr;</a></div>'
        '</body></html>'
    )
