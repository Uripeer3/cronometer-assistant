"""Core diary reading, batch logging, editing, removal, and optional cooking drafts."""

from __future__ import annotations

import hashlib
import json
import math
import threading
from copy import deepcopy
from datetime import date
from datetime import time as dtime
from typing import Any

import httpx
from cronometer_mcp.client import CronometerError

from ..auth import AuthError, CronometerSessionClient, SessionExpiredError
from ..nutrition import ValidationError, clean_text, finite_number, resolve_serving_amount
from ..parsing import (
    ID_TO_MEAL_GROUP,
    SUMMARY_MACRO_IDS,
    batch_recipe_wire_units,
    extract_energy_summary,
    extract_key_macros,
    format_api_time,
    is_batch_recipe,
    is_recipe_food,
    meal_group_for_hour,
    parse_day,
    parse_meal_group,
    parse_time,
    round_sig,
    serving_scale_and_display,
)
from ..state import AssistantState


class DiaryService:
    def __init__(
        self, client: CronometerSessionClient, state: AssistantState | None = None
    ) -> None:
        self.client = client
        self.state = state
        self._write_lock = threading.RLock()

    def _op_lock(self):
        return self.state.operation_lock() if self.state is not None else self._write_lock

    def _resolve_day(self, day_str: str | None) -> date:
        parsed = parse_day(day_str)
        if parsed is not None:
            return parsed
        self.client.sync_timezone(force=True)
        return self.client.today()

    @staticmethod
    def _normalize_time_str(value: Any) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return None
        parts = value.strip().split(":")
        try:
            nums = [int(p) for p in parts]
            if len(nums) == 2:
                return dtime(nums[0], nums[1], 0).isoformat(timespec="seconds")
            if len(nums) == 3:
                return dtime(nums[0], nums[1], nums[2]).isoformat(timespec="seconds")
        except ValueError:
            return value.strip()
        return value.strip()

    @classmethod
    def _verify_serving_row(
        cls,
        row: dict[str, Any],
        raw_food: dict[str, Any] | None,
        *,
        expected_food_id: int,
        expected_grams: float | None,
        expected_measure_id: int | None,
        expected_group_id: int | None,
        expected_time: dtime | None,
        expected_wire_grams: float | None = None,
        expected_grams_per_wire_unit: float | None = None,
        expected_is_batch_recipe: bool | None = None,
    ) -> dict[str, Any]:
        """Compare observed diary serving fields against requested values.

        Supports both full food metadata (`raw_food` provided) and catalog-free
        recovery/metadata-only edits using the catalog-derived `grams_per_wire_unit`.
        """
        obs_food_id = row.get("foodId")
        obs_measure_id = row.get("measureId")
        obs_wire_grams = row.get("grams")
        obs_gid = (row.get("order") or 0) >> 16
        obs_time_raw = row.get("time")
        obs_time_norm = cls._normalize_time_str(obs_time_raw)

        if raw_food is not None:
            _, _, _, actual_grams = serving_scale_and_display(raw_food, row)
            batch_recipe = is_batch_recipe(raw_food)
        else:
            batch_recipe = bool(expected_is_batch_recipe)
            if not isinstance(obs_wire_grams, (int, float)):
                actual_grams = None
            elif expected_grams_per_wire_unit is not None and expected_grams_per_wire_unit > 0:
                actual_grams = round(float(obs_wire_grams) * expected_grams_per_wire_unit, 4)
            elif not batch_recipe:
                actual_grams = float(obs_wire_grams)
            else:
                actual_grams = None

        mismatches: list[str] = []
        if obs_food_id != expected_food_id:
            mismatches.append(f"food_id: expected {expected_food_id}, observed {obs_food_id}")

        if expected_grams is not None:
            if actual_grams is None or not math.isclose(
                actual_grams, expected_grams, rel_tol=1e-3, abs_tol=0.05
            ):
                mismatches.append(f"grams: expected {expected_grams}, observed {actual_grams}")
        elif expected_wire_grams is not None:
            if not isinstance(obs_wire_grams, (int, float)) or not math.isclose(
                float(obs_wire_grams), expected_wire_grams, rel_tol=1e-3, abs_tol=1e-4
            ):
                mismatches.append(
                    f"wire_grams: expected {expected_wire_grams}, observed {obs_wire_grams}"
                )

        check_measure = expected_measure_id is not None and (
            not batch_recipe or expected_grams is None or raw_food is None
        )
        if check_measure and obs_measure_id != expected_measure_id:
            mismatches.append(
                f"measure_id: expected {expected_measure_id}, observed {obs_measure_id}"
            )
        if expected_group_id is not None and obs_gid != expected_group_id:
            mismatches.append(f"meal_group: expected {expected_group_id}, observed {obs_gid}")
        if expected_time is not None:
            exp_time_norm = expected_time.isoformat(timespec="seconds")
            if obs_time_norm != exp_time_norm:
                mismatches.append(f"time: expected {exp_time_norm}, observed {obs_time_norm}")

        return {
            "food_id": obs_food_id,
            "grams": round(actual_grams, 4) if actual_grams is not None else None,
            "measure_id": obs_measure_id,
            "meal_group": ID_TO_MEAL_GROUP.get(obs_gid, f"group_{obs_gid}"),
            "time": obs_time_raw,
            "verified": len(mismatches) == 0,
            "mismatches": mismatches,
        }

    def _fetch_all_targets(
        self, serving_ids: list[int]
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Fetch Cronometer's 'All Targets' score block; return (None, reason) on non-auth degradation."""
        try:
            scores = self.client.request_v2(
                "/api/v2/get_nutrition_scores",
                {
                    "startDay": "1900-1-1",
                    "endDay": "1900-1-1",
                    "servingIds": serving_ids,
                    "supplements": "true",
                    "config": {"call_version": 1},
                },
            )
        except (SessionExpiredError, AuthError):
            raise
        except httpx.HTTPStatusError as error:
            if error.response.status_code in (401, 403):
                raise
            return None, f"HTTP {error.response.status_code}"
        except (CronometerError, httpx.HTTPError, ValueError) as error:
            return None, str(error)

        if not isinstance(scores, dict) or not isinstance(scores.get("scores"), list):
            return None, "invalid nutrition scores payload"
        all_targets = next(
            (
                c
                for c in scores["scores"]
                if isinstance(c, dict) and c.get("title") == "All Targets"
            ),
            None,
        )
        if all_targets is None or not isinstance(all_targets.get("components"), list):
            return None, "nutrition scores omitted 'All Targets'"
        return all_targets, None

    def get_food_log(
        self, day: str | None = None, *, include_untracked: bool = True
    ) -> dict[str, Any]:
        """Read a full diary day: enriched entries, calorie targets/remaining, and all nutrients."""
        target_day = self._resolve_day(day)
        diary = self.client.get_diary(target_day)
        if not isinstance(diary, dict) or not isinstance(diary.get("diary"), list):
            raise CronometerError("Cronometer returned an invalid diary payload")

        raw_entries = diary["diary"]
        serving_food_ids = sorted(
            {
                e["foodId"]
                for e in raw_entries
                if isinstance(e, dict)
                and e.get("type") == "Serving"
                and isinstance(e.get("foodId"), int)
            }
        )
        foods_by_id = (
            {f["id"]: f for f in self.client.get_foods(serving_food_ids) if isinstance(f, dict)}
            if serving_food_ids
            else {}
        )
        defs = self.client.get_nutrient_definitions()

        servings_out: list[dict[str, Any]] = []
        other_entries: list[dict[str, Any]] = []
        serving_ids: list[int] = []
        local_totals: dict[int, float] = {}
        unscaled_servings = 0

        for row in raw_entries:
            if not isinstance(row, dict):
                continue
            etype = row.get("type")
            if etype != "Serving":
                other_entries.append(
                    {
                        "type": etype,
                        "id": row.get("exerciseId") or row.get("biometricId") or row.get("noteId"),
                        "name": row.get("name") or row.get("text"),
                        "calories": row.get("calories"),
                        "minutes": row.get("minutes"),
                        "amount": row.get("amount"),
                    }
                )
                continue

            sid = row.get("servingId")
            if isinstance(sid, int):
                serving_ids.append(sid)
            fid = row.get("foodId")
            raw_food = foods_by_id.get(fid, {})
            scale, measure_info, display_servings, actual_grams = serving_scale_and_display(
                raw_food, row
            )

            group_id = (row.get("order") or 0) >> 16
            servings_out.append(
                {
                    "entry_id": sid,
                    "food_id": fid,
                    "name": raw_food.get("name"),
                    "source": raw_food.get("source"),
                    "translation_id": row.get("translationId") or 0,
                    "diary_group_id": group_id,
                    "meal_group": ID_TO_MEAL_GROUP.get(group_id, f"group_{group_id}"),
                    "time": row.get("time"),
                    "grams": actual_grams,
                    "measure": measure_info,
                    "servings": display_servings,
                    "is_recipe": is_recipe_food(raw_food),
                    "key_macros": extract_key_macros(raw_food, scale) if scale is not None else {},
                }
            )

            if scale is None:
                unscaled_servings += 1
            else:
                for n in raw_food.get("nutrients", []):
                    if not isinstance(n, dict):
                        continue
                    nid, amt = n.get("id"), n.get("amount")
                    if (
                        isinstance(nid, int)
                        and isinstance(amt, (int, float))
                        and math.isfinite(amt)
                    ):
                        local_totals[nid] = local_totals.get(nid, 0.0) + float(amt) * scale

        all_targets, degraded_reason = self._fetch_all_targets(serving_ids)
        nutrient_summary_status = "complete" if all_targets is not None else "degraded"
        nutrient_summary_warning: str | None = None
        if degraded_reason is not None:
            nutrient_summary_warning = (
                f"Cronometer nutrition scores unavailable ({degraded_reason}); "
                "macro and nutrient totals below are computed locally from logged foods."
            )
            if unscaled_servings:
                nutrient_summary_warning += f" {unscaled_servings} serving(s) had unknown scale and were omitted from local totals."

        nutrients_out: list[dict[str, Any]] = []
        amounts_by_id: dict[int, float | None] = {}
        if all_targets is not None:
            for comp in all_targets["components"]:
                if not isinstance(comp, dict) or type(comp.get("nutrientId")) is not int:
                    continue
                nid = comp["nutrientId"]
                amt = comp.get("amount")
                amounts_by_id[nid] = amt
                meta = defs.get(nid, {})
                nutrients_out.append(
                    {
                        "id": nid,
                        "name": meta.get("name"),
                        "amount": amt,
                        "unit": meta.get("unit"),
                        "category": meta.get("category"),
                        "confidence": comp.get("confidence"),
                        "tracked": True,
                    }
                )
        tracked_count = len(nutrients_out)

        if include_untracked or nutrient_summary_status == "degraded":
            for nid, amt in sorted(local_totals.items()):
                if nid in amounts_by_id or nid <= 0:
                    continue
                meta = defs.get(nid, {})
                rounded = round_sig(amt)
                amounts_by_id[nid] = rounded
                nutrients_out.append(
                    {
                        "id": nid,
                        "name": meta.get("name"),
                        "amount": rounded,
                        "unit": meta.get("unit"),
                        "category": meta.get("category"),
                        "confidence": None,
                        "tracked": False,
                    }
                )

        macro_summary = {}
        for key, nid in SUMMARY_MACRO_IDS.items():
            meta = defs.get(nid, {})
            macro_summary[key] = {
                "amount": amounts_by_id.get(nid),
                "unit": meta.get("unit") or ("kcal" if key == "energy" else "g"),
            }

        info = diary.get("info") if isinstance(diary.get("info"), dict) else {}
        out: dict[str, Any] = {
            "date": target_day.isoformat(),
            "account_timezone": self.client.account_timezone,
            "day_complete": bool(info.get("complete", False)),
            "nutrient_summary_status": nutrient_summary_status,
            "energy_summary": extract_energy_summary(diary),
            "macro_summary": macro_summary,
            "serving_count": len(servings_out),
            "servings": servings_out,
            "other_entries": other_entries,
            "tracked_nutrient_count": tracked_count,
            "untracked_nutrient_count": len(nutrients_out) - tracked_count,
            "nutrients": nutrients_out,
        }
        if nutrient_summary_warning is not None:
            out["nutrient_summary_warning"] = nutrient_summary_warning
        return out

    def _add_serving_row(
        self,
        *,
        food_id: int,
        wire_measure_id: int,
        wire_grams: float,
        translation_id: int,
        target_day: date,
        group_id: int | None,
        wall_time: dtime | None,
    ) -> dict[str, Any]:
        """Single wire write path for adding a serving using pre-resolved wire fields."""
        stamp = wall_time or self.client.now().timetz()
        gid = 0 if group_id is None else group_id
        serving = {
            "order": (gid << 16) | 1,
            "day": self.client.format_day(target_day),
            "time": format_api_time(stamp),
            "offset": None,
            "source": None,
            "userId": self.client.user_id,
            "servingId": None,
            "type": "Serving",
            "foodId": food_id,
            "measureId": wire_measure_id or 0,
            "grams": wire_grams,
            "translationId": translation_id,
        }
        return self.client.request_v2(
            "/api/v2/add_serving", {"serving": serving, "config": {"call_version": 2}}
        )

    @staticmethod
    def _prepare_log_inputs(
        entries: list[dict[str, Any]],
        *,
        explicit_day: date | None,
        default_group: int | None,
        default_time: dtime | None,
    ) -> tuple[list[dict[str, Any]], bool, str]:
        """Validate caller inputs locally and hash them before touching Cronometer."""
        normalized: list[dict[str, Any]] = []
        needs_now = False
        for idx, item in enumerate(entries):
            if not isinstance(item, dict):
                raise ValidationError(f"entries[{idx}] must be an object")
            fid = item.get("food_id")
            if type(fid) is not int or fid <= 0:
                raise ValidationError(f"entries[{idx}].food_id must be a positive integer")
            grams_raw = item.get("grams")
            qty_raw = item.get("quantity")
            if (grams_raw is None) == (qty_raw is None):
                raise ValidationError(
                    f"entries[{idx}]: provide either `grams` OR `quantity` (not both)"
                )
            grams_val = (
                finite_number(grams_raw, f"entries[{idx}].grams", positive=True)
                if grams_raw is not None
                else None
            )
            qty_val = (
                finite_number(qty_raw, f"entries[{idx}].quantity", positive=True)
                if qty_raw is not None
                else None
            )
            mid_raw = item.get("measure_id")
            if mid_raw is not None and (type(mid_raw) is not int or mid_raw <= 0):
                raise ValidationError(f"entries[{idx}].measure_id must be a positive integer")
            mname_raw = item.get("measure_name")
            mname_clean = (
                clean_text(mname_raw, f"entries[{idx}].measure_name", 100)
                if mname_raw is not None
                else None
            )
            tid = item.get("translation_id", 0)
            if type(tid) is not int or tid < 0:
                raise ValidationError(f"entries[{idx}].translation_id must be a non-negative int")
            item_group = (
                parse_meal_group(item["meal_group"], allow_auto=True)
                if item.get("meal_group") is not None
                else default_group
            )
            item_time = parse_time(item["time"]) if item.get("time") is not None else default_time
            if item_time is None:
                needs_now = True
            normalized.append(
                {
                    "food_id": fid,
                    "grams": grams_val,
                    "quantity": qty_val,
                    "measure_id": mid_raw,
                    "measure_name": mname_clean,
                    "translation_id": tid,
                    "group_id": item_group,
                    "wall_time": item_time,
                }
            )

        # Fingerprint hashes ONLY normalized caller inputs (never mutable catalog weights) so
        # replaying a completed or interrupted request_id works even after catalog drift or offline.
        fingerprint = (
            "v2:"
            + hashlib.sha256(
                json.dumps(
                    {
                        "day": explicit_day.isoformat() if explicit_day is not None else None,
                        "items": [
                            {
                                "food_id": s["food_id"],
                                "grams": s["grams"],
                                "quantity": s["quantity"],
                                "measure_id": s["measure_id"],
                                "measure_name": s["measure_name"].casefold()
                                if s["measure_name"] is not None
                                else None,
                                "translation_id": s["translation_id"],
                                "group_id": s["group_id"],
                                "wall_time": s["wall_time"].isoformat(timespec="seconds")
                                if s["wall_time"]
                                else None,
                            }
                            for s in normalized
                        ],
                    },
                    sort_keys=True,
                ).encode()
            ).hexdigest()
        )
        return normalized, needs_now, fingerprint

    @staticmethod
    def _wire_serving_fields(
        raw_food: dict[str, Any], measure_id: int, grams: float
    ) -> tuple[int, float, float, bool]:
        """Compute exact wire `(wire_measure_id, wire_grams, grams_per_wire_unit, is_batch)`."""
        units = batch_recipe_wire_units(raw_food, measure_id=measure_id)
        if units is None:
            return measure_id, grams, 1.0, False
        wire_mid, grams_per_unit = units
        return wire_mid, grams / grams_per_unit, grams_per_unit, True

    def _resolve_planned_entries(
        self, normalized_inputs: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]:
        """Fetch Cronometer foods, validate active status, and resolve exact grams/measures."""
        food_ids = sorted({item["food_id"] for item in normalized_inputs})
        foods_by_id = {
            f["id"]: f
            for f in self.client.get_foods(food_ids)
            if isinstance(f, dict) and f.get("id")
        }

        planned: list[dict[str, Any]] = []
        for idx, item in enumerate(normalized_inputs):
            fid = item["food_id"]
            raw_food = foods_by_id.get(fid)
            if raw_food is None:
                raise ValidationError(f"entries[{idx}]: food_id {fid} not found in Cronometer")
            if raw_food.get("retired") is True:
                raise ValidationError(
                    f"entries[{idx}]: food {raw_food.get('name')!r} (id={fid}) is retired"
                )
            resolved = resolve_serving_amount(
                raw_food,
                grams=item["grams"],
                quantity=item["quantity"],
                measure_id=item["measure_id"],
                measure_name=item["measure_name"],
            )
            wire_mid, wire_g, gpu, batch = self._wire_serving_fields(
                raw_food, resolved["measure_id"], resolved["grams"]
            )
            planned.append(
                {
                    "food_id": fid,
                    "food_name": raw_food.get("name"),
                    "measure_id": resolved["measure_id"],
                    "measure_name": resolved["measure_name"],
                    "grams": resolved["grams"],
                    "wire_measure_id": wire_mid,
                    "wire_grams": wire_g,
                    "grams_per_wire_unit": gpu,
                    "is_batch_recipe": batch,
                    "translation_id": item["translation_id"],
                    "group_id": item["group_id"],
                    "wall_time": item["wall_time"],
                }
            )
        return planned, foods_by_id

    @staticmethod
    def _legacy_v1_log_fingerprint(planned: list[dict[str, Any]], explicit_day: date | None) -> str:
        """Compute the pre-v2 fingerprint format written by commit 90e00e2 for upgrade compatibility."""
        return hashlib.sha256(
            json.dumps(
                {
                    "day": explicit_day.isoformat() if explicit_day is not None else None,
                    "steps": [
                        {
                            "food_id": s["food_id"],
                            "measure_id": s["measure_id"],
                            "grams": s["grams"],
                            "translation_id": s["translation_id"],
                            "group_id": s["group_id"],
                            "wall_time": s["wall_time"].isoformat(timespec="seconds")
                            if s["wall_time"]
                            else None,
                        }
                        for s in planned
                    ],
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()

    def _reconcile_sending_steps(
        self,
        *,
        planned: list[dict[str, Any]],
        journal_steps: list[dict[str, Any]],
        foods_by_id: dict[int, dict[str, Any]],
        target_day: date,
        request_id: str,
        fingerprint: str,
    ) -> None:
        """Match previously interrupted `sending` steps strictly by food, grams, measure, group, and time."""
        assert self.state is not None
        diary_now = self.client.get_diary(target_day)
        claimed = {s["entry_id"] for s in journal_steps if s.get("entry_id") is not None}
        for idx, (step, jstep) in enumerate(zip(planned, journal_steps, strict=True)):
            if jstep["status"] != "sending":
                continue
            baseline = set(jstep.get("baseline_ids") or [])
            raw_food = foods_by_id.get(step["food_id"])
            candidates: list[int] = []
            nearby: list[dict[str, Any]] = []
            for row in diary_now.get("diary", []):
                if not isinstance(row, dict) or row.get("type") != "Serving":
                    continue
                sid = row.get("servingId")
                if sid in baseline or sid in claimed:
                    continue
                check = self._verify_serving_row(
                    row,
                    raw_food,
                    expected_food_id=step["food_id"],
                    expected_grams=step["grams"],
                    expected_measure_id=step["wire_measure_id"],
                    expected_group_id=step["group_id"],
                    expected_time=step["wall_time"],
                    expected_wire_grams=step["wire_grams"],
                    expected_grams_per_wire_unit=step.get("grams_per_wire_unit"),
                    expected_is_batch_recipe=step.get("is_batch_recipe"),
                )
                if check["verified"]:
                    candidates.append(sid)
                elif check["food_id"] == step["food_id"] and (
                    check["grams"] is not None
                    and math.isclose(check["grams"], step["grams"], rel_tol=1e-3, abs_tol=0.05)
                ):
                    nearby.append(
                        {
                            "entry_id": sid,
                            "grams": check["grams"],
                            "meal_group": check["meal_group"],
                            "time": check["time"],
                        }
                    )
            if len(candidates) == 1:
                jstep["entry_id"] = candidates[0]
                jstep["status"] = "confirmed"
                claimed.add(candidates[0])
                self.state.save_operation(
                    request_id,
                    "log_food_entries",
                    fingerprint=fingerprint,
                    status="in_progress",
                    data={"day": target_day.isoformat(), "steps": journal_steps},
                )
            else:
                nearby_note = (
                    f" Unclaimed entries with the same food/weight at a different time or "
                    f"meal group were not adopted: {nearby}."
                    if nearby
                    else ""
                )
                raise CronometerError(
                    f"Previous attempt for item {idx} ({step['food_name']!r}) has an "
                    f"uncertain outcome ({len(candidates)} exact matching entries found). "
                    f"Check get_food_log for {target_day.isoformat()} before retrying."
                    f"{nearby_note}"
                )

    def _verify_logged_batch(
        self,
        *,
        planned: list[dict[str, Any]],
        added_ids: list[int],
        foods_by_id: dict[int, dict[str, Any]],
        target_day: date,
    ) -> dict[str, Any]:
        """Read back the diary day and compare every logged serving against the request."""
        diary_after = self.client.get_diary(target_day)
        by_sid = {
            e.get("servingId"): e
            for e in diary_after.get("diary", [])
            if isinstance(e, dict) and e.get("type") == "Serving"
        }
        missing = [sid for sid in added_ids if sid not in by_sid]
        if missing:
            raise CronometerError(
                f"Read-back verification failed: serving IDs {missing} not found on {target_day.isoformat()}"
            )

        logged_out = []
        all_verified = True
        all_mismatches: list[dict[str, Any]] = []
        for idx, step in enumerate(planned):
            row = by_sid[step["entry_id"]]
            raw_food = foods_by_id.get(step["food_id"])
            check = self._verify_serving_row(
                row,
                raw_food,
                expected_food_id=step["food_id"],
                expected_grams=step["grams"],
                expected_measure_id=step["wire_measure_id"],
                expected_group_id=step["group_id"],
                expected_time=step["wall_time"],
                expected_wire_grams=step["wire_grams"],
                expected_grams_per_wire_unit=step.get("grams_per_wire_unit"),
                expected_is_batch_recipe=step.get("is_batch_recipe"),
            )
            if not check["verified"]:
                all_verified = False
                all_mismatches.append(
                    {"index": idx, "entry_id": step["entry_id"], "issues": check["mismatches"]}
                )
            item_out: dict[str, Any] = {
                "entry_id": step["entry_id"],
                "food_id": check["food_id"],
                "food_name": step["food_name"],
                "grams": check["grams"],
                "measure_id": check["measure_id"],
                "measure_name": step["measure_name"],
                "meal_group": check["meal_group"],
                "time": check["time"],
                "verified": check["verified"],
            }
            if check["mismatches"]:
                item_out["verification_mismatches"] = check["mismatches"]
            logged_out.append(item_out)

        response: dict[str, Any] = {
            "date": target_day.isoformat(),
            "logged_count": len(logged_out),
            "logged": logged_out,
            "verified": all_verified,
            "already_logged": False,
            "energy_summary": extract_energy_summary(diary_after),
        }
        if all_mismatches:
            response["verification_mismatches"] = all_mismatches
        return response

    def log_food_entries(
        self,
        entries: list[dict[str, Any]],
        *,
        day: str | None = None,
        meal_group: str | int = "auto",
        time: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Log one or more food servings in a single validated, read-back-verified operation."""
        if not isinstance(entries, list) or not entries or len(entries) > 50:
            raise ValidationError("entries must be a non-empty list of at most 50 items")

        explicit_day = parse_day(day)
        normalized_inputs, needs_now, fingerprint = self._prepare_log_inputs(
            entries,
            explicit_day=explicit_day,
            default_group=parse_meal_group(meal_group, allow_auto=True),
            default_time=parse_time(time),
        )

        with self._op_lock():
            journal_steps: list[dict[str, Any]] | None = None
            target_day: date | None = None
            planned: list[dict[str, Any]] | None = None
            foods_by_id: dict[int, dict[str, Any]] = {}
            op_fingerprint = fingerprint

            if request_id and self.state:
                existing_op = self.state.get_operation(
                    request_id, "log_food_entries", verify_fingerprint=False
                )
                if existing_op is not None:
                    saved_fp = existing_op["fingerprint"]
                    if saved_fp and saved_fp != fingerprint:
                        if saved_fp.startswith("v2:"):
                            raise ValidationError(
                                f"request_id {request_id!r} was already used for a different request payload; "
                                "use a new request_id for a different meal or food"
                            )
                        # Legacy pre-v2 journal record written before input-based fingerprints
                        legacy_match = False
                        try:
                            planned, foods_by_id = self._resolve_planned_entries(normalized_inputs)
                            legacy_fp = self._legacy_v1_log_fingerprint(planned, explicit_day)
                            legacy_match = legacy_fp == saved_fp
                        except (SessionExpiredError, AuthError):
                            raise
                        except Exception:
                            planned = None
                            foods_by_id = {}
                        if not legacy_match:
                            saved_data = existing_op["data"]
                            saved_day = saved_data.get("date") or saved_data.get("day")
                            saved_ids = [
                                item.get("entry_id")
                                for item in (
                                    saved_data.get("logged") or saved_data.get("steps") or []
                                )
                                if isinstance(item, dict) and item.get("entry_id") is not None
                            ]
                            raise ValidationError(
                                f"Legacy request_id {request_id!r} (status={existing_op['status']}, "
                                f"date={saved_day}, entry_ids={saved_ids}) could not be matched to "
                                "this payload; check get_food_log before retrying"
                            ) from None
                        op_fingerprint = saved_fp

                    if existing_op["status"] == "completed":
                        return {**existing_op["data"], "already_logged": True}
                    target_day = date.fromisoformat(existing_op["data"]["day"])
                    journal_steps = existing_op["data"]["steps"]
                    if all(
                        "wire_grams" in jstep
                        and "wire_measure_id" in jstep
                        and "grams_per_wire_unit" in jstep
                        and "grams" in jstep
                        and "food_id" in jstep
                        for jstep in journal_steps
                    ):
                        # Self-contained v2 journal: recover strictly from persisted wire plan + diary
                        # without hitting get_foods or reinterpreting historical rows via current catalog.
                        planned = [
                            {
                                "food_id": jstep["food_id"],
                                "food_name": jstep.get("food_name"),
                                "measure_id": jstep["measure_id"],
                                "measure_name": jstep.get("measure_name"),
                                "grams": float(jstep["grams"]),
                                "wire_measure_id": jstep["wire_measure_id"],
                                "wire_grams": float(jstep["wire_grams"]),
                                "grams_per_wire_unit": float(jstep["grams_per_wire_unit"]),
                                "is_batch_recipe": bool(jstep.get("is_batch_recipe", False)),
                                "translation_id": jstep.get("translation_id", 0),
                                "group_id": jstep.get("group_id"),
                                "wall_time": parse_time(jstep["wall_time"])
                                if jstep.get("wall_time") is not None
                                else None,
                            }
                            for jstep in journal_steps
                        ]
                        foods_by_id = {}
                    else:
                        if planned is None:
                            planned, foods_by_id = self._resolve_planned_entries(normalized_inputs)
                        for step, jstep in zip(planned, journal_steps, strict=True):
                            if jstep.get("wall_time") is not None:
                                step["wall_time"] = parse_time(jstep["wall_time"])
                            if jstep.get("group_id") is not None:
                                step["group_id"] = jstep["group_id"]

            if planned is None:
                planned, foods_by_id = self._resolve_planned_entries(normalized_inputs)

            if target_day is None:
                target_day = explicit_day if explicit_day is not None else self._resolve_day(None)
                if explicit_day is not None and needs_now:
                    self.client.sync_timezone(force=True)

            # Resolve concrete time and meal group BEFORE journaling or sending so recovery
            # compares exact persisted values and never adopts an unrelated meal.
            resolved_now: dtime | None = None
            for step in planned:
                if step["wall_time"] is None:
                    if resolved_now is None:
                        resolved_now = self.client.now().time().replace(microsecond=0)
                    step["wall_time"] = resolved_now
                if step["group_id"] is None:
                    step["group_id"] = meal_group_for_hour(step["wall_time"].hour)

            if request_id and self.state and journal_steps is None:
                journal_steps = [
                    {
                        "status": "pending",
                        "entry_id": None,
                        "baseline_ids": [],
                        "food_id": s["food_id"],
                        "food_name": s["food_name"],
                        "measure_id": s["measure_id"],
                        "measure_name": s["measure_name"],
                        "grams": s["grams"],
                        "wire_measure_id": s["wire_measure_id"],
                        "wire_grams": s["wire_grams"],
                        "grams_per_wire_unit": s["grams_per_wire_unit"],
                        "is_batch_recipe": s["is_batch_recipe"],
                        "translation_id": s["translation_id"],
                        "group_id": s["group_id"],
                        "wall_time": s["wall_time"].isoformat(timespec="seconds"),
                    }
                    for s in planned
                ]
                self.state.save_operation(
                    request_id,
                    "log_food_entries",
                    fingerprint=op_fingerprint,
                    status="in_progress",
                    data={"day": target_day.isoformat(), "steps": journal_steps},
                )

            if journal_steps is not None and any(s["status"] == "sending" for s in journal_steps):
                assert request_id
                self._reconcile_sending_steps(
                    planned=planned,
                    journal_steps=journal_steps,
                    foods_by_id=foods_by_id,
                    target_day=target_day,
                    request_id=request_id,
                    fingerprint=op_fingerprint,
                )

            known_ids: set[int] = set()
            if journal_steps is not None and any(s["status"] == "pending" for s in journal_steps):
                before = self.client.get_diary(target_day)
                known_ids = {
                    e["servingId"]
                    for e in before.get("diary", [])
                    if isinstance(e, dict)
                    and e.get("type") == "Serving"
                    and isinstance(e.get("servingId"), int)
                }

            added_ids: list[int] = []
            for idx, step in enumerate(planned):
                jstep = journal_steps[idx] if journal_steps is not None else None
                if jstep is not None and jstep["status"] == "confirmed" and jstep.get("entry_id"):
                    step["entry_id"] = jstep["entry_id"]
                    added_ids.append(jstep["entry_id"])
                    known_ids.add(jstep["entry_id"])
                    continue

                if jstep is not None and request_id and self.state:
                    jstep["status"] = "sending"
                    jstep["baseline_ids"] = sorted(known_ids)
                    jstep["group_id"] = step["group_id"]
                    jstep["wall_time"] = step["wall_time"].isoformat(timespec="seconds")
                    self.state.save_operation(
                        request_id,
                        "log_food_entries",
                        fingerprint=op_fingerprint,
                        status="in_progress",
                        data={"day": target_day.isoformat(), "steps": journal_steps},
                    )

                res = self._add_serving_row(
                    food_id=step["food_id"],
                    wire_measure_id=step["wire_measure_id"],
                    wire_grams=step["wire_grams"],
                    translation_id=step["translation_id"],
                    target_day=target_day,
                    group_id=step["group_id"],
                    wall_time=step["wall_time"],
                )
                sid = res.get("id")
                if type(sid) is not int or sid <= 0:
                    raise CronometerError(
                        f"Cronometer did not return a valid serving ID for {step['food_name']!r}"
                    )
                step["entry_id"] = sid
                added_ids.append(sid)
                known_ids.add(sid)

                if jstep is not None and request_id and self.state:
                    jstep["entry_id"] = sid
                    jstep["status"] = "confirmed"
                    self.state.save_operation(
                        request_id,
                        "log_food_entries",
                        fingerprint=op_fingerprint,
                        status="in_progress",
                        data={"day": target_day.isoformat(), "steps": journal_steps},
                    )

            response = self._verify_logged_batch(
                planned=planned,
                added_ids=added_ids,
                foods_by_id=foods_by_id,
                target_day=target_day,
            )
            if request_id and self.state and response["verified"]:
                self.state.save_idempotent(
                    request_id, "log_food_entries", response, fingerprint=op_fingerprint
                )
            return response

    def edit_food_entry(
        self,
        entry_id: int,
        *,
        day: str | None = None,
        grams: float | None = None,
        quantity: float | None = None,
        measure_id: int | None = None,
        measure_name: str | None = None,
        time: str | None = None,
        meal_group: str | int | None = None,
        new_day: str | None = None,
    ) -> dict[str, Any]:
        """Edit any existing serving's amount, measure, time, meal group, or date with read-back."""
        if type(entry_id) is not int or entry_id <= 0:
            raise ValidationError("entry_id must be a positive integer")
        if all(
            v is None
            for v in (grams, quantity, measure_id, measure_name, time, meal_group, new_day)
        ):
            raise ValidationError(
                "Provide at least one field to change (grams, quantity, measure_id, time, meal_group, or new_day)"
            )

        with self._op_lock():
            source_day = self._resolve_day(day)
            serving = self.client.find_entry(entry_id, "Serving", source_day)
            updated = deepcopy(serving)
            raw_food = self.client.get_food(int(serving["foodId"]))
            _, _, _, existing_actual_grams = serving_scale_and_display(raw_food, serving)
            expected_grams: float | None = existing_actual_grams
            expected_wire_grams: float | None = (
                float(serving["grams"]) if isinstance(serving.get("grams"), (int, float)) else None
            )
            expected_measure_id: int | None = serving.get("measureId")
            expected_group_id: int | None = (serving.get("order") or 0) >> 16
            expected_time: dtime | None = None

            if (
                grams is not None
                or quantity is not None
                or measure_id is not None
                or measure_name is not None
            ):
                effective_grams = grams
                effective_qty = quantity
                if effective_grams is None and effective_qty is None:
                    if is_batch_recipe(raw_food):
                        raise ValidationError(
                            "Provide `grams` or `quantity` when updating a recipe serving"
                        )
                    effective_grams = float(serving["grams"])
                resolved = resolve_serving_amount(
                    raw_food,
                    grams=effective_grams,
                    quantity=effective_qty,
                    measure_id=measure_id,
                    measure_name=measure_name,
                )
                expected_grams = resolved["grams"]
                wire_measure_id, wire_grams, _, _ = self._wire_serving_fields(
                    raw_food, resolved["measure_id"], resolved["grams"]
                )
                expected_measure_id = wire_measure_id
                expected_wire_grams = wire_grams
                updated["measureId"] = wire_measure_id
                updated["grams"] = wire_grams

            if time is not None:
                expected_time = parse_time(time)
                assert expected_time is not None
                updated["time"] = format_api_time(expected_time)

            if meal_group is not None:
                expected_group_id = parse_meal_group(meal_group, allow_auto=False)
                assert expected_group_id is not None
                updated["order"] = (expected_group_id << 16) | (
                    (updated.get("order") or 1) & 0xFFFF
                )

            dest_day = source_day
            if new_day is not None:
                dest_day = parse_day(new_day)  # type: ignore[assignment]
                updated["day"] = self.client.format_day(dest_day)

            self.client.request_v2("/api/v2/edit_serving", {"serving": updated})

            fresh = self.client.find_entry(entry_id, "Serving", dest_day)
            check = self._verify_serving_row(
                fresh,
                raw_food,
                expected_food_id=int(serving["foodId"]),
                expected_grams=expected_grams,
                expected_measure_id=expected_measure_id,
                expected_group_id=expected_group_id,
                expected_time=expected_time,
                expected_wire_grams=expected_wire_grams,
            )
            result: dict[str, Any] = {
                "entry_id": entry_id,
                "food_id": check["food_id"],
                "date": dest_day.isoformat(),
                "grams": check["grams"],
                "measure_id": check["measure_id"],
                "time": check["time"],
                "meal_group": check["meal_group"],
                "verified": check["verified"],
            }
            if check["mismatches"]:
                result["verification_mismatches"] = check["mismatches"]
            return result

    def remove_food_entries(
        self, entry_ids: list[int | str], *, day: str | None = None
    ) -> dict[str, Any]:
        """Remove one or more food servings on a day, with mandatory read-back verification."""
        if not isinstance(entry_ids, list) or not entry_ids:
            raise ValidationError("entry_ids must be a non-empty list of serving IDs")
        str_ids = [str(eid).strip() for eid in entry_ids if str(eid).strip()]
        if not str_ids:
            raise ValidationError("Provide at least one valid entry_id")

        with self._op_lock():
            target_day = self._resolve_day(day)
            result = self.client.delete_entries(str_ids, target_day, entry_type="Serving")
            return {
                "date": target_day.isoformat(),
                "removed": result.get("removed", []),
                "count": result.get("count", 0),
                "verified": True,
            }

    # Optional cooking scratchpad
    def save_meal_draft(
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
        if not self.state:
            raise ValidationError("Local state storage is disabled")
        if day is not None:
            parse_day(day)
        if local_time is not None:
            parse_time(local_time)
        if meal_group is not None:
            parse_meal_group(meal_group, allow_auto=True)
        draft = self.state.save_draft(
            title,
            items,
            draft_id=draft_id,
            day=day,
            meal_group=meal_group,
            local_time=local_time,
            notes=notes,
        )
        return {"draft": draft}

    def list_meal_drafts(self, *, limit: int = 20) -> dict[str, Any]:
        if not self.state:
            return {"drafts": []}
        return {"drafts": self.state.list_drafts(limit=limit)}

    def delete_meal_draft(self, draft_id: str) -> dict[str, Any]:
        if not self.state:
            raise ValidationError("Local state storage is disabled")
        return {"draft_id": draft_id, "deleted": self.state.delete_draft(draft_id)}
