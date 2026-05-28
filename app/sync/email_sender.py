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
    """Build a 7-day action execution dashboard for the email.

    Shows yesterday's and prior days' action completion as a visual table
    with streaks and computed action effects.
    """
    from .action_tracker import compute_streaks, load_action_history_with_sheets

    history = load_action_history_with_sheets(config, num_days=7)
    if not history:
        return ""

    streaks = compute_streaks(config.data_dir)
    any_streak = streaks.get("any_action", 0)
    all_streak = streaks.get("all_actions", 0)

    # Check if there is anything positive to show. If nothing completed in 7 days,
    # hide the execution dashboard entirely (F8 — no guilt narrative).
    any_recent_completions = any(
        any(a.get("done") for a in rec.get("actions", []))
        for rec in history
    )
    if not any_recent_completions and any_streak == 0:
        return ""

    # Streak badges (only when positive — never shame about zero)
    streak_html = (
        '<div style="margin-bottom:12px;font-size:14px;">'
    )
    if any_streak > 0:
        streak_html += (
            f'<span style="display:inline-block;padding:4px 10px;'
            f'background:#fef3c7;border-radius:6px;margin-right:8px;">'
            f'&#x1F525; {any_streak}-day streak</span>'
        )
    if all_streak > 0:
        streak_html += (
            f'<span style="display:inline-block;padding:4px 10px;'
            f'background:#d1fae5;border-radius:6px;">'
            f'&#x1F3AF; {all_streak}d perfect</span>'
        )
    streak_html += '</div>'

    # History table rows
    rows = ""
    for record in history:
        d = record["date"]
        actions = record.get("actions", [])
        done_count = sum(1 for a in actions if a.get("done"))
        total = len(actions)
        rate = done_count / total if total else 0

        # Status icons for each action
        icons = ""
        for a in actions:
            if a.get("done"):
                icons += (
                    '<span style="display:inline-block;width:22px;height:22px;'
                    'line-height:22px;text-align:center;background:#d1fae5;'
                    'border-radius:4px;margin-right:3px;font-size:13px;">'
                    '&#x2705;</span>'
                )
            else:
                icons += (
                    '<span style="display:inline-block;width:22px;height:22px;'
                    'line-height:22px;text-align:center;background:#fee2e2;'
                    'border-radius:4px;margin-right:3px;font-size:13px;">'
                    '&#x274C;</span>'
                )

        # Score color
        if rate >= 1.0:
            score_color = "#059669"
        elif rate >= 0.5:
            score_color = "#d97706"
        else:
            score_color = "#dc2626"

        rows += (
            f'<tr>'
            f'<td style="padding:6px 10px;border-bottom:1px solid #e5e7eb;'
            f'font-size:13px;color:#6b7280;">{d}</td>'
            f'<td style="padding:6px 10px;border-bottom:1px solid #e5e7eb;">'
            f'{icons}</td>'
            f'<td style="padding:6px 10px;border-bottom:1px solid #e5e7eb;'
            f'text-align:center;font-weight:700;color:{score_color};'
            f'font-size:14px;">{done_count}/{total}</td>'
            f'</tr>'
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
        '<h3 style="color:#1e40af;margin:0 0 10px 0;font-size:16px;">'
        '&#x1F4CA; Execution Dashboard</h3>'
        f'{streak_html}'
        '<table cellspacing="0" cellpadding="0" border="0" '
        'style="width:100%;border-collapse:collapse;">'
        '<tr style="background:#f1f5f9;">'
        '<th style="padding:6px 10px;text-align:left;font-size:12px;'
        'color:#64748b;border-bottom:2px solid #cbd5e1;">Date</th>'
        '<th style="padding:6px 10px;text-align:left;font-size:12px;'
        'color:#64748b;border-bottom:2px solid #cbd5e1;">Actions</th>'
        '<th style="padding:6px 10px;text-align:center;font-size:12px;'
        'color:#64748b;border-bottom:2px solid #cbd5e1;">Score</th>'
        '</tr>'
        f'{rows}'
        '</table>'
        f'{effects_html}'
        '</div>'
    )

    return html


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

    # Build action tracking buttons
    action_buttons_html = _build_action_buttons_html(config, advice_text, day)

    ctx = advice.get("context_summary", {})
    oura_badge = "Yes" if ctx.get("oura_available") else "No"
    lab_types = ", ".join(ctx.get("lab_report_types", [])) or "None"
    scan_count = ctx.get("image_analyses_count", 0)
    scan_info = f" &bull; Image scans: {scan_count}" if scan_count else ""

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
  <h2>Daily Health Plan &mdash; {day}</h2>
  <div class="meta">
    Oura data: {oura_badge} &bull; Lab reports: {lab_types}{scan_info} &bull; Model: {advice.get('model', 'N/A')}
  </div>
  {best_mover_html}
  {outcomes_html}
  {html_body}
  {biomarker_html}
  {execution_dashboard_html}
  {action_buttons_html}
  <div class="footer">
    Generated by Personal Doctor &bull; {advice.get('generated_at', '')}
  </div>
</body>
</html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Daily Health Plan \u2014 {day}"
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
        f"Daily Health Plan \u2014 {day}\n"
        f"Oura data: {oura_badge} | Lab reports: {lab_types}\n\n"
        f"{advice_text}\n\n"
        f"{action_links}"
        f"Generated: {advice.get('generated_at', '')}"
    )
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    context = ssl.create_default_context()
    smtp_port = config.smtp_port or 465

    if smtp_port == 465:
        # SSL connection (Yahoo, etc.)
        with smtplib.SMTP_SSL(config.smtp_host, smtp_port, context=context) as server:
            if config.smtp_user and config.smtp_password:
                server.login(config.smtp_user, config.smtp_password)
            server.sendmail(msg["From"], [config.email_to], msg.as_string())
    else:
        # STARTTLS connection (Gmail on 587, etc.)
        with smtplib.SMTP(config.smtp_host, smtp_port) as server:
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
