"""Linking the Google Health API from a phone: consent URL → paste → token."""
from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sync import google_health_link as link  # noqa: E402


def _cfg(tmp_path):
    (tmp_path / "creds").mkdir()
    (tmp_path / "creds" / "credentials.json").write_text("{}")
    return types.SimpleNamespace(
        data_dir=tmp_path, gdrive_credentials_dir=str(tmp_path / "creds"),
        timezone=ZoneInfo("Europe/Paris"), server_url="http://100.64.0.2:8000",
    )


class _StubCreds:
    refresh_token = "r"

    def to_json(self):
        return json.dumps({"refresh_token": "r", "token": "t"})


class _StubFlow:
    made = []

    def __init__(self, redirect_uri, code_verifier):
        self.redirect_uri, self.code_verifier = redirect_uri, code_verifier or "generated-verifier"
        self.exchanged = None
        _StubFlow.made.append(self)

    def authorization_url(self, **kw):
        return f"https://accounts.google.com/o/oauth2/auth?redirect_uri={self.redirect_uri}", "s"

    def fetch_token(self, code):
        self.exchanged = code
        self.credentials = _StubCreds()


@pytest.fixture
def stub_flow(monkeypatch):
    _StubFlow.made.clear()
    monkeypatch.setattr(link, "_make_flow", lambda cfg, uri, code_verifier=None: _StubFlow(uri, code_verifier))
    return _StubFlow


def test_code_from_redirect_accepts_url_query_or_bare_code():
    assert link.code_from_redirect("http://localhost:8770/?code=4%2F0Abc&scope=x") == "4/0Abc"
    assert link.code_from_redirect("?code=abc&scope=x") == "abc"
    assert link.code_from_redirect("  abc  ") == "abc"
    with pytest.raises(ValueError):
        link.code_from_redirect("http://localhost:8770/?error=access_denied")
    with pytest.raises(ValueError):
        link.code_from_redirect("")


def test_consent_then_exchange_reuses_the_pkce_verifier(tmp_path, stub_flow):
    cfg = _cfg(tmp_path)
    url = link.build_consent_url(cfg)
    assert url.startswith("https://accounts.google.com/") and "localhost:8770" in url
    state = json.loads((tmp_path / ".google_health_api_flow.json").read_text())
    assert state["code_verifier"] == "generated-verifier"
    assert not link.is_linked(cfg)

    tok = link.exchange(cfg, "http://localhost:8770/?code=4%2F0Abc&scope=s")
    assert tok == tmp_path / ".google_health_api_token.json" and link.is_linked(cfg)
    assert json.loads(tok.read_text())["refresh_token"] == "r"
    # The exchange Flow was built with the persisted verifier, and the state is gone.
    assert stub_flow.made[-1].code_verifier == "generated-verifier"
    assert stub_flow.made[-1].exchanged == "4/0Abc"
    assert not (tmp_path / ".google_health_api_flow.json").exists()


def test_exchange_without_refresh_token_does_not_save(tmp_path, stub_flow, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(_StubCreds, "refresh_token", None)
    with pytest.raises(RuntimeError):
        link.exchange(cfg, "abc")
    assert not link.is_linked(cfg)


def test_nudge_sends_once_a_day_until_linked(tmp_path, monkeypatch):
    from app.sync import whatsapp_sender as ws

    cfg = _cfg(tmp_path)
    sent = []
    monkeypatch.setattr(ws, "_run_openclaw_send", lambda msg, target=None: sent.append(msg) or True)
    assert link.nudge_if_unlinked(cfg) is True
    assert "http://100.64.0.2:8000/auth/google-health" in sent[0]
    assert link.nudge_if_unlinked(cfg) is False and len(sent) == 1
    (tmp_path / ".google_health_api_token.json").write_text("{}")
    (tmp_path / ".google_health_link_nudge.json").unlink()
    assert link.nudge_if_unlinked(cfg) is False and len(sent) == 1


def test_page_get_and_post(tmp_path, stub_flow, monkeypatch):
    os.environ["HEALTH_LOG_DIR"] = str(tmp_path / "logs")
    from fastapi.testclient import TestClient

    from app import server
    from app.sync import config as cfgmod

    cfg = _cfg(tmp_path)
    monkeypatch.setattr(cfgmod, "load_config", lambda: cfg)
    monkeypatch.setattr(link, "verify", lambda c: {
        "date": "2026-09-06", "steps": 4200, "sleep_hours": 6.8, "resting_hr": 57,
        "hrv": 31.0, "spo2": 96.0, "origins": ["google_health_api:FITBIT:Fitbit Air"], "errors": [],
    })
    client = TestClient(server.create_app())
    page = client.get("/auth/google-health")
    assert page.status_code == 200
    assert "Authorize with Google" in page.text and "accounts.google.com" in page.text
    assert "Publish app" in page.text  # the Cloud Console prerequisites

    done = client.post("/auth/google-health", data={"redirect": "http://localhost:8770/?code=zz&scope=s"})
    assert done.status_code == 200 and "Linked and saved" in done.text
    assert "Fitbit Air" in done.text and link.is_linked(cfg)

    bad = client.post("/auth/google-health", data={"redirect": "http://localhost:8770/?error=denied"})
    assert bad.status_code == 200 and "Not linked" in bad.text and "Authorize with Google" in bad.text


def test_digest_line_carries_the_phone_link(monkeypatch, tmp_path):
    from app.sync import whatsapp_sender as ws

    sent = {}
    monkeypatch.setattr(ws, "_run_openclaw_send", lambda msg, target=None: (sent.setdefault("msg", msg), True)[1])
    monkeypatch.setattr(ws, "_completion_footer", lambda cfg: "footer")
    monkeypatch.setattr(ws, "_yesterday_activity_line", lambda cfg, day: "")
    cfg = types.SimpleNamespace(data_dir=tmp_path, email_to="", smtp_host="", server_url="http://100.64.0.2:8000")
    ws.send_whatsapp_advice(cfg, {"date": "2026-09-06", "model": "x", "advice": "1. **Walk**",
                                  "context_summary": {"watch_devices": "Fitbit Air silent 33d"}})
    assert "http://100.64.0.2:8000/auth/google-health" in sent["msg"].splitlines()[1]
