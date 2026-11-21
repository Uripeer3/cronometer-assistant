"""Pure nutrition normalization, Hebrew/English label parsing, and exact measure math."""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Any

# Canonical nutrient aliases (Hebrew + English label variants -> Cronometer catalog slug)
LABEL_ALIASES: dict[str, str] = {
    # Energy
    "calories": "energy",
    "kcal": "energy",
    "אנרגיה": "energy",
    "קלוריות": "energy",
    # Protein
    "חלבון": "protein",
    "חלבונים": "protein",
    # Carbs & sub-components
    "carbohydrates": "carbs",
    "total_carbohydrates": "carbs",
    "total_carbs": "carbs",
    "פחמימות": "carbs",
    "סך_הפחמימות": "carbs",
    "dietary_fiber": "fiber",
    "סיבים_תזונתיים": "fiber",
    "סיבים": "fiber",
    "sugar": "sugars",
    "total_sugars": "sugars",
    "סוכרים": "sugars",
    "סוכר": "sugars",
    "מתוכן_סוכרים": "sugars",
    "מתוכם_סוכרים": "sugars",
    "polyols": "sugar_alcohol",
    "sugar_alcohols": "sugar_alcohol",
    "רב_כהליים": "sugar_alcohol",
    "רבי_כהלים": "sugar_alcohol",
    "פוליאולים": "sugar_alcohol",
    "מתוכן_רב_כהליים": "sugar_alcohol",
    # Fats
    "total_fat": "fat",
    "lipids": "fat",
    "שומנים": "fat",
    "שומן": "fat",
    "סך_השומנים": "fat",
    "saturated_fat": "saturated",
    "saturated_fats": "saturated",
    "שומן_רווי": "saturated",
    "חומצות_שומן_רוויות": "saturated",
    "מתוכן_חומצות_שומן_רוויות": "saturated",
    "trans_fat": "trans_fats",
    "שומן_טראנס": "trans_fats",
    "חומצות_שומן_טראנס": "trans_fats",
    "מתוכן_חומצות_שומן_טראנס": "trans_fats",
    "monounsaturated_fat": "monounsaturated",
    "שומן_חד_בלתי_רווי": "monounsaturated",
    "polyunsaturated_fat": "polyunsaturated",
    "שומן_רב_בלתי_רווי": "polyunsaturated",
    "cholesterol": "cholesterol",
    "כולסטרול": "cholesterol",
    # Sodium / Salt
    "נתרן": "sodium",
    "מלח": "salt",
    # Common micronutrients (Hebrew -> Cronometer catalog slug)
    "סידן": "calcium",
    "ברזל": "iron",
    "אשלגן": "potassium",
    "מגנזיום": "magnesium",
    "אבץ": "zinc",
    "זרחן": "phosphorus",
    "קפאין": "caffeine",
    "ויטמין_a": "vitamin_a",
    "ויטמין_c": "vitamin_c",
    "ויטמין_d": "vitamin_d",
    "ויטמין_e": "vitamin_e",
    "ויטמין_k": "vitamin_k",
    "ויטמין_b1": "b1_thiamine",
    "ויטמין_b2": "b2_riboflavin",
    "ויטמין_b3": "b3_niacin",
    "ויטמין_b5": "b5_pantothenic_acid",
    "ויטמין_b6": "b6_pyridoxine",
    "ויטמין_b12": "b12_cobalamin",
    "חומצה_פולית": "folate",
    "ביוטין": "biotin",
    # Extra English aliases matching cronometer_mcp
    "vitamin_b1": "b1_thiamine",
    "thiamine": "b1_thiamine",
    "vitamin_b2": "b2_riboflavin",
    "riboflavin": "b2_riboflavin",
    "vitamin_b3": "b3_niacin",
    "niacin": "b3_niacin",
    "vitamin_b5": "b5_pantothenic_acid",
    "pantothenic_acid": "b5_pantothenic_acid",
    "vitamin_b6": "b6_pyridoxine",
    "pyridoxine": "b6_pyridoxine",
    "vitamin_b12": "b12_cobalamin",
    "cobalamin": "b12_cobalamin",
    "lutein": "lutein_zeaxanthin",
    "zeaxanthin": "lutein_zeaxanthin",
}

MASS_FACTORS_G: dict[str, float] = {"g": 1.0, "mg": 1e-3, "ug": 1e-6}
ENERGY_UNITS = {"kcal", "kJ", "kj"}

# Key macro nutrient IDs used for concise food summaries
KEY_MACRO_IDS: dict[int, tuple[str, str]] = {
    208: ("energy_kcal", "kcal"),
    203: ("protein_g", "g"),
    205: ("carbs_g", "g"),
    291: ("fiber_g", "g"),
    269: ("sugars_g", "g"),
    10007: ("sugar_alcohol_g", "g"),
    204: ("fat_g", "g"),
    606: ("saturated_fat_g", "g"),
    307: ("sodium_mg", "mg"),
}


class ValidationError(ValueError):
    """User or tool input needs correction before executing an operation."""


def finite_number(value: Any, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{name} must be a number")
    num = float(value)
    if not math.isfinite(num) or num < 0 or (positive and num == 0):
        bound = "positive" if positive else "non-negative"
        raise ValidationError(f"{name} must be finite and {bound}")
    return num


def clean_text(value: Any, name: str, max_len: int = 500) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > max_len:
        raise ValidationError(f"{name} must be non-empty text of at most {max_len} characters")
    return value.strip()


def normalize_key(value: str) -> str:
    """Normalize a Hebrew or English label/alias string for stable matching."""
    text = unicodedata.normalize("NFKC", value).casefold()
    text = re.sub(r"[^\w\s%]", " ", text)
    return "_".join(text.split())


def canonical_unit(unit: str) -> str:
    cleaned = unit.strip()
    if cleaned in {"µg", "μg", "mcg", "MCG"}:
        return "ug"
    if cleaned in {"kJ", "KJ", "kj"}:
        return "kJ"
    if cleaned in {"kcal", "KCAL", "Cal"}:
        return "kcal"
    if cleaned in {"iu", "IU"}:
        return "IU"
    if cleaned in {"g", "G"}:
        return "g"
    if cleaned in {"mg", "MG"}:
        return "mg"
    return cleaned


def validate_barcode(value: str) -> str:
    """Validate an EAN-8, UPC-A (12), EAN-13, or GTIN-14 barcode with checksum."""
    code = clean_text(value, "barcode", 14)
    if not code.isascii() or not code.isdigit() or len(code) not in {8, 12, 13, 14}:
        raise ValidationError("barcode must be 8, 12, 13, or 14 digits")
    expected = (-sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(code[-2::-1]))) % 10
    if int(code[-1]) != expected:
        raise ValidationError("barcode checksum failed; double-check the digits")
    return code


def extract_measures(raw_food: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract visible measures from a Cronometer food with exact grams_per_unit.

    Cronometer stores `amount` (e.g. 22) and `value` (e.g. 30 g) so 22 pieces = 30 g,
    meaning grams_per_unit = value / amount = 1.3636 g/piece.
    """
    out: list[dict[str, Any]] = []
    default_id = raw_food.get("defaultMeasureId")
    for m in raw_food.get("measures", []):
        if not isinstance(m, dict) or m.get("hidden"):
            continue
        mid = m.get("id")
        mtype = m.get("type")
        amount = m.get("amount")
        value = m.get("value")
        grams_per_unit: float | None = None
        if (
            mtype in {"Weight", "Atomic"}
            and isinstance(value, (int, float))
            and isinstance(amount, (int, float))
            and math.isfinite(value)
            and math.isfinite(amount)
            and value > 0
            and amount > 0
        ):
            grams_per_unit = round(float(value) / float(amount), 6)
        out.append(
            {
                "measure_id": mid,
                "name": m.get("name"),
                "type": mtype,
                "amount": amount,
                "value": value,
                "grams_per_unit": grams_per_unit,
                "is_default": mid == default_id,
            }
        )
    return out


def _is_one_gram_measure(measure: dict[str, Any]) -> bool:
    name = str(measure.get("name", "")).strip().casefold()
    val = measure.get("value")
    amt = measure.get("amount") or 1.0
    return (
        name in {"g", "gram", "grams"}
        and isinstance(val, (int, float))
        and isinstance(amt, (int, float))
        and amt > 0
        and math.isclose(float(val) / float(amt), 1.0, rel_tol=1e-4, abs_tol=1e-6)
    )


def resolve_serving_amount(
    raw_food: dict[str, Any],
    *,
    grams: float | None = None,
    quantity: float | None = None,
    measure_id: int | None = None,
    measure_name: str | None = None,
) -> dict[str, Any]:
    """Resolve exact (measure_id, grams, display_measure) for any Cronometer food.

    Supports Weight measures, Atomic measures, and Recipe foods (meal=True).
    Either `grams` OR `quantity` must be supplied (not both).
    """
    if (grams is None) == (quantity is None):
        raise ValidationError("Provide either `grams` OR `quantity` (not both)")

    measures = [m for m in raw_food.get("measures", []) if isinstance(m, dict) and m.get("id")]
    is_native_recipe = bool(raw_food.get("meal"))

    def _find_measure() -> dict[str, Any] | None:
        if measure_id is not None:
            found = next((m for m in measures if m.get("id") == measure_id), None)
            if found is None:
                valid = [(m.get("id"), m.get("name")) for m in measures if not m.get("hidden")]
                raise ValidationError(
                    f"measure_id {measure_id} does not belong to food {raw_food.get('id')}. "
                    f"Available measures: {valid}"
                )
            return found
        if measure_name is not None:
            target = measure_name.strip().casefold()
            found = next(
                (
                    m
                    for m in measures
                    if not m.get("hidden") and str(m.get("name", "")).strip().casefold() == target
                ),
                None,
            )
            if found is None:
                valid = [m.get("name") for m in measures if not m.get("hidden")]
                raise ValidationError(
                    f"Measure {measure_name!r} not found on food {raw_food.get('id')}. "
                    f"Available measures: {valid}"
                )
            return found
        return None

    if grams is not None:
        grams_val = finite_number(grams, "grams", positive=True)
        chosen = _find_measure()
        if chosen is None:
            # Prefer a 1-gram measure if present, else defaultMeasureId, else first visible measure
            chosen = next(
                (
                    m
                    for m in measures
                    if not m.get("hidden")
                    and str(m.get("name", "")).strip().casefold() in {"g", "gram", "grams"}
                    and isinstance(m.get("value"), (int, float))
                    and isinstance(m.get("amount"), (int, float))
                    and m["amount"] > 0
                    and math.isclose(m["value"] / m["amount"], 1.0, rel_tol=1e-4)
                ),
                None,
            )
            if chosen is None:
                default_id = raw_food.get("defaultMeasureId")
                chosen = next((m for m in measures if m.get("id") == default_id), None)
            if chosen is None and measures:
                chosen = next((m for m in measures if not m.get("hidden")), measures[0])
        if chosen is None and not is_native_recipe:
            raise ValidationError(f"Food {raw_food.get('id')} has no usable measure")
        return {
            "measure_id": chosen["id"] if chosen else 0,
            "measure_name": chosen.get("name") if chosen else "g",
            "grams": grams_val,
        }

    # Quantity-based logging
    qty_val = finite_number(quantity, "quantity", positive=True)
    chosen = _find_measure()
    if chosen is None:
        default_id = raw_food.get("defaultMeasureId")
        chosen = next((m for m in measures if m.get("id") == default_id), None)
        # Cronometer assigns defaultMeasureId to the 1 g `g` measure on Weight custom foods
        # even when a named serving (e.g. `serving = 42 g`) is defined. When a caller logs by
        # `quantity` without naming a unit, prefer the food's primary serving measure over `1 g`.
        if chosen is not None and _is_one_gram_measure(chosen):
            non_mass = [
                m
                for m in measures
                if not m.get("hidden")
                and str(m.get("name", "")).strip().casefold()
                not in {"g", "gram", "grams", "oz", "ounce", "ounces", "lb", "lbs", "kg"}
            ]
            if non_mass:
                chosen = non_mass[0]
        if chosen is None:
            chosen = next((m for m in measures if not m.get("hidden")), None)
    if chosen is None:
        raise ValidationError(f"Food {raw_food.get('id')} has no default measure for quantity")

    if is_native_recipe and chosen.get("type") == "Recipe":
        # Recipe measures store units-per-batch in `value` (e.g. 4 servings per batch).
        # Compute total batch grams from ingredients (or Recipe "g" measure) for add_serving().
        total_batch = sum(
            float(i.get("grams") or 0)
            for i in raw_food.get("ingredients", [])
            if isinstance(i, dict)
        )
        if total_batch <= 0:
            g_m = next(
                (
                    m
                    for m in measures
                    if m.get("type") == "Recipe"
                    and m.get("name") == "g"
                    and (m.get("value") or 0) > 0
                ),
                None,
            )
            if g_m is not None:
                total_batch = float(g_m["value"])
        m_val = chosen.get("value")
        if total_batch <= 0 or not isinstance(m_val, (int, float)) or m_val <= 0:
            raise ValidationError(
                f"Cannot derive gram weight for recipe {raw_food.get('id')} measure {chosen.get('name')!r}"
            )
        grams_val = qty_val * (total_batch / float(m_val))
        return {
            "measure_id": chosen["id"],
            "measure_name": chosen.get("name"),
            "grams": round(grams_val, 4),
        }

    m_val = chosen.get("value")
    m_amt = chosen.get("amount") or 1.0
    if (
        not isinstance(m_val, (int, float))
        or not isinstance(m_amt, (int, float))
        or not math.isfinite(m_val)
        or not math.isfinite(m_amt)
        or m_val <= 0
        or m_amt <= 0
    ):
        raise ValidationError(
            f"Measure {chosen.get('name')!r} on food {raw_food.get('id')} has no valid gram weight"
        )
    grams_per_unit = float(m_val) / float(m_amt)
    return {
        "measure_id": chosen["id"],
        "measure_name": chosen.get("name"),
        "grams": round(qty_val * grams_per_unit, 4),
    }


def normalize_label_nutrients(
    nutrients: dict[str, Any],
    nutrient_index: dict[str, dict[str, Any]],
    *,
    basis_amount: float = 100.0,
    basis_unit: str = "g",
    carbohydrate_basis: str = "unspecified",
    density_g_per_ml: float | None = None,
) -> dict[str, Any]:
    """Normalize package-label nutrients to Cronometer's per-100g wire format.

    Accepts either:
      - `{"energy": {"amount": 250, "unit": "kcal"}, "salt": {"amount": 1.2, "unit": "g"}}`
      - OR plain numeric amounts in each nutrient's catalog unit: `{"energy": 250, "protein": 12}`
        (with `energy_kj` and `salt_g` conveniences supported as well).

    Handles EU/Israeli `excludes_fiber` labels by adding fiber once to yield Cronometer's
    canonical total carbohydrates (which always include fiber).
    """
    basis_val = finite_number(basis_amount, "basis_amount", positive=True)
    if basis_unit == "ml":
        if density_g_per_ml is None:
            raise ValidationError(
                "A per-ml label requires `density_g_per_ml` to convert accurately to grams"
            )
        basis_grams = basis_val * finite_number(density_g_per_ml, "density_g_per_ml", positive=True)
    elif basis_unit == "g":
        basis_grams = basis_val
    else:
        raise ValidationError("basis_unit must be 'g' or 'ml'")

    if carbohydrate_basis not in {"unspecified", "includes_fiber", "excludes_fiber"}:
        raise ValidationError(
            "carbohydrate_basis must be 'includes_fiber', 'excludes_fiber', or 'unspecified'"
        )
    if not isinstance(nutrients, dict) or not nutrients:
        raise ValidationError("Provide at least one nutrient")

    by_id: dict[int, dict[str, Any]] = {meta["id"]: meta for meta in nutrient_index.values()}
    resolved_per_100g: dict[int, float] = {}
    resolved_slug_by_id: dict[int, str] = {}
    notes: list[str] = []
    unknown: list[str] = []

    for raw_key, raw_val in nutrients.items():
        if raw_val is None:
            continue
        if isinstance(raw_val, dict):
            if "amount" not in raw_val or "unit" not in raw_val:
                raise ValidationError(f"Nutrient {raw_key!r} dict must contain 'amount' and 'unit'")
            if raw_val["amount"] is None:
                continue
            amount = finite_number(raw_val["amount"], str(raw_key))
            supplied_unit: str | None = canonical_unit(clean_text(raw_val["unit"], "unit", 20))
        else:
            amount = finite_number(raw_val, str(raw_key))
            supplied_unit = None

        slug = normalize_key(str(raw_key))
        # Handle shorthand convenience keys
        if slug == "energy_kj":
            slug = "energy"
            supplied_unit = "kJ"
        elif slug == "salt_g":
            slug = "salt"
            supplied_unit = "g"

        slug = LABEL_ALIASES.get(slug, slug)

        if slug == "net_carbs":
            raise ValidationError(
                "Pass label `carbs` with `carbohydrate_basis` instead of `net_carbs`"
            )

        # Salt -> Sodium (label convention: salt = sodium * 2.5 => 1 g salt = 400 mg sodium)
        if slug == "salt":
            unit = supplied_unit or "g"
            if unit not in MASS_FACTORS_G:
                raise ValidationError(f"salt requires a mass unit (g/mg/ug), got {unit!r}")
            amount = amount * MASS_FACTORS_G[unit] * 400.0
            slug = "sodium"
            supplied_unit = "mg"
            notes.append("Converted salt to sodium using label standard (salt = sodium × 2.5)")

        # Resolve target nutrient definition
        target_meta: dict[str, Any] | None = None
        if re.fullmatch(r"cronometer_[1-9][0-9]*", slug):
            nid = int(slug.split("_", 1)[1])
            target_meta = by_id.get(nid)
        else:
            target_meta = nutrient_index.get(slug)

        if target_meta is None:
            unknown.append(str(raw_key))
            continue

        nid = target_meta["id"]
        target_unit = canonical_unit(str(target_meta.get("unit") or ""))

        # Convert supplied unit to target catalog unit
        if supplied_unit is not None and supplied_unit != target_unit:
            if target_unit == "kcal" and supplied_unit == "kJ":
                amount = amount / 4.184
                notes.append("Converted energy from kJ to kcal (/ 4.184)")
            elif supplied_unit in MASS_FACTORS_G and target_unit in MASS_FACTORS_G:
                amount = amount * MASS_FACTORS_G[supplied_unit] / MASS_FACTORS_G[target_unit]
            else:
                raise ValidationError(
                    f"Nutrient {raw_key!r} expects unit {target_unit!r}, got {supplied_unit!r}"
                )

        if nid in resolved_per_100g:
            raise ValidationError(
                f"Multiple input fields mapped to the same nutrient ({target_meta['name']}); "
                "pass each nutrient once"
            )

        resolved_per_100g[nid] = amount * 100.0 / basis_grams
        resolved_slug_by_id[nid] = slug

    if unknown:
        raise ValidationError(
            f"Unknown nutrient(s): {', '.join(sorted(unknown))}. "
            "Call list_nutrients to see all supported keys and units."
        )

    # Carbohydrate convention check (Cronometer IDs: carbs=205, fiber=291, sugars=269, sugar_alcohol=10007)
    if 205 in resolved_per_100g:
        if carbohydrate_basis == "unspecified":
            raise ValidationError(
                "Specify carbohydrate_basis ('includes_fiber' for US/CA labels, "
                "'excludes_fiber' for EU/Israeli labels) when carbs are provided"
            )
        if carbohydrate_basis == "excludes_fiber":
            if 291 not in resolved_per_100g:
                raise ValidationError(
                    "Fiber is required when carbohydrate_basis='excludes_fiber' "
                    "(pass fiber=0 if the label lists 0 g fiber)"
                )
            resolved_per_100g[205] += resolved_per_100g[291]
            notes.append("Added fiber once to available carbs for Cronometer canonical total carbs")
        total_carbs = resolved_per_100g[205]
        for sub_id, sub_label in ((291, "fiber"), (269, "sugars"), (10007, "sugar_alcohol")):
            if sub_id in resolved_per_100g and resolved_per_100g[sub_id] > total_carbs + 1e-6:
                raise ValidationError(
                    f"{sub_label} ({resolved_per_100g[sub_id]:.2f} g/100g) exceeds total carbs "
                    f"({total_carbs:.2f} g/100g); check carbohydrate_basis"
                )

    wire_rows = [
        {"id": nid, "amount": round(val, 4)} for nid, val in sorted(resolved_per_100g.items())
    ]
    if not wire_rows:
        raise ValidationError("At least one known nutrient amount is required")

    return {
        "basis_grams": basis_grams,
        "carbohydrate_basis": carbohydrate_basis,
        "label_type": "EU" if carbohydrate_basis == "excludes_fiber" else "AMERICAN_2016",
        "nutrients_wire": wire_rows,
        "notes": notes,
    }
