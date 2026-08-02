"""Paris care coordination — turning an overdue check-up into a booked visit.

The check-up engine knows WHAT is due. This knows WHO does it, and what to say
when booking. Everything here is Paris 16e / Trocadéro-centred, matching the
user's primary lab.

Two honesty rules shape this module:

1. Specialist names are NOT invented. Fabricating "Dr Martin, 12 rue X" for a
   real medical appointment is worse than useless — the user calls a number
   that doesn't exist, or worse, trusts a made-up credential. Only the lab the
   schedule already references is recorded as a concrete provider. Everything
   else is a SPECIALTY with a real, verifiable search route (Doctolib filter
   URL, the annuaire santé), so the user picks an actual practitioner.

2. No booking is performed. Doctolib booking is an outward-facing commitment
   on the user's behalf; this prepares the exact request text and leaves the
   click to them.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..sync.checkup_schedule import SPECIALIST
from ..sync.config import SyncConfig

HOME_AREA = "Paris 16e (Trocadéro)"


@dataclass
class Provider:
    """A place care actually happens."""
    key: str
    name: str
    kind: str                 # "lab" | "specialty"
    address: str = ""
    phone: str = ""
    booking_url: str = ""
    notes: str = ""
    # For kind="specialty": how to find a real practitioner, since we don't
    # invent one.
    search_url: str = ""


def _doctolib_search(specialty_slug: str) -> str:
    return f"https://www.doctolib.fr/{specialty_slug}/paris-75016"


# The only concrete provider we assert is the one already in the check-up
# schedule — it came from the user, not from us.
KNOWN_PROVIDERS: List[Provider] = [
    Provider(
        key="primary_lab",
        name="Laboratoire Clément Eylau-Unilabs",
        kind="lab",
        address="17 avenue d'Eylau, 75016 Paris (métro Trocadéro)",
        notes="Primary lab for all blood + semen panels. Walk-in for most "
              "panels; spermogram needs an appointment and a 2-4 day "
              "abstinence window.",
        booking_url="https://www.unilabs.fr/",
    ),
]

# Specialty routing: which kind of professional resolves which check-up key.
# `question` is what the user should actually ask for — the specific panel or
# procedure, phrased so the receptionist can book the right slot.
SPECIALTY_ROUTES: Dict[str, Dict[str, str]] = {
    "ecg_baseline": {
        "specialty": "Cardiologue",
        "slug": "cardiologue",
        "question": "ECG 12 dérivations de repos, bilan de base",
        "why": "ITGB3 PlA2 heterozygous (~1.5x MI risk) + 9p21 CAD loading.",
    },
    "dental": {
        "specialty": "Chirurgien-dentiste",
        "slug": "dentiste",
        "question": "Détartrage + bilan parodontal",
        "why": "Periodontal inflammation raises systemic hs-CRP, which tracks "
               "against both cardiovascular and fertility goals.",
    },
    "eye": {
        "specialty": "Ophtalmologue",
        "slug": "ophtalmologue",
        "question": "Bilan ophtalmologique complet avec fond d'œil",
        "why": "Baseline retinal exam; long booking lead times in Paris — start early.",
    },
    "skin": {
        "specialty": "Dermatologue",
        "slug": "dermatologue",
        "question": "Contrôle des grains de beauté (dépistage mélanome)",
        "why": "Annual mole screening.",
    },
    "body_comp": {
        "specialty": "Centre d'imagerie (DEXA)",
        "slug": "radiologue",
        "question": "Ostéodensitométrie / DEXA avec composition corporelle",
        "why": "Lean-mass and body-fat tracking against the energy protocol.",
    },
    "sleep_study": {
        "specialty": "Médecin du sommeil / centre du sommeil",
        "slug": "pneumologue",
        "question": "Polygraphie ventilatoire nocturne (dépistage apnée du sommeil)",
        "why": "The Fitbit Air reports no sleep data at all, so sleep-disordered "
               "breathing cannot currently be ruled out from wearable data — and "
               "untreated apnoea suppresses testosterone and wrecks HRV.",
    },
    "sperm_analysis": {
        "specialty": "Laboratoire (spermiologie)",
        "slug": "laboratoire",
        "question": "Spermogramme + spermocytogramme, avec fragmentation de l'ADN",
        "why": "Primary fertility endpoint.",
    },
}

# Specialists worth having even when no check-up is overdue, given the goals.
STANDING_CARE: List[Dict[str, str]] = [
    {
        "specialty": "Andrologue / urologue-andrologue",
        "slug": "urologue",
        "question": "Consultation d'andrologie — infertilité masculine, "
                    "bilan et prise en charge",
        "why": "Sperm count, motility, morphology and vitality are all severely "
               "below WHO 2021 references and declining. Self-directed "
               "supplementation has a ceiling; a specialist can order a "
               "varicocele ultrasound, karyotype/Y-microdeletion testing and "
               "hormonal workup that change management.",
        "priority": "high",
    },
    {
        "specialty": "Centre d'AMP / médecine de la reproduction",
        "slug": "gynecologue-medical",
        "question": "Bilan de fertilité du couple (CECOS / centre AMP)",
        "why": "If conception is the goal, couple-level assessment is the route "
               "with the strongest evidence base — and in France it is largely "
               "reimbursed once indicated.",
        "priority": "medium",
    },
]


def _care_dir(config: SyncConfig) -> Path:
    d = config.data_dir / "care"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_providers(config: SyncConfig) -> List[Provider]:
    """User-maintained directory, seeded with the known lab on first run."""
    path = _care_dir(config) / "providers.json"
    if not path.exists():
        save_providers(config, KNOWN_PROVIDERS)
        return list(KNOWN_PROVIDERS)
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return list(KNOWN_PROVIDERS)
    out = []
    for r in raw:
        known = {k: v for k, v in r.items() if k in Provider.__annotations__}
        out.append(Provider(**known))
    return out


def save_providers(config: SyncConfig, providers: List[Provider]) -> Path:
    path = _care_dir(config) / "providers.json"
    path.write_text(json.dumps([asdict(p) for p in providers], indent=2, ensure_ascii=False))
    return path


def add_provider(config: SyncConfig, provider: Provider) -> List[Provider]:
    """Record a real practitioner once the user has actually chosen one."""
    providers = [p for p in load_providers(config) if p.key != provider.key]
    providers.append(provider)
    save_providers(config, providers)
    return providers


def booking_actions(
    config: SyncConfig, today: Optional[date] = None
) -> List[Dict[str, Any]]:
    """What to book right now, derived from overdue check-ups.

    Each entry carries the specialty, the exact French request text, a real
    search URL, and the clinical reason — enough to make the call without
    looking anything up.
    """
    if today is None:
        today = date.today()
    from ..sync.checkup_schedule import overdue_lab_visits

    out: List[Dict[str, Any]] = []
    for item in overdue_lab_visits(config, today):
        key = item.get("key", "")
        route = SPECIALTY_ROUTES.get(key)
        entry: Dict[str, Any] = {
            "key": key,
            "name": item.get("name", key),
            "days_overdue": item.get("days_overdue", 0),
            "prep": item.get("prep", ""),
        }
        # Specialist visits carry lab_provider=SPECIALIST ("specialist") as a
        # placeholder, NOT a real lab — routing on truthiness alone sent the
        # dentist and the cardiologist to the blood lab's website.
        is_specialist = (
            item.get("bundle") == "specialist"
            or item.get("lab_provider") == SPECIALIST
        )
        if item.get("lab_provider") and not is_specialist:
            lab = next(
                (p for p in load_providers(config) if p.kind == "lab"), None
            )
            entry.update({
                "where": item["lab_provider"],
                "address": lab.address if lab else "",
                "ask_for": item.get("lab_panel_name") or item.get("name", ""),
                "search_url": lab.booking_url if lab else "",
                "why": item.get("rationale", ""),
            })
        elif route:
            entry.update({
                "where": route["specialty"],
                "address": HOME_AREA,
                "ask_for": route["question"],
                "search_url": _doctolib_search(route["slug"]),
                "why": route.get("why", item.get("rationale", "")),
            })
        else:
            entry.update({
                "where": "self / home",
                "address": "",
                "ask_for": item.get("name", ""),
                "search_url": "",
                "why": item.get("rationale", ""),
            })
        out.append(entry)
    return out


def render_care_summary(
    config: SyncConfig, today: Optional[date] = None
) -> str:
    """Markdown block for the weekly brief."""
    if today is None:
        today = date.today()
    actions = booking_actions(config, today)
    lines = ["## 🏥 Appointments to book"]
    if not actions:
        lines.append("Nothing overdue. Next check-ups are on schedule.")
    else:
        for a in actions[:6]:
            lines.append(
                f"- **{a['name']}** — {a['days_overdue']}d overdue → "
                f"{a['where']}"
            )
            lines.append(f"  ask for: *{a['ask_for']}*")
            if a.get("prep"):
                lines.append(f"  prep: {a['prep']}")
            if a.get("search_url"):
                lines.append(f"  {a['search_url']}")
        if len(actions) > 6:
            lines.append(f"- …and {len(actions) - 6} more overdue")

    # Standing specialist care is surfaced separately — it is not driven by a
    # date slipping, but by where the biomarkers actually are.
    lines.append("")
    lines.append("### Worth having on the books")
    for s in STANDING_CARE:
        lines.append(f"- **{s['specialty']}** ({s['priority']} priority) — {s['why']}")
        lines.append(f"  ask for: *{s['question']}*  ·  {_doctolib_search(s['slug'])}")
    return "\n".join(lines)


def render_lab_slip(config: SyncConfig, today: Optional[date] = None) -> str:
    """The exact French panel list to hand the lab or ask the GP to prescribe.

    French labs need an ordonnance for most panels; showing the precise panel
    names is the difference between one visit and three.
    """
    if today is None:
        today = date.today()
    from ..sync.checkup_schedule import overdue_lab_visits, upcoming_lab_visits

    panels: List[str] = []
    for item in overdue_lab_visits(config, today):
        # Only real lab panels belong on an ordonnance — a dental cleaning or
        # an ECG is not something the lab draws.
        if item.get("bundle") == "specialist" or item.get("lab_provider") == SPECIALIST:
            continue
        if item.get("lab_panel_name"):
            panels.append(item["lab_panel_name"])
    for v in upcoming_lab_visits(config, within_days=30, today=today):
        for p in v.get("panels", []):
            if p not in panels:
                panels.append(p)
    if not panels:
        return ""
    lab = next((p for p in load_providers(config) if p.kind == "lab"), None)
    lines = ["## 🧾 Lab slip (ordonnance à demander)"]
    if lab:
        lines.append(f"{lab.name} — {lab.address}")
    lines.append("")
    for p in panels:
        lines.append(f"- {p}")
    return "\n".join(lines)
