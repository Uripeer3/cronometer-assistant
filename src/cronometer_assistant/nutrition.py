"""Label normalization and nutrient summaries that keep incomplete data visible."""

from .models import CORE_UNITS, Food, MealItem, Nutrient, ValidationError, number, text

LABEL_NAMES = {
    "חלבון": "protein",
    "פחמימות": "carbs",
    "שומנים": "fat",
    "נתרן": "sodium",
    "סיבים תזונתיים": "fiber",
    "אנרגיה": "energy",
    "carbohydrates": "carbs",
    "calories": "energy",
}


def normalize_label(
    name: str,
    basis_amount: float,
    basis_unit: str,
    nutrients: dict,
    density_g_per_ml: float | None = None,
) -> dict:
    """Validate model-extracted label fields; this function does not read images."""
    name = text(name, "product name")
    basis = number(basis_amount, "label basis", positive=True)
    if basis_unit == "ml":
        if density_g_per_ml is None:
            raise ValidationError("A per-ml label needs a known density before conversion to grams")
        basis *= number(density_g_per_ml, "density", positive=True)
    elif basis_unit != "g":
        raise ValidationError("Label basis must be g or ml")
    if not nutrients or len(nutrients) > 150:
        raise ValidationError("Provide 1–150 label nutrients")
    result = {}
    notes = []
    mass = {"g": 1, "mg": 0.001, "ug": 0.000001}
    for original, row in nutrients.items():
        key = LABEL_NAMES.get(original, original)
        text(key, "nutrient key", 100)
        if not isinstance(row, dict) or set(row) != {"amount", "unit"}:
            raise ValidationError("Each nutrient requires exactly amount and unit")
        unit = row["unit"]
        amount = row["amount"]
        if amount is not None:
            amount = number(amount, key)
        if key == "salt":
            if unit not in mass:
                raise ValidationError("Salt requires a mass unit")
            # Food-label salt equivalent convention: salt = sodium × 2.5.
            amount = None if amount is None else amount * mass[unit] * 400
            key, unit = "sodium", "mg"
            notes.append("Converted label salt equivalent to sodium using salt = sodium × 2.5")
        elif key == "energy":
            if unit not in {"kcal", "kJ"}:
                raise ValidationError("Energy requires kcal or kJ")
            if unit == "kJ" and amount is not None:
                amount /= 4.184
            unit = "kcal"
        else:
            target = CORE_UNITS.get(key, unit)
            if unit not in mass or target not in mass:
                raise ValidationError("Non-energy nutrients require a supported mass unit")
            amount = None if amount is None else amount * mass[unit] / mass[target]
            unit = target
        if key in result:
            raise ValidationError(f"Multiple label fields map to {key}; resolve them explicitly")
        result[key] = Nutrient(None if amount is None else amount * 100 / basis, unit, "label")
    return {
        "name": name,
        "basis_grams": basis,
        "conversion_notes": notes,
        "nutrients_per_100g": {
            key: {"amount": n.amount, "unit": n.unit, "provenance": n.provenance}
            for key, n in result.items()
        },
        "missing_fields_are_unknown": True,
    }


def summarize(items: list[tuple[MealItem, Food]]) -> dict:
    keys = set(CORE_UNITS)
    for _, food in items:
        keys.update(food.nutrients_per_100g)
    result = {}
    for key in sorted(keys):
        unit = CORE_UNITS.get(key)
        total = 0.0
        known = 0
        missing = []
        for item, food in items:
            n = food.nutrients_per_100g.get(key)
            if n is None or n.amount is None or item.grams is None:
                missing.append(item.description)
                continue
            if unit is not None and unit != n.unit:
                raise ValidationError(f"Inconsistent stored units for {key}")
            unit = n.unit
            total += n.amount * item.grams / 100
            known += 1
        result[key] = {
            "known_amount": round(total, 6) if known else None,
            "unit": unit,
            "complete": bool(items) and not missing,
            "missing_from": missing,
        }
    return result


def portion_ingredients(ingredients: list[dict], batch_grams: float, portion_grams: float) -> list:
    batch = number(batch_grams, "finished batch weight", positive=True)
    portion = number(portion_grams, "portion weight", positive=True)
    if portion > batch:
        raise ValidationError("A portion cannot exceed this batch")
    if not ingredients:
        raise ValidationError("A recipe needs ingredients")
    result = []
    for row in ingredients:
        item = MealItem(**row)
        if item.grams is None:
            raise ValidationError("All recipe ingredients need a quantity")
        result.append({**item.to_dict(), "grams": item.grams * portion / batch})
    return result
