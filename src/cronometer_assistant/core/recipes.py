"""Core recipe creation and editing (weight-based with cooked yield + servings-based)."""

from __future__ import annotations

import math
import re
import threading
from copy import deepcopy
from typing import Any

from cronometer_mcp.client import COMPUTED_NUTRIENT_IDS, CronometerError

from ..auth import CronometerSessionClient
from ..nutrition import (
    ValidationError,
    clean_text,
    extract_measures,
    finite_number,
    resolve_serving_amount,
)
from ..parsing import (
    extract_key_macros,
    is_batch_recipe,
    is_recipe_food,
    per_100g_nutrient_factor,
)
from ..state import AssistantState

_RAW_SUMMARY_RE = re.compile(r"Raw ingredients \(([\d.]+) g\):")


class RecipeService:
    def __init__(
        self, client: CronometerSessionClient, state: AssistantState | None = None
    ) -> None:
        self.client = client
        self.state = state
        self._write_lock = threading.RLock()

    def _op_lock(self):
        return self.state.operation_lock() if self.state is not None else self._write_lock

    @staticmethod
    def _existing_weight_recipe_cooked_grams(existing_food: dict[str, Any]) -> float | None:
        """Detect if an existing weight recipe has a persisted cooked weight distinct from raw."""
        props = existing_food.get("properties") or {}
        if isinstance(props, dict) and props.get("cooked_weight") is not None:
            try:
                cw = float(props["cooked_weight"])
                if math.isfinite(cw) and cw > 0:
                    return cw
            except (TypeError, ValueError):
                pass
        full_m = next(
            (
                m
                for m in existing_food.get("measures", [])
                if isinstance(m, dict) and str(m.get("name", "")).casefold() == "full recipe"
            ),
            None,
        )
        if not full_m:
            return None
        val = full_m.get("value")
        if not isinstance(val, (int, float)) or not math.isfinite(val) or val <= 0:
            return None
        full_grams = float(val)
        comments = str(existing_food.get("comments") or "")
        if "Cooked weight " in comments:
            return full_grams
        match = _RAW_SUMMARY_RE.search(comments)
        if match:
            try:
                prev_raw = float(match.group(1))
            except ValueError:
                return None
            if not math.isclose(full_grams, prev_raw, rel_tol=1e-3, abs_tol=0.1):
                return full_grams
        return None

    @staticmethod
    def _extract_user_notes(comments: str | None) -> str | None:
        """Preserve any user notes prefix above the auto-generated Raw ingredients block."""
        if not comments:
            return None
        marker = "\n\nRaw ingredients ("
        if marker in comments:
            prefix = comments.split(marker, 1)[0].strip()
            return prefix or None
        if comments.startswith("Raw ingredients ("):
            return None
        return comments

    @staticmethod
    def _sum_weight_recipe_nutrients(
        rows: list[dict[str, Any]],
        foods_by_id: dict[int, dict[str, Any]],
        final_grams: float,
    ) -> list[dict[str, Any]]:
        """Sum ingredient nutrients accurately for both normal foods and nested `meal=True` recipes."""
        if final_grams <= 0:
            raise ValidationError("Recipe weight must be positive")
        totals: dict[int, float] = {}
        for ing in rows:
            fid = int(ing["foodId"])
            grams = float(ing["grams"])
            raw_food = foods_by_id[fid]
            factor = per_100g_nutrient_factor(raw_food)
            if factor is None:
                raise ValidationError(
                    f"Cannot compute per-100g nutrients for nested recipe {raw_food.get('name')!r} "
                    f"(food_id={fid}): batch weight is unknown"
                )
            for n in raw_food.get("nutrients", []):
                if not isinstance(n, dict):
                    continue
                nid = n.get("id")
                amt = n.get("amount")
                if (
                    not isinstance(nid, int)
                    or nid <= 0
                    or nid in COMPUTED_NUTRIENT_IDS
                    or not isinstance(amt, (int, float))
                    or not math.isfinite(amt)
                ):
                    continue
                totals[nid] = totals.get(nid, 0.0) + float(amt) * factor * grams / 100.0
        return [
            {"id": nid, "amount": round(total / final_grams * 100.0, 6)}
            for nid, total in sorted(totals.items())
        ]

    def _save_weight_recipe(
        self,
        *,
        name: str,
        rows: list[dict[str, Any]],
        foods_by_id: dict[int, dict[str, Any]],
        total_grams: float,
        servings: float,
        notes: str | None,
        cooked_grams: float | None,
        existing_food: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Create or update a weight-based recipe, handling nested native recipes and measure IDs."""
        recipe_id = int(existing_food["id"]) if existing_food is not None else 0
        final_grams = float(cooked_grams or total_grams)
        nutrients = self._sum_weight_recipe_nutrients(rows, foods_by_id, final_grams)
        if not nutrients:
            raise CronometerError("Ingredients carry no nutrient data")

        effective_notes = (
            notes
            if notes is not None
            else (
                self._extract_user_notes(existing_food.get("comments"))
                if existing_food is not None
                else None
            )
        )
        lines = [f"{r['grams']:g} g of food {r['foodId']}" for r in rows]
        summary = f"Raw ingredients ({total_grams:g} g): " + "; ".join(lines)
        if cooked_grams is not None:
            summary += f". Cooked weight {final_grams:g} g."
        comments = f"{effective_notes}\n\n{summary}" if effective_notes else summary

        existing_measures = [
            m for m in (existing_food or {}).get("measures", []) if isinstance(m, dict)
        ]
        existing_by_name = {
            str(m.get("name", "")).casefold(): m for m in existing_measures if m.get("id")
        }

        def build_measure(m_name: str, value: float) -> dict[str, Any]:
            prev = existing_by_name.get(m_name.casefold())
            return {
                "id": int(prev["id"]) if prev is not None else 0,
                "name": prev["name"] if prev is not None else m_name,
                "value": value,
                "amount": 1.0,
                "type": "Weight",
            }

        measures: list[dict[str, Any]] = [
            build_measure("g", 1.0),
            build_measure("full recipe", final_grams),
        ]
        has_existing_serving = "serving" in existing_by_name
        if not math.isclose(servings, 1.0) or has_existing_serving:
            measures.append(build_measure("serving", round(final_grams / servings, 2)))

        included_names = {str(m["name"]).casefold() for m in measures}
        for prev in existing_measures:
            pname = str(prev.get("name", "")).casefold()
            if prev.get("id") and pname not in included_names:
                measures.append(deepcopy(prev))
                included_names.add(pname)

        food_data: dict[str, Any] = {
            "id": recipe_id,
            "name": name,
            "category": (existing_food or {}).get("category", 12),
            "owner": existing_food.get("owner") if existing_food is not None else None,
            "retired": existing_food.get("retired") if existing_food is not None else None,
            "source": existing_food.get("source") if existing_food is not None else None,
            "defaultMeasureId": (existing_food or {}).get("defaultMeasureId", 0),
            "comments": comments,
            "alternateId": (existing_food or {}).get("alternateId"),
            "measures": measures,
            "labelType": (existing_food or {}).get("labelType") or "AMERICAN_2016",
            "nutrients": nutrients,
            "properties": {
                **((existing_food or {}).get("properties") or {}),
                "advancedServingSize": "true",
            },
            "foodTags": list((existing_food or {}).get("foodTags") or []),
            "meal": False,
        }
        data = self.client._save_food(food_data, keep_measures=bool(recipe_id))
        food_id = data.get("id")
        if type(food_id) is not int or food_id <= 0:
            raise CronometerError(f"Failed to save weight recipe: {data}")
        return {
            "food_id": food_id,
            "serving_type": "weight",
            "total_grams": total_grams,
            "final_grams": final_grams,
            "grams_per_serving": round(final_grams / servings, 2),
        }

    def _update_servings_recipe(
        self,
        *,
        existing_food: dict[str, Any],
        name: str,
        rows: list[dict[str, Any]],
        total_grams: float,
        servings: float,
        notes: str | None,
    ) -> dict[str, Any]:
        """Update a servings-based (`meal=True`) recipe while preserving all existing measure IDs."""
        updated = deepcopy(existing_food)
        updated["name"] = name
        updated["meal"] = bool(existing_food.get("meal"))
        updated["ingredients"] = rows
        updated["nutrients"] = []
        if notes is not None:
            updated["comments"] = notes

        measures = [deepcopy(m) for m in updated.get("measures", []) if isinstance(m, dict)]
        found_serving = False
        found_g = False
        for m in measures:
            if m.get("type") == "Recipe" and str(m.get("name", "")).casefold() == "serving":
                m["value"] = float(servings)
                m["amount"] = 1.0
                found_serving = True
            elif m.get("type") == "Recipe" and str(m.get("name", "")).casefold() == "g":
                m["value"] = float(total_grams)
                m["amount"] = 1.0
                found_g = True
        if not found_serving:
            measures.append(
                {
                    "id": 0,
                    "name": "serving",
                    "value": float(servings),
                    "amount": 1.0,
                    "type": "Recipe",
                }
            )
        if not found_g:
            measures.append(
                {
                    "id": 0,
                    "name": "g",
                    "value": float(total_grams),
                    "amount": 1.0,
                    "type": "Recipe",
                }
            )
        updated["measures"] = measures
        data = self.client._save_food(updated, keep_measures=True)
        food_id = data.get("id") or existing_food.get("id")
        if type(food_id) is not int or food_id <= 0:
            raise CronometerError(f"Failed to update recipe: {data}")
        return {
            "food_id": food_id,
            "serving_type": "servings",
            "total_grams": total_grams,
            "grams_per_serving": round(total_grams / servings, 2),
        }

    def save_recipe(
        self,
        name: str,
        ingredients: list[dict[str, Any]],
        *,
        servings: float | None = None,
        cooked_grams: float | None = None,
        serving_type: str = "weight",
        recipe_id: int | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Create or update a recipe, resolving ingredient quantities/measures to exact grams."""
        recipe_name = clean_text(name, "name", 300)
        srv_count = (
            finite_number(servings, "servings", positive=True) if servings is not None else None
        )
        if cooked_grams is not None:
            cooked_grams = finite_number(cooked_grams, "cooked_grams", positive=True)
        if serving_type not in {"weight", "servings"}:
            raise ValidationError("serving_type must be 'weight' or 'servings'")
        if not isinstance(ingredients, list) or not ingredients:
            raise ValidationError("ingredients must be a non-empty list")

        ing_fids = []
        for idx, ing in enumerate(ingredients):
            if (
                not isinstance(ing, dict)
                or type(ing.get("food_id")) is not int
                or ing["food_id"] <= 0
            ):
                raise ValidationError(f"ingredients[{idx}].food_id must be a positive integer")
            ing_fids.append(ing["food_id"])
        foods_by_id = {
            f["id"]: f
            for f in self.client.get_foods(sorted(set(ing_fids)))
            if isinstance(f, dict) and f.get("id")
        }

        normalized_ings: list[dict[str, Any]] = []
        for idx, ing in enumerate(ingredients):
            fid = ing["food_id"]
            raw_food = foods_by_id.get(fid)
            if raw_food is None:
                raise ValidationError(f"ingredients[{idx}]: food_id {fid} not found")
            resolved = resolve_serving_amount(
                raw_food,
                grams=ing.get("grams"),
                quantity=ing.get("quantity"),
                measure_id=ing.get("measure_id"),
                measure_name=ing.get("measure_name"),
            )
            normalized_ings.append(
                {
                    "food_id": fid,
                    "grams": resolved["grams"],
                    "measure_id": resolved["measure_id"],
                }
            )

        rows, total_grams = self.client._normalise_ingredients(normalized_ings)

        with self._op_lock():
            effective_cooked_grams = cooked_grams
            if recipe_id is not None:
                if type(recipe_id) is not int or recipe_id <= 0:
                    raise ValidationError("recipe_id must be a positive integer")
                existing_food = self.client.get_food(recipe_id)
                if existing_food.get("owner") != self.client.user_id:
                    raise ValidationError(f"Recipe {recipe_id} is not yours to edit")
                if not is_recipe_food(existing_food):
                    raise ValidationError(
                        f"Food {recipe_id} is not a recipe; use update_custom_food instead"
                    )
                is_weight = not is_batch_recipe(existing_food)
                if is_weight:
                    if effective_cooked_grams is None:
                        effective_cooked_grams = self._existing_weight_recipe_cooked_grams(
                            existing_food
                        )
                    existing_final = float(effective_cooked_grams or total_grams)
                    existing_servings = self.client._recipe_servings(existing_food, existing_final)
                    effective_servings = srv_count if srv_count is not None else existing_servings
                    result = self._save_weight_recipe(
                        name=recipe_name,
                        rows=rows,
                        foods_by_id=foods_by_id,
                        total_grams=total_grams,
                        servings=effective_servings,
                        notes=notes,
                        cooked_grams=effective_cooked_grams,
                        existing_food=existing_food,
                    )
                else:
                    if cooked_grams is not None:
                        raise ValidationError(
                            "cooked_grams applies to weight-based recipes only; this recipe is servings-based"
                        )
                    existing_servings = self.client._recipe_servings(existing_food, total_grams)
                    effective_servings = srv_count if srv_count is not None else existing_servings
                    result = self._update_servings_recipe(
                        existing_food=existing_food,
                        name=recipe_name,
                        rows=rows,
                        total_grams=total_grams,
                        servings=effective_servings,
                        notes=notes,
                    )
            else:
                effective_servings = srv_count if srv_count is not None else 1.0
                if serving_type == "weight":
                    result = self._save_weight_recipe(
                        name=recipe_name,
                        rows=rows,
                        foods_by_id=foods_by_id,
                        total_grams=total_grams,
                        servings=effective_servings,
                        notes=notes,
                        cooked_grams=effective_cooked_grams,
                        existing_food=None,
                    )
                else:
                    if cooked_grams is not None:
                        raise ValidationError(
                            "cooked_grams applies to weight-based recipes only; use serving_type='weight'"
                        )
                    result = self.client.create_recipe(
                        recipe_name,
                        normalized_ings,
                        servings=effective_servings,
                        notes=notes,
                        serving_type="servings",
                    )

            fresh = self.client.get_food(result["food_id"])
            final_total = float(effective_cooked_grams or total_grams)
            observed_servings = self.client._recipe_servings(fresh, final_total)
            mismatches: list[str] = []
            if not math.isclose(observed_servings, effective_servings, rel_tol=1e-2, abs_tol=0.05):
                mismatches.append(
                    f"servings: expected {effective_servings}, observed {observed_servings}"
                )
            if not is_batch_recipe(fresh):
                full_m = next(
                    (
                        m
                        for m in fresh.get("measures", [])
                        if isinstance(m, dict)
                        and str(m.get("name", "")).casefold() == "full recipe"
                    ),
                    None,
                )
                obs_full = full_m.get("value") if isinstance(full_m, dict) else None
                if not isinstance(obs_full, (int, float)) or not math.isclose(
                    float(obs_full), final_total, rel_tol=1e-3, abs_tol=0.1
                ):
                    mismatches.append(f"final_grams: expected {final_total}, observed {obs_full}")
            out: dict[str, Any] = {
                **result,
                "name": fresh.get("name"),
                "servings": observed_servings,
                "measures": extract_measures(fresh),
                "macros_per_100g": extract_key_macros(fresh),
                "verified": len(mismatches) == 0,
            }
            if mismatches:
                out["verification_mismatches"] = mismatches
            return out
