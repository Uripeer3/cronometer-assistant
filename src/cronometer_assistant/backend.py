"""Write boundary. This milestone implements only a persistent simulated diary."""

from typing import Protocol
from uuid import uuid4

from .models import DiaryEntry
from .store import Store


class DiaryBackend(Protocol):
    mode: str

    def entries(self, day: str) -> list[DiaryEntry]: ...

    def add(self, *, food_id: str, grams: float, day: str, meal_group: str) -> DiaryEntry: ...


class DemoDiary:
    mode = "demo"

    def __init__(self, store: Store):
        self.store = store

    def entries(self, day: str) -> list[DiaryEntry]:
        return [DiaryEntry(**row) for row in self.store.values("demo_diary") if row["day"] == day]

    def add(self, *, food_id: str, grams: float, day: str, meal_group: str) -> DiaryEntry:
        entry = DiaryEntry("demo-entry:" + uuid4().hex, food_id, grams, day, meal_group)
        self.store.put("demo_diary", entry.entry_id, entry.to_dict())
        return entry
