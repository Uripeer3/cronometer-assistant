# Complete PR map and acceptance tests

Updated 17 November 2026. PRs #1–#6 provide the merged baseline: conversational
demo drafts, tunnel setup, protected local authentication, real-food inspection,
and reviewed private-food creation with serving/category/label-display fixes.

PRs #7–#14 below are implemented and open, not merged or deployed. Automated
checks do not replace the live acceptance tests in the table. PRs **15–35 are
planned slots, not existing GitHub PRs or reserved issue numbers**. Split or
renumber them if implementation or testing reveals a better boundary. Implementation
lives in each respective PR branch; updating PR #8's documentation enables no new tools.

## Implemented: review and test before merging

| PR | What was done | What needs testing | Simple test cases / expected result |
|---|---|---|---|
| [#7](https://github.com/Uripeer3/cronometer-assistant/pull/7) | Photo-retention and optional-submission policy | Honest photo/submission claims; respect an existing choice | Give label photos and decline submission: no public submission. Already request submission: collect missing information without asking for approval again. This PR is policy, not image-transfer code. |
| [#8](https://github.com/Uripeer3/cronometer-assistant/pull/8) | Complete PR map, dependencies and per-PR acceptance tests | Agreement between documentation, actual tools and dependency order | Follow one documented test path; every named tool exists in its stated mode. Future capabilities are clearly marked as future. |
| [#9](https://github.com/Uripeer3/cronometer-assistant/pull/9) | Private photo archive, references, hashes and deduplication | Front/label roles, actual retained bytes, restart recovery, invalid imports | Place front and label files in the configured inbox; import twice and restart: both roles remain, with no duplicate bytes. A reference alone is not reported as locally archived. Corrupt a test copy: review reports failure. |
| [#10](https://github.com/Uripeer3/cronometer-assistant/pull/10) | Persisted submission choice, barcode checksum and manual-result journal | Ask once; resume missing information; distinguish pending, accepted and unknown | Request submission before providing a barcode, then provide it: continue without re-asking. Decline: photos remain private. Invalid check digit: ask to check the digits. Interrupted submission: remain unknown, not published. |
| [#11](https://github.com/Uripeer3/cronometer-assistant/pull/11) | Review/adopt manual repairs to the same private food | Ownership, unchanged nutrients, serving/category/label checks, stale snapshots | Correct the existing cereal in Cronometer, review and adopt: same food ID, no new food. Change it again between review and adopt: adoption stops. This tool updates our journal, not Cronometer. |
| [#12](https://github.com/Uripeer3/cronometer-assistant/pull/12) | Persistent drafts using reviewed real food data; aliases and account timezone | Hebrew/English dialogue, quantity/preparation questions, resume, mode boundaries | Say “150 g rice”: ask dry or cooked. Confirm cooked and later eaten: save a complete local draft, with no diary entry. Restart and reuse “my usual yogurt”: keep the verified match. Discard a local draft: it disappears, without deleting diary data. |
| [#13](https://github.com/Uripeer3/cronometer-assistant/pull/13) | Consistent SQLite/evidence backup and isolated restore | Recover drafts, aliases, choices and imported photos; reject damaged backups | Back up, restore to a new directory, and compare a draft and photo hash. An existing destination must not be overwritten. Missing/corrupted evidence fails verification. Sessions, keys, inbox files and legacy manual photo folders are excluded. |
| [#14](https://github.com/Uripeer3/cronometer-assistant/pull/14) | Launcher inbox wiring, bounded download recovery and consolidated documentation | Correct mode/inbox forwarding, startup, retries and credential scope | Start product mode with an inbox path containing spaces: imports use that inbox. Draft-only mode rejects the photo-inbox option. Simulated download timeout retries within its bound; CI release credentials never reach asset downloads. |

## Planned: complete the product workflow

| Planned PR | What to implement | What needs testing | Simple test cases / expected result |
|---|---|---|---|
| 15 | Food-data fidelity and verified source/category/measure lookup | Source identity, raw/cooked state, units, unknown versus zero, grams and label conventions; account timezone | Compare an exact packaged product and a generic food with the app. For the cereal's 42 g serving, explain label carbs about 13 g versus canonical total carbs including fiber, and net carbs about 8.8 g. Preview 50 g. Report a timezone mismatch without silently changing settings. |
| 16 | Automatic attachment delivery from the assistant host to the private archive | No manual inbox-copy step; correct product/version; interrupted transfer and duplicate handling | Send front and label photos in chat: both reach the product archive and survive restart. Send one again: no duplicate. A failed transfer is reported, never called archived. Do not fetch arbitrary model-supplied URLs or paths. |
| 17 | Native barcode linkage and lookup/reuse | Linkage to the existing private food, scanner lookup, exact variant and duplicate prevention | Attach a valid barcode to the existing cereal; scan it in the app and find the same food. Repeating the request creates no duplicate. A barcode for another variant must trigger clarification. Local checksum validation alone is insufficient. |
| 18 | Verified Cronometer photo upload/attachment integration | Actual native interface, correct image roles, private-photo persistence if supported, upload receipts and partial failure | Upload front and label to the intended food/submission and verify both. Cancel publication: our private originals remain. Establish whether Cronometer itself retains private attachments; if unsupported, keep one product with our archive and a documented submission upload step. |
| 19 | Authorized public-food submission | Existing private food, exact reviewed evidence, requested/declined choice and actual receipt | “Submit this product” sends that product once and reports submitted/pending only after a receipt. “Keep it private” sends nothing. Missing barcode/photo prompts only for that missing item. Upload success alone is not submission. |
| 20 | Submission status and interrupted-operation reconciliation | Pending/accepted/rejected/unknown outcomes, evidence version and safe retry decisions | Lose the response after submit: check status before any repeat. Distinguish “not sent” from “sent, awaiting review.” Acceptance requires observed curation evidence; rejection retains the private food/photos and does not automatically resubmit. |
| 21 | Guarded remote edits to an existing private food | Field-level review, ownership, stable food/measure IDs, external edits, read-back and historical diary impact | Fix category or serving grams in place: same food ID and correct 50 g conversion. An external edit blocks a stale update. Changed formulation creates a new reviewed version. A timeout triggers verification, not a blind second edit. |
| 22 | Custom-food retirement/deletion and local cleanup rules | Exact target, diary/recipe references, supported retirement versus deletion, retained history/evidence | Retire a disposable custom food and verify it is no longer offered for new use. For a food used in an old meal/recipe, show the impact and preserve historical nutrition. Do not delete a shared database food or unrelated photos/aliases. |

## Planned: real diary logging and recovery

| Planned PR | What to implement | What needs testing | Simple test cases / expected result |
|---|---|---|---|
| 23 | Read actual diary entries and meal-group metadata | Exact dates, account timezone, meal groups, entry IDs and incomplete nutrient coverage | Read a user-selected day and compare food, quantity and group with the app. Check a meal near midnight. Distinguish an empty day from a failed read. Never infer a write succeeded merely from matching totals. |
| 24 | Opt-in verified live meal commit | Consumption intent, reviewed revision, exact grams/date/group, durable operation ID and read-back | On a user-selected test day, log one reviewed 100 g food and inspect the resulting entry. Say “log it” again for the same event: no duplicate. Preparing a meal causes no entry. A separate second identical meal remains valid. |
| 25 | Partial-write and crash reconciliation | Recovery after each side effect, returned IDs, baseline entries and duplicate protection | In a three-food meal, interrupt after the first add: retain its progress and reconcile before continuing. If an add timed out after succeeding, find its exact entry rather than adding it again. A pre-existing identical entry cannot be claimed. |
| 26 | Correct an already logged meal | Quantity/date/group edits, stable event association, additional portion versus revised total, conflict detection | “Actually 200 g, not 150 g” changes that meal instead of adding another. “I ate another 50 g” is handled as an additional portion after clarifying if needed. An app-side edit causes a conflict instead of being overwritten. |
| 27 | Undo/delete logged meals | Exact operation-owned entry IDs, partial deletion, read-back and repeated undo | Undo a two-food assistant-logged meal: only its two entries disappear. A manually added identical food remains. Repeating undo is harmless. An interrupted deletion resumes by inspecting the remaining IDs, never by wiping the day. |

## Planned: recipes, conversation and assistant compatibility

| Planned PR | What to implement | What needs testing | Simple test cases / expected result |
|---|---|---|---|
| 28 | Persistent recipe versions and reusable batches | Ingredient food IDs, amounts, assumptions, original nutrient snapshots and historical stability; native-recipe feasibility | Save a rice-and-oil recipe, then change its oil quantity: create version 2; version 1 and meals using it remain unchanged. Compare native recipe behavior before choosing native storage or structured local ingredients. |
| 29 | Cooked yield, portions and consumption of recipes | Finished batch weight, fractions/servings, preparation versus consumption, discarded liquid and estimates | A homogeneous 1,000 g finished batch with 2,000 kcal gives a 250 g portion of 500 kcal. “I ate half” logs only half. If yield is unknown, accept a disclosed fraction estimate instead of assuming raw weight equals cooked weight. |
| 30 | Refine follow-up priorities, usual products and correction scope | Minimal questions, relevant identity/quantity gaps, user-established defaults, changed labels and nutrient priorities | Reuse the usual yogurt and ask only quantity. Ask dry/cooked for rice and about meaningful oil. For an unreadable critical label field, request a crop, not all photos again. Apply “this meal only” without changing the saved default. |
| 31 | MCP discovery/session compatibility and actionable diagnostics | Modern discovery and fallback, initialization, sequential calls, schema refresh and scrubbed errors | Connect/refresh and call a tool without the misleading validation dump. Distinguish one unsupported discovery method from real task-tool failures. An expired session requests local login without exposing credentials. Do not blindly upgrade the pinned SDK/client. |
| 32 | Phone voice acceptance and fallback decision | Actual mobile tool access, Hebrew/English switching, photos, interruptions and approval taps | Speak a short meal, answer one follow-up, send a label and resume after an interruption. Measure time/questions/taps. Run the early smoke test with #12; complete the full logging test after #24–#31. If the host cannot support it, evaluate another host or a small voice UI. |

## Planned: daily operation and completion

| Planned PR | What to implement | What needs testing | Simple test cases / expected result |
|---|---|---|---|
| 33 | Always-on authenticated deployment and supervised runtime | Reachability while laptop sleeps, persistent data, restart/health checks and protected access | Put the laptop to sleep and use the chosen phone host successfully. Restart the service: drafts/journals/photos remain. An unauthorized client cannot access tools. Choose hosting only after phone compatibility is established. |
| 34 | Operational recovery, migrations and release process | Session expiry, privacy-preserving diagnostics, protected backup location/retention, schema upgrades, restore and rollback | Expire a session: guide local reauthentication without logging credentials. Restore a selected backup and roll back a deployment without losing journals or replaying uncertain writes. Verify credentials remain outside data bundles. |
| 35 | End-to-end pilot and release acceptance | Several days of real meals, all agreed flows, reconciliation with the app and acceptable friction | Exercise generic food, exact product, missing product/photos, submission decline/request, recipe portion, correction, undo, restart and mobile voice. Compare entries/totals with Cronometer, record problems, and resolve them before calling the system ready for daily use. |

## Dependencies and merge process

Review #7–#14 in order. They currently form a stack: #7 targets main and each
subsequent PR targets the previous branch. Merge a reviewed parent, retarget/rebase
its child onto the resulting main revision, and rerun CI before merging the child.
A squash merge requires rebasing the remaining stack; do not assume a changed
base branch alone removes already-merged commits from its diff.

Future numbering is a suggested review sequence, not a requirement that every
branch depend on every earlier one. Source/serving fidelity (#15) gates arithmetic
and live diary writes. Attachment delivery, barcode and photo integration
(#16–#18) build on the product/evidence stack; submission (#19–#20) depends on
verified evidence and its actual interface. Remote edits/deletion require a
verified interface and historical-impact checks. Diary commit depends on #23;
correction and undo depend on the write journal. Recipe consumption reuses the
same logging/recovery path. Perform the phone smoke test early; hosting waits for
its result. Product work and diary work can proceed on separate branches after
the shared foundations are reviewed.

For each PR, first run focused synthetic tests and the existing suite/CI. Then
perform the stated live acceptance case against an explicitly selected product
or diary test date. Record observed IDs/outcomes privately. Actual writes and
deletions need their normal scoped user request; planning a test is not its execution.
Do not merge or deploy unreviewed branches as part of this map update.

## Completion rules

- One product/version links its label, Cronometer ID, category, serving, barcode,
  original photos and submission choice. Do not rebuild it just to submit it.
- Retain original photos privately regardless of submission choice. Distinguish
  host references, archived bytes, native attachments, submission and acceptance.
- Unavailable private-API capabilities require an explicit supported/manual
  fallback. A checklist or operator report must not masquerade as API success.
- Unknown nutrients remain unknown. A detailed database source never overrides
  the wrong brand, variant or preparation. Record accepted estimates explicitly.
- Preparing is not eating. An uncertain side effect is not permission to retry.
  Corrections and deletion target recorded IDs and preserve history appropriately.
- A green test suite is necessary but insufficient. Completion includes the
  app-side result, restart/recovery, phone experience and acceptable effort.

Related guides: [implementation plan](implementation-plan.md),
[photo evidence](https://github.com/Uripeer3/cronometer-assistant/blob/codex/submission-journal/docs/product-evidence.md),
[manual repairs](https://github.com/Uripeer3/cronometer-assistant/blob/codex/product-repair-review/docs/product-repairs.md),
[real-food drafts](https://github.com/Uripeer3/cronometer-assistant/blob/codex/live-meal-drafts/docs/live-drafts.md),
[backup/restore](https://github.com/Uripeer3/cronometer-assistant/blob/codex/runtime-backups/docs/backups.md).
