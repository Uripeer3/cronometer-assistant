"""Core food discovery, inspection, nutrient catalog, connection, and personal aliases."""

from __future__ import annotations

from typing import Any

from ..auth import CronometerSessionClient, validate_timezone
from ..nutrition import ValidationError, clean_text, extract_measures, normalize_key
from ..parsing import extract_key_macros, is_recipe_food
from ..state import AssistantState


class FoodService:
    def __init__(
        self, client: CronometerSessionClient, state: AssistantState | None = None
    ) -> None:
        self.client = client
        self.state = state

    def check_connection(self) -> dict[str, Any]:
        """Verify live Cronometer auth, refresh account timezone, and read key nutrition prefs."""
        profile = self.client.get_profile()
        tz = validate_timezone(profile.get("timezone"))
        if tz != self.client._timezone:
            self.client._timezone = tz
            self.client._save_cached_session()
        self.client._tz_synced = True

        prefs: dict[str, Any] = {}
        for item in profile.get("prefs") or []:
            if isinstance(item, dict):
                prefs.update(item)

        return {
            "connected": True,
            "account_timezone": tz,
            "today": self.client.today().isoformat(),
            "prefs": {
                "net_carbs_enabled": prefs.get("targets.macros.netcarbs") == "true",
                "diary_groups_enabled": prefs.get("DG_ON") == "true",
                "timestamps_enabled": prefs.get("time.on") == "true",
                "gold": bool(prefs.get("gold", False)),
            },
        }

    def search_foods(self, query: str, *, limit: int = 10) -> dict[str, Any]:
        """Search Cronometer foods (or barcodes) and batch-enrich with measures & macros."""
        q = clean_text(query, "query", 300)
        if type(limit) is not int or not 1 <= limit <= 25:
            raise ValidationError("limit must be an integer between 1 and 25")

        raw_rows = self.client.search_food(q)[:limit]
        ids = [r["id"] for r in raw_rows if isinstance(r.get("id"), int)]
        details_by_id = {
            f["id"]: f for f in self.client.get_foods(ids) if isinstance(f, dict) and f.get("id")
        }

        foods: list[dict[str, Any]] = []
        for row in raw_rows:
            fid = row.get("id")
            detail = details_by_id.get(fid, {})
            foods.append(
                {
                    "food_id": fid,
                    "name": row.get("name") or detail.get("name"),
                    "source": row.get("source") or detail.get("source"),
                    "translation_id": row.get("translationId") or 0,
                    "category_id": detail.get("category"),
                    "is_recipe": is_recipe_food(detail),
                    "barcodes": detail.get("barcodes") or [],
                    "measures": extract_measures(detail),
                    "macros_per_100g": extract_key_macros(detail, 1.0),
                }
            )

        matched_aliases = self.state.list_aliases(q) if self.state else []
        return {
            "query": q,
            "count": len(foods),
            "matched_aliases": matched_aliases,
            "foods": foods,
        }

    def get_food_details(self, food_id: int) -> dict[str, Any]:
        """Inspect complete metadata, measures, and nutrients for a single food."""
        if type(food_id) is not int or food_id <= 0:
            raise ValidationError("food_id must be a positive integer")
        raw = self.client.get_food(food_id)
        if not isinstance(raw, dict) or raw.get("id") != food_id:
            raise ValidationError(f"Food {food_id} was not found")

        defs = self.client.get_nutrient_definitions()
        measures = extract_measures(raw)
        default_measure = next(
            (m for m in measures if m["is_default"]), measures[0] if measures else None
        )
        serving_grams = (
            default_measure["grams_per_unit"] * (default_measure["amount"] or 1.0)
            if default_measure and default_measure.get("grams_per_unit")
            else 100.0
        )

        nutrients_per_100g: list[dict[str, Any]] = []
        for row in raw.get("nutrients", []):
            if not isinstance(row, dict):
                continue
            nid = row.get("id")
            amt = row.get("amount")
            if not isinstance(nid, int) or nid <= 0 or amt is None:
                continue
            meta = defs.get(nid, {})
            nutrients_per_100g.append(
                {
                    "id": nid,
                    "name": meta.get("name"),
                    "unit": meta.get("unit"),
                    "category": meta.get("category"),
                    "amount": amt,
                }
            )

        return {
            "food_id": raw["id"],
            "name": raw.get("name"),
            "source": raw.get("source"),
            "category_id": raw.get("category"),
            "label_type": raw.get("labelType"),
            "notes": raw.get("comments"),
            "barcodes": raw.get("barcodes") or [],
            "is_recipe": is_recipe_food(raw),
            "is_custom": raw.get("owner") == self.client.user_id,
            "retired": bool(raw.get("retired")),
            "default_measure_id": raw.get("defaultMeasureId"),
            "default_serving_grams": round(serving_grams, 4),
            "measures": measures,
            "macros_per_100g": extract_key_macros(raw, 1.0),
            "nutrients_per_100g": nutrients_per_100g,
            "missing_fields_are_unknown": True,
        }

    def get_my_foods(
        self,
        *,
        query: str = "",
        include_recent: bool = True,
        include_custom: bool = True,
        include_aliases: bool = True,
    ) -> dict[str, Any]:
        """Fast lookup of saved aliases, recently logged foods, and custom foods/recipes."""
        q_norm = normalize_key(query) if query.strip() else ""
        result: dict[str, Any] = {"query": query}

        if include_aliases:
            result["aliases"] = self.state.list_aliases(query) if self.state else []

        if include_recent:
            recent = self.client.get_recent_foods()
            if q_norm:
                recent = [r for r in recent if q_norm in normalize_key(str(r.get("name") or ""))]
            result["recent_foods"] = recent

        if include_custom:
            result["custom_foods"] = self.client.list_own_foods(query.strip())

        return result

    def list_nutrients(self, *, query: str = "") -> dict[str, Any]:
        """List all writable Cronometer nutrients with canonical keys and catalog units."""
        index = self.client.nutrient_index()
        q_norm = normalize_key(query) if query.strip() else ""
        by_category: dict[str, list[dict[str, Any]]] = {}
        count = 0
        for key, meta in sorted(index.items()):
            if (
                q_norm
                and q_norm not in key
                and q_norm not in normalize_key(str(meta.get("name", "")))
            ):
                continue
            by_category.setdefault(meta.get("category") or "Other", []).append(
                {"key": key, "id": meta["id"], "name": meta["name"], "unit": meta["unit"]}
            )
            count += 1
        return {
            "count": count,
            "categories": by_category,
            "label_conveniences": {
                "energy_kj": "Energy in kJ (converted to kcal via / 4.184)",
                "salt_g / salt": "Salt mass (converted to sodium in mg via salt = sodium × 2.5)",
                "label_aliases": "Also accepts configured localized aliases in nutrition.LABEL_ALIASES (English + Hebrew included out of the box)",
            },
        }

    def remember_food_alias(
        self,
        alias: str,
        food_id: int,
        *,
        translation_id: int = 0,
        default_grams: float | None = None,
        default_measure_id: int | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        if not self.state:
            raise ValidationError("Local state storage is disabled")
        if type(food_id) is not int or food_id <= 0:
            raise ValidationError("food_id must be a positive integer")
        raw = self.client.get_food(food_id)
        if not isinstance(raw, dict) or raw.get("id") != food_id:
            raise ValidationError(f"Food {food_id} not found")
        if default_measure_id is not None:
            valid_ids = {m.get("id") for m in raw.get("measures", []) if isinstance(m, dict)}
            if default_measure_id not in valid_ids:
                raise ValidationError(
                    f"default_measure_id {default_measure_id} not on food {food_id}"
                )
        saved = self.state.set_alias(
            alias,
            food_id=food_id,
            food_name=str(raw.get("name") or f"Food {food_id}"),
            translation_id=translation_id,
            default_grams=default_grams,
            default_measure_id=default_measure_id,
            notes=notes,
        )
        return {"saved": True, "alias": saved}

    def delete_food_alias(self, alias: str) -> dict[str, Any]:
        if not self.state:
            raise ValidationError("Local state storage is disabled")
        return {"alias": alias, "deleted": self.state.delete_alias(alias)}
