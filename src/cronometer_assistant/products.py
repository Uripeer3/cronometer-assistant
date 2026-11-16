"""Reviewed private-food creation with a durable, account-bound operation journal."""

import hashlib
import json
import math
import re
from copy import deepcopy

from .evidence import EvidenceArchive
from .models import ValidationError, normalized, number, text
from .nutrition import normalize_label
from .store import Store

NUTRIENT_IDS = {
    "energy": 208,
    "protein": 203,
    "fat": 204,
    "carbs": 205,
    "fiber": 291,
    "sugars": 269,
    "sugar_alcohol": 10007,
    "sodium": 307,
    "saturated_fat": 606,
    "trans_fat": 605,
    "cholesterol": 601,
}


class ProductService:
    def __init__(self, store: Store, client, *, evidence_inbox=None):
        self.store, self.client = store, client
        self.evidence = EvidenceArchive(store, store.path.parent / "evidence", inbox=evidence_inbox)
        account = client.user_id
        with store.locked():
            bound = store.get("product_settings", "account")
            if bound and bound["user_id"] != account:
                raise ValidationError("Use a separate product database for this Cronometer account")
            store.put("product_settings", "account", {"user_id": account})

    @staticmethod
    def response(product=None, **data):
        # Internal request payloads are never tool results.
        public = (
            {}
            if product is None
            else {"product": {k: v for k, v in product.items() if k != "payload"}}
        )
        return {"mode": "cronometer_products", "real_diary_changed": False, **public, **data}

    def _product(self, proposal_id):
        value = self.store.get("product_proposals", proposal_id)
        if value is None:
            raise ValidationError("Unknown product proposal; prepare and review it first")
        return value

    def _check_unresolved(self, product):
        for other in self.store.values("product_proposals"):
            if other["proposal_id"] == product["proposal_id"]:
                continue
            same_name = normalized(other["label"]["name"]) == normalized(product["label"]["name"])
            same_barcode = product["barcode"] and product["barcode"] == other["barcode"]
            if (same_name or same_barcode) and other["state"] in {
                "sending",
                "uncertain",
                "needs_verification",
                "needs_reconciliation",
            }:
                raise ValidationError(
                    "Resolve the existing uncertain creation first: " + other["proposal_id"]
                )

    def prepare(
        self,
        name: str,
        basis_amount: float,
        basis_unit: str,
        nutrients: dict,
        carbohydrate_basis: str = "unspecified",
        serving_grams: float = 100,
        barcode: str | None = None,
        density_g_per_ml: float | None = None,
        category_id: int | None = None,
    ):
        if category_id is not None and (type(category_id) is not int or category_id < 0):
            raise ValidationError("category_id must be a non-negative Cronometer category ID")
        if barcode is not None:
            barcode = text(barcode, "barcode", 14)
            if not re.fullmatch(r"(?:[0-9]{8}|[0-9]{12,14})", barcode):
                raise ValidationError("Barcode must contain 8, 12, 13 or 14 digits")
        label = normalize_label(
            name,
            basis_amount,
            basis_unit,
            nutrients,
            density_g_per_ml,
            carbohydrate_basis=carbohydrate_basis,
        )
        proposal = {
            "label": label,
            "serving_grams": number(serving_grams, "serving grams", positive=True),
            "barcode": barcode,
            "category_id": category_id,
            "label_type": ("EU" if carbohydrate_basis == "excludes_fiber" else "AMERICAN_2016"),
        }
        identity = {
            "name": normalized(label["name"]),
            "serving_grams": proposal["serving_grams"],
            "barcode": barcode,
            "nutrients": {
                k: v for k, v in label["nutrients_per_100g"].items() if v["amount"] is not None
            },
        }
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        with self.store.locked():
            self._check_unresolved({"proposal_id": digest, **proposal})
            existing = self.store.get("product_proposals", digest)
            if existing is None:
                existing = {"proposal_id": digest, "state": "prepared", **proposal}
                self.store.put("product_proposals", digest, existing)
            elif existing["state"] == "prepared":
                # Metadata may be completed before the first attempt. It must not create a
                # second food identity or rewrite an already-sent request.
                if category_id is not None:
                    existing["category_id"] = category_id
                existing.setdefault(
                    "label_type",
                    "EU"
                    if existing["label"]["carbohydrate_basis"] == "excludes_fiber"
                    else "AMERICAN_2016",
                )
                self.store.put("product_proposals", digest, existing)
        return self.response(existing, review_required=True, remote_food_changed=False)

    def review(self, proposal_id: str):
        return self.response(self._product(proposal_id), remote_food_changed=False)

    def _payload(self, product):
        if product.get("category_id") is None:
            raise ValidationError(
                "Select a verified category_id before creation; use 0 only for an "
                "explicitly uncategorized food"
            )
        definitions = self.client.get_nutrient_definitions()
        rows = []
        for key, nutrient in product["label"]["nutrients_per_100g"].items():
            if nutrient["amount"] is None:
                continue
            nutrient_id = NUTRIENT_IDS.get(key)
            if nutrient_id is None and re.fullmatch(r"cronometer_[1-9][0-9]*", key):
                nutrient_id = int(key.split("_", 1)[1])
            definition = definitions.get(nutrient_id, {})
            unit = {"µg": "ug", "μg": "ug"}.get(definition.get("unit"), definition.get("unit"))
            if not definition.get("name") or unit != nutrient["unit"]:
                raise ValidationError(f"No verified nutrient ID/unit mapping for {key}")
            rows.append({"id": nutrient_id, "amount": round(nutrient["amount"], 4)})
        if not rows:
            raise ValidationError("At least one known, mapped nutrient is required")
        return {
            "data": {
                "id": 0,
                "name": product["label"]["name"],
                "category": product["category_id"],
                "owner": None,
                "retired": None,
                "source": None,
                "defaultMeasureId": 0,
                "alternateId": None,
                "comments": "cronometer-assistant product:" + product["proposal_id"],
                "measures": [
                    {
                        "id": 0,
                        "name": "serving",
                        "value": product["serving_grams"],
                        "amount": 1.0,
                        "type": "Weight",
                    },
                    {"id": 0, "name": "g", "value": 1.0, "amount": 1.0, "type": "Weight"},
                ],
                # Raw nutrients already use total carbs including fiber.
                "labelType": product["label_type"],
                "nutrients": rows,
                "properties": {},
                "foodTags": [],
            },
            "config": {"call_version": 1},
        }

    def _matches(self, raw, product):
        if not isinstance(raw, dict):
            return False
        payload = product["payload"]["data"]
        if raw.get("name") != payload["name"] or raw.get("comments") != payload["comments"]:
            return False
        if raw.get("owner") != self.client.user_id:
            return False
        if raw.get("category") != payload["category"]:
            return False
        if raw.get("labelType") != payload["labelType"]:
            return False
        amounts = {n.get("id"): n.get("amount") for n in raw.get("nutrients", [])}
        expected_ids = {n["id"] for n in payload["nutrients"]}
        if any(nid > 0 and nid not in expected_ids for nid in amounts if type(nid) is int):
            return False  # Unlisted positive nutrient rows must not become invented zeroes.
        for row in payload["nutrients"]:
            amount = amounts.get(row["id"])
            if type(amount) not in (int, float) or not math.isclose(
                amount, row["amount"], rel_tol=1e-6, abs_tol=0.0001
            ):
                return False
        serving_matches = any(
            m.get("id")
            and m.get("id") == raw.get("defaultMeasureId")
            and m.get("name") == "serving"
            and m.get("type") == "Weight"
            and not m.get("hidden", False)
            and type(m.get("amount")) in (int, float)
            and math.isclose(m["amount"], 1.0, abs_tol=1e-6)
            and type(m.get("value")) in (int, float)
            and math.isclose(m["value"], product["serving_grams"], abs_tol=1e-6)
            for m in raw.get("measures", [])
        )
        grams_match = any(
            m.get("id")
            and m.get("name") == "g"
            and m.get("type") == "Weight"
            and not m.get("hidden", False)
            and type(m.get("amount")) in (int, float)
            and math.isclose(m["amount"], 1.0, abs_tol=1e-6)
            and type(m.get("value")) in (int, float)
            and math.isclose(m["value"], 1.0, abs_tol=1e-6)
            for m in raw.get("measures", [])
        )
        return serving_matches and grams_match

    def _verify(self, product, *, remote_food_changed=False):
        try:
            raw = self.client.get_food(product["remote_food_id"])
        except Exception:
            product["state"] = "needs_verification"
        else:
            try:
                matches = self._matches(raw, product)
            except Exception:
                matches = False
            product["state"] = "created" if matches else "needs_reconciliation"
        self.store.put("product_proposals", product["proposal_id"], product)
        return self.response(
            product,
            created=product["state"] == "created",
            retry_blocked=product["state"] == "needs_reconciliation",
            barcode_linked=False,
            remote_food_changed=remote_food_changed,
        )

    def create(self, proposal_id: str, reviewed: bool):
        if reviewed is not True:
            raise ValidationError("Review the prepared product before creating it in Cronometer")
        with self.store.locked():
            product = self._product(proposal_id)
            self._check_unresolved(product)
            if product["state"] == "created":
                # Also recheck legacy records: a positive Atomic value is not a
                # usable weight, and external edits must not get a stale success.
                return {**self._verify(product), "already_created": True}
            if product["state"] in {"sending", "uncertain", "needs_reconciliation"}:
                return self.response(
                    product, created=False, retry_blocked=True, remote_food_changed="unknown"
                )
            if product.get("remote_food_id"):
                return self._verify(product)
            # Complete all mapping/authenticated reads before the durable attempt marker.
            product["payload"] = self._payload(product)
            product["state"] = "sending"
            self.store.put("product_proposals", proposal_id, product)
            try:
                # Upstream _request mutates its argument to insert auth. Isolate that copy:
                # no session token may reach SQLite, tool results, or stored proposals.
                response = self.client._request("/api/v2/add_food", deepcopy(product["payload"]))
                food_id = response.get("id")
                if type(food_id) is not int or food_id <= 0:
                    raise ValueError
            except Exception:
                product["state"] = "uncertain"
                self.store.put("product_proposals", proposal_id, product)
                return self.response(
                    product, created=False, retry_blocked=True, remote_food_changed="unknown"
                )
            product["remote_food_id"] = food_id
            product["state"] = "needs_verification"
            self.store.put("product_proposals", proposal_id, product)
            return self._verify(product, remote_food_changed=True)

    def reconcile(self, proposal_id: str, food_id: int):
        if type(food_id) is not int or food_id <= 0:
            raise ValidationError("Provide a positive remote food ID")
        with self.store.locked():
            product = self._product(proposal_id)
            if product["state"] not in {
                "sending",
                "uncertain",
                "needs_verification",
                "needs_reconciliation",
            }:
                raise ValidationError("This product does not need reconciliation")
            raw = self.client.get_food(food_id)
            if not self._matches(raw, product):
                raise ValidationError(
                    "Food does not match account, creation marker, nutrients and serving"
                )
            product["remote_food_id"], product["state"] = food_id, "created"
            self.store.put("product_proposals", proposal_id, product)
            return self.response(
                product, created=True, remote_food_changed=False, barcode_linked=False
            )

    def public_submission_checklist(self, proposal_id: str):
        return self.response(
            self._product(proposal_id),
            submitted=False,
            public=False,
            requirements=[
                "Verified private food",
                "Barcode",
                "Package-front photo",
                "Nutrition-label photo",
            ],
            next_step="Use Cronometer's Publish Food flow; automated submission is not implemented",
        )

    def attach_evidence_reference(self, proposal_id, role, provider, reference):
        self._product(proposal_id)
        row = self.evidence.reference(proposal_id, role, provider, reference)
        return self.response(evidence=row, remote_food_changed=False)

    def import_evidence(self, proposal_id, role, inbox_name):
        self._product(proposal_id)
        row = self.evidence.import_file(proposal_id, role, inbox_name)
        return self.response(evidence=row, remote_food_changed=False)

    def review_evidence(self, proposal_id):
        product = self._product(proposal_id)
        return self.response(
            proposal_id=proposal_id,
            remote_food_id=product.get("remote_food_id"),
            evidence=self.evidence.review(proposal_id),
            remote_food_changed=False,
        )
