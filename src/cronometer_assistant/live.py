"""Local interactive authentication and a narrowly restricted, read-only client.

The upstream package is imported only by create_client. No credentials are read
from environment variables, passed through MCP, or retained for automatic login.
"""

import getpass
import json
import logging
import os
import stat
import sys
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import ValidationError, number, text

READ_ENDPOINTS = frozenset({"/api/v2/find_food", "/api/v2/get_food", "/api/v2/get_nutrients"})


class LiveError(Exception):
    """A scrubbed diagnostic safe to return to the assistant."""


class LoginRequired(LiveError):
    pass


def check_request(method: str, url: str, payload: dict | None):
    """Fail closed before HTTP, including accidental calls to upstream write methods."""
    if method.upper() != "POST" or url not in READ_ENDPOINTS | {"/api/v2/login"}:
        raise LiveError("This connection permits only authentication and food reads")
    if url == "/api/v2/login" and (
        not isinstance(payload, dict) or payload.get("timezone", "missing") is not None
    ):
        raise LiveError("Login must leave the account timezone unchanged")


def account_timezone(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise LiveError("Cronometer did not supply an account timezone; do not guess it")
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise LiveError("Cronometer supplied an unsupported account timezone") from None
    return value


class SessionFile:
    """A dedicated private directory; atomic 0600 token files, never passwords."""

    def __init__(self, path: Path):
        # Do not resolve away symlinks before checking them.
        self.path = path.absolute()
        for parent in (self.path.parent, *self.path.parent.parents):
            if parent.is_symlink():
                raise LiveError("Session path must not contain symbolic links")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        mode = self.path.parent.stat()
        if mode.st_uid != os.getuid() or stat.S_IMODE(mode.st_mode) & 0o077:
            raise LiveError("Use a dedicated session directory owned by you with mode 0700")

    def read(self) -> dict | None:
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return None
        except OSError:
            raise LiveError("Cannot safely open the session file") from None
        with os.fdopen(fd) as source:
            info = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o077
            ):
                raise LiveError("Session file must be a private regular file with mode 0600")
            try:
                data = json.loads(source.read(65537))
            except (ValueError, UnicodeError):
                raise LiveError("Session file is invalid; sign in again locally") from None
        if (
            not isinstance(data, dict)
            or data.get("version") != 1
            or type(data.get("user_id")) is not int
            or not isinstance(data.get("token"), str)
            or not data["token"]
        ):
            raise LiveError("Session file is invalid; sign in again locally")
        account_timezone(data.get("timezone"))
        return data

    def write(self, user_id: int, token: str, timezone: str):
        data = {
            "version": 1,
            "user_id": user_id,
            "token": token,
            "timezone": account_timezone(timezone),
        }
        with tempfile.NamedTemporaryFile(mode="w", dir=self.path.parent, delete=False) as target:
            temporary = Path(target.name)
            try:
                os.fchmod(target.fileno(), 0o600)
                json.dump(data, target, allow_nan=False)
                target.flush()
                os.fsync(target.fileno())
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)


def create_client(session_path: Path, *, interactive: bool = False, transport=None):
    try:
        import httpx
        from cronometer_mcp.client import CronometerClient, CronometerError
    except ImportError:
        raise LiveError("Install the locked live extra before connecting to Cronometer") from None

    # Upstream INFO includes a session-token prefix. Suppress that logger entirely.
    # HTTP debug logging can include sensitive headers; this CLI owns its process.
    for name in ("cronometer_mcp.client", "httpx", "httpcore"):
        logger = logging.getLogger(name)
        logger.disabled = True
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
    session = SessionFile(session_path)

    class RestrictedHTTP(httpx.Client):
        def request(self, method, url, **kwargs):
            check_request(method, str(url), kwargs.get("json"))
            return super().request(method, url, **kwargs)

    class LocalSessionClient(CronometerClient):
        def _load_cached_session(self):
            if interactive:
                return  # An explicit sign-in always uses the account entered now.
            data = session.read()
            if data:
                self._user_id = data["user_id"]
                self._token = data["token"]
                self._timezone = data["timezone"]

        def _save_cached_session(self):
            session.write(self._user_id, self._token, self._timezone)

        def _resolve_timezone(self, response_tz):
            return account_timezone(response_tz)  # No environment override or fallback.

        def _get_credentials(self):
            if not interactive:
                raise LoginRequired("Session expired or absent; run login in your terminal")
            if not sys.stdin.isatty():
                raise LiveError("Run login in an interactive terminal")
            return (
                getpass.getpass("Cronometer email (hidden): ").strip(),
                getpass.getpass("Cronometer password (hidden): "),
            )

        def _totp_code(self):
            return getattr(self, "_one_time_code", None)

        def login(self):
            if not interactive:
                raise LoginRequired("Session expired or absent; run login in your terminal")
            # Prompt credentials once, keep only in this call's local scope.
            credentials = self._get_credentials()
            self._get_credentials = lambda: credentials
            try:
                try:
                    super().login()
                except CronometerError as error:
                    if "2FA code" not in str(error):
                        raise
                    self._one_time_code = getpass.getpass("Current 2FA code (hidden): ").strip()
                    if not self._one_time_code:
                        raise LiveError("A current 2FA code is required") from None
                    super().login()
            except LiveError:
                raise
            except Exception:
                raise LiveError(
                    "Cronometer login failed; check credentials, 2FA, or connection"
                ) from None
            finally:
                del self._get_credentials
                self._one_time_code = None
                credentials = None

    client = LocalSessionClient(session_path=session.path)
    client._http.close()
    client._http = RestrictedHTTP(
        base_url="https://mobile.cronometer.com",
        headers={
            "user-agent": "Dart/3.9 (dart:io)",
            "content-type": "text/plain; charset=utf-8",
        },
        timeout=30.0,
        follow_redirects=False,
        transport=transport,
    )
    return client


class ReadOnlyConnection:
    """Food inspection without diary data, credentials, or write methods in results."""

    def __init__(self, client):
        self.client = client

    @staticmethod
    def response(**data):
        return {"mode": "cronometer_read_only", "real_diary_changed": False, **data}

    def _read(self, function, *args):
        try:
            return function(*args)
        except LoginRequired:
            raise
        except Exception:
            # Never propagate upstream exception payloads containing tokens/account data.
            raise LiveError(
                "Cronometer read failed; check connection or sign in again locally"
            ) from None

    def check(self):
        # Make an authenticated read: a cached token alone is not proof of connectivity.
        self.client._nutrient_defs = None
        definitions = self._read(self.client.get_nutrient_definitions)
        return self.response(
            connected=True,
            account_timezone=account_timezone(self.client._timezone),
            nutrient_definition_count=len(definitions),
        )

    def search(self, query: str, limit: int = 5):
        query = text(query, "query", 300)
        if type(limit) is not int or not 1 <= limit <= 10:
            raise ValidationError("limit must be an integer from 1 to 10")
        rows = self._read(self.client.search_food, query)
        return self.response(
            candidates=[
                {
                    "food_id": f"crono:{row['id']}",
                    "name": row.get("name"),
                    "source_code": row.get("source"),
                    "source_verified": False,
                    "measure_id": row.get("measureId"),
                    "measure_name": row.get("measureDisplayName"),
                }
                for row in rows[:limit]
            ],
            source_note="Source codes are unverified; do not guess database names",
        )

    def food(self, food_id: str):
        if not isinstance(food_id, str) or not food_id.startswith("crono:"):
            raise ValidationError("Select a Cronometer food ID returned by search")
        suffix = food_id[6:]
        if not suffix.isascii() or not suffix.isdigit() or int(suffix) <= 0:
            raise ValidationError("Invalid Cronometer food ID")
        raw = self._read(self.client.get_food, int(suffix))
        definitions = self._read(self.client.get_nutrient_definitions)
        nutrients = []
        for row in raw.get("nutrients", []):
            meta = definitions.get(row.get("id"), {})
            amount = row.get("amount")
            if amount is not None:
                number(amount, "database nutrient")
            nutrients.append(
                {
                    "nutrient_id": row.get("id"),
                    "name": meta.get("name"),
                    "unit": meta.get("unit"),
                    "amount": amount,
                    "known": amount is not None,
                }
            )
        return self.response(
            food_id=food_id,
            name=raw.get("name"),
            source_code=raw.get("source"),
            source_verified=False,
            preparation="unspecified",
            default_measure_id=raw.get("defaultMeasureId"),
            measures=[
                {"measure_id": m.get("id"), "name": m.get("name"), "grams": m.get("value")}
                for m in raw.get("measures", [])
            ],
            nutrients_per_100g=nutrients,
            missing_fields_are_unknown=True,
            basis_note="Per-100-g basis follows the pinned client; verify against the app",
        )

    def close(self):
        self.client._http.close()
