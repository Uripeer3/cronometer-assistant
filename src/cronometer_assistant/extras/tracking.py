"""Opt-in non-food tracking domains: biometrics, exercise, and fasting."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ..auth import CronometerSessionClient
from ..nutrition import ValidationError, clean_text, finite_number, normalize_key
from ..parsing import parse_day, parse_local_datetime


class TrackingService:
    def __init__(self, client: CronometerSessionClient) -> None:
        self.client = client

    def _resolve_day(self, day_str: str | None) -> date:
        parsed = parse_day(day_str)
        if parsed is not None:
            return parsed
        self.client.sync_timezone()
        return self.client.today()

    # ------------------------------------------------------------------
    # Biometrics
    # ------------------------------------------------------------------

    def list_biometrics(self, *, query: str = "") -> dict[str, Any]:
        q_norm = normalize_key(query) if query.strip() else ""
        metrics = self.client.get_metrics()
        results = []
        for m in metrics:
            name = str(m.get("name") or "")
            if q_norm and q_norm not in normalize_key(name):
                continue
            results.append(
                {
                    "metric_id": m.get("id"),
                    "name": name,
                    "units": [
                        {"unit_id": u.get("id"), "name": u.get("name")} for u in m.get("units", [])
                    ],
                }
            )
        return {"count": len(results), "metrics": results}

    def get_biometrics(
        self,
        metric_id: int,
        unit_id: int,
        *,
        start_day: str | None = None,
        end_day: str | None = None,
    ) -> dict[str, Any]:
        if type(metric_id) is not int or metric_id <= 0:
            raise ValidationError("metric_id must be a positive integer")
        if type(unit_id) is not int or unit_id <= 0:
            raise ValidationError("unit_id must be a positive integer")
        end = self._resolve_day(end_day)
        start = parse_day(start_day) if start_day is not None else (end - timedelta(days=30))
        assert start is not None
        if start > end:
            raise ValidationError("start_date must not be after end_date")
        data = self.client.get_biometrics(metric_id, unit_id, start=start, end=end)
        return {
            "metric_id": metric_id,
            "unit_id": unit_id,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "biometrics": data,
        }

    def add_biometric(
        self, metric_id: int, unit_id: int, amount: float, *, day: str | None = None
    ) -> dict[str, Any]:
        if type(metric_id) is not int or metric_id <= 0:
            raise ValidationError("metric_id must be a positive integer")
        if type(unit_id) is not int or unit_id <= 0:
            raise ValidationError("unit_id must be a positive integer")
        val = finite_number(amount, "amount")
        target_day = self._resolve_day(day)
        res = self.client.add_biometric(metric_id, unit_id, val, target_day)
        return {
            "biometric_id": res.get("id"),
            "metric_id": metric_id,
            "unit_id": unit_id,
            "amount": val,
            "date": target_day.isoformat(),
        }

    def edit_biometric(
        self, biometric_id: int, amount: float, *, day: str | None = None
    ) -> dict[str, Any]:
        if type(biometric_id) is not int or biometric_id <= 0:
            raise ValidationError("biometric_id must be a positive integer")
        val = finite_number(amount, "amount")
        target_day = self._resolve_day(day)
        self.client.edit_biometric(biometric_id, val, target_day)
        return {"biometric_id": biometric_id, "amount": val, "date": target_day.isoformat()}

    def remove_biometrics(
        self, biometric_ids: list[int], *, day: str | None = None
    ) -> dict[str, Any]:
        if not isinstance(biometric_ids, list) or not biometric_ids:
            raise ValidationError("biometric_ids must be a non-empty list")
        target_day = self._resolve_day(day)
        res = self.client.delete_entries(
            [str(i) for i in biometric_ids], target_day, entry_type="Biometric"
        )
        return {
            "date": target_day.isoformat(),
            "removed": res.get("removed", []),
            "count": res.get("count", 0),
            "verified": True,
        }

    # ------------------------------------------------------------------
    # Exercise
    # ------------------------------------------------------------------

    def add_exercise(
        self,
        name: str,
        minutes: int,
        calories_burned: float,
        *,
        day: str | None = None,
    ) -> dict[str, Any]:
        label = clean_text(name, "name", 200)
        if type(minutes) is not int or minutes <= 0:
            raise ValidationError("minutes must be a positive integer")
        kcal = finite_number(calories_burned, "calories_burned", positive=True)
        target_day = self._resolve_day(day)
        res = self.client.add_exercise(label, minutes, kcal, target_day)
        return {
            "exercise_id": res.get("id"),
            "name": label,
            "minutes": minutes,
            "calories_burned": kcal,
            "date": target_day.isoformat(),
        }

    def edit_exercise(
        self,
        exercise_id: int,
        *,
        minutes: int | None = None,
        calories_burned: float | None = None,
        day: str | None = None,
    ) -> dict[str, Any]:
        if type(exercise_id) is not int or exercise_id <= 0:
            raise ValidationError("exercise_id must be a positive integer")
        if minutes is None and calories_burned is None:
            raise ValidationError("Provide minutes and/or calories_burned")
        if minutes is not None and (type(minutes) is not int or minutes <= 0):
            raise ValidationError("minutes must be a positive integer")
        kcal = (
            finite_number(calories_burned, "calories_burned", positive=True)
            if calories_burned is not None
            else None
        )
        target_day = self._resolve_day(day)
        self.client.edit_exercise(
            exercise_id, minutes=minutes, calories_burned=kcal, day=target_day
        )
        return {"exercise_id": exercise_id, "date": target_day.isoformat()}

    def remove_exercises(
        self, exercise_ids: list[int], *, day: str | None = None
    ) -> dict[str, Any]:
        if not isinstance(exercise_ids, list) or not exercise_ids:
            raise ValidationError("exercise_ids must be a non-empty list")
        target_day = self._resolve_day(day)
        res = self.client.delete_entries(
            [str(i) for i in exercise_ids], target_day, entry_type="Exercise"
        )
        return {
            "date": target_day.isoformat(),
            "removed": res.get("removed", []),
            "count": res.get("count", 0),
            "verified": True,
        }

    # ------------------------------------------------------------------
    # Fasting
    # ------------------------------------------------------------------

    def get_fasting_history(
        self, *, start_day: str | None = None, end_day: str | None = None
    ) -> dict[str, Any]:
        end = self._resolve_day(end_day)
        start = parse_day(start_day) if start_day is not None else (end - timedelta(days=30))
        assert start is not None
        if start > end:
            raise ValidationError("start_date must not be after end_date")
        data = self.client.get_fasting_with_date_range(start, end)
        return {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "fasting": data,
        }

    def get_fasting_stats(self) -> dict[str, Any]:
        return {"stats": self.client.get_fasting_stats()}

    def add_fast(
        self, start: str, *, end: str | None = None, goal_hours: float = 16.0
    ) -> dict[str, Any]:
        self.client.sync_timezone()
        tz = self.client.tzinfo()
        started = parse_local_datetime(start, tz)
        ended = parse_local_datetime(end, tz) if end is not None else None
        if ended is not None and ended <= started:
            raise ValidationError("Fast end must be after start")
        goal = finite_number(goal_hours, "goal_hours", positive=True)
        res = self.client.add_fast(started, ended, goal)
        return {"fast_id": res.get("id"), "start": start, "end": end, "goal_hours": goal}

    def edit_fast(
        self,
        fast_id: int,
        *,
        start: str | None = None,
        end: str | None = None,
        goal_hours: float | None = None,
    ) -> dict[str, Any]:
        if type(fast_id) is not int or fast_id <= 0:
            raise ValidationError("fast_id must be a positive integer")
        if start is None and end is None and goal_hours is None:
            raise ValidationError("Provide start, end, or goal_hours")
        self.client.sync_timezone()
        tz = self.client.tzinfo()
        started = parse_local_datetime(start, tz) if start is not None else None
        ended = parse_local_datetime(end, tz) if end is not None else None
        existing_fasts = self.client.get_fasting_with_date_range().get("fasts", [])
        existing = next((f for f in existing_fasts if f.get("id") == fast_id), None)
        if existing is None:
            raise ValidationError(f"No fast with id {fast_id} in the recent range")
        eff_start_ms = (
            int(started.timestamp() * 1000) if started is not None else existing.get("start")
        )
        eff_end_ms = int(ended.timestamp() * 1000) if ended is not None else existing.get("end")
        if eff_start_ms is not None and eff_end_ms is not None and eff_end_ms <= eff_start_ms:
            raise ValidationError("Fast end must be after start")
        goal = (
            finite_number(goal_hours, "goal_hours", positive=True)
            if goal_hours is not None
            else None
        )
        self.client.edit_fast(fast_id, start=started, end=ended, goal_hours=goal)
        return {"fast_id": fast_id}

    def delete_fast(self, fast_id: int) -> dict[str, Any]:
        if type(fast_id) is not int or fast_id <= 0:
            raise ValidationError("fast_id must be a positive integer")
        self.client.delete_fast(fast_id)
        return {"deleted": fast_id}
