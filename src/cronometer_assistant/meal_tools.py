"""MCP tools for the live diary, registered only in explicit meals mode."""

from .daily_nutrition import daily_nutrition
from .diary_changes import DiaryChanges


def register_meal_tools(server, service, call):
    changes = DiaryChanges(service)

    @server.tool(annotations={"readOnlyHint": True})
    def get_daily_nutrition(day: str | None = None) -> dict:
        """Read Cronometer totals for a full diary day, including manually logged foods."""
        return call(daily_nutrition, service.client, day)

    @server.tool(
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True}
    )
    def log_meal(meal_id: str, expected_revision: int, review_token: str, confirmed: bool) -> dict:
        """Write to the real diary only after the user confirms the latest review.

        Present the exact food names, amounts, date, local time and group before
        asking. If the result needs verification, use get_logging_status.
        """
        return call(
            service.commit_meal,
            meal_id,
            expected_revision,
            review_token=review_token,
            confirmed=confirmed,
        )

    @server.tool(annotations={"readOnlyHint": True})
    def get_logging_status(meal_id: str) -> dict:
        """Read back this meal's existing serving IDs and any uncertain write candidates."""
        return call(service.status, meal_id)

    @server.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    def reconcile_logged_serving(meal_id: str, item_index: int, entry_id: str) -> dict:
        """After checking the diary, assign an exact new serving to an uncertain journal step."""
        return call(service.reconcile_entry, meal_id, item_index, entry_id)

    @server.tool(
        annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True}
    )
    def correct_logged_amount(
        meal_id: str, item_index: int, expected_grams: float, grams: float, confirmed: bool
    ) -> dict:
        """Change one assistant-created serving's amount after confirming its old and new grams."""
        return call(
            changes.correct_amount, meal_id, item_index, expected_grams, grams, confirmed=confirmed
        )

    @server.tool(
        annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True}
    )
    def undo_logged_item(
        meal_id: str, item_index: int, expected_grams: float, confirmed: bool
    ) -> dict:
        """Remove one verified assistant-created serving after the user confirms its removal."""
        return call(changes.undo_item, meal_id, item_index, expected_grams, confirmed=confirmed)
