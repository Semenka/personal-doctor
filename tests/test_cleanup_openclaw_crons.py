import unittest

from scripts.cleanup_openclaw_crons import parse_output, select_jobs


class CleanupCronTests(unittest.TestCase):
    def setUp(self):
        self.jobs = [
            {'id': 'legacy', 'name': 'personal-doctor-oura'},
            {'id': 'main', 'name': 'personal-doctor-daily', 'agentId': 'main'},
            {'id': 'managed', 'name': 'personal-doctor-oura', 'agentId': 'personal-doctor'},
            {'id': 'other', 'name': 'unrelated'},
            {'name': 'personal-doctor-gdrive'},
        ]

    def test_cleanup_preserves_managed_and_unrelated_jobs(self):
        self.assertEqual([j['id'] for j in select_jobs(self.jobs)], ['legacy', 'main'])

    def test_uninstall_can_include_managed_jobs(self):
        self.assertEqual([j['id'] for j in select_jobs(self.jobs, True)],
                         ['legacy', 'main', 'managed'])

    def test_json_notices_and_invalid_data(self):
        self.assertEqual(parse_output('[notice] migration\n{"jobs":[]}'), {'jobs': []})
        with self.assertRaises(ValueError):
            parse_output('{"error":"no jobs"}')


if __name__ == '__main__':
    unittest.main()


def test_gateway_probe_reads_whatsapp_health(monkeypatch):
    import json
    import subprocess
    import types

    from app.sync import whatsapp_sender as ws

    monkeypatch.setattr(ws, "_resolve_openclaw_bin", lambda: "/usr/bin/openclaw")
    account = {"enabled": True, "running": True, "connected": True, "linked": True,
               "healthState": "healthy"}

    def run(health):
        out = json.dumps({"ok": True, "channels": {"whatsapp": {"accounts": {"default": health}}}})
        monkeypatch.setattr(ws.subprocess, "run", lambda *a, **k: types.SimpleNamespace(
            returncode=0, stdout=out, stderr=""))
        return ws.probe_gateway_whatsapp()

    assert run(account) == (True, "")
    assert run({**account, "linked": False})[0] is False

    def timeout(*a, **k):
        raise subprocess.TimeoutExpired("openclaw", 30)

    monkeypatch.setattr(ws.subprocess, "run", timeout)
    ok, err = ws.probe_gateway_whatsapp()
    assert ok is False and "timed out" in err
