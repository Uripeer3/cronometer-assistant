"""Persistent meal workflow; the assistant supplies language, this layer enforces state."""

from datetime import date, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from .backend import DemoDiary, DiaryBackend
from .catalog import Catalog, DemoCatalog, rank_foods
from .models import DiaryEntry, Food, MealItem, ValidationError, normalized, text
from .nutrition import normalize_label, summarize
from .store import Store

GROUPS = {"breakfast", "lunch", "dinner", "snack"}


class MealService:
    def __init__(
        self,
        store: Store,
        *,
        catalog: Catalog | None = None,
        diary: DiaryBackend | None = None,
        timezone: str = "Asia/Jerusalem",
    ):
        self.store = store
        self.catalog = catalog or DemoCatalog(store)
        self.diary = diary or DemoDiary(store)
        self.timezone = ZoneInfo(timezone)
        # The write path deliberately cannot be enabled against a real diary in this milestone.
        if self.diary.mode != "demo":
            raise ValidationError("Real diary writes are not implemented in this milestone")

    def response(self, **data) -> dict:
        return {"mode": "demo", "real_diary_changed": False, **data}

    def _meal(self, meal_id: str) -> dict:
        meal = self.store.get("meals", meal_id)
        if meal is None:
            raise ValidationError("Unknown meal_id")
        return meal

    def begin_meal(
        self,
        title: str,
        *,
        day: str | None = None,
        meal_group: str = "lunch",
        intent: str = "preparing",
    ) -> dict:
        title = text(title, "meal title")
        day = day or datetime.now(self.timezone).date().isoformat()
        try:
            if date.fromisoformat(day).isoformat() != day:
                raise ValueError
        except (ValueError, TypeError) as error:
            raise ValidationError("day must use YYYY-MM-DD") from error
        if meal_group not in GROUPS:
            raise ValidationError("Choose breakfast, lunch, dinner, or snack")
        if intent not in {"preparing", "eaten"}:
            raise ValidationError("intent must be preparing or eaten")
        meal = {
            "meal_id": uuid4().hex,
            "title": title,
            "day": day,
            "timezone": str(self.timezone),
            "meal_group": meal_group,
            "intent": intent,
            "revision": 0,
            "state": "collecting",
            "items": [],
            "pending_questions": [],
        }
        with self.store.locked():
            self.store.put("meals", meal["meal_id"], meal)
        return self.response(meal=meal)

    def resolve_food(
        self,
        query: str,
        *,
        preparation: str | None = None,
        packaged: bool = False,
        brand: str | None = None,
    ) -> dict:
        query = text(query, "query", 300)
        if preparation is not None and preparation not in {"raw", "cooked", "as_sold"}:
            raise ValidationError("Search preparation must be raw, cooked, or as_sold")
        alias = self.store.get("aliases", normalized(query))
        candidates = self.catalog.search(query)
        remembered_id = alias["food_id"] if alias else None
        if remembered_id and remembered_id not in {food.food_id for food in candidates}:
            candidates.append(self.catalog.get(remembered_id))
        return self.response(
            candidates=rank_foods(
                candidates,
                query,
                preparation=preparation,
                packaged=packaged,
                brand=brand,
                remembered_id=remembered_id,
            ),
            selection_required=True,
        )

    def remember_product(self, alias: str, food_id: str) -> dict:
        alias = text(alias, "alias", 200)
        self.catalog.get(food_id)
        with self.store.locked():
            self.store.put("aliases", normalized(alias), {"alias": alias, "food_id": food_id})
        return self.response(alias=alias, food_id=food_id)

    def update_meal(
        self,
        meal_id: str,
        *,
        expected_revision: int,
        items: list[dict],
        pending_questions: list[str] | None = None,
        intent: str | None = None,
    ) -> dict:
        if not isinstance(items, list) or len(items) > 100:
            raise ValidationError("items must be a complete list of at most 100 foods")
        try:
            parsed = [MealItem(**row) for row in items]
        except TypeError as error:
            raise ValidationError("Malformed meal item fields") from error
        for item in parsed:
            if item.food_id:
                self.catalog.get(item.food_id)
        if pending_questions is not None and not isinstance(pending_questions, list):
            raise ValidationError("pending_questions must be a list")
        questions = (
            None
            if pending_questions is None
            else [text(q, "pending question") for q in pending_questions]
        )
        if questions is not None and len(questions) > 20:
            raise ValidationError("Too many pending questions")
        if intent not in {None, "preparing", "eaten"}:
            raise ValidationError("intent must be preparing or eaten")
        with self.store.locked():
            meal = self._meal(meal_id)
            if meal["revision"] != expected_revision:
                raise ValidationError(
                    "Draft changed; retrieve its current revision before updating"
                )
            if self.store.get("operations", meal_id) is not None:
                raise ValidationError("This draft already has a logging operation and is immutable")
            meal["items"] = [item.to_dict() for item in parsed]
            if questions is not None:
                meal["pending_questions"] = questions
            meal["intent"] = intent or meal["intent"]
            meal["revision"] += 1
            review = self._review(meal)
            meal["state"] = "needs_clarification" if review["questions"] else "ready"
            self.store.put("meals", meal_id, meal)
        return self.response(meal=meal, review=review)

    def _review(self, meal: dict) -> dict:
        questions = [{"reason": "assistant_flag", "question": q} for q in meal["pending_questions"]]
        if not meal["items"]:
            questions.append({"reason": "empty", "question": "What did you prepare or eat?"})
        resolved = []
        entries = []
        for index, row in enumerate(meal["items"]):
            item = MealItem(**row)
            if not item.food_id:
                questions.append(
                    {
                        "item": index,
                        "reason": "identity",
                        "question": f"Which food matches {item.description}?",
                    }
                )
                continue
            food = self.catalog.get(item.food_id)
            if item.grams is None:
                questions.append(
                    {
                        "item": index,
                        "reason": "quantity",
                        "question": f"How much {item.description}?",
                    }
                )
            # Do not reinterpret a weighed cooked portion as a dry/raw entry.
            if item.preparation != "unspecified" and food.preparation != item.preparation:
                questions.append(
                    {
                        "item": index,
                        "reason": "preparation_mismatch",
                        "question": "Confirm a matching preparation or choose another food",
                    }
                )
            elif item.preparation == "unspecified" and food.preparation in {"raw", "cooked"}:
                questions.append(
                    {
                        "item": index,
                        "reason": "preparation",
                        "question": f"Was {item.description} weighed raw/dry or cooked?",
                    }
                )
            resolved.append((item, food))
            entries.append(
                {
                    **item.to_dict(),
                    "food_name": food.name,
                    "source": food.source,
                    "synthetic": food.synthetic,
                }
            )
        nutrients = summarize(resolved)
        unresolved = [row["description"] for row in meal["items"] if not row.get("food_id")]
        for nutrient in nutrients.values():
            nutrient["missing_from"].extend(unresolved)
            if unresolved:
                nutrient["complete"] = False
        return {
            "revision": meal["revision"],
            "questions": questions,
            "suggested_next_questions": questions[:2],
            "entries": entries,
            "nutrients": nutrients,
            "ready_to_log": not questions and meal["intent"] == "eaten",
            "consumption_confirmation_needed": meal["intent"] != "eaten",
            "estimated_items": [
                i for i, row in enumerate(meal["items"]) if row["quantity_basis"] != "weighed"
            ],
        }

    def review_meal(self, meal_id: str) -> dict:
        meal = self._meal(meal_id)
        return self.response(meal=meal, review=self._review(meal))

    def delete_draft(self, meal_id: str, expected_revision: int) -> dict:
        """Remove an unlogged draft; never erase the journal of a possible diary write."""
        with self.store.locked():
            meal = self._meal(meal_id)
            if meal["revision"] != expected_revision:
                raise ValidationError("Retrieve the current draft revision before deleting")
            if self.store.get("operations", meal_id) is not None:
                raise ValidationError(
                    "This meal has a logging operation; draft deletion is blocked"
                )
            self.store.delete("meals", meal_id)
        return self.response(meal_id=meal_id, deleted=True, diary_entries_deleted=0)

    def propose_label(self, **kwargs) -> dict:
        return self.response(proposal=normalize_label(**kwargs), saved=False)

    def save_label_product(self, proposal: dict, alias: str, reviewed: bool) -> dict:
        if reviewed is not True:
            raise ValidationError("Review the extracted label before saving the product")
        if not isinstance(self.catalog, DemoCatalog):
            raise ValidationError("Custom product saving is only implemented for the demo catalog")
        if set(proposal) != {
            "name",
            "basis_grams",
            "conversion_notes",
            "nutrients_per_100g",
            "missing_fields_are_unknown",
        }:
            raise ValidationError("Expected a validated label proposal")
        # Revalidate proposed values; never trust a round trip through the assistant.
        validated = normalize_label(
            proposal["name"],
            100,
            "g",
            {
                key: {"amount": row["amount"], "unit": row["unit"]}
                for key, row in proposal["nutrients_per_100g"].items()
            },
        )
        food = Food.from_dict(
            {
                "food_id": "demo-label:" + uuid4().hex,
                "name": validated["name"],
                "source": "USER_LABEL",
                "preparation": "as_sold",
                "synthetic": False,
                "nutrients_per_100g": validated["nutrients_per_100g"],
            }
        )
        alias = text(alias, "alias", 200)
        with self.store.locked():
            self.store.put("foods", food.food_id, food.to_dict())
            self.store.put("aliases", normalized(alias), {"alias": alias, "food_id": food.food_id})
        return self.response(food=food.to_dict(), saved_to="local_demo_catalog")

    @staticmethod
    def _matches(entry: DiaryEntry, item: dict, meal: dict) -> bool:
        return (
            entry.food_id == item["food_id"]
            and abs(entry.grams - item["grams"]) < 0.000001
            and entry.day == meal["day"]
            and entry.meal_group == meal["meal_group"]
        )

    def _persist_operation(self, meal: dict, operation: dict, state: str):
        operation["state"] = state
        meal["state"] = state
        self.store.put_many(
            [
                ("operations", meal["meal_id"], operation),
                ("meals", meal["meal_id"], meal),
            ]
        )

    def commit_meal(self, meal_id: str, expected_revision: int) -> dict:
        with self.store.locked():
            meal = self._meal(meal_id)
            if expected_revision != meal["revision"]:
                raise ValidationError("Review the current draft revision before logging")
            operation = self.store.get("operations", meal_id)
            if operation and operation["state"] == "committed":
                return self.response(operation=operation, already_logged=True)
            if operation is None:
                if not self._review(meal)["ready_to_log"]:
                    raise ValidationError("Resolve food questions and confirm consumption first")
                operation = {
                    "operation_id": uuid4().hex,
                    "meal_id": meal_id,
                    "revision": meal["revision"],
                    "state": "writing",
                    "steps": [{"item": row, "state": "pending"} for row in meal["items"]],
                }
                self._persist_operation(meal, operation, "writing")
            for step in operation["steps"]:
                if step["state"] == "verified":
                    continue
                if step["state"] in {"sending", "uncertain"}:
                    # The last attempt might have reached the remote diary. Never add it again.
                    self._persist_operation(meal, operation, "needs_reconciliation")
                    return self.response(operation=operation, retry_blocked=True)
                if step["state"] == "pending":
                    try:
                        before = self.diary.entries(meal["day"])
                    except Exception:
                        self._persist_operation(meal, operation, "waiting_for_diary")
                        return self.response(operation=operation, retry_safe=True)
                    step["baseline_ids"] = [entry.entry_id for entry in before]
                    step["state"] = "sending"
                    # Durable before the remote side effect; a crash now requires reconciliation.
                    self._persist_operation(meal, operation, "writing")
                    try:
                        entry = self.diary.add(
                            food_id=step["item"]["food_id"],
                            grams=step["item"]["grams"],
                            day=meal["day"],
                            meal_group=meal["meal_group"],
                        )
                    except Exception:
                        # Persist no exception text: it might include credentials or request data.
                        step["state"] = "uncertain"
                        self._persist_operation(meal, operation, "needs_reconciliation")
                        return self.response(operation=operation, retry_blocked=True)
                    step["entry_id"] = entry.entry_id
                    step["state"] = "needs_verification"
                    self._persist_operation(meal, operation, "writing")
                try:
                    after = self.diary.entries(meal["day"])
                except Exception:
                    self._persist_operation(meal, operation, "needs_verification")
                    return self.response(operation=operation, retry_safe=True)
                found = next((e for e in after if e.entry_id == step["entry_id"]), None)
                if found is None or not self._matches(found, step["item"], meal):
                    self._persist_operation(meal, operation, "needs_reconciliation")
                    return self.response(operation=operation, retry_blocked=True)
                if found.entry_id in step["baseline_ids"]:
                    self._persist_operation(meal, operation, "needs_reconciliation")
                    return self.response(operation=operation, retry_blocked=True)
                step["state"] = "verified"
                self._persist_operation(meal, operation, "writing")
            self._persist_operation(meal, operation, "committed")
            return self.response(operation=operation, already_logged=False)

    def reconcile_entry(self, meal_id: str, item_index: int, entry_id: str) -> dict:
        """Explicitly associate an uncertain write with a verified, newly created entry."""
        with self.store.locked():
            meal = self._meal(meal_id)
            operation = self.store.get("operations", meal_id)
            if operation is None or not 0 <= item_index < len(operation["steps"]):
                raise ValidationError("No matching operation step")
            step = operation["steps"][item_index]
            if step["state"] not in {"sending", "uncertain", "needs_verification"}:
                raise ValidationError("This step does not need reconciliation")
            if entry_id in step.get("baseline_ids", []):
                raise ValidationError("Entry predates the write attempt")
            for op in self.store.values("operations"):
                for index, other in enumerate(op["steps"]):
                    if op["meal_id"] == meal_id and index == item_index:
                        continue
                    if other.get("entry_id") == entry_id:
                        raise ValidationError("Entry is already assigned to a logging operation")
            entry = next(
                (e for e in self.diary.entries(meal["day"]) if e.entry_id == entry_id), None
            )
            if entry is None or not self._matches(entry, step["item"], meal):
                raise ValidationError("Entry does not match this item, amount, date, and group")
            step["entry_id"], step["state"] = entry_id, "verified"
            state = (
                "committed"
                if all(s["state"] == "verified" for s in operation["steps"])
                else "writing"
            )
            self._persist_operation(meal, operation, state)
        return self.response(operation=operation)

    def status(self, meal_id: str) -> dict:
        meal = self._meal(meal_id)
        return self.response(
            meal=meal,
            operation=self.store.get("operations", meal_id),
            diary=[entry.to_dict() for entry in self.diary.entries(meal["day"])],
        )
