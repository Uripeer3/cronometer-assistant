"""Consistent SQLite/evidence backup and isolated restore using synthetic state."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_evidence import PNG
from test_live_drafts import DraftClient
from test_products import FakeClient, label_values

from cronometer_assistant.backups import create_backup, restore_backup, verify_backup
from cronometer_assistant.live_drafts import LiveDraftService
from cronometer_assistant.models import ValidationError
from cronometer_assistant.products import ProductService
from cronometer_assistant.store import Store


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / "source.sqlite")
        self.inbox = self.root / "inbox"
        self.inbox.mkdir(mode=0o700)
        (self.inbox / "label.png").write_bytes(PNG)
        self.client = FakeClient()
        self.products = ProductService(self.store, self.client, evidence_inbox=self.inbox)
        self.pid = self.products.prepare(
            "Synthetic cereal",
            100,
            "g",
            label_values(),
            carbohydrate_basis="excludes_fiber",
            category_id=0,
        )["product"]["proposal_id"]
        self.products.import_evidence(self.pid, "nutrition_label", "label.png")
        self.products.set_submission_choice(self.pid, 0, "declined")
        drafts = LiveDraftService(self.store, DraftClient())
        drafts.verify_food("crono:5", "cooked", True, True)
        drafts.remember_product("האורז שלי", "crono:5")
        self.meal = drafts.begin_meal("ארוחה")["meal"]

    def test_backup_and_restore_recover_drafts_evidence_choices_without_credentials(self):
        (self.root / "session.json").write_text("synthetic-secret-must-not-copy")
        with self.store.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
        self.store.put("test", "wal", {"committed": True})
        backup = self.root / "backup"
        self.assertTrue(create_backup(self.store, backup)["verified"])
        self.assertEqual((backup / "state.sqlite").stat().st_mode & 0o777, 0o600)
        self.assertFalse((backup / "session.json").exists())
        destination = self.root / "restore"
        self.assertTrue(restore_backup(backup, destination)["restored"])
        restored = Store(destination / "state.sqlite")
        self.assertTrue(restored.get("test", "wal")["committed"])
        products = ProductService(restored, self.client)
        self.assertEqual(products.submissions.get(self.pid)["choice"], "declined")
        self.assertTrue(products.review_evidence(self.pid)["evidence"][0]["local_bytes_verified"])
        drafts = LiveDraftService(restored, DraftClient())
        self.assertEqual(drafts.list_drafts()["drafts"][0]["meal_id"], self.meal["meal_id"])
        self.assertEqual(drafts.resolve_food("האורז שלי")["candidates"][0]["food_id"], "crono:5")
        with self.assertRaises(ValidationError):
            restore_backup(backup, destination)

    def test_corruption_missing_evidence_and_incomplete_backup_are_rejected(self):
        backup = self.root / "backup"
        create_backup(self.store, backup)
        photo = next((backup / "evidence").iterdir())
        photo.write_bytes(b"corrupt")
        with self.assertRaises(ValidationError):
            verify_backup(backup)
        with self.assertRaises(ValidationError):
            restore_backup(backup, self.root / "bad-restore")
        self.assertFalse((self.root / "bad-restore").exists())
        incomplete = self.root / "incomplete"
        incomplete.mkdir(mode=0o700)
        with self.assertRaises(ValidationError):
            verify_backup(incomplete)
        original = next((self.root / "evidence").iterdir())
        original.unlink()
        with self.assertRaises(ValidationError):
            create_backup(self.store, self.root / "missing-source")
        self.assertFalse((self.root / "missing-source" / "manifest.json").exists())

    def test_manifest_traversal_symlinks_and_omitted_references_are_rejected(self):
        for kind in ["traversal", "omitted", "symlink"]:
            backup = self.root / kind
            create_backup(self.store, backup)
            p = backup / "manifest.json"
            m = json.loads(p.read_text())
            if kind == "traversal":
                m["files"][0]["path"] = "../source.sqlite"
            elif kind == "omitted":
                m["files"] = m["files"][:1]
            else:
                photo = next((backup / "evidence").iterdir())
                photo.unlink()
                photo.symlink_to(self.inbox / "label.png")
            p.write_text(json.dumps(m))
            with self.assertRaises(ValidationError):
                verify_backup(backup)

    def test_existing_destination_is_not_overwritten(self):
        backup = self.root / "backup"
        backup.mkdir(mode=0o700)
        marker = backup / "keep.txt"
        marker.write_text("keep")
        with self.assertRaises(ValidationError):
            create_backup(self.store, backup)
        self.assertEqual(marker.read_text(), "keep")

    def test_cli_backup_verify_restore_and_missing_arguments(self):
        backup = self.root / "cli-backup"

        def run(*args):
            return subprocess.run(
                [sys.executable, "-m", "cronometer_assistant", *args],
                capture_output=True,
                text=True,
            )

        created = run("backup-runtime", "--db", str(self.store.path), "--backup-dir", str(backup))
        self.assertEqual(created.returncode, 0, created.stderr)
        self.assertTrue(json.loads(created.stdout)["verified"])
        self.assertEqual(run("verify-backup", "--backup-dir", str(backup)).returncode, 0)
        restored = run(
            "restore-runtime",
            "--backup-dir",
            str(backup),
            "--restore-dir",
            str(self.root / "cli-restored"),
        )
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertTrue(json.loads(restored.stdout)["restored"])
        self.assertEqual(run("backup-runtime").returncode, 1)
        missing = self.root / "does-not-exist"
        self.assertEqual(run("verify-backup", "--backup-dir", str(missing)).returncode, 1)
        self.assertFalse(missing.exists())
