from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.sync.checkup_schedule import latest_result_date
from app.sync.biomarkers import find_by_alias
from app.sync.config import SyncConfig
from app.sync.llm_client import _generate_codex
from app.sync.report_summarizer import summarize_report_text
from app.sync.storage import write_lab_document_json


def config_for(data_dir: Path) -> SyncConfig:
    return SyncConfig(
        data_dir=data_dir,
        oura_access_token=None,
        timezone=ZoneInfo("Europe/Paris"),
        database_url=None,
        openalex_mailto=None,
        gdrive_credentials_dir=None,
        google_api_key=None,
        openai_api_key=None,
        email_to=None,
        smtp_host=None,
        smtp_port=None,
        smtp_user=None,
        smtp_password=None,
    )


class DriveReportStorageTests(unittest.TestCase):
    def test_drive_reports_from_same_kind_and_date_do_not_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            first = write_lab_document_json(
                data_dir,
                "blood_test",
                "2026-08-08",
                {"kind": "blood_test", "drive_file_id": "drive-a", "text": "first"},
            )
            second = write_lab_document_json(
                data_dir,
                "blood_test",
                "2026-08-08",
                {"kind": "blood_test", "drive_file_id": "drive-b", "text": "second"},
            )

            self.assertNotEqual(first, second)
            self.assertEqual(json.loads(first.read_text())["date"], "2026-08-08")
            self.assertEqual(json.loads(second.read_text())["text"], "second")

    def test_schedule_reads_date_from_drive_report_filename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            write_lab_document_json(
                data_dir,
                "sperm_test",
                "2026-08-08",
                {"kind": "sperm_test", "drive_file_id": "drive-a"},
            )

            self.assertEqual(
                latest_result_date(config_for(data_dir), "sperm_analysis"),
                date(2026, 8, 8),
            )

    def test_codex_image_prompt_is_sent_over_stdin(self) -> None:
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["input"] = kwargs.get("input")
            output_path = Path(cmd[cmd.index("--output-last-message") + 1])
            output_path.write_text("result")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with patch("app.sync.llm_client._codex_cli_path", return_value="/usr/bin/codex"), patch(
            "app.sync.llm_client._codex_exec_env", return_value={}
        ), patch("app.sync.llm_client.subprocess.run", side_effect=fake_run):
            result = _generate_codex(
                system="system",
                user="user",
                model="test-model",
                reasoning="low",
                timeout_s=10,
                image_path=Path("scan.jpg"),
            )

        self.assertEqual(result, "result")
        self.assertIn("[SYSTEM]", captured["input"])
        self.assertIn("user", captured["input"])
        self.assertNotIn(captured["input"], captured["cmd"])

    def test_report_summary_records_active_model(self) -> None:
        response = json.dumps(
            {
                "summary": "summary",
                "flags": [],
                "severity": "NORMAL",
                "specialist_referral": False,
                "follow_ups": [],
            }
        )
        with patch("app.sync.llm_client.has_credentials", return_value=True), patch(
            "app.sync.llm_client.generate", return_value=response
        ), patch(
            "app.sync.llm_client.provider_info",
            return_value={"provider": "codex", "model": "gpt-test"},
        ):
            summary = summarize_report_text(
                config_for(Path(".")), "blood_test", "x" * 100, "report.pdf"
            )

        self.assertIsNotNone(summary)
        self.assertEqual(summary["provider"], "codex")
        self.assertEqual(summary["model"], "gpt-test")

    def test_historical_russian_hormone_labels_are_registered(self) -> None:
        self.assertEqual(find_by_alias("Дигидротестостерон").id, "dihydrotestosterone")
        self.assertEqual(find_by_alias("17-ОН-прогестерон").id, "17oh_progesterone")


if __name__ == "__main__":
    unittest.main()
