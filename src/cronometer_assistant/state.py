"""Single, lightweight SQLite store for aliases, idempotency, and optional meal drafts."""

from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .nutrition import ValidationError, clean_text, finite_number, normalize_key

SCHEMA_VERSION = 1


class AssistantState:
    """Account-bound local SQLite storage with mode-0600 permissions and fcntl locking."""

    def __init__(self, path: Path, user_id: int) -> None:
        self.path = Path(path).resolve()
        self.user_id = user_id
        self._thread_lock = threading.RLock()
        self._op_thread_lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        self.path.chmod(0o600)
        self._init_db()

    @contextmanager
    def _locked(self):
        lock_path = str(self.path) + ".lock"
        with self._thread_lock:
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    @contextmanager
    def operation_lock(self):
        """Process- and thread-safe exclusive lock across multi-step write/reconcile/edit flows."""
        lock_path = str(self.path) + ".op.lock"
        with self._op_thread_lock:
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    @contextmanager
    def _conn(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        finally:
            db.close()

    def _init_db(self) -> None:
        with self._locked(), self._conn() as db:
            ver = db.execute("PRAGMA user_version").fetchone()[0]
            if ver not in (0, SCHEMA_VERSION):
                raise RuntimeError(f"Unsupported database schema version: {ver}")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS aliases (
                    alias_key TEXT PRIMARY KEY,
                    alias TEXT NOT NULL,
                    food_id INTEGER NOT NULL,
                    translation_id INTEGER NOT NULL DEFAULT 0,
                    food_name TEXT NOT NULL,
                    default_grams REAL,
                    default_measure_id INTEGER,
                    notes TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS idempotency (
                    request_id TEXT PRIMARY KEY,
                    action TEXT NOT NULL,
                    fingerprint TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'completed',
                    response_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS drafts (
                    draft_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    day TEXT,
                    meal_group TEXT,
                    local_time TEXT,
                    items_json TEXT NOT NULL,
                    notes TEXT,
                    updated_at TEXT NOT NULL
                );
                """
            )
            # Upgrade existing v1 idempotency tables seamlessly if columns were absent
            cols = {r["name"] for r in db.execute("PRAGMA table_info(idempotency)").fetchall()}
            if "fingerprint" not in cols:
                db.execute(
                    "ALTER TABLE idempotency ADD COLUMN fingerprint TEXT NOT NULL DEFAULT ''"
                )
            if "status" not in cols:
                db.execute(
                    "ALTER TABLE idempotency ADD COLUMN status TEXT NOT NULL DEFAULT 'completed'"
                )
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            row = db.execute("SELECT value FROM meta WHERE key='user_id'").fetchone()
            if row is not None and int(row["value"]) != self.user_id:
                raise ValidationError(
                    "This state database belongs to a different Cronometer account; "
                    "pass a separate --db path."
                )
            db.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES ('user_id', ?)",
                (str(self.user_id),),
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    # ------------------------------------------------------------------
    # Idempotency (durable write journal + retry deduplication)
    # ------------------------------------------------------------------

    def get_idempotent(
        self, request_id: str, action: str, *, fingerprint: str | None = None
    ) -> dict[str, Any] | None:
        record = self.get_operation(request_id, action, fingerprint=fingerprint)
        if record is None or record["status"] != "completed":
            return None
        return record["data"]

    def get_operation(
        self,
        request_id: str,
        action: str,
        *,
        fingerprint: str | None = None,
        verify_fingerprint: bool = True,
    ) -> dict[str, Any] | None:
        key = clean_text(request_id, "request_id", 200)
        with self._conn() as db:
            row = db.execute(
                "SELECT action, fingerprint, status, response_json FROM idempotency WHERE request_id=?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        if row["action"] != action:
            raise ValidationError(
                f"request_id {key!r} was already used for a different action ({row['action']})"
            )
        if (
            verify_fingerprint
            and fingerprint is not None
            and row["fingerprint"]
            and row["fingerprint"] != fingerprint
        ):
            raise ValidationError(
                f"request_id {key!r} was already used for a different request payload; "
                "use a new request_id for a different meal or food"
            )
        return {
            "request_id": key,
            "action": row["action"],
            "fingerprint": row["fingerprint"],
            "status": row["status"],
            "data": json.loads(row["response_json"]),
        }

    def save_operation(
        self,
        request_id: str,
        action: str,
        *,
        fingerprint: str,
        status: str,
        data: dict[str, Any],
    ) -> None:
        key = clean_text(request_id, "request_id", 200)
        payload = json.dumps(data, ensure_ascii=False, allow_nan=False)
        with self._locked(), self._conn() as db:
            db.execute(
                "INSERT OR REPLACE INTO idempotency"
                "(request_id, action, fingerprint, status, response_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (key, action, fingerprint, status, payload, self._now()),
            )

    def save_idempotent(
        self,
        request_id: str,
        action: str,
        response: dict[str, Any],
        *,
        fingerprint: str = "",
    ) -> None:
        self.save_operation(
            request_id, action, fingerprint=fingerprint, status="completed", data=response
        )

    # ------------------------------------------------------------------
    # Personal food aliases ("my usual yogurt", Hebrew shorthands)
    # ------------------------------------------------------------------

    def set_alias(
        self,
        alias: str,
        *,
        food_id: int,
        food_name: str,
        translation_id: int = 0,
        default_grams: float | None = None,
        default_measure_id: int | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        alias_clean = clean_text(alias, "alias", 200)
        alias_key = normalize_key(alias_clean)
        if not alias_key:
            raise ValidationError("alias must contain letters or digits")
        if default_grams is not None:
            default_grams = finite_number(default_grams, "default_grams", positive=True)
        now = self._now()
        with self._locked(), self._conn() as db:
            db.execute(
                """
                INSERT INTO aliases(
                    alias_key, alias, food_id, translation_id, food_name,
                    default_grams, default_measure_id, notes, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(alias_key) DO UPDATE SET
                    alias=excluded.alias,
                    food_id=excluded.food_id,
                    translation_id=excluded.translation_id,
                    food_name=excluded.food_name,
                    default_grams=excluded.default_grams,
                    default_measure_id=excluded.default_measure_id,
                    notes=excluded.notes,
                    updated_at=excluded.updated_at
                """,
                (
                    alias_key,
                    alias_clean,
                    food_id,
                    translation_id,
                    food_name,
                    default_grams,
                    default_measure_id,
                    notes,
                    now,
                ),
            )
        return self.get_alias(alias_clean)  # type: ignore[return-value]

    def get_alias(self, alias: str) -> dict[str, Any] | None:
        key = normalize_key(alias)
        with self._conn() as db:
            row = db.execute("SELECT * FROM aliases WHERE alias_key=?", (key,)).fetchone()
        return dict(row) if row else None

    def list_aliases(self, query: str = "") -> list[dict[str, Any]]:
        q = normalize_key(query) if query.strip() else ""
        with self._conn() as db:
            rows = db.execute("SELECT * FROM aliases ORDER BY updated_at DESC").fetchall()
        results = [dict(r) for r in rows]
        if q:
            q_tokens = set(q.split("_"))
            results = [
                r
                for r in results
                if q in r["alias_key"]
                or q in normalize_key(r["food_name"])
                or bool(q_tokens & set(r["alias_key"].split("_")))
            ]
        return results

    def delete_alias(self, alias: str) -> bool:
        key = normalize_key(alias)
        with self._locked(), self._conn() as db:
            cur = db.execute("DELETE FROM aliases WHERE alias_key=?", (key,))
        return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Optional cooking scratchpad (meal drafts across chat turns/sessions)
    # ------------------------------------------------------------------

    def save_draft(
        self,
        title: str,
        items: list[dict[str, Any]],
        *,
        draft_id: str | None = None,
        day: str | None = None,
        meal_group: str | None = None,
        local_time: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        title_clean = clean_text(title, "title", 200)
        did = clean_text(draft_id, "draft_id", 64) if draft_id else uuid4().hex[:12]
        now = self._now()
        items_json = json.dumps(items, ensure_ascii=False, allow_nan=False)
        with self._locked(), self._conn() as db:
            db.execute(
                """
                INSERT INTO drafts(
                    draft_id, title, day, meal_group, local_time, items_json, notes, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(draft_id) DO UPDATE SET
                    title=excluded.title,
                    day=excluded.day,
                    meal_group=excluded.meal_group,
                    local_time=excluded.local_time,
                    items_json=excluded.items_json,
                    notes=excluded.notes,
                    updated_at=excluded.updated_at
                """,
                (did, title_clean, day, meal_group, local_time, items_json, notes, now),
            )
        return self.get_draft(did)  # type: ignore[return-value]

    def get_draft(self, draft_id: str) -> dict[str, Any] | None:
        with self._conn() as db:
            row = db.execute("SELECT * FROM drafts WHERE draft_id=?", (draft_id,)).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["items"] = json.loads(data.pop("items_json"))
        return data

    def list_drafts(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._conn() as db:
            rows = db.execute(
                "SELECT * FROM drafts ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        out = []
        for row in rows:
            data = dict(row)
            data["items"] = json.loads(data.pop("items_json"))
            out.append(data)
        return out

    def delete_draft(self, draft_id: str) -> bool:
        with self._locked(), self._conn() as db:
            cur = db.execute("DELETE FROM drafts WHERE draft_id=?", (draft_id,))
        return cur.rowcount > 0
