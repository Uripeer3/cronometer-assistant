"""Secondary diary utilities: day completion, day copying, food-usage search, and diary notes."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ..auth import CronometerSessionClient
from ..nutrition import ValidationError, clean_text
from ..parsing import is_batch_recipe, parse_day, serving_scale_and_display

MAX_SCAN_DAYS = 90


class DiaryExtrasService:
    def __init__(self, client: CronometerSessionClient) -> None:
        self.client = client

    def _resolve_day(self, day_str: str | None) -> date:
        parsed = parse_day(day_str)
        if parsed is not None:
            return parsed
        self.client.sync_timezone()
        return self.client.today()

    def mark_day_complete(self, day: str | None = None, *, complete: bool = True) -> dict[str, Any]:
        """Mark a diary day as complete or incomplete."""
        if not isinstance(complete, bool):
            raise ValidationError("complete must be a boolean")
        target_day = self._resolve_day(day)
        self.client.mark_day_complete(target_day, complete)
        diary = self.client.get_diary(target_day)
        info = diary.get("info") if isinstance(diary.get("info"), dict) else {}
        recorded = bool(info.get("complete", False))
        return {
            "date": target_day.isoformat(),
            "complete": recorded,
            "verified": recorded == complete,
        }

    def copy_day(self, *, to_day: str | None = None, from_day: str | None = None) -> dict[str, Any]:
        """Copy all diary entries from source day (default: day before to_day) into to_day additively."""
        dest = self._resolve_day(to_day)
        src = parse_day(from_day) if from_day is not None else (dest - timedelta(days=1))
        assert src is not None
        if src == dest:
            raise ValidationError("from_day and to_day must be different dates")
        self.client.copy_day(from_day=src, to_day=dest)
        return {
            "from_date": src.isoformat(),
            "to_date": dest.isoformat(),
            "copied": True,
        }

    def find_entries_by_food(
        self,
        food_id: int,
        *,
        start_day: str | None = None,
        end_day: str | None = None,
    ) -> dict[str, Any]:
        """Find every diary entry referencing `food_id` over a bounded date range (default 30 days)."""
        if type(food_id) is not int or food_id <= 0:
            raise ValidationError("food_id must be a positive integer")
        end = self._resolve_day(end_day)
        start = parse_day(start_day) if start_day is not None else (end - timedelta(days=30))
        assert start is not None
        if start > end:
            raise ValidationError("start_date must not be after end_date")
        if (end - start).days > MAX_SCAN_DAYS:
            raise ValidationError(
                f"Date range exceeds {MAX_SCAN_DAYS} days; narrow start_date/end_date"
            )
        entries = self.client.find_entries_by_food(food_id, start=start, end=end)
        raw_food = self.client.get_food(food_id)
        batch_recipe = is_batch_recipe(raw_food)
        normalized_entries: list[dict[str, Any]] = []
        unknown_weight_count = 0
        for entry in entries:
            row_like = {
                "foodId": food_id,
                "measureId": entry.get("measure_id"),
                "grams": entry.get("grams"),
            }
            _, _, _, actual_grams = serving_scale_and_display(raw_food, row_like)
            item = dict(entry)
            if actual_grams is not None:
                item["grams"] = round(actual_grams, 2)
            elif batch_recipe:
                item["grams"] = None
                unknown_weight_count += 1
            normalized_entries.append(item)
        known_grams = [
            float(e["grams"])
            for e in normalized_entries
            if isinstance(e.get("grams"), (int, float))
        ]
        result: dict[str, Any] = {
            "food_id": food_id,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "count": len(normalized_entries),
            "total_grams": round(sum(known_grams), 2)
            if known_grams or unknown_weight_count == 0
            else None,
            "entries": normalized_entries,
        }
        if unknown_weight_count:
            result["unknown_weight_entries"] = unknown_weight_count
        return result

    def add_note(self, text: str, *, day: str | None = None) -> dict[str, Any]:
        """Add a diary note to a day (Cronometer supports editing notes, not deleting them)."""
        body = clean_text(text, "text", 2000)
        target_day = self._resolve_day(day)
        result = self.client.add_note(body, target_day)
        return {
            "note_id": result.get("id"),
            "date": target_day.isoformat(),
            "text": body,
        }

    def edit_note(self, note_id: int, text: str, *, day: str | None = None) -> dict[str, Any]:
        """Replace the text of an existing diary note."""
        if type(note_id) is not int or note_id <= 0:
            raise ValidationError("note_id must be a positive integer")
        body = clean_text(text, "text", 2000)
        target_day = self._resolve_day(day)
        self.client.edit_note(note_id, body, target_day)
        return {
            "note_id": note_id,
            "date": target_day.isoformat(),
            "text": body,
        }
