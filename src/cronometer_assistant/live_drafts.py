"""Account-bound local meal drafts using reviewed Cronometer profiles, without writes."""

import hashlib
import json
import re
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

from .catalog import CronometerReadOnlyCatalog
from .live import LiveError, account_timezone, fresh_account_timezone
from .live_diary import LiveDiary
from .live_diary import local_time as validated_time
from .models import Food, ValidationError, normalized, text
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
        snapshot = {
            "observed": json.loads(json.dumps(food.to_dict())),
            "preparation": preparation,
            "per_100g_confirmed": True,
            "selection": selection,
        }
        selection_id = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
        snapshot["selection_id"] = selection_id
        with self.store.locked():
            self.store.put("food_selections", selection_id, snapshot)
            # A compatibility default for new items; bound items never use this pointer.
            self.store.put("verified_foods", food_id, snapshot)
        return replace(food, preparation=preparation, selection_id=selection_id, **selection)

    def get_selection(self, food_id, selection_id=None):
        if selection_id is not None:
            selection_id = text(selection_id, "selection_id", 200)
        if selection_id is None:
            matches = [
                r
                for r in self.store.values("food_selections")
                if r["observed"]["food_id"] == food_id
            ]
            if len(matches) > 1:
                raise ValidationError(
                    "Several verified selections exist; pass the exact selection_id"
                )
        row = (
            self.store.get("food_selections", selection_id)
            if selection_id
            else self.store.get("verified_foods", food_id)
        )
        if not row or row["observed"]["food_id"] != food_id:
            raise ValidationError("Food is unverified; review its exact selection first")
        observed = {"translation_id": 0, "selection_id": None, **row["observed"]}
        food = self.upstream.get(food_id)
        if observed != json.loads(json.dumps(food.to_dict())):
            raise ValidationError(
                "Food is unverified or changed; review its identity and basis again"
            )
        return replace(
            food,
            preparation=row["preparation"],
            selection_id=row.get("selection_id"),
            **row.get("selection", {}),
        )

    def search(self, query):
        foods = self.upstream.search(query)
        for i, food in enumerate(foods):
            saved = self.store.get("verified_foods", food.food_id)
            if saved and saved.get("selection") == {
                "name": food.name,
                "translation_id": food.translation_id,
            }:
                foods[i] = replace(
                    food, preparation=saved["preparation"], selection_id=saved.get("selection_id")
                )
        return foods

    def get(self, food_id) -> Food:
        return self.get_selection(food_id)


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
        with scoped.locked():
            for row in scoped.values("verified_foods"):
                if not row.get("selection_id"):
                    identity = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
                    row["selection_id"] = identity
                    scoped.put("food_selections", identity, row)
                    scoped.put("verified_foods", row["observed"]["food_id"], row)
            for namespace in ("meals", "aliases"):
                for record in scoped.values(namespace):
                    items = record.get("items", []) if namespace == "meals" else [record]
                    changed = False
                    for item in items:
                        saved = scoped.get("verified_foods", item.get("food_id", ""))
                        if saved and not item.get("selection_id"):
                            item["selection_id"] = saved["selection_id"]
                            changed = True
                    if changed:
                        key = (
                            record["meal_id"]
                            if namespace == "meals"
                            else normalized(record["alias"])
                        )
                        scoped.put(namespace, key, record)

    def _item_food(self, item):
        return self.catalog.get_selection(item["food_id"], item.get("selection_id"))

    def update_meal(self, meal_id, *, items, **kwargs):
        bound = []
        for item in items:
            item = dict(item)
            if item.get("food_id"):
                item["selection_id"] = self._item_food(item).selection_id
            bound.append(item)
        return super().update_meal(meal_id, items=bound, **kwargs)

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
        if fresh_account_timezone(self.client) != meal["timezone"]:
            raise ValidationError("Cronometer's timezone changed; review the date and time again")
        result = []
        for item in meal["items"]:
            food = self._item_food(item)
            params = self.diary.prepare(item, meal, {"translation_id": food.translation_id})
            result.append(
                {
                    "item": item,
                    "state": "pending",
                    "add_parameters": params,
                    "food_profile": json.loads(json.dumps(food.to_dict())),
                    "measure_profile": self.diary.measure_snapshot(params),
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
        with self.store.locked():
            operation = self.store.get("operations", meal_id)
            if operation:
                return self.response(
                    meal=self._meal(meal_id),
                    original_draft_is_historical=True,
                    current_recorded=self._current_recorded(operation),
                    review={
                        "ready_to_log": False,
                        "questions": [],
                        "note": "Original draft is historical; current recorded state is separate.",
                    },
                )
            return self._with_review_token(super().review_meal(meal_id))

    def _planned_steps(self, meal, review_token):
        steps = self._plan(meal)
        if not review_token or review_token != self._token(meal, steps):
            raise ValidationError("The food or serving changed; present the current review again")
        return steps

    def _validate_pending_step(self, meal, step):
        if fresh_account_timezone(self.client) != meal["timezone"]:
            raise ValidationError("Account timezone changed")
        food = self._item_food(step["item"])
        params = self.diary.prepare(step["item"], meal, {"translation_id": food.translation_id})
        profile = json.loads(json.dumps(food.to_dict()))
        if (
            step.get("food_profile") != profile
            or step.get("add_parameters") != params
            or step.get("measure_profile") != self.diary.measure_snapshot(params)
        ):
            raise ValidationError("Pending food or serving changed; review again")

    def cancel_pending(self, meal_id, *, confirmed=False):
        if confirmed is not True:
            raise ValidationError("Confirm cancelling the unsent remainder first")
        with self.store.locked():
            meal = self._meal(meal_id)
            operation = self.store.get("operations", meal_id)
            if not operation:
                raise ValidationError("No logging operation; delete an unlogged draft instead")
            for step in operation["steps"]:
                if step["state"] in {"pending", "not_applied"}:
                    step["cancelled_from"] = step["state"]
                    step["state"] = "cancelled"
            terminal = all(s["state"] in {"verified", "cancelled"} for s in operation["steps"])
            self._persist_operation(
                meal,
                operation,
                self._completed_state(operation) if terminal else "needs_reconciliation",
            )
            return self.response(
                operation=self._visible_operation(operation),
                current_recorded=self._current_recorded(operation),
                next_step="Unsent items are cancelled. Uncertain attempts remain protected; only cancelled items may be reviewed in a new draft.",
            )

    @staticmethod
    def _current_recorded(operation):
        rows = []
        for index, step in enumerate(operation["steps"]):
            change = step.get("diary_change")
            grams = step.get("add_parameters", {}).get("grams", step["item"].get("grams"))
            state = step["state"]
            last_verified = grams if state == "verified" else None
            if change:
                if change["state"] == "verified":
                    grams = change["to_grams"]
                    last_verified = grams
                    state = "undone" if grams is None else "verified"
                elif change["state"] == "not_applied":
                    grams = last_verified = change["from_grams"]
                else:
                    last_verified = change["from_grams"]
                    state, grams = "change_uncertain", None
            elif state != "verified":
                grams = None
            rows.append(
                {
                    "item_index": index,
                    "food_id": step["item"]["food_id"],
                    "food_name": step.get("food_profile", {}).get(
                        "name", step["item"]["description"]
                    ),
                    "entry_id": step.get("entry_id"),
                    "state": state,
                    "grams": grams,
                    "last_verified_grams": last_verified,
                    "requested_grams": change["to_grams"] if change else None,
                }
            )
        return {
            "source": "assistant journal; use logging status for fresh diary readback",
            "items": rows,
        }

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
        result["current_recorded"] = self._current_recorded(operation)
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
                original_draft_is_historical=True,
                current_recorded=self._current_recorded(operation),
            )
