"""Exercise the real client and MCP tool registration against a fake Cronometer diary."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from cronometer_assistant.diary_changes import DiaryChanges
from cronometer_assistant.live import LiveError, SessionFile, check_request, create_client
from cronometer_assistant.live_drafts import LiveMealService
from cronometer_assistant.models import ValidationError
from cronometer_assistant.store import Store

DAY = "2026-11-08"
FOOD = {
    "id": 42,
    "name": "Steamed rice",
    "meal": False,
    "defaultMeasureId": 17,
    "measures": [
        {"id": 17, "type": "Weight", "name": "cup", "value": 158, "amount": 1},
        {"id": 18, "type": "Weight", "name": "g", "value": 1, "amount": 1},
    ],
    "nutrients": [{"id": 208, "amount": 130}],
}


@unittest.skipUnless(importlib.util.find_spec("cronometer_mcp"), "live SDK not installed")
class LiveMealTests(unittest.TestCase):
    def setUp(self):
        import httpx

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        session = root / "private" / "session.json"
        SessionFile(session).write(7, "synthetic-session", "Asia/Damascus")
        self.food = {**FOOD}
        self.profile_timezone = "Asia/Damascus"
        self.servings = []
        self.writes = []
        self.timeout_after_add = False
        self.wrong_translation_on_read = False
        self.bad_diary = False
        self.remove_is_noop = False

        def respond(request):
            path = request.url.path
            payload = json.loads(request.content)
            if path == "/api/v2/get_profile":
                return httpx.Response(200, json={"timezone": self.profile_timezone})
            if path == "/api/v2/find_food":
                return httpx.Response(
                    200,
                    json={
                        "foods": [
                            {
                                "id": 42,
                                "name": "Rice, Cooked in Unsalted Water",
                                "translationId": 77,
                                "measureId": 18,
                            }
                        ]
                    },
                )
            if path == "/api/v2/get_food":
                return httpx.Response(200, json=self.food)
            if path == "/api/v2/get_nutrients":
                return httpx.Response(
                    200,
                    json={
                        "nutrients": [{"id": 208, "name": "Energy", "unit": "kcal"}],
                    },
                )
            if path == "/api/v2/get_diary":
                if self.bad_diary:
                    return httpx.Response(200, json={})
                self.assertEqual(payload["day"], "2026-11-8")
                return httpx.Response(200, json={"diary": self.servings, "info": {"day": DAY}})
            if path == "/api/v2/add_serving":
                raw = payload["serving"]
                self.writes.append(raw)
                self.assertEqual(payload["config"], {"call_version": 2})
                key = len(self.servings) + 100
                row = {**raw, "servingId": key, "meta": {"unused": True}}
                if self.wrong_translation_on_read:
                    row["translationId"] = 0
                self.servings.append(row)
                if self.timeout_after_add:
                    raise httpx.ReadTimeout("reply lost")
                return httpx.Response(200, json={"id": key})
            if path == "/api/v2/edit_serving":
                row = payload["serving"]
                self.writes.append(row)
                index = next(
                    i for i, s in enumerate(self.servings) if s["servingId"] == row["servingId"]
                )
                self.servings[index] = row
                return httpx.Response(200, json={})
            if path == "/api/v3/user/7/diary-entries" and request.method == "DELETE":
                (row,) = payload["diaryEntries"]
                self.writes.append(row)
                self.assertNotIn("meta", row)
                self.assertEqual(row["id"], row["servingId"])
                if not self.remove_is_noop:
                    self.servings = [s for s in self.servings if s["servingId"] != row["id"]]
                return httpx.Response(204)
            self.fail(f"Unexpected endpoint {path}")

        self.client = create_client(
            session, allow_diary=True, transport=httpx.MockTransport(respond)
        )
        self.addCleanup(self.client._http.close)
        self.db = root / "drafts.sqlite"
        self.meals = LiveMealService(Store(self.db), self.client)

    def draft(self, items=1):
        self.meals.verify_food("crono:42", "cooked", True, True, 77, "cooked rice")
        meal = self.meals.begin_meal(
            "ארוחת ערב", day=DAY, meal_group="dinner", intent="eaten", local_time="19:15"
        )["meal"]
        draft = self.meals.update_meal(
            meal["meal_id"],
            expected_revision=0,
            items=[
                {
                    "description": "אורז",
                    "food_id": "crono:42",
                    "preparation": "cooked",
                    "grams": 150 + index,
                }
                for index in range(items)
            ],
        )
        self.assertTrue(draft["review"]["ready_to_log"], draft["review"])
        self.assertEqual(
            draft["review"]["servings_to_log"][0]["food_name"], "Rice, Cooked in Unsalted Water"
        )
        return meal["meal_id"], draft["review"]["review_token"]

    def test_writes_with_exact_measure_translation_day_group_and_time(self):
        key, token = self.draft(items=2)
        with self.assertRaises(ValidationError):
            self.meals.commit_meal(key, 1, review_token=token, confirmed=False)
        result = self.meals.commit_meal(key, 1, review_token=token, confirmed=True)
        self.assertEqual(result["operation"]["state"], "committed")
        self.assertEqual(result["verified_serving_count"], 2)
        self.assertTrue(result["real_diary_changed"])
        for entry, grams in zip(self.writes, (150, 151), strict=True):
            self.assertEqual(
                entry,
                {
                    "foodId": 42,
                    "day": "2026-11-8",
                    "time": "19:15:0",
                    "userId": 7,
                    "order": (3 << 16) | 1,
                    "measureId": 18,
                    "grams": grams,
                    "translationId": 77,
                    "type": "Serving",
                    "servingId": None,
                    "offset": None,
                    "source": None,
                },
            )
        restarted = LiveMealService(Store(self.db), self.client)
        repeated = restarted.commit_meal(key, 1, review_token=token, confirmed=True)
        self.assertTrue(repeated["already_logged"])
        self.assertEqual(len(self.writes), 2)
        changes = DiaryChanges(restarted)
        updated = changes.correct_amount(key, 0, 150, 160, confirmed=True)
        self.assertEqual(updated["state"], "verified")
        removed = changes.undo_item(key, 0, 160, confirmed=True)
        self.assertEqual(removed["state"], "verified")
        self.assertEqual([row["grams"] for row in self.servings], [151])

    def test_ambiguous_write_can_be_reconciled_without_replaying(self):
        key, token = self.draft()
        self.timeout_after_add = True
        first = self.meals.commit_meal(key, 1, review_token=token, confirmed=True)
        self.assertTrue(first["retry_blocked"])
        self.assertIsNone(first["real_diary_changed"])
        repeated = self.meals.commit_meal(key, 1, review_token=token, confirmed=True)
        self.assertTrue(repeated["retry_blocked"])
        candidates = self.meals.status(key)["reconciliation_candidates"]
        self.assertEqual(candidates, [{"item_index": 0, "entry_ids": ["crono-serving:100"]}])
        result = self.meals.reconcile_entry(key, 0, "crono-serving:100")
        self.assertEqual(result["operation"]["state"], "committed")
        self.assertEqual(len(self.writes), 1)

    def test_wrong_readback_and_malformed_baseline_do_not_report_logged(self):
        key, token = self.draft()
        self.bad_diary = True
        attempt = self.meals.commit_meal(key, 1, review_token=token, confirmed=True)
        self.assertEqual(attempt["operation"]["state"], "waiting_for_diary")
        self.assertEqual(self.writes, [])
        self.bad_diary = False
        self.wrong_translation_on_read = True
        attempt = self.meals.commit_meal(key, 1, review_token=token, confirmed=True)
        self.assertEqual(attempt["operation"]["state"], "needs_reconciliation")
        self.assertEqual(self.meals.status(key)["reconciliation_candidates"][0]["entry_ids"], [])

    def test_review_rejects_a_changed_measure_and_stale_translation(self):
        key, token = self.draft()
        self.food["measures"] = [{**m, "id": 19} if m["id"] == 18 else m for m in FOOD["measures"]]
        with self.assertRaisesRegex(ValidationError, "changed"):
            self.meals.commit_meal(key, 1, review_token=token, confirmed=True)
        with self.assertRaises(ValidationError):
            self.meals.verify_food("crono:42", "cooked", True, True, 999, "cooked rice")
        self.assertEqual(self.writes, [])

    def test_undo_204_without_removal_remains_unverified(self):
        key, token = self.draft()
        self.meals.commit_meal(key, 1, review_token=token, confirmed=True)
        self.remove_is_noop = True
        changes = DiaryChanges(self.meals)
        first = changes.undo_item(key, 0, 150, confirmed=True)
        self.assertEqual(first["state"], "needs_verification")
        changes.undo_item(key, 0, 150, confirmed=True)
        self.assertEqual(len(self.writes), 2)


class GuardTests(unittest.TestCase):
    def test_diary_mode_will_not_write_unrelated_entry_types_or_accounts(self):
        row = {
            "type": "Serving",
            "userId": 7,
            "foodId": 42,
            "measureId": 18,
            "order": 196609,
            "day": "2026-11-8",
            "grams": 150,
            "translationId": 77,
            "time": "19:15:0",
        }
        check_request("POST", "/api/v2/add_serving", {"serving": row}, diary_account=7)
        for changes in (
            {"type": "Note"},
            {"userId": 8},
            {"measureId": 0},
            {"translationId": None},
            {"order": 1},
        ):
            with self.assertRaises(LiveError):
                check_request(
                    "POST", "/api/v2/add_serving", {"serving": {**row, **changes}}, diary_account=7
                )
        with self.assertRaises(LiveError):
            check_request(
                "DELETE", "/api/v3/user/8/diary-entries", {"diaryEntries": [row]}, diary_account=7
            )
