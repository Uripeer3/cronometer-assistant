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
        rows = [
            (namespace, key, json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True))
            for namespace, key, value in documents
        ]
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

    def delete(self, namespace: str, key: str):
        with self.connection() as db:
            db.execute("DELETE FROM documents WHERE namespace=? AND key=?", (namespace, key))


class ScopedStore:
    """Share one database/lock while separating real drafts from demo documents."""

    def __init__(self, store: Store, prefix: str):
        self.store, self.prefix, self.path = store, prefix, store.path

    def locked(self):
        return self.store.locked()

    def get(self, namespace, key):
        return self.store.get(self.prefix + namespace, key)

    def put(self, namespace, key, value):
        self.store.put(self.prefix + namespace, key, value)

    def put_many(self, documents):
        self.store.put_many([(self.prefix + ns, key, value) for ns, key, value in documents])

    def values(self, namespace):
        return self.store.values(self.prefix + namespace)

    def delete(self, namespace, key):
        self.store.delete(self.prefix + namespace, key)
