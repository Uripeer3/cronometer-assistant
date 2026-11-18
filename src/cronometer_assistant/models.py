"""Small validated models; all stored nutrients have an explicit quantity basis."""

import math
import re
import unicodedata
from dataclasses import asdict, dataclass, field


class ValidationError(ValueError):
    """Input needs correction or clarification before it can be used."""


def number(value: object, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{name} must be a number")
    result = float(value)
    if not math.isfinite(result) or result < 0 or (positive and result == 0):
        raise ValidationError(
            f"{name} must be finite and {'positive' if positive else 'nonnegative'}"
        )
    return result


def text(value: object, name: str, maximum: int = 1000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValidationError(f"{name} must contain 1–{maximum} characters")
    return value.strip()


def normalized(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^\w\s%]", "", value).strip()


UNITS = {"g", "mg", "ug", "kcal", "kJ", "IU"}
CORE_UNITS = {
    "energy": "kcal",
    "protein": "g",
    "carbs": "g",
    "fat": "g",
    "fiber": "g",
    "sodium": "mg",
    "sugars": "g",
    "sugar_alcohol": "g",
    "saturated_fat": "g",
    "trans_fat": "g",
    "cholesterol": "mg",
}


@dataclass(frozen=True)
class Nutrient:
    amount: float | None
    unit: str
    provenance: str = "database"

    def __post_init__(self):
        if self.unit not in UNITS:
            raise ValidationError(f"Unsupported nutrient unit: {self.unit}")
        if self.amount is not None:
            number(self.amount, "nutrient amount")
        if self.provenance not in {"database", "label", "estimate", "synthetic"}:
            raise ValidationError("Unsupported nutrient provenance")


@dataclass(frozen=True)
class Food:
    food_id: str
    name: str
    source: str
    nutrients_per_100g: dict[str, Nutrient]
    preparation: str = "unspecified"
    brand: str | None = None
    aliases: tuple[str, ...] = ()
    synthetic: bool = False
    translation_id: int = 0

    def __post_init__(self):
        text(self.food_id, "food_id", 200)
        text(self.name, "food name")
        text(self.source, "source", 200)
        if self.preparation not in {"raw", "cooked", "as_sold", "unspecified"}:
            raise ValidationError("Unknown food preparation")
        for key, nutrient in self.nutrients_per_100g.items():
            if key in CORE_UNITS and nutrient.unit != CORE_UNITS[key]:
                raise ValidationError(f"{key} must use {CORE_UNITS[key]}")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> "Food":
        data = dict(value)
        data["nutrients_per_100g"] = {
            key: Nutrient(**row) for key, row in data["nutrients_per_100g"].items()
        }
        data["aliases"] = tuple(data.get("aliases", []))
        return cls(**data)


@dataclass(frozen=True)
class MealItem:
    description: str
    food_id: str | None = None
    grams: float | None = None
    preparation: str = "unspecified"
    quantity_basis: str = "weighed"
    assumption: str | None = None

    def __post_init__(self):
        text(self.description, "description")
        if self.food_id is not None:
            text(self.food_id, "food_id", 200)
        if self.grams is not None:
            number(self.grams, "grams", positive=True)
        if self.preparation not in {"raw", "cooked", "as_sold", "unspecified"}:
            raise ValidationError("Unknown item preparation")
        if self.quantity_basis not in {"weighed", "estimated", "user_default"}:
            raise ValidationError("Unknown quantity basis")
        if self.quantity_basis != "weighed" and not self.assumption:
            raise ValidationError("An estimate or default needs a recorded assumption")
        if self.assumption is not None:
            text(self.assumption, "assumption")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class DiaryEntry:
    entry_id: str
    food_id: str
    grams: float
    day: str
    meal_group: str
    local_time: str | None = None
    measure_id: int | None = None
    translation_id: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Review:
    questions: list[dict] = field(default_factory=list)
    entries: list[dict] = field(default_factory=list)
    nutrients: dict[str, dict] = field(default_factory=dict)
