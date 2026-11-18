"""Real SDK/stdio integration test; required in CI, optional in an offline checkout."""

import asyncio
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


class MCPTests(unittest.TestCase):
    def test_read_only_surface_and_scrubbed_errors(self):
        if importlib.util.find_spec("mcp") is None:
            if os.environ.get("REQUIRE_MCP_TESTS") == "1":
                self.fail("MCP SDK is required for this CI job")
            self.skipTest("MCP SDK not installed")
        asyncio.run(self.read_only_surface())

    async def read_only_surface(self):
        from cronometer_assistant.live import ReadOnlyConnection
        from cronometer_assistant.read_only_server import create_read_only_server

        class Client:
            def search_food(self, query):
                raise RuntimeError("sensitive-token")

        server = create_read_only_server(ReadOnlyConnection(Client()))
        tools = await server.list_tools()
        self.assertEqual(
            {tool.name for tool in tools},
            {"check_cronometer_connection", "search_cronometer_foods", "inspect_cronometer_food"},
        )
        self.assertTrue(all(tool.annotations.readOnlyHint for tool in tools))
        result = await server.call_tool("search_cronometer_foods", {"query": "rice"})
        rendered = str(result)
        self.assertNotIn("sensitive-token", rendered)
        self.assertIn("Cronometer read failed", rendered)

    def test_stdio_round_trip(self):
        if importlib.util.find_spec("mcp") is None:
            if os.environ.get("REQUIRE_MCP_TESTS") == "1":
                self.fail("MCP SDK is required for this CI job")
            self.skipTest("MCP SDK not installed; core tests remain available offline")
        asyncio.run(self.round_trip())

    async def round_trip(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        def payload(result):
            self.assertFalse(result.isError)
            return result.structuredContent or json.loads(result.content[0].text)

        with tempfile.TemporaryDirectory() as directory:
            params = StdioServerParameters(
                command=sys.executable,
                args=[
                    "-m",
                    "cronometer_assistant",
                    "serve",
                    "--db",
                    str(Path(directory) / "test.sqlite"),
                ],
                env={**os.environ},
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    names = {t.name for t in tools.tools}
                    self.assertIn("commit_meal", names)
                    self.assertIn("propose_product_from_label", names)
                    self.assertIn("delete_meal_draft", names)
                    draft = payload(await session.call_tool("begin_meal", {"title": "discard me"}))
                    deleted = payload(
                        await session.call_tool(
                            "delete_meal_draft",
                            {
                                "meal_id": draft["meal"]["meal_id"],
                                "expected_revision": 0,
                            },
                        )
                    )
                    self.assertTrue(deleted["deleted"])
                    missing = payload(
                        await session.call_tool(
                            "get_meal_review",
                            {
                                "meal_id": draft["meal"]["meal_id"],
                            },
                        )
                    )
                    self.assertFalse(missing["ok"])
                    created = payload(
                        await session.call_tool(
                            "begin_meal",
                            {
                                "title": "ארוחה לבדיקה",
                                "intent": "eaten",
                            },
                        )
                    )
                    meal_id = created["meal"]["meal_id"]
                    updated = payload(
                        await session.call_tool(
                            "update_meal_draft",
                            {
                                "meal_id": meal_id,
                                "expected_revision": 0,
                                "items": [
                                    {
                                        "description": "אורז",
                                        "food_id": "demo:rice-cooked",
                                        "grams": 150,
                                        "preparation": "cooked",
                                    }
                                ],
                            },
                        )
                    )
                    self.assertTrue(updated["review"]["ready_to_log"])
                    args = {"meal_id": meal_id, "expected_revision": 1}
                    committed = payload(await session.call_tool("commit_meal", args))
                    self.assertEqual(committed["operation"]["state"], "committed")
                    again = payload(await session.call_tool("commit_meal", args))
                    self.assertTrue(again["already_logged"])
                    self.assertFalse(again["real_diary_changed"])

    def test_product_mode_surface_and_reviewed_creation(self):
        if importlib.util.find_spec("mcp") is None:
            if os.environ.get("REQUIRE_MCP_TESTS") == "1":
                self.fail("MCP SDK is required")
            self.skipTest("MCP SDK not installed")
        asyncio.run(self.product_surface())

    async def product_surface(self):
        from test_products import FakeClient

        from cronometer_assistant.live import ReadOnlyConnection
        from cronometer_assistant.products import ProductService
        from cronometer_assistant.read_only_server import create_read_only_server
        from cronometer_assistant.store import Store

        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient()
            service = ProductService(Store(Path(directory) / "products.sqlite"), client)
            server = create_read_only_server(ReadOnlyConnection(client), product_service=service)
            names = {t.name for t in await server.list_tools()}
            self.assertIn("create_verified_cronometer_food", names)
            self.assertIn("prepare_public_food_submission", names)
            self.assertIn("import_product_photo", names)
            self.assertIn("get_product_evidence", names)
            self.assertNotIn("commit_meal", names)
            self.assertNotIn("submit_food_for_review", names)
            result = await server.call_tool(
                "prepare_cronometer_product",
                {
                    "name": "Synthetic brand, vanilla",
                    "basis_amount": 100,
                    "basis_unit": "g",
                    "nutrients": {"protein": {"amount": 10, "unit": "g"}},
                    "category_id": 0,
                },
            )

            def payload(value):
                if isinstance(value, tuple):
                    return value[1]
                return json.loads(value[0].text)

            data = payload(result)
            self.assertTrue(data["ok"])
            pid = data["product"]["proposal_id"]
            evidence = payload(
                await server.call_tool(
                    "attach_product_evidence_reference",
                    {
                        "proposal_id": pid,
                        "role": "package_front",
                        "provider": "host",
                        "reference": "synthetic-front",
                    },
                )
            )
            self.assertFalse(evidence["evidence"]["local_bytes_verified"])
            blocked_import = payload(
                await server.call_tool(
                    "import_product_photo",
                    {"proposal_id": pid, "role": "package_front", "inbox_name": "front.png"},
                )
            )
            self.assertFalse(blocked_import["ok"])
            self.assertEqual(client.adds, 0)
            blocked = await server.call_tool(
                "create_verified_cronometer_food",
                {
                    "proposal_id": pid,
                    "reviewed": False,
                },
            )
            self.assertFalse(payload(blocked)["ok"])
            self.assertEqual(client.adds, 0)
            created = await server.call_tool(
                "create_verified_cronometer_food",
                {
                    "proposal_id": pid,
                    "reviewed": True,
                },
            )
            self.assertTrue(payload(created)["created"])
            self.assertFalse(payload(created)["real_diary_changed"])
            choice = payload(
                await server.call_tool(
                    "set_product_submission_choice",
                    {"proposal_id": pid, "expected_revision": 0, "choice": "declined"},
                )
            )
            self.assertEqual(choice["submission"]["choice"], "declined")
            checklist = payload(
                await server.call_tool("prepare_public_food_submission", {"proposal_id": pid})
            )
            self.assertFalse(checklist["should_offer_submission"])
            self.assertFalse(checklist["submitted"])
            self.assertEqual(client.adds, 1)

    def test_repair_surface_rechecks_the_same_food(self):
        if importlib.util.find_spec("mcp") is None:
            if os.environ.get("REQUIRE_MCP_TESTS") == "1":
                self.fail("MCP SDK is required")
            self.skipTest("MCP SDK not installed")
        asyncio.run(self.repair_surface())

    async def repair_surface(self):
        from test_products import FakeClient, label_values

        from cronometer_assistant.live import ReadOnlyConnection
        from cronometer_assistant.products import ProductService
        from cronometer_assistant.read_only_server import create_read_only_server
        from cronometer_assistant.store import Store

        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient()
            service = ProductService(Store(Path(directory) / "products.sqlite"), client)
            pid = service.prepare(
                "Synthetic cereal",
                100,
                "g",
                label_values(),
                carbohydrate_basis="excludes_fiber",
                category_id=0,
            )["product"]["proposal_id"]
            service.create(pid, True)
            server = create_read_only_server(ReadOnlyConnection(client), product_service=service)

            def payload(result):
                return result[1] if isinstance(result, tuple) else json.loads(result[0].text)

            review = payload(
                await server.call_tool(
                    "prepare_product_repair",
                    {"proposal_id": pid, "category_id": 0, "serving_grams": 100},
                )
            )
            adopted = payload(
                await server.call_tool(
                    "adopt_manual_product_repair",
                    {"repair_id": review["repair_id"], "reviewed": True},
                )
            )
            self.assertTrue(adopted["adopted"])
            self.assertFalse(adopted["remote_food_changed"])
            self.assertEqual(client.adds, 1)

    def test_live_draft_stdio_round_trip(self):
        if importlib.util.find_spec("mcp") is None:
            if os.environ.get("REQUIRE_MCP_TESTS") == "1":
                self.fail("MCP SDK is required")
            self.skipTest("MCP SDK not installed")
        asyncio.run(self.live_draft_round_trip())

    async def live_draft_round_trip(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        def payload(result):
            self.assertFalse(result.isError)
            return result.structuredContent or json.loads(result.content[0].text)

        with tempfile.TemporaryDirectory() as directory:
            code = (
                "import sys; from pathlib import Path; "
                f"sys.path.insert(0, {str(Path(__file__).parent.resolve())!r}); "
                "from test_live_drafts import DraftClient; "
                "from cronometer_assistant.live import ReadOnlyConnection; "
                "from cronometer_assistant.live_drafts import LiveDraftService; "
                "from cronometer_assistant.read_only_server import create_read_only_server; "
                "from cronometer_assistant.store import Store; client=DraftClient(); "
                f"service=LiveDraftService(Store(Path({directory!r})/'test.sqlite'),client); "
                "create_read_only_server(ReadOnlyConnection(client),draft_service=service).run(transport='stdio')"
            )
            async with stdio_client(
                StdioServerParameters(command=sys.executable, args=["-c", code], env={**os.environ})
            ) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    names = {t.name for t in (await session.list_tools()).tools}
                    self.assertNotIn("commit_meal", names)
                    verified = payload(
                        await session.call_tool(
                            "verify_food_for_draft",
                            {
                                "food_id": "crono:5",
                                "preparation": "cooked",
                                "reviewed": True,
                                "per_100g_confirmed": True,
                            },
                        )
                    )
                    self.assertTrue(verified["verified_for_drafts"])
                    meal = payload(await session.call_tool("begin_meal", {"title": "אורז / rice"}))[
                        "meal"
                    ]
                    result = payload(
                        await session.call_tool(
                            "update_meal_draft",
                            {
                                "meal_id": meal["meal_id"],
                                "expected_revision": 0,
                                "intent": "eaten",
                                "items": [
                                    {
                                        "description": "אורז",
                                        "food_id": "crono:5",
                                        "grams": 100,
                                        "preparation": "cooked",
                                    }
                                ],
                            },
                        )
                    )
                    self.assertEqual(result["review"]["nutrients"]["energy"]["known_amount"], 120)
                    self.assertTrue(result["review"]["draft_complete"])
                    self.assertFalse(result["review"]["ready_to_log"])
                    self.assertFalse(result["real_diary_changed"])
