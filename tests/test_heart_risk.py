"""Heart-risk block: single lipid readings and never-measured Lp(a)/ApoB reach the plan."""
from __future__ import annotations

import json
import sys
import types
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _cfg(tmp_path, rows):
    d = tmp_path / "biomarkers"
    d.mkdir(parents=True, exist_ok=True)
    (d / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return types.SimpleNamespace(data_dir=tmp_path)


def _row(marker, value, unit, day):
    return {"biomarker_id": marker, "value": value, "unit": unit, "date": day,
            "source_kind": "blood_test", "source_file": "x", "flagged": None}


def test_single_high_ldl_and_missing_lpa_surface(tmp_path):
    from app.care.heart_risk import advisor_context_line, heart_risk_status, render_heart_risk

    cfg = _cfg(tmp_path, [_row("ldl", 169.0, "mg/dL", "2026-04-27"), _row("hdl", 61.0, "mg/dL", "2026-04-27")])
    s = heart_risk_status(cfg, date(2026, 11, 8))
    assert s["discuss_with_doctor"] and s["missing"] == ["Lp(a)", "ApoB"]
    assert s["ldl_age_days"] == 195

    block = render_heart_risk(cfg, date(2026, 11, 8))
    assert "LDL: 169 mg/dL (2026-04-27)" in block
    assert "Lp(a): **never measured**" in block
    assert "quel objectif de LDL" in block and "195 days old" in block

    line = advisor_context_line(cfg, date(2026, 11, 8))
    assert "LDL 169 mg/dL" in line and "Never measured: Lp(a), ApoB" in line


def test_nothing_to_say_when_all_measured_and_in_range(tmp_path):
    from app.care.heart_risk import advisor_context_line, render_heart_risk

    cfg = _cfg(tmp_path, [_row("ldl", 95.0, "mg/dL", "2026-10-01"), _row("apob", 80.0, "mg/dL", "2026-10-01"),
                          _row("lp_a", 30.0, "nmol/L", "2026-10-01")])
    assert render_heart_risk(cfg, date(2026, 11, 8)) == ""
    assert advisor_context_line(cfg, date(2026, 11, 8)) == ""


def test_care_summary_carries_the_heart_block(tmp_path, monkeypatch):
    from app.care import providers

    cfg = _cfg(tmp_path, [_row("ldl", 169.0, "mg/dL", "2026-04-27")])
    monkeypatch.setattr(providers, "booking_actions", lambda c, t: [])
    out = providers.render_care_summary(cfg, date(2026, 11, 8))
    assert "### ❤️ Heart risk" in out


def test_advisor_rules_cover_report_recommendations():
    from app.sync.daily_advisor import SYSTEM_PROMPT

    assert "22. Know your Lp(a)" in SYSTEM_PROMPT
    assert "23. Plastics" in SYSTEM_PROMPT and "associational" in SYSTEM_PROMPT
    assert "24. Never recommend unapproved or compounded peptides" in SYSTEM_PROMPT
