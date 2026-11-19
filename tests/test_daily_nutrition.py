import unittest
from datetime import UTC, date, datetime
from unittest.mock import Mock, patch

from cronometer_assistant.daily_nutrition import daily_nutrition
from cronometer_assistant.live import LiveError, LoginRequired
from cronometer_assistant.models import ValidationError


class DailyNutritionTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock(_timezone="Asia/Damascus")
        self.client.get_nutrition_scores.return_value = {
            "scores": [
                {
                    "title": "All Targets",
                    "components": [
                        {"nutrientId": 208, "amount": 159.5},
                        {"nutrientId": 203, "amount": None},
                        {"nutrientId": 204, "amount": 0},
                        {"nutrientId": 401, "amount": 18.4},
                    ],
                }
            ]
        }
        self.client.get_nutrient_definitions.return_value = {
            208: {"name": "Energy", "unit": "kcal"},
            203: {"name": "Protein", "unit": "g"},
            204: {"name": "Fat", "unit": "g"},
            401: {"name": "Vitamin C", "unit": "mg"},
        }

    def test_explicit_day_covers_app_totals_and_retains_unknowns(self):
        result = daily_nutrition(self.client, "2026-11-18")

        self.client.get_nutrition_scores.assert_called_once_with(date(2026, 11, 18))
        self.assertEqual(result["date"], "2026-11-18")
        self.assertEqual(result["account_timezone"], "Asia/Damascus")
        self.assertEqual(result["summary"]["energy"], {"amount": 159.5, "unit": "kcal"})
        self.assertEqual(result["summary"]["protein"], {"amount": None, "unit": "g"})
        self.assertEqual(result["summary"]["fat"], {"amount": 0, "unit": "g"})
        self.assertEqual(result["summary"]["net_carbs"], {"amount": None, "unit": None})
        self.assertEqual(result["nutrients"][3]["amount"], 18.4)
        self.assertEqual(result["nutrients"][3]["unit"], "mg")
        self.assertFalse(result["real_diary_changed"])

    def test_default_day_uses_account_timezone_past_midnight(self):
        moment = datetime(2026, 11, 17, 22, 30, tzinfo=UTC)
        with patch("cronometer_assistant.daily_nutrition.datetime") as clock:
            clock.now.side_effect = moment.astimezone
            result = daily_nutrition(self.client)

        self.assertEqual(result["date"], "2026-11-18")
        self.client.get_nutrition_scores.assert_called_once_with(date(2026, 11, 18))

    def test_invalid_date_does_not_contact_cronometer(self):
        for value in ("2026-02-30", "20261118", "today", 20261118):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                daily_nutrition(self.client, value)
        self.client.get_nutrition_scores.assert_not_called()

    def test_response_error_is_scrubbed_and_expired_session_is_actionable(self):
        self.client.get_nutrition_scores.side_effect = RuntimeError("private upstream payload")
        with self.assertRaisesRegex(LiveError, "Cronometer daily totals could not be read"):
            daily_nutrition(self.client)
        self.client.get_nutrition_scores.side_effect = LoginRequired("run login")
        with self.assertRaisesRegex(LoginRequired, "run login"):
            daily_nutrition(self.client)

    def test_malformed_rows_fail_instead_of_returning_partial_totals(self):
        rows = self.client.get_nutrition_scores.return_value["scores"][0]["components"]
        rows[0]["amount"] = float("nan")
        with self.assertRaisesRegex(LiveError, "invalid daily nutrient amount"):
            daily_nutrition(self.client)
