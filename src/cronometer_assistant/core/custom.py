"""Core custom-food creation/editing/retirement and recipe management."""

from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from typing import Any

from cronometer_mcp.client import CronometerError

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
from ..parsing import extract_key_macros
from ..state import AssistantState


class CustomFoodService:
    def __init__(
        self, client: CronometerSessionClient, state: AssistantState | None = None
    ) -> None:
        self.client = client
        self.state = state

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

        measures: list[dict[str, Any]] = [
            {
                "id": 0,
                "name": srv_name,
                "value": srv_grams,
                "amount": 1.0,
                "type": "Weight",
            },
            {
                "id": 0,
                "name": "g",
                "value": 1.0,
                "amount": 1.0,
                "type": "Weight",
            },
        ]
        seen_names = {srv_name.casefold(), "g"}
        for extra in extra_measures or []:
            if not isinstance(extra, dict):
                raise ValidationError("Each extra_measures entry must be an object")
            m_name = clean_text(extra.get("name"), "measure name", 100)
            m_grams = finite_number(extra.get("grams"), "measure grams", positive=True)
            m_amount = finite_number(extra.get("amount", 1.0), "measure amount", positive=True)
            if m_name.casefold() in seen_names:
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
        persisted_barcodes = fresh.get("barcodes") or []
        response = {
            "food_id": food_id,
            "name": fresh.get("name"),
            "category_id": fresh.get("category"),
            "label_type": fresh.get("labelType"),
            "barcodes": persisted_barcodes,
            "barcode_linked": bool(valid_barcode and valid_barcode in persisted_barcodes),
            "measures": extract_measures(fresh),
            "nutrients_set": len(normalized["nutrients_wire"]),
            "conversion_notes": normalized["notes"],
            "verified": True,
            "already_created": False,
        }
        if request_id and self.state:
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
        food = self.client.get_food(food_id)
        if food.get("owner") != self.client.user_id:
            raise ValidationError(f"Food {food_id} is not one of your custom foods")
        if food.get("meal"):
            raise ValidationError(
                f"Food {food_id} is a servings-based recipe; use save_recipe instead"
            )

        if name is not None:
            food["name"] = clean_text(name, "name", 300)
        if notes is not None:
            food["comments"] = notes
        if category_id is not None:
            if type(category_id) is not int or category_id < 0:
                raise ValidationError("category_id must be a non-negative integer")
            food["category"] = category_id
        if barcode is not None:
            valid_barcode = validate_barcode(barcode)
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
                    if m_name.casefold() in names:
                        raise ValidationError(
                            f"Food {food_id} already has a measure named {m_name!r}"
                        )
                    food.setdefault("measures", []).append(
                        {
                            "id": 0,
                            "name": m_name,
                            "value": m_grams,
                            "amount": m_amount,
                            "type": "Weight",
                        }
                    )
                    names.add(m_name.casefold())
                else:
                    target = by_id.get(mid)
                    if target is None:
                        raise ValidationError(f"Food {food_id} has no measure_id {mid}")
                    if patch.get("name") is not None:
                        target["name"] = clean_text(patch["name"], "measure name", 100)
                    if patch.get("grams") is not None:
                        target["value"] = finite_number(
                            patch["grams"], "measure grams", positive=True
                        )
                    if patch.get("amount") is not None:
                        target["amount"] = finite_number(
                            patch["amount"], "measure amount", positive=True
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
            existing = {
                n["id"]: n for n in food.get("nutrients", []) if isinstance(n, dict) and "id" in n
            }
            for row in normalized["nutrients_wire"]:
                if row["id"] in existing:
                    existing[row["id"]]["amount"] = row["amount"]
                else:
                    existing[row["id"]] = row
            food["nutrients"] = list(existing.values())
            if carbohydrate_basis in {"includes_fiber", "excludes_fiber"}:
                food["labelType"] = normalized["label_type"]

        self.client._save_food(food, keep_measures=True)
        fresh = self.client.get_food(food_id)
        return {
            "food_id": food_id,
            "name": fresh.get("name"),
            "category_id": fresh.get("category"),
            "label_type": fresh.get("labelType"),
            "barcodes": fresh.get("barcodes") or [],
            "measures": extract_measures(fresh),
            "nutrient_count": len(fresh.get("nutrients", [])),
            "conversion_notes": conversion_notes,
            "verified": True,
        }

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
            existing_servings = self.client._recipe_servings(existing_food, total_grams)
            effective_servings = srv_count if srv_count is not None else existing_servings
            if is_weight:
                result = self.client._create_weight_recipe(
                    recipe_name,
                    rows,
                    total_grams,
                    servings=effective_servings,
                    notes=notes,
                    recipe_id=recipe_id,
                    cooked_grams=cooked_grams,
                )
                # If a weight recipe previously had a `serving` measure (servings != 1) and
                # is now updated to servings == 1, update that existing measure's weight to full batch.
                if math.isclose(effective_servings, 1.0):
                    saved_now = self.client.get_food(recipe_id)
                    serving_m = next(
                        (m for m in saved_now.get("measures", []) if m.get("name") == "serving"),
                        None,
                    )
                    final_g = float(cooked_grams or total_grams)
                    if serving_m is not None and not math.isclose(
                        float(serving_m.get("value") or 0), final_g, rel_tol=1e-3
                    ):
                        serving_m["value"] = round(final_g, 2)
                        self.client._save_food(saved_now, keep_measures=True)
            else:
                if cooked_grams is not None:
                    raise ValidationError(
                        "cooked_grams applies to weight-based recipes only; this recipe is servings-based"
                    )
                result = self.client.create_recipe(
                    recipe_name,
                    normalized_ings,
                    servings=effective_servings,
                    notes=notes,
                    recipe_id=recipe_id,
                    serving_type="servings",
                )
        else:
            effective_servings = srv_count if srv_count is not None else 1.0
            result = self.client.create_recipe(
                recipe_name,
                normalized_ings,
                servings=effective_servings,
                notes=notes,
                serving_type=serving_type,
                cooked_grams=cooked_grams,
            )

        fresh = self.client.get_food(result["food_id"])
        final_total = float(cooked_grams or total_grams)
        observed_servings = self.client._recipe_servings(fresh, final_total)
        servings_match = math.isclose(
            observed_servings, effective_servings, rel_tol=1e-2, abs_tol=0.05
        )
        out: dict[str, Any] = {
            **result,
            "name": fresh.get("name"),
            "servings": observed_servings,
            "measures": extract_measures(fresh),
            "macros_per_100g": extract_key_macros(fresh, 1.0),
            "verified": servings_match,
        }
        if not servings_match:
            out["verification_mismatches"] = [
                f"servings: expected {effective_servings}, observed {observed_servings}"
            ]
        return out
