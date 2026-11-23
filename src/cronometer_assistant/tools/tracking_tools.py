"""Opt-in non-food tracking MCP tools: biometrics, exercise, and fasting."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from mcp.server.fastmcp import FastMCP

from ..service import CronometerAssistant
from . import DESTRUCTIVE, IDEMPOTENT_WRITE, READ, WRITE


def register_tracking_tools(
    mcp: FastMCP, assistant: CronometerAssistant, run: Callable[..., Awaitable[str]]
) -> None:
    """Register the 13 opt-in biometric, exercise, and fasting tools."""

    # --- Biometrics ---

    @mcp.tool(annotations=READ)
    async def list_biometrics(query: str = "") -> str:
        """List trackable Cronometer biometric metrics and their unit IDs (optionally filtered)."""
        return await run(assistant.tracking.list_biometrics, query=query)

    @mcp.tool(annotations=READ)
    async def get_biometrics(
        metric_id: int,
        unit_id: int,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> str:
        """Read a biometric time series (e.g. weight, body fat) over a date range."""
        return await run(
            assistant.tracking.get_biometrics,
            metric_id,
            unit_id,
            start_day=start_date,
            end_day=end_date,
        )

    @mcp.tool(annotations=WRITE)
    async def add_biometric(
        metric_id: int, unit_id: int, amount: float, date: str | None = None
    ) -> str:
        """Record a biometric measurement (use list_biometrics for metric_id and unit_id)."""
        return await run(assistant.tracking.add_biometric, metric_id, unit_id, amount, day=date)

    @mcp.tool(annotations=IDEMPOTENT_WRITE)
    async def edit_biometric(biometric_id: int, amount: float, date: str | None = None) -> str:
        """Edit an existing biometric entry's value."""
        return await run(assistant.tracking.edit_biometric, biometric_id, amount, day=date)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def remove_biometrics(biometric_ids: list[int], date: str | None = None) -> str:
        """Delete one or more biometric entries on a date (with read-back verification)."""
        return await run(assistant.tracking.remove_biometrics, biometric_ids, day=date)

    # --- Exercise ---

    @mcp.tool(annotations=WRITE)
    async def add_exercise(
        name: str, minutes: int, calories_burned: float, date: str | None = None
    ) -> str:
        """Log an exercise entry with positive calories_burned."""
        return await run(assistant.tracking.add_exercise, name, minutes, calories_burned, day=date)

    @mcp.tool(annotations=IDEMPOTENT_WRITE)
    async def edit_exercise(
        exercise_id: int,
        minutes: int | None = None,
        calories_burned: float | None = None,
        date: str | None = None,
    ) -> str:
        """Edit an existing exercise entry's duration and/or calories burned."""
        return await run(
            assistant.tracking.edit_exercise,
            exercise_id,
            minutes=minutes,
            calories_burned=calories_burned,
            day=date,
        )

    @mcp.tool(annotations=DESTRUCTIVE)
    async def remove_exercises(exercise_ids: list[int], date: str | None = None) -> str:
        """Delete one or more exercise entries on a date (with read-back verification)."""
        return await run(assistant.tracking.remove_exercises, exercise_ids, day=date)

    # --- Fasting ---

    @mcp.tool(annotations=READ)
    async def get_fasting_history(
        start_date: str | None = None, end_date: str | None = None
    ) -> str:
        """Get fasting history over a date range (defaults to last 30 days)."""
        return await run(
            assistant.tracking.get_fasting_history, start_day=start_date, end_day=end_date
        )

    @mcp.tool(annotations=READ)
    async def get_fasting_stats() -> str:
        """Get aggregate fasting statistics."""
        return await run(assistant.tracking.get_fasting_stats)

    @mcp.tool(annotations=WRITE)
    async def add_fast(start: str, end: str | None = None, goal_hours: float = 16.0) -> str:
        """Record a fast (start/end as YYYY-MM-DD HH:MM in account local time; omit end for ongoing)."""
        return await run(assistant.tracking.add_fast, start, end=end, goal_hours=goal_hours)

    @mcp.tool(annotations=IDEMPOTENT_WRITE)
    async def edit_fast(
        fast_id: int,
        start: str | None = None,
        end: str | None = None,
        goal_hours: float | None = None,
    ) -> str:
        """Edit a fast's start, end (to close an open fast), or goal_hours."""
        return await run(
            assistant.tracking.edit_fast,
            fast_id,
            start=start,
            end=end,
            goal_hours=goal_hours,
        )

    @mcp.tool(annotations=DESTRUCTIVE)
    async def delete_fast(fast_id: int) -> str:
        """Delete a recorded fast."""
        return await run(assistant.tracking.delete_fast, fast_id)
