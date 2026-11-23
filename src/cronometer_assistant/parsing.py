"""Shared date, time, meal-group, and Cronometer serving-scaling helpers."""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from datetime import time as dtime
from typing import Any
from zoneinfo import ZoneInfo

from .nutrition import KEY_MACRO_IDS, ValidationError

_FOOTER_MARKER = "\n\nRaw ingredients ("
_FOOTER_PREFIX = "Raw ingredients ("
_NUM_TOKEN = r"\d+(?:\.\d+)?(?:[eE][+-]?\d+)?"
_FOOTER_ING_RE = re.compile(rf"(?<![\w.-])({_NUM_TOKEN})\s*g\s+of\s+food\s+(\d+)\b")

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


def is_batch_recipe(raw_food: dict[str, Any]) -> bool:
    """True when Cronometer stores this recipe's nutrients per full batch (not per 100 g).

    Covers both `meal=True` recipes and Cronometer-app servings-based recipes whose
    measures use `type == "Recipe"` (even when `meal` is False).
    """
    if raw_food.get("meal"):
        return True
    return any(
        isinstance(m, dict) and m.get("type") == "Recipe" for m in raw_food.get("measures", [])
    )


def native_recipe_total_grams(raw_food: dict[str, Any]) -> float | None:
    """Total batch weight in grams for a batch-stored (`Recipe`-measure / `meal=True`) recipe."""
    if not is_batch_recipe(raw_food):
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
    Batch-stored recipes (`meal=True` or `Recipe`-typed measures) store nutrients per full
    batch (factor = 100 / batch_grams).
    """
    if not is_batch_recipe(raw_food):
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
    if is_batch_recipe(raw_food):
        return True
    if raw_food.get("ingredients"):
        return True
    props = raw_food.get("properties")
    return isinstance(props, dict) and str(props.get("advancedServingSize", "")).lower() == "true"


def extract_weight_recipe_footer(comments: str | None) -> str | None:
    """Return only the auto-generated Raw ingredients footer, excluding any user notes."""
    if not comments:
        return None
    text = str(comments)
    idx = text.rfind(_FOOTER_MARKER)
    if idx != -1:
        return text[idx + 2 :]
    if text.startswith(_FOOTER_PREFIX):
        return text
    return None


def parse_weight_recipe_ingredient_footer(comments: str | None) -> list[tuple[int, float]]:
    """Parse `(food_id, grams)` pairs strictly from the generated Raw ingredients footer."""
    footer = extract_weight_recipe_footer(comments)
    if not footer:
        return []
    pairs: list[tuple[int, float]] = []
    for grams_s, fid_s in _FOOTER_ING_RE.findall(footer):
        try:
            pairs.append((int(fid_s), float(grams_s)))
        except ValueError:
            continue
    return pairs


def extract_weight_recipe_user_notes(comments: str | None) -> str | None:
    """Return user notes above the generated Raw ingredients footer, if any."""
    if not comments:
        return None
    text = str(comments)
    idx = text.rfind(_FOOTER_MARKER)
    if idx != -1:
        return text[:idx].strip() or None
    if text.startswith(_FOOTER_PREFIX):
        return None
    return text


def batch_recipe_wire_units(
    raw_food: dict[str, Any], measure_id: int | None = None
) -> tuple[int, float] | None:
    """Return `(wire_measure_id, grams_per_wire_unit)` for any batch-stored recipe, or None.

    Uses the same batch classification (`is_batch_recipe`) as diary read-back so both
    `meal=True` and app-created `meal=False` `Recipe`-measure recipes convert identically.
    """
    if not is_batch_recipe(raw_food):
        return None
    total = native_recipe_total_grams(raw_food)
    if not total or total <= 0:
        raise ValidationError(
            f"Recipe {raw_food.get('id')} has no known batch weight, so gram conversion is unavailable"
        )
    measures = [m for m in raw_food.get("measures", []) if isinstance(m, dict)]
    chosen = next(
        (
            m
            for m in measures
            if m.get("type") == "Recipe"
            and str(m.get("name", "")).casefold() == "full recipe"
            and isinstance(m.get("value"), (int, float))
            and m["value"] > 0
        ),
        None,
    )
    if chosen is None and measure_id is not None:
        chosen = next(
            (
                m
                for m in measures
                if m.get("id") == measure_id
                and m.get("type") == "Recipe"
                and isinstance(m.get("value"), (int, float))
                and m["value"] > 0
            ),
            None,
        )
    if chosen is None:
        chosen = next(
            (
                m
                for m in measures
                if m.get("type") == "Recipe"
                and isinstance(m.get("value"), (int, float))
                and m["value"] > 0
            ),
            None,
        )
    if chosen is None:
        raise ValidationError(f"Recipe {raw_food.get('id')} has no usable Recipe measure")
    return int(chosen.get("id") or 0), total / float(chosen["value"])


def recipe_servings(raw_food: dict[str, Any], total_grams: float) -> float:
    """Return a recipe's serving count, matching both 'serving' and Cronometer app 'Serving'."""
    serving_m = next(
        (
            m
            for m in raw_food.get("measures", [])
            if isinstance(m, dict) and str(m.get("name", "")).casefold() == "serving"
        ),
        None,
    )
    if serving_m is None:
        return 1.0
    val = serving_m.get("value")
    if not isinstance(val, (int, float)) or not math.isfinite(val) or val <= 0:
        return 1.0
    if serving_m.get("type") == "Recipe":
        return float(val)
    amt = serving_m.get("amount") or 1.0
    if not isinstance(amt, (int, float)) or not math.isfinite(amt) or amt <= 0:
        amt = 1.0
    grams_per_serving = float(val) / float(amt)
    return round(total_grams / grams_per_serving, 4) if total_grams > 0 else 1.0


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

    if is_batch_recipe(raw_food):
        if not isinstance(grams, (int, float)):
            return None, measure_info, None, None
        total_grams = native_recipe_total_grams(raw_food)
        if (
            measure
            and measure.get("type") == "Recipe"
            and isinstance(measure.get("value"), (int, float))
            and measure["value"] > 0
        ):
            fraction: float = float(grams) / float(measure["value"])
        elif measure and measure.get("type") != "Recipe":
            # Legacy Atomic / non-Recipe batch rows scale by grams / 100.0 in Cronometer.
            fraction = float(grams) / 100.0
        else:
            fraction = float(grams)
        actual_grams = round(fraction * total_grams, 2) if total_grams and total_grams > 0 else None
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
