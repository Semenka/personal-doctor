"""Lab values are compared in one unit per marker, whatever the report printed."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sync.biomarker_extractor import extract_biomarkers_heuristic, load_all_readings  # noqa: E402
from app.sync.biomarker_trends import prev_vs_new  # noqa: E402
from app.sync.biomarkers import normalize_unit  # noqa: E402


def _store(tmp_path, rows):
    d = tmp_path / "biomarkers"
    d.mkdir()
    (d / "results.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )
    return SimpleNamespace(data_dir=tmp_path)


def test_si_units_are_converted_and_others_left_alone():
    assert normalize_unit("zinc", 15, "µmol/L") == (98.1, "µg/dL", True)
    assert normalize_unit("zinc", 96, "ug/dL")[:2] == (96, "µg/dL")
    assert normalize_unit("testosterone_total", 15.3, "nmol/L")[0] == 441.252
    assert normalize_unit("lh", 4.0, "UI/L") == (4.0, "mIU/mL", False)
    # Unknown units are never rescaled on a guess.
    assert normalize_unit("zinc", 96, "per bushel") == (96, "per bushel", False)


def test_stored_zinc_in_umol_no_longer_reads_as_a_540pct_jump(tmp_path):
    # 2026-09-28 digest: "Zinc 15→96 µg/dL (+540%)" — two normal results,
    # the first printed in µmol/L.
    cfg = _store(tmp_path, [
        {"biomarker_id": "zinc", "value": 15, "unit": "µmol/L",
         "date": "2025-11-02", "source_kind": "blood_test", "flagged": "low"},
        {"biomarker_id": "zinc", "value": 96, "unit": "µg/dL",
         "date": "2026-09-20", "source_kind": "blood_test"},
    ])
    rows = load_all_readings(cfg)
    assert rows[0]["value"] == 98.1 and rows[0]["unit"] == "µg/dL"
    assert rows[0]["flagged"] != "low"
    delta = prev_vs_new(cfg, "zinc")
    assert abs(delta["pct_change"]) < 5


def test_mislabelled_unit_is_not_inflated(tmp_path):
    # A µg/dL value tagged "µmol/L" would become 628 µg/dL — implausible, so
    # the label is the error and the reading is kept as printed.
    cfg = _store(tmp_path, [
        {"biomarker_id": "zinc", "value": 96, "unit": "µmol/L",
         "date": "2026-09-20", "source_kind": "blood_test"},
    ])
    assert load_all_readings(cfg)[0]["value"] == 96


def test_testosterone_in_nmol_survives_the_sanity_floor():
    out = extract_biomarkers_heuristic(
        "Testostérone totale : 15,3 nmol/L", "blood_test", "bilan.pdf", "2026-09-20"
    )
    assert [(r.biomarker_id, r.value, r.unit) for r in out] == [
        ("testosterone_total", 441.252, "ng/dL")
    ]
