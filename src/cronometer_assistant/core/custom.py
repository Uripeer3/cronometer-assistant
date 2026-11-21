"""Core custom-food creation/editing/retirement and recipe management."""

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
    normalize_label_nutrients,
    resolve_serving_amount,
    validate_barcode,
)
from ..parsing import extract_key_macros, per_100g_nutrient_factor
from ..state import AssistantState

RESERVED_GRAM_NAMES = frozenset({"g", "gram", "grams"})
_RAW_SUMMARY_RE = re.compile(r"Raw ingredients \(([\d.]+) g\):")


class CustomFoodService:
    def __init__(
        self, client: CronometerSessionClient, state: AssistantState | None = None
    ) -> None:
        self.client = client
        self.state = state
        self._write_lock = threading.RLock()

    def _op_lock(self):
        return self.state.operation_lock() if self.state is not None else self._write_lock

    @staticmethod
    def _validate_measure_name(name: str, grams: float, amount: float) -> None:
        if name.casefold() in RESERVED_GRAM_NAMES and not math.isclose(
            grams / amount, 1.0, rel_tol=1e-4, abs_tol=1e-6
        ):
            raise ValidationError(
                f"Measure name {name!r} is reserved for the physical 1 g gram measure; "
                "use a descriptive name such as 'serving', 'scoop', 'bar', or 'piece'"
            )

    @staticmethod
    def _verify_custom_food(
        fresh: dict[str, Any],
        *,
        expected_name: str | None = None,
        expected_category: int | None = None,
        expected_label_type: str | None = None,
        expected_barcode: str | None = None,
        expected_measures: list[dict[str, Any]] | None = None,
        expected_nutrients: list[dict[str, Any]] | None = None,
    ) -> tuple[bool, list[str]]:
        """Compare saved Cronometer food fields, measures, and nutrients."""
        mismatches: list[str] = []
        if expected_name is not None and fresh.get("name") != expected_name:
            mismatches.append(f"name: expected {expected_name!r}, observed {fresh.get('name')!r}")
        if expected_category is not None and fresh.get("category") != expected_category:
            mismatches.append(
                f"category_id: expected {expected_category}, observed {fresh.get('category')}"
            )
        if expected_label_type is not None and fresh.get("labelType") != expected_label_type:
            mismatches.append(
                f"label_type: expected {expected_label_type!r}, observed {fresh.get('labelType')!r}"
            )
        if expected_barcode is not None:
            barcodes = fresh.get("barcodes") or []
            if expected_barcode not in barcodes:
                mismatches.append(f"barcode: expected {expected_barcode!r} in {barcodes!r}")

        fresh_measures = [m for m in fresh.get("measures", []) if isinstance(m, dict)]
        by_id = {m.get("id"): m for m in fresh_measures if m.get("id")}
        by_name = {str(m.get("name", "")).casefold(): m for m in fresh_measures}

        for exp_m in expected_measures or []:
            mid = exp_m.get("id")
            obs_m = by_id.get(mid) if mid else by_name.get(str(exp_m["name"]).casefold())
            if obs_m is None:
                mismatches.append(f"measure {exp_m['name']!r}: missing after save")
                continue
            if str(obs_m.get("name", "")).casefold() != str(exp_m["name"]).casefold():
                mismatches.append(
                    f"measure {mid} name: expected {exp_m['name']!r}, observed {obs_m.get('name')!r}"
                )
            obs_val = obs_m.get("value")
            if not isinstance(obs_val, (int, float)) or not math.isclose(
                float(obs_val), float(exp_m["value"]), rel_tol=1e-3, abs_tol=0.05
            ):
                mismatches.append(
                    f"measure {exp_m['name']!r} grams: expected {exp_m['value']}, observed {obs_val}"
                )
            obs_amt = obs_m.get("amount") or 1.0
            if not isinstance(obs_amt, (int, float)) or not math.isclose(
                float(obs_amt), float(exp_m["amount"]), rel_tol=1e-3, abs_tol=1e-3
            ):
                mismatches.append(
                    f"measure {exp_m['name']!r} amount: expected {exp_m['amount']}, observed {obs_amt}"
                )

        if expected_nutrients:
            obs_nutrients = {
                n["id"]: n.get("amount")
                for n in fresh.get("nutrients", [])
                if isinstance(n, dict) and isinstance(n.get("id"), int)
            }
            for row in expected_nutrients:
                nid = row["id"]
                exp_amt = float(row["amount"])
                obs_amt = obs_nutrients.get(nid)
                if not isinstance(obs_amt, (int, float)) or not math.isclose(
                    float(obs_amt), exp_amt, rel_tol=1e-3, abs_tol=0.05
                ):
                    mismatches.append(f"nutrient {nid}: expected {exp_amt}, observed {obs_amt}")

        return len(mismatches) == 0, mismatches

    @staticmethod
    def _existing_weight_recipe_cooked_grams(existing_food: dict[str, Any]) -> float | None:
        """Detect if an existing weight recipe has a persisted cooked weight distinct from raw."""
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

    def create_custom_food(
        self,
        name: str,
        nutrients: dict[str, Any],
        *,
        basis_amount: float = 100.0,
        basis_unit: str = "g",
        carbohydrate_basis: str = "unspecified",
        serving_name: str = "serving",
        serving_grams: float = 100.0,
        density_g_per_ml: float | None = None,
        category_id: int = 0,
        barcode: str | None = None,
        notes: str | None = None,
        extra_measures: list[dict[str, Any]] | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a private custom food with Weight measures, EU/US carb handling, and native barcode."""
        food_name = clean_text(name, "name", 300)
        srv_name = clean_text(serving_name, "serving_name", 100)
        srv_grams = finite_number(serving_grams, "serving_grams", positive=True)
        self._validate_measure_name(srv_name, srv_grams, 1.0)
        if type(category_id) is not int or category_id < 0:
            raise ValidationError("category_id must be a non-negative integer")
        valid_barcode = validate_barcode(barcode) if barcode is not None else None

        normalized = normalize_label_nutrients(
            nutrients,
            self.client.nutrient_index(),
            basis_amount=basis_amount,
            basis_unit=basis_unit,
            carbohydrate_basis=carbohydrate_basis,
            density_g_per_ml=density_g_per_ml,
        )

        # Put the primary serving measure FIRST (matching Cronometer's own custom foods) followed
        # by any extra measures, and include the physical 1 g `g` measure at the end.
        measures: list[dict[str, Any]] = [
            {
                "id": 0,
                "name": srv_name,
                "value": srv_grams,
                "amount": 1.0,
                "type": "Weight",
            }
        ]
        seen_names = {srv_name.casefold()}
        for extra in extra_measures or []:
            if not isinstance(extra, dict):
                raise ValidationError("Each extra_measures entry must be an object")
            m_name = clean_text(extra.get("name"), "measure name", 100)
            m_grams = finite_number(extra.get("grams"), "measure grams", positive=True)
            m_amount = finite_number(extra.get("amount", 1.0), "measure amount", positive=True)
            self._validate_measure_name(m_name, m_grams, m_amount)
            if m_name.casefold() in seen_names or m_name.casefold() == "g":
                raise ValidationError(f"Duplicate measure name: {m_name!r}")
            seen_names.add(m_name.casefold())
            measures.append(
                {
                    "id": 0,
                    "name": m_name,
                    "value": m_grams,
                    "amount": m_amount,
                    "type": "Weight",
                }
            )
        if "g" not in seen_names:
            measures.append(
                {
                    "id": 0,
                    "name": "g",
                    "value": 1.0,
                    "amount": 1.0,
                    "type": "Weight",
                }
            )

        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "name": food_name,
                    "category_id": category_id,
                    "barcode": valid_barcode,
                    "label_type": normalized["label_type"],
                    "measures": measures,
                    "nutrients": normalized["nutrients_wire"],
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()

        with self._op_lock():
            if request_id and self.state:
                existing_op = self.state.get_operation(
                    request_id, "create_custom_food", fingerprint=fingerprint
                )
                if existing_op is not None:
                    if existing_op["status"] == "completed":
                        return {**existing_op["data"], "already_created": True}
                    raise CronometerError(
                        f"Previous create_custom_food attempt for request_id {request_id!r} has an "
                        "uncertain outcome. Check get_my_foods before creating another food."
                    )
                self.state.save_operation(
                    request_id,
                    "create_custom_food",
                    fingerprint=fingerprint,
                    status="sending",
                    data={"name": food_name},
                )

            food_data: dict[str, Any] = {
                "id": 0,
                "name": food_name,
                "category": category_id,
                "owner": None,
                "retired": None,
                "source": None,
                "defaultMeasureId": 0,
                "alternateId": None,
                "comments": notes,
                "measures": measures,
                "labelType": normalized["label_type"],
                "nutrients": normalized["nutrients_wire"],
                "properties": {},
                "foodTags": [],
            }
            if valid_barcode is not None:
                food_data["barcodes"] = [valid_barcode]

            created = self.client._request(
                "/api/v2/add_food", {"data": deepcopy(food_data), "config": {"call_version": 1}}
            )
            food_id = created.get("id")
            if type(food_id) is not int or food_id <= 0:
                raise CronometerError("Cronometer did not return a valid food_id on creation")

            fresh = self.client.get_food(food_id)
            verified, mismatches = self._verify_custom_food(
                fresh,
                expected_name=food_name,
                expected_category=category_id,
                expected_label_type=normalized["label_type"],
                expected_barcode=valid_barcode,
                expected_measures=measures,
                expected_nutrients=normalized["nutrients_wire"],
            )
            persisted_barcodes = fresh.get("barcodes") or []
            extracted_measures = extract_measures(fresh)
            primary_measure = next(
                (
                    m
                    for m in extracted_measures
                    if str(m.get("name", "")).casefold() == srv_name.casefold()
                ),
                None,
            )
            response: dict[str, Any] = {
                "food_id": food_id,
                "name": fresh.get("name"),
                "category_id": fresh.get("category"),
                "label_type": fresh.get("labelType"),
                "default_measure_id": fresh.get("defaultMeasureId"),
                "primary_measure": primary_measure,
                "barcodes": persisted_barcodes,
                "barcode_linked": bool(valid_barcode and valid_barcode in persisted_barcodes),
                "measures": extracted_measures,
                "nutrients_set": len(normalized["nutrients_wire"]),
                "conversion_notes": normalized["notes"],
                "verified": verified,
                "already_created": False,
            }
            if mismatches:
                response["verification_mismatches"] = mismatches
            if request_id and self.state and verified:
                self.state.save_idempotent(
                    request_id, "create_custom_food", response, fingerprint=fingerprint
                )
            return response

    def update_custom_food(
        self,
        food_id: int,
        *,
        name: str | None = None,
        notes: str | None = None,
        category_id: int | None = None,
        barcode: str | None = None,
        nutrients: dict[str, Any] | None = None,
        basis_amount: float = 100.0,
        basis_unit: str = "g",
        carbohydrate_basis: str = "unspecified",
        density_g_per_ml: float | None = None,
        measures: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Edit an owned custom food in place while preserving existing measure IDs."""
        if type(food_id) is not int or food_id <= 0:
            raise ValidationError("food_id must be a positive integer")

        with self._op_lock():
            food = self.client.get_food(food_id)
            if food.get("owner") != self.client.user_id:
                raise ValidationError(f"Food {food_id} is not one of your custom foods")
            if food.get("meal"):
                raise ValidationError(
                    f"Food {food_id} is a servings-based recipe; use save_recipe instead"
                )

            expected_name: str | None = None
            expected_category: int | None = None
            expected_label_type: str | None = None
            expected_barcode: str | None = None
            expected_measures: list[dict[str, Any]] = []
            expected_nutrients: list[dict[str, Any]] | None = None

            if name is not None:
                expected_name = clean_text(name, "name", 300)
                food["name"] = expected_name
            if notes is not None:
                food["comments"] = notes
            if category_id is not None:
                if type(category_id) is not int or category_id < 0:
                    raise ValidationError("category_id must be a non-negative integer")
                expected_category = category_id
                food["category"] = category_id
            if barcode is not None:
                valid_barcode = validate_barcode(barcode)
                expected_barcode = valid_barcode
                existing_codes = list(food.get("barcodes") or [])
                if valid_barcode not in existing_codes:
                    existing_codes.append(valid_barcode)
                food["barcodes"] = existing_codes

            if measures:
                by_id = {m.get("id"): m for m in food.get("measures", []) if isinstance(m, dict)}
                names = {
                    str(m.get("name", "")).casefold()
                    for m in food.get("measures", [])
                    if isinstance(m, dict)
                }
                for patch in measures:
                    if not isinstance(patch, dict):
                        raise ValidationError("Each measure patch must be an object")
                    mid = patch.get("measure_id")
                    if mid is None:
                        m_name = clean_text(patch.get("name"), "measure name", 100)
                        m_grams = finite_number(patch.get("grams"), "measure grams", positive=True)
                        m_amount = finite_number(
                            patch.get("amount", 1.0), "measure amount", positive=True
                        )
                        self._validate_measure_name(m_name, m_grams, m_amount)
                        if m_name.casefold() in names:
                            raise ValidationError(
                                f"Food {food_id} already has a measure named {m_name!r}"
                            )
                        new_m = {
                            "id": 0,
                            "name": m_name,
                            "value": m_grams,
                            "amount": m_amount,
                            "type": "Weight",
                        }
                        food.setdefault("measures", []).append(new_m)
                        names.add(m_name.casefold())
                        expected_measures.append(new_m)
                    else:
                        target = by_id.get(mid)
                        if target is None:
                            raise ValidationError(f"Food {food_id} has no measure_id {mid}")
                        old_name_cf = str(target.get("name", "")).casefold()
                        eff_name = (
                            clean_text(patch["name"], "measure name", 100)
                            if patch.get("name") is not None
                            else str(target.get("name", ""))
                        )
                        eff_grams = (
                            finite_number(patch["grams"], "measure grams", positive=True)
                            if patch.get("grams") is not None
                            else float(target.get("value") or 1.0)
                        )
                        eff_amount = (
                            finite_number(patch["amount"], "measure amount", positive=True)
                            if patch.get("amount") is not None
                            else float(target.get("amount") or 1.0)
                        )
                        self._validate_measure_name(eff_name, eff_grams, eff_amount)
                        if eff_name.casefold() != old_name_cf and eff_name.casefold() in names:
                            raise ValidationError(
                                f"Food {food_id} already has a measure named {eff_name!r}"
                            )
                        names.discard(old_name_cf)
                        names.add(eff_name.casefold())
                        target["name"] = eff_name
                        target["value"] = eff_grams
                        target["amount"] = eff_amount
                        expected_measures.append(
                            {
                                "id": mid,
                                "name": eff_name,
                                "value": eff_grams,
                                "amount": eff_amount,
                            }
                        )

            conversion_notes: list[str] = []
            if nutrients:
                normalized = normalize_label_nutrients(
                    nutrients,
                    self.client.nutrient_index(),
                    basis_amount=basis_amount,
                    basis_unit=basis_unit,
                    carbohydrate_basis=carbohydrate_basis,
                    density_g_per_ml=density_g_per_ml,
                )
                conversion_notes = normalized["notes"]
                expected_nutrients = normalized["nutrients_wire"]
                existing = {
                    n["id"]: n
                    for n in food.get("nutrients", [])
                    if isinstance(n, dict) and "id" in n
                }
                for row in normalized["nutrients_wire"]:
                    if row["id"] in existing:
                        existing[row["id"]]["amount"] = row["amount"]
                    else:
                        existing[row["id"]] = row
                food["nutrients"] = list(existing.values())
                if carbohydrate_basis in {"includes_fiber", "excludes_fiber"}:
                    expected_label_type = normalized["label_type"]
                    food["labelType"] = expected_label_type

            self.client._save_food(food, keep_measures=True)
            fresh = self.client.get_food(food_id)
            verified, mismatches = self._verify_custom_food(
                fresh,
                expected_name=expected_name,
                expected_category=expected_category,
                expected_label_type=expected_label_type,
                expected_barcode=expected_barcode,
                expected_measures=expected_measures or None,
                expected_nutrients=expected_nutrients,
            )
            out: dict[str, Any] = {
                "food_id": food_id,
                "name": fresh.get("name"),
                "category_id": fresh.get("category"),
                "label_type": fresh.get("labelType"),
                "default_measure_id": fresh.get("defaultMeasureId"),
                "barcodes": fresh.get("barcodes") or [],
                "measures": extract_measures(fresh),
                "nutrient_count": len(fresh.get("nutrients", [])),
                "conversion_notes": conversion_notes,
                "verified": verified,
            }
            if mismatches:
                out["verification_mismatches"] = mismatches
            return out

    def retire_custom_food(self, food_id: int, *, retired: bool = True) -> dict[str, Any]:
        """Retire (or restore) an owned custom food or recipe safely."""
        if type(food_id) is not int or food_id <= 0:
            raise ValidationError("food_id must be a positive integer")
        food = self.client.get_food(food_id)
        if food.get("owner") != self.client.user_id:
            raise ValidationError(f"Food {food_id} is not one of your custom foods")
        self.client.retire_custom_food(food_id, retired=retired)
        fresh = self.client.get_food(food_id)
        return {
            "food_id": food_id,
            "name": fresh.get("name"),
            "retired": bool(fresh.get("retired")),
            "verified": bool(fresh.get("retired")) == bool(retired),
        }

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
        updated["meal"] = True
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
                is_weight = (
                    str(existing_food.get("properties", {}).get("advancedServingSize", "")).lower()
                    == "true"
                )
                if not existing_food.get("meal") and not is_weight:
                    raise ValidationError(
                        f"Food {recipe_id} is not a recipe; use update_custom_food instead"
                    )
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
            if not fresh.get("meal"):
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
