"""Core everyday meal-logging MCP tools (flat schemas compatible with ChatGPT, Claude, Gemini)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from ..service import CronometerAssistant
from . import DESTRUCTIVE, IDEMPOTENT_WRITE, READ, WRITE


class LogFoodEntryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    food_id: int = Field(gt=0, description="Cronometer food_id from search_foods or get_my_foods.")
    grams: float | None = Field(
        default=None,
        gt=0,
        description="Weight in grams. Provide either `grams` OR `quantity`, not both.",
    )
    quantity: float | None = Field(
        default=None,
        gt=0,
        description="Number of units of `measure_id` / `measure_name` (or the food's default measure).",
    )
    measure_id: int | None = Field(
        default=None,
        gt=0,
        description="Optional measure_id from search_foods / get_food_details.",
    )
    measure_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        description="Optional exact measure name (e.g. 'cup', 'large', 'serving', 'g').",
    )
    translation_id: int = Field(
        default=0,
        ge=0,
        description="translation_id from search_foods (0 if none).",
    )
    meal_group: str | None = Field(
        default=None,
        description="Optional per-item override: auto, breakfast, lunch, dinner, snacks, uncategorized, or '0'..'8'.",
    )
    time: str | None = Field(
        default=None,
        description="Optional per-item local wall time (HH:MM or HH:MM:SS).",
    )


class CustomMeasureInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    measure_id: int | None = Field(
        default=None,
        gt=0,
        description="Existing measure_id to edit, or omit to add a new Weight measure.",
    )
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        description="Measure label, e.g. 'piece', 'scoop', 'slice'.",
    )
    grams: float | None = Field(
        default=None,
        gt=0,
        description="Total grams for `amount` units of this measure.",
    )
    amount: float | None = Field(
        default=None,
        gt=0,
        description="Number of units weighing `grams` (e.g. amount=22, grams=30 for '22 pieces = 30g'). Omit when editing a measure without changing its unit count.",
    )


class RecipeIngredientInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    food_id: int = Field(gt=0, description="Cronometer food_id of the ingredient.")
    grams: float | None = Field(
        default=None,
        gt=0,
        description="Ingredient weight in grams (provide either `grams` or `quantity`).",
    )
    quantity: float | None = Field(
        default=None,
        gt=0,
        description="Number of units of `measure_id` / `measure_name`.",
    )
    measure_id: int | None = Field(default=None, gt=0)
    measure_name: str | None = Field(default=None, min_length=1, max_length=100)


class DraftItemInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=300)
    food_id: int | None = Field(default=None, gt=0)
    grams: float | None = Field(default=None, gt=0)
    quantity: float | None = Field(default=None, gt=0)
    measure_id: int | None = Field(default=None, gt=0)
    measure_name: str | None = Field(default=None, min_length=1, max_length=100)
    notes: str | None = Field(default=None, max_length=500)


def register_core_tools(
    mcp: FastMCP, assistant: CronometerAssistant, run: Callable[..., Awaitable[str]]
) -> None:
    """Register the 19 core food, diary, custom-food, recipe, alias, and draft tools."""

    # --- Read & Discovery ---

    @mcp.tool(annotations=READ)
    async def check_connection() -> str:
        """Verify live Cronometer connectivity, account timezone, today's date, and key prefs."""
        return await run(assistant.foods.check_connection)

    @mcp.tool(annotations=READ)
    async def get_food_log(date: str | None = None, include_untracked: bool = True) -> str:
        """Get a full diary day: all logged foods, calorie target/remaining, and full nutrient totals."""
        return await run(assistant.diary.get_food_log, date, include_untracked=include_untracked)

    @mcp.tool(annotations=READ)
    async def search_foods(query: str, limit: int = 10) -> str:
        """Search Cronometer foods (or barcode) with measures, per-100g macros, and matching aliases."""
        return await run(assistant.foods.search_foods, query, limit=limit)

    @mcp.tool(annotations=READ)
    async def get_food_details(food_id: int) -> str:
        """Inspect complete metadata, measures, barcodes, recipe ingredients, and all nutrients for a food or recipe."""
        return await run(assistant.foods.get_food_details, food_id)

    @mcp.tool(annotations=READ)
    async def get_my_foods(
        query: str = "",
        include_recent: bool = True,
        include_custom: bool = True,
        include_aliases: bool = True,
    ) -> str:
        """Look up saved aliases, recently logged foods, and custom foods/recipes in one call."""
        return await run(
            assistant.foods.get_my_foods,
            query=query,
            include_recent=include_recent,
            include_custom=include_custom,
            include_aliases=include_aliases,
        )

    @mcp.tool(annotations=READ)
    async def list_nutrients(query: str = "") -> str:
        """List all writable Cronometer nutrients with canonical keys, IDs, and catalog units."""
        return await run(assistant.foods.list_nutrients, query=query)

    # --- Diary Writes ---

    @mcp.tool(annotations=WRITE)
    async def log_food_entries(
        entries: list[LogFoodEntryInput],
        date: str | None = None,
        meal_group: str = "auto",
        time: str | None = None,
        request_id: str | None = None,
    ) -> str:
        """Log one or more foods to Cronometer in a single read-back-verified call.

        `meal_group`: auto, breakfast, lunch, dinner, snacks, uncategorized, or '0'..'8'.
        Each entry takes `grams` OR `quantity` + `measure_id`/`measure_name`.
        When adding more of a food already logged in the same meal on that date, prefer updating
        the existing entry's total with `edit_food_entry` instead of adding another line (unless
        a separate line is explicitly wanted).
        """
        return await run(
            assistant.diary.log_food_entries,
            [e.model_dump(exclude_none=True) for e in entries],
            day=date,
            meal_group=meal_group,
            time=time,
            request_id=request_id,
        )

    @mcp.tool(annotations=IDEMPOTENT_WRITE)
    async def edit_food_entry(
        entry_id: int,
        date: str | None = None,
        grams: float | None = None,
        quantity: float | None = None,
        measure_id: int | None = None,
        measure_name: str | None = None,
        time: str | None = None,
        meal_group: str | None = None,
        new_date: str | None = None,
    ) -> str:
        """Edit any logged serving's amount, measure, time, meal group ('breakfast'/'lunch'/'dinner'/'snacks'/'uncategorized'/'0'..'8'), or date.

        Only pass the fields you want to change; omitted fields (amount, measure, time,
        meal_group, date) are preserved automatically. Prefer this over `log_food_entries`
        when adding more of a food already logged in the same meal — pass the new combined
        `grams` (or `quantity`).
        """
        return await run(
            assistant.diary.edit_food_entry,
            entry_id,
            day=date,
            grams=grams,
            quantity=quantity,
            measure_id=measure_id,
            measure_name=measure_name,
            time=time,
            meal_group=meal_group,
            new_day=new_date,
        )

    @mcp.tool(annotations=DESTRUCTIVE)
    async def remove_food_entries(entry_ids: list[int], date: str | None = None) -> str:
        """Remove one or more logged food servings from a diary day (with read-back verification)."""
        return await run(assistant.diary.remove_food_entries, entry_ids, day=date)

    # --- Custom Foods & Recipes ---

    @mcp.tool(annotations=WRITE)
    async def create_custom_food(
        name: str,
        nutrients: dict[str, Any],
        basis_amount: float = 100.0,
        basis_unit: Literal["g", "ml"] = "g",
        carbohydrate_basis: Literal[
            "includes_fiber", "excludes_fiber", "unspecified"
        ] = "unspecified",
        serving_name: str = "serving",
        serving_grams: float = 100.0,
        density_g_per_ml: float | None = None,
        category_id: int = 0,
        barcode: str | None = None,
        notes: str | None = None,
        extra_measures: list[CustomMeasureInput] | None = None,
        request_id: str | None = None,
    ) -> str:
        """Create a private custom food with Weight measures, EU/US carb handling, and native barcode.

        `nutrients` maps any Cronometer nutrient key (or configured label alias) to either
        `{"amount": number, "unit": "g"|"mg"|"ug"|"kcal"|"kJ"|"IU"}` or a plain number in that
        nutrient's catalog unit. Use `carbohydrate_basis="excludes_fiber"` for EU/UK/AU/NZ/Israeli
        labels and `"includes_fiber"` for US/Canadian labels.
        """
        return await run(
            assistant.custom.create_custom_food,
            name,
            nutrients,
            basis_amount=basis_amount,
            basis_unit=basis_unit,
            carbohydrate_basis=carbohydrate_basis,
            serving_name=serving_name,
            serving_grams=serving_grams,
            density_g_per_ml=density_g_per_ml,
            category_id=category_id,
            barcode=barcode,
            notes=notes,
            extra_measures=[
                m.model_dump(exclude_unset=True, exclude_none=True) for m in extra_measures
            ]
            if extra_measures
            else None,
            request_id=request_id,
        )

    @mcp.tool(annotations=IDEMPOTENT_WRITE)
    async def update_custom_food(
        food_id: int,
        name: str | None = None,
        notes: str | None = None,
        category_id: int | None = None,
        barcode: str | None = None,
        nutrients: dict[str, Any] | None = None,
        basis_amount: float = 100.0,
        basis_unit: Literal["g", "ml"] = "g",
        carbohydrate_basis: Literal[
            "includes_fiber", "excludes_fiber", "unspecified"
        ] = "unspecified",
        density_g_per_ml: float | None = None,
        measures: list[CustomMeasureInput] | None = None,
    ) -> str:
        """Edit an owned custom food in place while safely preserving existing measure IDs."""
        return await run(
            assistant.custom.update_custom_food,
            food_id,
            name=name,
            notes=notes,
            category_id=category_id,
            barcode=barcode,
            nutrients=nutrients,
            basis_amount=basis_amount,
            basis_unit=basis_unit,
            carbohydrate_basis=carbohydrate_basis,
            density_g_per_ml=density_g_per_ml,
            measures=[m.model_dump(exclude_unset=True, exclude_none=True) for m in measures]
            if measures
            else None,
        )

    @mcp.tool(annotations=DESTRUCTIVE)
    async def retire_custom_food(food_id: int, retired: bool = True) -> str:
        """Retire (or restore with retired=False) an owned custom food or recipe."""
        return await run(assistant.custom.retire_custom_food, food_id, retired=retired)

    @mcp.tool(annotations=WRITE)
    async def save_recipe(
        name: str,
        ingredients: list[RecipeIngredientInput],
        servings: float | None = None,
        cooked_grams: float | None = None,
        serving_type: Literal["weight", "servings"] = "weight",
        recipe_id: int | None = None,
        notes: str | None = None,
        request_id: str | None = None,
    ) -> str:
        """Create or update a recipe. Pass `cooked_grams` when a dish lost/gained water in cooking.

        `servings` defaults to 1.0 when creating a new recipe, or keeps the existing recipe's
        serving count on update unless a new value is provided. Pass a stable `request_id` when
        creating a new recipe so retries never create duplicates.
        """
        return await run(
            assistant.recipes.save_recipe,
            name,
            [i.model_dump(exclude_none=True) for i in ingredients],
            servings=servings,
            cooked_grams=cooked_grams,
            serving_type=serving_type,
            recipe_id=recipe_id,
            notes=notes,
            request_id=request_id,
        )

    # --- Personal Aliases & Optional Cooking Scratchpad ---

    @mcp.tool(annotations=IDEMPOTENT_WRITE)
    async def remember_food_alias(
        alias: str,
        food_id: int,
        translation_id: int = 0,
        default_grams: float | None = None,
        default_measure_id: int | None = None,
        notes: str | None = None,
    ) -> str:
        """Remember a personal shorthand (in any language) for a usual food and optional portion."""
        return await run(
            assistant.foods.remember_food_alias,
            alias,
            food_id,
            translation_id=translation_id,
            default_grams=default_grams,
            default_measure_id=default_measure_id,
            notes=notes,
        )

    @mcp.tool(annotations=DESTRUCTIVE)
    async def delete_food_alias(alias: str) -> str:
        """Delete a previously saved personal food alias."""
        return await run(assistant.foods.delete_food_alias, alias)

    @mcp.tool(annotations=WRITE)
    async def save_meal_draft(
        title: str,
        items: list[DraftItemInput],
        draft_id: str | None = None,
        date: str | None = None,
        meal_group: str | None = None,
        local_time: str | None = None,
        notes: str | None = None,
    ) -> str:
        """Optional scratchpad to save a meal being prepared across chats. Not required before logging.

        Omitting `draft_id` creates a new draft; pass a caller-stable `draft_id` to update or make
        retries idempotent.
        """
        return await run(
            assistant.diary.save_meal_draft,
            title,
            [i.model_dump(exclude_none=True) for i in items],
            draft_id=draft_id,
            day=date,
            meal_group=meal_group,
            local_time=local_time,
            notes=notes,
        )

    @mcp.tool(annotations=READ)
    async def list_meal_drafts(limit: int = 20) -> str:
        """List saved cooking scratchpad drafts."""
        return await run(assistant.diary.list_meal_drafts, limit=limit)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def delete_meal_draft(draft_id: str) -> str:
        """Delete a cooking scratchpad draft (does not touch Cronometer diary entries)."""
        return await run(assistant.diary.delete_meal_draft, draft_id)
