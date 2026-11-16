# Implementation review and private-food creation

Reviewed 16 November 2026. All examples and automated fixtures are synthetic.
The initial change used mocked writes. Later live UI review exposed the usability gaps recorded below.

## Findings and fixes

| Finding | Consequence | Change |
|---|---|---|
| Labels had no carbohydrate convention | Fiber could be subtracted twice downstream | Require includes_fiber or excludes_fiber when a known carb amount is supplied; preserve the label value and normalize total carbs |
| Sugar alcohols were not canonical fields | Missing or incorrect net-carb inputs | Preserve sugar_alcohol in grams; do not manufacture a net-carb value or overwrite account preferences |
| IU and unclassified nutrient rows | Valid foods could fail conversion, or internal computed rows could be treated as nutrients | Support IU without mass conversion; exclude computed/unknown-unit rows from the catalog model and flag classification in raw inspection |
| Repeated label saves generated new IDs | Duplicate local products | Content-based IDs for identical normalized local products |
| Creation can time out after a side effect | Blind retries create duplicate remote foods | Durable sending marker, returned-ID verification, and explicit marker-based reconciliation |
| Upstream request helper mutates payload with auth | Storing the request object after sending could retain a token | Send a deep copy; never return internal payloads; regression tests simulate mutation |
| Runtime key reader silently truncated oversized input | An invalid partial key could be used | Reject an oversized saved key without echoing it |

Existing demo write reconciliation remains conservative. Ambiguous operations
stay blocked; there is no automatic claim of exactly-once delivery.

## Labels and carbohydrate semantics

The normal form uses total carbohydrates including fiber and polyols. For a label
excluding fiber, add the known fiber amount once. Keep raw per-100-g label values,
the selected convention and conversion notes in the proposal. Missing fiber blocks
conversion of a known available-carbohydrate amount. Do not assume a missing
polyol value is zero. The supported conventions both include polyols; a marketing
net-carb number is not an acceptable substitute for the label carbohydrate row.

Use clear label layout/explicit statements to identify the convention. Do not
ask the user technical terminology when the image already establishes it.
Ask for a targeted clarification if it does not. Preserve labelled energy; do
not reconstruct it from macros as an exact equality.

IU is nutrient-specific. It must never be silently converted to micrograms.
A numeric amount with no known definition/unit remains unclassified in inspection
and is not used for nutrient arithmetic. Catalog source prefixes are mapped only
when an explicit verified source_map is supplied.

See Cronometer's [label guidance](https://support.cronometer.com/hc/en-us/articles/360018869171-Reading-Nutrition-Labels)
and [carbohydrate settings](https://support.cronometer.com/hc/en-us/articles/360060130052-Tracking-Carbohydrates).

## Explicit product mode

The existing demo and read-only modes retain their capability boundaries.
To try the NEW private-food creation capability after updating to this code:

1. Stop the existing launcher. Keep the same private local Cronometer session.
2. Launch with `--mode products` (and `--save-key` if storing a runtime key).
3. Refresh the ChatGPT plugin's metadata, then start a new test conversation.
4. Search for the exact brand and variant before preparing a product.
5. Call prepare_cronometer_product with label fields and serving grams.
6. Review get_product_proposal; create only when the user asks to save that exact proposal.
7. Treat the food as created only when created=true after read-back.

The launcher selects serve-products and data/products.sqlite. The product journal
is bound to the authenticated account. A different account needs another database.
Read-only mode still exposes only its three original tools; product mode adds:

- prepare_cronometer_product
- get_product_proposal
- create_verified_cronometer_food
- reconcile_created_food
- prepare_public_food_submission

The HTTP boundary permits add_food only when product mode was explicitly enabled,
and only with a new-food ID of zero. Existing-food edits, recipes, diary writes,
public submission and all v3 routes remain blocked. Product creation does not log
consumption. New formulations create a separate proposal/food; the service does
not edit a shared food whose changes could alter earlier diary totals.

## Recovery and limits

A content fingerprint deduplicates identical proposals in the same account/store.
A durable sending state is written before the remote request. A timeout or crash
blocks another add. To recover, find the food and reconcile its exact ID against
the private creation marker, owner, name, nutrients and serving. A known returned
ID allows another read-back attempt without another add. Do not evade a blocked
attempt by making a fresh proposal.

Read-back rejects unexpected positive nutrient rows, including zeroes inserted
for unlisted nutrients, rather than silently claiming the label was preserved.
This strict behavior and marker/owner/measure preservation need a controlled live
creation test. Automated tests use the actual pinned client with mocked HTTP.

Barcode is currently local proposal metadata. The selected client does not expose
a verified barcode-creation parameter, so barcode_linked=false is explicit. Do not
promise mobile scan lookup from this change.

prepare_public_food_submission only returns a checklist and manual next step.
It does not upload photos, submit a food, or make it public. Cronometer's curation
team controls approval. Follow the separate roadmap gate for a verified submission
interface; use the documented manual flow when needed.

Older already-saved demo foods are not rewritten automatically. New proposals
include label-convention metadata; recreate old pending proposals through the
proposal tool instead of manually adding fields to an old object. The new optional
tool parameter also requires refreshing ChatGPT's cached schemas before testing.

A changed proposal cannot evade an outstanding uncertain creation of the same
named product/barcode. Canonical nutrient/serving identity deduplicates equivalent
label conventions; original label fields, units, basis and density are retained
separately for audit. Creating a new formulation must wait until any uncertain
previous attempt has been resolved.

The laptop working folder was a source projection rather than a normal Git
checkout. Once the host reconnects, establish a proper checkout and preserve
ignored databases, session and runtime-key files when synchronizing. Do not
assume git pull works in the old folder. GitHub and CI remain the source of
reviewed code while the host is unavailable.

## Follow-up: weight measures, category and label display

Live UI review found that a positive Atomic value can pass numeric read-back while
the app still shows serving grams as unavailable. Creation now uses Weight measures
for both the reviewed serving and 1 g. Verification checks the default serving,
type, amount, visibility, gram measure, category and label format, in addition to
nutrients. Inspection returns raw measure metadata and only calls Weight values grams.

Product preparation accepts `category_id` from verified Cronometer food inspection.
Creation requires a selected category; explicit 0 is an intentionally uncategorized
food. No numeric category meanings are guessed. Completing metadata before the
first creation keeps the same proposal identity; metadata changes after creation do
not create another food or silently edit the existing one.

Excluding-fiber labels use the EU display format. Their stored canonical total
carbohydrates still include fiber. Including-fiber labels retain American 2016.
This is a display convention, not a reason to change nutrient totals or account
preferences. Missing nutrients remain unknown.

Reusing a created proposal now rechecks the remote record. Legacy records that fail
the stronger measure checks require reconciliation and must not be recreated.
An existing food can be repaired in Cronometer's editor without replacing its ID;
the separate guarded edit-tool workflow is specified in the implementation plan.
No existing-food edit HTTP permission is added by these creation fixes.

Acceptance includes selecting grams in the app and previewing 50 g, checking the
original serving size, category and package-style carbohydrate display. Unit tests
use synthetic profiles and include stale success, positive Atomic values, missing
or hidden gram measures, wrong amounts/default measures, and metadata mismatches.
