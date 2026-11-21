"""Account-day nutrition totals reported by Cronometer."""

import math
from datetime import date, datetime
from zoneinfo import ZoneInfo

from .live import LiveError, LoginRequired, fresh_account_timezone
from .models import ValidationError, number

# Stable Cronometer IDs used by the pinned client's daily macro summary.
MACROS = {
    "energy": 208,
    "protein": 203,
    "carbs": 205,
    "net_carbs": -1205,
    "fat": 204,
    "fiber": 291,
    "alcohol": 221,
}


def daily_nutrition(client, day: str | None = None) -> dict:
    """Read the selected day's tracked totals across the entire Cronometer diary."""
    if day is not None:
        try:
            selected_day = date.fromisoformat(day)
            if selected_day.isoformat() != day:
                raise ValueError
        except (ValueError, TypeError):
            raise ValidationError("day must be a real date in YYYY-MM-DD format") from None

    if client._timezone is None:
        raise LoginRequired("Session expired or absent; run login in your terminal")
    timezone = fresh_account_timezone(client)
    if day is None:
        selected_day = datetime.now(ZoneInfo(timezone)).date()

    try:
        scores = client.get_nutrition_scores(selected_day)
        if not isinstance(scores, dict) or not isinstance(scores.get("scores"), list):
            raise ValueError
        if any(not isinstance(row, dict) for row in scores["scores"]):
            raise ValueError
        targets = [row for row in scores["scores"] if row.get("title") == "All Targets"]
        if len(targets) != 1 or not isinstance(targets[0].get("components"), list):
            raise ValueError
        definitions = client.get_nutrient_definitions()
        result = {"nutrients": []}
        for component in targets[0]["components"]:
            if not isinstance(component, dict) or type(component.get("nutrientId")) is not int:
                raise ValueError
            meta = definitions.get(component["nutrientId"], {})
            result["nutrients"].append(
                {
                    "id": component["nutrientId"],
                    "name": meta.get("name"),
                    "unit": meta.get("unit"),
                    "amount": component.get("amount"),
                    "confidence": component.get("confidence"),
                    "tracked": True,
                }
            )
    except LoginRequired:
        raise
    except Exception:
        raise LiveError("Cronometer daily totals could not be read") from None
    if not isinstance(result, dict) or not isinstance(result.get("nutrients"), list):
        raise LiveError("Cronometer returned an invalid daily nutrition response")

    nutrients = {}
    for row in result["nutrients"]:
        if (
            not isinstance(row, dict)
            or type(row.get("id")) is not int
            or row["id"] in nutrients
            or row.get("tracked") is not True
        ):
            raise LiveError("Cronometer returned invalid tracked nutrient data")
        amount = row.get("amount")
        try:
            if amount is not None:
                amount = number(amount, "Cronometer daily nutrient amount")
        except (ValidationError, OverflowError):
            raise LiveError("Cronometer returned an invalid daily nutrient amount") from None
        unit = row.get("unit")
        name = row.get("name")
        confidence = row.get("confidence")
        nutrients[row["id"]] = {
            "nutrient_id": row["id"],
            "name": name if isinstance(name, str) and name else None,
            "unit": unit if isinstance(unit, str) and unit else None,
            "amount": amount if isinstance(unit, str) and unit else None,
            "confidence": confidence
            if type(confidence) in (int, float) and math.isfinite(confidence)
            else None,
        }

    summary = {}
    for name, nutrient_id in MACROS.items():
        row = nutrients.get(nutrient_id, {})
        summary[name] = {"amount": row.get("amount"), "unit": row.get("unit")}

    return {
        "date": selected_day.isoformat(),
        "account_timezone": timezone,
        "summary": summary,
        "nutrients": list(nutrients.values()),
        "tracked_count": len(nutrients),
        "source": "Cronometer diary totals for tracked nutrients",
        "coverage_note": "These totals use the whole diary day. Missing values are unknown and "
        "totals depend on nutrient data in the foods logged. Untracked nutrients are excluded.",
        "real_diary_changed": False,
    }
