"""Local publication choices and operator-reported outcomes; no upload or submit API."""

from datetime import UTC, datetime

from .models import ValidationError, text


def validate_barcode(value):
    value = text(value, "barcode", 14)
    if not value.isascii() or not value.isdigit() or len(value) not in {8, 12, 13, 14}:
        raise ValidationError("Barcode must contain 8, 12, 13 or 14 digits")
    expected = (-sum(int(v) * (3 if i % 2 == 0 else 1) for i, v in enumerate(value[-2::-1]))) % 10
    if int(value[-1]) != expected:
        raise ValidationError("Barcode checksum failed; recheck the package digits")
    return value


class SubmissionJournal:
    def __init__(self, store):
        self.store = store

    def get(self, proposal_id):
        return self.store.get("product_submissions", proposal_id) or {
            "proposal_id": proposal_id,
            "revision": 0,
            "choice": "not_asked",
            "status": "not_submitted",
            "events": [],
        }

    def update(
        self,
        proposal_id,
        expected_revision,
        *,
        choice=None,
        status=None,
        receipt=None,
        context=None,
    ):
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValidationError("Use the current non-negative submission revision")
        if choice is not None and choice not in {"requested", "declined", "deferred"}:
            raise ValidationError("Choice must be requested, declined or deferred")
        if status is not None and status not in {
            "submitted_pending",
            "accepted",
            "rejected",
            "unknown",
        }:
            raise ValidationError("Unknown manual submission outcome")
        if (choice is None) == (status is None):
            raise ValidationError("Record one choice or one outcome per operation")
        if status:
            receipt = text(receipt, "operator receipt or observation", 500)
        with self.store.locked():
            value = self.get(proposal_id)
            if value["revision"] != expected_revision:
                raise ValidationError("Submission changed; retrieve its current revision")
            if status and value["choice"] != "requested":
                raise ValidationError("Record the user's submission request first")
            if status in {"accepted", "rejected"} and value["status"] not in {
                "submitted_pending",
                "unknown",
            }:
                raise ValidationError("Record the initial submission outcome before curation")
            if value["status"] in {"accepted", "rejected"} and status is not None:
                raise ValidationError("Curation already has a terminal recorded outcome")
            event = {"at": datetime.now(UTC).isoformat()}
            if choice:
                value["choice"] = choice
                event["choice"] = choice
                # Declining after submission cannot retract a review already sent.
            if status:
                value["status"] = status
                value["outcome_source"] = "operator_reported"
                event.update(
                    status=status, receipt=receipt, source="operator_reported", context=context
                )
            value["revision"] += 1
            value["events"].append(event)
            self.store.put("product_submissions", proposal_id, value)
        return value
