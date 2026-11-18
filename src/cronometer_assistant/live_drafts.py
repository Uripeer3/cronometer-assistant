"""Account-bound local meal drafts using reviewed Cronometer profiles, without writes."""

import json
import re
from dataclasses import replace
from zoneinfo import ZoneInfo

from .catalog import CronometerReadOnlyCatalog
from .live import account_timezone
from .models import Food, ValidationError
from .service import MealService
from .store import ScopedStore


class ReviewedCatalog:
    def __init__(self, store, client):
        self.store = store
        self.upstream = CronometerReadOnlyCatalog(client)

    def verify(self, food_id, preparation, reviewed, per_100g_confirmed):
        if reviewed is not True or per_100g_confirmed is not True:
            raise ValidationError("Review exact identity, preparation and per-100-g basis first")
        if preparation not in {"raw", "cooked", "as_sold"}:
            raise ValidationError("Confirm raw, cooked or as_sold preparation")
        if not isinstance(food_id, str) or not re.fullmatch(r"crono:[1-9][0-9]*", food_id):
            raise ValidationError("Select an inspected Cronometer food ID")
        food = self.upstream.get(food_id)
        with self.store.locked():
            self.store.put(
                "verified_foods",
                food_id,
                {
                    "observed": json.loads(json.dumps(food.to_dict())),
                    "preparation": preparation,
                    "per_100g_confirmed": True,
                },
            )
        return replace(food, preparation=preparation)

    def _reviewed(self, food):
        row = self.store.get("verified_foods", food.food_id)
        if row and row["observed"] == json.loads(json.dumps(food.to_dict())):
            return replace(food, preparation=row["preparation"])
        return None

    def search(self, query):
        return [self._reviewed(f) or f for f in self.upstream.search(query)]

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
    def __init__(self, store, client):
        scoped = ScopedStore(store, "live_drafts:")
        timezone = account_timezone(client._timezone)
        account = client.user_id
        with scoped.locked():
            bound = scoped.get("settings", "account")
            if bound and bound["user_id"] != account:
                raise ValidationError("Use a separate draft database for this Cronometer account")
            scoped.put("settings", "account", {"user_id": account})
        super().__init__(
            scoped, catalog=ReviewedCatalog(scoped, client), timezone=timezone, draft_only=True
        )
        self.client = client

    def begin_meal(self, *args, **kwargs):
        # Do not silently move a running conversation to a different calendar zone.
        if ZoneInfo(account_timezone(self.client._timezone)) != self.timezone:
            raise ValidationError("Account timezone changed; reconnect before starting a draft")
        return super().begin_meal(*args, **kwargs)

    def verify_food(self, food_id, preparation, reviewed, per_100g_confirmed):
        food = self.catalog.verify(food_id, preparation, reviewed, per_100g_confirmed)
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
