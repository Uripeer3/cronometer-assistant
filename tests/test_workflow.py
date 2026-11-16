import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from cronometer_assistant.backend import DemoDiary
from cronometer_assistant.catalog import CronometerReadOnlyCatalog, rank_foods
from cronometer_assistant.cli import run_demo
from cronometer_assistant.models import Food, MealItem, Nutrient, ValidationError
from cronometer_assistant.nutrition import normalize_label, portion_ingredients, summarize
from cronometer_assistant.service import MealService
from cronometer_assistant.store import Store


def rice(grams=150, preparation="cooked"):
    return {
        "description": "אורז",
        "food_id": "demo:rice-cooked",
        "grams": grams,
        "preparation": preparation,
    }


class ServiceFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "data" / "test.sqlite")
        self.service = MealService(self.store)

    def ready(self, service=None, items=None):
        service = service or self.service
        meal = service.begin_meal("lunch", day="2026-11-16", intent="eaten")["meal"]
        service.update_meal(meal["meal_id"], expected_revision=0, items=items or [rice()])
        return meal["meal_id"]


class ServiceTests(ServiceFixture):
    def test_demo_runs_without_credentials(self):
        result = run_demo(Path(self.temp.name) / "demo.sqlite")
        self.assertEqual(result["logging_state"], "committed")
        self.assertTrue(result["repeat_was_deduplicated"])
        self.assertEqual(result["simulated_entries"], 2)
        self.assertFalse(result["real_diary_changed"])

    def test_preparation_is_a_question_and_blocks_commit(self):
        meal_id = self.ready(items=[rice(preparation="unspecified")])
        review = self.service.review_meal(meal_id)["review"]
        self.assertEqual(review["questions"][0]["reason"], "preparation")
        with self.assertRaises(ValidationError):
            self.service.commit_meal(meal_id, 1)

    def test_mismatching_dry_vs_cooked_is_blocked(self):
        meal_id = self.ready(items=[rice(preparation="raw")])
        self.assertFalse(self.service.review_meal(meal_id)["review"]["ready_to_log"])

    def test_preparing_does_not_log_consumption(self):
        meal = self.service.begin_meal("cooking")["meal"]
        self.service.update_meal(meal["meal_id"], expected_revision=0, items=[rice()])
        with self.assertRaises(ValidationError):
            self.service.commit_meal(meal["meal_id"], 1)
        self.assertEqual(self.service.status(meal["meal_id"])["diary"], [])

    def test_unanswered_questions_survive_partial_update(self):
        meal_id = self.ready()
        self.service.update_meal(
            meal_id, expected_revision=1, items=[rice()], pending_questions=["How much oil?"]
        )
        self.service.update_meal(meal_id, expected_revision=2, items=[rice(170)])
        self.assertEqual(
            self.service.review_meal(meal_id)["meal"]["pending_questions"], ["How much oil?"]
        )

    def test_revision_prevents_stale_commit(self):
        meal_id = self.ready()
        self.service.update_meal(meal_id, expected_revision=1, items=[rice(200)])
        with self.assertRaises(ValidationError):
            self.service.commit_meal(meal_id, 1)

    def test_repeat_commit_persists_across_restart(self):
        meal_id = self.ready()
        first = self.service.commit_meal(meal_id, 1)
        restarted = MealService(Store(self.store.path))
        again = restarted.commit_meal(meal_id, 1)
        self.assertEqual(first["operation"]["operation_id"], again["operation"]["operation_id"])
        self.assertTrue(again["already_logged"])
        self.assertEqual(len(again["operation"]["steps"]), 1)
        self.assertEqual(len(restarted.status(meal_id)["diary"]), 1)

    def test_second_identical_meal_is_valid(self):
        for _ in range(2):
            self.service.commit_meal(self.ready(), 1)
        self.assertEqual(len(self.service.diary.entries("2026-11-16")), 2)

    def test_concurrent_commits_share_one_operation(self):
        meal_id = self.ready()
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _: self.service.commit_meal(meal_id, 1), range(4)))
        self.assertEqual(len({r["operation"]["operation_id"] for r in results}), 1)
        self.assertEqual(len(self.service.diary.entries("2026-11-16")), 1)

    def test_logged_draft_is_immutable(self):
        meal_id = self.ready()
        self.service.commit_meal(meal_id, 1)
        with self.assertRaises(ValidationError):
            self.service.update_meal(meal_id, expected_revision=1, items=[rice(200)])

    def test_unknown_food_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.ready(items=[{**rice(), "food_id": "made-up"}])

    def test_alias_survives_restart_and_searches_in_hebrew(self):
        self.service.remember_product("היוגורט שלי", "demo:yogurt")
        restarted = MealService(Store(self.store.path))
        result = restarted.resolve_food("היוגורט שלי", packaged=True)
        self.assertEqual(result["candidates"][0]["food_id"], "demo:yogurt")
        self.assertTrue(restarted.resolve_food("אורז")["candidates"])

    def test_label_saved_once_can_be_reused(self):
        proposal = self.service.propose_label(
            name="מוצר לבדיקה",
            basis_amount=100,
            basis_unit="g",
            nutrients={"חלבון": {"amount": 12, "unit": "g"}},
        )["proposal"]
        saved = self.service.save_label_product(proposal, "המוצר שלי", reviewed=True)
        food_id = saved["food"]["food_id"]
        self.assertEqual(
            self.service.resolve_food("המוצר שלי")["candidates"][0]["food_id"], food_id
        )
        self.assertNotIn("sodium", self.service.catalog.get(food_id).nutrients_per_100g)
        with self.assertRaises(ValidationError):
            self.service.save_label_product(proposal, "other", reviewed=False)

    def test_unknown_identity_makes_totals_incomplete(self):
        meal_id = self.ready(items=[rice(), {"description": "unknown sauce", "grams": 20}])
        energy = self.service.review_meal(meal_id)["review"]["nutrients"]["energy"]
        self.assertEqual(energy["known_amount"], 195)
        self.assertFalse(energy["complete"])
        self.assertIn("unknown sauce", energy["missing_from"])

    def test_default_date_uses_account_timezone(self):
        meal = self.service.begin_meal("today")["meal"]
        self.assertEqual(meal["timezone"], "Asia/Jerusalem")
        date.fromisoformat(meal["day"])
        with self.assertRaises(ValidationError):
            self.service.begin_meal("bad date", day="2026-1-1")


class ReliabilityTests(ServiceFixture):
    def service_with(self, backend_type):
        return MealService(self.store, diary=backend_type(self.store))

    def test_timeout_after_remote_write_never_retries_add(self):
        class TimeoutDiary(DemoDiary):
            def add(self, **kwargs):
                super().add(**kwargs)
                raise TimeoutError("request might have succeeded")

        service = self.service_with(TimeoutDiary)
        meal_id = self.ready(service)
        first = service.commit_meal(meal_id, 1)
        self.assertEqual(first["operation"]["state"], "needs_reconciliation")
        again = MealService(Store(self.store.path)).commit_meal(meal_id, 1)
        self.assertTrue(again["retry_blocked"])
        entries = service.diary.entries("2026-11-16")
        self.assertEqual(len(entries), 1)
        service.reconcile_entry(meal_id, 0, entries[0].entry_id)
        self.assertTrue(service.commit_meal(meal_id, 1)["already_logged"])

    def test_process_crash_leaves_a_durable_attempt_marker(self):
        class SimulatedCrash(BaseException):
            pass

        class CrashDiary(DemoDiary):
            def add(self, **kwargs):
                super().add(**kwargs)
                raise SimulatedCrash

        service = self.service_with(CrashDiary)
        meal_id = self.ready(service)
        with self.assertRaises(SimulatedCrash):
            service.commit_meal(meal_id, 1)
        restarted = MealService(Store(self.store.path))
        result = restarted.commit_meal(meal_id, 1)
        self.assertTrue(result["retry_blocked"])
        self.assertEqual(len(restarted.diary.entries("2026-11-16")), 1)

    def test_reconciliation_cannot_claim_an_older_matching_meal(self):
        previous_id = self.ready()
        self.service.commit_meal(previous_id, 1)
        older = self.service.diary.entries("2026-11-16")[0]

        class TimeoutDiary(DemoDiary):
            def add(self, **kwargs):
                super().add(**kwargs)
                raise TimeoutError

        service = self.service_with(TimeoutDiary)
        meal_id = self.ready(service)
        service.commit_meal(meal_id, 1)
        with self.assertRaises(ValidationError):
            service.reconcile_entry(meal_id, 0, older.entry_id)

    def test_verification_failure_does_not_repeat_add(self):
        class VerifyFailure(DemoDiary):
            count = 0

            def entries(self, day):
                self.count += 1
                if self.count == 2:
                    raise ConnectionError
                return super().entries(day)

        service = self.service_with(VerifyFailure)
        meal_id = self.ready(service)
        self.assertEqual(
            service.commit_meal(meal_id, 1)["operation"]["state"], "needs_verification"
        )
        restarted = MealService(Store(self.store.path))
        self.assertEqual(restarted.commit_meal(meal_id, 1)["operation"]["state"], "committed")
        self.assertEqual(len(restarted.diary.entries("2026-11-16")), 1)

    def test_read_failure_before_add_is_safe_to_retry(self):
        class ReadFailure(DemoDiary):
            def entries(self, day):
                raise ConnectionError

        service = self.service_with(ReadFailure)
        meal_id = self.ready(service)
        self.assertTrue(service.commit_meal(meal_id, 1)["retry_safe"])
        restarted = MealService(Store(self.store.path))
        self.assertEqual(restarted.commit_meal(meal_id, 1)["operation"]["state"], "committed")

    def test_partial_write_does_not_duplicate_verified_items(self):
        class SecondTimeout(DemoDiary):
            count = 0

            def add(self, **kwargs):
                self.count += 1
                entry = super().add(**kwargs)
                if self.count == 2:
                    raise TimeoutError
                return entry

        service = self.service_with(SecondTimeout)
        meal_id = self.ready(service, [rice(), {**rice(100), "description": "second"}])
        result = service.commit_meal(meal_id, 1)
        self.assertEqual(
            [s["state"] for s in result["operation"]["steps"]], ["verified", "uncertain"]
        )
        self.assertTrue(service.commit_meal(meal_id, 1)["retry_blocked"])
        self.assertEqual(len(service.diary.entries("2026-11-16")), 2)

    def test_real_backend_cannot_be_enabled(self):
        backend = DemoDiary(self.store)
        backend.mode = "live"
        with self.assertRaises(ValidationError):
            MealService(self.store, diary=backend)


class NutritionTests(unittest.TestCase):
    def test_missing_zero_and_known_subtotal_are_distinct(self):
        f = Food("1", "food", "label", {"sodium": Nutrient(0, "mg")})
        totals = summarize([(MealItem("food", "1", 100), f)])
        self.assertEqual(totals["sodium"]["known_amount"], 0)
        self.assertTrue(totals["sodium"]["complete"])
        self.assertIsNone(totals["protein"]["known_amount"])
        self.assertFalse(totals["protein"]["complete"])

    def test_label_basis_energy_and_mass_units(self):
        label = normalize_label(
            "test",
            40,
            "g",
            {
                "energy": {"amount": 418.4, "unit": "kJ"},
                "protein": {"amount": 4000, "unit": "mg"},
                "sodium": {"amount": None, "unit": "mg"},
            },
        )
        values = label["nutrients_per_100g"]
        self.assertAlmostEqual(values["energy"]["amount"], 250)
        self.assertEqual(values["protein"]["amount"], 10)
        self.assertIsNone(values["sodium"]["amount"])
        self.assertNotIn("fiber", values)

    def test_ml_requires_density(self):
        nutrients = {"protein": {"amount": 5, "unit": "g"}}
        with self.assertRaises(ValidationError):
            normalize_label("milk", 100, "ml", nutrients)
        label = normalize_label("milk", 100, "ml", nutrients, density_g_per_ml=1.03)
        self.assertAlmostEqual(label["nutrients_per_100g"]["protein"]["amount"], 5 / 1.03)

    def test_salt_sodium_conflict_is_not_silently_resolved(self):
        with self.assertRaises(ValidationError):
            normalize_label(
                "test",
                100,
                "g",
                {
                    "salt": {"amount": 1, "unit": "g"},
                    "sodium": {"amount": 400, "unit": "mg"},
                },
            )
        value = normalize_label("test", 100, "g", {"salt": {"amount": 1, "unit": "g"}})
        self.assertEqual(value["nutrients_per_100g"]["sodium"]["amount"], 400)

    def test_nonfinite_negative_and_boolean_quantities_are_rejected(self):
        for value in (float("nan"), float("inf"), -1, 0, True):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                MealItem("test", "1", value)

    def test_estimates_require_assumptions(self):
        with self.assertRaises(ValidationError):
            MealItem("test", "1", 100, quantity_basis="estimated")

    def test_finished_batch_weight_controls_portion(self):
        result = portion_ingredients([rice(300)], batch_grams=900, portion_grams=150)
        self.assertEqual(result[0]["grams"], 50)
        with self.assertRaises(ValidationError):
            portion_ingredients([rice()], batch_grams=100, portion_grams=200)

    def test_identity_wins_over_preferred_database(self):
        exact = Food("1", "My yogurt 3%", "BRANDED", {})
        generic = Food("2", "Yogurt", "NCCDB", {})
        candidates = rank_foods([generic, exact], "My yogurt 3%", packaged=False)
        self.assertEqual(candidates[0]["food_id"], "1")

    def test_preparation_mismatch_is_filtered(self):
        raw = Food("1", "rice", "NCCDB", {}, preparation="raw")
        cooked = Food("2", "rice", "USDA", {}, preparation="cooked")
        self.assertEqual(
            [x["food_id"] for x in rank_foods([raw, cooked], "rice", preparation="cooked")], ["2"]
        )


class AdapterTests(unittest.TestCase):
    def test_raw_missing_values_and_unknown_source_code_are_preserved(self):
        class Client:
            def get_nutrient_definitions(self):
                return {203: {"unit": "g"}, 307: {"unit": "mg"}}

            def get_food(self, fid):
                return {
                    "id": fid,
                    "name": "food",
                    "source": 99,
                    "nutrients": [{"id": 203, "amount": None}, {"id": 307, "amount": 0}],
                }

        food = CronometerReadOnlyCatalog(Client()).get("crono:123")
        self.assertEqual(food.source, "UNVERIFIED:99")
        self.assertIsNone(food.nutrients_per_100g["protein"].amount)
        self.assertEqual(food.nutrients_per_100g["sodium"].amount, 0)
