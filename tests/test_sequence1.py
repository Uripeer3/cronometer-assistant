"""Audit regressions: exact selection, reviewed snapshots and bounded recovery."""

import unittest
from copy import deepcopy
from unittest.mock import patch

import test_live_drafts as draft_fixtures
import test_live_meals as meal_fixtures
import test_products as product_fixtures
from test_live_meals import DAY, FOOD

from cronometer_assistant.daily_nutrition import daily_nutrition
from cronometer_assistant.diary_changes import DiaryChanges
from cronometer_assistant.live import LiveError, MutationNotApplied
from cronometer_assistant.live_drafts import LiveDraftService, LiveMealService
from cronometer_assistant.models import ValidationError
from cronometer_assistant.store import Store


class SelectionRegressionTests(unittest.TestCase):
    setUp = draft_fixtures.LiveDraftTests.setUp

    def test_two_translations_keep_distinct_drafts_and_aliases_after_restart(self):
        self.client.search_food = lambda q: [
            {"id": 5, "name": "Rice A", "translationId": 77},
            {"id": 5, "name": "Rice B", "translationId": 88},
        ]
        drafts = []
        for translation, name in [(77, "Rice A"), (88, "Rice B")]:
            selected = self.service.verify_food(
                "crono:5", "cooked", True, True, translation, "rice"
            )["food"]
            meal = self.service.begin_meal(name)["meal"]
            result = self.service.update_meal(
                meal["meal_id"],
                expected_revision=0,
                items=[
                    {
                        "description": name,
                        "food_id": "crono:5",
                        "selection_id": selected["selection_id"],
                        "grams": 50,
                        "preparation": "cooked",
                    }
                ],
            )
            self.service.remember_product(name, "crono:5", selected["selection_id"])
            drafts.append((meal["meal_id"], name, translation, result["meal"]["revision"]))
        restarted = LiveDraftService(self.store, self.client)
        for key, name, translation, revision in drafts:
            result = restarted.review_meal(key)
            self.assertEqual(result["meal"]["revision"], revision)
            self.assertEqual(result["review"]["entries"][0]["food_name"], name)
            candidate = restarted.resolve_food(name)["candidates"][0]
            self.assertEqual(candidate["translation_id"], translation)

    def test_omitted_selection_cannot_rebind_an_existing_draft(self):
        self.client.search_food = lambda q: [
            {"id": 5, "name": f"Variant {q}", "translationId": int(q)}
        ]
        selected = self.service.verify_food("crono:5", "cooked", True, True, 77, "77")["food"]
        meal = self.service.begin_meal("rice")["meal"]
        item = {"description": "rice", "food_id": "crono:5", "grams": 50, "preparation": "cooked"}
        self.service.update_meal(meal["meal_id"], expected_revision=0, items=[item])
        self.service.verify_food("crono:5", "cooked", True, True, 88, "88")
        with self.assertRaisesRegex(ValidationError, "selection_id"):
            self.service.update_meal(
                meal["meal_id"], expected_revision=1, items=[{**item, "grams": 60}]
            )
        with self.assertRaisesRegex(ValidationError, "selection_id"):
            self.service.remember_product("usual", "crono:5")
        result = self.service.review_meal(meal["meal_id"])
        self.assertEqual(result["review"]["entries"][0]["food_name"], "Variant 77")
        self.assertEqual(result["meal"]["items"][0]["selection_id"], selected["selection_id"])


@unittest.skipUnless(
    meal_fixtures.importlib.util.find_spec("cronometer_mcp"), "live SDK not installed"
)
class MealRecoveryRegressionTests(unittest.TestCase):
    setUp = meal_fixtures.LiveMealTests.setUp
    draft = meal_fixtures.LiveMealTests.draft

    def test_pending_snapshots_block_nutrients_measure_retirement_and_timezone_changes(self):
        for mutation in ("nutrients", "measure", "retired", "timezone", "legacy"):
            with self.subTest(mutation=mutation):
                self.food = deepcopy(FOOD)
                self.client._timezone = "Asia/Damascus"
                self.meals = LiveMealService(Store(self.db), self.client)
                key, token = self.draft()
                self.bad_diary = True
                self.meals.commit_meal(key, 1, review_token=token, confirmed=True)
                self.bad_diary = False
                if mutation == "nutrients":
                    self.food["nutrients"][0]["amount"] = 999
                elif mutation == "measure":
                    self.food["measures"][1]["name"] = "new grams definition"
                elif mutation == "retired":
                    self.food["retired"] = True
                elif mutation == "timezone":
                    self.client._timezone = "UTC"
                else:
                    operation = self.meals.store.get("operations", key)
                    operation["steps"][0].pop("food_profile")
                    self.meals.store.put("operations", key, operation)
                result = self.meals.commit_meal(key, 1, review_token=token, confirmed=True)
                self.assertEqual(result["operation"]["state"], "needs_review")
                self.assertEqual(self.writes, [])
                self.meals.cancel_pending(key, confirmed=True)
                repeated = self.meals.commit_meal(key, 1, review_token=token, confirmed=True)
                self.assertEqual(repeated["operation"]["state"], "completed_with_cancellations")
                self.assertEqual(self.writes, [])

    def test_partial_cleanup_cancels_unsent_preserves_uncertain_and_never_recreates(self):
        key, token = self.draft(items=3)
        original_add = self.meals.diary.add
        calls = 0

        def add(**kwargs):
            nonlocal calls
            calls += 1
            result = original_add(**kwargs)
            if calls == 2:
                raise TimeoutError("response lost")
            return result

        with patch.object(self.meals.diary, "add", side_effect=add):
            result = self.meals.commit_meal(key, 1, review_token=token, confirmed=True)
        self.assertEqual(
            [s["state"] for s in result["operation"]["steps"]], ["verified", "uncertain", "pending"]
        )
        # A manually entered identical food must survive scoped cleanup.
        self.servings.append({**self.servings[0], "servingId": 999})
        changes = DiaryChanges(self.meals)
        changes.undo_item(key, 0, 150, confirmed=True)
        result = self.meals.cancel_pending(key, confirmed=True)
        self.assertEqual(
            [s["state"] for s in result["operation"]["steps"]],
            ["verified", "uncertain", "cancelled"],
        )
        restarted = LiveMealService(Store(self.db), self.client)
        restarted.reconcile_entry(key, 1, "crono-serving:101")
        for _ in range(2):
            restarted.commit_meal(key, 1, review_token=token, confirmed=True)
            DiaryChanges(restarted).undo_item(key, 0, 150, confirmed=True)
        self.assertEqual(len(self.writes), 3)  # two adds and one removal
        self.assertEqual({r["servingId"] for r in self.servings}, {101, 999})

    def test_current_summary_survives_food_change_and_retains_edit_history(self):
        key, token = self.draft()
        self.meals.commit_meal(key, 1, review_token=token, confirmed=True)
        changes = DiaryChanges(self.meals)
        changes.correct_amount(key, 0, 150, 120, confirmed=True)
        self.food["nutrients"] = [{"id": 208, "amount": 999}]
        self.assertEqual(self.meals.review_meal(key)["current_recorded"]["items"][0]["grams"], 120)
        changes.undo_item(key, 0, 120, confirmed=True)
        current = self.meals.status(key)["current_recorded"]["items"][0]
        self.assertEqual(current["state"], "undone")
        self.assertIsNone(current["grams"])
        operation = self.meals.store.get("operations", key)
        self.assertEqual(operation["steps"][0]["change_history"][0]["to_grams"], 120)
        self.assertEqual(self.meals._meal(key)["items"][0]["grams"], 150)
        self.meals.commit_meal(key, 1, review_token=token, confirmed=True)
        self.assertEqual(len(self.writes), 3)

    def test_adapter_proves_pre_send_failure_without_clearing_timeouts(self):
        key, token = self.draft()
        # Real pinned client cannot start its write when a pre-write food lookup fails.
        with patch.object(self.client, "_recipe_entry_units", side_effect=ValueError("local fail")):
            result = self.meals.commit_meal(key, 1, review_token=token, confirmed=True)
        self.assertEqual(result["operation"]["steps"][0]["state"], "not_applied")
        self.assertEqual(self.writes, [])
        self.meals.cancel_pending(key, confirmed=True)
        key2, token2 = self.draft()
        self.timeout_after_add = True
        result = self.meals.commit_meal(key2, 1, review_token=token2, confirmed=True)
        self.assertEqual(result["operation"]["steps"][0]["state"], "uncertain")
        self.meals.cancel_pending(key2, confirmed=True)
        self.assertEqual(self.meals.store.get("operations", key2)["steps"][0]["state"], "uncertain")

    def test_rejected_change_can_be_reconfirmed_but_uncertainty_is_explicit(self):
        key, token = self.draft()
        self.meals.commit_meal(key, 1, review_token=token, confirmed=True)
        changes = DiaryChanges(self.meals)
        with patch.object(
            self.meals.diary, "edit_amount", side_effect=MutationNotApplied("not sent")
        ):
            result = changes.correct_amount(key, 0, 150, 120, confirmed=True)
        self.assertEqual(result["state"], "not_applied")
        self.assertEqual(self.meals.status(key)["current_recorded"]["items"][0]["grams"], 150)
        changes.correct_amount(key, 0, 150, 120, confirmed=True)
        with patch.object(self.meals.diary, "edit_amount", side_effect=TimeoutError):
            changes.correct_amount(key, 0, 120, 100, confirmed=True)
        current = self.meals.status(key)["current_recorded"]["items"][0]
        self.assertIsNone(current["grams"])
        self.assertEqual(current["last_verified_grams"], 120)
        self.assertEqual(current["requested_grams"], 100)

    def test_real_client_raw_totals_distinguish_malformed_empty_unknown_and_zero(self):
        import httpx

        for scores in (
            {},
            {"scores": []},
            {"scores": [{"title": "All Targets"}]},
            {"scores": [{"title": "All Targets", "components": None}]},
        ):
            with (
                self.subTest(scores=scores),
                patch.object(self.client, "get_nutrition_scores", return_value=scores),
            ):
                with self.assertRaises(LiveError):
                    daily_nutrition(self.client, DAY)
        for components in (
            [],
            [{"nutrientId": 208}],
            [{"nutrientId": 208, "amount": 0}],
            [{"nutrientId": 208, "amount": 159.5}],
        ):

            def respond(request, components=components):
                if request.url.path.endswith("get_diary"):
                    return httpx.Response(200, json={"diary": [], "info": {"day": DAY}})
                if request.url.path.endswith("get_nutrition_scores"):
                    return httpx.Response(
                        200, json={"scores": [{"title": "All Targets", "components": components}]}
                    )
                if request.url.path.endswith("get_nutrients"):
                    return httpx.Response(
                        200, json={"nutrients": [{"id": 208, "name": "Energy", "unit": "kcal"}]}
                    )
                raise AssertionError(request.url.path)

            with patch.object(self.client._http, "_transport", httpx.MockTransport(respond)):
                self.client._nutrient_defs = None
                result = daily_nutrition(self.client, DAY)
            self.assertEqual(
                result["summary"]["energy"]["amount"],
                components[0].get("amount") if components else None,
            )

    def test_rejected_correction_followed_by_external_edit_is_a_conflict(self):
        key, token = self.draft()
        self.meals.commit_meal(key, 1, review_token=token, confirmed=True)
        changes = DiaryChanges(self.meals)
        with patch.object(
            self.meals.diary, "edit_amount", side_effect=MutationNotApplied("not sent")
        ):
            changes.correct_amount(key, 0, 150, 120, confirmed=True)
        self.servings[0]["grams"] = 120
        with self.assertRaisesRegex(ValidationError, "changed in Cronometer"):
            changes.correct_amount(key, 0, 150, 120, confirmed=True)
        self.assertEqual(
            self.meals.store.get("operations", key)["steps"][0]["diary_change"]["state"],
            "not_applied",
        )

    def test_mutation_boundary_rejections_vs_malformed_success(self):
        import httpx

        original = self.client._http._transport
        for response in (
            httpx.Response(401),
            httpx.Response(403),
            httpx.Response(200, json={"result": "FAIL"}),
            httpx.Response(200, json={"result": "FAILURE"}),
            httpx.Response(500),
            httpx.Response(200, content=b"not-json"),
        ):
            key, token = self.draft()

            def respond(request, response=response):
                if request.url.path.endswith("add_serving"):
                    return response
                return original.handle_request(request)

            with patch.object(self.client._http, "_transport", httpx.MockTransport(respond)):
                result = self.meals.commit_meal(key, 1, review_token=token, confirmed=True)
            expected = (
                "uncertain"
                if response.status_code == 500 or response.content == b"not-json"
                else "not_applied"
            )
            self.assertEqual(result["operation"]["steps"][0]["state"], expected)
        self.assertEqual(self.writes, [])

    def test_malformed_diary_cannot_become_a_zero_nutrition_day(self):
        import httpx

        requests = []

        def respond(request):
            requests.append(request.url.path)
            if request.url.path.endswith("get_diary"):
                return httpx.Response(200, json={})
            return httpx.Response(
                200,
                json={
                    "scores": [
                        {"title": "All Targets", "components": [{"nutrientId": 208, "amount": 0}]}
                    ]
                },
            )

        with patch.object(self.client._http, "_transport", httpx.MockTransport(respond)):
            with self.assertRaises(LiveError):
                daily_nutrition(self.client, DAY)
        self.assertEqual(requests, ["/api/v2/get_diary"])


class ProductReviewRegressionTests(unittest.TestCase):
    setUp = product_fixtures.ProductTests.setUp

    def test_old_category_review_cannot_create_changed_proposal(self):
        from test_products import label_values

        first = self.service.prepare(
            "Review cereal", 100, "g", label_values(), "includes_fiber", category_id=5
        )
        second = self.service.prepare(
            "Review cereal", 100, "g", label_values(), "includes_fiber", category_id=7
        )
        pid = first["product"]["proposal_id"]
        self.assertEqual(pid, second["product"]["proposal_id"])
        self.assertNotEqual(first["review_token"], second["review_token"])
        with self.assertRaises(ValidationError):
            self.service.create(pid, True, first["review_token"])
        with self.assertRaises(ValidationError):
            self.service.create(pid, True)
        self.assertEqual(self.client.adds, 0)
        self.assertTrue(self.service.create(pid, True, second["review_token"])["created"])
        self.service.create(pid, True, second["review_token"])
        self.assertEqual(self.client.adds, 1)
