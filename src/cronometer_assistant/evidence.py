"""Private product evidence. Never fetch URLs or read model-selected server paths."""

import hashlib
import os
import re
import stat
import tempfile
from pathlib import Path

from .models import ValidationError, text
from .store import Store

ROLES = {"package_front", "nutrition_label", "barcode", "ingredients"}
MAX_IMAGE_BYTES = 10 * 1024 * 1024


def private_directory(path: Path) -> Path:
    path = path.absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValidationError("Evidence directories must not contain symbolic links")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise ValidationError("Evidence directory must be owned by you with mode 0700")
    return path


def image_type(data: bytes) -> str:
    # A bounded signature check, not a full image decoder or OCR verification.
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24 and data[12:16] == b"IHDR":
        return "image/png"
    if data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9"):
        return "image/jpeg"
    if len(data) >= 16 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise ValidationError("Expected a JPEG, PNG or WebP image signature")


class EvidenceArchive:
    def __init__(self, store: Store, root: Path, *, inbox: Path | None = None):
        self.store = store
        self.root = private_directory(root)
        self.inbox = private_directory(inbox) if inbox is not None else None

    @staticmethod
    def _role(role):
        if role not in ROLES:
            raise ValidationError("Choose package_front, nutrition_label, barcode or ingredients")
        return role

    def reference(self, proposal_id, role, provider, reference):
        self._role(role)
        if provider not in {"chatgpt_library", "host"}:
            raise ValidationError("Unknown evidence reference provider")
        reference = text(reference, "durable reference", 300)
        if provider == "chatgpt_library" and not re.fullmatch(r"libfile_[a-zA-Z0-9_]+", reference):
            raise ValidationError("Provide a Library file ID, never a download URL")
        if "://" in reference or "/" in reference or "\\" in reference:
            raise ValidationError("Use an opaque durable file ID, not a URL or path")
        identity = hashlib.sha256(
            f"{proposal_id}:{role}:{provider}:{reference}".encode()
        ).hexdigest()
        value = {
            "evidence_id": identity,
            "proposal_id": proposal_id,
            "role": role,
            "provider": provider,
            "reference": reference,
            "storage": "host_reference_only",
            "local_bytes_verified": False,
            "cronometer_uploaded": False,
        }
        with self.store.locked():
            self.store.put("product_evidence", identity, value)
        return value

    def _read(self, directory, name):
        try:
            fd = os.open(directory / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_IMAGE_BYTES:
                    raise ValidationError("Evidence must be a regular image of at most 10 MiB")
                data = source.read(MAX_IMAGE_BYTES + 1)
        except OSError:
            raise ValidationError(
                "Evidence file is unavailable; ask the host to transfer it"
            ) from None
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise ValidationError("Evidence must be a nonempty image of at most 10 MiB")
        return data

    def import_file(self, proposal_id, role, inbox_name):
        self._role(role)
        if self.inbox is None:
            raise ValidationError("Photo import is disabled; configure a private evidence inbox")
        inbox_name = text(inbox_name, "inbox filename", 200)
        if inbox_name in {".", ".."} or any(c in inbox_name for c in "/\\\x00"):
            raise ValidationError("Use a filename inside the configured inbox")
        data = self._read(private_directory(self.inbox), inbox_name)
        mime = image_type(data)
        digest = hashlib.sha256(data).hexdigest()
        identity = hashlib.sha256(f"{proposal_id}:{role}:{digest}".encode()).hexdigest()
        value = {
            "evidence_id": identity,
            "proposal_id": proposal_id,
            "role": role,
            "sha256": digest,
            "size_bytes": len(data),
            "mime_type": mime,
            "original_filename": inbox_name,
            "storage": "private_local_archive",
            "local_bytes_verified": True,
            "format_check": "signature_only",
            "cronometer_uploaded": False,
        }
        with self.store.locked():
            root = private_directory(self.root)
            target = root / digest
            if target.exists() or target.is_symlink():
                if hashlib.sha256(self._read(root, digest)).hexdigest() != digest:
                    raise ValidationError("Archived evidence failed its integrity check")
            else:
                with tempfile.NamedTemporaryFile(dir=root, delete=False) as output:
                    temporary = Path(output.name)
                    try:
                        os.fchmod(output.fileno(), 0o600)
                        output.write(data)
                        output.flush()
                        os.fsync(output.fileno())
                        os.replace(temporary, target)
                        directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
                        try:
                            os.fsync(directory_fd)
                        finally:
                            os.close(directory_fd)
                    finally:
                        temporary.unlink(missing_ok=True)
            self.store.put("product_evidence", identity, value)
        return value

    def review(self, proposal_id):
        rows = [v for v in self.store.values("product_evidence") if v["proposal_id"] == proposal_id]
        for row in rows:
            if row["storage"] == "private_local_archive":
                try:
                    data = self._read(private_directory(self.root), row["sha256"])
                    row["local_bytes_verified"] = hashlib.sha256(data).hexdigest() == row["sha256"]
                except ValidationError:
                    row["local_bytes_verified"] = False
        return rows
