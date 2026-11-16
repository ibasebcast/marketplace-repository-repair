"""Exercise the CLI with Home Assistant 2026.11 Marketplace storage fixtures."""

import json
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import stat
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import repair


SCRIPT = Path(__file__).with_name("repair.py")
OLD = "original/alarm"
NEW = "maintainer/alarm"
STORE = "marketplace.repositories"


class RepairTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.config = Path(temp.name)
        storage = self.config / ".storage"
        storage.mkdir()
        self.store = storage / STORE
        self.backup = storage / f"{STORE}.before-repository-repair"
        self.manifest = self.config / "custom_components" / "alarm" / "manifest.json"
        self.manifest.parent.mkdir(parents=True)
        self.manifest.write_text('{"domain":"alarm","version":"1.0"}', encoding="utf-8")
        self.registries = {
            storage / name: b'{"test_fixture":true}'
            for name in ("core.config_entries", "core.device_registry", "core.entity_registry")
        }
        for path, contents in self.registries.items():
            path.write_bytes(contents)
        self.document = {
            "version": 1,
            "minor_version": 1,
            "key": STORE,
            "data": {
                "1000": {
                    "full_name": OLD,
                    "category": "integration",
                    "domain": "alarm",
                    "installed": True,
                    "installed_commit": "old-commit",
                    "version_installed": "1.0",
                    "repository_manifest": {"name": "Original"},
                },
                "2000": {"full_name": NEW, "category": "integration", "domain": "alarm"},
                "3000": {
                    "full_name": "owner/another", "category": "integration",
                    "installed": True, "domain": "another",
                },
            },
        }

    def write_store(self):
        raw = json.dumps(self.document, separators=(",", ":")).encode()
        self.store.write_bytes(raw)
        self.store.chmod(0o640)
        return raw

    def invoke(self, *options):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--config", str(self.config),
             "--from", OLD, "--to", NEW, *options],
            capture_output=True, text=True, check=False,
        )

    def test_preview_leaves_storage_unchanged(self):
        original = self.write_store()
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"install {NEW}", result.stdout)
        self.assertEqual(self.store.read_bytes(), original)
        self.assertFalse(self.backup.exists())

    def test_apply_clears_one_owner_and_preserves_other_data(self):
        original = self.write_store()
        manifest = self.manifest.read_bytes()
        result = self.invoke("--apply", "--core-stopped")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.backup.read_bytes(), original)
        self.assertEqual(stat.S_IMODE(self.store.stat().st_mode), 0o640)
        for key in ("installed", "installed_commit", "version_installed"):
            del self.document["data"]["1000"][key]
        self.assertEqual(json.loads(self.store.read_bytes()), self.document)
        self.assertEqual(self.manifest.read_bytes(), manifest)
        for path, original_content in self.registries.items():
            self.assertEqual(path.read_bytes(), original_content)
        result = self.invoke("--apply", "--core-stopped")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("No change needed", result.stdout)
        self.assertEqual(self.backup.read_bytes(), original)

    def test_duplicate_install_keeps_the_replacement_installed(self):
        self.document["data"]["2000"]["installed"] = True
        self.document["data"]["2000"]["version_installed"] = "2.0"
        self.write_store()
        result = self.invoke("--apply", "--core-stopped")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"update {NEW}", result.stdout)
        self.assertEqual(json.loads(self.store.read_bytes())["data"]["2000"],
                         self.document["data"]["2000"])

    def test_apply_requires_core_stopped_confirmation(self):
        original = self.write_store()
        result = self.invoke("--apply")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.store.read_bytes(), original)
        self.assertFalse(self.backup.exists())

    def test_rejects_mismatched_domain_third_owner_and_unknown_schema(self):
        for case, error in (
            ("different domain", "same integration domain"),
            ("third owner", "Another installed repository"),
            ("unknown format", "Unsupported Marketplace"),
            ("missing target", "no stored record"),
        ):
            with self.subTest(case=case):
                snapshot = json.loads(json.dumps(self.document))
                if case == "different domain":
                    self.document["data"]["2000"]["domain"] = "other"
                elif case == "third owner":
                    self.document["data"]["3000"]["domain"] = "alarm"
                elif case == "unknown format":
                    self.document["minor_version"] = 2
                else:
                    del self.document["data"]["2000"]
                original = self.write_store()
                result = self.invoke("--apply", "--core-stopped")
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn(error, result.stderr)
                self.assertEqual(self.store.read_bytes(), original)
                self.assertFalse(self.backup.exists())
                self.document = snapshot

    def test_refuses_to_overwrite_rollback_copy(self):
        original = self.write_store()
        self.backup.write_bytes(b"prior backup")
        result = self.invoke("--apply", "--core-stopped")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.store.read_bytes(), original)
        self.assertEqual(self.backup.read_bytes(), b"prior backup")

    def test_refuses_missing_integration_files(self):
        original = self.write_store()
        self.manifest.unlink()
        result = self.invoke("--apply", "--core-stopped")
        self.assertEqual(result.returncode, 1)
        self.assertIn("manifest is missing", result.stderr)
        self.assertEqual(self.store.read_bytes(), original)
        self.assertFalse(self.backup.exists())

    def guided(self, answer, cli, *arguments):
        with (
            patch("repair.sys.stdin.isatty", return_value=True),
            patch("repair.shutil.which", return_value="/usr/bin/ha"),
            patch("builtins.input", return_value=answer),
            patch("repair.subprocess.run", side_effect=cli),
            redirect_stdout(StringIO()),
            redirect_stderr(StringIO()),
        ):
            return repair.main([
                "--config", str(self.config),
                *(arguments or ("--guided", "--from", OLD, "--to", NEW)),
            ])

    def test_guided_cancel_leaves_core_and_files_alone(self):
        original = self.write_store()

        def cli(*args, **kwargs):
            self.fail("Cancellation should never run the Supervisor CLI")

        self.assertEqual(self.guided("", cli), 0)
        self.assertEqual(self.store.read_bytes(), original)
        self.assertFalse(self.backup.exists())

    def test_guided_stops_repairs_and_starts_core(self):
        original = self.write_store()
        calls = []

        def cli(command, *, check):
            self.assertTrue(check)
            action = command[-1]
            calls.append(action)
            if action == "stop":
                self.assertEqual(self.store.read_bytes(), original)
            else:
                self.assertEqual(self.backup.read_bytes(), original)
                self.assertNotIn("installed", json.loads(self.store.read_bytes())["data"]["1000"])

        self.assertEqual(self.guided("REPAIR", cli), 0)
        self.assertEqual(calls, ["stop", "start"])

    def test_alarmdotcom_preset_selects_the_published_repositories(self):
        old, new = self.document["data"]["1000"], self.document["data"]["2000"]
        old["full_name"] = "pyalarmdotcom/alarmdotcom"
        new["full_name"] = "ibasebcast/ha-alarmdotcom"
        old["domain"] = new["domain"] = "alarmdotcom"
        self.manifest.write_text('{"domain":"alarmdotcom"}', encoding="utf-8")
        self.manifest.parent.rename(self.manifest.parent.with_name("alarmdotcom"))
        self.write_store()
        calls = []

        def cli(command, *, check):
            calls.append(command[-1])

        self.assertEqual(self.guided("REPAIR", cli, "--alarmdotcom"), 0)
        self.assertEqual(calls, ["stop", "start"])
        self.assertNotIn("installed", json.loads(self.store.read_bytes())["data"]["1000"])

    def test_guided_revalidates_storage_after_stop_and_starts_core_on_failure(self):
        self.write_store()
        calls = []

        def cli(command, *, check):
            action = command[-1]
            calls.append(action)
            if action == "stop":
                self.document["data"]["2000"]["installed"] = True
                self.write_store()

        self.assertEqual(self.guided("REPAIR", cli), 1)
        self.assertEqual(calls, ["stop", "start"])
        self.assertEqual(json.loads(self.store.read_bytes()), self.document)
        self.assertFalse(self.backup.exists())
