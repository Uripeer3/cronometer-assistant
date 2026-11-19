"""Synthetic existing-food repairs; no live account or catalog fixtures."""

import tempfile
import unittest
from pathlib import Path

from test_products import FakeClient, label_values

from cronometer_assistant.models import ValidationError
from cronometer_assistant.products import ProductService
from cronometer_assistant.repairs import ProductRepairs
from cronometer_assistant.store import Store


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "test.sqlite")
        self.client = FakeClient()
        self.service = ProductService(self.store, self.client)
        self.pid = self.service.prepare(
            "Synthetic cereal",
            100,
            "g",
            label_values(),
            carbohydrate_basis="excludes_fiber",
            category_id=0,
        )["product"]["proposal_id"]
        self.service.create(self.pid, True, self.service.review(self.pid)["review_token"])
        self.repairs = ProductRepairs(self.service)

    def test_adopts_same_food_without_recreation_and_later_barcode_reuses_identity(self):
        self.client.foods[123]["category"] = 5
        review = self.repairs.prepare(self.pid, 5, 100)
        self.assertTrue(review["observed_matches_target"])
        self.assertNotIn("owner", str(review))
        with self.assertRaises(ValidationError):
            self.repairs.adopt(review["repair_id"], False)
        result = self.repairs.adopt(review["repair_id"], True)
        self.assertTrue(result["created"])
        self.assertFalse(result["remote_food_changed"])
        self.assertEqual(result["product"]["remote_food_id"], 123)
        self.service.set_submission_barcode(self.pid, "4006381333931")
        reused = self.service.prepare(
            "Synthetic cereal",
            100,
            "g",
            label_values(),
            carbohydrate_basis="excludes_fiber",
            category_id=5,
            barcode="4006381333931",
        )
        self.assertEqual(reused["product"]["proposal_id"], self.pid)
        self.assertTrue(
            self.service.create(self.pid, True, self.service.review(self.pid)["review_token"])[
                "created"
            ]
        )
        self.assertTrue(self.repairs.adopt(review["repair_id"], True)["already_adopted"])
        self.assertEqual(self.client.adds, 1)

    def test_conflict_requires_fresh_review(self):
        review = self.repairs.prepare(self.pid, 0, 100)
        self.client.foods[123]["category"] = 5
        with self.assertRaisesRegex(ValidationError, "Remote food changed"):
            self.repairs.adopt(review["repair_id"], True)
        self.assertEqual(self.store.get("product_proposals", self.pid)["category_id"], 0)
        self.assertEqual(self.client.adds, 1)

    def test_rejects_wrong_owner_marker_nutrients_and_atomic_measures(self):
        for field, value in [("owner", 8), ("comments", "foreign"), ("retired", True)]:
            original = self.client.foods[123][field]
            self.client.foods[123][field] = value
            with self.assertRaises(ValidationError):
                self.repairs.prepare(self.pid, 0, 100)
            self.client.foods[123][field] = original
        self.client.foods[123]["nutrients"][0]["amount"] += 10
        review = self.repairs.prepare(self.pid, 0, 100)
        self.assertFalse(review["observed_matches_target"])
        with self.assertRaises(ValidationError):
            self.repairs.adopt(review["repair_id"], True)
        self.client.foods[123]["nutrients"][0]["amount"] -= 10
        self.client.foods[123]["measures"][0]["type"] = "Atomic"
        review = self.repairs.prepare(self.pid, 0, 100)
        with self.assertRaises(ValidationError):
            self.repairs.adopt(review["repair_id"], True)

    def test_local_change_and_read_failure_leave_original(self):
        review = self.repairs.prepare(self.pid, 0, 100)
        self.service.set_submission_barcode(self.pid, "4006381333931")
        with self.assertRaisesRegex(ValidationError, "Product changed"):
            self.repairs.adopt(review["repair_id"], True)
        fresh = self.repairs.prepare(self.pid, 0, 100)
        self.client.fail_read = True
        with self.assertRaises(ConnectionError):
            self.repairs.adopt(fresh["repair_id"], True)
        self.assertEqual(
            self.store.get("product_repairs", fresh["repair_id"])["state"], "reviewed_snapshot"
        )
