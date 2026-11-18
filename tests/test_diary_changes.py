import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from cronometer_assistant.diary_changes import DiaryChanges
from cronometer_assistant.models import DiaryEntry, ValidationError
from cronometer_assistant.store import Store


class DiaryFixture:
    def __init__(self, serving, other):
        self.rows = [serving, other]
        self.calls = []
        self.fail_to_send = False
        self.fail_read_after_write = False

    def entries(self, day):
        if self.calls and self.fail_read_after_write:
            raise TimeoutError
        return [entry for entry in self.rows if entry.day == day]

    def edit_amount(self, entry, grams):
        self.calls.append(("edit", entry.entry_id, grams))
        if self.fail_to_send:
            raise TimeoutError
        self.rows[self.rows.index(entry)] = replace(entry, grams=grams)

    def delete(self, entry):
        self.calls.append(("delete", entry.entry_id))
        if self.fail_to_send:
            raise TimeoutError
        self.rows.remove(entry)


class DiaryChangesTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.store = Store(Path(folder) / "meals.sqlite")
        self.serving = DiaryEntry(
            "crono-serving:10", "crono:5", 100.0, "2026-11-18", "dinner", "20:05:00", 50, 0
        )
        self.other = replace(self.serving, entry_id="crono-serving:11")
        self.diary = DiaryFixture(self.serving, self.other)
        self.meal = {"meal_id": "meal-1", "day": self.serving.day, "meal_group": "dinner"}
        self.meals = SimpleNamespace(
            store=self.store,
            _meal=lambda key: self.store.get("meals", key),
            diary=self.diary,
            response=lambda **fields: {"real_diary_changed": False, **fields},
        )
        self.changes = DiaryChanges(self.meals)
        parameters = self.serving.to_dict()
        parameters.pop("entry_id")
        self.store.put("meals", "meal-1", self.meal)
        self.store.put(
            "operations",
            "meal-1",
            {
                "meal_id": "meal-1",
                "state": "committed",
                "steps": [
                    {
                        "state": "verified",
                        "entry_id": self.serving.entry_id,
                        "item": {"food_id": self.serving.food_id},
                        "add_parameters": parameters,
                    }
                ],
            },
        )

    def test_edit_then_undo_only_touches_the_chosen_serving(self):
        corrected = self.changes.correct_amount("meal-1", 0, 100, 125, confirmed=True)
        self.assertEqual(corrected["state"], "verified")
        self.assertTrue(corrected["real_diary_changed"])
        repeated = self.changes.correct_amount("meal-1", 0, 100, 125, confirmed=True)
        self.assertTrue(repeated["already_applied"])
        with self.assertRaisesRegex(ValidationError, "current logged amount"):
            self.changes.undo_item("meal-1", 0, 100, confirmed=True)
        self.assertTrue(
            self.changes.undo_item("meal-1", 0, 125, confirmed=True)["real_diary_changed"]
        )
        self.assertTrue(self.changes.undo_item("meal-1", 0, 125, confirmed=True)["already_applied"])
        self.assertEqual(self.diary.rows, [self.other])
        self.assertEqual(
            self.diary.calls, [("edit", "crono-serving:10", 125), ("delete", "crono-serving:10")]
        )
        self.assertEqual(self.store.get("operations", "meal-1")["state"], "committed")

    def test_confirmation_and_external_changes_prevent_mutation(self):
        with self.assertRaisesRegex(ValidationError, "Confirm"):
            self.changes.correct_amount("meal-1", 0, 100, 120, confirmed=False)
        for changed in (
            replace(self.serving, grams=130),
            replace(self.serving, food_id="crono:6"),
            replace(self.serving, local_time="21:15:00"),
            replace(self.serving, measure_id=51),
            replace(self.serving, translation_id=1),
            replace(self.serving, meal_group="lunch"),
            replace(self.serving, day="2026-11-19"),
        ):
            with self.subTest(changed=changed):
                self.diary.rows[0] = changed
                with self.assertRaisesRegex(ValidationError, "changed in Cronometer"):
                    self.changes.undo_item("meal-1", 0, 100, confirmed=True)
        self.assertEqual(self.diary.calls, [])

    def test_failed_readback_reconciles_on_retry_even_in_new_instance(self):
        self.diary.fail_read_after_write = True
        waiting = self.changes.undo_item("meal-1", 0, 100, confirmed=True)
        self.assertEqual(waiting["state"], "needs_verification")
        self.assertIsNone(waiting["real_diary_changed"])
        self.diary.fail_read_after_write = False
        verified = DiaryChanges(self.meals).undo_item("meal-1", 0, 100, confirmed=True)
        self.assertEqual(verified["state"], "verified")
        self.assertEqual(self.diary.calls, [("delete", "crono-serving:10")])

    def test_unconfirmed_outcome_never_replays_write(self):
        self.diary.fail_to_send = True
        first = self.changes.correct_amount("meal-1", 0, 100, 120, confirmed=True)
        self.assertTrue(first["retry_blocked"])
        self.diary.fail_to_send = False
        again = DiaryChanges(self.meals).correct_amount("meal-1", 0, 100, 120, confirmed=True)
        self.assertTrue(again["retry_blocked"])
        with self.assertRaisesRegex(ValidationError, "previous change"):
            self.changes.undo_item("meal-1", 0, 100, confirmed=True)
        self.assertEqual(self.diary.calls, [("edit", "crono-serving:10", 120)])


if __name__ == "__main__":
    unittest.main()
