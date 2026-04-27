"""One-shot: walk every stored lab-document JSON and run the biomarker
extractor on it. Writes results into data/ingested/biomarkers/results.jsonl.

Idempotent: save_readings() dedupes on (biomarker_id, date, source_file).

Usage:
    cd /Users/andrey/personal-doctor
    .venv/bin/python -m scripts.backfill_biomarkers
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from app.sync.biomarker_extractor import extract_and_save
from app.sync.config import load_config


LAB_KINDS = (
    "blood_test",
    "urine_test",
    "sperm_test",
    "hormone_panel",
    "health_check",
)
# Note: doctor_conclusion / genetic_test are deliberately excluded from
# backfill — they contain prose that triggers false-positive heuristic
# matches. The live pipeline uses Gemini extraction which handles those
# kinds safely; the backfill should not.


def main() -> int:
    config = load_config()
    data_dir = config.data_dir
    if not data_dir.exists():
        print(f"data dir missing: {data_dir}")
        return 1

    candidates = []
    for p in sorted(data_dir.glob("*.json")):
        name = p.name
        if name.startswith("daily_"):
            continue
        if not any(name.startswith(k + "_") for k in LAB_KINDS):
            continue
        candidates.append(p)

    print(f"Found {len(candidates)} candidate lab document(s).")
    total_saved = 0
    for p in candidates:
        try:
            doc = json.loads(p.read_text())
        except Exception as exc:
            print(f"  skip {p.name}: parse error: {exc}")
            continue
        kind = doc.get("kind") or "unclassified"
        raw_text = doc.get("text") or doc.get("raw_text") or ""
        draw_date = doc.get("date")
        source_file = doc.get("original_name") or p.name
        if not raw_text or len(raw_text.strip()) < 80:
            print(f"  skip {p.name}: empty / too short text")
            continue
        readings = extract_and_save(
            config, raw_text, source_kind=kind,
            source_file=source_file, draw_date=draw_date,
        )
        if readings:
            print(f"  ✓ {p.name} ({kind}) — extracted {len(readings)} biomarker(s)")
            for r in readings:
                print(f"      {r.biomarker_id} = {r.value} {r.unit} ({r.flagged or 'in_range'})")
            total_saved += len(readings)
        else:
            print(f"  • {p.name} ({kind}) — no biomarkers extracted")

    print(f"\nBackfill complete: {total_saved} biomarker reading(s) written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
