"""Thin facade composing core meal-logging and cleanly separated extra services."""

from __future__ import annotations

from .auth import CronometerSessionClient
from .core import CustomFoodService, DiaryService, FoodService, RecipeService
from .extras import DiaryExtrasService, TargetsService, TrackingService
from .state import AssistantState


class CronometerAssistant:
    """Single entry object exposing cleanly grouped domain services.

    Core everyday logging:
      - `assistant.foods`   (connection, search, details, my_foods, nutrients, aliases)
      - `assistant.diary`   (food_log, log/edit/remove servings, optional cooking drafts)
      - `assistant.custom`  (custom foods create/update/retire)
      - `assistant.recipes` (weight-based and servings-based recipes)

    Optional / secondary features:
      - `assistant.diary_extras` (mark_day_complete, copy_day, find_entries_by_food, notes)
      - `assistant.targets`      (nutrient targets, macro schedules, nutrition scores, streaks)
      - `assistant.tracking`     (biometrics, exercise, fasting)
    """

    def __init__(
        self, client: CronometerSessionClient, state: AssistantState | None = None
    ) -> None:
        self.client = client
        self.state = state

        # Core everyday meal logging
        self.foods = FoodService(client, state)
        self.diary = DiaryService(client, state)
        self.custom = CustomFoodService(client, state)
        self.recipes = RecipeService(client, state)

        # Cleanly isolated secondary features
        self.diary_extras = DiaryExtrasService(client)
        self.targets = TargetsService(client)
        self.tracking = TrackingService(client)
