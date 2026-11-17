"""Real-data workflow shape with synthetic profiles and an unavailable diary."""

import asyncio
import importlib.util
import os
import tempfile
import unittest
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from cronometer_assistant.live import ReadOnlyConnection
from cronometer_assistant.live_drafts import LiveDraftService
from cronometer_assistant.models import ValidationError
from cronometer_assistant.read_only_server import create_read_only_server
from cronometer_assistant.service import MealService
from cronometer_assistant.store import Store


class DraftClient:
    user_id = 7
    _timezone = "Pacific/Kiritimati"

    def __init__(self):
        self.foods = {
            5: {
                "id": 5,
                "name": "Synthetic rice",
                "source": "unknown:9",
                "nutrients": [{"id": 208, "amount": 120}, {"id": 203, "amount": 2}],
            }
        }

    def get_nutrient_definitions(self):
        return {208: {"name": "Energy", "unit": "kcal"}, 203: {"name": "Protein", "unit": "g"}}

    def get_food(self, food_id):
        return deepcopy(self.foods[food_id])

    def search_food(self, query):
        return (
            [{"id": 5, "name": "Synthetic rice", "source": "unknown:9"}] if "rice" in query else []
        )


class LiveDraftTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "test.sqlite")
        self.client = DraftClient()
        self.service = LiveDraftService(self.store, self.client)

    def verify(self):
        return self.service.verify_food("crono:5", "cooked", True, True)

    def test_drafts_require_review_and_cannot_write_any_diary(self):
        meal = self.service.begin_meal("אורז / rice")["meal"]
        with self.assertRaises(ValidationError):
            self.service.update_meal(
                meal["meal_id"],
                expected_revision=0,
                items=[{"description": "אורז", "food_id": "crono:5", "grams": 150}],
            )
        self.verify()
        result = self.service.update_meal(
            meal["meal_id"],
            expected_revision=0,
            items=[{"description": "אורז", "food_id": "crono:5", "grams": 150}],
        )
        self.assertEqual(result["review"]["questions"][0]["reason"], "preparation")
        result = self.service.update_meal(
            meal["meal_id"],
            expected_revision=1,
            intent="eaten",
            items=[
                {"description": "אורז", "food_id": "crono:5", "grams": 150, "preparation": "cooked"}
            ],
        )
        self.assertTrue(result["review"]["draft_complete"])
        self.assertFalse(result["review"]["ready_to_log"])
        self.assertFalse(result["logging_supported"])
        self.assertEqual(result["review"]["nutrients"]["energy"]["known_amount"], 180)
        self.assertFalse(result["review"]["nutrients"]["fiber"]["complete"])
        for function, args in [
            (self.service.commit_meal, (meal["meal_id"], 2)),
            (self.service.reconcile_entry, (meal["meal_id"], 0, "5")),
        ]:
            with self.assertRaises(ValidationError):
                function(*args)
        self.assertEqual(self.store.values("live_drafts:operations"), [])
        self.assertEqual(self.store.values("demo_diary"), [])
        self.assertEqual(self.service.status(meal["meal_id"])["diary_status"], "not_accessed")

    def test_aliases_restart_and_demo_namespaces_are_separate(self):
        self.verify()
        self.service.remember_product("האורז שלי", "crono:5")
        meal = self.service.begin_meal("live")["meal"]
        demo = MealService(self.store)
        demo.begin_meal("demo")
        restarted = LiveDraftService(Store(self.store.path), self.client)
        candidates = restarted.resolve_food("האורז שלי")["candidates"]
        self.assertEqual(candidates[0]["food_id"], "crono:5")
        self.assertEqual(candidates[0]["preparation"], "cooked")
        self.assertTrue(candidates[0]["source"].startswith("UNVERIFIED:"))
        self.assertEqual(len(restarted.list_drafts()["drafts"]), 1)
        with self.assertRaises(ValidationError):
            restarted.delete_draft(meal["meal_id"], 99)
        restarted.delete_draft(meal["meal_id"], 0)
        self.assertEqual(len(self.store.values("meals")), 1)

    def test_profile_change_account_binding_timezone_and_invalid_ids(self):
        self.verify()
        self.client.foods[5]["nutrients"][0]["amount"] = 999
        with self.assertRaisesRegex(ValidationError, "changed"):
            self.service.catalog.get("crono:5")
        self.client.user_id = 8
        with self.assertRaises(ValidationError):
            LiveDraftService(self.store, self.client)
        self.client.user_id = 7
        self.client._timezone = "Asia/Jerusalem"
        with self.assertRaisesRegex(ValidationError, "timezone changed"):
            self.service.begin_meal("changed")
        for food_id in ["crono:-5", "crono:٥", "demo:egg"]:
            with self.assertRaises(ValidationError):
                self.service.verify_food(food_id, "cooked", True, True)
        with self.assertRaises(ValidationError):
            self.service.verify_food("crono:5", "cooked", True, False)

    def test_default_date_uses_account_timezone(self):
        class Clock:
            @staticmethod
            def now(zone):
                return datetime(2026, 1, 1, 18, tzinfo=UTC).astimezone(zone)

        with patch("cronometer_assistant.service.datetime", Clock):
            self.assertEqual(self.service.begin_meal("timezone")["meal"]["day"], "2026-01-02")

    def test_real_mcp_surface_has_no_diary_tools(self):
        if importlib.util.find_spec("mcp") is None:
            if os.environ.get("REQUIRE_MCP_TESTS") == "1":
                self.fail("MCP SDK is required")
            self.skipTest("MCP SDK not installed")
        asyncio.run(self.surface())

    async def surface(self):
        server = create_read_only_server(
            ReadOnlyConnection(self.client), draft_service=self.service
        )
        names = {t.name for t in await server.list_tools()}
        self.assertIn("resolve_food", names)
        self.assertIn("verify_food_for_draft", names)
        self.assertIn("list_meal_drafts", names)
        self.assertNotIn("commit_meal", names)
        self.assertNotIn("reconcile_demo_entry", names)
        self.assertNotIn("create_verified_cronometer_food", names)
        result = await server.call_tool("begin_meal", {"title": "ארוחה"})
        self.assertIn("cronometer_drafts", str(result))
