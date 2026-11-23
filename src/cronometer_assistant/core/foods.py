"""Core food discovery, inspection, nutrient catalog, connection, and personal aliases."""

from __future__ import annotations

from typing import Any

from ..auth import CronometerSessionClient
from ..nutrition import ValidationError, clean_text, extract_measures, normalize_key
from ..parsing import (
    extract_key_macros,
    is_batch_recipe,
    is_recipe_food,
    parse_weight_recipe_ingredient_footer,
    per_100g_nutrient_factor,
)
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
        tz = self.client.apply_profile_timezone(profile.get("timezone"))

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
                    "macros_per_100g": extract_key_macros(detail),
                }
            )

        matched_aliases = self.state.list_aliases(q) if self.state else []
        return {
            "query": q,
            "count": len(foods),
            "matched_aliases": matched_aliases,
            "foods": foods,
        }

    def _extract_recipe_ingredients(
        self, raw: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], float | None]:
        """Resolve recipe ingredients (native list or weight-recipe notes summary) with food names."""
        raw_ings = [i for i in (raw.get("ingredients") or []) if isinstance(i, dict)]
        parsed_from_notes = (
            parse_weight_recipe_ingredient_footer(raw.get("comments"))
            if not raw_ings and is_recipe_food(raw)
            else []
        )

        if raw_ings:
            ing_fids = sorted(
                {
                    i["foodId"]
                    for i in raw_ings
                    if isinstance(i.get("foodId"), int) and i["foodId"] > 0
                }
            )
            foods_by_id = (
                {
                    f["id"]: f
                    for f in self.client.get_foods(ing_fids)
                    if isinstance(f, dict) and f.get("id")
                }
                if ing_fids
                else {}
            )
            out: list[dict[str, Any]] = []
            total = 0.0
            for row in raw_ings:
                fid = row.get("foodId")
                ing_food = foods_by_id.get(fid, {})
                mid = row.get("measureId")
                measure_name = next(
                    (
                        m.get("name")
                        for m in ing_food.get("measures", [])
                        if isinstance(m, dict) and m.get("id") == mid
                    ),
                    None,
                )
                grams = row.get("grams")
                if isinstance(grams, (int, float)):
                    total += float(grams)
                out.append(
                    {
                        "food_id": fid,
                        "food_name": ing_food.get("name"),
                        "grams": round(float(grams), 2)
                        if isinstance(grams, (int, float))
                        else None,
                        "measure_id": mid,
                        "measure_name": measure_name,
                    }
                )
            return out, round(total, 2) if total > 0 else None

        if parsed_from_notes:
            ing_fids = sorted({fid for fid, _ in parsed_from_notes})
            foods_by_id = {
                f["id"]: f
                for f in self.client.get_foods(ing_fids)
                if isinstance(f, dict) and f.get("id")
            }
            out = [
                {
                    "food_id": fid,
                    "food_name": foods_by_id.get(fid, {}).get("name"),
                    "grams": round(grams, 2),
                }
                for fid, grams in parsed_from_notes
            ]
            total = sum(grams for _, grams in parsed_from_notes)
            return out, round(total, 2) if total > 0 else None

        return [], None

    def _list_own_foods(self, query: str = "") -> list[dict[str, Any]]:
        """List owned custom foods & recipes with accurate recipe detection (incl. app recipes)."""
        rows = self.client.request_v2(
            "/api/v2/find_food",
            {
                "query": query,
                "tab": "CUSTOM",
                "sources": ["All"],
                "config": {"newSearch": True, "newSpellcheck": True, "call_version": 1},
            },
        ).get("foods", [])
        ids = [r["id"] for r in rows if isinstance(r, dict) and isinstance(r.get("id"), int)]
        details = (
            {f["id"]: f for f in self.client.get_foods(ids) if isinstance(f, dict) and f.get("id")}
            if ids
            else {}
        )
        out: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            detail = details.get(row.get("id"), {})
            if bool(detail.get("retired")):
                continue
            recipe = is_recipe_food(detail)
            serving_type = ("servings" if is_batch_recipe(detail) else "weight") if recipe else None
            item: dict[str, Any] = {
                "food_id": row.get("id"),
                "name": row.get("name"),
                "measure_id": row.get("measureId"),
                "measure_name": row.get("measureDisplayName"),
                "is_recipe": recipe,
                "serving_type": serving_type,
                "retired": False,
                "notes": detail.get("comments"),
            }
            if recipe and detail.get("ingredients"):
                item["ingredient_count"] = len(detail["ingredients"])
            out.append(item)
        return out

    def get_food_details(self, food_id: int) -> dict[str, Any]:
        """Inspect complete metadata, measures, recipe ingredients, and nutrients for a food."""
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

        factor = per_100g_nutrient_factor(raw)
        nutrients_per_100g: list[dict[str, Any]] = []
        if factor is not None:
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
                        "amount": round(float(amt) * factor, 4) if factor != 1.0 else amt,
                    }
                )

        recipe = is_recipe_food(raw)
        serving_type = ("servings" if is_batch_recipe(raw) else "weight") if recipe else None
        out: dict[str, Any] = {
            "food_id": raw["id"],
            "name": raw.get("name"),
            "source": raw.get("source"),
            "category_id": raw.get("category"),
            "label_type": raw.get("labelType"),
            "notes": raw.get("comments"),
            "barcodes": raw.get("barcodes") or [],
            "is_recipe": recipe,
            "serving_type": serving_type,
            "is_custom": raw.get("owner") == self.client.user_id,
            "retired": bool(raw.get("retired")),
            "default_measure_id": raw.get("defaultMeasureId"),
            "default_serving_grams": round(serving_grams, 4),
            "measures": measures,
            "macros_per_100g": extract_key_macros(raw),
            "nutrients_per_100g": nutrients_per_100g,
            "missing_fields_are_unknown": True,
        }
        if recipe:
            ingredients, raw_ing_grams = self._extract_recipe_ingredients(raw)
            out["ingredients"] = ingredients
            if raw_ing_grams is not None:
                out["raw_ingredients_grams"] = raw_ing_grams
            cooked_prop = (raw.get("properties") or {}).get("cooked_weight")
            if cooked_prop is not None:
                try:
                    out["cooked_weight_grams"] = round(float(cooked_prop), 2)
                except (TypeError, ValueError):
                    pass
        return out

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
            result["custom_foods"] = self._list_own_foods(query.strip())

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
