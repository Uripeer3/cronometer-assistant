"""Core recipe creation and editing (weight-based with cooked yield + servings-based)."""

from __future__ import annotations

import hashlib
import json
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
    extract_weight_recipe_footer,
    extract_weight_recipe_user_notes,
    is_batch_recipe,
    is_recipe_food,
    parse_weight_recipe_ingredient_footer,
    per_100g_nutrient_factor,
    recipe_servings,
)
from ..state import AssistantState

_RAW_SUMMARY_RE = re.compile(r"Raw ingredients \((\d+(?:\.\d+)?(?:[eE][+-]?\d+)?) g\):")


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
        footer = extract_weight_recipe_footer(existing_food.get("comments"))
        if not footer:
            return None
        if "Cooked weight " in footer:
            return full_grams
        match = _RAW_SUMMARY_RE.search(footer)
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
        return extract_weight_recipe_user_notes(comments)

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

    def _build_weight_recipe_payload(
        self,
        *,
        name: str,
        rows: list[dict[str, Any]],
        nutrients: list[dict[str, Any]],
        total_grams: float,
        servings: float,
        notes: str | None,
        cooked_grams: float | None,
        existing_food: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Assemble weight-recipe payload while preserving every existing measure ID."""
        recipe_id = int(existing_food["id"]) if existing_food is not None else 0
        final_grams = float(cooked_grams or total_grams)

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
        existing_by_name: dict[str, dict[str, Any]] = {}
        for m in existing_measures:
            key = str(m.get("name", "")).casefold()
            if m.get("id") and key not in existing_by_name:
                existing_by_name[key] = m

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

        # Preserve every existing measure by ID (even if Cronometer holds duplicate names).
        included_ids = {int(m["id"]) for m in measures if m.get("id")}
        for prev in existing_measures:
            pid = prev.get("id")
            if isinstance(pid, int) and pid > 0 and pid not in included_ids:
                measures.append(deepcopy(prev))
                included_ids.add(pid)

        props = dict((existing_food or {}).get("properties") or {})
        props["advancedServingSize"] = "true"
        if cooked_grams is not None:
            props["cooked_weight"] = f"{final_grams:g}"

        return {
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
            "properties": props,
            "foodTags": list((existing_food or {}).get("foodTags") or []),
            "meal": False,
        }

    @staticmethod
    def _build_servings_recipe_update_payload(
        *,
        existing_food: dict[str, Any],
        name: str,
        rows: list[dict[str, Any]],
        total_grams: float,
        servings: float,
        notes: str | None,
    ) -> dict[str, Any]:
        """Build updated servings-based recipe payload preserving all existing measure IDs."""
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
        return updated

    @classmethod
    def _extract_persisted_ingredient_pairs(cls, fresh: dict[str, Any]) -> list[tuple[int, float]]:
        raw_ings = [i for i in (fresh.get("ingredients") or []) if isinstance(i, dict)]
        if raw_ings:
            pairs: list[tuple[int, float]] = []
            for row in raw_ings:
                fid = row.get("foodId")
                grams = row.get("grams")
                if isinstance(fid, int) and isinstance(grams, (int, float)):
                    pairs.append((fid, float(grams)))
            return pairs
        return parse_weight_recipe_ingredient_footer(fresh.get("comments"))

    @staticmethod
    def _compare_ingredient_lists(
        expected_rows: list[dict[str, Any]], observed_pairs: list[tuple[int, float]]
    ) -> list[str]:
        expected_sorted = sorted((int(r["foodId"]), float(r["grams"])) for r in expected_rows)
        observed_sorted = sorted(observed_pairs)
        if len(expected_sorted) != len(observed_sorted):
            return [
                f"ingredients count: expected {len(expected_sorted)}, observed {len(observed_sorted)}"
            ]
        mismatches: list[str] = []
        for idx, ((exp_fid, exp_g), (obs_fid, obs_g)) in enumerate(
            zip(expected_sorted, observed_sorted, strict=True)
        ):
            if exp_fid != obs_fid or not math.isclose(exp_g, obs_g, rel_tol=1e-2, abs_tol=0.1):
                mismatches.append(
                    f"ingredient[{idx}]: expected (food_id={exp_fid}, grams={exp_g:g}), "
                    f"observed (food_id={obs_fid}, grams={obs_g:g})"
                )
        return mismatches

    def _finalize_saved_recipe(
        self,
        record: dict[str, Any],
        *,
        request_id: str | None,
        fingerprint: str,
    ) -> dict[str, Any]:
        result = record["result"]
        expected_name = record["expected_name"]
        expected_servings = float(record["expected_servings"])
        expected_final_grams = float(record["expected_final_grams"])
        expected_rows = record["expected_rows"]
        expected_nutrients = record.get("expected_nutrients")
        expected_notes = record.get("expected_notes")
        is_weight = bool(record["is_weight"])

        fresh = self.client.get_food(result["food_id"])
        observed_servings = recipe_servings(fresh, expected_final_grams)
        mismatches: list[str] = []

        if fresh.get("name") != expected_name:
            mismatches.append(f"name: expected {expected_name!r}, observed {fresh.get('name')!r}")

        if not math.isclose(observed_servings, expected_servings, rel_tol=1e-2, abs_tol=0.05):
            mismatches.append(
                f"servings: expected {expected_servings}, observed {observed_servings}"
            )

        observed_ings = self._extract_persisted_ingredient_pairs(fresh)
        mismatches.extend(self._compare_ingredient_lists(expected_rows, observed_ings))

        if is_weight:
            full_m = next(
                (
                    m
                    for m in fresh.get("measures", [])
                    if isinstance(m, dict) and str(m.get("name", "")).casefold() == "full recipe"
                ),
                None,
            )
            obs_full = full_m.get("value") if isinstance(full_m, dict) else None
            if not isinstance(obs_full, (int, float)) or not math.isclose(
                float(obs_full), expected_final_grams, rel_tol=1e-3, abs_tol=0.1
            ):
                mismatches.append(
                    f"final_grams: expected {expected_final_grams}, observed {obs_full}"
                )
            if expected_nutrients:
                obs_nutrients = {
                    n["id"]: n.get("amount")
                    for n in fresh.get("nutrients", [])
                    if isinstance(n, dict) and isinstance(n.get("id"), int)
                }
                expected_by_id = {row["id"]: float(row["amount"]) for row in expected_nutrients}
                for nid, exp_amt in expected_by_id.items():
                    obs_amt = obs_nutrients.get(nid)
                    if not isinstance(obs_amt, (int, float)) or not math.isclose(
                        float(obs_amt), exp_amt, rel_tol=1e-3, abs_tol=0.05
                    ):
                        mismatches.append(f"nutrient {nid}: expected {exp_amt}, observed {obs_amt}")
                for nid, obs_amt in sorted(obs_nutrients.items()):
                    if nid <= 0 or nid in COMPUTED_NUTRIENT_IDS or nid in expected_by_id:
                        continue
                    if isinstance(obs_amt, (int, float)) and abs(float(obs_amt)) > 1e-5:
                        mismatches.append(
                            f"unexpected nutrient {nid}: expected absent/0, observed {obs_amt}"
                        )
            if expected_notes is not None:
                obs_notes = self._extract_user_notes(fresh.get("comments"))
                exp_clean = expected_notes.strip() or None
                if obs_notes != exp_clean:
                    mismatches.append(f"notes: expected {exp_clean!r}, observed {obs_notes!r}")
        elif expected_notes is not None and fresh.get("comments") != expected_notes:
            mismatches.append(
                f"notes: expected {expected_notes!r}, observed {fresh.get('comments')!r}"
            )

        out: dict[str, Any] = {
            **result,
            "name": fresh.get("name"),
            "servings": observed_servings,
            "measures": extract_measures(fresh),
            "macros_per_100g": extract_key_macros(fresh),
            "verified": len(mismatches) == 0,
            "already_saved": False,
        }
        if mismatches:
            out["verification_mismatches"] = mismatches
        if request_id and self.state and out["verified"]:
            self.state.save_idempotent(request_id, "save_recipe", out, fingerprint=fingerprint)
        return out

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
        request_id: str | None = None,
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
        if recipe_id is not None and (type(recipe_id) is not int or recipe_id <= 0):
            raise ValidationError("recipe_id must be a positive integer")

        normalized_input_ings: list[dict[str, Any]] = []
        ing_fids: list[int] = []
        for idx, ing in enumerate(ingredients):
            if (
                not isinstance(ing, dict)
                or type(ing.get("food_id")) is not int
                or ing["food_id"] <= 0
            ):
                raise ValidationError(f"ingredients[{idx}].food_id must be a positive integer")
            grams_raw = ing.get("grams")
            qty_raw = ing.get("quantity")
            if (grams_raw is None) == (qty_raw is None):
                raise ValidationError(
                    f"ingredients[{idx}]: provide either `grams` OR `quantity` (not both)"
                )
            grams_val = (
                finite_number(grams_raw, f"ingredients[{idx}].grams", positive=True)
                if grams_raw is not None
                else None
            )
            qty_val = (
                finite_number(qty_raw, f"ingredients[{idx}].quantity", positive=True)
                if qty_raw is not None
                else None
            )
            mid_raw = ing.get("measure_id")
            if mid_raw is not None and (type(mid_raw) is not int or mid_raw <= 0):
                raise ValidationError(f"ingredients[{idx}].measure_id must be a positive integer")
            mname_raw = ing.get("measure_name")
            mname_clean = (
                clean_text(mname_raw, f"ingredients[{idx}].measure_name", 100)
                if mname_raw is not None
                else None
            )
            ing_fids.append(ing["food_id"])
            normalized_input_ings.append(
                {
                    "food_id": ing["food_id"],
                    "grams": grams_val,
                    "quantity": qty_val,
                    "measure_id": mid_raw,
                    "measure_name": mname_clean,
                }
            )

        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "name": recipe_name,
                    "recipe_id": recipe_id,
                    "serving_type": serving_type,
                    "servings": srv_count,
                    "cooked_grams": cooked_grams,
                    "notes": notes,
                    "ingredients": [
                        {
                            **ing,
                            "measure_name": ing["measure_name"].casefold()
                            if ing["measure_name"] is not None
                            else None,
                        }
                        for ing in normalized_input_ings
                    ],
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()

        with self._op_lock():
            if request_id and self.state:
                existing_op = self.state.get_operation(
                    request_id, "save_recipe", fingerprint=fingerprint
                )
                if existing_op is not None:
                    if existing_op["status"] == "completed":
                        return {**existing_op["data"], "already_saved": True}
                    if existing_op["status"] == "created" and existing_op["data"].get("result"):
                        return self._finalize_saved_recipe(
                            existing_op["data"], request_id=request_id, fingerprint=fingerprint
                        )
                    raise CronometerError(
                        f"Previous save_recipe attempt for request_id {request_id!r} has an "
                        "uncertain outcome. Check get_my_foods before saving another recipe."
                    )

            foods_by_id = {
                f["id"]: f
                for f in self.client.get_foods(sorted(set(ing_fids)))
                if isinstance(f, dict) and f.get("id")
            }

            normalized_ings: list[dict[str, Any]] = []
            for idx, ing in enumerate(normalized_input_ings):
                fid = ing["food_id"]
                raw_food = foods_by_id.get(fid)
                if raw_food is None:
                    raise ValidationError(f"ingredients[{idx}]: food_id {fid} not found")
                resolved = resolve_serving_amount(
                    raw_food,
                    grams=ing["grams"],
                    quantity=ing["quantity"],
                    measure_id=ing["measure_id"],
                    measure_name=ing["measure_name"],
                )
                normalized_ings.append(
                    {
                        "food_id": fid,
                        "grams": resolved["grams"],
                        "measure_id": resolved["measure_id"],
                    }
                )

            rows, total_grams = self.client.normalise_ingredients(normalized_ings)

            effective_cooked_grams = cooked_grams
            existing_food: dict[str, Any] | None = None
            if recipe_id is not None:
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
                    existing_servings = recipe_servings(existing_food, existing_final)
                    effective_servings = srv_count if srv_count is not None else existing_servings
                else:
                    if cooked_grams is not None:
                        raise ValidationError(
                            "cooked_grams applies to weight-based recipes only; this recipe is servings-based"
                        )
                    existing_servings = recipe_servings(existing_food, total_grams)
                    effective_servings = srv_count if srv_count is not None else existing_servings
            else:
                effective_servings = srv_count if srv_count is not None else 1.0
                is_weight = serving_type == "weight"
                if not is_weight and cooked_grams is not None:
                    raise ValidationError(
                        "cooked_grams applies to weight-based recipes only; use serving_type='weight'"
                    )

            # Build and preflight-check payloads BEFORE writing status='sending' so no local
            # error (missing nutrients or measure-ID preservation) can strand request_id.
            final_total = float(effective_cooked_grams or total_grams)
            expected_nutrients: list[dict[str, Any]] | None = None
            payload_to_save: dict[str, Any] | None = None
            if is_weight:
                expected_nutrients = self._sum_weight_recipe_nutrients(
                    rows, foods_by_id, final_total
                )
                if not expected_nutrients:
                    raise ValidationError("Ingredients carry no nutrient data")
                payload_to_save = self._build_weight_recipe_payload(
                    name=recipe_name,
                    rows=rows,
                    nutrients=expected_nutrients,
                    total_grams=total_grams,
                    servings=effective_servings,
                    notes=notes,
                    cooked_grams=effective_cooked_grams,
                    existing_food=existing_food,
                )
            elif existing_food is not None:
                payload_to_save = self._build_servings_recipe_update_payload(
                    existing_food=existing_food,
                    name=recipe_name,
                    rows=rows,
                    total_grams=total_grams,
                    servings=effective_servings,
                    notes=notes,
                )

            if payload_to_save is not None and existing_food is not None:
                req_ids = {
                    int(m["id"])
                    for m in existing_food.get("measures", [])
                    if isinstance(m, dict) and isinstance(m.get("id"), int) and m["id"] > 0
                }
                got_ids = {
                    int(m["id"])
                    for m in payload_to_save.get("measures", [])
                    if isinstance(m, dict) and isinstance(m.get("id"), int) and m["id"] > 0
                }
                missing_ids = sorted(req_ids - got_ids)
                if missing_ids:
                    raise ValidationError(
                        f"Refusing recipe update that drops existing measure IDs: {missing_ids}"
                    )

            if request_id and self.state:
                self.state.save_operation(
                    request_id,
                    "save_recipe",
                    fingerprint=fingerprint,
                    status="sending",
                    data={"name": recipe_name, "recipe_id": recipe_id},
                )

            if is_weight:
                assert payload_to_save is not None
                data = self.client.save_food(payload_to_save, keep_measures=bool(existing_food))
                food_id = data.get("id")
                if type(food_id) is not int or food_id <= 0:
                    raise CronometerError(f"Failed to save weight recipe: {data}")
                result = {
                    "food_id": food_id,
                    "serving_type": "weight",
                    "total_grams": total_grams,
                    "final_grams": final_total,
                    "grams_per_serving": round(final_total / effective_servings, 2),
                }
            elif existing_food is not None:
                assert payload_to_save is not None
                data = self.client.save_food(payload_to_save, keep_measures=True)
                food_id = data.get("id") or existing_food.get("id")
                if type(food_id) is not int or food_id <= 0:
                    raise CronometerError(f"Failed to update recipe: {data}")
                result = {
                    "food_id": food_id,
                    "serving_type": "servings",
                    "total_grams": total_grams,
                    "grams_per_serving": round(total_grams / effective_servings, 2),
                }
            else:
                result = self.client.create_recipe(
                    recipe_name,
                    normalized_ings,
                    servings=effective_servings,
                    notes=notes,
                    serving_type="servings",
                )

            created_record = {
                "result": result,
                "expected_name": recipe_name,
                "expected_servings": effective_servings,
                "expected_final_grams": float(effective_cooked_grams or total_grams),
                "expected_rows": rows,
                "expected_nutrients": expected_nutrients,
                "expected_notes": notes,
                "is_weight": is_weight,
            }
            if request_id and self.state:
                self.state.save_operation(
                    request_id,
                    "save_recipe",
                    fingerprint=fingerprint,
                    status="created",
                    data=created_record,
                )

            return self._finalize_saved_recipe(
                created_record, request_id=request_id, fingerprint=fingerprint
            )
