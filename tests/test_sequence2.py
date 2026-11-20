"""Synthetic sequence-2 pilot: create an exact product, log, correct, undo and resume."""

import asyncio
import importlib.util
import json
import tempfile
import unittest
from contextlib import chdir
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch

import test_live_meals as meal_fixtures
from test_products import FakeClient, label_values

from cronometer_assistant.cli import main
from cronometer_assistant.live import ReadOnlyConnection, create_client
from cronometer_assistant.live_drafts import LiveDraftService, LiveMealService
from cronometer_assistant.products import ProductService
from cronometer_assistant.store import Store

HAS_SDK = importlib.util.find_spec("mcp") and importlib.util.find_spec("cronometer_mcp")


@unittest.skipUnless(HAS_SDK, "MCP and live SDKs not installed")
class CombinedPilotTests(unittest.TestCase):
    def setUp(self):
        import httpx

        meal_fixtures.LiveMealTests.setUp(self)
        self.product_writes = []
        diary_transport = self.client._http._transport
        definitions = FakeClient().get_nutrient_definitions()

        def respond(request):
            payload = json.loads(request.content)
            if request.url.path == "/api/v2/add_food":
                self.product_writes.append(deepcopy(payload["data"]))
                self.food = deepcopy(payload["data"])
                self.food.update(id=123, owner=7, meal=False, defaultMeasureId=456)
                for index, measure in enumerate(self.food["measures"]):
                    measure["id"] = 456 + index
                return httpx.Response(200, json={"id": 123})
            if request.url.path == "/api/v2/get_nutrients":
                return httpx.Response(
                    200,
                    json={"nutrients": [{"id": key, **row} for key, row in definitions.items()]},
                )
            if request.url.path == "/api/v2/find_food":
                foods = []
                if self.product_writes:
                    foods.append({"id": 123, "name": self.food["name"], "translationId": 0})
                return httpx.Response(200, json={"foods": foods})
            return diary_transport.handle_request(request)

        self.client = create_client(
            self.db.parent / "private" / "session.json",
            allow_product_creation=True,
            allow_diary=True,
            transport=httpx.MockTransport(respond),
        )
        self.addCleanup(self.client._http.close)
        self.products_db = self.db.with_name("products.sqlite")
        self.restart_server()

    def restart_server(self):
        from cronometer_assistant.read_only_server import create_read_only_server

        self.products = ProductService(Store(self.products_db), self.client)
        self.meals = LiveMealService(Store(self.db), self.client)
        self.server = create_read_only_server(
            ReadOnlyConnection(self.client),
            product_service=self.products,
            draft_service=self.meals,
            live_meals=True,
        )

    async def call(self, tool_name, *, expected_ok=True, **arguments):
        result = await self.server.call_tool(tool_name, arguments)
        data = result[1] if isinstance(result, tuple) else json.loads(result[0].text)
        self.assertEqual(data["ok"], expected_ok, data)
        return data

    def test_chocolate_product_to_diary_correction_undo_and_restart(self):
        asyncio.run(self.chocolate_product_to_diary())

    async def chocolate_product_to_diary(self):
        tools = {tool.name for tool in await self.server.list_tools()}
        self.assertTrue(
            {
                "prepare_cronometer_product",
                "create_verified_cronometer_food",
                "log_meal",
                "correct_logged_amount",
                "undo_logged_item",
            }.issubset(tools)
        )
        await self.call("search_cronometer_foods", query="Synthetic Brand chocolate")
        args = {
            "name": "Synthetic Brand, Chocolate",
            "basis_amount": 100,
            "basis_unit": "g",
            "nutrients": label_values(),
            "carbohydrate_basis": "excludes_fiber",
            "serving_grams": 25,
            "category_id": 0,
        }
        proposal = await self.call("prepare_cronometer_product", **args)
        pid = proposal["product"]["proposal_id"]
        self.assertEqual(self.product_writes, [])
        self.assertEqual(self.writes, [])
        creation = {
            "proposal_id": pid,
            "reviewed": True,
            "review_token": proposal["review_token"],
        }
        await self.call(
            "create_verified_cronometer_food",
            expected_ok=False,
            **{**creation, "reviewed": False},
        )
        await self.call(
            "create_verified_cronometer_food",
            expected_ok=False,
            **{**creation, "review_token": "unreviewed-token"},
        )
        self.assertEqual(self.product_writes, [])
        created = await self.call("create_verified_cronometer_food", **creation)
        self.assertTrue(created["created"])
        self.assertFalse(created["real_diary_changed"])
        self.assertEqual(created["product"]["remote_food_id"], 123)
        self.assertEqual(self.writes, [])
        self.assertTrue(
            (await self.call("create_verified_cronometer_food", **creation))["already_created"]
        )
        again = await self.call("prepare_cronometer_product", **args)
        self.assertEqual(again["product"]["proposal_id"], pid)
        self.assertEqual(len(self.product_writes), 1)

        stored = self.products.store.get("product_proposals", pid)
        self.assertEqual(stored["label"]["label_fields"]["carbs"]["amount"], 30)
        self.assertEqual(stored["label"]["nutrients_per_100g"]["carbs"]["amount"], 38)
        nutrients = {row["id"]: row["amount"] for row in self.food["nutrients"]}
        self.assertEqual((nutrients[205], nutrients[291], nutrients[10007]), (38, 8, 6))
        self.assertEqual(nutrients[208], 320)
        self.assertEqual(self.food["labelType"], "EU")
        measures = {row["name"]: row for row in self.food["measures"]}
        self.assertEqual(
            (measures["serving"]["type"], measures["serving"]["value"]), ("Weight", 25)
        )
        self.assertEqual((measures["g"]["type"], measures["g"]["value"]), ("Weight", 1))
        await self.call("inspect_cronometer_food", food_id="crono:123")
        selected = await self.call(
            "verify_food_for_draft",
            food_id="crono:123",
            preparation="as_sold",
            reviewed=True,
            per_100g_confirmed=True,
            translation_id=0,
            search_query="Synthetic Brand chocolate",
        )
        meal = await self.call(
            "begin_meal",
            title="Chocolate snack",
            day=meal_fixtures.DAY,
            meal_group="dinner",
            intent="eaten",
            local_time="19:15",
        )
        meal_id = meal["meal"]["meal_id"]
        draft = await self.call(
            "update_meal_draft",
            meal_id=meal_id,
            expected_revision=0,
            items=[
                {
                    "description": "Chocolate",
                    "food_id": "crono:123",
                    "grams": 37.5,
                    "preparation": "as_sold",
                    "selection_id": selected["food"]["selection_id"],
                }
            ],
        )
        self.assertTrue(draft["review"]["ready_to_log"])
        self.assertEqual(draft["review"]["nutrients"]["energy"]["known_amount"], 120)
        self.assertEqual(draft["review"]["nutrients"]["carbs"]["known_amount"], 14.25)
        logging = {
            "meal_id": meal_id,
            "expected_revision": 1,
            "review_token": draft["review"]["review_token"],
            "confirmed": True,
        }
        self.restart_server()
        self.assertEqual(
            (await self.call("get_product_proposal", proposal_id=pid))["product"]["state"],
            "created",
        )
        self.assertEqual(
            (await self.call("get_meal_review", meal_id=meal_id))["meal"]["revision"], 1
        )
        await self.call("log_meal", expected_ok=False, **{**logging, "confirmed": False})
        self.assertEqual(self.writes, [])
        logged = await self.call("log_meal", **logging)
        self.assertEqual(logged["operation"]["state"], "committed")
        self.assertEqual(len(self.writes), 1)
        row = self.servings[0]
        self.assertEqual(
            (row["foodId"], row["grams"], row["measureId"]), (123, 37.5, measures["g"]["id"])
        )
        self.assertTrue((await self.call("log_meal", **logging))["already_logged"])

        correction = {
            "meal_id": meal_id,
            "item_index": 0,
            "expected_grams": 37.5,
            "grams": 40,
            "confirmed": True,
        }
        self.assertEqual(
            (await self.call("correct_logged_amount", **correction))["state"], "verified"
        )
        self.assertTrue((await self.call("correct_logged_amount", **correction))["already_applied"])
        self.assertEqual(self.servings[0]["grams"], 40)
        self.restart_server()
        status = await self.call("get_logging_status", meal_id=meal_id)
        self.assertEqual(status["current_recorded"]["items"][0]["grams"], 40)
        undo = {"meal_id": meal_id, "item_index": 0, "expected_grams": 40, "confirmed": True}
        self.assertEqual((await self.call("undo_logged_item", **undo))["state"], "verified")
        self.assertTrue((await self.call("undo_logged_item", **undo))["already_applied"])
        self.restart_server()
        await self.call("log_meal", **logging)
        await self.call("create_verified_cronometer_food", **creation)
        self.assertEqual(self.servings, [])
        self.assertEqual(len(self.writes), 3)
        self.assertEqual(len(self.product_writes), 1)
        status = await self.call("get_logging_status", meal_id=meal_id)
        self.assertEqual(status["current_recorded"]["items"][0]["state"], "undone")
        self.assertIsNone(Store(self.db).get("product_proposals", pid))
        self.assertIsNone(Store(self.products_db).get("live_drafts:meals", meal_id))
        self.assertIsNotNone(Store(self.db).get("live_drafts:operations", meal_id))


@unittest.skipUnless(HAS_SDK, "MCP and live SDKs not installed")
class PilotCLITests(unittest.TestCase):
    def test_cli_paths_and_capabilities_keep_narrower_modes_restricted(self):
        from cronometer_assistant.read_only_server import create_read_only_server

        with tempfile.TemporaryDirectory() as directory, chdir(directory):
            root = Path(directory)
            cases = [
                ("serve-meals", [], root / "data/drafts.sqlite", root / "data/products.sqlite"),
                (
                    "serve-meals",
                    ["--db", "pilot/drafts.sqlite"],
                    root / "pilot/drafts.sqlite",
                    root / "pilot/products.sqlite",
                ),
                (
                    "serve-meals",
                    ["--db", "pilot/drafts.sqlite", "--products-db", "catalog/products.sqlite"],
                    root / "pilot/drafts.sqlite",
                    root / "catalog/products.sqlite",
                ),
                (
                    "serve-products",
                    ["--db", "products-only.sqlite"],
                    root / "products-only.sqlite",
                    root / "products-only.sqlite",
                ),
                ("serve-drafts", ["--db", "drafts-only.sqlite"], root / "drafts-only.sqlite", None),
                ("serve-read-only", [], None, None),
            ]
            for command, arguments, drafts_path, products_path in cases:
                with self.subTest(command=command, arguments=arguments):
                    client = FakeClient()
                    client._timezone = "Asia/Damascus"
                    client._http = Mock()
                    with (
                        patch("sys.argv", ["cronometer-assistant", command, *arguments]),
                        patch(
                            "cronometer_assistant.live.create_client", return_value=client
                        ) as factory,
                        patch(
                            "cronometer_assistant.read_only_server.create_read_only_server"
                        ) as server_factory,
                    ):
                        main()
                    self.assertEqual(
                        factory.call_args.kwargs["allow_product_creation"],
                        command in {"serve-meals", "serve-products"},
                    )
                    self.assertEqual(
                        factory.call_args.kwargs["allow_diary"], command == "serve-meals"
                    )
                    options = server_factory.call_args.kwargs
                    products, drafts = options["product_service"], options["draft_service"]
                    if products_path:
                        self.assertIsInstance(products, ProductService)
                        self.assertEqual(products.store.path, products_path)
                    else:
                        self.assertIsNone(products)
                    if drafts_path:
                        self.assertIsInstance(
                            drafts,
                            LiveMealService if command == "serve-meals" else LiveDraftService,
                        )
                        self.assertEqual(drafts.store.path, drafts_path)
                    else:
                        self.assertIsNone(drafts)
                    self.assertEqual(options["live_meals"], command == "serve-meals")
                    server_factory.return_value.run.assert_called_once_with(transport="stdio")
                    client._http.close.assert_called_once()
                    server = create_read_only_server(server_factory.call_args.args[0], **options)
                    names = {tool.name for tool in asyncio.run(server.list_tools())}
                    self.assertEqual(
                        "create_verified_cronometer_food" in names, products_path is not None
                    )
                    self.assertEqual("begin_meal" in names, drafts_path is not None)
                    self.assertEqual("log_meal" in names, command == "serve-meals")
                    self.assertEqual("undo_logged_item" in names, command == "serve-meals")
