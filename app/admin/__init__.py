"""Health administration: reimbursement claims, insurer correspondence.

French health cover is two-layer: Assurance Maladie (Ameli, the state) pays the
base rate, then the mutuelle/complémentaire (here: Henner) tops up the
remainder. Most care is auto-transmitted via Carte Vitale, but anything the
user pays out of pocket — a non-conventionné specialist, a lab slip paid
directly, a receipt that never reached the télétransmission — only comes back
if a claim is actually filed. Those are the ones that get forgotten.

This package tracks expenses, detects what is unreimbursed, and drafts the
correspondence. It never submits anything on its own: see claims.py.
"""
