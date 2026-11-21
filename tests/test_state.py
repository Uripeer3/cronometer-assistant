"""Unit tests for AssistantState SQLite persistence and SessionFile security."""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from cronometer_assistant.auth import AuthError, SessionFile
from cronometer_assistant.nutrition import ValidationError
from cronometer_assistant.state import AssistantState


class StateTests(unittest.TestCase):
    def test_aliases_idempotency_drafts_and_account_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "assistant.sqlite"
            state = AssistantState(db_path, user_id=1001)
            self.assertEqual(stat.S_IMODE(db_path.stat().st_mode), 0o600)

            # Different account rejected
            with self.assertRaises(ValidationError):
                AssistantState(db_path, user_id=2002)

            # Aliases (Hebrew & English)
            saved = state.set_alias(
                "היוגורט שלי",
                food_id=555,
                food_name="Greek Yogurt 3%",
                default_grams=200,
                default_measure_id=10,
            )
            self.assertEqual(saved["food_id"], 555)
            self.assertEqual(len(state.list_aliases("יוגורט")), 1)
            self.assertTrue(state.delete_alias("היוגורט שלי"))
            self.assertEqual(state.list_aliases(), [])

            # Idempotency
            self.assertIsNone(state.get_idempotent("req-1", "log_food_entries"))
            state.save_idempotent("req-1", "log_food_entries", {"logged_count": 2})
            self.assertEqual(state.get_idempotent("req-1", "log_food_entries"), {"logged_count": 2})
            with self.assertRaises(ValidationError):
                state.get_idempotent("req-1", "create_custom_food")

            # Optional cooking drafts
            draft = state.save_draft(
                "Dinner stir-fry",
                [{"description": "tofu", "food_id": 7, "grams": 150}],
                day="2026-11-21",
                meal_group="dinner",
            )
            self.assertEqual(len(state.list_drafts()), 1)
            updated = state.save_draft(
                "Dinner stir-fry",
                [
                    {"description": "tofu", "food_id": 7, "grams": 150},
                    {"description": "olive oil", "food_id": 8, "grams": 10},
                ],
                draft_id=draft["draft_id"],
            )
            self.assertEqual(len(updated["items"]), 2)
            self.assertTrue(state.delete_draft(draft["draft_id"]))
            self.assertEqual(state.list_drafts(), [])

    def test_session_file_permissions_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            sdir = Path(tmp) / "sess"
            sdir.mkdir(mode=0o700)
            sf = SessionFile(sdir / "session.json")
            self.assertIsNone(sf.read())
            sf.write(99, "tok-secret", "Asia/Jerusalem")
            self.assertEqual(stat.S_IMODE((sdir / "session.json").stat().st_mode), 0o600)
            data = sf.read()
            self.assertEqual(data["user_id"], 99)
            self.assertEqual(data["timezone"], "Asia/Jerusalem")

            # World-readable session file must be rejected
            os.chmod(sdir / "session.json", 0o644)
            with self.assertRaises(AuthError):
                sf.read()


if __name__ == "__main__":
    unittest.main()
