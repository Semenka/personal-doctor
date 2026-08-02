"""CLI for the health-admin ledger and claim queue.

    .venv/bin/python -m app.admin.cli add 2026-05-22 "Labo Eylau" "Spermogramme" 95.00 --category lab
    .venv/bin/python -m app.admin.cli list
    .venv/bin/python -m app.admin.cli draft
    .venv/bin/python -m app.admin.cli show <claim_id>
    .venv/bin/python -m app.admin.cli approve <claim_id>
    .venv/bin/python -m app.admin.cli reject <claim_id> "reason"
    .venv/bin/python -m app.admin.cli mark <expense_id> --ameli reimbursed --ameli-eur 66.50

`approve` is the only path that authorizes a claim to leave the machine, and
it is human-only by design — no scheduled job calls it.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date

from ..sync.config import load_config
from .claims import (
    add_expense,
    approve_draft,
    build_pending_claims,
    load_drafts,
    load_expenses,
    reject_draft,
    render_claims_summary,
    save_expenses,
    unclaimed_expenses,
)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="app.admin.cli", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="record an out-of-pocket expense")
    a.add_argument("care_date"); a.add_argument("provider")
    a.add_argument("description"); a.add_argument("amount_eur", type=float)
    a.add_argument("--category", default="consultation")
    a.add_argument("--receipt", default=None)
    a.add_argument("--notes", default="")

    sub.add_parser("list", help="show ledger + outstanding money")
    sub.add_parser("draft", help="draft claims for anything unclaimed")

    s = sub.add_parser("show", help="print a claim draft in full")
    s.add_argument("claim_id")

    ap = sub.add_parser("approve", help="AUTHORIZE a claim to be sent")
    ap.add_argument("claim_id")

    rj = sub.add_parser("reject", help="reject a claim draft")
    rj.add_argument("claim_id"); rj.add_argument("reason", nargs="?", default="")

    m = sub.add_parser("mark", help="update reimbursement state of an expense")
    m.add_argument("expense_id")
    m.add_argument("--ameli", default=None)
    m.add_argument("--mutuelle", default=None)
    m.add_argument("--ameli-eur", type=float, default=None)
    m.add_argument("--mutuelle-eur", type=float, default=None)

    args = p.parse_args(argv)
    config = load_config()
    today = date.today()

    if args.cmd == "add":
        e = add_expense(
            config, care_date=args.care_date, provider=args.provider,
            description=args.description, amount_eur=args.amount_eur,
            category=args.category, receipt_path=args.receipt, notes=args.notes,
        )
        print(f"Recorded {e.id}: {e.date} {e.provider} {e.amount_eur:.2f} EUR")
        return 0

    if args.cmd == "list":
        expenses = load_expenses(config)
        if not expenses:
            print("Ledger is empty. Add one with:\n"
                  "  .venv/bin/python -m app.admin.cli add "
                  "2026-05-22 \"Labo Eylau\" \"Spermogramme\" 95.00 --category lab")
            return 0
        for e in expenses:
            print(f"{e.id}  {e.date}  {e.amount_eur:7.2f}€  "
                  f"ameli={e.ameli_status:<17} mut={e.mutuelle_status:<10} {e.provider}")
        print()
        print(render_claims_summary(config, today) or "Nothing outstanding.")
        return 0

    if args.cmd == "draft":
        drafts = build_pending_claims(config, today)
        if not drafts:
            print("No new claims to draft.")
            return 0
        for d in drafts:
            print(f"[{d.id}] → {d.target} ({d.channel}) {d.total_eur:.2f}€ — status={d.status}")
        print("\nReview with `show <id>`, then `approve <id>` to authorize sending.")
        return 0

    if args.cmd == "show":
        for d in load_drafts(config):
            if d.id == args.claim_id:
                print(f"id={d.id} target={d.target} channel={d.channel} "
                      f"status={d.status} total={d.total_eur:.2f}€")
                print(f"recipient: {d.recipient}")
                print(f"subject:   {d.subject}")
                if d.attachments:
                    print(f"attach:    {', '.join(d.attachments)}")
                print("\n--- body ---\n" + d.body)
                if d.instructions:
                    print("\n--- how to submit ---\n" + d.instructions)
                return 0
        print(f"No draft {args.claim_id}", file=sys.stderr)
        return 1

    if args.cmd == "approve":
        d = approve_draft(config, args.claim_id)
        if not d:
            print(f"No draft {args.claim_id}", file=sys.stderr)
            return 1
        print(f"APPROVED {d.id} ({d.target}). ", end="")
        if d.channel == "portal_manual":
            print("Submit it yourself on ameli.fr — see `show` for the steps.")
        else:
            print(f"Cleared to send to {d.recipient}.")
        return 0

    if args.cmd == "reject":
        d = reject_draft(config, args.claim_id, args.reason)
        print(f"Rejected {d.id}" if d else f"No draft {args.claim_id}")
        return 0 if d else 1

    if args.cmd == "mark":
        expenses = load_expenses(config)
        for e in expenses:
            if e.id == args.expense_id:
                if args.ameli:
                    e.ameli_status = args.ameli
                if args.mutuelle:
                    e.mutuelle_status = args.mutuelle
                if args.ameli_eur is not None:
                    e.ameli_reimbursed_eur = args.ameli_eur
                if args.mutuelle_eur is not None:
                    e.mutuelle_reimbursed_eur = args.mutuelle_eur
                save_expenses(config, expenses)
                print(f"{e.id}: ameli={e.ameli_status} mutuelle={e.mutuelle_status} "
                      f"outstanding={e.outstanding_eur:.2f}€")
                return 0
        print(f"No expense {args.expense_id}", file=sys.stderr)
        return 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
