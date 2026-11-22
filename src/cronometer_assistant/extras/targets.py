"""Secondary nutrition analytics & settings: nutrient targets, macro schedules, scores, streaks."""

from __future__ import annotations

from datetime import date
from typing import Any

from ..auth import CronometerSessionClient
from ..nutrition import LABEL_ALIASES, ValidationError, clean_text, finite_number, normalize_key
from ..parsing import parse_day


class TargetsService:
    def __init__(self, client: CronometerSessionClient) -> None:
        self.client = client

    def _resolve_day(self, day_str: str | None) -> date:
        parsed = parse_day(day_str)
        if parsed is not None:
            return parsed
        self.client.sync_timezone()
        return self.client.today()

    def get_targets(self, *, query: str = "") -> dict[str, Any]:
        """Read nutrient targets (min, max, custom, visible) enriched with names and units."""
        defs = self.client.get_nutrient_definitions()
        q_norm = normalize_key(query) if query.strip() else ""
        rows: list[dict[str, Any]] = []
        for row in self.client.get_targets().get("targets", []):
            if not isinstance(row, dict):
                continue
            meta = defs.get(row.get("id"), {})
            name = meta.get("name") or ""
            if q_norm and q_norm not in normalize_key(name):
                continue
            rows.append(
                {
                    "nutrient_id": row.get("id"),
                    "name": name or None,
                    "unit": meta.get("unit"),
                    "category": meta.get("category"),
                    "min": row.get("min"),
                    "max": row.get("max"),
                    "custom": bool(row.get("custom", False)),
                    "visible": bool(row.get("vis", True)),
                }
            )
        return {"count": len(rows), "targets": rows}

    def set_nutrient_target(
        self,
        nutrient: str,
        *,
        minimum: float | None = None,
        maximum: float | None = None,
        visible: bool | None = None,
    ) -> dict[str, Any]:
        """Set a nutrient's minimum target, upper limit, or tracking visibility (merges existing)."""
        if minimum is None and maximum is None and visible is None:
            raise ValidationError("Provide at least one of minimum, maximum, or visible")
        if minimum is not None:
            minimum = finite_number(minimum, "minimum")
        if maximum is not None:
            maximum = finite_number(maximum, "maximum")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValidationError("minimum cannot exceed maximum")

        raw = clean_text(nutrient, "nutrient", 100)
        slug = LABEL_ALIASES.get(normalize_key(raw), normalize_key(raw))
        index = self.client.nutrient_index()
        entry = index.get(slug)
        if entry is None:
            raise ValidationError(
                f"Unknown nutrient {raw!r}. Call list_nutrients to see valid keys."
            )
        previous = self.client.find_target(entry["id"])
        eff_min = minimum if minimum is not None else (previous.get("min") if previous else None)
        eff_max = maximum if maximum is not None else (previous.get("max") if previous else None)
        if eff_min is not None and eff_max is not None and float(eff_min) > float(eff_max):
            raise ValidationError(
                f"Resulting target range is invalid: minimum ({eff_min}) exceeds maximum ({eff_max})"
            )
        result = self.client.set_target(slug, minimum=minimum, maximum=maximum, visible=visible)
        return {**result, "previous": previous}

    def get_macro_targets(self) -> dict[str, Any]:
        """Read weekly macro target schedules and saved macro templates."""
        return {
            "schedules": self.client.get_macro_schedules(),
            "templates": self.client.get_macro_target_templates(),
        }

    def get_nutrition_scores(self, day: str | None = None) -> dict[str, Any]:
        """Read Cronometer's category nutrition scores (Vitamins, Minerals, Electrolytes, etc.)."""
        target_day = self._resolve_day(day)
        raw = self.client.get_nutrition_scores(target_day)
        categories = []
        for row in raw.get("scores", []) if isinstance(raw, dict) else []:
            if not isinstance(row, dict):
                continue
            categories.append(
                {
                    "title": row.get("title"),
                    "score": row.get("score"),
                    "component_count": len(row.get("components") or []),
                    "components": row.get("components") or [],
                }
            )
        return {
            "date": target_day.isoformat(),
            "category_count": len(categories),
            "scores": categories,
        }

    def get_streak(self, day: str | None = None) -> dict[str, Any]:
        """Read current and longest diary logging streaks as of `day`."""
        target_day = self._resolve_day(day)
        data = self.client.get_streak(target_day)
        return {"date": target_day.isoformat(), "streak": data}
