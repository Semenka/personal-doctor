"""Send daily health advice via email (SMTP)."""
from __future__ import annotations

import re
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List

from .config import SyncConfig


def _build_best_mover_html(config: SyncConfig, day: str) -> str:
    """Surface the single highest-impact action at the top of the email (F9).

    Pulls the top entry from `compute_action_effects` and highlights it. If the
    user has never completed anything yet, returns an empty string so the email
    stays clean.
    """
    try:
        from datetime import date as date_type

        from .action_effects import compute_action_effects

        effects = compute_action_effects(
            config.data_dir, date_type.fromisoformat(day), lookback_days=14
        )
    except Exception:
        return ""

    if not effects:
        return ""

    # Pick the best beneficial effect (positive impact with largest absolute delta)
    positive = [e for e in effects if e.get("impact", 0) > 0]
    if not positive:
        return ""
    best = positive[0]

    arrow = "&#x2B06;"
    return (
        '<div style="margin:0 0 20px 0;padding:14px 18px;background:#fef3c7;'
        'border-left:4px solid #f59e0b;border-radius:6px;">'
        '<div style="font-size:12px;font-weight:700;color:#92400e;'
        'text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">'
        'Your highest-impact action</div>'
        f'<div style="font-size:15px;color:#1f2937;">'
        f'{arrow} <strong>{best["action"]}</strong>: '
        f'{best["metric"]} {best["delta"]} '
        f'<span style="color:#6b7280;font-size:13px;">'
        f'({best["days_done"]}d done vs {best["days_skipped"]}d skipped)'
        f'</span></div>'
        '<div style="font-size:12px;color:#92400e;margin-top:4px;">'
        'Consider repeating this today.</div>'
        '</div>'
    )


def _build_execution_dashboard_html(config: SyncConfig, day: str) -> str:
    """7-day execution dashboard: what got done, missed, or simply not recorded.

    Old behaviour hid the card after 7 days without a completion — exactly
    when feedback matters — and painted every unticked task ❌, including
    supplements no sensor can see. Now each past task is classified:

      ✅ done       ticked in the Sheet/email, or auto-verified by the watch
      ❌ missed     sensable (movement/sleep) and the watch shows it wasn't met
      ⚪ unrecorded  not sensable (supplement, heat, light…) and never ticked
      ⏳ open        today, window not yet passed

    Expired tasks never show as open anywhere; today's list shows only what
    is still doable.
    """
    from datetime import datetime as _dt
    from html import escape as _esc

    from .action_lifecycle import action_state, enrich
    from .action_tracker import compute_streaks, load_action_history_with_sheets
    from .auto_complete import _classify

    history = load_action_history_with_sheets(config, num_days=7)
    if not history:
        return ""

    now = _dt.now(tz=config.timezone)
    counts = {"auto": 0, "ticked": 0, "missed": 0, "unrecorded": 0, "open": 0}
    chip_style = {
        "done": ("#d1fae5", "#065f46", "&#x2705;"),
        "missed": ("#fee2e2", "#991b1b", "&#x274C;"),
        "unrecorded": ("#f3f4f6", "#6b7280", "&#x26AA;"),
        "open": ("#fef3c7", "#92400e", "&#x23F3;"),
    }

    rows = ""
    for record in history:
        d = record["date"]
        chips = ""
        for raw in record.get("actions", []):
            act = enrich(dict(raw))
            state = action_state(act, d, now)
            if state == "done":
                kind = "done"
                counts["auto" if str(act.get("source", "")).endswith("_auto") else "ticked"] += 1
            elif state == "open":
                kind = "open"
                counts["open"] += 1
            else:
                cat = _classify(act.get("title", ""), act.get("description", ""),
                                act.get("category") or "")
                kind = "missed" if cat in ("movement", "sleep") else "unrecorded"
                counts[kind] += 1
            bg, fg, icon = chip_style[kind]
            title = act.get("title", "?")
            short = title if len(title) <= 26 else title[:24].rstrip() + "…"
            chips += (
                f'<span style="display:inline-block;padding:2px 7px;margin:2px 4px 2px 0;'
                f'background:{bg};color:{fg};border-radius:5px;font-size:12px;">'
                f'{icon} {_esc(short)}</span>'
            )
        label = "Today" if d == now.date().isoformat() else d[5:]
        rows += (
            '<tr>'
            f'<td style="padding:5px 8px;border-bottom:1px solid #e5e7eb;font-size:12px;'
            f'color:#6b7280;white-space:nowrap;vertical-align:top;">{label}</td>'
            f'<td style="padding:5px 8px;border-bottom:1px solid #e5e7eb;">{chips}</td>'
            '</tr>'
        )

    done_total = counts["auto"] + counts["ticked"]
    summary = (
        f'<b>{done_total}</b> done ({counts["auto"]} auto-verified, {counts["ticked"]} ticked)'
        f' · <b>{counts["missed"]}</b> missed'
        f' · <b>{counts["unrecorded"]}</b> not recorded'
    )
    hint = ""
    if counts["unrecorded"] > done_total:
        hint = (
            '<div style="font-size:12px;color:#6b7280;margin-top:6px;">'
            '&#x26AA; = the watch can&rsquo;t see it (supplements, heat, light). '
            'One tick in the tracker turns it into real adherence data for the '
            '&ldquo;what moved your metrics&rdquo; analysis.</div>'
        )

    streaks = compute_streaks(config.data_dir)
    streak_html = ""
    if streaks.get("any_action", 0) > 0:
        streak_html = (
            f'<span style="display:inline-block;padding:3px 9px;background:#fef3c7;'
            f'border-radius:6px;margin-left:8px;font-size:12px;">'
            f'&#x1F525; {streaks["any_action"]}-day streak</span>'
        )

    # Action effects section
    effects_html = ""
    try:
        from .action_effects import compute_action_effects
        from datetime import date as date_type

        effects = compute_action_effects(
            config.data_dir, date_type.fromisoformat(day), lookback_days=14
        )
        if effects:
            effects_html = (
                '<div style="margin-top:12px;padding-top:10px;'
                'border-top:1px solid #dbeafe;">'
                '<div style="font-size:12px;font-weight:700;color:#1e40af;'
                'margin-bottom:6px;">What moved your metrics:</div>'
            )
            for eff in effects[:3]:
                arrow = "&#x2B06;" if eff["impact"] > 0 else "&#x2B07;"
                color = "#059669" if eff["impact"] > 0 else "#dc2626"
                effects_html += (
                    f'<div style="font-size:12px;color:#374151;'
                    f'padding:2px 0;">{arrow} '
                    f'<strong>{eff["action"]}</strong>: '
                    f'<span style="color:{color};">{eff["metric"]} '
                    f'{eff["delta"]}</span> '
                    f'({eff["days_done"]}d done vs {eff["days_skipped"]}d '
                    f'skipped)</div>'
                )
            effects_html += '</div>'
    except Exception:
        pass

    html = (
        '<div style="margin-top:28px;padding:16px 20px;background:#eff6ff;'
        'border-radius:12px;border:1px solid #bfdbfe;">'
        '<h3 style="color:#1e40af;margin:0 0 6px 0;font-size:16px;">'
        f'&#x1F4CA; Execution Dashboard{streak_html}</h3>'
        f'<div style="font-size:13px;color:#374151;margin-bottom:10px;">Last 7 days: {summary}</div>'
        '<table cellspacing="0" cellpadding="0" border="0" '
        'style="width:100%;border-collapse:collapse;">'
        f'{rows}'
        '</table>'
        f'{hint}'
        f'{effects_html}'
        '</div>'
    )

    return html


def _build_research_html(config: SyncConfig, day: str) -> str:
    """Recent-papers block for the daily summary, green/red impact-coded.

    The daily research sync (PubMed + OpenAlex) feeds the LLM but was never
    shown to the user. This renders the fetched paper-backed recommendations
    as cards matching the dashboard style: a green up-arrow + impact % for a
    beneficial action, the journal + citation count, and a link to the paper.
    """
    from datetime import date as _date

    try:
        from ..research.pipeline import load_research_for_day
    except Exception:
        return ""

    try:
        recs = load_research_for_day(config, _date.fromisoformat(day))
    except Exception:
        recs = []
    if not recs:
        return ""

    # Goal label + emoji
    goal_labels = {
        "sperm_motility": "🧬 Sperm motility",
        "sperm_quality": "🧬 Sperm quality",
        "testosterone": "🧪 Testosterone",
        "energy": "⚡ Energy",
        "hrv": "❤️ HRV / recovery",
        "sleep": "😴 Sleep",
    }

    rows = ""
    for r in recs[:6]:
        impact = r.get("expected_impact_pct") or 0
        # Green for a positive expected impact, grey when neutral/unknown.
        color = "#059669" if impact > 0 else "#6b7280"
        arrow = "&#x2197;" if impact > 0 else "&#x2194;"
        goal = goal_labels.get(r.get("goal", ""), r.get("goal", ""))
        cited = r.get("cited_by_count") or 0
        cited_badge = (
            f'<span style="background:#dbeafe;color:#1e40af;border-radius:9999px;'
            f'padding:1px 7px;font-size:11px;font-weight:600;margin-left:6px;">'
            f'cited {cited}</span>' if cited else ""
        )
        title = r.get("paper_title", "")
        journal = r.get("journal", "")
        url = r.get("url") or ""
        action = r.get("action", "")
        title_html = (
            f'<a href="{url}" style="color:#2563eb;text-decoration:none;">{title}</a>'
            if url else title
        )
        rows += (
            '<div style="border:1px solid #e5e7eb;border-radius:8px;padding:10px 12px;'
            'margin-bottom:8px;background:#fff;">'
            f'<div style="font-size:12px;font-weight:700;color:#1f2937;">{goal}{cited_badge}</div>'
            f'<div style="font-size:13px;color:#111827;margin-top:3px;">'
            f'<span style="color:{color};font-weight:600;">{arrow} '
            f'{("+" + format(impact, ".1f") + "%") if impact > 0 else "—"}</span> '
            f'{action}</div>'
            f'<div style="font-size:11px;color:#6b7280;margin-top:3px;">{title_html}'
            f'{(" · " + journal) if journal else ""}</div>'
            '</div>'
        )

    return (
        '<div style="margin-top:28px;padding:16px 20px;background:#f5f3ff;'
        'border-radius:12px;border:1px solid #ddd6fe;">'
        '<h3 style="color:#6d28d9;margin:0 0 6px 0;font-size:16px;">'
        '&#x1F4DA; Recent papers</h3>'
        '<div style="font-size:12px;color:#6b7280;margin-bottom:10px;">'
        'Fresh literature (PubMed + OpenAlex) matched to your goals. '
        'Green = expected positive impact; citation count shows how established the work is.'
        '</div>' + rows + '</div>'
    )


def _build_action_buttons_html(
    config: SyncConfig, advice_text: str, day: str
) -> str:
    """Build action tracking section for the email.

    Primary: Google Sheets link (works from any device).
    Fallback: local server buttons (if Sheet URL unavailable).
    """
    from .action_tracker import parse_actions

    actions = parse_actions(advice_text, day)
    if not actions:
        return ""

    # Try Google Sheets URL (cached — no API call if Sheet already exists)
    sheet_url = None
    try:
        from .sheets_tracker import get_tracker_sheet_url_cached

        sheet_url = get_tracker_sheet_url_cached(config)
    except Exception:
        pass

    # ── Google Sheets variant (primary) ──
    if sheet_url:
        action_list = ""
        for action in actions:
            action_list += (
                f'<tr><td style="padding:4px 0;color:#374151;font-size:14px;">'
                f'&#x2610; {action["idx"] + 1}. {action["title"]}</td></tr>'
            )

        html = (
            '<div style="margin-top:28px;padding:16px 20px;background:#f0fdf4;'
            'border-radius:12px;border:1px solid #86efac;">'
            '<h3 style="color:#059669;margin:0 0 12px 0;font-size:16px;">'
            '&#x1F4CB; Track Your Actions</h3>'
            '<table cellspacing="0" cellpadding="0" border="0">'
            f'{action_list}</table>'
            '<div style="margin-top:16px;text-align:center;">'
            f'<a href="{sheet_url}" style="display:inline-block;padding:12px 32px;'
            f'background:#059669;color:#ffffff;text-decoration:none;'
            f'border-radius:8px;font-weight:700;font-size:16px;">'
            f'&#x2705; Open Action Tracker (Google Sheets)</a></div>'
            '<div style="margin-top:8px;text-align:center;font-size:12px;color:#6b7280;">'
            'Tap checkboxes on any device &mdash; auto-saves instantly</div>'
        )

        # Small fallback: local server links
        if config.server_url:
            html += (
                '<div style="margin-top:14px;padding-top:10px;border-top:1px solid #bbf7d0;'
                'font-size:12px;color:#9ca3af;">'
                'Or use local server: '
            )
            for action in actions:
                done_url = (
                    f"{config.server_url}/action/done?date={day}&idx={action['idx']}"
                )
                html += (
                    f'<a href="{done_url}" style="color:#9ca3af;'
                    f'margin-right:8px;">[{action["idx"]+1}]</a>'
                )
            dashboard_url = f"{config.server_url}/dashboard"
            html += (
                f' | <a href="{dashboard_url}" style="color:#9ca3af;">'
                f'Dashboard</a></div>'
            )

        html += '</div>'
        return html

    # ── Fallback: local server buttons (original style) ──
    if not config.server_url:
        return ""

    rows = ""
    for action in actions:
        idx = action["idx"]
        title = action["title"]
        done_url = f"{config.server_url}/action/done?date={day}&idx={idx}"
        rows += (
            f'<tr><td style="padding:6px 0;">'
            f'<a href="{done_url}" style="display:inline-block;padding:8px 18px;'
            f"background:#2563eb;color:#ffffff;text-decoration:none;"
            f'border-radius:6px;font-weight:600;font-size:14px;">'
            f"&#x2713; Done</a></td>"
            f'<td style="padding:6px 8px;color:#374151;font-size:14px;">'
            f"{idx + 1}. {title}</td></tr>"
        )

    dashboard_url = f"{config.server_url}/dashboard"
    return (
        '<div style="margin-top:28px;padding:16px 20px;background:#f0f9ff;'
        'border-radius:12px;border:1px solid #bfdbfe;">'
        '<h3 style="color:#1e40af;margin:0 0 12px 0;font-size:16px;">'
        "&#x1F4CB; Track Your Actions</h3>"
        '<table cellspacing="0" cellpadding="0" border="0">'
        f"{rows}</table>"
        '<div style="margin-top:14px;padding-top:10px;border-top:1px solid #bfdbfe;">'
        f'<a href="{dashboard_url}" style="color:#2563eb;font-size:13px;'
        f'text-decoration:none;">View full dashboard &rarr;</a></div>'
        "</div>"
    )


_REPORT_KINDS = {
    "weekly_retrospective": "Weekly Retrospective",
    "health_os_brief": "Health OS brief",
    # Keeps "Daily Health Plan" in the subject so mail filters still match,
    # but says up front that no plan was generated.
    "advisor_error": "Daily Health Plan (advisor failed)",
}


def summarize_kinds(kinds: List[Any]) -> str:
    """"sperm_test ×17, blood_test ×4" instead of every lab file's kind in a row."""
    counts: Dict[str, int] = {}
    for kind in kinds:
        if kind:
            counts[str(kind)] = counts.get(str(kind), 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return ", ".join(k if n == 1 else f"{k} \u00d7{n}" for k, n in ranked) or "None"


def report_kind(advice: Dict[str, Any]) -> str:
    """Human heading for the report type — subject line and page heading."""
    return _REPORT_KINDS.get(str(advice.get("report_type") or ""), "Daily Health Plan")


def send_advice_email(config: SyncConfig, advice: Dict[str, Any]) -> None:
    """Send the daily advice as an HTML email."""
    if not config.email_to:
        raise RuntimeError("EMAIL_TO is required to send advice by email")
    if not config.smtp_host:
        raise RuntimeError("SMTP_HOST is required to send advice by email")

    day = advice["date"]
    advice_text = advice["advice"]

    # Convert markdown-ish advice to simple HTML
    html_body = _markdown_to_html(advice_text)

    # Highlight the single highest-impact action (F9)
    best_mover_html = _build_best_mover_html(config, day)

    # Biomarker dashboard — sparklines for blood + spermogram time-series
    biomarker_html = ""
    try:
        from .biomarker_dashboard import render_email_dashboard_html

        biomarker_html = render_email_dashboard_html(config)
    except Exception:
        biomarker_html = ""

    # "Since your last test" outcome block — only when a report landed recently.
    outcomes_html = ""
    try:
        from datetime import date as _date, timedelta as _td

        from .outcomes import latest_progress, render_outcomes_email_block

        prog = latest_progress(config)
        if prog and prog.get("deltas"):
            # Only show if the newest reading in the deltas is within 7 days.
            newest = max(
                (d.get("new_date", "") for d in prog["deltas"]), default=""
            )
            try:
                fresh = _date.fromisoformat(newest) >= (_date.today() - _td(days=7))
            except Exception:
                fresh = False
            if fresh:
                outcomes_html = render_outcomes_email_block(prog)
    except Exception:
        outcomes_html = ""

    # Build execution dashboard (7-day history + effects)
    execution_dashboard_html = _build_execution_dashboard_html(config, day)

    # Recent papers (PubMed + OpenAlex) — green/red impact-coded block
    research_html = ""
    try:
        research_html = _build_research_html(config, day)
    except Exception:
        research_html = ""

    # Device comparison (Oura vs Fitbit) — only renders when Fitbit synced
    device_compare_html = ""
    try:
        from datetime import date as _date, timedelta as _td

        from .device_compare import _has_oura, compare_metrics, render_compare_email_html

        rows = compare_metrics(config, day)
        steps_today = next((r["fitbit"] or 0 for r in rows if r["metric"] == "steps"), 0)
        if rows and not _has_oura(rows) and steps_today < 500:
            # 08:00 same-day file is an upload-lag stub ("Steps 12"); show
            # yesterday's finalized day instead — the number that informs
            # this morning. Two-device days keep the live comparison.
            yday = (_date.fromisoformat(day) - _td(days=1)).isoformat()
            device_compare_html = render_compare_email_html(config, yday, label="Yesterday")
        else:
            device_compare_html = render_compare_email_html(config, day)
    except Exception:
        device_compare_html = ""

    # Build action tracking buttons
    action_buttons_html = _build_action_buttons_html(config, advice_text, day)

    ctx = advice.get("context_summary", {})
    oura_badge = "Yes" if ctx.get("fitbit_available") else "No"
    lab_types = summarize_kinds(ctx.get("lab_report_types", []))
    scan_count = ctx.get("image_analyses_count", 0)
    scan_info = f" &bull; Image scans: {scan_count}" if scan_count else ""

    # The weekly retro and the fallback error report reuse this sender. They
    # used to go out under the daily heading with a "Fitbit Air data: No |
    # Lab reports: None" line that described nothing about them.
    kind = report_kind(advice)
    is_daily = kind == "Daily Health Plan"
    meta_html = (
        f"Fitbit Air data: {oura_badge} &bull; Lab reports: {lab_types}{scan_info} &bull; "
        if is_daily else ""
    ) + f"Model: {advice.get('model', 'N/A')}"
    meta_plain = f"Fitbit Air data: {oura_badge} | Lab reports: {lab_types}\n" if is_daily else ""

    html = f"""\
<html>
<head>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         max-width: 640px; margin: 0 auto; padding: 20px; color: #1a1a1a; }}
  h2 {{ color: #2563eb; border-bottom: 2px solid #2563eb; padding-bottom: 8px; }}
  h3 {{ color: #1e40af; margin-top: 24px; }}
  .meta {{ font-size: 13px; color: #6b7280; margin-bottom: 24px; }}
  .footer {{ margin-top: 32px; padding-top: 16px; border-top: 1px solid #e5e7eb;
             font-size: 12px; color: #9ca3af; }}
</style>
</head>
<body>
  <h2>{kind} &mdash; {day}</h2>
  <div class="meta">
    {meta_html}
  </div>
  {best_mover_html}
  {outcomes_html}
  {html_body}
  {biomarker_html}
  {device_compare_html}
  {execution_dashboard_html}
  {research_html}
  {action_buttons_html}
  <div class="footer">
    Generated by Personal Doctor &bull; {advice.get('generated_at', '')}
  </div>
</body>
</html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"{kind} \u2014 {day}"
    msg["From"] = config.smtp_user or f"health-advisor@{config.smtp_host}"
    msg["To"] = config.email_to

    # Plain-text fallback (includes Sheet URL + action URLs)
    action_links = ""
    from .action_tracker import parse_actions

    actions = parse_actions(advice_text, day)
    if actions:
        # Try Google Sheets URL
        sheet_url = None
        try:
            from .sheets_tracker import get_tracker_sheet_url_cached

            sheet_url = get_tracker_sheet_url_cached(config)
        except Exception:
            pass

        action_links = "\n--- Track Your Actions ---\n"
        if sheet_url:
            action_links += f"Open Action Tracker (Google Sheets):\n  {sheet_url}\n\n"
        for a in actions:
            action_links += f"  {a['idx']+1}. {a['title']}\n"
        action_links += "\n"
        if config.server_url:
            action_links += f"Local dashboard: {config.server_url}/dashboard\n"

    plain = (
        f"{kind} \u2014 {day}\n"
        f"{meta_plain}\n"
        f"{advice_text}\n\n"
        f"{action_links}"
        f"Generated: {advice.get('generated_at', '')}"
    )
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    context = ssl.create_default_context()
    smtp_port = config.smtp_port or 465

    # Without an explicit timeout smtplib inherits the global socket timeout
    # (None), so a stalled SMTP connection blocks the 08:00 advisor job forever.
    smtp_timeout_s = 30

    if smtp_port == 465:
        # SSL connection (Yahoo, etc.)
        with smtplib.SMTP_SSL(
            config.smtp_host, smtp_port, context=context, timeout=smtp_timeout_s
        ) as server:
            if config.smtp_user and config.smtp_password:
                server.login(config.smtp_user, config.smtp_password)
            server.sendmail(msg["From"], [config.email_to], msg.as_string())
    else:
        # STARTTLS connection (Gmail on 587, etc.)
        with smtplib.SMTP(config.smtp_host, smtp_port, timeout=smtp_timeout_s) as server:
            server.ehlo()
            if smtp_port != 25:
                server.starttls(context=context)
                server.ehlo()
            if config.smtp_user and config.smtp_password:
                server.login(config.smtp_user, config.smtp_password)
            server.sendmail(msg["From"], [config.email_to], msg.as_string())


def _markdown_to_html(text: str) -> str:
    """Minimal markdown-to-HTML for the advice text."""
    lines = text.split("\n")
    html_lines = []
    in_list = False

    for line in lines:
        stripped = line.strip()

        if not stripped:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append("<br>")
            continue

        # Headings
        if stripped.startswith("### "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h3>{stripped[4:]}</h3>")
            continue
        if stripped.startswith("## "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h2>{stripped[3:]}</h2>")
            continue

        # Bold
        stripped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", stripped)

        # Numbered list items
        if re.match(r"^\d+\.\s", stripped):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            content = re.sub(r"^\d+\.\s*", "", stripped)
            html_lines.append(f"<li>{content}</li>")
            continue

        # Bullet list items
        if stripped.startswith("- "):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            html_lines.append(f"<li>{stripped[2:]}</li>")
            continue

        # Regular paragraph
        if in_list:
            html_lines.append("</ul>")
            in_list = False
        html_lines.append(f"<p>{stripped}</p>")

    if in_list:
        html_lines.append("</ul>")

    return "\n".join(html_lines)
