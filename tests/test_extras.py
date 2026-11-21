"""Unit tests for the cleanly isolated extras modules (diary_extras, targets, tracking)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cronometer_assistant.auth import CronometerSessionClient
from cronometer_assistant.nutrition import ValidationError
from cronometer_assistant.service import CronometerAssistant
from tests.test_service_and_mcp import FakeCronometerBackend


class ExtrasTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        sdir = root / "session"
        sdir.mkdir(mode=0o700)
        session_path = sdir / "session.json"
        session_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "user_id": 1001,
                    "token": "fake-tok",
                    "timezone": "Asia/Jerusalem",
                }
            )
        )
        session_path.chmod(0o600)
        self.backend = FakeCronometerBackend(user_id=1001, timezone="Asia/Jerusalem")
        self.client = CronometerSessionClient(
            session_path, interactive=False, transport=self.backend.transport()
        )
        self.assistant = CronometerAssistant(self.client)

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def test_diary_extras_complete_copy_find_and_notes(self):
        # Log rice on 2026-11-20, copy to 2026-11-21, find across range
        self.assistant.diary.log_food_entries(
            [{"food_id": 460334, "grams": 150}], day="2026-11-20", meal_group="lunch"
        )
        copied = self.assistant.diary_extras.copy_day(to_day="2026-11-21")
        self.assertTrue(copied["copied"])
        self.assertEqual(copied["from_date"], "2026-11-20")

        found = self.assistant.diary_extras.find_entries_by_food(
            460334, start_day="2026-11-20", end_day="2026-11-21"
        )
        self.assertEqual(found["count"], 2)
        self.assertAlmostEqual(found["total_grams"], 300.0)

        # Range > 90 days rejected to prevent runaway API loops
        with self.assertRaises(ValidationError):
            self.assistant.diary_extras.find_entries_by_food(
                460334, start_day="2026-01-01", end_day="2026-11-21"
            )

        # Mark day complete & incomplete
        done = self.assistant.diary_extras.mark_day_complete("2026-11-21", complete=True)
        self.assertTrue(done["complete"] and done["verified"])
        undone = self.assistant.diary_extras.mark_day_complete("2026-11-21", complete=False)
        self.assertFalse(undone["complete"])
        self.assertTrue(undone["verified"])

        # Add & edit note
        note = self.assistant.diary_extras.add_note("High-protein day", day="2026-11-21")
        self.assertIsNotNone(note["note_id"])
        edited = self.assistant.diary_extras.edit_note(
            note["note_id"], "Updated note", day="2026-11-21"
        )
        self.assertEqual(edited["text"], "Updated note")

    def test_targets_scores_and_streak(self):
        targets = self.assistant.targets.get_targets(query="protein")
        self.assertEqual(targets["count"], 1)
        self.assertEqual(targets["targets"][0]["min"], 120.0)

        updated = self.assistant.targets.set_nutrient_target("חלבון", minimum=145, maximum=190)
        self.assertEqual(updated["target"]["min"], 145.0)
        self.assertEqual(updated["previous"]["min"], 120.0)

        scores = self.assistant.targets.get_nutrition_scores("2026-11-21")
        self.assertEqual(scores["category_count"], 2)
        self.assertEqual(self.assistant.targets.get_streak("2026-11-21")["streak"]["current"], 5)
        self.assertIn("templates", self.assistant.targets.get_macro_targets())

    def test_tracking_biometrics_exercise_and_fasting(self):
        metrics = self.assistant.tracking.list_biometrics(query="weight")
        self.assertEqual(metrics["count"], 1)
        bio = self.assistant.tracking.add_biometric(1, 1, 80.5, day="2026-11-21")
        self.assistant.tracking.edit_biometric(bio["biometric_id"], 80.2, day="2026-11-21")
        rm_bio = self.assistant.tracking.remove_biometrics([bio["biometric_id"]], day="2026-11-21")
        self.assertEqual(rm_bio["count"], 1)

        ex = self.assistant.tracking.add_exercise("Running", 30, 320, day="2026-11-21")
        self.assistant.tracking.edit_exercise(ex["exercise_id"], minutes=35, day="2026-11-21")
        rm_ex = self.assistant.tracking.remove_exercises([ex["exercise_id"]], day="2026-11-21")
        self.assertEqual(rm_ex["count"], 1)

        fast = self.assistant.tracking.add_fast("2026-11-20 20:00", end="2026-11-21 12:00")
        self.assistant.tracking.edit_fast(fast["fast_id"], goal_hours=18)
        hist = self.assistant.tracking.get_fasting_history(
            start_day="2026-11-20", end_day="2026-11-21"
        )
        self.assertEqual(len(hist["fasting"]["fasts"]), 1)
        self.assistant.tracking.delete_fast(fast["fast_id"])
        self.assertEqual(self.assistant.tracking.get_fasting_stats()["stats"]["completedCount"], 0)


if __name__ == "__main__":
    unittest.main()
