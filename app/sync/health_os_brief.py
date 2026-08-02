"""The weekly Health OS brief — one document, every pillar.

The daily digest answers "what do I do today?". This answers the questions a
daily view structurally cannot: is the protocol working, what did the
literature say, what is owed to me, what have I not booked, and what is the
system itself failing to measure.

Assembled from parts that each degrade independently — any section that can't
be built is skipped with a note rather than failing the brief. Delivery is
email + WhatsApp, Sunday 18:30 (after the 18:00 retro).
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

from .config import SyncConfig


def _brief_dir(config: SyncConfig) -> Path:
    d = config.data_dir / "health_os"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe(label: str, fn) -> str:
    """Run a section builder; never let one broken pillar kill the brief."""
    try:
        return fn() or ""
    except Exception as exc:  # noqa: BLE001 — a brief with 4/5 sections beats none
        return f"## {label}\n_(section unavailable: {exc})_"


def _goal_status(config: SyncConfig, today: date) -> str:
    """Where the two goals actually stand, stated plainly."""
    lines = ["## 🎯 Goal status"]
    try:
        from .biomarker_trends import summarize_for_advisor

        rows = summarize_for_advisor(config, top_n=12)
        semen = [r for r in rows if r.get("category") == "semen"]
        if semen:
            lines.append("**Fertility** (WHO 2021 references)")
            for r in semen[:5]:
                arrow = {"declining": "↓", "improving": "↑"}.get(r.get("direction"), "→")
                lines.append(
                    f"- {r.get('name')}: {r.get('first_value')} → "
                    f"**{r.get('last_value')}{r.get('unit') or ''}** {arrow} "
                    f"({r.get('last_flagged') or 'n/a'})"
                )
    except Exception as exc:
        lines.append(f"_(biomarkers unavailable: {exc})_")

    # Energy: say plainly when the data isn't there rather than implying health.
    try:
        from .trend_analyzer import compute_rolling_averages, load_primary_wearable_history

        hist = load_primary_wearable_history(config.data_dir, today, num_days=7)
        avg = compute_rolling_averages(hist) or {}
        measured = {
            k: v for k, v in avg.items()
            if isinstance(v, (int, float)) and v
        }
        lines.append("")
        lines.append("**Energy** (7-day averages)")
        if measured:
            for k, v in list(measured.items())[:6]:
                label = k.replace("avg_", "").replace("_", " ")
                shown = f"{v:,.0f}" if abs(v) >= 100 else f"{v:.1f}"
                lines.append(f"- {label}: {shown}")
        recovery_keys = ("avg_hrv", "avg_sleep_hours", "avg_resting_hr")
        if not any(measured.get(k) for k in recovery_keys):
            lines.append(
                "- ⚠️ **No recovery data.** HRV, sleep and resting HR are all "
                "unmeasured — the Fitbit Air sends activity only. Energy cannot "
                "be assessed until a recovery source is connected."
            )
    except Exception as exc:
        lines.append(f"_(wearable trends unavailable: {exc})_")
    return "\n".join(lines)


def _system_health(config: SyncConfig, today: date) -> str:
    """What the OS itself is failing to do — the part no other section owns."""
    lines = ["## ⚙️ System health"]
    issues: List[str] = []

    try:
        from .pipeline import fitbit_data_is_fresh
        from .storage import load_wearable_payload_file

        fresh_days = 0
        for i in range(7):
            d = date.fromordinal(today.toordinal() - i)
            try:
                p = load_wearable_payload_file(
                    config.data_dir, d.isoformat(), source="fitbit"
                )
            except Exception:
                continue
            if p and fitbit_data_is_fresh(p):
                fresh_days += 1
        lines.append(f"- Wearable sync: {fresh_days}/7 days with activity data")
        if fresh_days < 5:
            issues.append("wearable sync degraded")
    except Exception:
        lines.append("- Wearable sync: unknown")

    # Recovery-data gap is a standing, unresolved system failure — keep it
    # visible every week until it is actually fixed.
    issues.append(
        "no HRV/sleep/resting-HR reaching the system (Health Connect → Google "
        "Fit relays activity only)"
    )

    try:
        from ..research.journal_watch import load_latest_journal_watch

        jw = load_latest_journal_watch(config)
        if jw:
            lines.append(
                f"- Journal watch: {jw.get('papers_reviewed', 0)} papers reviewed "
                f"({jw.get('date')})"
            )
    except Exception:
        pass

    if issues:
        lines.append("")
        lines.append("**Open issues**")
        for i in issues:
            lines.append(f"- {i}")
    return "\n".join(lines)


def build_brief(config: SyncConfig, today: date) -> Dict[str, Any]:
    """Assemble the full weekly brief. Returns {date, markdown, sections}."""
    from ..admin.claims import render_claims_summary
    from ..care.providers import render_care_summary, render_lab_slip
    from ..research.journal_watch import load_latest_journal_watch

    def _journal() -> str:
        jw = load_latest_journal_watch(config)
        if not jw or not jw.get("markdown"):
            return ""
        return jw["markdown"]

    sections = [
        ("Goal status", lambda: _goal_status(config, today)),
        ("Journal watch", _journal),
        ("Appointments", lambda: render_care_summary(config, today)),
        ("Lab slip", lambda: render_lab_slip(config, today)),
        ("Reimbursements", lambda: render_claims_summary(config, today)),
        ("System health", lambda: _system_health(config, today)),
    ]

    parts = [f"# Health OS — week of {today.isoformat()}", ""]
    built: Dict[str, bool] = {}
    for label, fn in sections:
        block = _safe(label, fn)
        built[label] = bool(block.strip())
        if block.strip():
            parts.append(block)
            parts.append("")

    markdown = "\n".join(parts).rstrip() + "\n"
    result = {
        "date": today.isoformat(),
        "markdown": markdown,
        "sections": built,
    }
    path = _brief_dir(config) / f"brief_{today.isoformat()}.md"
    path.write_text(markdown)
    (_brief_dir(config) / "latest.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False)
    )
    return result


def _to_whatsapp(markdown: str, limit: int = 3500) -> str:
    """Action-first phone version.

    A naive markdown dump truncates mid-prose and loses exactly the sections
    that need a decision (appointments, claims). So the phone version keeps
    whole sections in priority order and drops the prose-heavy ones — the full
    brief is in the email either way.
    """
    # Split into (heading, [lines]) blocks.
    blocks: List[tuple[str, List[str]]] = []
    current: tuple[str, List[str]] | None = None
    for line in markdown.splitlines():
        s = line.rstrip()
        if s.startswith("## "):
            if current:
                blocks.append(current)
            current = (s[3:].strip(), [])
        elif current is not None:
            current[1].append(s)
    if current:
        blocks.append(current)

    # Decision-relevant first; journal prose last (it's a read, not an action).
    priority = [
        "🎯 Goal status",
        "🏥 Appointments to book",
        "💶 Reimbursements",
        "🧾 Lab slip (ordonnance à demander)",
        "⚙️ System health",
        "Protocol implications",
    ]
    def rank(name: str) -> int:
        for i, p in enumerate(priority):
            if name.startswith(p) or p.startswith(name):
                return i
        return len(priority) + 1

    ordered = sorted(blocks, key=lambda b: rank(b[0]))

    out: List[str] = [f"*Health OS — {markdown.splitlines()[0].split('week of ')[-1]}*"]
    for name, lines in ordered:
        body = [
            l.strip().replace("**", "*")
            for l in lines
            if l.strip() and not l.strip().startswith("http")
        ]
        if not body:
            continue
        chunk = "\n".join([f"\n*{name}*"] + body[:9])
        if len("\n".join(out)) + len(chunk) > limit - 80:
            break
        out.append(chunk)
    out.append("\n📧 Full brief with links in your email.")
    text = "\n".join(out)
    return text[: limit - 3] + "..." if len(text) > limit else text


def send_brief(config: SyncConfig, result: Dict[str, Any]) -> Dict[str, bool]:
    """Deliver the brief by email + WhatsApp. Best-effort on each channel."""
    delivered = {"email": False, "whatsapp": False}
    day = result["date"]
    markdown = result["markdown"]

    try:
        from .whatsapp_sender import send_via_email_fallback

        delivered["email"] = send_via_email_fallback(
            config, f"Health OS — week of {day}", markdown
        )
    except Exception as exc:
        print(f"Health OS brief email failed: {exc}")

    try:
        from .whatsapp_sender import _run_openclaw_send

        delivered["whatsapp"] = _run_openclaw_send(_to_whatsapp(markdown))
    except Exception as exc:
        print(f"Health OS brief WhatsApp failed: {exc}")

    return delivered


def run_health_os_brief(config: SyncConfig, today: date) -> Dict[str, Any]:
    """Build, draft any pending claims, and deliver. Called by the scheduler."""
    # Draft claims first so the brief can list what is waiting for approval.
    try:
        from ..admin.claims import build_pending_claims

        new_drafts = build_pending_claims(config, today)
        if new_drafts:
            print(f"Drafted {len(new_drafts)} claim(s) awaiting approval.")
    except Exception as exc:
        print(f"Claim drafting failed (non-fatal): {exc}")

    result = build_brief(config, today)
    result["delivered"] = send_brief(config, result)
    return result


if __name__ == "__main__":
    from .config import load_config

    cfg = load_config()
    out = build_brief(cfg, date.today())
    print(out["markdown"])
