"""Register local draft tools shared by real-food draft and logging modes."""


def register_draft_tools(server, service, call):
    @server.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    def verify_food_for_draft(
        food_id: str,
        preparation: str,
        reviewed: bool,
        per_100g_confirmed: bool,
        translation_id: int = 0,
        search_query: str | None = None,
    ) -> dict:
        """Retain an inspected food after exact identity/preparation/basis review.

        Never infer per_100g_confirmed from a tool field name alone. Confirm it
        against the app/label. Changed profiles require review again. For a search
        match, pass its original query and translation_id along with the food ID.
        """
        return call(
            service.verify_food,
            food_id,
            preparation,
            reviewed,
            per_100g_confirmed,
            translation_id,
            search_query,
        )

    @server.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    def begin_meal(
        title: str,
        day: str | None = None,
        meal_group: str = "lunch",
        intent: str = "preparing",
        local_time: str | None = None,
    ) -> dict:
        """Create a local draft. Pass an explicit YYYY-MM-DD and HH:MM when known."""
        return call(
            service.begin_meal,
            title,
            day=day,
            meal_group=meal_group,
            intent=intent,
            local_time=local_time,
        )

    @server.tool(annotations={"readOnlyHint": True})
    def resolve_food(
        query: str, preparation: str | None = None, packaged: bool = False, brand: str | None = None
    ) -> dict:
        """Rank real food candidates; unverified sources never receive guessed database names."""
        return call(
            service.resolve_food, query, preparation=preparation, packaged=packaged, brand=brand
        )

    @server.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    def update_meal_draft(
        meal_id: str,
        expected_revision: int,
        items: list[dict],
        pending_questions: list[str] | None = None,
        intent: str | None = None,
        day: str | None = None,
        meal_group: str | None = None,
        local_time: str | None = None,
    ) -> dict:
        """Replace the full LOCAL draft, preserving unanswered questions and estimate assumptions."""
        return call(
            service.update_meal,
            meal_id,
            expected_revision=expected_revision,
            items=items,
            pending_questions=pending_questions,
            intent=intent,
            day=day,
            meal_group=meal_group,
            local_time=local_time,
        )

    @server.tool(annotations={"readOnlyHint": True})
    def get_meal_review(meal_id: str) -> dict:
        """Review local draft, incomplete nutrient subtotals and useful follow-up questions."""
        return call(service.review_meal, meal_id)

    @server.tool(annotations={"readOnlyHint": True})
    def list_meal_drafts(limit: int = 10) -> dict:
        """Find saved local drafts to resume; diary state is not read."""
        return call(service.list_drafts, limit)

    @server.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
    def delete_meal_draft(meal_id: str, expected_revision: int) -> dict:
        """Discard an unlogged local draft after the user asks; never deletes diary entries."""
        return call(service.delete_draft, meal_id, expected_revision)

    @server.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    def remember_product(alias: str, food_id: str) -> dict:
        """Remember a verified usual product only after the user establishes that preference."""
        return call(service.remember_product, alias, food_id)
