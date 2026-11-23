"""Secondary nutrition & diary extra MCP tools (day management, notes, targets, scores, streaks)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from mcp.server.fastmcp import FastMCP

from ..service import CronometerAssistant
from . import IDEMPOTENT_WRITE, READ, WRITE


def register_extras_tools(
    mcp: FastMCP, assistant: CronometerAssistant, run: Callable[..., Awaitable[str]]
) -> None:
    """Register the 10 secondary nutrition/diary tools."""

    # --- Diary management & notes ---

    @mcp.tool(annotations=IDEMPOTENT_WRITE)
    async def mark_day_complete(date: str | None = None, complete: bool = True) -> str:
        """Mark a diary day as complete or incomplete in Cronometer."""
        return await run(assistant.diary_extras.mark_day_complete, date, complete=complete)

    @mcp.tool(annotations=WRITE)
    async def copy_day(date: str | None = None, from_date: str | None = None) -> str:
        """Copy all diary entries from `from_date` (default: day before `date`) into `date` additively."""
        return await run(assistant.diary_extras.copy_day, to_day=date, from_day=from_date)

    @mcp.tool(annotations=READ)
    async def find_entries_by_food(
        food_id: int, start_date: str | None = None, end_date: str | None = None
    ) -> str:
        """Scan diary days (default last 30 days, max 90) for every entry referencing `food_id`."""
        return await run(
            assistant.diary_extras.find_entries_by_food,
            food_id,
            start_day=start_date,
            end_day=end_date,
        )

    @mcp.tool(annotations=WRITE)
    async def add_note(text: str, date: str | None = None) -> str:
        """Add a text note to a diary day (Cronometer notes can be edited, not deleted via API)."""
        return await run(assistant.diary_extras.add_note, text, day=date)

    @mcp.tool(annotations=IDEMPOTENT_WRITE)
    async def edit_note(note_id: int, text: str, date: str | None = None) -> str:
        """Replace the text of an existing diary note."""
        return await run(assistant.diary_extras.edit_note, note_id, text, day=date)

    # --- Targets, scores, and streaks ---

    @mcp.tool(annotations=READ)
    async def get_targets(query: str = "") -> str:
        """Read nutrient min/max/visibility targets for the account (optionally filtered by name)."""
        return await run(assistant.targets.get_targets, query=query)

    @mcp.tool(annotations=IDEMPOTENT_WRITE)
    async def set_nutrient_target(
        nutrient: str,
        minimum: float | None = None,
        maximum: float | None = None,
        visible: bool | None = None,
    ) -> str:
        """Set a nutrient's daily minimum target, upper limit, or tracking visibility."""
        return await run(
            assistant.targets.set_nutrient_target,
            nutrient,
            minimum=minimum,
            maximum=maximum,
            visible=visible,
        )

    @mcp.tool(annotations=READ)
    async def get_macro_targets() -> str:
        """Read weekly macro target schedules and saved macro templates."""
        return await run(assistant.targets.get_macro_targets)

    @mcp.tool(annotations=READ)
    async def get_nutrition_scores(date: str | None = None) -> str:
        """Read Cronometer's category nutrition scores (Vitamins, Minerals, Electrolytes, etc.)."""
        return await run(assistant.targets.get_nutrition_scores, date)

    @mcp.tool(annotations=READ)
    async def get_streak(date: str | None = None) -> str:
        """Read current and record diary logging streaks as of `date`."""
        return await run(assistant.targets.get_streak, date)
