"""Account-bound local meal drafts using reviewed Cronometer profiles, without writes."""

import hashlib
import json
import re
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

from .catalog import CronometerReadOnlyCatalog
from .live import LiveError, account_timezone
from .live_diary import LiveDiary
from .live_diary import local_time as validated_time
from .models import Food, ValidationError, text
from .service import MealService
from .store import ScopedStore


class ReviewedCatalog:
    def __init__(self, store, client):
        self.store = store
        self.upstream = CronometerReadOnlyCatalog(client)

    def verify(
        self,
        food_id,
        preparation,
        reviewed,
        per_100g_confirmed,
        translation_id=0,
        search_query=None,
    ):
        if reviewed is not True or per_100g_confirmed is not True:
            raise ValidationError("Review exact identity, preparation and per-100-g basis first")
        if preparation not in {"raw", "cooked", "as_sold"}:
            raise ValidationError("Confirm raw, cooked or as_sold preparation")
        if not isinstance(food_id, str) or not re.fullmatch(r"crono:[1-9][0-9]*", food_id):
            raise ValidationError("Select an inspected Cronometer food ID")
        food = self.upstream.get(food_id)
        if type(translation_id) is not int or translation_id < 0:
            raise ValidationError("Invalid Cronometer translation ID")
        selection = {"name": food.name, "translation_id": translation_id}
        if translation_id or search_query is not None:
            query = text(search_query, "original food search query", 300)
            rows = self.upstream.client.search_food(query)
            row = next(
                (
                    row
                    for row in rows
                    if row.get("id") == int(food_id.removeprefix("crono:"))
                    and (row.get("translationId") or 0) == translation_id
                ),
                None,
            )
            if row is None:
                raise ValidationError("Search again and choose the exact Cronometer food variant")
            selection["name"] = row.get("name") or food.name
        with self.store.locked():
            self.store.put(
                "verified_foods",
                food_id,
                {
                    "observed": json.loads(json.dumps(food.to_dict())),
                    "preparation": preparation,
                    "per_100g_confirmed": True,
                    "selection": selection,
                },
            )
        return replace(food, preparation=preparation, **selection)

    def _reviewed(self, food):
        row = self.store.get("verified_foods", food.food_id)
        if row and row["observed"] == json.loads(json.dumps(food.to_dict())):
            return replace(food, preparation=row["preparation"], **row.get("selection", {}))
        # Old draft records had no translation field and describe the canonical food.
        if row and "translation_id" not in row["observed"]:
            observed = {**row["observed"], "translation_id": 0}
            if observed == json.loads(json.dumps(food.to_dict())):
                return replace(food, preparation=row["preparation"])
        return None

    def search(self, query):
        foods = self.upstream.search(query)
        for i, food in enumerate(foods):
            saved = self.store.get("verified_foods", food.food_id)
            if saved and saved.get("selection") == {
                "name": food.name,
                "translation_id": food.translation_id,
            }:
                foods[i] = replace(food, preparation=saved["preparation"])
        return foods

    def get(self, food_id) -> Food:
        if not isinstance(food_id, str) or not re.fullmatch(r"crono:[1-9][0-9]*", food_id):
            raise ValidationError("Select an inspected Cronometer food ID")
        food = self._reviewed(self.upstream.get(food_id))
        if food is None:
            raise ValidationError(
                "Food is unverified or changed; review its identity and basis again"
            )
        return food


class LiveDraftService(MealService):
    def __init__(self, store, client, *, diary=None):
        scoped = ScopedStore(store, "live_drafts:")
        timezone = account_timezone(client._timezone)
        account = client.user_id
        with scoped.locked():
            bound = scoped.get("settings", "account")
            if bound and bound["user_id"] != account:
                raise ValidationError("Use a separate draft database for this Cronometer account")
            scoped.put("settings", "account", {"user_id": account})
        super().__init__(
            scoped,
            catalog=ReviewedCatalog(scoped, client),
            timezone=timezone,
            diary=diary,
            draft_only=diary is None,
        )
        self.client = client

    def begin_meal(self, *args, **kwargs):
        # Do not silently move a running conversation to a different calendar zone.
        if ZoneInfo(account_timezone(self.client._timezone)) != self.timezone:
            raise ValidationError("Account timezone changed; reconnect before starting a draft")
        return super().begin_meal(*args, **kwargs)

    def verify_food(
        self,
        food_id,
        preparation,
        reviewed,
        per_100g_confirmed,
        translation_id=0,
        search_query=None,
    ):
        food = self.catalog.verify(
            food_id, preparation, reviewed, per_100g_confirmed, translation_id, search_query
        )
        return self.response(food=food.to_dict(), verified_for_drafts=True)

    def list_drafts(self, limit=10):
        if type(limit) is not int or not 1 <= limit <= 30:
            raise ValidationError("limit must be an integer from 1 to 30")
        rows = sorted(
            self.store.values("meals"), key=lambda m: (m["day"], m["meal_id"]), reverse=True
        )
        return self.response(
            drafts=[
                {
                    k: row[k]
                    for k in ("meal_id", "title", "day", "timezone", "revision", "state", "intent")
                }
                for row in rows[:limit]
            ]
        )


class LiveMealService(LiveDraftService):
    """Save only a reviewed dinner or other meal and report what Cronometer recorded."""

    def __init__(self, store, client):
        super().__init__(store, client, diary=LiveDiary(client))

    def response(self, **data):
        return {**super().response(), "mode": "cronometer_live", **data}

    def begin_meal(self, title, *, day=None, local_time=None, **kwargs):
        now = datetime.now(self.timezone)
        if local_time is not None:
            local_time = validated_time(local_time)
        elif day is None or day == now.date().isoformat():
            local_time = now.time().isoformat(timespec="seconds")
        return super().begin_meal(title, day=day, local_time=local_time, **kwargs)

    def update_meal(self, meal_id, *, local_time=None, **kwargs):
        if local_time is not None:
            local_time = validated_time(local_time)
        result = super().update_meal(meal_id, local_time=local_time, **kwargs)
        return self._with_review_token(result)

    def _review(self, meal):
        review = super()._review(meal)
        if not meal.get("local_time"):
            review["questions"].append(
                {"reason": "meal_time", "question": "What local time did you eat this meal?"}
            )
            review["suggested_next_questions"] = review["questions"][:2]
            review["draft_complete"] = review["ready_to_log"] = False
        return review

    def _plan(self, meal):
        if account_timezone(self.client._timezone) != meal["timezone"]:
            raise ValidationError("Cronometer's timezone changed; review the date and time again")
        result = []
        for item in meal["items"]:
            food = self.catalog.get(item["food_id"])
            params = self.diary.prepare(item, meal, {"translation_id": food.translation_id})
            result.append(
                {
                    "item": item,
                    "state": "pending",
                    "add_parameters": params,
                    "food_profile": food.to_dict(),
                }
            )
        return result

    @staticmethod
    def _token(meal, steps):
        reviewed = [meal["meal_id"], meal["revision"], meal["timezone"], steps]
        return hashlib.sha256(json.dumps(reviewed, sort_keys=True).encode()).hexdigest()

    def _with_review_token(self, result):
        meal, review = result["meal"], result["review"]
        if review["ready_to_log"] and not self.store.get("operations", meal["meal_id"]):
            try:
                steps = self._plan(meal)
            except (LiveError, ValidationError) as error:
                review["ready_to_log"] = False
                review["write_preflight_error"] = str(error)
            else:
                review["review_token"] = self._token(meal, steps)
                review["servings_to_log"] = [
                    {**step["add_parameters"], "food_name": step["food_profile"]["name"]}
                    for step in steps
                ]
        else:
            review["ready_to_log"] = False
        return result

    def review_meal(self, meal_id):
        return self._with_review_token(super().review_meal(meal_id))

    def _planned_steps(self, meal, review_token):
        steps = self._plan(meal)
        if not review_token or review_token != self._token(meal, steps):
            raise ValidationError("The food or serving changed; present the current review again")
        for step in steps:
            del step["food_profile"]
        return steps

    def _matches_step(self, entry, step, meal):
        params = step["add_parameters"]
        return super()._matches_step(entry, step, meal) and all(
            getattr(entry, key) == params[key]
            for key in ("local_time", "measure_id", "translation_id")
        )

    def commit_meal(self, meal_id, expected_revision, *, review_token=None, confirmed=False):
        if confirmed is not True:
            raise ValidationError("Ask the user to confirm this exact meal before writing it")
        result = super().commit_meal(meal_id, expected_revision, review_token=review_token)
        operation = result["operation"]
        result["verified_serving_count"] = sum(
            step["state"] == "verified" for step in operation["steps"]
        )
        uncertain = operation["state"] in {"needs_reconciliation", "needs_verification"}
        result["diary_change_uncertain"] = uncertain
        result["real_diary_changed"] = (
            False
            if result.get("already_logged")
            else (True if operation["state"] == "committed" else None)
        )
        result["operation"] = self._visible_operation(operation)
        return result

    @staticmethod
    def _visible_operation(operation):
        return {
            **operation,
            "steps": [
                {key: value for key, value in step.items() if key != "baseline_ids"}
                for step in operation["steps"]
            ],
        }

    def reconcile_entry(self, meal_id, item_index, entry_id):
        result = super().reconcile_entry(meal_id, item_index, entry_id)
        result["operation"] = self._visible_operation(result["operation"])
        return result

    def status(self, meal_id):
        with self.store.locked():
            meal = self._meal(meal_id)
            operation = self.store.get("operations", meal_id)
            if operation is None:
                return self.response(meal=meal, operation=None, diary=[])
            entries = self.diary.entries(meal["day"])
            own_ids = {step.get("entry_id") for step in operation["steps"]}
            candidates = []
            for index, step in enumerate(operation["steps"]):
                if step["state"] not in {"sending", "uncertain", "needs_verification"}:
                    continue
                other_ids = own_ids - {step.get("entry_id")}
                ineligible = other_ids.union(step.get("baseline_ids", []))
                candidates.append(
                    {
                        "item_index": index,
                        "entry_ids": [
                            entry.entry_id
                            for entry in entries
                            if entry.entry_id not in ineligible
                            and self._matches_step(entry, step, meal)
                        ],
                    }
                )
            return self.response(
                meal=meal,
                operation=self._visible_operation(operation),
                diary=[entry.to_dict() for entry in entries if entry.entry_id in own_ids],
                reconciliation_candidates=candidates,
            )
