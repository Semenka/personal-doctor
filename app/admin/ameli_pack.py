"""Make the Ameli side of a claim as close to zero work as possible.

The login itself is not automated — that needs the user's own credentials on a
government portal. So this attacks the time cost from the other direction:
eliminate the submissions that shouldn't happen at all, and for whatever is
genuinely left, pre-compute every value so the manual session is paste-and-go
rather than look-everything-up.

Three layers, in order of how much time they save:

1. ELIMINATE — NOEMIE. If Ameli↔Henner télétransmission is active, the state
   notifies the mutuelle automatically once it pays and the top-up arrives with
   no claim filed at all. That removes the entire Henner workflow permanently,
   which beats automating it. Checked once, then remembered.

2. AVOID — duplicate guard. Most care is télétransmitted by the provider via
   Carte Vitale, so filing again creates a duplicate and costs more time than
   it saves. The pack forces a "Mes paiements" check first and records the
   answer so the same expense isn't re-examined every week.

3. PRE-FILL — for whatever genuinely needs filing, a single ordered block with
   every field value, the CPAM address, and a ready cover letter.

Note: ameli.fr's own wording changes; the field labels here are a guide, not a
scrape. Nothing in this module reads or writes the portal.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..sync.config import SyncConfig
from .claims import Expense, load_drafts, load_expenses, _fmt_eur

# Paris CPAM postal address for feuilles de soins.
CPAM_PARIS = "CPAM de Paris\n75948 Paris Cedex 19"


@dataclass
class AdminSettings:
    """Slow-changing facts that decide how much work the workflow needs."""
    noemie_active: Optional[bool] = None      # Ameli → Henner télétransmission
    noemie_checked: Optional[str] = None      # ISO date of last check
    social_security_number: str = ""          # NEVER auto-filled; user may store
    henner_member_id: str = ""
    notes: str = ""


def _settings_path(config: SyncConfig) -> Path:
    d = config.data_dir / "admin"
    d.mkdir(parents=True, exist_ok=True)
    return d / "settings.json"


def load_settings(config: SyncConfig) -> AdminSettings:
    path = _settings_path(config)
    if not path.exists():
        return AdminSettings()
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return AdminSettings()
    known = {k: v for k, v in raw.items() if k in AdminSettings.__annotations__}
    return AdminSettings(**known)


def save_settings(config: SyncConfig, settings: AdminSettings) -> Path:
    path = _settings_path(config)
    path.write_text(json.dumps(asdict(settings), indent=2, ensure_ascii=False))
    return path


def noemie_advice(config: SyncConfig) -> str:
    """The highest-leverage block: kill the workflow instead of automating it."""
    s = load_settings(config)
    if s.noemie_active is True:
        return (
            "✅ **NOEMIE is active** (checked "
            f"{s.noemie_checked or 'previously'}). Once Ameli pays, Henner is "
            "notified automatically and pays the top-up — no mutuelle claim to "
            "file. Only genuinely non-transmitted care needs anything from you."
        )
    if s.noemie_active is False:
        return (
            "⚠️ **NOEMIE is NOT active.** Every Henner top-up has to be claimed "
            "by hand. Setting it up is a one-time call to Henner with your "
            "numéro de sécurité sociale — it permanently removes the mutuelle "
            "half of this workload. Worth doing before automating anything else."
        )
    return (
        "❓ **NOEMIE status unknown — check this first.** If Ameli↔Henner "
        "télétransmission is active, the state notifies Henner the moment it "
        "pays and the top-up lands automatically, with no claim filed. That "
        "deletes the entire mutuelle workflow rather than speeding it up.\n"
        "   - ameli.fr → *Mon compte* → *Ma mutuelle* — does Henner appear?\n"
        "   - Or ask Henner directly whether télétransmission NOEMIE is active.\n"
        "   - Record it: `python -m app.admin.cli noemie yes|no`"
    )


def build_submission_pack(
    config: SyncConfig, draft_id: str, today: Optional[date] = None
) -> Dict[str, Any]:
    """Everything needed for one Ameli session, pre-computed and ordered.

    Returns {path, markdown, expense_count}. Writes the pack to
    data/ingested/admin/packs/ so it can be opened on the phone mid-session.
    """
    if today is None:
        today = date.today()
    draft = next((d for d in load_drafts(config) if d.id == draft_id), None)
    if draft is None:
        raise ValueError(f"No claim {draft_id}")

    by_id = {e.id: e for e in load_expenses(config)}
    expenses: List[Expense] = [by_id[i] for i in draft.expense_ids if i in by_id]

    s = load_settings(config)
    lines: List[str] = [
        f"# Ameli submission pack — {today.isoformat()}",
        f"Claim `{draft.id}` · {len(expenses)} item(s) · {_fmt_eur(draft.total_eur)}",
        "",
        "## 0. Before you touch anything",
        noemie_advice(config),
        "",
        "**Duplicate guard.** Open *Mes paiements* on ameli.fr and search each "
        "date below. Anything already listed was télétransmitted by the "
        "provider — do NOT file it again; mark it instead:",
        "",
    ]
    for e in expenses:
        lines.append(
            f"- `{e.id}` {e.date} · {e.provider} · {_fmt_eur(e.amount_eur)} — "
            f"if already there: `python -m app.admin.cli mark {e.id} "
            f"--ameli auto_transmitted`"
        )

    lines += [
        "",
        "## 1. What actually needs filing",
        "",
        "For care the provider did NOT télétransmit, the route is a paper "
        "**feuille de soins** from that provider, posted to your CPAM — the "
        "ameli.fr account is mainly for tracking payments and downloading "
        "décomptes, not for declaring arbitrary spend. If a provider never gave "
        "you a feuille de soins, ask them for one; without it there is nothing "
        "to file.",
        "",
        f"**Post to:**\n```\n{CPAM_PARIS}\n```",
        "",
        "## 2. Per-item values (copy as needed)",
        "",
    ]
    for e in expenses:
        lines += [
            f"### {e.date} — {e.provider}",
            "```",
            f"Date des soins   : {e.date}",
            f"Professionnel    : {e.provider}",
            f"Acte             : {e.description}",
            f"Montant payé     : {_fmt_eur(e.amount_eur)}",
            f"Catégorie        : {e.category}",
            "```",
            f"Receipt: {e.receipt_path or '⚠️ MISSING — request a duplicate facture acquittée'}",
            "",
        ]

    lines += [
        "## 3. Cover letter (if posting)",
        "```",
        "Madame, Monsieur,",
        "",
        "Veuillez trouver ci-joint la ou les feuilles de soins correspondant "
        "aux actes suivants, réglés directement par mes soins :",
        "",
    ]
    for e in expenses:
        lines.append(f"  - {e.date} — {e.provider} — {e.description} — {_fmt_eur(e.amount_eur)}")
    lines += [
        "",
        f"Total : {_fmt_eur(draft.total_eur)}",
        "",
        "Je vous remercie de bien vouloir procéder au remboursement sur mon "
        "compte habituel.",
        "",
        "Veuillez agréer, Madame, Monsieur, mes salutations distinguées.",
        "```",
        "",
        "## 4. After Ameli pays",
        "",
        "1. Download the **décompte** from *Mes paiements* — Henner needs it.",
        "2. Record what came back:",
        "",
    ]
    for e in expenses:
        lines.append(
            f"   `python -m app.admin.cli mark {e.id} --ameli reimbursed "
            f"--ameli-eur <montant>`"
        )
    if s.noemie_active is True:
        lines += [
            "",
            "3. Henner: nothing to do — NOEMIE transmits it automatically.",
        ]
    else:
        lines += [
            "",
            "3. Then draft + send the Henner top-up:",
            "   `python -m app.admin.cli draft` → `show <id>` → `approve <id>` "
            "→ `send <id>`",
        ]

    markdown = "\n".join(lines) + "\n"
    packs = config.data_dir / "admin" / "packs"
    packs.mkdir(parents=True, exist_ok=True)
    path = packs / f"ameli_pack_{draft.id}.md"
    path.write_text(markdown)
    return {"path": str(path), "markdown": markdown, "expense_count": len(expenses)}
