"""Catalog policy and a read-only adapter around the pinned upstream client."""

from typing import Protocol

from .models import Food, Nutrient, ValidationError, normalized
from .store import Store


class Catalog(Protocol):
    def search(self, query: str) -> list[Food]: ...

    def get(self, food_id: str) -> Food: ...


def demo_foods() -> list[Food]:
    """Invented test profiles. Source names exercise policy, not database authenticity."""

    def food(fid, name, source, prep, aliases, values, brand=None):
        return Food(
            fid,
            name,
            source,
            {key: Nutrient(amount, unit, "synthetic") for key, (amount, unit) in values.items()},
            prep,
            brand,
            tuple(aliases),
            True,
        )

    return [
        food(
            "demo:rice-cooked",
            "Rice, cooked — synthetic",
            "NCCDB",
            "cooked",
            ["אורז מבושל", "rice", "אורז"],
            {
                "energy": (130, "kcal"),
                "protein": (2.7, "g"),
                "carbs": (28, "g"),
                "fat": (0.3, "g"),
                "fiber": (0.4, "g"),
                "sodium": (1, "mg"),
            },
        ),
        food(
            "demo:rice-raw",
            "Rice, dry — synthetic",
            "USDA",
            "raw",
            ["אורז יבש", "rice", "אורז"],
            {
                "energy": (360, "kcal"),
                "protein": (7, "g"),
                "carbs": (80, "g"),
                "fat": (1, "g"),
                "fiber": (1, "g"),
                "sodium": (0, "mg"),
            },
        ),
        food(
            "demo:egg",
            "Egg, whole, raw — synthetic",
            "USDA",
            "raw",
            ["egg", "eggs", "ביצה", "ביצים"],
            {
                "energy": (140, "kcal"),
                "protein": (12, "g"),
                "carbs": (1, "g"),
                "fat": (10, "g"),
                "sodium": (140, "mg"),
            },
        ),
        food(
            "demo:yogurt",
            "Example yogurt 3% — synthetic",
            "BRANDED",
            "as_sold",
            ["יוגורט", "yogurt"],
            {
                "energy": (65, "kcal"),
                "protein": (5, "g"),
                "carbs": (4, "g"),
                "fat": (3, "g"),
                "sodium": (45, "mg"),
            },
            "Example",
        ),
        food(
            "demo:oil",
            "Olive oil — synthetic",
            "NCCDB",
            "as_sold",
            ["שמן זית", "olive oil", "oil"],
            {
                "energy": (880, "kcal"),
                "protein": (0, "g"),
                "carbs": (0, "g"),
                "fat": (100, "g"),
                "fiber": (0, "g"),
                "sodium": (0, "mg"),
            },
        ),
    ]


class DemoCatalog:
    def __init__(self, store: Store):
        self.store = store
        for food in demo_foods():
            if self.store.get("foods", food.food_id) is None:
                self.store.put("foods", food.food_id, food.to_dict())

    def get(self, food_id: str) -> Food:
        data = self.store.get("foods", food_id)
        if data is None:
            raise ValidationError("Food is not in this catalog; search or select it first")
        return Food.from_dict(data)

    def search(self, query: str) -> list[Food]:
        tokens = set(normalized(query).split())
        return [
            food
            for food in map(Food.from_dict, self.store.values("foods"))
            if tokens & set(normalized(" ".join((food.name, *food.aliases))).split())
        ]


def rank_foods(
    candidates: list[Food],
    query: str,
    *,
    preparation: str | None = None,
    packaged: bool = False,
    brand: str | None = None,
    remembered_id: str | None = None,
) -> list[dict]:
    """Identity first; source coverage is a tie-breaker, never a substitute for identity."""
    tokens = set(normalized(query).split())
    ranked = []
    for food in candidates:
        if preparation and food.preparation not in {preparation, "unspecified"}:
            continue
        names = [normalized(food.name), *(normalized(a) for a in food.aliases)]
        overlap = max(
            (len(tokens & set(n.split())) / max(len(tokens), 1) for n in names), default=0
        )
        if not overlap and food.food_id != remembered_id:
            continue
        identity = 1 if normalized(query) in names else overlap
        remembered = int(food.food_id == remembered_id)
        brand_match = int(bool(brand) and normalized(food.brand or "") == normalized(brand))
        if brand and not brand_match:
            continue
        preferred_source = int(
            (packaged and food.source in {"BRANDED", "USER_LABEL"})
            or (not packaged and food.source in {"NCCDB", "USDA"})
        )
        reasons = []
        if remembered:
            reasons.append("Previously verified alias")
        if preferred_source:
            reasons.append("Preferred source for this food type")
        if food.preparation == "unspecified":
            reasons.append("Preparation is not established")
        ranked.append(
            (
                (remembered, brand_match, identity, preferred_source),
                {**food.to_dict(), "reasons": reasons},
            )
        )
    ranked.sort(key=lambda row: row[0], reverse=True)
    return [row[1] for row in ranked]


class CronometerReadOnlyCatalog:
    """No diary-write or login UI. Pass an authenticated upstream client explicitly.

    Numeric source codes require a verified mapping supplied by the caller.
    Read raw nutrient rows to avoid the upstream helper's missing-value defaults.
    """

    def __init__(self, client, source_map: dict[str, str] | None = None):
        self.client = client
        self.source_map = source_map or {}

    def _food(self, raw: dict, source=None) -> Food:
        definitions = self.client.get_nutrient_definitions()
        nutrients = {}
        canonical = {
            208: "energy",
            203: "protein",
            205: "carbs",
            204: "fat",
            291: "fiber",
            307: "sodium",
        }
        for row in raw.get("nutrients", []):
            meta = definitions.get(row.get("id"))
            if meta is None:
                continue
            key = canonical.get(row["id"], f"cronometer_{row['id']}")
            unit = meta.get("unit")
            unit = {"µg": "ug", "μg": "ug"}.get(unit, unit)
            nutrients[key] = Nutrient(row.get("amount"), unit)
        source = raw.get("source", source)
        source_key = str(source)
        source_name = self.source_map.get(source_key, source_key)
        if source_name not in {"NCCDB", "USDA", "BRANDED", "USER_LABEL"}:
            source_name = f"UNVERIFIED:{source_key}"
        return Food(
            f"crono:{raw['id']}",
            raw["name"],
            source_name,
            nutrients,
            preparation="unspecified",
        )

    def search(self, query: str) -> list[Food]:
        # Limit detail calls. The caller can refine its search rather than retrieving the catalog.
        return [
            self._food(self.client.get_food(row["id"]), row.get("source"))
            for row in self.client.search_food(query)[:10]
        ]

    def get(self, food_id: str) -> Food:
        if not food_id.startswith("crono:"):
            raise ValidationError("Expected a Cronometer food ID")
        try:
            numeric_id = int(food_id.split(":", 1)[1])
        except ValueError as error:
            raise ValidationError("Malformed Cronometer food ID") from error
        return self._food(self.client.get_food(numeric_id))
