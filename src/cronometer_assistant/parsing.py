"""Shared date, time, meal-group, and Cronometer serving-scaling helpers."""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from datetime import time as dtime
from typing import Any
from zoneinfo import ZoneInfo

from .nutrition import KEY_MACRO_IDS, ValidationError

MEAL_GROUP_TO_ID: dict[str, int] = {
    "uncategorized": 0,
    "breakfast": 1,
    "lunch": 2,
    "dinner": 3,
    "snack": 4,
    "snacks": 4,
}
ID_TO_MEAL_GROUP: dict[int, str] = {
    0: "uncategorized",
    1: "breakfast",
    2: "lunch",
    3: "dinner",
    4: "snacks",
}
SUMMARY_MACRO_IDS: dict[str, int] = {
    "energy": 208,
    "protein": 203,
    "carbs": 205,
    "net_carbs": -1205,
    "fat": 204,
    "fiber": 291,
    "alcohol": 221,
}


def round_sig(value: float, digits: int = 6) -> float:
    if not value or not math.isfinite(value):
        return value
    return round(value, -math.floor(math.log10(abs(value))) + (digits - 1))


def parse_day(value: str | None) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValidationError("date must use YYYY-MM-DD format")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValidationError(f"Invalid calendar date: {value!r}") from None


def parse_time(value: str | None) -> dtime | None:
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\d{2}:\d{2}(?::\d{2})?", value.strip()):
        raise ValidationError("time must use HH:MM or HH:MM:SS (24-hour local time)")
    try:
        return dtime.fromisoformat(value.strip())
    except ValueError:
        raise ValidationError(f"Invalid wall time: {value!r}") from None


def parse_local_datetime(value: str, tz: ZoneInfo) -> datetime:
    if not isinstance(value, str):
        raise ValidationError("timestamp must use YYYY-MM-DD HH:MM format")
    cleaned = value.strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(cleaned, fmt).replace(tzinfo=tz)
        except ValueError:
            continue
    raise ValidationError(f"Invalid timestamp {value!r}; use YYYY-MM-DD HH:MM")


def parse_meal_group(value: str | int | None, *, allow_auto: bool = True) -> int | None:
    """Parse meal group into Cronometer's group int (0=uncategorized, 1..8=groups), or None for auto."""
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        if 0 <= value <= 8:
            return value
        raise ValidationError("meal_group integer must be between 0 and 8")
    if not isinstance(value, str):
        raise ValidationError("meal_group must be a string")
    key = value.strip().casefold()
    if key == "auto":
        if not allow_auto:
            raise ValidationError(
                "Specify an explicit meal_group (breakfast, lunch, dinner, snacks, uncategorized, or 0..8)"
            )
        return None
    if key in MEAL_GROUP_TO_ID:
        return MEAL_GROUP_TO_ID[key]
    if key.isdigit() and 0 <= int(key) <= 8:
        return int(key)
    raise ValidationError(
        f"Unknown meal_group {value!r}. Use auto, breakfast, lunch, dinner, snacks, uncategorized, or 0..8."
    )


def format_api_time(t: dtime) -> str:
    return f"{t.hour}:{t.minute}:{t.second}"


def meal_group_for_hour(hour: int) -> int:
    """Match CronometerClient's auto meal-group mapping (1=Breakfast, 2=Lunch, 3=Dinner, 4=Snacks)."""
    if 4 <= hour < 10:
        return 1
    if 10 <= hour < 14:
        return 2
    if 14 <= hour < 21:
        return 3
    return 4


def native_recipe_total_grams(raw_food: dict[str, Any]) -> float | None:
    """Total batch weight in grams for a native (`meal=True`) recipe, or None for normal foods."""
    if not raw_food.get("meal"):
        return None
    total = sum(
        float(i.get("grams") or 0) for i in raw_food.get("ingredients", []) if isinstance(i, dict)
    )
    if total > 0:
        return total
    for m in raw_food.get("measures", []):
        if isinstance(m, dict) and m.get("type") == "Recipe" and m.get("name") == "g":
            v = m.get("value")
            if isinstance(v, (int, float)) and math.isfinite(v) and v > 0:
                return float(v)
    return None


def per_100g_nutrient_factor(raw_food: dict[str, Any]) -> float | None:
    """Multiplier converting raw_food['nutrients'] amounts to per-100g values.

    Normal foods and weight-based recipes already store nutrients per 100 g (factor = 1.0).
    Native recipes (`meal=True`) store nutrients per full batch (factor = 100 / batch_grams).
    """
    if not raw_food.get("meal"):
        return 1.0
    total = native_recipe_total_grams(raw_food)
    if total is None or total <= 0:
        return None
    return 100.0 / total


def extract_key_macros(raw_food: dict[str, Any], scale: float | None = None) -> dict[str, float]:
    """Extract key macros scaled by `scale` (defaults to per-100g normalization)."""
    if scale is None:
        scale = per_100g_nutrient_factor(raw_food)
    if scale is None:
        return {}
    out: dict[str, float] = {}
    for row in raw_food.get("nutrients", []):
        if not isinstance(row, dict):
            continue
        nid = row.get("id")
        amt = row.get("amount")
        if nid in KEY_MACRO_IDS and isinstance(amt, (int, float)) and math.isfinite(amt):
            key, _ = KEY_MACRO_IDS[nid]
            out[key] = round(float(amt) * scale, 2)
    return out


def is_recipe_food(raw_food: dict[str, Any]) -> bool:
    if raw_food.get("meal"):
        return True
    props = raw_food.get("properties")
    return isinstance(props, dict) and str(props.get("advancedServingSize", "")).lower() == "true"


def serving_scale_and_display(
    raw_food: dict[str, Any], entry: dict[str, Any]
) -> tuple[float | None, dict[str, Any] | None, float | None, float | None]:
    """Compute (nutrient_scale, measure_info, display_servings, actual_grams) for a diary Serving."""
    measures_by_id = {
        m.get("id"): m for m in raw_food.get("measures", []) if isinstance(m, dict) and m.get("id")
    }
    measure = measures_by_id.get(entry.get("measureId")) or measures_by_id.get(
        raw_food.get("defaultMeasureId")
    )
    grams = entry.get("grams")
    measure_info: dict[str, Any] | None = None
    servings: float | None = None
    actual_grams: float | None = float(grams) if isinstance(grams, (int, float)) else None

    if measure:
        val = measure.get("value")
        amt = measure.get("amount") or 1.0
        gpu: float | None = None
        if (
            measure.get("type") in {"Weight", "Atomic"}
            and isinstance(val, (int, float))
            and isinstance(amt, (int, float))
            and val > 0
            and amt > 0
        ):
            gpu = float(val) / float(amt)
        measure_info = {
            "measure_id": measure.get("id"),
            "name": measure.get("name"),
            "type": measure.get("type"),
            "grams_per_unit": round(gpu, 6) if gpu is not None else None,
        }
        if isinstance(grams, (int, float)) and gpu and gpu > 0:
            servings = round(float(grams) / gpu, 4)

    if raw_food.get("meal") and isinstance(grams, (int, float)):
        total_grams = native_recipe_total_grams(raw_food)
        if total_grams and total_grams > 0:
            if (
                measure
                and measure.get("type") == "Recipe"
                and isinstance(measure.get("value"), (int, float))
                and measure["value"] > 0
            ):
                fraction = float(grams) / float(measure["value"])
            elif measure and measure.get("type") != "Recipe":
                fraction = float(grams) / 100.0
            else:
                fraction = float(grams)
            actual_grams = round(fraction * total_grams, 2)
            return fraction, measure_info, None, actual_grams

    scale = (float(grams) / 100.0) if isinstance(grams, (int, float)) else None
    return scale, measure_info, servings, actual_grams


def extract_energy_summary(diary: dict[str, Any]) -> dict[str, Any] | None:
    summary = diary.get("summary")
    if not isinstance(summary, dict):
        return None
    macros = summary.get("macros") or {}
    consumed = summary.get("consumed") or {}
    burned = summary.get("burned") or {}
    target_kcal = macros.get("energy")
    consumed_kcal = consumed.get("total")
    out: dict[str, Any] = {}
    if target_kcal is not None and consumed_kcal is not None:
        out["total_target_kcal"] = round(float(target_kcal), 1)
        out["consumed_kcal"] = round(float(consumed_kcal), 1)
        out["remaining_kcal"] = round(float(target_kcal) - float(consumed_kcal), 1)
    if burned.get("total") is not None:
        out["burned_kcal"] = round(float(burned["total"]), 1)
    macro_targets = {
        k: round(float(macros[k]), 1)
        for k in ("protein", "carbs", "net_carbs", "fat")
        if macros.get(k) is not None
    }
    if macro_targets:
        out["macro_targets_g"] = macro_targets
    return out or None
