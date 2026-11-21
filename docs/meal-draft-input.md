# Updating a meal draft and logging selected foods

`update_meal_draft` replaces the **complete item list** at `expected_revision`.
Read `get_meal_review` first and use `meal.items` as the editable structure.
`review.entries` also contains display metadata and must not be copied as input.

| Item field | Meaning |
|---|---|
| `description` | Required nonblank food description |
| `food_id` | Selected food ID; `null` while unresolved |
| `grams` | Positive finite mass in grams; `null` while unknown |
| `preparation` | `raw`, `cooked`, `as_sold`, or `unspecified` |
| `quantity_basis` | `weighed`, `estimated`, or `user_default` |
| `assumption` | Required explanation for an accepted estimate/default; otherwise `null` |
| `selection_id` | Preserve the exact verified selection reference returned by the live tools |

Only `description` is required in the schema. Missing identity/quantity remains a
draft requiring clarification. A resolvable identity alone does not make the food
verified: live foods still need the existing inspected-profile verification.
Do not send display fields such as `food_name`, `source`, `nutrients`, or aliases
such as `name` and `amount`. Nutrients come from the verified food profile.

## Log five foods while leaving another pending

There is no atomic split or selected-index logging tool. `log_meal` logs the
whole ready draft. Use the existing draft workflow carefully:

1. Read the current draft and confirm which foods/quantities were consumed.
   A dated draft can have an empty item list; recover missing foods from the
   conversation or ask the user. Do not claim they were saved or invent them.
2. If deferring foods already in the draft, first create and save a separate
   draft with those foods, assumptions and unresolved questions. Preserve the
   original explicit day, meal group and local time. Read it back successfully
   before removing anything from the original draft. If interrupted, inspect
   both drafts and resume; do not create another copy blindly.
3. Update the original with the complete selected list, its current revision,
   and only the questions relevant to those foods. Resolve those questions;
   set `intent=eaten` only when consumption is established. Preserve the original
   date/time rather than defaulting an old conversation to today.
4. Review both drafts. The pending food must still exist; the selected draft
   must be ready. Call `log_meal` using its latest revision and review token.
5. Inspect operation status. Repeat/recovery uses the same meal operation;
   never create a second draft to bypass an uncertain write. Report exactly
   which foods were logged and which remain pending.

This is a conversational sequence, not a transactional split. An atomic split
may be added later if this workflow proves too fragile. Existing logged drafts
remain immutable; this procedure applies to drafts without logging operations.

## Deploying the schema fix

Pull the merged code, sync locked dependencies and restart the systemd service
using the [setup/update guide](setup-and-operations.md). Then refresh the
ChatGPT connection's tools and use a new chat if it still has the old schema.
Refreshing tools alone does not reload Python code. Confirm `update_meal_draft`
shows the seven item fields before retrying. No database migration is required.

## Recovering from a changed account timezone

The login session can retain an old timezone after the profile changes in
Cronometer. In meals mode, `check_cronometer_connection` now reads the current
profile and labels it `timezone_source=current_profile`. Other modes label
their food-only connection check's timezone `cached_session`; it is not proof
of the current profile zone. New meals and default-day nutrition totals use the
freshly read profile timezone without mutating shared session defaults.

Existing drafts keep their original zone until explicitly acknowledged. When
preflight reports both the draft and current account zones:

1. Establish the intended local calendar date and wall time in the current zone.
   Existing user approval suffices if it covers those exact values. Never assume
   two zone names are interchangeable because their offsets sometimes match.
2. Call `confirm_meal_timezone` with the draft ID, current revision, exact current
   timezone, explicit `day`, `local_time`, and `confirmed=true`. This preserves
   the supplied date/time without converting it and leaves foods, quantities,
   questions, intent and meal group untouched. It does not write to the diary
   or alter the Cronometer profile.
3. Review the returned draft and use its new revision/token when logging. Old
   tokens are invalidated. If the profile changes again, acknowledge the newly
   observed values before retrying; there is no bypass flag.

This applies only before any logging operation exists. For an attempted or
uncertain operation, inspect logging status and follow recovery instructions;
never rebind it or create a duplicate meal to evade the existing operation.
