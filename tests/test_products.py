"""Synthetic-only product workflow, label-convention, and write-boundary regressions."""

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

from cronometer_assistant.catalog import CronometerReadOnlyCatalog
from cronometer_assistant.live import LiveError, check_request
from cronometer_assistant.models import ValidationError
from cronometer_assistant.nutrition import normalize_label
from cronometer_assistant.products import ProductService
from cronometer_assistant.service import MealService
from cronometer_assistant.store import Store


def label_values():
    return {
        "energy": {"amount": 320, "unit": "kcal"},
        "protein": {"amount": 24, "unit": "g"},
        "carbs": {"amount": 30, "unit": "g"},
        "fiber": {"amount": 8, "unit": "g"},
        "sugar_alcohol": {"amount": 6, "unit": "g"},
        "sodium": {"amount": 0, "unit": "mg"},
        "cholesterol": {"amount": None, "unit": "mg"},
    }


class FakeClient:
    user_id = 7

    def __init__(self):
        self.foods = {}
        self.adds = 0
        self.timeout_after_create = False
        self.fail_read = False

    def get_nutrient_definitions(self):
        return {
            208: {"name": "Energy", "unit": "kcal"},
            203: {"name": "Protein", "unit": "g"},
            205: {"name": "Carbs", "unit": "g"},
            291: {"name": "Fiber", "unit": "g"},
            10007: {"name": "Sugar Alcohol", "unit": "g"},
            307: {"name": "Sodium", "unit": "mg"},
            324: {"name": "Vitamin D", "unit": "IU"},
        }

    def _request(self, endpoint, payload):
        if endpoint != "/api/v2/add_food":
            raise AssertionError("No other writes allowed")
        # Exercise the actual upstream request helper's argument mutation.
        payload["auth"] = {"token": "synthetic-secret-never-persist"}
        self.adds += 1
        raw = deepcopy(payload["data"])
        raw["id"] = 123
        raw["owner"] = self.user_id
        raw["measures"][0]["id"] = 456
        self.foods[123] = raw
        if self.timeout_after_create:
            raise TimeoutError("synthetic-secret-never-persist")
        return {"id": 123}

    def get_food(self, food_id):
        if self.fail_read:
            raise ConnectionError("synthetic-secret-never-persist")
        return deepcopy(self.foods[food_id])


class LabelConventionTests(unittest.TestCase):
    def test_fiber_added_exactly_once_and_raw_label_preserved(self):
        p = normalize_label(
            "Synthetic cereal", 100, "g", label_values(), carbohydrate_basis="excludes_fiber"
        )
        self.assertEqual(p["label_nutrients_per_100g"]["carbs"]["amount"], 30)
        self.assertEqual(p["nutrients_per_100g"]["carbs"]["amount"], 38)
        self.assertEqual(p["nutrients_per_100g"]["sugar_alcohol"]["amount"], 6)
        self.assertEqual(p["nutrients_per_100g"]["energy"]["amount"], 320)
        already_total = normalize_label(
            "Synthetic cereal",
            100,
            "g",
            {"carbs": {"amount": 38, "unit": "g"}, "fiber": {"amount": 8, "unit": "g"}},
            carbohydrate_basis="includes_fiber",
        )
        self.assertEqual(already_total["nutrients_per_100g"]["carbs"]["amount"], 38)

    def test_ambiguous_basis_missing_fiber_and_net_input_are_rejected(self):
        with self.assertRaises(ValidationError):
            normalize_label("test", 100, "g", label_values())
        with self.assertRaises(ValidationError):
            normalize_label(
                "test",
                100,
                "g",
                {"carbs": {"amount": 5, "unit": "g"}},
                carbohydrate_basis="excludes_fiber",
            )
        with self.assertRaises(ValidationError):
            normalize_label("test", 100, "g", {"net_carbs": {"amount": 5, "unit": "g"}})
        with self.assertRaises(ValidationError):
            normalize_label("test", 100, "g", [])
        with self.assertRaises(ValidationError):
            normalize_label(
                "test",
                100,
                "g",
                {"carbs": {"amount": 5, "unit": "g"}, "sugar_alcohol": {"amount": 10, "unit": "g"}},
                carbohydrate_basis="includes_fiber",
            )

    def test_ius_and_micrograms_are_not_confused(self):
        p = normalize_label(
            "Synthetic label",
            100,
            "g",
            {
                "cronometer_324": {"amount": 80, "unit": "IU"},
                "cronometer_418": {"amount": 4, "unit": "µg"},
            },
        )
        self.assertEqual(p["nutrients_per_100g"]["cronometer_324"]["unit"], "IU")
        self.assertEqual(p["nutrients_per_100g"]["cronometer_418"]["unit"], "ug")

    def test_catalog_handles_iu_unclassified_and_computed_rows(self):
        client = FakeClient()
        client.foods[1] = {
            "id": 1,
            "name": "Synthetic food",
            "source": "TEST:42",
            "nutrients": [
                {"id": 324, "amount": 80},
                {"id": -205, "amount": 99},
                {"id": 10007, "amount": 6},
                {"id": 999, "amount": 3},
                {"id": 307, "amount": 0},
            ],
        }
        food = CronometerReadOnlyCatalog(client, source_map={"TEST": "USDA"}).get("crono:1")
        self.assertEqual(food.source, "USDA")
        self.assertEqual(food.nutrients_per_100g["cronometer_324"].unit, "IU")
        self.assertNotIn("cronometer_-205", food.nutrients_per_100g)
        self.assertNotIn("cronometer_999", food.nutrients_per_100g)
        self.assertEqual(food.nutrients_per_100g["sodium"].amount, 0)
        self.assertEqual(food.nutrients_per_100g["sugar_alcohol"].amount, 6)

    def test_local_label_save_reuses_id_and_does_not_double_add_fiber(self):
        with tempfile.TemporaryDirectory() as directory:
            service = MealService(Store(Path(directory) / "demo.sqlite"))
            p = service.propose_label(
                name="Synthetic cereal",
                basis_amount=100,
                basis_unit="g",
                nutrients=label_values(),
                carbohydrate_basis="excludes_fiber",
            )["proposal"]
            first = service.save_label_product(p, "usual", True)["food"]
            second = service.save_label_product(p, "usual", True)["food"]
            self.assertEqual(first["food_id"], second["food_id"])
            self.assertEqual(first["nutrients_per_100g"]["carbs"]["amount"], 38)


class ProductTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "products.sqlite")
        self.client = FakeClient()
        self.service = ProductService(self.store, self.client)

    def proposal(self, nutrients=None):
        return self.service.prepare(
            "Synthetic Brand, Cocoa",
            100,
            "g",
            nutrients or label_values(),
            carbohydrate_basis="excludes_fiber",
            serving_grams=25,
        )["product"]["proposal_id"]

    def test_creation_readback_and_repeat_are_deduplicated_without_credentials(self):
        pid = self.proposal()
        self.assertEqual(pid, self.proposal())
        with self.assertRaises(ValidationError):
            self.service.create(pid, False)
        result = self.service.create(pid, True)
        self.assertTrue(result["created"])
        self.assertFalse(result["real_diary_changed"])
        self.assertFalse(result["barcode_linked"])
        stored = self.store.get("product_proposals", pid)
        rows = {n["id"]: n["amount"] for n in stored["payload"]["data"]["nutrients"]}
        self.assertEqual(rows[205], 38)
        self.assertEqual(rows[307], 0)
        self.assertNotIn(601, rows)
        self.assertNotIn("synthetic-secret", json.dumps(stored))
        self.assertNotIn("payload", result["product"])
        restarted = ProductService(Store(self.store.path), self.client)
        self.assertTrue(restarted.create(pid, True)["already_created"])
        self.assertEqual(self.client.adds, 1)

    def test_timeout_never_blindly_repeats_and_marker_allows_reconciliation(self):
        pid = self.proposal()
        self.client.timeout_after_create = True
        result = self.service.create(pid, True)
        self.assertTrue(result["retry_blocked"])
        self.assertNotIn("synthetic-secret", json.dumps(result))
        restarted = ProductService(Store(self.store.path), self.client)
        self.assertTrue(restarted.create(pid, True)["retry_blocked"])
        self.assertEqual(self.client.adds, 1)
        self.assertTrue(restarted.reconcile(pid, 123)["created"])
        self.assertTrue(restarted.create(pid, True)["already_created"])

    def test_crash_leaves_durable_marker(self):
        class Crash(BaseException):
            pass

        original = self.client._request

        def crash(endpoint, payload):
            original(endpoint, payload)
            raise Crash

        self.client._request = crash
        pid = self.proposal()
        with self.assertRaises(Crash):
            self.service.create(pid, True)
        self.assertEqual(self.store.get("product_proposals", pid)["state"], "sending")
        restarted = ProductService(Store(self.store.path), self.client)
        self.assertTrue(restarted.create(pid, True)["retry_blocked"])
        self.assertEqual(self.client.adds, 1)

    def test_known_id_read_failure_can_retry_only_verification(self):
        pid = self.proposal()
        self.client.fail_read = True
        result = self.service.create(pid, True)
        self.assertEqual(result["product"]["state"], "needs_verification")
        self.client.fail_read = False
        self.assertTrue(self.service.create(pid, True)["created"])
        self.assertEqual(self.client.adds, 1)

    def test_unexpected_remote_change_and_wrong_marker_cannot_pass(self):
        pid = self.proposal()
        self.client.timeout_after_create = True
        self.service.create(pid, True)
        self.client.foods[123]["comments"] = "another operation"
        with self.assertRaises(ValidationError):
            self.service.reconcile(pid, 123)
        self.client.foods[123]["comments"] = "cronometer-assistant product:" + pid
        self.client.foods[123]["nutrients"].append({"id": 301, "amount": 0})
        with self.assertRaises(ValidationError):
            self.service.reconcile(pid, 123)

    def test_unmapped_or_wrong_unit_nutrient_blocks_before_write(self):
        for key, unit in [("unrecognized", "g"), ("cronometer_324", "ug")]:
            pid = self.proposal({key: {"amount": 4, "unit": unit}})
            with self.assertRaises(ValidationError):
                self.service.create(pid, True)
            self.assertEqual(self.service.review(pid)["product"]["state"], "prepared")
        self.assertEqual(self.client.adds, 0)

    def test_other_account_cannot_reuse_database(self):
        other = FakeClient()
        other.user_id = 8
        with self.assertRaises(ValidationError):
            ProductService(self.store, other)

    def test_concurrent_calls_share_single_creation(self):
        pid = self.proposal()
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _: self.service.create(pid, True), range(4)))
        self.assertTrue(all(r["created"] for r in results))
        self.assertEqual(self.client.adds, 1)

    def test_checklist_does_not_submit_and_barcode_is_only_metadata(self):
        pid = self.proposal()
        result = self.service.public_submission_checklist(pid)
        self.assertFalse(result["submitted"])
        self.assertFalse(result["public"])
        self.assertEqual(self.client.adds, 0)
        with self.assertRaises(ValidationError):
            self.service.prepare("x", 100, "g", label_values(), barcode="not-a-code")

    def test_http_guard_is_opt_in_and_cannot_edit_or_log(self):
        check_request("POST", "/api/v2/add_food", {"data": {"id": 0}}, allow_product_creation=True)
        for data in ({"id": 123}, {"id": True}, {"id": 0, "meal": {"ingredients": [1]}}):
            with self.assertRaises(LiveError):
                check_request(
                    "POST", "/api/v2/add_food", {"data": data}, allow_product_creation=True
                )
        with self.assertRaises(LiveError):
            check_request("POST", "/api/v2/add_food", {"data": {"id": 0}})
        with self.assertRaises(LiveError):
            check_request("POST", "/api/v2/add_serving", {}, allow_product_creation=True)
