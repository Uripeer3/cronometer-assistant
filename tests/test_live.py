"""Read-only boundary tests use invented responses, never account credentials."""

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cronometer_assistant.live import (
    LiveError,
    LoginRequired,
    ReadOnlyConnection,
    SessionFile,
    account_timezone,
    check_request,
    create_client,
)


class BoundaryTests(unittest.TestCase):
    def test_write_and_unexpected_requests_are_blocked(self):
        for method, url in [
            ("POST", "/api/v2/add_serving"),
            ("POST", "/api/v2/delete_entries"),
            ("DELETE", "/api/v3/user/1/diary-entries"),
            ("POST", "https://example.com/api/v2/get_food"),
            ("GET", "/api/v2/get_food"),
            ("POST", "/api/v2/get_diary"),
        ]:
            with self.subTest(url=url), self.assertRaises(LiveError):
                check_request(method, url, {})
        check_request("POST", "/api/v2/get_food", {"id": 1})
        for payload in ({}, {"timezone": "Asia/Tokyo"}, None):
            with self.assertRaises(LiveError):
                check_request("POST", "/api/v2/login", payload)
        check_request("POST", "/api/v2/login", {"timezone": None})

    def test_unknown_timezone_is_not_silently_replaced(self):
        for value in (None, "", "Invalid/Timezone"):
            with self.assertRaises(LiveError):
                account_timezone(value)
        self.assertEqual(account_timezone("Asia/Jerusalem"), "Asia/Jerusalem")

    def test_session_permissions_and_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private" / "session.json"
            session = SessionFile(path)
            self.assertIsNone(session.read())
            session.write(1, "synthetic-session-token", "Asia/Jerusalem")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(session.read()["user_id"], 1)
            self.assertNotIn("password", path.read_text())
            path.chmod(0o644)
            with self.assertRaises(LiveError):
                session.read()
            path.unlink()
            path.symlink_to(Path(directory) / "target")
            with self.assertRaises(LiveError):
                session.read()
            (Path(directory) / "link").symlink_to(path.parent, target_is_directory=True)
            with self.assertRaises(LiveError):
                SessionFile(Path(directory) / "link" / "session.json")

    def test_session_does_not_accept_shared_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory).chmod(0o755)
            with self.assertRaises(LiveError):
                SessionFile(Path(directory) / "session.json")

    def test_food_inspection_preserves_missing_zero_and_uncommon_units(self):
        class FakeClient:
            def get_food(self, fid):
                return {
                    "id": fid,
                    "name": "Synthetic food",
                    "source": 77,
                    "measures": [{"id": 4, "name": "portion", "value": 80}],
                    "nutrients": [
                        {"id": 1, "amount": 0},
                        {"id": 2},
                        {"id": 3, "amount": 1.2},
                    ],
                    "token": "must-not-return",
                    "userId": 123,
                }

            def get_nutrient_definitions(self):
                return {1: {"unit": "mg"}, 2: {"unit": "g"}, 3: {"unit": "IU"}}

        result = ReadOnlyConnection(FakeClient()).food("crono:1")
        rows = result["nutrients_per_100g"]
        self.assertEqual(rows[0]["amount"], 0)
        self.assertTrue(rows[0]["known"])
        self.assertIsNone(rows[1]["amount"])
        self.assertFalse(rows[1]["known"])
        self.assertEqual(rows[2]["unit"], "IU")
        self.assertEqual(result["measures"][0]["grams"], 80)
        self.assertFalse(result["source_verified"])
        self.assertNotIn("must-not-return", json.dumps(result))
        self.assertFalse(result["real_diary_changed"])

    def test_upstream_exception_text_is_not_returned(self):
        class FakeClient:
            def search_food(self, query):
                raise RuntimeError("secret-token sensitive-account-data")

        with self.assertRaises(LiveError) as error:
            ReadOnlyConnection(FakeClient()).search("rice")
        self.assertNotIn("secret-token", str(error.exception))


class PinnedClientTests(unittest.TestCase):
    def setUp(self):
        if importlib.util.find_spec("cronometer_mcp") is None:
            if os.environ.get("REQUIRE_LIVE_TESTS") == "1":
                self.fail("Pinned upstream client is required in CI")
            self.skipTest("Optional live dependency is not installed")
        import httpx

        self.httpx = httpx
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "private" / "session.json"
        self.requests = []

    def handler(self, request):
        payload = json.loads(request.content)
        self.requests.append((request.url.path, payload))
        if request.url.path == "/api/v2/login":
            self.assertIsNone(payload["timezone"])
            if payload["userCode"] is None:
                return self.httpx.Response(
                    200, json={"result": "FAIL", "error": "TOTP_CODE_REQUIRED"}
                )
            self.assertEqual(payload["userCode"], "123456")
            return self.httpx.Response(
                200,
                json={
                    "result": "SUCCESS",
                    "id": 1,
                    "sessionKey": "fake-token",
                    "timezone": "Asia/Jerusalem",
                },
            )
        return self.httpx.Response(
            200, json={"nutrients": [{"id": 208, "name": "Energy", "unit": "kcal"}]}
        )

    def client(self, interactive=False):
        client = create_client(
            self.path, interactive=interactive, transport=self.httpx.MockTransport(self.handler)
        )
        self.addCleanup(client._http.close)
        return client

    def test_hidden_login_with_one_time_code_and_session_reuse(self):
        with (
            patch("sys.stdin.isatty", return_value=True),
            patch(
                "getpass.getpass", side_effect=["example@invalid.test", "fake-password", "123456"]
            ) as prompt,
        ):
            client = self.client(interactive=True)
            client.login()
        self.assertEqual(prompt.call_count, 3)
        contents = self.path.read_text()
        self.assertNotIn("fake-password", contents)
        self.assertNotIn("123456", contents)
        self.assertNotIn("example@invalid.test", contents)
        self.assertIsNone(client._one_time_code)
        with patch.dict(os.environ, {"CRONOMETER_ACCOUNT_TZ": "Asia/Tokyo"}):
            reused = self.client()
        result = ReadOnlyConnection(reused).check()
        self.assertEqual(result["account_timezone"], "Asia/Jerusalem")
        self.assertEqual([p for p, _ in self.requests].count("/api/v2/login"), 2)
        self.assertNotIn("fake-token", json.dumps(result))

    def test_expired_session_does_not_use_environment_password_or_prompt(self):
        SessionFile(self.path).write(1, "expired", "Asia/Jerusalem")
        client = self.client()
        client._http._transport = self.httpx.MockTransport(lambda request: self.httpx.Response(401))
        with (
            patch.dict(os.environ, {"CRONOMETER_PASSWORD": "must-not-use"}),
            patch("getpass.getpass", side_effect=AssertionError("No prompt allowed")),
            self.assertRaises(LoginRequired),
        ):
            ReadOnlyConnection(client).check()

    def test_inherited_write_methods_blocked_before_transport(self):
        SessionFile(self.path).write(1, "fake-token", "Asia/Jerusalem")
        client = self.client()
        with self.assertRaises(LiveError):
            client._request("/api/v2/add_serving", {})
        with self.assertRaises(LiveError):
            client._http.delete("/api/v3/user/1/diary-entries")
        self.assertEqual(self.requests, [])

    def test_product_creation_with_real_pinned_request_helper(self):
        from copy import deepcopy

        from cronometer_assistant.products import ProductService
        from cronometer_assistant.store import Store

        SessionFile(self.path).write(1, "fake-token", "Asia/Jerusalem")
        remote = {}
        def handler(request):
            payload = json.loads(request.content)
            if request.url.path == "/api/v2/get_nutrients":
                return self.httpx.Response(200, json={
                    "nutrients": [{"id": 208, "name": "Energy", "unit": "kcal"}]
                })
            if request.url.path == "/api/v2/add_food":
                remote.update(deepcopy(payload["data"]))
                remote["owner"] = 1
                remote["id"] = 123
                remote["measures"][0]["id"] = 456
                self.assertEqual(payload["auth"]["token"], "fake-token")
                return self.httpx.Response(200, json={"id": 123})
            if request.url.path == "/api/v2/get_food":
                return self.httpx.Response(200, json=remote)
            raise AssertionError("Unexpected endpoint")
        client = create_client(
            self.path, allow_product_creation=True, transport=self.httpx.MockTransport(handler)
        )
        self.addCleanup(client._http.close)
        service = ProductService(Store(Path(self.temp.name) / "products.sqlite"), client)
        pid = service.prepare(
            "Synthetic food", 100, "g", {"energy": {"amount": 200, "unit": "kcal"}}
        )["product"]["proposal_id"]
        result = service.create(pid, True)
        self.assertTrue(result["created"])
        self.assertNotIn("fake-token", json.dumps(service.store.get("product_proposals", pid)))
        self.assertNotIn("fake-token", json.dumps(result))
        with self.assertRaises(LiveError):
            client._request("/api/v2/add_food", {"data": {"id": 123}})
        with self.assertRaises(LiveError):
            client._request("/api/v2/add_serving", {})
