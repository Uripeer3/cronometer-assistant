"""Review and adopt manual repairs to an existing food; never issue a remote edit."""

import hashlib
import json
from copy import deepcopy
from uuid import uuid4

from .models import ValidationError, number


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def snapshot(raw):
    # Relevant fields only, never a response-level authentication envelope.
    fields = {
        k: raw.get(k)
        for k in (
            "id",
            "name",
            "owner",
            "comments",
            "category",
            "labelType",
            "defaultMeasureId",
            "retired",
        )
    }
    fields["nutrients"] = sorted(
        [{k: n.get(k) for k in ("id", "amount")} for n in raw.get("nutrients", [])],
        key=lambda n: str(n["id"]),
    )
    fields["measures"] = sorted(
        [
            {k: m.get(k) for k in ("id", "name", "type", "amount", "value", "hidden")}
            for m in raw.get("measures", [])
        ],
        key=lambda m: str(m["id"]),
    )
    return fields


class ProductRepairs:
    def __init__(self, products):
        self.products = products
        self.store, self.client = products.store, products.client

    def _owned(self, raw, product):
        if not isinstance(raw, dict) or (
            raw.get("id") != product.get("remote_food_id")
            or raw.get("owner") != self.client.user_id
            or raw.get("comments") != "cronometer-assistant product:" + product["proposal_id"]
            or raw.get("retired") not in (None, False)
        ):
            raise ValidationError(
                "Select the same active private food with its original creation marker"
            )

    def prepare(self, proposal_id, category_id, serving_grams):
        if type(category_id) is not int or category_id < 0:
            raise ValidationError("Select a verified non-negative category ID")
        serving_grams = number(serving_grams, "serving grams", positive=True)
        with self.store.locked():
            product = self.products._product(proposal_id)
            if not product.get("remote_food_id") or not product.get("payload"):
                raise ValidationError(
                    "A known created food is required; reconcile uncertain identity first"
                )
            raw = self.client.get_food(product["remote_food_id"])
            self._owned(raw, product)
            target = deepcopy(product)
            target["category_id"], target["serving_grams"] = category_id, serving_grams
            target["label_type"] = (
                "EU"
                if target["label"]["carbohydrate_basis"] == "excludes_fiber"
                else "AMERICAN_2016"
            )
            target["payload"] = self.products._payload(target)
            observed = snapshot(raw)
            repair = {
                "repair_id": uuid4().hex,
                "proposal_id": proposal_id,
                "state": "reviewed_snapshot",
                "product_fingerprint": fingerprint(product),
                "observed": observed,
                "observed_fingerprint": fingerprint(observed),
                "target": target,
                "observed_matches_target": self.products._matches(raw, target),
            }
            self.store.put("product_repairs", repair["repair_id"], repair)
        return self.products.response(
            repair_id=repair["repair_id"],
            proposal_id=proposal_id,
            observed={
                k: observed[k]
                for k in ("id", "name", "category", "labelType", "defaultMeasureId", "measures")
            },
            target={
                "category_id": category_id,
                "serving_grams": serving_grams,
                "label_type": target["label_type"],
            },
            observed_matches_target=repair["observed_matches_target"],
            next_step="Correct the same food in Cronometer if needed, then prepare a fresh snapshot. "
            "Adopt only after reviewing a matching snapshot.",
            remote_food_changed=False,
        )

    def adopt(self, repair_id, reviewed):
        if reviewed is not True:
            raise ValidationError("Review the matching manual repair before adopting it locally")
        with self.store.locked():
            repair = self.store.get("product_repairs", repair_id)
            if repair is None:
                raise ValidationError("Prepare a repair review first")
            product = self.products._product(repair["proposal_id"])
            if repair["state"] == "adopted":
                return {**self.products._verify(product), "already_adopted": True}
            if fingerprint(product) != repair["product_fingerprint"]:
                raise ValidationError("Product changed; prepare a fresh repair snapshot")
            raw = self.client.get_food(product["remote_food_id"])
            self._owned(raw, product)
            if fingerprint(snapshot(raw)) != repair["observed_fingerprint"]:
                raise ValidationError("Remote food changed; prepare a fresh repair snapshot")
            target = repair["target"]
            if not self.products._matches(raw, target):
                raise ValidationError(
                    "Remote nutrients, category, label or Weight measures do not match"
                )
            target["state"] = "created"
            repair["state"] = "adopted"
            self.store.put_many(
                [
                    ("product_proposals", product["proposal_id"], target),
                    ("product_repairs", repair_id, repair),
                ]
            )
        return self.products.response(
            target, created=True, adopted=True, barcode_linked=False, remote_food_changed=False
        )
