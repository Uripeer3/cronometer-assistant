"""Core custom-food creation, editing, and retirement."""

from __future__ import annotations

import hashlib
import json
import math
import threading
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
    validate_barcode,
)
from ..parsing import is_batch_recipe
from ..state import AssistantState

RESERVED_GRAM_NAMES = frozenset({"g", "gram", "grams"})


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
        expected_notes: str | None = None,
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
        if expected_notes is not None and fresh.get("comments") != expected_notes:
            mismatches.append(
                f"notes: expected {expected_notes!r}, observed {fresh.get('comments')!r}"
            )

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
    def _canonical_nutrients_input(nutrients: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(nutrients, dict) or not nutrients:
            raise ValidationError("Provide at least one nutrient")
        out: dict[str, Any] = {}
        for k, v in nutrients.items():
            key = str(k).strip()
            if isinstance(v, dict):
                out[key] = {
                    "amount": float(v["amount"])
                    if isinstance(v.get("amount"), (int, float))
                    and not isinstance(v.get("amount"), bool)
                    else v.get("amount"),
                    "unit": str(v["unit"]).strip()
                    if isinstance(v.get("unit"), str)
                    else v.get("unit"),
                }
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                out[key] = float(v)
            else:
                out[key] = v
        return out

    def _finalize_created_custom_food(
        self,
        record: dict[str, Any],
        *,
        request_id: str | None,
        fingerprint: str,
    ) -> dict[str, Any]:
        food_id = int(record["food_id"])
        fresh = self.client.get_food(food_id)
        verified, mismatches = self._verify_custom_food(
            fresh,
            expected_name=record["expected_name"],
            expected_category=int(record["expected_category"]),
            expected_label_type=record["expected_label_type"],
            expected_barcode=record.get("expected_barcode"),
            expected_notes=record.get("expected_notes"),
            expected_measures=record["expected_measures"],
            expected_nutrients=record["expected_nutrients"],
        )
        persisted_barcodes = fresh.get("barcodes") or []
        extracted_measures = extract_measures(fresh)
        srv_cf = record["srv_name"].casefold()
        primary_measure = next(
            (m for m in extracted_measures if str(m.get("name", "")).casefold() == srv_cf),
            None,
        )
        expected_barcode = record.get("expected_barcode")
        response: dict[str, Any] = {
            "food_id": food_id,
            "name": fresh.get("name"),
            "category_id": fresh.get("category"),
            "label_type": fresh.get("labelType"),
            "default_measure_id": fresh.get("defaultMeasureId"),
            "primary_measure": primary_measure,
            "barcodes": persisted_barcodes,
            "barcode_linked": bool(expected_barcode and expected_barcode in persisted_barcodes),
            "measures": extracted_measures,
            "nutrients_set": len(record["expected_nutrients"]),
            "conversion_notes": record.get("conversion_notes") or [],
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
        basis_val = finite_number(basis_amount, "basis_amount", positive=True)
        density_val = (
            finite_number(density_g_per_ml, "density_g_per_ml", positive=True)
            if density_g_per_ml is not None
            else None
        )
        canonical_nutrients = self._canonical_nutrients_input(nutrients)

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

        fingerprint = (
            "v2:"
            + hashlib.sha256(
                json.dumps(
                    {
                        "name": food_name,
                        "category_id": category_id,
                        "barcode": valid_barcode,
                        "notes": notes,
                        "basis_amount": basis_val,
                        "basis_unit": basis_unit,
                        "carbohydrate_basis": carbohydrate_basis,
                        "density_g_per_ml": density_val,
                        "measures": measures,
                        "nutrients": canonical_nutrients,
                    },
                    sort_keys=True,
                ).encode()
            ).hexdigest()
        )

        with self._op_lock():
            if request_id and self.state:
                existing_op = self.state.get_operation(
                    request_id, "create_custom_food", fingerprint=fingerprint
                )
                if existing_op is not None:
                    if existing_op["status"] == "completed":
                        return {**existing_op["data"], "already_created": True}
                    if existing_op["status"] == "created" and existing_op["data"].get("food_id"):
                        return self._finalize_created_custom_food(
                            existing_op["data"], request_id=request_id, fingerprint=fingerprint
                        )
                    raise CronometerError(
                        f"Previous create_custom_food attempt for request_id {request_id!r} has an "
                        "uncertain outcome. Check get_my_foods before creating another food."
                    )

            normalized = normalize_label_nutrients(
                nutrients,
                self.client.nutrient_index(),
                basis_amount=basis_val,
                basis_unit=basis_unit,
                carbohydrate_basis=carbohydrate_basis,
                density_g_per_ml=density_val,
            )

            if request_id and self.state:
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

            created = self.client.request_v2(
                "/api/v2/add_food", {"data": deepcopy(food_data), "config": {"call_version": 1}}
            )
            food_id = created.get("id")
            if type(food_id) is not int or food_id <= 0:
                raise CronometerError("Cronometer did not return a valid food_id on creation")

            created_record = {
                "food_id": food_id,
                "expected_name": food_name,
                "expected_category": category_id,
                "expected_label_type": normalized["label_type"],
                "expected_barcode": valid_barcode,
                "expected_notes": notes,
                "expected_measures": measures,
                "expected_nutrients": normalized["nutrients_wire"],
                "srv_name": srv_name,
                "conversion_notes": normalized["notes"],
            }
            if request_id and self.state:
                self.state.save_operation(
                    request_id,
                    "create_custom_food",
                    fingerprint=fingerprint,
                    status="created",
                    data=created_record,
                )

            return self._finalize_created_custom_food(
                created_record, request_id=request_id, fingerprint=fingerprint
            )

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
            if is_batch_recipe(food):
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

            self.client.save_food(food, keep_measures=True)
            fresh = self.client.get_food(food_id)
            verified, mismatches = self._verify_custom_food(
                fresh,
                expected_name=expected_name,
                expected_category=expected_category,
                expected_label_type=expected_label_type,
                expected_barcode=expected_barcode,
                expected_notes=notes,
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
        with self._op_lock():
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
