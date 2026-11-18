"""Diary adapter for weighed servings of verified, non-recipe foods."""

import re
from datetime import date, time
from math import isclose, isfinite

from .live import LiveError, LoginRequired
from .models import DiaryEntry, ValidationError, number

MEAL_GROUP_IDS = {"breakfast": 1, "lunch": 2, "dinner": 3, "snack": 4}
GROUP_NAMES = {value: key for key, value in MEAL_GROUP_IDS.items()}


def _read_day(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", value):
        raise ValueError("Invalid diary date")
    return date(*map(int, value.split("-"))).isoformat()


def local_time(value):
    """Validate a reported wall time and preserve it without a timezone conversion."""
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{2}:[0-9]{2}(?::[0-9]{2})?", value):
        raise ValidationError("Report the meal time using HH:MM (24-hour local time)")
    try:
        return time.fromisoformat(value).isoformat(timespec="seconds")
    except ValueError:
        raise ValidationError("Report a valid local meal time") from None


def _entry_id(value):
    if type(value) is not int or value <= 0:
        raise ValueError("Invalid serving ID")
    return f"crono-serving:{value}"


def serving_measure(food):
    """Use a real gram measure, or a verified weight-based serving measure."""
    measures = food.get("measures")
    if not isinstance(measures, list):
        raise ValidationError("This food has no verified weight measure")
    weights = [
        row
        for row in measures
        if isinstance(row, dict)
        and type(row.get("id")) is int
        and row["id"] > 0
        and row.get("type") in {"Weight", "Atomic"}
        and type(row.get("value")) in {int, float}
        and type(row.get("amount")) in {int, float}
        and isfinite(row["value"])
        and isfinite(row["amount"])
        and row["value"] > 0
        and row["amount"] > 0
    ]
    for row in weights:
        if str(row.get("name", "")).casefold() in {"g", "gram", "grams"} and isclose(
            row["value"] / row["amount"], 1.0
        ):
            return row["id"]
    for row in weights:
        if row["id"] == food.get("defaultMeasureId") and row["type"] == "Weight":
            return row["id"]
    raise ValidationError("Choose a food with a verified gram or weight serving measure")


class LiveDiary:
    mode = "cronometer_live"

    def __init__(self, client):
        self.client = client

    @staticmethod
    def _call(function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except LoginRequired:
            raise
        except Exception:
            raise LiveError("Cronometer diary request failed") from None

    def prepare(self, item, meal, selection):
        numeric_id = int(item["food_id"].removeprefix("crono:"))
        food = self._call(self.client.get_food, numeric_id)
        if (
            food.get("id") != numeric_id
            or food.get("meal") is not False
            or food.get("retired") is True
        ):
            raise ValidationError("Select a regular Cronometer food with a confirmed food ID")
        return {
            "food_id": item["food_id"],
            "grams": number(item["grams"], "grams", positive=True),
            "day": meal["day"],
            "meal_group": meal["meal_group"],
            "local_time": local_time(meal.get("local_time")),
            "measure_id": serving_measure(food),
            "translation_id": selection["translation_id"],
        }

    def _servings(self, day):
        response = self._call(self.client.get_diary, date.fromisoformat(day))
        try:
            rows = response["diary"]
            if not isinstance(rows, list) or _read_day(response["info"]["day"]) != day:
                raise ValueError
            entries = []
            seen = set()
            for row in rows:
                if row["type"] != "Serving":
                    continue
                identity = _entry_id(row["servingId"])
                if identity in seen or type(row["foodId"]) is not int or row["foodId"] <= 0:
                    raise ValueError
                seen.add(identity)
                recorded_day = _read_day(row["day"])
                if recorded_day != day:
                    raise ValueError
                stamp = row.get("time")
                stamp = time(*map(int, stamp.split(":"))).isoformat() if stamp else None
                measure_id = row.get("measureId")
                translation_id = row.get("translationId")
                if measure_id is not None and (type(measure_id) is not int or measure_id < 0):
                    raise ValueError
                if translation_id is not None and (
                    type(translation_id) is not int or translation_id < 0
                ):
                    raise ValueError
                entry = DiaryEntry(
                    identity,
                    f"crono:{row['foodId']}",
                    number(row["grams"], "diary grams"),
                    recorded_day,
                    GROUP_NAMES.get(row["order"] >> 16, "uncategorized"),
                    stamp,
                    measure_id,
                    translation_id if translation_id is not None else 0,
                )
                entries.append((entry, row))
            return entries
        except (AttributeError, KeyError, TypeError, ValueError):
            raise LiveError(
                "Cronometer returned an incomplete diary; no result can be verified"
            ) from None

    def entries(self, day):
        return [entry for entry, _ in self._servings(day)]

    def add(self, *, food_id, grams, day, meal_group, local_time, measure_id, translation_id):
        result = self._call(
            self.client.add_serving,
            int(food_id.removeprefix("crono:")),
            measure_id,
            grams,
            translation_id=translation_id,
            day=date.fromisoformat(day),
            diary_group=MEAL_GROUP_IDS[meal_group],
            time=time.fromisoformat(local_time),
        )
        try:
            identity = _entry_id(result["id"])
        except (KeyError, TypeError, ValueError):
            raise LiveError("Cronometer did not return a serving ID") from None
        return DiaryEntry(
            identity, food_id, grams, day, meal_group, local_time, measure_id, translation_id
        )

    def edit_amount(self, entry, grams):
        row = self._validate_target(entry)
        self._call(
            self.client._request,
            "/api/v2/edit_serving",
            {"serving": {**row, "grams": number(grams, "grams", positive=True)}},
        )

    def delete(self, entry):
        row = self._validate_target(entry)
        payload = {key: value for key, value in row.items() if key != "meta"}
        payload["id"] = row["servingId"]
        result = self._call(
            self.client._request_v3,
            "DELETE",
            "/diary-entries",
            json_body={"diaryEntries": [payload]},
        )
        if result.status_code != 204:
            raise LiveError("Cronometer did not acknowledge the serving removal")

    def _validate_target(self, target):
        for entry, row in self._servings(target.day):
            if entry == target:
                return row
        raise LiveError("The serving changed before the request was sent")
