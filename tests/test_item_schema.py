"""Published item contracts and safe recovery from copied review output."""

import asyncio
import importlib.util
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import test_live_meals as meal_fixtures
from test_live_drafts import DraftClient

from cronometer_assistant.live import ReadOnlyConnection, create_client
from cronometer_assistant.live_drafts import LiveDraftService, LiveMealService
from cronometer_assistant.models import ValidationError
from cronometer_assistant.read_only_server import create_read_only_server
from cronometer_assistant.server import create_server
from cronometer_assistant.service import MealService
from cronometer_assistant.store import Store

HAS_MCP = importlib.util.find_spec("mcp") is not None
HAS_LIVE_SDK = importlib.util.find_spec("cronometer_mcp") is not None
ITEM_FIELDS = {
    "description",
    "food_id",
    "grams",
    "preparation",
    "quantity_basis",
    "assumption",
    "selection_id",
}


def payload(result):
    return result[1] if isinstance(result, tuple) else json.loads(result[0].text)


def expand_ref(schema, root):
    while "$ref" in schema:
        reference = schema["$ref"]
        if not reference.startswith("#/"):
            raise AssertionError(f"Unexpected external schema reference: {reference}")
        schema = root
        for part in reference[2:].split("/"):
            schema = schema[part.replace("~1", "/").replace("~0", "~")]
    return schema


class ItemValidationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.client = DraftClient()
        self.live = LiveDraftService(Store(root / "live.sqlite"), self.client)
        self.demo = MealService(Store(root / "demo.sqlite"))

    def test_direct_malformed_items_are_actionable_atomic_and_precede_food_lookup(self):
        private_value = "private-label-value-do-not-echo"
        invalid_items = [
            [{"description": "rice", "food_id": "crono:5"}, {"name": private_value}],
            [{"description": "rice", "food_id": "crono:5", "food_name": private_value}],
            [{"description": "rice", "source": private_value}],
            [{"description": "rice", "nutrients": {"private": private_value}}],
            [{"description": "rice", "amount": private_value}],
            [{"description": "rice"}, private_value],
        ]
        for service in (self.demo, self.live):
            meal = service.begin_meal("Existing draft", day="2026-11-16")["meal"]
            key = meal["meal_id"]
            service.update_meal(
                key,
                expected_revision=0,
                items=[{"description": "unresolved chocolate"}],
                pending_questions=["Which exact product?"],
            )
            before = deepcopy(service.store.get("meals", key))
            for items in invalid_items:
                with self.subTest(mode=service.response()["mode"], items=items):
                    with patch.object(service, "_item_food") as lookup:
                        with self.assertRaises(ValidationError) as caught:
                            service.update_meal(key, expected_revision=1, items=items)
                        lookup.assert_not_called()
                    message = str(caught.exception)
                    self.assertIn("items[", message)
                    for field in ITEM_FIELDS:
                        self.assertIn(field, message)
                    self.assertNotIn(private_value, message)
                    self.assertEqual(service.store.get("meals", key), before)

    def test_direct_bad_collection_and_count_preserve_draft(self):
        for service in (self.demo, self.live):
            meal = service.begin_meal("Unchanged draft")["meal"]
            before = deepcopy(meal)
            for items in (None, {}, "rice", [{"description": "rice"}] * 101):
                with self.subTest(mode=service.response()["mode"], kind=type(items).__name__):
                    with self.assertRaisesRegex(ValidationError, "100"):
                        service.update_meal(meal["meal_id"], expected_revision=0, items=items)
                    self.assertEqual(service.store.get("meals", meal["meal_id"]), before)


@unittest.skipUnless(HAS_MCP, "MCP SDK not installed")
class RegisteredItemSchemaTests(unittest.TestCase):
    setUp = ItemValidationTests.setUp

    def servers(self):
        return [
            ("demo", create_server(self.demo), self.demo, "demo:rice-cooked", None),
            (
                "live",
                create_read_only_server(ReadOnlyConnection(self.client), draft_service=self.live),
                self.live,
                "crono:5",
                self.live.verify_food("crono:5", "cooked", True, True)["food"]["selection_id"],
            ),
        ]

    def test_actual_mcp_item_schema_exposes_fields_required_description_and_enums(self):
        asyncio.run(self.check_schemas())

    async def check_schemas(self):
        for mode, server, *_ in self.servers():
            with self.subTest(mode=mode):
                tool = next(t for t in await server.list_tools() if t.name == "update_meal_draft")
                root = tool.inputSchema
                array = expand_ref(root["properties"]["items"], root)
                self.assertEqual(array["type"], "array")
                item = expand_ref(array["items"], root)
                self.assertEqual(item["type"], "object")
                self.assertEqual(set(item["properties"]), ITEM_FIELDS)
                self.assertEqual(item["required"], ["description"])
                self.assertIs(item["additionalProperties"], False)
                self.assertEqual(item["properties"]["description"]["type"], "string")
                self.assertEqual(
                    set(item["properties"]["preparation"]["enum"]),
                    {"raw", "cooked", "as_sold", "unspecified"},
                )
                self.assertEqual(
                    set(item["properties"]["quantity_basis"]["enum"]),
                    {"weighed", "estimated", "user_default"},
                )

    def test_registered_tools_accept_known_and_unresolved_items(self):
        asyncio.run(self.valid_items())

    async def valid_items(self):
        for mode, server, service, food_id, selection_id in self.servers():
            with self.subTest(mode=mode):
                meal = payload(
                    await server.call_tool("begin_meal", {"title": "Rice and chocolate"})
                )
                key = meal["meal"]["meal_id"]
                known = {
                    "description": "rice",
                    "food_id": food_id,
                    "grams": 150,
                    "preparation": "cooked",
                    "quantity_basis": "weighed",
                }
                if selection_id:
                    known["selection_id"] = selection_id
                updated = payload(
                    await server.call_tool(
                        "update_meal_draft",
                        {
                            "meal_id": key,
                            "expected_revision": 0,
                            "items": [known, {"description": "chocolate awaiting exact label"}],
                            "pending_questions": ["Which chocolate product?"],
                        },
                    )
                )
                self.assertTrue(updated["ok"], updated)
                stored = service.store.get("meals", key)
                self.assertEqual(stored["revision"], 1)
                self.assertEqual(stored["items"][0]["grams"], 150)
                self.assertIsNone(stored["items"][1]["food_id"])
                self.assertEqual(stored["pending_questions"], ["Which chocolate product?"])
                self.assertFalse(updated["review"]["ready_to_log"])

    def test_registered_tools_reject_review_fields_aliases_and_wrong_types_before_update(self):
        asyncio.run(self.invalid_items())

    async def invalid_items(self):
        cases = [
            {"description": "rice", "food_name": "copied review name"},
            {"description": "rice", "source": "copied review source"},
            {"description": "rice", "nutrients": {}},
            {"name": "rice", "amount": 150},
            {"description": "rice", "amount": 150},
            {"description": 123},
            {"description": "rice", "grams": "150"},
            {"description": "rice", "grams": True},
            {"description": "rice", "food_id": 5},
            {"description": "rice", "preparation": "dry"},
            {"description": "rice", "quantity_basis": "guessed"},
        ]
        from mcp.server.fastmcp.exceptions import ToolError

        for mode, server, service, *_ in self.servers():
            meal = service.begin_meal("Unchanged draft")["meal"]
            for item in cases:
                with self.subTest(mode=mode, item=item):
                    with patch.object(service, "update_meal") as update:
                        with self.assertRaises(ToolError):
                            await server.call_tool(
                                "update_meal_draft",
                                {
                                    "meal_id": meal["meal_id"],
                                    "expected_revision": 0,
                                    "items": [item],
                                },
                            )
                        update.assert_not_called()
                    self.assertEqual(service.store.get("meals", meal["meal_id"]), meal)


@unittest.skipUnless(HAS_MCP and HAS_LIVE_SDK, "MCP and live SDKs not installed")
class HistoricalLunchSchemaRegressionTests(unittest.TestCase):
    def setUp(self):
        import httpx

        meal_fixtures.LiveMealTests.setUp(self)
        transport = self.client._http._transport
        names = {42: "Rice", 43: "Chicken", 44: "Lentils", 45: "Olive oil", 46: "Cucumber"}

        def respond(request):
            raw = json.loads(request.content)
            if request.url.path == "/api/v2/get_diary":
                self.assertEqual(raw["day"], "2026-11-16")
                return httpx.Response(
                    200,
                    json={
                        "diary": self.servings,
                        "info": {"day": "2026-11-16"},
                    },
                )
            if request.url.path == "/api/v2/get_food":
                food_id = raw["id"]
                return httpx.Response(
                    200,
                    json={
                        **deepcopy(meal_fixtures.FOOD),
                        "id": food_id,
                        "name": names[food_id],
                    },
                )
            return transport.handle_request(request)

        self.client = create_client(
            self.db.parent / "private" / "session.json",
            allow_diary=True,
            transport=httpx.MockTransport(respond),
        )
        self.addCleanup(self.client._http.close)
        self.restart_server()

    def restart_server(self):
        self.meals = LiveMealService(Store(self.db), self.client)
        self.server = create_read_only_server(
            ReadOnlyConnection(self.client),
            draft_service=self.meals,
            live_meals=True,
        )

    async def call(self, tool, **arguments):
        result = payload(await self.server.call_tool(tool, arguments))
        self.assertTrue(result["ok"], result)
        return result

    def test_historical_five_item_lunch_logs_once_and_unresolved_chocolate_stays_draft(self):
        asyncio.run(self.historical_lunch())

    async def historical_lunch(self):
        selected = []
        for food_id, grams in zip(range(42, 47), (150, 120, 80, 10, 100), strict=True):
            choice = await self.call(
                "verify_food_for_draft",
                food_id=f"crono:{food_id}",
                preparation="cooked",
                reviewed=True,
                per_100g_confirmed=True,
            )
            selected.append(
                {
                    "description": choice["food"]["name"],
                    "food_id": f"crono:{food_id}",
                    "grams": grams,
                    "preparation": "cooked",
                    "selection_id": choice["food"]["selection_id"],
                }
            )
        lunch = await self.call(
            "begin_meal",
            title="Historical lunch",
            day="2026-11-16",
            meal_group="lunch",
            intent="eaten",
            local_time="13:05",
        )
        lunch_id = lunch["meal"]["meal_id"]
        deferred = {"description": "Exact chocolate product unknown", "grams": 20}
        question = "Which exact product and nutrition label?"
        original = await self.call(
            "update_meal_draft",
            meal_id=lunch_id,
            expected_revision=0,
            items=[*selected, deferred],
            pending_questions=[question],
        )
        self.assertFalse(original["review"]["ready_to_log"])
        chocolate = await self.call(
            "begin_meal",
            title="Unresolved chocolate",
            day="2026-11-16",
            meal_group="lunch",
            intent="eaten",
            local_time="13:05",
        )
        chocolate_id = chocolate["meal"]["meal_id"]
        chocolate = await self.call(
            "update_meal_draft",
            meal_id=chocolate_id,
            expected_revision=0,
            items=[original["meal"]["items"][-1]],
            pending_questions=[question],
        )
        self.assertFalse(chocolate["review"]["ready_to_log"])
        pending = await self.call("get_meal_review", meal_id=chocolate_id)
        self.assertEqual(pending["meal"]["items"], chocolate["meal"]["items"])
        updated = await self.call(
            "update_meal_draft",
            meal_id=lunch_id,
            expected_revision=1,
            items=original["meal"]["items"][:-1],
            pending_questions=[],
        )
        self.assertTrue(updated["review"]["ready_to_log"], updated["review"])
        self.assertEqual(updated["meal"]["day"], "2026-11-16")
        self.assertEqual(updated["meal"]["local_time"], lunch["meal"]["local_time"])
        arguments = {
            "meal_id": lunch_id,
            "expected_revision": 2,
            "review_token": updated["review"]["review_token"],
            "confirmed": True,
        }
        logged = await self.call("log_meal", **arguments)
        self.assertEqual(logged["operation"]["state"], "committed")
        self.assertEqual(logged["verified_serving_count"], 5)
        self.assertTrue(logged["real_diary_changed"])
        self.assertEqual(len(self.writes), 5)
        self.assertEqual([row["foodId"] for row in self.writes], list(range(42, 47)))
        self.assertEqual([row["grams"] for row in self.writes], [150, 120, 80, 10, 100])
        for row in self.writes:
            self.assertEqual(row["day"], "2026-11-16")
            self.assertEqual(row["time"], "13:5:0")
            self.assertEqual(row["order"], (2 << 16) | 1)
        self.restart_server()
        repeated = await self.call("log_meal", **arguments)
        self.assertTrue(repeated["already_logged"])
        self.assertEqual(len(self.writes), 5)
        review = await self.call("get_meal_review", meal_id=chocolate_id)
        self.assertEqual(review["meal"]["revision"], 1)
        self.assertEqual(review["meal"]["items"], chocolate["meal"]["items"])
        self.assertIsNone(self.meals.store.get("operations", chocolate_id))
        self.assertFalse(review["review"]["ready_to_log"])
