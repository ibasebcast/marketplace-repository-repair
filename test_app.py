"""Exercise the app's browser flow and simulated Supervisor lifecycle."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from time import monotonic, sleep
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

from marketplace_repair import app


class BrowserRepairTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        config = Path(temp.name)
        (config / ".storage").mkdir()
        self.path = config / ".storage" / app.STORE_KEY
        self.backup = self.path.with_name(self.path.name + ".before-repository-repair")
        self.recovery = config / "core-restart-pending"
        manifest = config / "custom_components" / "alarmdotcom" / "manifest.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text('{"domain":"alarmdotcom"}', encoding="utf-8")
        self.registry = config / ".storage" / "core.entity_registry"
        self.registry.write_bytes(b"unchanged registry")
        old, new = app.ALARM_DOT_COM_REPOS
        self.document = {
            "key": app.STORE_KEY, "version": 1, "data": {
                "100": {"full_name": old, "category": "integration",
                        "domain": "alarmdotcom", "installed": True},
                "200": {"full_name": new, "category": "integration", "domain": "alarmdotcom"},
            }
        }
        self.original = json.dumps(self.document).encode()
        self.path.write_bytes(self.original)
        self.state = app.AppState()
        self.calls = []
        self.patch = patch.multiple(
            app, CONFIG=config, RECOVERY_FILE=self.recovery,
            INGRESS_PEER="127.0.0.1", STATE=self.state, supervisor=self.supervisor,
        )
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        worker = Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        worker.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/"
        self.addCleanup(self.server.server_close)
        self.addCleanup(worker.join)
        self.addCleanup(self.server.shutdown)

    def supervisor(self, action):
        self.calls.append(action)

    def request(self, **fields):
        data = urlencode(fields).encode() if fields else None
        try:
            with urlopen(self.url, data=data, timeout=2) as response:
                return response.status, response.read().decode()
        except HTTPError as error:
            with error:
                return error.code, error.read().decode()

    def preview(self):
        status, body = self.request(action="alarm", csrf=self.state.csrf)
        self.assertEqual(status, 200)
        self.assertIn("Review the repair", body)
        self.assertIn("pyalarmdotcom/alarmdotcom", body)
        return self.state.preview_id

    def wait_for_repair(self):
        deadline = monotonic() + 2
        while self.state.busy and monotonic() < deadline:
            sleep(0.01)
        self.assertFalse(self.state.busy, "Repair did not finish")

    def test_preview_confirmation_stops_repairs_and_starts_core(self):
        _, landing = self.request()
        self.assertIn("Preview Alarm.com repair", landing)
        self.assertIn("Open Alarm.com in Marketplace", landing)
        self.assertIn("owner=ibasebcast&amp;repository=ha-alarmdotcom&amp;category=integration", landing)
        preview = self.preview()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.path.read_bytes(), self.original)
        status, body = self.request(action="apply", csrf=self.state.csrf, preview=preview, backup="yes")
        self.assertEqual(status, 202)
        self.assertIn("Repair started", body)
        self.wait_for_repair()
        self.assertEqual(self.calls, ["stop", "start"])
        self.assertEqual(self.backup.read_bytes(), self.original)
        self.assertEqual(self.registry.read_bytes(), b"unchanged registry")
        self.assertFalse(self.recovery.exists())
        self.assertNotIn("installed", json.loads(self.path.read_bytes())["data"]["100"])
        self.assertIn("Repair complete", self.request()[1])

    def test_refuses_untrusted_peer_and_requests_without_confirmation(self):
        with patch.object(app, "INGRESS_PEER", "172.30.32.2"):
            self.assertEqual(self.request()[0], 403)
        preview = self.preview()
        self.assertEqual(self.request(action="apply", preview=preview, backup="yes")[0], 403)
        self.assertEqual(self.request(action="apply", csrf=self.state.csrf, preview=preview)[0], 400)
        self.assertEqual(self.request(action="apply", csrf=self.state.csrf, preview="old", backup="yes")[0], 400)
        self.assertEqual(self.calls, [])
        self.assertFalse(self.backup.exists())

    def test_storage_change_during_shutdown_still_starts_core(self):
        def stop_and_change(action):
            self.calls.append(action)
            if action == "stop":
                self.document["data"]["200"]["installed"] = True
                self.path.write_text(json.dumps(self.document), encoding="utf-8")

        with patch.object(app, "supervisor", side_effect=stop_and_change):
            preview = self.preview()
            self.request(action="apply", csrf=self.state.csrf, preview=preview, backup="yes")
            self.wait_for_repair()
        self.assertEqual(self.calls, ["stop", "start"])
        self.assertIn("conflict changed during shutdown", self.request()[1])
        self.assertFalse(self.backup.exists())
        self.assertEqual(json.loads(self.path.read_bytes()), self.document)

    def test_app_restart_recovers_core_when_start_was_interrupted(self):
        self.recovery.touch()
        self.state.resume_core()
        self.assertEqual(self.calls, ["start"])
        self.assertFalse(self.recovery.exists())
        self.assertIn("interrupted repair", self.request()[1])
