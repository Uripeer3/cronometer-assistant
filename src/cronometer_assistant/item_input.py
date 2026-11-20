"""Explicit MCP input schema; domain validation remains in the meal service."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MealItemInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    description: str = Field(min_length=1, max_length=1000, description="User's food description.")
    food_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="Selected food ID; null while identity is unresolved. Never invent an ID.",
    )
    grams: float | None = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
        description="Consumed/planned mass in grams, not servings or milliliters; null if unknown.",
    )
    preparation: Literal["raw", "cooked", "as_sold", "unspecified"] = "unspecified"
    quantity_basis: Literal["weighed", "estimated", "user_default"] = "weighed"
    assumption: str | None = Field(
        default=None,
        min_length=1,
        max_length=1000,
        description="Required for estimated/user_default quantities; record the user's accepted assumption.",
    )
    selection_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="Preserve the exact selection_id returned by verify_food_for_draft or meal.items.",
    )
