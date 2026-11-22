"""Private local Cronometer session handling and safe client wrapper.

Credentials are never stored on disk, read from environment variables, or passed
through MCP. Login runs interactively in a local terminal and saves only the
account's session token, user_id, and timezone in a mode-0600 file.
"""

from __future__ import annotations

import getpass
import json
import logging
import os
import stat
import sys
import tempfile
import threading
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cronometer_mcp.client import CronometerClient, CronometerError

logger = logging.getLogger(__name__)


class AuthError(Exception):
    """Authentication or session configuration error safe to surface."""


class SessionExpiredError(AuthError):
    """Raised when the cached Cronometer session is missing or rejected."""


def validate_timezone(value: object) -> str:
    """Validate an IANA timezone name; never silently fall back to a guessed zone."""
    if not isinstance(value, str) or not value.strip():
        raise AuthError("Cronometer did not provide a valid account timezone")
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise AuthError(f"Unsupported account timezone: {value!r}") from None
    return value


class SessionFile:
    """Atomic, symlink-safe mode-0600 session file inside a mode-0700 directory."""

    def __init__(self, path: Path) -> None:
        self.path = path.absolute()
        for parent in (self.path.parent, *self.path.parent.parents):
            if parent.is_symlink():
                raise AuthError("Session path must not contain symbolic links")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        info = self.path.parent.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise AuthError("Session directory must be owned by you with mode 0700")

    def read(self) -> dict | None:
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return None
        except OSError:
            raise AuthError("Cannot safely open the session file") from None
        with os.fdopen(fd, "r", encoding="utf-8") as source:
            info = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o077
            ):
                raise AuthError("Session file must be a private regular file with mode 0600")
            try:
                data = json.loads(source.read(65537))
            except (ValueError, UnicodeError):
                raise AuthError(
                    "Session file is corrupted; run `cronometer-assistant login`"
                ) from None
        if (
            not isinstance(data, dict)
            or type(data.get("user_id")) is not int
            or not isinstance(data.get("token"), str)
            or not data["token"]
        ):
            raise AuthError("Session file is invalid; run `cronometer-assistant login`")
        validate_timezone(data.get("timezone"))
        return data

    def write(self, user_id: int, token: str, timezone: str) -> None:
        payload = {
            "version": 1,
            "user_id": user_id,
            "token": token,
            "timezone": validate_timezone(timezone),
        }
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=self.path.parent, delete=False
        ) as target:
            tmp = Path(target.name)
            try:
                os.fchmod(target.fileno(), 0o600)
                json.dump(payload, target, allow_nan=False)
                target.flush()
                os.fsync(target.fileno())
                os.replace(tmp, self.path)
            finally:
                tmp.unlink(missing_ok=True)


def _silence_sensitive_loggers() -> None:
    """Prevent upstream client/HTTP loggers from ever printing session token prefixes."""
    for name in ("cronometer_mcp.client", "httpx", "httpcore"):
        log = logging.getLogger(name)
        log.disabled = True
        log.addHandler(logging.NullHandler())
        log.propagate = False


class CronometerSessionClient(CronometerClient):
    """CronometerClient subclass with private local login and fresh timezone sync."""

    def __init__(
        self,
        session_path: Path,
        *,
        interactive: bool = False,
        transport=None,
    ) -> None:
        _silence_sensitive_loggers()
        self._session_file = SessionFile(session_path)
        self._interactive = interactive
        self._one_time_code: str | None = None
        self._tz_synced = False
        self._lock = threading.RLock()
        super().__init__(session_path=self._session_file.path)
        if transport is not None:
            import httpx

            self._http.close()
            self._http = httpx.Client(
                base_url="https://mobile.cronometer.com",
                headers={
                    "user-agent": "Dart/3.9 (dart:io)",
                    "content-type": "text/plain; charset=utf-8",
                    "accept-encoding": "gzip",
                },
                timeout=30.0,
                follow_redirects=False,
                transport=transport,
            )

    def _load_cached_session(self) -> None:
        if self._interactive:
            return
        data = self._session_file.read()
        if data:
            self._user_id = data["user_id"]
            self._token = data["token"]
            self._timezone = data["timezone"]

    def _save_cached_session(self) -> None:
        if self._user_id is None or self._token is None or self._timezone is None:
            return
        self._session_file.write(self._user_id, self._token, self._timezone)

    def _invalidate_session(self) -> None:
        # Clear in-memory token only; leave session.json intact so a transient
        # error never wipes a valid token on disk.
        self._token = None

    def _resolve_timezone(self, response_tz: str | None) -> str:
        return validate_timezone(response_tz)

    def _get_credentials(self) -> tuple[str, str]:
        if not self._interactive:
            raise SessionExpiredError(
                "Cronometer session is missing or expired. "
                "Run `cronometer-assistant login` in your terminal."
            )
        if not sys.stdin.isatty():
            raise AuthError("Run `cronometer-assistant login` in an interactive terminal")
        email = getpass.getpass("Cronometer email (hidden): ").strip()
        password = getpass.getpass("Cronometer password (hidden): ")
        if not email or not password:
            raise AuthError("Email and password are required")
        return email, password

    def _totp_code(self) -> str | None:
        return self._one_time_code

    def login(self) -> None:
        if not self._interactive:
            raise SessionExpiredError(
                "Cronometer session is missing or expired. "
                "Run `cronometer-assistant login` in your terminal."
            )
        credentials = self._get_credentials()
        original_getter = self._get_credentials
        self._get_credentials = lambda: credentials  # type: ignore[method-assign]
        try:
            try:
                super().login()
            except CronometerError as error:
                if "2FA code" not in str(error) and "TOTP" not in str(error):
                    raise
                code = getpass.getpass("Current 2FA code (hidden): ").strip()
                if not code:
                    raise AuthError("A current 2FA code is required") from None
                self._one_time_code = code
                super().login()
        except AuthError:
            raise
        except Exception as error:
            raise AuthError(f"Cronometer login failed: {type(error).__name__}") from None
        finally:
            self._get_credentials = original_getter  # type: ignore[method-assign]
            self._one_time_code = None
            credentials = ("", "")

    def _request(self, endpoint: str, payload: dict, *, _retried: bool = False) -> dict:
        with self._lock:
            return super()._request(endpoint, payload, _retried=_retried)

    def _request_v3(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        _retried: bool = False,
    ):
        with self._lock:
            return super()._request_v3(method, path, json_body=json_body, _retried=_retried)

    def sync_timezone(self, *, force: bool = True) -> str:
        """Refresh the account timezone from Cronometer's profile and persist if changed."""
        with self._lock:
            if not force and self._tz_synced and self._timezone:
                return self._timezone
            profile = self.get_profile()
            tz = validate_timezone(profile.get("timezone"))
            changed = tz != self._timezone
            self._timezone = tz
            self._tz_synced = True
            if changed and self._user_id is not None and self._token is not None:
                self._save_cached_session()
            return tz

    def close(self) -> None:
        self._http.close()
