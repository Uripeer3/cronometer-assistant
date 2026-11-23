"""End-to-end core service, mocked-HTTP CronometerClient, MCP tier & schema, and launcher tests."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Any

import httpx

from cronometer_assistant.auth import CronometerSessionClient
from cronometer_assistant.nutrition import ValidationError
from cronometer_assistant.server import SESSION_IDLE_TIMEOUT, create_server
from cronometer_assistant.service import CronometerAssistant
from cronometer_assistant.state import AssistantState
from scripts.run_tunnel import (
    acquire_client,
    choose_asset,
    find_cached_client,
    install_archive,
    launch_arguments,
    validate_client_version,
    validate_tunnel_id,
)


class FakeCronometerBackend:
    """Stateful fake mobile.cronometer.com backend wired via httpx.MockTransport."""

    def __init__(self, *, user_id: int = 1001, timezone: str = "Asia/Jerusalem") -> None:
        self.user_id = user_id
        self.timezone = timezone
        self.next_id = 5000
        self.next_food_id = 9000
        self.next_measure_id = 80000
        self.diary: dict[str, list[dict[str, Any]]] = {}
        self.day_complete: dict[str, bool] = {}
        self.targets: dict[int, dict[str, Any]] = {
            203: {"id": 203, "min": 120.0, "max": 180.0, "custom": True, "vis": True},
            324: {"id": 324, "min": 600.0, "custom": False, "vis": True},
        }
        self.tamper_serving_grams: float | None = None
        self.ignore_nutrient_updates: bool = False
        self.ignore_recipe_updates: bool = False
        self.fail_next_get_food: bool = False
        self.fail_get_foods: bool = False
        self.scores_mode: str = "normal"
        self.inject_stale_recipe_nutrient: dict[str, Any] | None = None
        self.add_food_calls: int = 0
        self.get_food_delay: float = 0.0
        self.diary_read_delay: float = 0.0
        self.fasts: list[dict[str, Any]] = []
        self.foods: dict[int, dict[str, Any]] = {
            460334: {
                "id": 460334,
                "name": "White Rice, Cooked in Unsalted Water",
                "source": "NCCDB:11272",
                "category": 6,
                "owner": None,
                "retired": False,
                "meal": False,
                "labelType": "AMERICAN",
                "defaultMeasureId": 1048859,
                "barcodes": [],
                "measures": [
                    {
                        "id": 1048859,
                        "name": "cup",
                        "type": "Weight",
                        "amount": 1,
                        "value": 158.0,
                        "hidden": False,
                    },
                    {
                        "id": 1048861,
                        "name": "g",
                        "type": "Weight",
                        "amount": 1,
                        "value": 1.0,
                        "hidden": False,
                    },
                ],
                "nutrients": [
                    {"id": 208, "amount": 130.0},
                    {"id": 203, "amount": 2.7},
                    {"id": 205, "amount": 28.0},
                    {"id": 291, "amount": 0.4},
                    {"id": 204, "amount": 0.3},
                    {"id": 262, "amount": 0.0},
                ],
            },
            72014363: {
                "id": 72014363,
                "name": "Pretzels, Beigale, Rings",
                "source": "Custom",
                "category": 21,
                "owner": user_id,
                "retired": False,
                "meal": False,
                "labelType": "AMERICAN_2016",
                "defaultMeasureId": 2001,
                "barcodes": ["7290111568961"],
                "measures": [
                    {
                        "id": 2001,
                        "name": "pieces",
                        "type": "Weight",
                        "amount": 22,
                        "value": 30.0,
                        "hidden": False,
                    },
                    {
                        "id": 2002,
                        "name": "g",
                        "type": "Weight",
                        "amount": 1,
                        "value": 1.0,
                        "hidden": False,
                    },
                ],
                "nutrients": [
                    {"id": 208, "amount": 380.0},
                    {"id": 203, "amount": 10.0},
                    {"id": 205, "amount": 78.0},
                    {"id": 291, "amount": 3.0},
                    {"id": 204, "amount": 3.0},
                    {"id": 262, "amount": 5.0},
                ],
            },
            71586518: {
                "id": 71586518,
                "name": "Tnuva Almond Drink",
                "source": "Custom",
                "category": 4,
                "owner": user_id,
                "retired": False,
                "meal": False,
                "labelType": "EU",
                "defaultMeasureId": 3001,
                "barcodes": ["7290014760141"],
                "measures": [
                    {
                        "id": 3001,
                        "name": "ml",
                        "type": "Atomic",
                        "amount": 100,
                        "value": 100.0,
                        "hidden": False,
                    },
                ],
                "nutrients": [
                    {"id": 208, "amount": 24.0},
                    {"id": 203, "amount": 0.5},
                    {"id": 204, "amount": 1.1},
                ],
            },
        }
        self.nutrient_defs = [
            {"id": 208, "name": "Energy", "unit": "kcal", "category": "General"},
            {"id": 203, "name": "Protein", "unit": "g", "category": "Protein"},
            {"id": 205, "name": "Carbs", "unit": "g", "category": "Carbohydrates"},
            {"id": -1205, "name": "Net Carbs", "unit": "g", "category": "Carbohydrates"},
            {"id": 291, "name": "Fiber", "unit": "g", "category": "Carbohydrates"},
            {"id": 269, "name": "Sugars", "unit": "g", "category": "Carbohydrates"},
            {"id": 10007, "name": "Sugar Alcohol", "unit": "g", "category": "Carbohydrates"},
            {"id": 204, "name": "Fat", "unit": "g", "category": "Lipids"},
            {"id": 606, "name": "Saturated", "unit": "g", "category": "Lipids"},
            {"id": 605, "name": "Trans-Fats", "unit": "g", "category": "Lipids"},
            {"id": 307, "name": "Sodium", "unit": "mg", "category": "Minerals"},
            {"id": 324, "name": "Vitamin D", "unit": "IU", "category": "Vitamins"},
            {"id": 262, "name": "Caffeine", "unit": "mg", "category": "General"},
        ]

    def _alloc(self) -> int:
        val = self.next_id
        self.next_id += 1
        return val

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content.decode()) if request.content else {}

        if path == "/api/v2/get_profile":
            return httpx.Response(
                200,
                json={
                    "result": "SUCCESS",
                    "id": self.user_id,
                    "timezone": self.timezone,
                    "prefs": [
                        {"DG_ON": "true"},
                        {"time.on": "true"},
                        {"targets.macros.netcarbs": "true"},
                        {"gold": False},
                    ],
                },
            )

        if path == "/api/v2/get_nutrients":
            return httpx.Response(200, json={"result": "SUCCESS", "nutrients": self.nutrient_defs})

        if path == "/api/v2/find_food":
            q = (body.get("query") or "").lower()
            tab = body.get("tab", "ALL")
            matches = []
            for f in self.foods.values():
                if tab == "CUSTOM" and (f.get("owner") != self.user_id or f.get("retired")):
                    continue
                if (
                    not q
                    or q in f["name"].lower()
                    or q in (f.get("barcodes") or [])
                    or (q == "אורז" and "rice" in f["name"].lower())
                ):
                    matches.append(
                        {
                            "id": f["id"],
                            "name": f["name"],
                            "source": f["source"].split(":")[0],
                            "measureId": f["defaultMeasureId"],
                            "translationId": 0,
                            "measureDisplayName": f["measures"][0]["name"]
                            if f["measures"]
                            else "g",
                        }
                    )
            return httpx.Response(200, json={"result": "SUCCESS", "foods": matches})

        if path == "/api/v2/get_food":
            if self.get_food_delay > 0:
                import time

                time.sleep(self.get_food_delay)
            if self.fail_next_get_food:
                self.fail_next_get_food = False
                return httpx.Response(503, json={"result": "FAIL"})
            fid = body["id"]
            if fid not in self.foods:
                return httpx.Response(404, json={"result": "FAIL"})
            return httpx.Response(200, json=self.foods[fid])

        if path == "/api/v2/get_foods":
            if self.fail_get_foods:
                return httpx.Response(503, json={"result": "FAIL"})
            ids = body.get("ids", [])
            return httpx.Response(
                200,
                json={
                    "result": "SUCCESS",
                    "foods": [self.foods[i] for i in ids if i in self.foods],
                },
            )

        if path == "/api/v2/get_recent_foods":
            return httpx.Response(
                200,
                json={
                    "result": "SUCCESS",
                    "servings": [{"food": self.foods[460334], "count": 12}],
                },
            )

        if path == "/api/v2/add_food":
            self.add_food_calls += 1
            data = dict(body["data"])
            fid = data.get("id") or 0
            existing = self.foods.get(fid) if fid else None
            if self.ignore_recipe_updates and existing is not None:
                return httpx.Response(200, json={"result": "SUCCESS", "id": fid})
            if fid == 0:
                fid = self.next_food_id
                self.next_food_id += 1
                data["id"] = fid
            data["owner"] = self.user_id
            data["source"] = data.get("source") or "Custom"
            measures = []
            for m in data.get("measures", []):
                m_copy = dict(m)
                if not m_copy.get("id"):
                    m_copy["id"] = self.next_measure_id
                    self.next_measure_id += 1
                measures.append(m_copy)
            if data.get("meal") and not any(m.get("name") == "full recipe" for m in measures):
                mid = self.next_measure_id
                self.next_measure_id += 1
                measures.insert(
                    0,
                    {
                        "id": mid,
                        "name": "full recipe",
                        "type": "Recipe",
                        "amount": 1.0,
                        "value": 1.0,
                    },
                )
            data["measures"] = measures
            if not data.get("defaultMeasureId") and measures:
                data["defaultMeasureId"] = measures[-1]["id"]
            if self.ignore_nutrient_updates and existing is not None:
                data["nutrients"] = list(existing.get("nutrients", []))
            elif data.get("meal") and data.get("ingredients"):
                # Native recipes store full-batch nutrients computed from ingredients
                totals: dict[int, float] = {}
                for ing in data["ingredients"]:
                    ing_food = self.foods.get(ing["foodId"], {})
                    ing_g = float(ing.get("grams") or 0)
                    for n in ing_food.get("nutrients", []):
                        nid, amt = n.get("id"), n.get("amount")
                        if isinstance(nid, int) and isinstance(amt, (int, float)):
                            totals[nid] = totals.get(nid, 0.0) + float(amt) * ing_g / 100.0
                data["nutrients"] = [
                    {"id": nid, "amount": round(val, 4)} for nid, val in sorted(totals.items())
                ]
            if self.inject_stale_recipe_nutrient is not None and not data.get("meal"):
                data["nutrients"] = [
                    *list(data.get("nutrients", [])),
                    dict(self.inject_stale_recipe_nutrient),
                ]
            self.foods[fid] = data
            return httpx.Response(200, json={"result": "SUCCESS", "id": fid})

        if path == "/api/v2/get_diary":
            if self.diary_read_delay > 0:
                import time

                time.sleep(self.diary_read_delay)
            day_key = body["day"]
            entries = list(self.diary.get(day_key, []))
            consumed_kcal = 0.0
            for e in entries:
                if e.get("type") == "Serving":
                    f = self.foods.get(e["foodId"], {})
                    kcal_100 = next(
                        (n["amount"] for n in f.get("nutrients", []) if n["id"] == 208), 0.0
                    )
                    consumed_kcal += kcal_100 * e["grams"] / 100.0
            return httpx.Response(
                200,
                json={
                    "info": {"day": day_key, "complete": self.day_complete.get(day_key, False)},
                    "diary": entries,
                    "summary": {
                        "macros": {"energy": 2000, "protein": 140, "carbs": 200, "fat": 65},
                        "consumed": {"total": round(consumed_kcal, 2)},
                        "burned": {"total": 2400},
                    },
                },
            )

        if path == "/api/v2/get_nutrition_scores":
            if self.scores_mode == "error_500":
                return httpx.Response(500, json={"result": "FAIL"})
            if self.scores_mode == "error_401":
                return httpx.Response(401, json={"result": "FAIL"})
            if self.scores_mode == "invalid_json":
                return httpx.Response(200, content=b"not-json")
            if self.scores_mode == "missing_all_targets":
                return httpx.Response(
                    200,
                    json={
                        "result": "SUCCESS",
                        "scores": [{"title": "Vitamins", "score": 75.0, "components": []}],
                    },
                )
            sids = set(body.get("servingIds", []))
            totals = {208: 0.0, 203: 0.0, 205: 0.0, 291: 0.0, 204: 0.0}
            for entries in self.diary.values():
                for e in entries:
                    if e.get("servingId") in sids:
                        f = self.foods.get(e["foodId"], {})
                        for n in f.get("nutrients", []):
                            if n["id"] in totals:
                                totals[n["id"]] += n["amount"] * e["grams"] / 100.0
            return httpx.Response(
                200,
                json={
                    "result": "SUCCESS",
                    "scores": [
                        {
                            "title": "All Targets",
                            "score": 82.0,
                            "components": [
                                {"nutrientId": nid, "amount": round(val, 4), "confidence": 1.0}
                                for nid, val in totals.items()
                            ],
                        },
                        {"title": "Vitamins", "score": 75.0, "components": []},
                    ],
                },
            )

        if path == "/api/v2/add_serving":
            row = dict(body["serving"])
            if self.tamper_serving_grams is not None:
                row["grams"] = self.tamper_serving_grams
            sid = self._alloc()
            row["servingId"] = sid
            self.diary.setdefault(row["day"], []).append(row)
            return httpx.Response(200, json={"result": "SUCCESS", "id": sid})

        if path == "/api/v2/edit_serving":
            row = dict(body["serving"])
            if self.tamper_serving_grams is not None:
                row["grams"] = self.tamper_serving_grams
            sid = row["servingId"]
            for day_key, entries in list(self.diary.items()):
                self.diary[day_key] = [e for e in entries if e.get("servingId") != sid]
            self.diary.setdefault(row["day"], []).append(row)
            return httpx.Response(200, json={"result": "SUCCESS"})

        if path == "/api/v2/set_complete":
            self.day_complete[body["day"]] = bool(body["complete"])
            return httpx.Response(200, json={"result": "SUCCESS"})

        if path == "/api/v2/copy":
            src_entries = self.diary.get(body["from"], [])
            for e in src_entries:
                copied = dict(e)
                copied["day"] = body["to"]
                if copied.get("type") == "Serving":
                    copied["servingId"] = self._alloc()
                self.diary.setdefault(body["to"], []).append(copied)
            return httpx.Response(200, json={"result": "SUCCESS"})

        if path == "/api/v2/add_note":
            note = dict(body["note"])
            nid = self._alloc()
            note["noteId"] = nid
            self.diary.setdefault(note["day"], []).append(note)
            return httpx.Response(200, json={"result": "SUCCESS", "id": nid})

        if path == "/api/v2/edit_note":
            note = dict(body["note"])
            nid = note["noteId"]
            for day_key in list(self.diary):
                self.diary[day_key] = [
                    note if e.get("noteId") == nid else e for e in self.diary[day_key]
                ]
            return httpx.Response(200, json={"result": "SUCCESS"})

        if path == "/api/v2/get_targets":
            return httpx.Response(
                200, json={"result": "SUCCESS", "targets": list(self.targets.values())}
            )

        if path == "/api/v2/edit_target":
            tid = body["id"]
            self.targets[tid] = {
                "id": tid,
                "min": body.get("min"),
                "max": body.get("max"),
                "custom": body.get("cus", False),
                "vis": body.get("vis", True),
            }
            return httpx.Response(200, json={"result": "SUCCESS"})

        if path == "/api/v2/get_macro_schedules":
            return httpx.Response(200, json={"result": "SUCCESS", "schedules": []})

        if path == "/api/v2/get_macro_target_templates":
            return httpx.Response(200, json={"result": "SUCCESS", "templates": []})

        if path == "/api/v2/get_streak":
            return httpx.Response(200, json={"result": "SUCCESS", "current": 5, "longest": 14})

        if path == "/api/v2/get_metrics":
            return httpx.Response(
                200,
                json={
                    "result": "SUCCESS",
                    "metrics": [{"id": 1, "name": "Weight", "units": [{"id": 1, "name": "kg"}]}],
                },
            )

        if path == "/api/v2/get_biometrics":
            return httpx.Response(
                200, json={"result": "SUCCESS", "data": [{"day": "2026-11-21", "value": 80.5}]}
            )

        if path == "/api/v2/add_biometric":
            row = dict(body["biometric"])
            bid = self._alloc()
            row["biometricId"] = bid
            self.diary.setdefault(row["day"], []).append(row)
            return httpx.Response(200, json={"result": "SUCCESS", "id": bid})

        if path == "/api/v2/edit_biometric":
            row = dict(body["biometric"])
            bid = row["biometricId"]
            for day_key in list(self.diary):
                self.diary[day_key] = [
                    row if e.get("biometricId") == bid else e for e in self.diary[day_key]
                ]
            return httpx.Response(200, json={"result": "SUCCESS"})

        if path == "/api/v2/add_exercise":
            row = dict(body["exercise"])
            eid = self._alloc()
            row["exerciseId"] = eid
            self.diary.setdefault(row["day"], []).append(row)
            return httpx.Response(200, json={"result": "SUCCESS", "id": eid})

        if path == "/api/v2/edit_exercise":
            row = dict(body["exercise"])
            eid = row["exerciseId"]
            for day_key in list(self.diary):
                self.diary[day_key] = [
                    row if e.get("exerciseId") == eid else e for e in self.diary[day_key]
                ]
            return httpx.Response(200, json={"result": "SUCCESS"})

        if path == "/api/v2/get_fasting_with_date_range":
            return httpx.Response(200, json={"result": "SUCCESS", "fasts": list(self.fasts)})

        if path == "/api/v2/get_fasting_stats":
            return httpx.Response(
                200, json={"result": "SUCCESS", "completedCount": len(self.fasts)}
            )

        if path == "/api/v2/add_fast":
            fast = dict(body["fast"])
            fid = self._alloc()
            fast["id"] = fid
            self.fasts.append(fast)
            return httpx.Response(200, json={"result": "SUCCESS", "id": fid})

        if path == "/api/v2/edit_fast":
            fast = dict(body["fast"])
            self.fasts = [fast if f["id"] == fast["id"] else f for f in self.fasts]
            return httpx.Response(200, json={"result": "SUCCESS"})

        if path == "/api/v2/delete_fast":
            self.fasts = [f for f in self.fasts if f["id"] != body["fastId"]]
            return httpx.Response(200, json={"result": "SUCCESS"})

        if request.method == "DELETE" and path == f"/api/v3/user/{self.user_id}/diary-entries":
            ids_to_del = {e.get("id") for e in body.get("diaryEntries", [])}
            for day_key in list(self.diary):
                self.diary[day_key] = [
                    e
                    for e in self.diary[day_key]
                    if (e.get("servingId") or e.get("biometricId") or e.get("exerciseId"))
                    not in ids_to_del
                ]
            return httpx.Response(204)

        return httpx.Response(404, json={"result": "FAIL", "error": f"Unhandled {path}"})


def _has_string_int_union(schema: Any) -> bool:
    """Detect `str | int` anyOf unions that break strict schema parsers such as Gemini."""
    if isinstance(schema, dict):
        any_of = schema.get("anyOf")
        if isinstance(any_of, list):
            types = {entry.get("type") for entry in any_of if isinstance(entry, dict)}
            if {"string", "integer"}.issubset(types):
                return True
        return any(_has_string_int_union(v) for v in schema.values())
    if isinstance(schema, list):
        return any(_has_string_int_union(item) for item in schema)
    return False


class ServiceAndMCPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        sdir = root / "session"
        sdir.mkdir(mode=0o700)
        self.session_path = sdir / "session.json"
        self.session_path.write_text(
            json.dumps({"version": 1, "user_id": 1001, "token": "fake-tok", "timezone": "UTC"})
        )
        self.session_path.chmod(0o600)

        self.backend = FakeCronometerBackend(user_id=1001, timezone="Asia/Jerusalem")
        self.client = CronometerSessionClient(
            self.session_path, interactive=False, transport=self.backend.transport()
        )
        self.state = AssistantState(root / "assistant.sqlite", user_id=1001)
        self.assistant = CronometerAssistant(self.client, state=self.state)

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def test_connection_syncs_drifted_timezone_automatically(self):
        info = self.assistant.foods.check_connection()
        self.assertTrue(info["connected"])
        self.assertEqual(info["account_timezone"], "Asia/Jerusalem")
        persisted = json.loads(self.session_path.read_text())
        self.assertEqual(persisted["timezone"], "Asia/Jerusalem")

    def test_search_preserves_hebrew_to_english_and_barcode_results(self):
        res = self.assistant.foods.search_foods("אורז")
        self.assertEqual(res["count"], 1)
        self.assertEqual(res["foods"][0]["food_id"], 460334)
        self.assertEqual(res["foods"][0]["source"], "NCCDB")

        bc = self.assistant.foods.search_foods("7290014760141")
        self.assertEqual(bc["count"], 1)
        self.assertEqual(bc["foods"][0]["food_id"], 71586518)

    def test_batch_log_quantity_atomic_edit_move_and_delete(self):
        logged = self.assistant.diary.log_food_entries(
            [
                {"food_id": 460334, "grams": 150},
                {"food_id": 72014363, "quantity": 11, "measure_name": "pieces"},
                {"food_id": 71586518, "quantity": 200, "measure_name": "ml"},
            ],
            day="2026-11-21",
            meal_group="lunch",
            time="13:15",
            request_id="meal-abc-1",
        )
        self.assertEqual(logged["logged_count"], 3)
        self.assertAlmostEqual(logged["logged"][1]["grams"], 15.0, places=3)
        self.assertAlmostEqual(logged["logged"][2]["grams"], 200.0, places=3)

        # Retry with same request_id and same payload deduplicates without double-writing
        dup = self.assistant.diary.log_food_entries(
            [
                {"food_id": 460334, "grams": 150},
                {"food_id": 72014363, "quantity": 11, "measure_name": "pieces"},
                {"food_id": 71586518, "quantity": 200, "measure_name": "ml"},
            ],
            day="2026-11-21",
            meal_group="lunch",
            time="13:15",
            request_id="meal-abc-1",
        )
        self.assertTrue(dup["already_logged"])

        # Reusing same request_id for a different meal must be rejected
        with self.assertRaises(ValidationError):
            self.assistant.diary.log_food_entries(
                [{"food_id": 460334, "grams": 150}],
                day="2026-11-21",
                request_id="meal-abc-1",
            )

        log = self.assistant.diary.get_food_log("2026-11-21", include_untracked=True)
        self.assertEqual(log["serving_count"], 3)
        pretzel_row = next(s for s in log["servings"] if s["food_id"] == 72014363)
        self.assertAlmostEqual(pretzel_row["servings"], 11.0, places=2)
        untracked_caffeine = next((n for n in log["nutrients"] if n["id"] == 262), None)
        self.assertIsNotNone(untracked_caffeine)
        self.assertFalse(untracked_caffeine["tracked"])
        self.assertAlmostEqual(untracked_caffeine["amount"], 0.75, places=3)

        sid = logged["logged"][0]["entry_id"]
        edited = self.assistant.diary.edit_food_entry(
            sid,
            day="2026-11-21",
            grams=200,
            time="19:30",
            meal_group="dinner",
            new_day="2026-11-20",
        )
        self.assertEqual(edited["grams"], 200.0)
        self.assertEqual(edited["meal_group"], "dinner")
        self.assertEqual(edited["date"], "2026-11-20")

        self.assertEqual(
            self.assistant.diary.remove_food_entries([sid], day="2026-11-20")["count"], 1
        )
        self.assertEqual(
            self.assistant.diary.remove_food_entries(
                [logged["logged"][1]["entry_id"], logged["logged"][2]["entry_id"]],
                day="2026-11-21",
            )["count"],
            2,
        )
        self.assertEqual(self.assistant.diary.get_food_log("2026-11-21")["serving_count"], 0)

    def test_custom_food_create_update_retire_and_recipe(self):
        created = self.assistant.custom.create_custom_food(
            "ProFlex Bar Chocolate",
            {
                "אנרגיה": {"amount": 210, "unit": "kcal"},
                "חלבון": {"amount": 20, "unit": "g"},
                "פחמימות": {"amount": 15, "unit": "g"},
                "סיבים תזונתיים": {"amount": 5, "unit": "g"},
                "שומן רווי": {"amount": 3, "unit": "g"},
                "מלח": {"amount": 0.5, "unit": "g"},
            },
            basis_amount=60,
            basis_unit="g",
            carbohydrate_basis="excludes_fiber",
            serving_name="bar",
            serving_grams=60,
            category_id=5,
            barcode="7290014760141",
            request_id="cf-1",
        )
        self.assertTrue(created["verified"] and created["barcode_linked"])
        fid = created["food_id"]

        updated = self.assistant.custom.update_custom_food(
            fid,
            nutrients={"protein": 22},
            basis_amount=60,
            measures=[{"name": "half bar", "grams": 30}],
        )
        self.assertTrue(updated["verified"])
        self.assertTrue(any(m["name"] == "half bar" for m in updated["measures"]))

        retired = self.assistant.custom.retire_custom_food(fid, retired=True)
        self.assertTrue(retired["retired"])
        with self.assertRaises(ValidationError):
            self.assistant.diary.log_food_entries([{"food_id": fid, "grams": 60}], day="2026-11-21")

        recipe = self.assistant.recipes.save_recipe(
            "Cooked Rice & Pretzel Mix",
            [
                {"food_id": 460334, "grams": 300},
                {"food_id": 72014363, "quantity": 22, "measure_name": "pieces"},
            ],
            servings=2,
            cooked_grams=280,
        )
        self.assertTrue(recipe["verified"])
        self.assertEqual(recipe["final_grams"], 280.0)
        self.assertAlmostEqual(recipe["servings"], 2.0)

        # Updating recipe servings from 2 -> 4 actually updates serving measure (140g -> 70g)
        edited_recipe = self.assistant.recipes.save_recipe(
            "Cooked Rice & Pretzel Mix",
            [
                {"food_id": 460334, "grams": 300},
                {"food_id": 72014363, "quantity": 22, "measure_name": "pieces"},
            ],
            servings=4,
            cooked_grams=280,
            recipe_id=recipe["food_id"],
        )
        self.assertTrue(edited_recipe["verified"])
        self.assertAlmostEqual(edited_recipe["servings"], 4.0)
        srv_m = next(m for m in edited_recipe["measures"] if m["name"] == "serving")
        self.assertAlmostEqual(srv_m["grams_per_unit"], 70.0, places=2)

        # Omitting servings on update preserves existing serving count (4)
        kept_recipe = self.assistant.recipes.save_recipe(
            "Cooked Rice & Pretzel Mix v2",
            [
                {"food_id": 460334, "grams": 300},
                {"food_id": 72014363, "quantity": 22, "measure_name": "pieces"},
            ],
            cooked_grams=280,
            recipe_id=recipe["food_id"],
        )
        self.assertTrue(kept_recipe["verified"])
        self.assertAlmostEqual(kept_recipe["servings"], 4.0)

    def test_partial_batch_retry_reconciles_without_duplicating(self):
        from cronometer_mcp.client import CronometerError

        orig_add = self.assistant.diary._add_serving_row
        calls = 0

        def fail_second_after_write(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                # Simulate response lost AFTER Cronometer actually wrote the second item
                orig_add(**kwargs)
                raise httpx.ReadTimeout("simulated timeout after write")
            return orig_add(**kwargs)

        items = [
            {"food_id": 460334, "grams": 150},
            {"food_id": 72014363, "grams": 30},
        ]
        self.assistant.diary._add_serving_row = fail_second_after_write  # type: ignore[method-assign]
        with self.assertRaises(httpx.ReadTimeout):
            self.assistant.diary.log_food_entries(
                items, day="2026-11-21", meal_group="lunch", time="13:00", request_id="partial-1"
            )
        # Restore normal writes and retry same request_id -> reconciles item 2, zero duplicates!
        self.assistant.diary._add_serving_row = orig_add  # type: ignore[method-assign]
        resumed = self.assistant.diary.log_food_entries(
            items, day="2026-11-21", meal_group="lunch", time="13:00", request_id="partial-1"
        )
        self.assertEqual(resumed["logged_count"], 2)
        self.assertTrue(resumed["verified"])
        self.assertEqual(self.assistant.diary.get_food_log("2026-11-21")["serving_count"], 2)

        # If a sending step never appeared in the diary, retry refuses to blindly resend
        def fail_before_write(**kwargs):
            raise httpx.ReadTimeout("uncertain timeout")

        self.assistant.diary._add_serving_row = fail_before_write  # type: ignore[method-assign]
        with self.assertRaises(httpx.ReadTimeout):
            self.assistant.diary.log_food_entries(
                [{"food_id": 460334, "grams": 100}],
                day="2026-11-21",
                meal_group="dinner",
                time="19:00",
                request_id="uncertain-1",
            )
        self.assistant.diary._add_serving_row = orig_add  # type: ignore[method-assign]
        with self.assertRaises(CronometerError):
            self.assistant.diary.log_food_entries(
                [{"food_id": 460334, "grams": 100}],
                day="2026-11-21",
                meal_group="dinner",
                time="19:00",
                request_id="uncertain-1",
            )

    def test_readback_detects_mismatched_grams_on_log_and_edit(self):
        self.backend.tamper_serving_grams = 1.0
        res = self.assistant.diary.log_food_entries(
            [{"food_id": 460334, "grams": 150}],
            day="2026-11-21",
            meal_group="lunch",
            time="13:00",
        )
        self.assertFalse(res["verified"])
        self.assertEqual(res["logged"][0]["grams"], 1.0)
        self.assertIn("verification_mismatches", res)

        edited = self.assistant.diary.edit_food_entry(
            res["logged"][0]["entry_id"], day="2026-11-21", grams=200
        )
        self.assertFalse(edited["verified"])
        self.assertEqual(edited["grams"], 1.0)
        self.assertIn("verification_mismatches", edited)

    def test_mcp_rename_measure_preserves_multi_unit_amount(self):
        srv = create_server(self.assistant)

        async def run():
            res = await srv.call_tool(
                "update_custom_food",
                {
                    "food_id": 72014363,
                    "measures": [{"measure_id": 2001, "name": "pretzel rings"}],
                },
            )
            text = res[0][0].text if isinstance(res, tuple) else res[0].text
            data = json.loads(text)
            self.assertTrue(data["ok"], data)
            renamed = next(m for m in data["measures"] if m["measure_id"] == 2001)
            self.assertEqual(renamed["name"], "pretzel rings")
            self.assertEqual(renamed["amount"], 22)
            self.assertAlmostEqual(renamed["grams_per_unit"], 30.0 / 22.0, places=4)

        asyncio.run(run())

    def test_always_on_server_refreshes_timezone_for_today_operations(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        self.backend.timezone = "Pacific/Niue"  # UTC-11
        log1 = self.assistant.diary.get_food_log()
        expected1 = datetime.now(ZoneInfo("Pacific/Niue")).date().isoformat()
        self.assertEqual(log1["date"], expected1)

        # Profile timezone changes while server stays running
        self.backend.timezone = "Pacific/Kiritimati"  # UTC+14 (always next day vs UTC-11!)
        log2 = self.assistant.diary.get_food_log()
        expected2 = datetime.now(ZoneInfo("Pacific/Kiritimati")).date().isoformat()
        self.assertEqual(log2["date"], expected2)
        self.assertNotEqual(log1["date"], log2["date"])

        # Explicit historical date remains untouched
        hist = self.assistant.diary.get_food_log("2026-11-15")
        self.assertEqual(hist["date"], "2026-11-15")

    def test_concurrent_identical_meal_retries_do_not_duplicate(self):
        from concurrent.futures import ThreadPoolExecutor

        self.backend.diary_read_delay = 0.04
        items = [
            {"food_id": 460334, "grams": 150},
            {"food_id": 72014363, "grams": 30},
        ]

        def submit():
            return self.assistant.diary.log_food_entries(
                items,
                day="2026-11-21",
                meal_group="lunch",
                time="13:00",
                request_id="race-meal-1",
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(submit)
            f2 = pool.submit(submit)
            r1, r2 = f1.result(), f2.result()

        self.assertEqual({r1["already_logged"], r2["already_logged"]}, {False, True})
        self.assertEqual(r1["logged_count"], 2)
        self.assertEqual(r2["logged_count"], 2)
        log = self.assistant.diary.get_food_log("2026-11-21")
        self.assertEqual(log["serving_count"], 2)

    def test_recovery_does_not_adopt_unrelated_meal_at_different_time_or_group(self):
        from cronometer_mcp.client import CronometerError

        orig_add = self.assistant.diary._add_serving_row

        def fail_before_write(**kwargs):
            raise httpx.ReadTimeout("timeout before server wrote")

        # Simulate a timeout BEFORE rice was written with time/group omitted (defaults to now/auto)
        self.assistant.diary._add_serving_row = fail_before_write  # type: ignore[method-assign]
        with self.assertRaises(httpx.ReadTimeout):
            self.assistant.diary.log_food_entries(
                [{"food_id": 460334, "grams": 150}],
                day="2026-11-21",
                request_id="orphan-rice-1",
            )
        self.assistant.diary._add_serving_row = orig_add  # type: ignore[method-assign]

        # Separately log the exact same food and weight at 23:59 for dinner
        dinner = self.assistant.diary.log_food_entries(
            [{"food_id": 460334, "grams": 150}],
            day="2026-11-21",
            meal_group="dinner",
            time="23:59",
            request_id="separate-dinner-1",
        )
        self.assertTrue(dinner["verified"])

        # Retrying the original request must NOT claim the 23:59 dinner entry
        with self.assertRaises(CronometerError) as ctx:
            self.assistant.diary.log_food_entries(
                [{"food_id": 460334, "grams": 150}],
                day="2026-11-21",
                request_id="orphan-rice-1",
            )
        self.assertIn("not adopted", str(ctx.exception))

    def test_retry_after_midnight_recovers_original_day_without_rejecting_key(self):
        from datetime import date, datetime
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("Asia/Jerusalem")
        current_now = [datetime(2026, 11, 21, 23, 59, 0, tzinfo=tz)]
        self.client.now = lambda: current_now[0]  # type: ignore[method-assign]
        self.client.today = lambda: current_now[0].date()  # type: ignore[method-assign]

        orig_add = self.assistant.diary._add_serving_row
        calls = 0

        def fail_second_after_write(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                orig_add(**kwargs)
                raise httpx.ReadTimeout("timeout after item 2 wrote")
            return orig_add(**kwargs)

        items = [
            {"food_id": 460334, "grams": 150},
            {"food_id": 72014363, "grams": 30},
        ]
        self.assistant.diary._add_serving_row = fail_second_after_write  # type: ignore[method-assign]
        with self.assertRaises(httpx.ReadTimeout):
            self.assistant.diary.log_food_entries(items, request_id="midnight-meal-1")
        self.assistant.diary._add_serving_row = orig_add  # type: ignore[method-assign]

        # Clock crosses midnight to 2026-11-22; retrying with day omitted recovers 2026-11-21!
        current_now[0] = datetime(2026, 11, 22, 0, 2, 0, tzinfo=tz)
        resumed = self.assistant.diary.log_food_entries(items, request_id="midnight-meal-1")
        self.assertTrue(resumed["verified"])
        self.assertEqual(resumed["date"], date(2026, 11, 21).isoformat())
        self.assertEqual(self.assistant.diary.get_food_log("2026-11-21")["serving_count"], 2)
        self.assertEqual(self.assistant.diary.get_food_log("2026-11-22")["serving_count"], 0)

    def test_concurrent_edits_to_same_entry_do_not_clobber_each_other(self):
        from concurrent.futures import ThreadPoolExecutor

        logged = self.assistant.diary.log_food_entries(
            [{"food_id": 460334, "grams": 100}],
            day="2026-11-21",
            meal_group="lunch",
            time="12:00",
        )
        sid = logged["logged"][0]["entry_id"]
        self.backend.diary_read_delay = 0.04

        with ThreadPoolExecutor(max_workers=2) as pool:
            f_grams = pool.submit(
                self.assistant.diary.edit_food_entry, sid, day="2026-11-21", grams=200
            )
            f_time = pool.submit(
                self.assistant.diary.edit_food_entry, sid, day="2026-11-21", time="19:30"
            )
            r_g, r_t = f_grams.result(), f_time.result()

        self.assertTrue(r_g["verified"] and r_t["verified"])
        log = self.assistant.diary.get_food_log("2026-11-21")
        self.assertEqual(log["serving_count"], 1)
        row = log["servings"][0]
        self.assertAlmostEqual(row["grams"], 200.0, places=2)
        self.assertEqual(row["time"], "19:30:0")

    def test_custom_food_default_portion_reserved_g_and_update_verification(self):
        # Real Cronometer assigns defaultMeasureId to the 1 g `g` measure on Weight custom foods.
        # Creating a 42 g cereal must still log 42 g for quantity=1 and 50 g for grams=50!
        created = self.assistant.custom.create_custom_food(
            "ProFlex Cereal 42g",
            {"energy": 160, "protein": 12, "carbs": 20, "fat": 3},
            basis_amount=42,
            carbohydrate_basis="includes_fiber",
            serving_name="serving",
            serving_grams=42,
            request_id="cereal-42g-1",
        )
        self.assertTrue(created["verified"], created)
        fid = created["food_id"]
        # Confirm backend set defaultMeasureId to `g` (just like real Cronometer)
        def_m = next(m for m in created["measures"] if m["is_default"])
        self.assertEqual(def_m["name"], "g")
        srv_m = created["primary_measure"]
        self.assertEqual(srv_m["name"], "serving")
        self.assertAlmostEqual(srv_m["grams_per_unit"], 42.0, places=3)

        # Release gate: quantity=1 logs 42 g (NOT 1 g!), grams=50 logs 50 g, quantity=50 g logs 50 g
        logged = self.assistant.diary.log_food_entries(
            [
                {"food_id": fid, "quantity": 1},
                {"food_id": fid, "grams": 50},
                {"food_id": fid, "quantity": 50, "measure_name": "g"},
            ],
            day="2026-11-21",
            meal_group="breakfast",
            time="08:00",
        )
        self.assertTrue(logged["verified"])
        self.assertAlmostEqual(logged["logged"][0]["grams"], 42.0, places=2)
        self.assertAlmostEqual(logged["logged"][1]["grams"], 50.0, places=2)
        self.assertAlmostEqual(logged["logged"][2]["grams"], 50.0, places=2)

        # Naming a non-1g serving "g" must be rejected on both create and update
        with self.assertRaises(ValidationError):
            self.assistant.custom.create_custom_food(
                "Bad Cereal",
                {"energy": 160, "protein": 12},
                basis_amount=42,
                serving_name="g",
                serving_grams=42,
            )
        with self.assertRaises(ValidationError):
            self.assistant.custom.update_custom_food(
                fid, measures=[{"measure_id": srv_m["measure_id"], "name": "g"}]
            )

        # If backend silently drops a protein correction on update, verified must be False
        self.backend.ignore_nutrient_updates = True
        bad_upd = self.assistant.custom.update_custom_food(
            fid, nutrients={"protein": 18}, basis_amount=42
        )
        self.assertFalse(bad_upd["verified"])
        self.assertIn("verification_mismatches", bad_upd)

    def test_native_recipe_per_100g_consistent_across_details_search_nested_and_history(self):
        # 400 g native (meal=True) rice recipe carrying 520 kcal total batch -> 130 kcal / 100 g
        self.backend.foods[9801] = {
            "id": 9801,
            "name": "Native Batch Rice",
            "source": "Custom",
            "category": 12,
            "owner": 1001,
            "retired": False,
            "meal": True,
            "labelType": "AMERICAN_2016",
            "defaultMeasureId": 98011,
            "barcodes": [],
            "measures": [
                {"id": 98010, "name": "full recipe", "type": "Recipe", "amount": 1.0, "value": 1.0},
                {"id": 98011, "name": "serving", "type": "Recipe", "amount": 1.0, "value": 2.0},
                {"id": 98012, "name": "g", "type": "Recipe", "amount": 1.0, "value": 400.0},
            ],
            "ingredients": [
                {"foodId": 460334, "measureId": 1048861, "grams": 400.0, "amount": 1.0}
            ],
            "nutrients": [
                {"id": 208, "amount": 520.0},
                {"id": 203, "amount": 10.8},
                {"id": 205, "amount": 112.0},
                {"id": 204, "amount": 1.2},
            ],
        }

        # 1) Details reports 130 kcal/100g (not 520)
        details = self.assistant.foods.get_food_details(9801)
        self.assertAlmostEqual(details["macros_per_100g"]["energy_kcal"], 130.0, places=2)
        energy_row = next(n for n in details["nutrients_per_100g"] if n["id"] == 208)
        self.assertAlmostEqual(energy_row["amount"], 130.0, places=2)

        # 2) Search reports 130 kcal/100g (not 520)
        search = self.assistant.foods.search_foods("Native Batch Rice")
        self.assertEqual(search["count"], 1)
        self.assertAlmostEqual(
            search["foods"][0]["macros_per_100g"]["energy_kcal"], 130.0, places=2
        )

        # 3) Nesting 100 g of this native recipe in a weight recipe contributes 130 kcal (not 520)
        nested = self.assistant.recipes.save_recipe(
            "Rice Bowl Wrapper",
            [{"food_id": 9801, "grams": 100}],
            servings=1,
        )
        self.assertTrue(nested["verified"])
        self.assertAlmostEqual(nested["macros_per_100g"]["energy_kcal"], 130.0, places=2)

        # 4) Logging 200 g and querying food history reports 200.0 g (not 0.5 batch fraction)
        self.assistant.diary.log_food_entries(
            [{"food_id": 9801, "grams": 200}], day="2026-11-21", meal_group="lunch", time="13:00"
        )
        hist = self.assistant.diary_extras.find_entries_by_food(
            9801, start_day="2026-11-21", end_day="2026-11-21"
        )
        self.assertAlmostEqual(hist["entries"][0]["grams"], 200.0, places=2)

    def test_recipe_edit_preserves_cooked_weight_and_servings_recipe_measure_ids(self):
        # Release gate A: 400 g raw / 250 g cooked weight recipe, 2 servings (125 g each)
        w_recipe = self.assistant.recipes.save_recipe(
            "Cooked Rice Pot",
            [{"food_id": 460334, "grams": 400}],
            servings=2,
            cooked_grams=250,
            notes="Lightly salted",
        )
        self.assertTrue(w_recipe["verified"])
        self.assertEqual(w_recipe["final_grams"], 250.0)
        w_orig_mids = {m["name"]: m["measure_id"] for m in w_recipe["measures"]}

        # Log 1 portion (125 g)
        w_log = self.assistant.diary.log_food_entries(
            [{"food_id": w_recipe["food_id"], "quantity": 1, "measure_name": "serving"}],
            day="2026-11-20",
            meal_group="dinner",
            time="19:00",
        )
        self.assertAlmostEqual(w_log["logged"][0]["grams"], 125.0, places=2)

        # Change servings from 2 -> 4 while OMITTING cooked_grams and notes
        w_edited = self.assistant.recipes.save_recipe(
            "Cooked Rice Pot",
            [{"food_id": 460334, "grams": 400}],
            servings=4,
            recipe_id=w_recipe["food_id"],
        )
        self.assertTrue(w_edited["verified"], w_edited)
        self.assertEqual(w_edited["final_grams"], 250.0)
        self.assertAlmostEqual(w_edited["servings"], 4.0, places=2)
        srv_m = next(m for m in w_edited["measures"] if m["name"] == "serving")
        self.assertAlmostEqual(srv_m["grams_per_unit"], 62.5, places=2)
        # Measure IDs and user notes preserved; historical diary entry still reads cleanly
        self.assertEqual({m["name"]: m["measure_id"] for m in w_edited["measures"]}, w_orig_mids)
        self.assertIn("Lightly salted", self.backend.foods[w_recipe["food_id"]]["comments"])
        hist_w = self.assistant.diary.get_food_log("2026-11-20")
        self.assertAlmostEqual(hist_w["servings"][0]["grams"], 125.0, places=2)
        self.assertIsNotNone(hist_w["servings"][0]["measure"])

        # Release gate B: servings-based (meal=True) recipe edit preserves measure IDs
        s_recipe = self.assistant.recipes.save_recipe(
            "Native Rice Servings",
            [{"food_id": 460334, "grams": 400}],
            servings=2,
            serving_type="servings",
        )
        self.assertTrue(s_recipe["verified"])
        self.assertAlmostEqual(s_recipe["macros_per_100g"]["energy_kcal"], 130.0, places=2)
        s_orig_mids = {m["name"]: m["measure_id"] for m in s_recipe["measures"]}

        s_log = self.assistant.diary.log_food_entries(
            [{"food_id": s_recipe["food_id"], "quantity": 1, "measure_name": "serving"}],
            day="2026-11-19",
            meal_group="lunch",
            time="13:00",
        )
        self.assertAlmostEqual(s_log["logged"][0]["grams"], 200.0, places=2)

        s_edited = self.assistant.recipes.save_recipe(
            "Native Rice Servings",
            [{"food_id": 460334, "grams": 400}],
            servings=4,
            recipe_id=s_recipe["food_id"],
        )
        self.assertTrue(s_edited["verified"], s_edited)
        self.assertAlmostEqual(s_edited["servings"], 4.0, places=2)
        self.assertEqual({m["name"]: m["measure_id"] for m in s_edited["measures"]}, s_orig_mids)
        hist_s = self.assistant.diary.get_food_log("2026-11-19")
        self.assertEqual(hist_s["serving_count"], 1)
        self.assertIsNotNone(hist_s["servings"][0]["measure"])
        self.assertIn(hist_s["servings"][0]["measure"]["measure_id"], s_orig_mids.values())

    def test_app_created_recipes_report_is_recipe_and_resolved_ingredients(self):
        # Real Cronometer app weight recipe: meal=False, advancedServingSize="false", cooked_weight
        self.backend.foods[71766482] = {
            "id": 71766482,
            "name": "FODMAP Paprica Chicken",
            "source": "Custom",
            "category": 15,
            "owner": 1001,
            "retired": False,
            "meal": False,
            "labelType": "AMERICAN_2016",
            "defaultMeasureId": 254047223,
            "barcodes": [],
            "properties": {"advancedServingSize": "false", "cooked_weight": "450.0"},
            "measures": [
                {
                    "id": 254047222,
                    "name": "full recipe",
                    "type": "Weight",
                    "amount": 1,
                    "value": 450,
                },
                {"id": 254047224, "name": "Serving", "type": "Weight", "amount": 1, "value": 120},
                {"id": 254047223, "name": "g", "type": "Weight", "amount": 1, "value": 1},
            ],
            "ingredients": [
                {"foodId": 460334, "measureId": 1048861, "grams": 600.0},
                {"foodId": 72014363, "measureId": 2002, "grams": 30.0},
            ],
            "nutrients": [
                {"id": 208, "amount": 252.38},
                {"id": 203, "amount": 29.5},
            ],
        }
        # Real Cronometer app servings recipe: meal=False, Recipe-typed measures, batch nutrients!
        self.backend.foods[72144024] = {
            "id": 72144024,
            "name": "Simple Rice",
            "source": "Custom",
            "category": 12,
            "owner": 1001,
            "retired": False,
            "meal": False,
            "labelType": "AMERICAN_2016",
            "defaultMeasureId": 721440242,
            "barcodes": [],
            "properties": {"advancedServingSize": "false"},
            "measures": [
                {"id": 721440241, "name": "g", "type": "Recipe", "amount": 1, "value": 400.0},
                {"id": 721440242, "name": "Serving", "type": "Recipe", "amount": 1, "value": 2},
                {"id": 721440243, "name": "full recipe", "type": "Recipe", "amount": 1, "value": 1},
            ],
            "ingredients": [{"foodId": 460334, "measureId": 1048861, "grams": 400.0}],
            "nutrients": [
                {"id": 208, "amount": 520.0},
                {"id": 203, "amount": 10.8},
            ],
        }

        # get_my_foods classifies BOTH app recipe shapes accurately
        mine = self.assistant.foods.get_my_foods(include_recent=False, include_aliases=False)
        by_id = {f["food_id"]: f for f in mine["custom_foods"]}
        self.assertTrue(by_id[71766482]["is_recipe"])
        self.assertEqual(by_id[71766482]["serving_type"], "weight")
        self.assertEqual(by_id[71766482]["ingredient_count"], 2)
        self.assertTrue(by_id[72144024]["is_recipe"])
        self.assertEqual(by_id[72144024]["serving_type"], "servings")

        # get_food_details returns resolved ingredients + cooked_weight for weight app recipe
        chick = self.assistant.foods.get_food_details(71766482)
        self.assertTrue(chick["is_recipe"])
        self.assertEqual(chick["serving_type"], "weight")
        self.assertAlmostEqual(chick["cooked_weight_grams"], 450.0)
        self.assertAlmostEqual(chick["raw_ingredients_grams"], 630.0)
        self.assertEqual(len(chick["ingredients"]), 2)
        self.assertEqual(
            chick["ingredients"][0]["food_name"], "White Rice, Cooked in Unsalted Water"
        )
        self.assertAlmostEqual(chick["macros_per_100g"]["energy_kcal"], 252.38, places=2)

        # get_food_details returns resolved ingredients + per-100g (not batch!) macros for servings app recipe
        rice = self.assistant.foods.get_food_details(72144024)
        self.assertTrue(rice["is_recipe"])
        self.assertEqual(rice["serving_type"], "servings")
        self.assertAlmostEqual(rice["macros_per_100g"]["energy_kcal"], 130.0, places=2)
        self.assertEqual(rice["ingredients"][0]["food_id"], 460334)

        # Editing app-created servings recipe (72144024, capital-'S' "Serving" = 2)
        # preserves servings=2 when omitted and verifies cleanly when updated to 4
        rice_keep = self.assistant.recipes.save_recipe(
            "Simple Rice Updated",
            [{"food_id": 460334, "grams": 1500}],
            recipe_id=72144024,
        )
        self.assertTrue(rice_keep["verified"])
        self.assertAlmostEqual(rice_keep["servings"], 2.0, places=2)

        rice_four = self.assistant.recipes.save_recipe(
            "Simple Rice 4 Servings",
            [{"food_id": 460334, "grams": 1500}],
            servings=4,
            recipe_id=72144024,
            request_id="app-rice-4srv",
        )
        self.assertTrue(rice_four["verified"])
        self.assertAlmostEqual(rice_four["servings"], 4.0, places=2)

        # Editing app-created weight recipe (71766482, capital-'S' "Serving" + cooked_weight=450)
        # updates cooked_weight to 500 AND keeps 500 on a subsequent edit omitting cooked_grams
        chick_500 = self.assistant.recipes.save_recipe(
            "FODMAP Paprica Chicken Updated",
            [{"food_id": 460334, "grams": 600}, {"food_id": 72014363, "grams": 30}],
            servings=4,
            cooked_grams=500,
            recipe_id=71766482,
            request_id="app-chick-500",
        )
        self.assertTrue(chick_500["verified"])
        self.assertAlmostEqual(chick_500["servings"], 4.0, places=2)
        self.assertAlmostEqual(
            self.assistant.foods.get_food_details(71766482)["cooked_weight_grams"], 500.0
        )

        chick_next = self.assistant.recipes.save_recipe(
            "FODMAP Paprica Chicken Updated",
            [{"food_id": 460334, "grams": 600}, {"food_id": 72014363, "grams": 30}],
            recipe_id=71766482,
        )
        self.assertTrue(chick_next["verified"])
        self.assertAlmostEqual(chick_next["final_grams"], 500.0, places=2)
        self.assertAlmostEqual(chick_next["servings"], 4.0, places=2)

        # Weight recipe created via save_recipe (ingredients in comments) also resolves ingredients
        saved = self.assistant.recipes.save_recipe(
            "Saved Weight Mix",
            [{"food_id": 460334, "grams": 200}, {"food_id": 72014363, "grams": 30}],
            servings=2,
        )
        saved_details = self.assistant.foods.get_food_details(saved["food_id"])
        self.assertTrue(saved_details["is_recipe"])
        self.assertEqual(len(saved_details["ingredients"]), 2)
        self.assertEqual(
            saved_details["ingredients"][0]["food_name"], "White Rice, Cooked in Unsalted Water"
        )

    def test_mcp_tool_tiers_and_gemini_claude_schema_compatibility(self):
        core_only = create_server(
            self.assistant, include_extras=False, include_tracking=False, port=8765
        )
        default_srv = create_server(self.assistant)
        full_srv = create_server(self.assistant, include_extras=True, include_tracking=True)

        self.assertEqual(SESSION_IDLE_TIMEOUT, 24 * 3600)
        self.assertEqual(core_only.settings.host, "127.0.0.1")
        self.assertEqual(core_only.settings.port, 8765)

        async def run():
            core_tools = await core_only.list_tools()
            default_tools = await default_srv.list_tools()
            full_tools = await full_srv.list_tools()

            self.assertEqual(len(core_tools), 19)
            self.assertEqual(len(default_tools), 29)
            self.assertEqual(len(full_tools), 42)

            # Verify ZERO str|int anyOf unions across all 42 tools (Gemini/Claude compatibility)
            for tool in full_tools:
                self.assertFalse(
                    _has_string_int_union(tool.inputSchema),
                    f"Tool {tool.name} has a string|integer union in schema: {tool.inputSchema}",
                )

            # save_recipe and save_meal_draft must NOT claim idempotentHint=True
            by_name = {t.name: t for t in core_tools}
            self.assertFalse(by_name["save_recipe"].annotations.idempotentHint)
            self.assertFalse(by_name["save_meal_draft"].annotations.idempotentHint)
            self.assertFalse(by_name["log_food_entries"].annotations.idempotentHint)
            self.assertFalse(by_name["create_custom_food"].annotations.idempotentHint)
            self.assertTrue(by_name["edit_food_entry"].annotations.idempotentHint)

            # Round-trip a core tool and an extra tool
            res = await default_srv.call_tool("search_foods", {"query": "אורז"})
            text = res[0][0].text if isinstance(res, tuple) else res[0].text
            data = json.loads(text)
            self.assertTrue(data["ok"] and data["foods"][0]["food_id"] == 460334)

            streak_res = await default_srv.call_tool("get_streak", {})
            st_text = streak_res[0][0].text if isinstance(streak_res, tuple) else streak_res[0].text
            st_data = json.loads(st_text)
            self.assertTrue(st_data["ok"] and st_data["streak"]["current"] == 5)

        asyncio.run(run())

    def test_save_recipe_request_id_and_draft_stable_id_prevent_duplicates(self):
        # Transient read-back failure right after recipe creation must resume by saved food_id
        self.backend.fail_next_get_food = True
        with self.assertRaises(httpx.HTTPStatusError):
            self.assistant.recipes.save_recipe(
                "Retry Safe Stew",
                [{"food_id": 460334, "grams": 200}],
                servings=2,
                request_id="recipe-req-1",
            )
        self.assertEqual(self.backend.add_food_calls, 1)

        # Retry with same request_id finishes verification without calling add_food a second time
        resumed = self.assistant.recipes.save_recipe(
            "Retry Safe Stew",
            [{"food_id": 460334, "grams": 200}],
            servings=2,
            request_id="recipe-req-1",
        )
        self.assertTrue(resumed["verified"])
        self.assertFalse(resumed["already_saved"])
        self.assertEqual(self.backend.add_food_calls, 1)

        # Completed replay succeeds offline with zero remote calls and returns already_saved=True
        self.backend.fail_get_foods = True
        replayed = self.assistant.recipes.save_recipe(
            "Retry Safe Stew",
            [{"food_id": 460334, "grams": 200}],
            servings=2,
            request_id="recipe-req-1",
        )
        self.backend.fail_get_foods = False
        self.assertTrue(replayed["already_saved"])
        self.assertEqual(replayed["food_id"], resumed["food_id"])
        self.assertEqual(self.backend.add_food_calls, 1)

        # Caller-stable draft_id makes save_meal_draft retries idempotent
        d1 = self.assistant.diary.save_meal_draft(
            "Dinner prep", [{"description": "rice", "grams": 150}], draft_id="dinner-2026-11-23"
        )
        d2 = self.assistant.diary.save_meal_draft(
            "Dinner prep", [{"description": "rice", "grams": 150}], draft_id="dinner-2026-11-23"
        )
        self.assertEqual(d1["draft"]["draft_id"], d2["draft"]["draft_id"])
        self.assertEqual(len(self.assistant.diary.list_meal_drafts()["drafts"]), 1)

    def test_completed_and_in_progress_meal_retries_survive_catalog_drift_and_outage(self):
        # Log 1 cup (158 g) with request_id
        first = self.assistant.diary.log_food_entries(
            [{"food_id": 460334, "quantity": 1, "measure_name": "cup"}],
            day="2026-11-22",
            meal_group="lunch",
            time="13:00",
            request_id="cup-drift-1",
        )
        self.assertTrue(first["verified"])
        self.assertAlmostEqual(first["logged"][0]["grams"], 158.0)

        # Mutate catalog cup weight 158 g -> 180 g, retire the food, AND fail remote food reads
        self.backend.foods[460334]["measures"][0]["value"] = 180.0
        self.backend.foods[460334]["retired"] = True
        self.backend.fail_get_foods = True

        # Replaying the exact same request_id + args succeeds offline with original 158 g!
        replayed = self.assistant.diary.log_food_entries(
            [{"food_id": 460334, "quantity": 1, "measure_name": "cup"}],
            day="2026-11-22",
            meal_group="lunch",
            time="13:00",
            request_id="cup-drift-1",
        )
        self.assertTrue(replayed["already_logged"])
        self.assertAlmostEqual(replayed["logged"][0]["grams"], 158.0)
        self.assertEqual(replayed["logged"][0]["entry_id"], first["logged"][0]["entry_id"])

        # Interrupted retry AFTER item 2 wrote remotely (journal left untouched in 'sending' state),
        # followed by a catalog outage AND cup-weight drift -> recovers cleanly via persisted wire plan!
        self.backend.fail_get_foods = False
        self.backend.foods[460334]["retired"] = False
        self.backend.foods[460334]["measures"][0]["value"] = 158.0

        orig_add = self.assistant.diary._add_serving_row
        calls = 0

        def fail_second_after_write(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                orig_add(**kwargs)
                raise httpx.ReadTimeout("simulated timeout after item 2 wrote")
            return orig_add(**kwargs)

        items = [
            {"food_id": 460334, "quantity": 1, "measure_name": "cup"},
            {"food_id": 72014363, "grams": 30},
        ]
        self.assistant.diary._add_serving_row = fail_second_after_write  # type: ignore[method-assign]
        with self.assertRaises(httpx.ReadTimeout):
            self.assistant.diary.log_food_entries(
                items, day="2026-11-23", meal_group="lunch", time="13:00", request_id="cup-mid-1"
            )
        self.assistant.diary._add_serving_row = orig_add  # type: ignore[method-assign]

        # Catalog cup weight drifts AND get_foods is down during replay; journal is untouched!
        self.backend.foods[460334]["measures"][0]["value"] = 180.0
        self.backend.fail_get_foods = True
        resumed = self.assistant.diary.log_food_entries(
            items, day="2026-11-23", meal_group="lunch", time="13:00", request_id="cup-mid-1"
        )
        self.backend.fail_get_foods = False
        self.assertTrue(resumed["verified"])
        self.assertAlmostEqual(resumed["logged"][0]["grams"], 158.0)
        self.assertEqual(self.assistant.diary.get_food_log("2026-11-23")["serving_count"], 2)

        # App-created meal=False batch recipe (400 g, 4 servings): logging 100 g sends 1.0 wire
        # serving, verifies 100 g on first call, and replays identically offline (and if tampered,
        # replay reports the same mismatch instead of falsely certifying the bad entry!).
        self.backend.foods[88010] = {
            "id": 88010,
            "name": "App Four-Serving Rice",
            "source": "Custom",
            "category": 12,
            "owner": 1001,
            "retired": False,
            "meal": False,
            "labelType": "AMERICAN_2016",
            "defaultMeasureId": 88011,
            "barcodes": [],
            "ingredients": [{"foodId": 460334, "grams": 400.0, "measureId": 1048861}],
            "measures": [
                {
                    "id": 88011,
                    "name": "serving",
                    "type": "Recipe",
                    "amount": 1.0,
                    "value": 4.0,
                    "hidden": False,
                }
            ],
            "nutrients": [{"id": 208, "amount": 520.0}, {"id": 203, "amount": 10.8}],
        }
        orig_add_b = self.assistant.diary._add_serving_row

        def interrupt_after_batch_write(**kwargs):
            orig_add_b(**kwargs)
            raise httpx.ReadTimeout("timeout after batch write")

        self.assistant.diary._add_serving_row = interrupt_after_batch_write  # type: ignore[method-assign]
        with self.assertRaises(httpx.ReadTimeout):
            self.assistant.diary.log_food_entries(
                [{"food_id": 88010, "grams": 100}],
                day="2026-11-24",
                meal_group="dinner",
                time="19:00",
                request_id="app-batch-1",
            )
        self.assistant.diary._add_serving_row = orig_add_b  # type: ignore[method-assign]

        # Wire row stored exactly 1.0 serving (NOT 100.0!)
        raw_row = self.backend.diary["2026-11-24"][0]
        self.assertAlmostEqual(raw_row["grams"], 1.0, places=4)
        self.assertEqual(raw_row["measureId"], 88011)

        # Replay with catalog reads disabled verifies 100 g for that single diary entry
        self.backend.fail_get_foods = True
        replayed_batch = self.assistant.diary.log_food_entries(
            [{"food_id": 88010, "grams": 100}],
            day="2026-11-24",
            meal_group="dinner",
            time="19:00",
            request_id="app-batch-1",
        )
        self.backend.fail_get_foods = False
        self.assertTrue(replayed_batch["verified"])
        self.assertAlmostEqual(replayed_batch["logged"][0]["grams"], 100.0, places=2)
        self.assertEqual(len(self.backend.diary["2026-11-24"]), 1)

        # Editing amount on meal=False batch recipe also converts to wire servings properly
        edited_batch = self.assistant.diary.edit_food_entry(
            replayed_batch["logged"][0]["entry_id"], day="2026-11-24", grams=200
        )
        self.assertTrue(edited_batch["verified"])
        self.assertAlmostEqual(edited_batch["grams"], 200.0, places=2)
        self.assertAlmostEqual(self.backend.diary["2026-11-24"][0]["grams"], 2.0, places=4)

        # If backend tampers wire grams to 100 servings, BOTH first call and replay fail identically
        self.backend.tamper_serving_grams = 100.0
        bad_first = self.assistant.diary.log_food_entries(
            [{"food_id": 88010, "grams": 100}],
            day="2026-11-25",
            meal_group="dinner",
            time="19:00",
            request_id="app-batch-tamper-1",
        )
        self.assertFalse(bad_first["verified"])
        self.assertAlmostEqual(bad_first["logged"][0]["grams"], 10000.0, places=1)
        self.backend.fail_get_foods = True
        bad_replay = self.assistant.diary.log_food_entries(
            [{"food_id": 88010, "grams": 100}],
            day="2026-11-25",
            meal_group="dinner",
            time="19:00",
            request_id="app-batch-tamper-1",
        )
        self.backend.fail_get_foods = False
        self.backend.tamper_serving_grams = None
        self.assertFalse(bad_replay["verified"])
        self.assertAlmostEqual(bad_replay["logged"][0]["grams"], 10000.0, places=1)

    def test_pre_v2_journal_record_fails_safely_with_status(self):
        # A pre-v2 (unprefixed) SQLite journal row surfaces its saved status instead of silently
        # overwriting or re-running Cronometer writes.
        self.state.save_idempotent(
            "legacy-meal-1",
            "log_food_entries",
            {"date": "2026-11-22", "logged": [{"entry_id": 7777}], "verified": True},
            fingerprint="a" * 64,
        )
        with self.assertRaises(ValidationError) as ctx:
            self.assistant.diary.log_food_entries(
                [{"food_id": 460334, "grams": 99}],
                day="2026-11-22",
                meal_group="lunch",
                time="13:00",
                request_id="legacy-meal-1",
            )
        self.assertIn("earlier version", str(ctx.exception))
        self.assertIn("completed", str(ctx.exception))

    def test_batch_recipe_unknown_weight_legacy_atomic_and_metadata_only_edits(self):
        # 1) Unknown-weight 4-serving Recipe-measure batch
        self.backend.foods[88001] = {
            "id": 88001,
            "name": "Mystery Four-Serving Chili",
            "source": "Custom",
            "category": 12,
            "owner": 1001,
            "retired": False,
            "meal": True,
            "labelType": "AMERICAN_2016",
            "defaultMeasureId": 88101,
            "barcodes": [],
            "ingredients": [],
            "measures": [
                {
                    "id": 88101,
                    "name": "serving",
                    "type": "Recipe",
                    "amount": 1.0,
                    "value": 4.0,
                    "hidden": False,
                },
            ],
            "nutrients": [
                {"id": 208, "amount": 800.0},
                {"id": 203, "amount": 40.0},
                {"id": 262, "amount": 20.0},
            ],
        }
        # 2) Known-weight 500 g batch with legacy Atomic measure (wire grams=20 -> 0.2 batch = 100 g)
        self.backend.foods[88002] = {
            "id": 88002,
            "name": "Legacy Atomic Soup",
            "source": "Custom",
            "category": 12,
            "owner": 1001,
            "retired": False,
            "meal": True,
            "labelType": "AMERICAN_2016",
            "defaultMeasureId": 88201,
            "barcodes": [],
            "ingredients": [{"foodId": 460334, "grams": 500.0, "measureId": 1048861}],
            "measures": [
                {
                    "id": 88201,
                    "name": "g",
                    "type": "Atomic",
                    "amount": 1.0,
                    "value": 1.0,
                    "hidden": False,
                },
            ],
            "nutrients": [
                {"id": 208, "amount": 500.0},
                {"id": 203, "amount": 25.0},
            ],
        }
        # 3) Unknown-weight batch with legacy Atomic measure (wire grams=20 -> 0.2 batch, grams=None)
        self.backend.foods[88003] = {
            **self.backend.foods[88002],
            "id": 88003,
            "name": "Unknown Weight Atomic Soup",
            "ingredients": [],
        }
        self.backend.diary["2026-11-22"] = [
            {
                "type": "Serving",
                "servingId": 99101,
                "foodId": 88001,
                "measureId": 88101,
                "grams": 1.0,
                "order": (3 << 16) | 1,
                "time": "19:00:00",
                "day": "2026-11-22",
            },
            {
                "type": "Serving",
                "servingId": 99102,
                "foodId": 88002,
                "measureId": 88201,
                "grams": 20.0,
                "order": (2 << 16) | 1,
                "time": "13:00:00",
                "day": "2026-11-22",
            },
            {
                "type": "Serving",
                "servingId": 99103,
                "foodId": 88003,
                "measureId": 88201,
                "grams": 20.0,
                "order": (2 << 16) | 2,
                "time": "13:05:00",
                "day": "2026-11-22",
            },
        ]
        log = self.assistant.diary.get_food_log("2026-11-22", include_untracked=True)
        by_eid = {r["entry_id"]: r for r in log["servings"]}

        self.assertIsNone(by_eid[99101]["grams"])
        self.assertAlmostEqual(by_eid[99101]["key_macros"]["energy_kcal"], 200.0, places=2)
        self.assertAlmostEqual(by_eid[99101]["key_macros"]["protein_g"], 10.0, places=2)
        caffeine = next((n for n in log["nutrients"] if n["id"] == 262), None)
        self.assertIsNotNone(caffeine)
        self.assertAlmostEqual(caffeine["amount"], 5.0, places=2)

        # Known-weight legacy Atomic row: 20 / 100 = 0.2 batch -> 100 g and 100 kcal!
        self.assertAlmostEqual(by_eid[99102]["grams"], 100.0, places=2)
        self.assertAlmostEqual(by_eid[99102]["key_macros"]["energy_kcal"], 100.0, places=2)
        self.assertAlmostEqual(by_eid[99102]["key_macros"]["protein_g"], 5.0, places=2)

        # Unknown-weight legacy Atomic row: 20 / 100 = 0.2 batch -> grams=None and 100 kcal!
        self.assertIsNone(by_eid[99103]["grams"])
        self.assertAlmostEqual(by_eid[99103]["key_macros"]["energy_kcal"], 100.0, places=2)

        # Metadata-only edit (meal_group/time) on unknown-weight batch verifies cleanly with grams=None
        moved = self.assistant.diary.edit_food_entry(
            99101, day="2026-11-22", meal_group="lunch", time="12:30"
        )
        self.assertTrue(moved["verified"])
        self.assertIsNone(moved["grams"])
        self.assertEqual(moved["meal_group"], "lunch")

        # Meal-group-only edit on a normal food preserves grams, measure_id, and time untouched
        normal = self.assistant.diary.log_food_entries(
            [{"food_id": 460334, "grams": 42}],
            day="2026-11-23",
            meal_group="breakfast",
            time="08:15",
        )
        nsid = normal["logged"][0]["entry_id"]
        nmid = normal["logged"][0]["measure_id"]
        moved_normal = self.assistant.diary.edit_food_entry(
            nsid, day="2026-11-23", meal_group="snacks"
        )
        self.assertTrue(moved_normal["verified"])
        self.assertAlmostEqual(moved_normal["grams"], 42.0, places=2)
        self.assertEqual(moved_normal["measure_id"], nmid)
        self.assertEqual(moved_normal["meal_group"], "snacks")
        self.assertEqual(moved_normal["time"], "8:15:0")

        # find_entries_by_food must also report grams=None instead of raw wire 1.0
        found = self.assistant.diary_extras.find_entries_by_food(
            88001, start_day="2026-11-22", end_day="2026-11-22"
        )
        self.assertIsNone(found["entries"][0]["grams"])
        self.assertIsNone(found["total_grams"])
        self.assertEqual(found["unknown_weight_entries"], 1)

    def test_recipe_verification_catches_ignored_swaps_stale_nutrients_notes_and_local_errors(self):
        # Create one servings-based recipe and one weight-based recipe with 100 g rice
        srv_recipe = self.assistant.recipes.save_recipe(
            "Servings Swap Test",
            [{"food_id": 460334, "grams": 100}],
            servings=2,
            serving_type="servings",
        )
        wt_recipe = self.assistant.recipes.save_recipe(
            "Weight Swap Test",
            [{"food_id": 460334, "grams": 100}],
            servings=2,
            serving_type="weight",
            notes="Previously used 10 g of food 42",
        )
        self.assertTrue(srv_recipe["verified"])
        self.assertTrue(wt_recipe["verified"])

        # User note mentioning "10 g of food 42" must NOT be counted as an ingredient
        details = self.assistant.foods.get_food_details(wt_recipe["food_id"])
        self.assertEqual(len(details["ingredients"]), 1)
        self.assertEqual(details["ingredients"][0]["food_id"], 460334)

        # Stale extra nonzero nutrient (even small micronutrient 0.04) must fail verification
        self.backend.inject_stale_recipe_nutrient = {"id": 324, "amount": 0.04}
        stale_res = self.assistant.recipes.save_recipe(
            "Stale Nutrient Check",
            [{"food_id": 460334, "grams": 100}],
            servings=1,
            serving_type="weight",
        )
        self.backend.inject_stale_recipe_nutrient = None
        self.assertFalse(stale_res["verified"])
        self.assertTrue(
            any("unexpected nutrient 324" in m for m in stale_res["verification_mismatches"])
        )

        # User note containing "Cooked weight varies" must NOT lock cooked weight on ingredient change
        cw_note = self.assistant.recipes.save_recipe(
            "Note Cooked Weight Test",
            [{"food_id": 460334, "grams": 100}],
            servings=1,
            serving_type="weight",
            notes="Cooked weight varies",
        )
        cw_updated = self.assistant.recipes.save_recipe(
            "Note Cooked Weight Test",
            [{"food_id": 460334, "grams": 200}],
            servings=1,
            recipe_id=cw_note["food_id"],
        )
        self.assertTrue(cw_updated["verified"])
        self.assertAlmostEqual(cw_updated["final_grams"], 200.0, places=2)

        # Duplicate-named existing measures with distinct IDs are both preserved (never stranding request_id)
        dup_id = cw_note["food_id"]
        self.backend.foods[dup_id]["measures"].append(
            {"id": 99555, "name": "serving", "type": "Weight", "amount": 1.0, "value": 50.0}
        )
        self.backend.foods[dup_id]["measures"].append(
            {"id": 99556, "name": "serving", "type": "Weight", "amount": 1.0, "value": 75.0}
        )
        dup_saved = self.assistant.recipes.save_recipe(
            "Note Cooked Weight Test",
            [{"food_id": 460334, "grams": 200}],
            servings=2,
            recipe_id=dup_id,
            request_id="dup-measures-1",
        )
        self.assertTrue(dup_saved["verified"])
        persisted_mids = {m["measure_id"] for m in dup_saved["measures"]}
        self.assertTrue({99555, 99556}.issubset(persisted_mids))

        # Footer parser round-trips scientific-notation weights emitted by `:g`
        from cronometer_assistant.parsing import parse_weight_recipe_ingredient_footer

        self.assertEqual(
            parse_weight_recipe_ingredient_footer(
                "Note\n\nRaw ingredients (1e+06 g): 1e+06 g of food 42; 25.5 g of food 7"
            ),
            [(42, 1000000.0), (7, 25.5)],
        )

        # Local nutrient validation failure must NOT leave request_id stuck in 'sending'
        self.backend.foods[88999] = {
            "id": 88999,
            "name": "Empty Nutrient Food",
            "source": "Custom",
            "category": 1,
            "owner": 1001,
            "retired": False,
            "meal": False,
            "defaultMeasureId": 88991,
            "barcodes": [],
            "measures": [
                {
                    "id": 88991,
                    "name": "g",
                    "type": "Weight",
                    "amount": 1.0,
                    "value": 1.0,
                    "hidden": False,
                }
            ],
            "nutrients": [],
        }
        with self.assertRaises(ValidationError):
            self.assistant.recipes.save_recipe(
                "No Nutrient Recipe",
                [{"food_id": 88999, "grams": 100}],
                request_id="recipe-local-fail-1",
            )
        self.assertIsNone(self.state.get_operation("recipe-local-fail-1", "save_recipe"))
        # Once the food has nutrients, the same request_id works cleanly!
        self.backend.foods[88999]["nutrients"] = [{"id": 208, "amount": 100.0}]
        recovered = self.assistant.recipes.save_recipe(
            "No Nutrient Recipe",
            [{"food_id": 88999, "grams": 100}],
            request_id="recipe-local-fail-1",
        )
        self.assertTrue(recovered["verified"])

        # Backend acknowledges save but silently retains the old recipe
        self.backend.ignore_recipe_updates = True

        srv_edit = self.assistant.recipes.save_recipe(
            "Servings Swap Test Renamed",
            [{"food_id": 72014363, "grams": 100}],
            servings=2,
            recipe_id=srv_recipe["food_id"],
        )
        self.assertFalse(srv_edit["verified"])
        self.assertTrue(any("ingredient" in m for m in srv_edit["verification_mismatches"]))
        self.assertTrue(any("name:" in m for m in srv_edit["verification_mismatches"]))

        wt_edit = self.assistant.recipes.save_recipe(
            "Weight Swap Test Renamed",
            [{"food_id": 72014363, "grams": 100}],
            servings=2,
            recipe_id=wt_recipe["food_id"],
        )
        self.assertFalse(wt_edit["verified"])
        self.assertTrue(any("ingredient" in m for m in wt_edit["verification_mismatches"]))
        self.assertTrue(any("nutrient" in m for m in wt_edit["verification_mismatches"]))
        self.assertTrue(any("name:" in m for m in wt_edit["verification_mismatches"]))

    def test_concurrent_retire_and_update_custom_food_do_not_clobber_each_other(self):
        from concurrent.futures import ThreadPoolExecutor

        self.backend.get_food_delay = 0.03
        with ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(
                self.assistant.custom.update_custom_food,
                72014363,
                name="Pretzels Renamed Safely",
            )
            f2 = pool.submit(self.assistant.custom.retire_custom_food, 72014363, retired=True)
            r1, r2 = f1.result(), f2.result()
        self.backend.get_food_delay = 0.0
        self.assertTrue(r1["verified"])
        self.assertTrue(r2["verified"])
        final = self.assistant.foods.get_food_details(72014363)
        self.assertEqual(final["name"], "Pretzels Renamed Safely")
        self.assertTrue(final["retired"])

    def test_custom_food_resumes_verification_after_readback_failure_and_hashes_notes(self):
        self.backend.fail_next_get_food = True
        with self.assertRaises(httpx.HTTPStatusError):
            self.assistant.custom.create_custom_food(
                "Crash Safe Bar",
                {"energy": 200, "protein": 15, "carbs": 20, "fiber": 5, "fat": 6},
                carbohydrate_basis="excludes_fiber",
                notes="batch A",
                request_id="cf-readback-1",
            )
        self.assertEqual(self.backend.add_food_calls, 1)

        # Retry resumes verification by persisted food_id without creating a second custom food
        resumed = self.assistant.custom.create_custom_food(
            "Crash Safe Bar",
            {"energy": 200, "protein": 15, "carbs": 20, "fiber": 5, "fat": 6},
            carbohydrate_basis="excludes_fiber",
            notes="batch A",
            request_id="cf-readback-1",
        )
        self.assertTrue(resumed["verified"])
        self.assertFalse(resumed["already_created"])
        self.assertEqual(self.backend.add_food_calls, 1)

        # Changing notes with the same request_id must be rejected as a different payload
        with self.assertRaises(ValidationError):
            self.assistant.custom.create_custom_food(
                "Crash Safe Bar",
                {"energy": 200, "protein": 15, "carbs": 20, "fiber": 5, "fat": 6},
                carbohydrate_basis="excludes_fiber",
                notes="batch B",
                request_id="cf-readback-1",
            )

        # Completed replay works even if Cronometer is offline
        self.backend.fail_next_get_food = True
        replayed = self.assistant.custom.create_custom_food(
            "Crash Safe Bar",
            {"energy": 200, "protein": 15, "carbs": 20, "fiber": 5, "fat": 6},
            carbohydrate_basis="excludes_fiber",
            notes="batch A",
            request_id="cf-readback-1",
        )
        self.assertTrue(replayed["already_created"])
        self.assertEqual(replayed["food_id"], resumed["food_id"])

    def test_slow_remote_read_does_not_block_concurrent_local_mcp_tool(self):
        import time

        self.assistant.diary.save_meal_draft(
            "Quick Draft", [{"description": "eggs", "quantity": 2}], draft_id="fast-1"
        )
        self.backend.diary_read_delay = 0.25
        srv = create_server(self.assistant)

        async def run():
            order: list[tuple[str, float]] = []

            async def slow_remote():
                res = await srv.call_tool("get_food_log", {"date": "2026-11-22"})
                order.append(("remote", time.monotonic()))
                return res

            async def fast_local():
                await asyncio.sleep(0.03)
                res = await srv.call_tool("list_meal_drafts", {})
                order.append(("local", time.monotonic()))
                return res

            rem_res, loc_res = await asyncio.gather(slow_remote(), fast_local())
            rem_text = rem_res[0][0].text if isinstance(rem_res, tuple) else rem_res[0].text
            loc_text = loc_res[0][0].text if isinstance(loc_res, tuple) else loc_res[0].text
            self.assertTrue(json.loads(rem_text)["ok"])
            self.assertTrue(json.loads(loc_text)["ok"])
            self.assertEqual([name for name, _ in order], ["local", "remote"])
            self.assertGreater(order[1][1] - order[0][1], 0.1)

        asyncio.run(run())

    def test_get_food_log_degrades_gracefully_when_nutrition_scores_unavailable(self):
        self.assistant.diary.log_food_entries(
            [{"food_id": 460334, "grams": 100}, {"food_id": 72014363, "grams": 30}],
            day="2026-11-22",
            meal_group="dinner",
            time="19:00",
        )

        # Normal mode reports complete
        ok_log = self.assistant.diary.get_food_log("2026-11-22")
        self.assertEqual(ok_log["nutrient_summary_status"], "complete")
        self.assertNotIn("nutrient_summary_warning", ok_log)

        # Missing "All Targets" returns servings + locally computed totals with degraded status
        self.backend.scores_mode = "missing_all_targets"
        deg1 = self.assistant.diary.get_food_log("2026-11-22")
        self.assertEqual(deg1["nutrient_summary_status"], "degraded")
        self.assertIn("All Targets", deg1["nutrient_summary_warning"])
        self.assertEqual(deg1["serving_count"], 2)
        self.assertAlmostEqual(deg1["macro_summary"]["energy"]["amount"], 244.0, places=1)
        self.assertAlmostEqual(deg1["macro_summary"]["protein"]["amount"], 5.7, places=1)

        # HTTP 500 on scores still returns the food list and local totals
        self.backend.scores_mode = "error_500"
        deg2 = self.assistant.diary.get_food_log("2026-11-22")
        self.assertEqual(deg2["nutrient_summary_status"], "degraded")
        self.assertIn("500", deg2["nutrient_summary_warning"])
        self.assertEqual(deg2["serving_count"], 2)

        # Malformed JSON on 200 OK from scores still degrades gracefully
        self.backend.scores_mode = "invalid_json"
        deg3 = self.assistant.diary.get_food_log("2026-11-22")
        self.assertEqual(deg3["nutrient_summary_status"], "degraded")
        self.assertEqual(deg3["serving_count"], 2)

        # Auth errors (401 -> SessionExpiredError) must still propagate, not be silently swallowed
        from cronometer_assistant.auth import SessionExpiredError

        self.backend.scores_mode = "error_401"
        with self.assertRaises(SessionExpiredError):
            self.assistant.diary.get_food_log("2026-11-22")

    def test_upstream_client_adapter_contract(self):
        from cronometer_mcp.client import CronometerClient

        for method_name in (
            "_request",
            "_request_v3",
            "_save_food",
            "_normalise_ingredients",
            "_format_day",
            "_tzinfo",
        ):
            self.assertTrue(
                hasattr(CronometerClient, method_name),
                f"Upstream CronometerClient missing expected helper {method_name}",
            )

    def test_tunnel_launcher_helpers(self):
        from unittest.mock import patch

        tid = "tunnel_" + "a" * 32
        self.assertEqual(validate_tunnel_id(tid), tid)
        with self.assertRaises(ValueError):
            validate_tunnel_id("bad")
        self.assertEqual(validate_client_version("v1.2.3"), "v1.2.3")
        with self.assertRaises(ValueError):
            validate_client_version("latest")

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("bin/tunnel-client", b"#!/bin/sh\necho ok\n")
        payload = buf.getvalue()
        digest = "sha256:" + hashlib.sha256(payload).hexdigest()

        asset = choose_asset(
            {
                "tag_name": "v1.2.3",
                "assets": [
                    {
                        "name": "tunnel-client-v1.2.3-linux-amd64.zip",
                        "browser_download_url": (
                            "https://github.com/openai/tunnel-client/releases/download/"
                            "v1.2.3/tunnel-client-v1.2.3-linux-amd64.zip"
                        ),
                        "digest": digest,
                    }
                ],
            },
            "Linux",
            "x86_64",
        )
        self.assertEqual(asset["digest"], digest)

        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            dest = runtime_dir / "v1.2.3" / "tunnel-client"
            install_archive(payload, digest, dest)
            self.assertTrue(dest.is_file())
            self.assertEqual(find_cached_client(runtime_dir), dest)
            self.assertEqual(find_cached_client(runtime_dir, version="v1.2.3"), dest)
            self.assertIsNone(find_cached_client(runtime_dir, version="v1.2.4"))

            # acquire_client reuses cached binary without hitting GitHub on restarts
            with patch(
                "scripts.run_tunnel.read_public_url",
                side_effect=AssertionError("should not access network when cached binary exists"),
            ):
                reused = acquire_client(runtime_dir)
                self.assertEqual(reused, dest)

            # Symlinked version directory must be rejected on install without touching its target
            outside = runtime_dir / "outside"
            outside.mkdir()
            (runtime_dir / "v1.2.4").symlink_to(outside)
            with self.assertRaises(ValueError):
                install_archive(payload, digest, runtime_dir / "v1.2.4" / "tunnel-client")
            self.assertEqual(list(outside.iterdir()), [])

            cmd = launch_arguments(
                dest,
                tid,
                Path("/usr/bin/python3"),
                runtime_dir / "assistant.sqlite",
                runtime_dir / "session.json",
                runtime_dir / "health.url",
            )
            self.assertIn("serve", " ".join(cmd))


if __name__ == "__main__":
    unittest.main()
