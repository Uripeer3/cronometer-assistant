"""Verified changes to servings created by this assistant."""

import re
from dataclasses import replace
from math import isclose

from .live import MutationNotApplied
from .models import DiaryEntry, ValidationError, number


def _same_amount(left: float, right: float) -> bool:
    return isclose(left, right, rel_tol=0.0, abs_tol=0.000001)


class DiaryChanges:
    def __init__(self, meals):
        self.meals = meals

    def correct_amount(
        self, meal_id: str, item_index: int, expected_grams: float, grams: float, *, confirmed: bool
    ) -> dict:
        return self._change(
            meal_id, item_index, expected_grams, number(grams, "grams", positive=True), confirmed
        )

    def undo_item(
        self, meal_id: str, item_index: int, expected_grams: float, *, confirmed: bool
    ) -> dict:
        return self._change(meal_id, item_index, expected_grams, None, confirmed)

    def _target(self, meal_id: str, item_index: int):
        meal = self.meals._meal(meal_id)
        operation = self.meals.store.get("operations", meal_id)
        if (
            not operation
            or type(item_index) is not int
            or not 0 <= item_index < len(operation["steps"])
        ):
            raise ValidationError("Select an item from a verified, logged meal")
        step = operation["steps"][item_index]
        params, entry_id = step.get("add_parameters"), step.get("entry_id")
        if (
            step["state"] != "verified"
            or not isinstance(entry_id, str)
            or not re.fullmatch(r"crono-serving:[1-9][0-9]*", entry_id)
            or not params
            or params.get("day") != meal["day"]
            or params.get("meal_group") != meal["meal_group"]
            or params.get("food_id") != step["item"]["food_id"]
            or not isinstance(params.get("local_time"), str)
            or type(params.get("measure_id")) is not int
            or type(params.get("translation_id")) is not int
        ):
            raise ValidationError("This log has no verified Cronometer serving to change")
        entry = DiaryEntry(
            entry_id=entry_id,
            food_id=params["food_id"],
            grams=params["grams"],
            day=params["day"],
            meal_group=params["meal_group"],
            local_time=params["local_time"],
            measure_id=params["measure_id"],
            translation_id=params["translation_id"],
        )
        return operation, step, entry

    def _read(self, entry: DiaryEntry) -> DiaryEntry | None:
        return next(
            (e for e in self.meals.diary.entries(entry.day) if e.entry_id == entry.entry_id),
            None,
        )

    @staticmethod
    def _matches(found: DiaryEntry | None, entry: DiaryEntry, grams: float | None) -> bool:
        if grams is None:
            return found is None
        return (
            found is not None
            and found == replace(entry, grams=found.grams)
            and _same_amount(found.grams, grams)
        )

    def _change(self, meal_id, item_index, expected_grams, grams, confirmed):
        if confirmed is not True:
            raise ValidationError("Confirm the specified serving and change first")
        expected_grams = number(expected_grams, "expected_grams", positive=True)
        with self.meals.store.locked():
            operation, step, entry = self._target(meal_id, item_index)
            previous = step.get("diary_change")
            request = {
                "from_grams": expected_grams,
                "to_grams": grams,
            }
            same_request = previous and all(
                previous[key] == value for key, value in request.items()
            )
            response = {
                "action": "undo" if grams is None else "correct_amount",
                "meal_id": meal_id,
                "item_index": item_index,
                "entry_id": entry.entry_id,
                "food_id": entry.food_id,
                "previous_grams": expected_grams,
                "grams": grams,
                "day": entry.day,
                "local_time": entry.local_time,
                "meal_group": entry.meal_group,
            }

            if previous and previous["state"] == "sending":
                if not same_request:
                    raise ValidationError("Verify the previous change before requesting another")
                return self._verify(operation, previous, entry, response, sent_now=False)

            found = self._read(entry)
            if (
                same_request
                and previous["state"] == "verified"
                and self._matches(found, entry, grams)
            ):
                return self.meals.response(**response, state="verified", already_applied=True)

            current_grams = (
                (
                    previous["from_grams"]
                    if previous["state"] == "not_applied"
                    else previous["to_grams"]
                )
                if previous
                else entry.grams
            )
            if current_grams is None:
                raise ValidationError("This serving was already undone")
            if not _same_amount(current_grams, expected_grams):
                raise ValidationError("Review the current logged amount before changing it")
            if not self._matches(found, entry, current_grams):
                raise ValidationError(
                    "The serving changed in Cronometer. Review it in the diary first"
                )
            if grams is not None and _same_amount(current_grams, grams):
                return self.meals.response(**response, state="verified", already_applied=True)

            if previous:
                step.setdefault("change_history", []).append(previous)
            change = {**request, "state": "sending"}
            step["diary_change"] = change
            self.meals.store.put("operations", meal_id, operation)
            try:
                if grams is None:
                    self.meals.diary.delete(found)
                else:
                    self.meals.diary.edit_amount(found, grams)
            except MutationNotApplied:
                change["state"] = "not_applied"
                self.meals.store.put("operations", meal_id, operation)
                return self.meals.response(
                    **response,
                    state="not_applied",
                    retry_safe=True,
                    next_step="No change was applied. Recheck the current serving and confirm before retrying.",
                )
            except Exception:
                # The request may have reached Cronometer. Only readback can settle it.
                pass
            return self._verify(operation, change, entry, response, sent_now=True)

    def _verify(self, operation, change, entry, response, *, sent_now):
        try:
            verified = self._matches(self._read(entry), entry, change["to_grams"])
        except Exception:
            verified = False
        if not verified:
            return self.meals.response(
                **response,
                state="needs_verification",
                real_diary_changed=None,
                retry_blocked=True,
                next_step="Read the serving in Cronometer, then recheck this same change.",
            )
        change["state"] = "verified"
        self.meals.store.put("operations", operation["meal_id"], operation)
        return self.meals.response(
            **response, state="verified", real_diary_changed=sent_now, already_applied=not sent_now
        )
