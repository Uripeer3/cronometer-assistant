"""Synthetic byte fixtures; no real photos or account data."""

import os
import tempfile
import unittest
from pathlib import Path

from cronometer_assistant.evidence import MAX_IMAGE_BYTES, EvidenceArchive
from cronometer_assistant.models import ValidationError
from cronometer_assistant.store import Store

PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR" + b"\x00\x00\x00\x01" * 2


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.inbox = self.root / "inbox"
        self.inbox.mkdir(mode=0o700)
        self.store = Store(self.root / "test.sqlite")
        self.archive = EvidenceArchive(self.store, self.root / "archive", inbox=self.inbox)
        (self.inbox / "label.png").write_bytes(PNG)

    def test_import_is_private_deduplicated_and_survives_restart(self):
        a = self.archive.import_file("product-a", "nutrition_label", "label.png")
        b = self.archive.import_file("product-a", "nutrition_label", "label.png")
        self.assertEqual(a, b)
        restored = EvidenceArchive(Store(self.store.path), self.root / "archive")
        self.assertTrue(restored.review("product-a")[0]["local_bytes_verified"])
        self.assertEqual(restored.review("product-b"), [])
        self.assertEqual(os.stat(self.root / "archive" / a["sha256"]).st_mode & 0o777, 0o600)
        self.assertFalse(a["cronometer_uploaded"])
        self.assertNotIn(str(self.root), str(a))

    def test_refs_are_not_local_or_uploaded_and_roles_are_isolated(self):
        self.archive.reference("p", "package_front", "chatgpt_library", "libfile_synthetic")
        self.archive.reference("p", "package_front", "chatgpt_library", "libfile_synthetic")
        rows = self.archive.review("p")
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["local_bytes_verified"])
        self.assertEqual(rows[0]["storage"], "host_reference_only")
        with self.assertRaises(ValidationError):
            self.archive.reference("p", "package_front", "host", "https://secret.example/token")

    def test_traversal_symlinks_non_images_and_oversize_are_rejected(self):
        for name in ["../label.png", "/etc/passwd", "..", "sub/file.png"]:
            with self.assertRaises(ValidationError):
                self.archive.import_file("p", "nutrition_label", name)
        (self.inbox / "link").symlink_to(self.inbox / "label.png")
        (self.inbox / "text").write_text("not an image")
        with (self.inbox / "large").open("wb") as f:
            f.truncate(MAX_IMAGE_BYTES + 1)
        for name in ["link", "text", "large"]:
            with self.assertRaises(ValidationError):
                self.archive.import_file("p", "nutrition_label", name)
        os.mkfifo(self.inbox / "fifo")
        with self.assertRaises(ValidationError):
            self.archive.import_file("p", "nutrition_label", "fifo")

    def test_corruption_is_reported_and_not_overwritten(self):
        row = self.archive.import_file("p", "nutrition_label", "label.png")
        (self.root / "archive" / row["sha256"]).write_bytes(b"corrupt")
        self.assertFalse(self.archive.review("p")[0]["local_bytes_verified"])
        with self.assertRaises(ValidationError):
            self.archive.import_file("p", "nutrition_label", "label.png")

    def test_disabled_inbox_and_insecure_directories(self):
        disabled = EvidenceArchive(self.store, self.root / "archive")
        with self.assertRaises(ValidationError):
            disabled.import_file("p", "nutrition_label", "label.png")
        self.inbox.chmod(0o755)
        with self.assertRaises(ValidationError):
            EvidenceArchive(self.store, self.root / "archive", inbox=self.inbox)
