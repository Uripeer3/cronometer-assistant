"""Local consistent SQLite/evidence backups. Credentials and inbox files are excluded."""

import hashlib
import json
import os
import re
import sqlite3
import stat
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from .evidence import MAX_IMAGE_BYTES, private_directory
from .models import ValidationError

MAX_DATABASE_BYTES = 1024 * 1024 * 1024


def _read(path, maximum):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o077
                or info.st_size > maximum
            ):
                raise ValidationError("Backup input must be a bounded private regular file")
            data = source.read(maximum + 1)
    except OSError:
        raise ValidationError("Backup input is missing or cannot be safely read") from None
    if len(data) > maximum:
        raise ValidationError("Backup input exceeds its size limit")
    return data


def _write(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())


def _directory(path):
    path = Path(path).absolute()
    private_directory(path.parent)
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        raise ValidationError(
            "Destination already exists; choose a new backup/restore directory"
        ) from None
    return path


def _existing_directory(path):
    path = Path(path).absolute()
    if not path.is_dir():
        raise ValidationError("Backup directory does not exist")
    return private_directory(path)


def _integrity(path):
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            if db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise ValidationError("Backup database failed SQLite integrity checking")
            if db.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise ValidationError("Unsupported backup database schema")
            columns = {row[1] for row in db.execute("PRAGMA table_info(documents)")}
            if columns != {"namespace", "key", "payload"}:
                raise ValidationError("Backup is not an application database")
    except sqlite3.Error:
        raise ValidationError("Backup database is unreadable") from None


def _record(path, data):
    return {"path": path, "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}


def create_backup(store, destination):
    destination = _directory(destination)
    # Cooperating application mutations share this host-local lock. SQLite's
    # backup API also includes committed WAL pages, unlike copying a live file.
    with store.locked():
        db_path = destination / "state.sqlite"
        _write(db_path, b"")
        with store.connection() as source, closing(sqlite3.connect(db_path)) as target:
            source.backup(target)
        _integrity(db_path)
        rows = [_record("state.sqlite", _read(db_path, MAX_DATABASE_BYTES))]
        evidence = store.values("product_evidence")
        hashes = {row["sha256"] for row in evidence if row["storage"] == "private_local_archive"}
        if hashes:
            source_root = _existing_directory(store.path.parent / "evidence")
            target_root = private_directory(destination / "evidence")
            for digest in sorted(hashes):
                if not re.fullmatch(r"[a-f0-9]{64}", digest):
                    raise ValidationError("Invalid evidence hash in product metadata")
                data = _read(source_root / digest, MAX_IMAGE_BYTES)
                if hashlib.sha256(data).hexdigest() != digest:
                    raise ValidationError("Evidence integrity failed; backup was not completed")
                _write(target_root / digest, data)
                rows.append(_record("evidence/" + digest, data))
        manifest = {
            "version": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "files": rows,
            "credentials_included": False,
            "host_references_materialized": False,
        }
        # Written last. An interrupted folder without this file is not a backup.
        _write(destination / "manifest.json", json.dumps(manifest, sort_keys=True).encode())
        fd = os.open(destination, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    return verify_backup(destination)


def verify_backup(directory):
    directory = _existing_directory(directory)
    try:
        manifest = json.loads(_read(directory / "manifest.json", 1024 * 1024))
    except (ValueError, UnicodeError):
        raise ValidationError("Backup manifest is invalid") from None
    if (
        not isinstance(manifest, dict)
        or manifest.get("version") != 1
        or not isinstance(manifest.get("files"), list)
    ):
        raise ValidationError("Unsupported backup manifest")
    seen = set()
    for row in manifest["files"]:
        if not isinstance(row, dict):
            raise ValidationError("Invalid backup file record")
        name = row.get("path")
        if (
            not isinstance(name, str)
            or name in seen
            or not re.fullmatch(r"state\.sqlite|evidence/[a-f0-9]{64}", name)
            or type(row.get("size_bytes")) is not int
            or not isinstance(row.get("sha256"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", row["sha256"])
        ):
            raise ValidationError("Invalid backup filename or integrity record")
        seen.add(name)
        if name.startswith("evidence/") and name != "evidence/" + row["sha256"]:
            raise ValidationError("Evidence name must match its content hash")
        if name.startswith("evidence/"):
            _existing_directory(directory / "evidence")
        data = _read(
            directory / name, MAX_DATABASE_BYTES if name == "state.sqlite" else MAX_IMAGE_BYTES
        )
        if len(data) != row["size_bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise ValidationError("Backup file failed its integrity check")
    if "state.sqlite" not in seen:
        raise ValidationError("Backup has no database")
    _integrity(directory / "state.sqlite")
    # Verify the database's references, not just files a manifest happens to list.
    with closing(
        sqlite3.connect((directory / "state.sqlite").as_uri() + "?mode=ro", uri=True)
    ) as db:
        for (payload,) in db.execute(
            "SELECT payload FROM documents WHERE namespace='product_evidence'"
        ):
            row = json.loads(payload)
            if (
                row.get("storage") == "private_local_archive"
                and "evidence/" + row["sha256"] not in seen
            ):
                raise ValidationError("Backup is missing referenced product evidence")
    return {"verified": True, "file_count": len(seen), "credentials_included": False}


def restore_backup(source, destination):
    source = _existing_directory(source)
    verify_backup(source)
    manifest = json.loads(_read(source / "manifest.json", 1024 * 1024))
    destination = _directory(destination)
    for row in manifest["files"]:
        name = row["path"]
        if name.startswith("evidence/"):
            private_directory(destination / "evidence")
        data = _read(
            source / name, MAX_DATABASE_BYTES if name == "state.sqlite" else MAX_IMAGE_BYTES
        )
        if hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise ValidationError("Backup changed during restore")
        _write(destination / name, data)
    _write(destination / "manifest.json", json.dumps(manifest, sort_keys=True).encode())
    return {**verify_backup(destination), "restored": True, "database_filename": "state.sqlite"}
