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
