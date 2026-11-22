"""Unit tests for nutrition normalization, Hebrew/English labels, barcodes, and measure math."""

from __future__ import annotations

import unittest

from cronometer_assistant.nutrition import (
    ValidationError,
    extract_measures,
    normalize_label_nutrients,
    resolve_serving_amount,
    validate_barcode,
)

SAMPLE_INDEX = {
    "energy": {"id": 208, "name": "Energy", "unit": "kcal", "category": "General"},
    "protein": {"id": 203, "name": "Protein", "unit": "g", "category": "Protein"},
    "carbs": {"id": 205, "name": "Carbs", "unit": "g", "category": "Carbohydrates"},
    "fiber": {"id": 291, "name": "Fiber", "unit": "g", "category": "Carbohydrates"},
    "sugars": {"id": 269, "name": "Sugars", "unit": "g", "category": "Carbohydrates"},
    "sugar_alcohol": {
        "id": 10007,
        "name": "Sugar Alcohol",
        "unit": "g",
        "category": "Carbohydrates",
    },
    "fat": {"id": 204, "name": "Fat", "unit": "g", "category": "Lipids"},
    "saturated": {"id": 606, "name": "Saturated", "unit": "g", "category": "Lipids"},
    "trans_fats": {"id": 605, "name": "Trans-Fats", "unit": "g", "category": "Lipids"},
    "cholesterol": {"id": 601, "name": "Cholesterol", "unit": "mg", "category": "Lipids"},
    "sodium": {"id": 307, "name": "Sodium", "unit": "mg", "category": "Minerals"},
    "calcium": {"id": 301, "name": "Calcium", "unit": "mg", "category": "Minerals"},
    "vitamin_d": {"id": 324, "name": "Vitamin D", "unit": "IU", "category": "Vitamins"},
    "b12_cobalamin": {"id": 418, "name": "B12 (Cobalamin)", "unit": "µg", "category": "Vitamins"},
}


class NutritionTests(unittest.TestCase):
    def test_israeli_hebrew_label_excludes_fiber_and_converts_units(self):
        result = normalize_label_nutrients(
            {
                "אנרגיה": {"amount": 418.4, "unit": "kJ"},
                "חלבון": {"amount": 10, "unit": "g"},
                "פחמימות": {"amount": 30, "unit": "g"},
                "סיבים תזונתיים": {"amount": 6, "unit": "g"},
                "מתוכן סוכרים": {"amount": 4, "unit": "g"},
                "רב כהליים": {"amount": 5, "unit": "g"},
                "שומנים": {"amount": 8, "unit": "g"},
                "שומן רווי": {"amount": 2.5, "unit": "g"},
                "שומן טראנס": {"amount": 0.1, "unit": "g"},
                "מלח": {"amount": 1.0, "unit": "g"},
                "סידן": {"amount": 0.12, "unit": "g"},
                "ויטמין B12": {"amount": 2.4, "unit": "µg"},
                "ויטמין D": {"amount": 400, "unit": "IU"},
            },
            SAMPLE_INDEX,
            basis_amount=50,
            basis_unit="g",
            carbohydrate_basis="excludes_fiber",
        )
        self.assertEqual(result["label_type"], "EU")
        by_id = {row["id"]: row["amount"] for row in result["nutrients_wire"]}
        # Scaled from 50g -> 100g (x2)
        self.assertAlmostEqual(by_id[208], 200.0, places=3)  # 418.4 kJ = 100 kcal -> 200 kcal/100g
        self.assertAlmostEqual(by_id[203], 20.0)
        self.assertAlmostEqual(by_id[205], 72.0)  # (30 + 6) * 2 = 72g canonical total carbs
        self.assertAlmostEqual(by_id[291], 12.0)
        self.assertAlmostEqual(by_id[269], 8.0)
        self.assertAlmostEqual(by_id[10007], 10.0)
        self.assertAlmostEqual(by_id[204], 16.0)
        self.assertAlmostEqual(by_id[606], 5.0)
        self.assertAlmostEqual(by_id[605], 0.2)
        self.assertAlmostEqual(by_id[307], 800.0)  # 1g salt = 400mg Na -> 800mg/100g
        self.assertAlmostEqual(by_id[301], 240.0)  # 0.12g = 120mg -> 240mg/100g
        self.assertAlmostEqual(by_id[418], 4.8)
        self.assertAlmostEqual(by_id[324], 800.0)

    def test_excludes_fiber_requires_fiber_field(self):
        with self.assertRaises(ValidationError):
            normalize_label_nutrients(
                {"carbs": 20},
                SAMPLE_INDEX,
                carbohydrate_basis="excludes_fiber",
            )

    def test_carbs_requires_explicit_basis(self):
        with self.assertRaises(ValidationError):
            normalize_label_nutrients({"carbs": 20}, SAMPLE_INDEX, carbohydrate_basis="unspecified")

    def test_pure_fat_food_does_not_require_carb_basis(self):
        res = normalize_label_nutrients(
            {"energy": 884, "fat": 100, "saturated_fat": 14},
            SAMPLE_INDEX,
            carbohydrate_basis="unspecified",
        )
        self.assertEqual(res["label_type"], "AMERICAN_2016")
        self.assertEqual(len(res["nutrients_wire"]), 3)

    def test_ml_basis_requires_density(self):
        with self.assertRaises(ValidationError):
            normalize_label_nutrients(
                {"energy": 50}, SAMPLE_INDEX, basis_amount=100, basis_unit="ml"
            )
        res = normalize_label_nutrients(
            {"energy": 50},
            SAMPLE_INDEX,
            basis_amount=100,
            basis_unit="ml",
            density_g_per_ml=1.03,
        )
        by_id = {row["id"]: row["amount"] for row in res["nutrients_wire"]}
        self.assertAlmostEqual(by_id[208], 50 / 1.03, places=3)

    def test_unknown_nutrient_and_duplicate_salt_sodium_rejected(self):
        with self.assertRaises(ValidationError):
            normalize_label_nutrients({"magic_dust": 5}, SAMPLE_INDEX)
        with self.assertRaises(ValidationError):
            normalize_label_nutrients({"salt_g": 1.0, "sodium": 400}, SAMPLE_INDEX)

    def test_barcode_validation(self):
        self.assertEqual(validate_barcode("7290014760141"), "7290014760141")
        with self.assertRaises(ValidationError):
            validate_barcode("7290014760142")  # wrong check digit
        with self.assertRaises(ValidationError):
            validate_barcode("12345")

    def test_extract_measures_and_resolve_multi_unit_and_atomic(self):
        pretzels = {
            "id": 72014363,
            "defaultMeasureId": 101,
            "measures": [
                {
                    "id": 101,
                    "name": "pieces",
                    "type": "Weight",
                    "amount": 22,
                    "value": 30,
                    "hidden": False,
                },
                {
                    "id": 102,
                    "name": "g",
                    "type": "Weight",
                    "amount": 1,
                    "value": 1,
                    "hidden": False,
                },
            ],
        }
        measures = extract_measures(pretzels)
        self.assertAlmostEqual(measures[0]["grams_per_unit"], 30 / 22, places=5)

        resolved_qty = resolve_serving_amount(pretzels, quantity=11, measure_name="pieces")
        self.assertEqual(resolved_qty["measure_id"], 101)
        self.assertAlmostEqual(resolved_qty["grams"], 15.0, places=3)

        resolved_g = resolve_serving_amount(pretzels, grams=45)
        self.assertEqual(resolved_g["measure_id"], 102)
        self.assertEqual(resolved_g["grams"], 45.0)

        # Atomic-only food (e.g. Tnuva almond drink or 1 large egg)
        atomic_egg = {
            "id": 999,
            "defaultMeasureId": 201,
            "measures": [
                {
                    "id": 201,
                    "name": "large",
                    "type": "Atomic",
                    "amount": 1,
                    "value": 50,
                    "hidden": False,
                },
            ],
        }
        r_egg_qty = resolve_serving_amount(atomic_egg, quantity=2)
        self.assertEqual(r_egg_qty["measure_id"], 201)
        self.assertAlmostEqual(r_egg_qty["grams"], 100.0)

        r_egg_g = resolve_serving_amount(atomic_egg, grams=60)
        self.assertEqual(r_egg_g["measure_id"], 201)
        self.assertEqual(r_egg_g["grams"], 60.0)


if __name__ == "__main__":
    unittest.main()
