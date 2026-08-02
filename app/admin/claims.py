"""Reimbursement tracking and claim drafting (Henner mutuelle + Ameli).

WHY THIS EXISTS
Out-of-pocket medical spend only comes back if someone files. The user's
fertility workup is exactly the kind of spend that slips: repeated spermograms,
lab panels paid at the counter, specialist consults above the tarif de
convention. This module keeps a ledger, flags what is unclaimed and still
inside the filing window, and drafts the letter/email.

WHAT IT DELIBERATELY DOES NOT DO
It does not send anything, and it does not log into ameli.fr. Both are
intentional:
  - Sending: a claim is an irreversible statement to an insurer about money.
    Every draft lands in a queue with status "draft" and is surfaced for
    approval; only an explicit human approval flips it to "approved", and only
    then may a sender act on it. approve_draft() is the single gate.
  - ameli.fr: submitting there means entering the user's credentials on a
    government portal. That is never automated. For Ameli-side claims this
    module produces the exact filled content plus step-by-step instructions,
    and the user pastes it in.

Henner accepts claims by email with scanned receipts attached; those drafts are
therefore complete and ready to send once approved.

Storage: data/ingested/admin/expenses.json  (the ledger)
         data/ingested/admin/claims/<id>.json  (one file per claim)
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..sync.config import SyncConfig

# French filing windows. Assurance Maladie: 2 years from the care date
# (Art. L332-1 CSS). Mutuelles are contractual and shorter — Henner's standard
# is 2 years, but we warn well before either edge.
AMELI_WINDOW_DAYS = 730
MUTUELLE_WINDOW_DAYS = 730
WARN_WINDOW_DAYS = 90  # start nagging when this little time is left

HENNER_CLAIMS_EMAIL = "sinistre.sante@henner.com"


@dataclass
class Expense:
    """One out-of-pocket medical payment."""
    id: str
    date: str                      # ISO date of care
    provider: str
    description: str
    amount_eur: float
    category: str = "consultation"  # consultation | lab | pharmacy | imaging | dental | other
    paid_out_of_pocket: bool = True
    # Reimbursement state
    ameli_status: str = "pending"   # pending | auto_transmitted | claimed | reimbursed | not_eligible
    mutuelle_status: str = "pending"  # pending | claimed | reimbursed | not_eligible
    ameli_reimbursed_eur: float = 0.0
    mutuelle_reimbursed_eur: float = 0.0
    receipt_path: Optional[str] = None
    notes: str = ""

    @property
    def outstanding_eur(self) -> float:
        return round(
            self.amount_eur - self.ameli_reimbursed_eur - self.mutuelle_reimbursed_eur, 2
        )


@dataclass
class ClaimDraft:
    """A drafted claim awaiting human approval. Never auto-sent."""
    id: str
    created: str
    target: str                    # "henner" | "ameli"
    expense_ids: List[str]
    total_eur: float
    subject: str
    body: str
    channel: str                   # "email" | "portal_manual"
    recipient: str = ""
    status: str = "draft"          # draft | approved | sent | rejected
    instructions: str = ""         # for portal_manual: how to submit
    approved_at: Optional[str] = None
    sent_at: Optional[str] = None
    attachments: List[str] = field(default_factory=list)


def _admin_dir(config: SyncConfig) -> Path:
    d = config.data_dir / "admin"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _claims_dir(config: SyncConfig) -> Path:
    d = _admin_dir(config) / "claims"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Ledger ────────────────────────────────────────────────────────────────

def load_expenses(config: SyncConfig) -> List[Expense]:
    path = _admin_dir(config) / "expenses.json"
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    out = []
    for r in raw:
        known = {k: v for k, v in r.items() if k in Expense.__annotations__}
        out.append(Expense(**known))
    return out


def save_expenses(config: SyncConfig, expenses: List[Expense]) -> Path:
    path = _admin_dir(config) / "expenses.json"
    path.write_text(
        json.dumps([asdict(e) for e in expenses], indent=2, ensure_ascii=False)
    )
    return path


def add_expense(
    config: SyncConfig, *, care_date: str, provider: str, description: str,
    amount_eur: float, category: str = "consultation",
    receipt_path: Optional[str] = None, notes: str = "",
) -> Expense:
    """Append one expense to the ledger. Idempotent on (date, provider, amount)."""
    expenses = load_expenses(config)
    for e in expenses:
        if (e.date == care_date and e.provider.lower() == provider.lower()
                and abs(e.amount_eur - amount_eur) < 0.01):
            return e  # already recorded
    exp = Expense(
        id=uuid.uuid4().hex[:12], date=care_date, provider=provider,
        description=description, amount_eur=round(float(amount_eur), 2),
        category=category, receipt_path=receipt_path, notes=notes,
    )
    expenses.append(exp)
    save_expenses(config, expenses)
    return exp


def unclaimed_expenses(
    config: SyncConfig, today: Optional[date] = None
) -> List[Dict[str, Any]]:
    """Expenses with money still outstanding, annotated with filing deadlines.

    Sorted by urgency: whatever is closest to falling out of its window first.
    """
    if today is None:
        today = date.today()
    out: List[Dict[str, Any]] = []
    for e in load_expenses(config):
        if not e.paid_out_of_pocket:
            continue
        ameli_open = e.ameli_status in ("pending",)
        mut_open = e.mutuelle_status in ("pending",)
        if not (ameli_open or mut_open):
            continue
        if e.outstanding_eur <= 0:
            continue
        try:
            care = date.fromisoformat(e.date)
        except ValueError:
            continue
        ameli_left = AMELI_WINDOW_DAYS - (today - care).days
        mut_left = MUTUELLE_WINDOW_DAYS - (today - care).days
        soonest = min(
            [d for d, open_ in ((ameli_left, ameli_open), (mut_left, mut_open)) if open_]
            or [9999]
        )
        out.append({
            "expense": e,
            "ameli_open": ameli_open,
            "mutuelle_open": mut_open,
            "ameli_days_left": ameli_left,
            "mutuelle_days_left": mut_left,
            "days_left": soonest,
            "expiring_soon": soonest <= WARN_WINDOW_DAYS,
            "expired": soonest < 0,
        })
    out.sort(key=lambda r: r["days_left"])
    return out


# ── Receipt ingestion ─────────────────────────────────────────────────────

_AMOUNT_RE = re.compile(r"(\d+[.,]\d{2})\s*(?:€|EUR|euros?)", re.IGNORECASE)
_DATE_RE = re.compile(r"(\d{2})[/.-](\d{2})[/.-](\d{4})")


def parse_receipt_text(text: str) -> Dict[str, Any]:
    """Best-effort extraction of (date, amount) from French receipt text.

    Deliberately conservative: returns what it is confident about and leaves
    the rest None for a human to fill. A wrong auto-filed amount is far worse
    than an unfilled field.
    """
    result: Dict[str, Any] = {"date": None, "amount_eur": None}
    amounts = [float(m.group(1).replace(",", ".")) for m in _AMOUNT_RE.finditer(text)]
    if amounts:
        # The total is usually the largest figure on a care receipt.
        result["amount_eur"] = max(amounts)
    m = _DATE_RE.search(text)
    if m:
        dd, mm, yyyy = m.groups()
        try:
            result["date"] = date(int(yyyy), int(mm), int(dd)).isoformat()
        except ValueError:
            pass
    return result


# ── Drafting ──────────────────────────────────────────────────────────────

def _fmt_eur(v: float) -> str:
    return f"{v:.2f} €".replace(".", ",")


def draft_henner_claim(
    config: SyncConfig, expenses: List[Expense], today: Optional[date] = None
) -> ClaimDraft:
    """Draft the Henner (mutuelle) reimbursement email. Status stays 'draft'."""
    if today is None:
        today = date.today()
    total = round(sum(e.outstanding_eur for e in expenses), 2)
    rows = "\n".join(
        f"  - {e.date} · {e.provider} · {e.description} · {_fmt_eur(e.amount_eur)}"
        + (f" (déjà remboursé Sécu : {_fmt_eur(e.ameli_reimbursed_eur)})"
           if e.ameli_reimbursed_eur else "")
        for e in expenses
    )
    subject = (
        f"Demande de remboursement — {len(expenses)} soin(s) — "
        f"total {_fmt_eur(total)}"
    )
    body = f"""Bonjour,

Je vous prie de bien vouloir procéder au remboursement des frais de santé
suivants, restés à ma charge après intervention de l'Assurance Maladie :

{rows}

Total restant à charge : {_fmt_eur(total)}

Vous trouverez ci-joint les justificatifs correspondants (factures acquittées
et, le cas échéant, décomptes de l'Assurance Maladie).

Je reste à votre disposition pour tout complément d'information.

Bien cordialement,
"""
    attachments = [e.receipt_path for e in expenses if e.receipt_path]
    return ClaimDraft(
        id=uuid.uuid4().hex[:12], created=today.isoformat(), target="henner",
        expense_ids=[e.id for e in expenses], total_eur=total,
        subject=subject, body=body, channel="email",
        recipient=HENNER_CLAIMS_EMAIL, attachments=[a for a in attachments if a],
        instructions=(
            "Attach the receipts listed above before sending. If any is missing, "
            "request a duplicate from the provider first — Henner rejects claims "
            "without a facture acquittée."
        ),
    )


def draft_ameli_claim(
    config: SyncConfig, expenses: List[Expense], today: Optional[date] = None
) -> ClaimDraft:
    """Prepare an Ameli claim as manual-submission content.

    channel is "portal_manual" on purpose: submitting on ameli.fr requires
    logging in to a government portal with the user's credentials, which is
    never automated. This produces the content and the click path.
    """
    if today is None:
        today = date.today()
    total = round(sum(e.amount_eur for e in expenses), 2)
    rows = "\n".join(
        f"  - {e.date} · {e.provider} · {e.description} · {_fmt_eur(e.amount_eur)}"
        for e in expenses
    )
    body = f"""Soins à déclarer à l'Assurance Maladie ({len(expenses)}) :

{rows}

Total : {_fmt_eur(total)}
"""
    instructions = """Submit on ameli.fr yourself (never automated — it needs your login):

1. Log in at ameli.fr → "Mes démarches".
2. If the care was NOT auto-transmitted by Carte Vitale, use the paper route:
   fill a "feuille de soins" from the provider and post it to your CPAM.
   Paris CPAM: CPAM de Paris, 75948 Paris Cedex 19.
3. For a provider who gave you a feuille de soins électronique, no action is
   needed — check "Mes paiements" in 5-10 days first to avoid double-filing.
4. Once Ameli pays, the décompte appears under "Mes paiements". Download it —
   Henner needs it for the top-up claim.

Check "Mes paiements" BEFORE filing: most care is already auto-transmitted and
filing again creates a duplicate."""
    return ClaimDraft(
        id=uuid.uuid4().hex[:12], created=today.isoformat(), target="ameli",
        expense_ids=[e.id for e in expenses], total_eur=total,
        subject=f"Déclaration de soins — {len(expenses)} soin(s)",
        body=body, channel="portal_manual", recipient="ameli.fr",
        instructions=instructions,
    )


# ── Claim queue ───────────────────────────────────────────────────────────

def save_draft(config: SyncConfig, draft: ClaimDraft) -> Path:
    path = _claims_dir(config) / f"{draft.id}.json"
    path.write_text(json.dumps(asdict(draft), indent=2, ensure_ascii=False))
    return path


def load_drafts(
    config: SyncConfig, status: Optional[str] = None
) -> List[ClaimDraft]:
    out: List[ClaimDraft] = []
    for f in sorted(_claims_dir(config).glob("*.json")):
        try:
            raw = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        known = {k: v for k, v in raw.items() if k in ClaimDraft.__annotations__}
        d = ClaimDraft(**known)
        if status is None or d.status == status:
            out.append(d)
    return out


def approve_draft(config: SyncConfig, draft_id: str) -> Optional[ClaimDraft]:
    """THE gate. Only an explicit human call flips a draft to 'approved'.

    Nothing in the scheduler calls this. A sender must refuse to act on any
    draft whose status is not 'approved'.
    """
    for d in load_drafts(config):
        if d.id == draft_id:
            d.status = "approved"
            d.approved_at = datetime.now().isoformat(timespec="seconds")
            save_draft(config, d)
            return d
    return None


def reject_draft(config: SyncConfig, draft_id: str, reason: str = "") -> Optional[ClaimDraft]:
    for d in load_drafts(config):
        if d.id == draft_id:
            d.status = "rejected"
            d.instructions = (d.instructions + f"\nRejected: {reason}").strip()
            save_draft(config, d)
            return d
    return None


def build_pending_claims(
    config: SyncConfig, today: Optional[date] = None
) -> List[ClaimDraft]:
    """Draft claims for everything unclaimed. Returns NEW drafts only.

    Groups by target so the user gets one Henner email rather than one per
    receipt. Skips expenses already covered by an open draft.
    """
    if today is None:
        today = date.today()
    rows = unclaimed_expenses(config, today)
    if not rows:
        return []

    already = {
        eid
        for d in load_drafts(config)
        if d.status in ("draft", "approved", "sent")
        for eid in d.expense_ids
    }
    ameli_batch = [
        r["expense"] for r in rows
        if r["ameli_open"] and not r["expired"] and r["expense"].id not in already
    ]
    henner_batch = [
        r["expense"] for r in rows
        # Henner tops up what Ameli left; claim once Ameli is settled or n/a.
        if r["mutuelle_open"] and not r["expired"]
        and r["expense"].ameli_status in ("reimbursed", "auto_transmitted", "not_eligible")
        and r["expense"].id not in already
    ]

    drafts: List[ClaimDraft] = []
    if ameli_batch:
        d = draft_ameli_claim(config, ameli_batch, today)
        save_draft(config, d)
        drafts.append(d)
    if henner_batch:
        d = draft_henner_claim(config, henner_batch, today)
        save_draft(config, d)
        drafts.append(d)
    return drafts


def render_claims_summary(
    config: SyncConfig, today: Optional[date] = None
) -> str:
    """Markdown block for the weekly brief: money owed, deadlines, drafts waiting."""
    if today is None:
        today = date.today()
    rows = unclaimed_expenses(config, today)
    pending = load_drafts(config, status="draft")
    if not rows and not pending:
        return ""

    total = round(sum(r["expense"].outstanding_eur for r in rows), 2)
    lines = ["## 💶 Reimbursements"]
    if rows:
        lines.append(
            f"**{_fmt_eur(total)} outstanding** across {len(rows)} unclaimed item(s)."
        )
        for r in rows[:6]:
            e = r["expense"]
            flag = ""
            if r["expired"]:
                flag = " ⛔️ WINDOW CLOSED"
            elif r["expiring_soon"]:
                flag = f" ⚠️ {r['days_left']}d left to file"
            lines.append(
                f"- {e.date} · {e.provider} · {_fmt_eur(e.outstanding_eur)}{flag}"
            )
    if pending:
        lines.append("")
        lines.append(f"**{len(pending)} claim draft(s) awaiting your approval:**")
        for d in pending:
            where = "email to Henner" if d.target == "henner" else "manual on ameli.fr"
            lines.append(f"- `{d.id}` → {where}, {_fmt_eur(d.total_eur)}")
        lines.append("")
        lines.append("Nothing is sent until you approve it.")
    return "\n".join(lines)
