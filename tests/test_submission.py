"""Publication is a local journal until an operator observes an actual receipt."""

import tempfile
import unittest
from pathlib import Path

from test_evidence import PNG
from test_products import FakeClient, label_values

from cronometer_assistant.models import ValidationError
from cronometer_assistant.products import ProductService
from cronometer_assistant.store import Store
from cronometer_assistant.submission import SubmissionJournal, validate_barcode


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.inbox = self.root / "inbox"
        self.inbox.mkdir(mode=0o700)
        self.store = Store(self.root / "test.sqlite")
        self.client = FakeClient()
        self.service = ProductService(self.store, self.client, evidence_inbox=self.inbox)
        self.pid = self.service.prepare(
            "Synthetic cereal",
            100,
            "g",
            label_values(),
            carbohydrate_basis="excludes_fiber",
            category_id=0,
        )["product"]["proposal_id"]

    def test_barcode_checksum_without_changing_creation_identity(self):
        for code in ["96385074", "036000291452", "4006381333931", "10012345000017"]:
            self.assertEqual(validate_barcode(code), code)
        for code in ["96385073", "036000291451", "123", "٤٠٠٦٣٨١٣٣٣٩٣١"]:
            with self.assertRaises(ValidationError):
                validate_barcode(code)
        self.service.create(self.pid, True)
        before = self.store.get("product_proposals", self.pid)["payload"]
        result = self.service.set_submission_barcode(self.pid, "4006381333931")
        self.assertEqual(result["product"]["proposal_id"], self.pid)
        self.assertEqual(self.store.get("product_proposals", self.pid)["payload"], before)
        self.assertFalse(result["barcode_linked"])
        self.assertEqual(self.client.adds, 1)

    def test_requested_resumes_without_reasking_and_decline_keeps_evidence(self):
        self.service.create(self.pid, True)
        self.assertFalse(
            self.service.public_submission_checklist(self.pid)["should_offer_submission"]
        )
        self.service.attach_evidence_reference(self.pid, "package_front", "host", "front")
        self.assertTrue(
            self.service.public_submission_checklist(self.pid)["should_offer_submission"]
        )
        self.service.set_submission_choice(self.pid, 0, "requested")
        self.service.set_submission_barcode(self.pid, "4006381333931")
        (self.inbox / "front.png").write_bytes(PNG)
        for role in ["package_front", "nutrition_label"]:
            self.service.import_evidence(self.pid, role, "front.png")
        restarted = ProductService(Store(self.store.path), self.client)
        review = restarted.public_submission_checklist(self.pid)
        self.assertFalse(review["should_offer_submission"])
        self.assertTrue(review["ready_for_manual_submission"])
        self.assertFalse(review["submitted"])
        restarted.set_submission_choice(self.pid, 1, "declined")
        review = restarted.public_submission_checklist(self.pid)
        self.assertFalse(review["ready_for_manual_submission"])
        self.assertEqual(len([e for e in review["evidence"] if e["local_bytes_verified"]]), 2)
        self.assertTrue(any(e["local_bytes_verified"] for e in review["evidence"]))
        self.assertEqual(self.client.adds, 1)

    def test_stale_revision_timeout_receipt_and_attribution(self):
        with self.assertRaises(ValidationError):
            self.service.record_submission_outcome(self.pid, 0, "submitted_pending", "receipt")
        self.service.set_submission_choice(self.pid, 0, "requested")
        with self.assertRaises(ValidationError):
            self.service.set_submission_choice(self.pid, 0, "declined")
        with self.assertRaises(ValidationError):
            self.service.record_submission_outcome(self.pid, 1, "accepted", "receipt")
        self.service.record_submission_outcome(self.pid, 1, "unknown", "Response interrupted")
        review = self.service.public_submission_checklist(self.pid)
        self.assertFalse(review["ready_for_manual_submission"])
        self.assertFalse(review["submitted"])
        result = self.service.record_submission_outcome(
            self.pid, 2, "submitted_pending", "UI receipt observed"
        )
        self.assertFalse(result["independently_verified"])
        self.assertEqual(result["submission"]["outcome_source"], "operator_reported")
        self.service.record_submission_outcome(
            self.pid, 3, "accepted", "Operator saw curation approval"
        )
        with self.assertRaises(ValidationError):
            self.service.record_submission_outcome(self.pid, 4, "submitted_pending", "again")
        self.assertEqual(
            SubmissionJournal(Store(self.store.path)).get(self.pid)["status"], "accepted"
        )
        self.assertEqual(self.client.adds, 0)

    def test_stale_remote_food_and_reference_only_evidence_do_not_pass_readiness(self):
        self.service.create(self.pid, True)
        self.service.set_submission_choice(self.pid, 0, "requested")
        self.service.set_submission_barcode(self.pid, "4006381333931")
        self.service.attach_evidence_reference(self.pid, "package_front", "host", "front")
        self.client.foods[123]["category"] = 99
        review = self.service.public_submission_checklist(self.pid)
        self.assertFalse(review["checks"]["verified_private_food"])
        self.assertFalse(review["checks"]["package_front_archived"])
        self.assertFalse(review["ready_for_manual_submission"])
