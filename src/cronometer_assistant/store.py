"""Single-user SQLite storage with durable writes and a cross-process operation lock."""

import fcntl
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        self.path.chmod(0o600)
        with self.connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise RuntimeError("Database schema is newer than this application")
            db.execute("""CREATE TABLE IF NOT EXISTS documents (
                namespace TEXT NOT NULL, key TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(namespace, key))""")
            db.execute("PRAGMA user_version=1")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def locked(self):
        """Serialize draft mutations/commits across threads and processes on this host."""
        fd = os.open(str(self.path) + ".lockfile", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def get(self, namespace: str, key: str) -> dict | None:
        with self.connection() as db:
            row = db.execute(
                "SELECT payload FROM documents WHERE namespace=? AND key=?", (namespace, key)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, namespace: str, key: str, value: dict):
        self.put_many([(namespace, key, value)])

    def put_many(self, documents: list[tuple[str, str, dict]]):
        rows = [(namespace, key,
                 json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True))
                for namespace, key, value in documents]
        with self.connection() as db:
            db.executemany(
                "INSERT INTO documents VALUES (?, ?, ?) "
                "ON CONFLICT(namespace,key) DO UPDATE SET payload=excluded.payload",
                rows,
            )

    def values(self, namespace: str) -> list[dict]:
        with self.connection() as db:
            rows = db.execute(
                "SELECT payload FROM documents WHERE namespace=? ORDER BY key", (namespace,)
            ).fetchall()
        return [json.loads(row[0]) for row in rows]
