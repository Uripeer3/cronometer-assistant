"""Timezone recovery uses fresh account data and never bypasses meal review."""

import asyncio
import importlib.util
import json
import os
import unittest
from copy import deepcopy
from datetime import UTC, datetime
from unittest.mock import patch

import test_live_meals

from cronometer_assistant.live import LiveError, ReadOnlyConnection
from cronometer_assistant.live_drafts import LiveDraftService
from cronometer_assistant.models import ValidationError
from cronometer_assistant.read_only_server import create_read_only_server

DAY = test_live_meals.DAY
OLD_ZONE = "Asia/Damascus"
NEW_ZONE = "America/Los_Angeles"


@unittest.skipUnless(importlib.util.find_spec("cronometer_mcp"), "live SDK not installed")
class TimezoneReviewTests(unittest.TestCase):
    # Reuse the HTTP transport fixture without inheriting its test methods.
    setUp = test_live_meals.LiveMealTests.setUp
    draft = test_live_meals.LiveMealTests.draft

    def confirm(self, meal_id, **changes):
        return self.meals.confirm_meal_timezone(
            meal_id,
            **{
                "expected_revision": 1,
                "timezone": NEW_ZONE,
                "day": DAY,
                "local_time": "20:10",
                "confirmed": True,
                **changes,
            },
        )

    def assert_unchanged(self, meal_id, meal, operation=None):
        self.assertEqual(self.meals.store.get("meals", meal_id), meal)
        self.assertEqual(self.meals.store.get("operations", meal_id), operation)
        self.assertEqual(self.writes, [])

    def test_changed_profile_blocks_review_with_actionable_recovery(self):
        key, token = self.draft()
        before = self.meals.store.get("meals", key)
        self.profile_timezone = NEW_ZONE
        review = self.meals.review_meal(key)["review"]
        self.assertFalse(review["ready_to_log"])
        self.assertNotIn("review_token", review)
        error = review["write_preflight_error"]
        for detail in (OLD_ZONE, NEW_ZONE, "confirm_meal_timezone"):
            self.assertIn(detail, error)
        with self.assertRaises(ValidationError):
            self.meals.commit_meal(key, 1, review_token=token, confirmed=True)
        self.assert_unchanged(key, before)

    def test_confirmation_refreshes_review_and_stale_token_cannot_log(self):
        key, old_token = self.draft()
        before = self.meals.store.get("meals", key)
        self.profile_timezone = NEW_ZONE
        result = self.confirm(key)
        meal, review = result["meal"], result["review"]
        self.assertFalse(result["real_diary_changed"])
        self.assertEqual(meal["timezone"], NEW_ZONE)
        self.assertEqual(meal["day"], DAY)
        self.assertEqual(meal["local_time"], "20:10:00")
        self.assertEqual(meal["revision"], 2)
        for field in ("meal_id", "title", "items", "pending_questions", "meal_group", "intent"):
            self.assertEqual(meal[field], before[field])
        self.assertTrue(review["ready_to_log"], review)
        self.assertNotEqual(review["review_token"], old_token)
        self.assertEqual(self.writes, [])
        self.assertIsNone(self.meals.store.get("operations", key))
        with self.assertRaises(ValidationError):
            self.meals.commit_meal(key, 2, review_token=old_token, confirmed=True)
        self.assert_unchanged(key, meal)
        with self.assertRaises(ValidationError):
            self.meals.commit_meal(key, 1, review_token=old_token, confirmed=True)
        self.assert_unchanged(key, meal)
        logged = self.meals.commit_meal(key, 2, review_token=review["review_token"], confirmed=True)
        self.assertEqual(logged["operation"]["state"], "committed")
        self.assertEqual(len(self.writes), 1)
        self.assertEqual(self.writes[0]["time"], "20:10:0")
        self.assertEqual(self.writes[0]["day"], "2026-11-8")
        self.assertEqual(self.writes[0]["order"], (3 << 16) | 1)

    def test_confirmation_preserves_unresolved_questions_and_items(self):
        key, _ = self.draft(items=2)
        meal = self.meals.store.get("meals", key)
        before = self.meals.update_meal(
            key,
            expected_revision=1,
            items=meal["items"],
            pending_questions=["Was any cooking oil added?"],
        )["meal"]
        self.profile_timezone = NEW_ZONE
        result = self.confirm(key, expected_revision=2, day="2026-11-07", local_time="23:45")
        after = result["meal"]
        self.assertEqual(after["revision"], 3)
        self.assertEqual(after["day"], "2026-11-07")
        self.assertEqual(after["local_time"], "23:45:00")
        for field in ("title", "items", "pending_questions", "meal_group", "intent"):
            self.assertEqual(after[field], before[field])
        self.assertFalse(result["review"]["ready_to_log"])
        self.assertNotIn("review_token", result["review"])
        self.assertIn(
            "Was any cooking oil added?",
            [question["question"] for question in result["review"]["questions"]],
        )
        self.assertEqual(self.writes, [])

    def test_a_second_profile_change_requires_another_confirmation(self):
        key, _ = self.draft()
        self.profile_timezone = NEW_ZONE
        confirmed = self.confirm(key)
        self.profile_timezone = "Asia/Tokyo"
        review = self.meals.review_meal(key)["review"]
        self.assertFalse(review["ready_to_log"])
        self.assertIn(NEW_ZONE, review["write_preflight_error"])
        self.assertIn("Asia/Tokyo", review["write_preflight_error"])
        with self.assertRaises(ValidationError):
            self.meals.commit_meal(
                key, 2, review_token=confirmed["review"]["review_token"], confirmed=True
            )
        self.assert_unchanged(key, confirmed["meal"])

    def test_confirmation_rejects_wrong_zone_unapproved_and_invalid_revision(self):
        key, _ = self.draft()
        before = self.meals.store.get("meals", key)
        self.profile_timezone = NEW_ZONE
        for changes in (
            {"timezone": OLD_ZONE},
            {"timezone": "US/Pacific"},  # Equivalent zone is not the fresh profile's exact value.
            {"timezone": "not/a-zone"},
            {"confirmed": False},
            {"confirmed": None},
            {"confirmed": 1},
            {"expected_revision": 0},
            {"expected_revision": 99},
            {"expected_revision": True},
            {"expected_revision": 1.0},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises((ValidationError, LiveError)):
                    self.confirm(key, **changes)
                self.assert_unchanged(key, before)
        with self.assertRaises(ValidationError):
            self.meals.confirm_meal_timezone(
                key, expected_revision=1, timezone=NEW_ZONE, day=DAY, local_time="20:10"
            )
        self.assert_unchanged(key, before)

    def test_confirmation_requires_explicit_valid_day_and_local_time(self):
        key, _ = self.draft()
        before = self.meals.store.get("meals", key)
        self.profile_timezone = NEW_ZONE
        for changes in (
            {"day": None},
            {"day": ""},
            {"day": "2026-11-31"},
            {"day": "2026-1-8"},
            {"local_time": None},
            {"local_time": ""},
            {"local_time": "24:00"},
            {"local_time": "19:60"},
            {"local_time": "20:10Z"},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(ValidationError):
                    self.confirm(key, **changes)
                self.assert_unchanged(key, before)

    def test_confirmation_rejects_every_existing_operation_without_mutation(self):
        key, _ = self.draft()
        before = self.meals.store.get("meals", key)
        self.profile_timezone = NEW_ZONE
        operations = [{}] + [
            {"state": state, "steps": []}
            for state in (
                "writing",
                "waiting_for_diary",
                "needs_review",
                "needs_reconciliation",
                "needs_verification",
                "committed",
                "completed_with_cancellations",
            )
        ]
        for operation in operations:
            with self.subTest(operation=operation):
                self.meals.store.put("operations", key, deepcopy(operation))
                with self.assertRaises(ValidationError):
                    self.confirm(key)
                self.assert_unchanged(key, before, operation)

    def test_new_draft_uses_fresh_profile_zone_and_day_without_rewriting_session(self):
        old_key, _ = self.draft()
        old_meal = self.meals.store.get("meals", old_key)
        session = self.db.parent / "private" / "session.json"
        session_bytes = session.read_bytes()
        self.assertEqual(self.client._timezone, OLD_ZONE)
        self.profile_timezone = NEW_ZONE

        class Clock:
            @staticmethod
            def now(zone):
                return datetime(2026, 11, 8, 23, 30, tzinfo=UTC).astimezone(zone)

        with (
            patch("cronometer_assistant.live_drafts.datetime", Clock),
            patch("cronometer_assistant.service.datetime", Clock),
        ):
            meal = self.meals.begin_meal("fresh timezone")["meal"]
        self.assertEqual(meal["timezone"], NEW_ZONE)
        self.assertEqual(meal["day"], DAY)
        self.assertEqual(meal["local_time"], "15:30:00")
        self.assertEqual(str(self.meals.timezone), OLD_ZONE)
        self.assertEqual(self.client._timezone, OLD_ZONE)
        self.assertEqual(session.read_bytes(), session_bytes)
        self.assertEqual(self.meals.store.get("meals", old_key), old_meal)
        self.assertEqual(self.writes, [])

    def test_interleaved_new_drafts_preserve_each_fresh_timezone_snapshot(self):
        class Clock:
            @staticmethod
            def now(zone):
                return datetime(2026, 11, 8, 23, 30, tzinfo=UTC).astimezone(zone)

        self.profile_timezone = NEW_ZONE
        begin = self.meals._begin_meal
        interleaved = []

        def interleave(title, **kwargs):
            if title == "first":
                self.profile_timezone = "Asia/Tokyo"
                interleaved.append(self.meals.begin_meal("second")["meal"])
            return begin(title, **kwargs)

        with (
            patch("cronometer_assistant.live_drafts.datetime", Clock),
            patch.object(self.meals, "_begin_meal", side_effect=interleave),
        ):
            first = self.meals.begin_meal("first")["meal"]
        self.assertEqual(first["timezone"], NEW_ZONE)
        self.assertEqual(first["day"], DAY)
        self.assertEqual(first["local_time"], "15:30:00")
        self.assertEqual(interleaved[0]["timezone"], "Asia/Tokyo")
        self.assertEqual(interleaved[0]["day"], "2026-11-09")
        self.assertEqual(interleaved[0]["local_time"], "08:30:00")
        self.assertEqual(str(self.meals.timezone), OLD_ZONE)
        self.assertEqual(self.client._timezone, OLD_ZONE)
        self.assertEqual(self.writes, [])

    def test_confirmation_cannot_use_cached_zone_when_profile_is_unavailable(self):
        key, _ = self.draft()
        before = self.meals.store.get("meals", key)
        with patch.object(self.client, "get_profile", return_value={}):
            with self.assertRaises(LiveError):
                self.confirm(key, timezone=OLD_ZONE)
        self.assert_unchanged(key, before)

    def require_mcp(self):
        if importlib.util.find_spec("mcp") is None:
            if os.environ.get("REQUIRE_MCP_TESTS") == "1":
                self.fail("MCP SDK is required")
            self.skipTest("MCP SDK not installed")

    def test_meals_connection_check_reports_the_fresh_profile_timezone(self):
        self.require_mcp()
        asyncio.run(self.check_timezone_source())

    async def check_timezone_source(self):
        self.profile_timezone = NEW_ZONE
        connection = ReadOnlyConnection(self.client)
        for live_meals, expected_zone, expected_source in (
            (True, NEW_ZONE, "current_profile"),
            (False, OLD_ZONE, "cached_session"),
        ):
            server = create_read_only_server(
                connection,
                draft_service=self.meals if live_meals else None,
                live_meals=live_meals,
            )
            result = await server.call_tool("check_cronometer_connection", {})
            data = result[1] if isinstance(result, tuple) else json.loads(result[0].text)
            self.assertTrue(data["ok"], data)
            self.assertEqual(data["account_timezone"], expected_zone)
            self.assertEqual(data["timezone_source"], expected_source)
        self.assertEqual(self.writes, [])

    def test_real_mcp_schema_and_recovery_call_are_limited_to_meals_mode(self):
        self.require_mcp()
        asyncio.run(self.mcp_recovery())

    async def mcp_recovery(self):
        key, _ = self.draft()
        self.profile_timezone = NEW_ZONE
        connection = ReadOnlyConnection(self.client)
        server = create_read_only_server(connection, draft_service=self.meals, live_meals=True)
        tools = {tool.name: tool for tool in await server.list_tools()}
        self.assertIn("confirm_meal_timezone", tools)
        schema = tools["confirm_meal_timezone"].inputSchema
        self.assertTrue(
            {"meal_id", "expected_revision", "timezone", "day", "local_time"}.issubset(
                schema["required"]
            )
        )
        self.assertEqual(schema["properties"]["confirmed"]["type"], "boolean")

        def payload(result):
            return result[1] if isinstance(result, tuple) else json.loads(result[0].text)

        args = {
            "meal_id": key,
            "expected_revision": 1,
            "timezone": NEW_ZONE,
            "day": DAY,
            "local_time": "20:10",
        }
        before = self.meals.store.get("meals", key)
        rejected = payload(
            await server.call_tool("confirm_meal_timezone", {**args, "confirmed": False})
        )
        self.assertFalse(rejected["ok"])
        self.assert_unchanged(key, before)
        result = payload(
            await server.call_tool("confirm_meal_timezone", {**args, "confirmed": True})
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["meal"]["revision"], 2)
        self.assertTrue(result["review"]["ready_to_log"])
        self.assertTrue(result["review"]["review_token"])
        self.assertEqual(self.writes, [])

        draft_only = LiveDraftService(self.meals.store.store, self.client)
        for other_server in (
            create_read_only_server(connection),
            create_read_only_server(connection, draft_service=draft_only),
        ):
            self.assertNotIn(
                "confirm_meal_timezone", {tool.name for tool in await other_server.list_tools()}
            )
