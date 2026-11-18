# Conversational Cronometer logger — implementation plan

Prepared 16 November 2026. Status: conversational prototype, real-account food lookup, and a controlled private-food creation have been exercised. Live UI review exposed serving-type, category and label-display gaps; creation fixes are merged. Automatic photo intake/archive and public-upload integration remain pending. Live diary writes, automated existing-food corrections and public-food submission are not yet implemented.

## Scope correction: personal utility

The user wants a small home/personal tool: simple flow, easy fixes and minimal
maintenance. The complete PR table is a backlog, not a commercial-product plan.
Use the [personal scope review](personal-scope.md) and the new priority section in
[the PR map](pr-map.md) to order delivery: early basic deployment; ordinary meal
logging/correction/undo; useful product/label support; then batch recipes.
Automatic public submission and rare food administration may use the app first.
Keep short write journals, read-back, units and unknown nutrients correct; avoid
enterprise infrastructure and repeated technical approvals.

A server-owned secret/session file is sufficient for this single user. Current
interactive login already retains a session; optional credential-file re-login
is possible but not implemented. Host prices/free limits and GitHub/GitLab Pages
constraints are documented in the review. Removing server photo storage would
not stop the assistant reading a nutrition photo, but would change the previously
requested retention behavior and needs an explicit choice before implementation.

## Goal and agreed preferences

Speak naturally while preparing or eating meals, answer a few useful follow-up questions, and have the assistant record the food in Cronometer using appropriate database entries. Repeated meals should become progressively easier to log.

- Start inside an existing assistant rather than building a phone app.
- Support Hebrew, English, mixed speech, Hebrew package labels, and local food names.
- Use metric quantities and the Cronometer account’s timezone; expected user timezone is Asia/Jerusalem.
- Preserve nutrient-data quality, distinguish unknown nutrients from zero, and remember established products and recipes.
- Track preparation as a draft and actual consumption as a diary event.

The primary success criterion is reduced effort during real meals. A technically successful connector that repeatedly interrupts cooking or demands screen approvals is insufficient.

## Recommended architecture

Use an existing assistant for speech, dialogue, translation, and reading attached label photos. Add a private service exposing a small set of MCP tools. The service owns food preferences, meal drafts, validation, and reliable diary writes. Wrap an existing Cronometer client behind a replaceable adapter rather than implementing the private protocol from scratch.

| Component | Responsibility | Initial implementation choice |
|---|---|---|
| Existing assistant | Voice conversation, Hebrew/English interpretation, photos, short explanations | ChatGPT first candidate, subject to actual account and mobile-tool tests |
| Meal service | Persistent drafts, product aliases, candidate ranking, question flags, reliable writes | Python service with MCP interface |
| Persistent store | Preferences, verified matches, recipes, draft state, operation journal | SQLite for one user, with backups |
| Cronometer adapter | Authentication, food search/details, diary read/write, custom products | Evaluate existing open-source clients; pin an audited revision |
| Hosting | Reachable authenticated endpoint even when the laptop is asleep | Private deployment on an always-on host; local service for development |

Cronometer remains authoritative for the diary and its recorded nutrition. Our store is authoritative for assistant-specific preferences, unfinished meals, recipe versions, provenance, and the relationship between a logging request and its resulting entries. General chat memory must not be the only store of these facts.

MCP is the interface, not the policy engine. Ranking, units, permissible writes, and duplicate prevention must run in application code rather than depending solely on a prompt.

## Feasibility gates before substantial implementation

### Gate 1: existing-assistant access

Check the user’s actual account for custom MCP tools with writes. OpenAI’s current documentation describes full custom MCP access for Business and Enterprise/Edu; do not assume a personal Plus account has equivalent access. Separately test whether the installed connection works in the phone’s Live voice experience, because general voice support does not prove this particular custom integration works there.

Test a harmless draft-only tool first, including a Hebrew/English exchange and an attached label photograph. Count required taps, especially action approvals. OpenAI documents on-screen approval for actions that require confirmation; do not promise a completely hands-free workflow.

If this fails, assess another existing assistant using its current documentation and an actual phone test. Consumer Gemini custom MCP currently has a US-only restriction and is not the initial choice for an Israeli user. If no existing assistant meets the requirements, revisit a small dedicated voice interface with the user; the meal service remains reusable.

Exit: identify an available assistant that can conduct the conversation and call our tools on the phone, with acceptable interaction overhead. Do not buy a subscription or choose hosting before resolving this gate.

### Gate 2: Cronometer data fidelity

Authenticate interactively, read the account timezone and today’s diary, search a few generic and Israeli packaged foods, and inspect their full nutrient and serving data. Confirm source codes can be mapped to NCCDB, USDA, branded, and user-created sources without guessing. Check that explicit zero, missing fields, and untracked nutrients remain distinguishable.

Then perform an explicitly scoped add/read-back/correct test on a user-selected diary date. Record entry IDs and remove only the entries created by the test if cleanup is requested. An empty diary is not proof that the entire integration works.

Exit: one ordinary food logs at the correct weight, date, meal group, and nutrient totals, and can be corrected without duplication.

### Gate 3: custom products and recipes

Test label-based custom food creation with omitted nutrients, gram-based portions, Unicode names, and mobile rendering. Test recipe yield with a known input and cooked batch weight, checking both total and per-portion nutrition. Confirm whether a recipe is editable in Cronometer and whether editing it changes old diary totals.

Exit: select an honest supported representation. If native recipe behavior is unreliable, initially save the structured recipe in our store and log its ingredient amounts proportionally as ordinary food entries. That preserves ingredient data while avoiding incorrect recipe-weight behavior.

## Existing code: what is known and what is still unverified

| Candidate | Evidence found | Decision |
|---|---|---|
| atticoos/cronometer-mcp | Documents search/details, diary writes, custom foods, recipe creation, session-based authentication, hosted and self-hosted deployment | Keep as an adapter candidate; its hosted service is not automatically our default |
| rollecode/cronometer-mcp | Inspected Python tool/client source: search returns source metadata; details include measures and per-serving/per-100-g nutrients; exposes food writes and recipe operations | Useful starting point for a Python implementation, pending tests and authentication review |
| Official Cronometer ChatGPT integration | Read-only daily aggregates; no individual foods or diary writes | Cannot implement meal logging |

The Python candidate’s default weight-based recipe path stores summed nutrition as a custom food and preserves ingredients in notes rather than as an editable native ingredient list. Its alternate recipe path has documented serving/weight limitations. This is a connector-specific implementation issue, not an established limitation of all Cronometer integrations.

Its example deployment retains login credentials and can require a two-factor secret. Do not adopt that credential design automatically. Prefer interactive login and protected session storage where the adapter supports it; retain two-factor authentication and handle reauthentication explicitly. The hosted TypeScript candidate documents a different session-based approach, so candidates need separate review.

All private-API behavior is provisional. Source inspection verifies what the code attempts, not successful operation on this account. No supported public personal diary-write API has been established.

## Food selection and follow-up policy

### Choose by identity first, detail second

1. Match identity, preparation, and relevant composition: cooked/raw, fat level, drained weight, edible portion, etc.
2. Reuse a previously verified exact product when the user indicates it is the same one.
3. For packaged food, prefer a current exact product match with an appropriate label.
4. For generic ingredients, prefer a suitable detailed NCCDB/USDA entry; retain source and food ID.
5. Use a clearly disclosed substitute only when an exact match is unavailable and the user accepts the estimate.

A source’s detailed nutrient coverage must not override a materially wrong food identity. An exact branded product can be the right choice even with fewer micronutrients.

### Ask the smallest useful question

The backend returns unresolved facts and reasons; the assistant turns the most useful ones into a short natural question. Group related questions, usually one or two at a time. Prioritize likely effect on calories, protein, a user-prioritized nutrient, or product identity. Start with rules rather than fabricated confidence percentages.

| Uncertainty | Default response |
|---|---|
| Rice weight might be dry or cooked | Ask before selecting the entry |
| Oil or dressing is unspecified | Ask a quick quantity question if relevant |
| Familiar product, quantity missing | Reuse the product; ask only quantity |
| Tiny unspecified amount of herbs | Estimate or omit with a brief record of the assumption |
| Unfamiliar cheese or yogurt | Ask brand/fat level or offer a label photo |
| User does not know a weight | Offer a household measure or approximate portion; do not block all logging |
| User says “estimate it” | Save a practical estimate and its assumptions; allow later correction |

Support names such as קוטג׳, גבינה לבנה, טחינה, and פיתה, but treat names as search/alias hints rather than universal nutrient profiles. “Tahini” may mean paste or diluted prepared sauce; a pita’s weight varies.

Store explicit defaults such as “my usual yogurt” separately from one-off guesses. Never promote an accidental inference into a permanent preference silently. A correction can apply to this meal, future matches, or a recipe version; resolve scope when it matters.

## Label-photo workflow

Ask for a photo only when it can resolve a meaningful gap: unknown product, poor match, conflicting values, or changed product. Request the product front/barcode too when the nutrition table alone does not establish identity. Barcode lookup is optional and depends on the chosen adapter’s actual capabilities.

The assistant extracts a structured proposal: product identity; quantity basis (per 100 g, per 100 ml, or serving); serving/package size; nutrient names, amounts and units; prepared/as-sold state; and unreadable fields. The backend validates dimensions and converts units. Grams and millilitres are not interchangeable without a supported conversion.

Handle Hebrew terminology, decimal punctuation, kcal versus kJ, sodium versus salt, and per-serving versus per-100-g columns. Do not recompute label energy from macros as an exact equality or overwrite it: rounding, fibre, and labelling conventions can differ. Use consistency checks to flag probable reading errors rather than “repairing” them automatically.

Present a compact read-back of consequential or uncertain values, then save a reusable custom product. A clear photo may need only one short confirmation; ask for a targeted crop if a critical field is unreadable. Do not request the whole table again for a familiar product.

Unlisted nutrients stay unknown. Never silently fill them with zero or copy a generic food’s micronutrients into an exact labelled product. A deliberately requested inferred profile must remain distinguishable from measured/label data.

## Missing packaged foods: private creation and optional public submission

Treat these as separate features. A custom food saved in the user's Cronometer account is immediately reusable there; submission to the shared CRDB starts Cronometer's curation process and does not mean it has been published.

1. **Create a verified private Cronometer food.** Search by barcode and exact brand/product/variant first. If no verified match exists, extract a proposal from the package front and nutrition table. Preserve label basis, serving weight, explicit zeros, unknown nutrients, sugar alcohols and carbohydrate convention. Ask only about unresolved consequential fields. After review, create the food in Cronometer (not only our local store), retain its ID/barcode and label version, and read back its nutrients and measures. Do not silently overwrite similar products or historical formulations. Repeating a save request must reuse the recorded creation or reconcile uncertainty before retrying.
2. **Offer submission to the public CRDB separately.** Prepare the verified custom food, barcode, clear front and nutrition-label images, and optional notification preference. Submit only when the user explicitly asks to share that product information. Track prepared, submitted/pending, accepted, rejected, or unknown status where the available interface permits; do not promise acceptance, approval timing, or public barcode availability before publication. Exclude homemade recipes and unsupported estimates from public submission.
3. **Check implementation feasibility.** Review whether the selected connector actually supports publication, evidence-image upload and submission status. No supported submission API is assumed. If automation is unavailable, prepare the package and guide the user through Cronometer's documented Publish Food flow. The private custom food remains usable while curation is pending.

Add proposed tools `create_verified_cronometer_food`, `prepare_public_food_submission`, and `submit_food_for_review`; expose only implemented capabilities. Submission and creation need independent IDs/journals, duplicate protection, and separate outcomes. Never equate a local draft, a saved private food, a submitted food, and an approved public database record.

Acceptance: an absent packaged product can be created from a bilingual label, found again by identity/barcode where supported, and logged at the correct weight without a repeated photo request. A separately authorized submission sends only product information and evidence, while clearly reporting that Cronometer controls publication. An existing packaged food must also pass an exact-variant label comparison as a positive control.

Reference: [Cronometer's publishing workflow](https://support.cronometer.com/hc/en-us/articles/360018652672-Publishing-a-food-to-the-CRDB-Database). This establishes the user-facing workflow, not the existence of an integration endpoint.

## Product photos: retain privately, offer submission separately

Product evidence is retained even if the user declines public submission. For the current pilot, the host can retain the original photos and materialize private local copies; the MCP server itself has no image-intake/archive or upload tool yet. Never turn a photo reference or a checklist into a claim that Cronometer received the image.

### Next implementation: durable evidence attached to the product

- Store original package-front, nutrition-table and barcode photos under private application data, outside Git. Also retain an existing durable host/Library reference when available; do not create duplicate Library items for already retained photos.
- Associate evidence with the proposal ID, eventual Cronometer food ID and label/formulation version. Record role, SHA-256, MIME type, byte size and original filename. Use content-based duplicate detection and preserve both original evidence and any separately prepared submission copy.
- Add a supported host-to-service transfer/import path rather than making the model reconstruct image bytes or exposing arbitrary server paths. Validate file type and size, install files atomically with private permissions, and test restart, duplicate attachment and incomplete transfer behavior. A reference-only record must explicitly distinguish retained host bytes from verified local bytes.
- Expose an evidence review tool returning roles, durable references and missing requirements. It must distinguish privately archived, attached/uploaded to Cronometer, and included in a submitted review request. Read-back of a food alone verifies none of these image states.

### Assistant policy and submission journal

1. After identity/label review and successful private-food verification, ask once whether to submit an eligible packaged product to Cronometer for public review. Do not ask again when the user already requested or declined submission. Photo retention continues independently.
2. Persist the choice (`not_asked`, `requested`, `declined` or `deferred`) per product/version. A new or changed photo does not silently grant publication permission. Retain the user's existing request while collecting missing evidence; do not ask them to approve the same submission repeatedly.
3. For a requested submission, verify the existing food and collect missing barcode, package-front and nutrition-table evidence. Show that submission shares product evidence with Cronometer's curation team and may lead to publication; no approval time or acceptance is guaranteed.
4. Implement separate prepare, submit and status/reconciliation operations only after verifying the actual interface. Until upload/submission support exists, use Cronometer's authenticated Publish Food flow. Keep `prepared`, `submitted_pending`, `accepted`, `rejected` and `unknown` distinct. A lost response requires status checking before another submission.
5. Keep originals privately even when submission is declined or rejected. Never include credentials, account details, diary records or unrelated attachments in the submission package.

Acceptance: an unfamiliar packaged food supplied by photos can be saved and revisited without another photo request; evidence survives restarts and is linked to the right formulation; the assistant offers submission once; a decline causes no upload; an existing request resumes at missing evidence; and a submission is reported only after a visible or API receipt. A controlled test of the manual flow precedes promises of automatic public submission.

## Custom-food usability and correction gate

Complete this gate before public-submission testing. A successful API response and matching numeric values alone do not establish that the food is usable in Cronometer.

- **Weight-based measures:** create a default `Weight` serving with explicit grams per one serving and an additional `Weight` measure for 1 g. Read back type, amount, value, default measure ID and visibility. A positive `Atomic` value must never be described as a verified gram weight. In the app, select grams and preview an arbitrary portion such as 50 g without logging it.
- **Category:** expose category IDs during inspection, select a category from verified Cronometer data for the same food family, and include it in the reviewed proposal and read-back. Do not guess numeric IDs or silently default to uncategorized; 0 must be an explicit choice. A full verified category-name lookup remains follow-up work.
- **Label display:** retain the original carbohydrate convention and canonical total carbs separately. For excluding-fiber labels, use an excluding-fiber display format while storing total carbs including fiber; show the relationship between label carbs, fiber, polyols and the account's net-carb calculation. Do not change account-wide carb preferences to make one product appear correct.
- **Acceptance:** the saved food retains all label nutrients, missing values remain unknown, the app shows the intended category and serving grams, a 50 g portion converts correctly, and the displayed label's carbohydrate convention is explained. Repeated creation rechecks the existing remote record rather than trusting a stale local success.

### Planned: reviewed correction of an existing private food

Add a separate `prepare_food_correction` / `apply_food_correction` workflow; do not implement edits by deleting and recreating the food or by widening every inherited write method.

1. Read the exact existing food ID, prove account ownership, snapshot its nutrients, measures, category, label format and assistant marker, and show the proposed field-level difference. Distinguish correcting a data-entry mistake from a new product formulation.
2. Warn about the effect of nutrient/serving edits on diary entries referencing that food. Preserve the food ID and existing measure IDs for an ordinary correction. Use a separate version for a new formulation; never overwrite a shared catalog entry.
3. Re-read and compare the snapshot immediately before writing. If the user edited the food externally, stop and rebuild the proposal rather than overwriting it. Limit changes to the reviewed fields, preserve unknown nutrients, and keep authentication out of snapshots and journals.
4. Journal the attempt, update once, and read back the complete result. A timeout permits verification/reconciliation only, never a blind second update. Update the local product record only after matching read-back, retaining the correction history.
5. Test the same food ID through category, label-format and serving repairs; arbitrary gram portions; stale snapshots; interrupted updates; externally edited records; legacy Atomic measures; and reuse after restart. Mobile and web usability are part of acceptance.

Until that tool exists, corrections use the authenticated Cronometer editor and are explicitly reported as manual UI corrections. Public submission stays a separate user-requested step after usability review, with barcode/photo requirements and Cronometer-controlled approval.

## Recipes, portions, and corrections

Store ingredients by food ID and quantity, recipe version, cooking assumptions, and optional finished yield. For a reasonably homogeneous batch, a portion of mass p from a batch of finished mass M uses fraction p/M of the recipe’s nutrients. Alternatively use a stated fraction or number of servings when no cooked weight is available. Do not assume ingredient weight equals finished weight.

Yield scaling handles water change; it does not model nutrient loss from heat, discarded liquid, bones, or uneven portions. Ask about those only when consequential. Preserve an estimate instead of demanding a batch weighing for every meal.

Recipe changes create a new version by default. Before modifying shared custom foods or native recipes, check whether old diary entries would change. A new formulation should not rewrite history silently.

Draft states: collecting, needs clarification, ready, writing, committed, partially written, or needs reconciliation. Cooking drafts do not contribute to Cronometer intake. “I ate half” commits the appropriate portion. “Actually 200 grams” corrects the associated event rather than adding another meal. Detect whether “another half” means an additional portion or a revised total before writing.

## Proposed initial MCP tools

| Tool | Purpose |
|---|---|
| begin_or_resume_meal | Start or resume a persistent draft; set preparing/eaten intent |
| resolve_food | Return ranked candidates, source, preparation, serving basis, and missing facts |
| update_meal_draft | Apply quantities, selected products, assumptions, and corrections |
| get_meal_review | Return planned entries, estimates, and material unresolved facts |
| propose_product_from_label | Validate extracted label data and produce a product proposal |
| save_verified_product | Save a reviewed product and its reusable alias |
| save_recipe_version | Store complete ingredients and yield without rewriting older versions |
| commit_meal | Validate and write the ready portion, using an operation ID |
| correct_logged_meal | Reconcile a correction against the exact entries created for that event |
| get_logging_status | Report draft/diary state, incomplete writes, and verified results |

Expose only the capabilities needed for logging. Broad target-setting, exercise, fasting, and unrelated deletion tools are outside the first version. Credentials never appear in tool responses or assistant context.

## Data and reliability requirements

Persist at least: product matches and aliases; food/source/measure IDs; preparation; per-serving and per-100-unit basis; nutrient amount/unit/known-status/provenance; recipe versions and yields; draft assumptions; consumption date and timezone; and a write journal linking operation IDs to remote entry IDs.

Keep dimensions and arithmetic in validated code. Distinguish a nutrient with no target from a missing nutrient value, and a recorded zero from an absent value. If some meal components lack a nutrient, present the known subtotal as incomplete rather than a complete intake total.

Serialize commits for one account/date. Record intended writes before calling Cronometer, capture each resulting ID, and read back the result. On timeout, first reconcile with the remote diary; never retry a non-idempotent food addition blindly. Client operation IDs alone do not make Cronometer’s private API idempotent. Identical legitimate meals need separate event IDs.

A multi-food write may partially succeed. Preserve progress, clearly report the unfinished state, and complete or correct only the entries associated with that operation. Never say “logged” before verification. Do not promise transactional or exactly-once guarantees the upstream API cannot provide. An undo must target exact recorded IDs; surface conflicts if the user edited them externally.

## Delivery milestones and acceptance criteria

| Milestone | Deliverable | Acceptance evidence |
|---|---|---|
| 0. Compatibility | Assistant/mobile/voice and connector feasibility report | Phone can invoke draft tools; authentication and food data work; friction measured |
| 1. Ordinary meals | Hebrew/English text dialogue, persistent drafts, source-aware matching, verified commit/correction | End-to-end logs match the Cronometer diary without duplicates |
| 2. Product memory | Aliases and label-photo proposals, custom product reuse | A verified product logs again without another photo; units and unknown fields survive |
| 3. Batch cooking | Versioned recipes, yield/fraction portions, preparation-to-consumption flow | Known batch example yields correct totals and portions in both tools and mobile app |
| 4. Voice pilot | Same workflow through the chosen assistant’s phone voice mode | Several days of real meals with acceptable question count, taps, and corrections |

Exercise voice at milestone 0 to avoid choosing an incompatible host, then develop difficult data behavior in text before the full voice pilot. Do not build a separate app unless the existing-assistant route fails its acceptance criteria.

High-value validation scenarios: dry/cooked rice; exact Israeli dairy product; Hebrew/English code-switching; an unreadable label; per-serving/per-100-g mix-up; sodium/salt and kJ/kcal units; absent nutrient versus zero; untracked nutrient; known cooked batch yield; estimated fraction; recipe reformulation without historical changes; midnight/timezone changes; interrupted write after one of several foods; repeated “log it”; valid second identical meal; expired session; and user edits made directly in Cronometer.

Suggested pilot targets, to calibrate with use rather than guarantee: familiar meals usually need no product questions; an ordinary unfamiliar meal needs at most one or two clarification turns; label capture is a one-time cost per product/version; every write has a verifiable result. Measure time, questions, screen taps, incorrect matches, duplicate entries, and connection failures. Record uncertainty instead of promising laboratory accuracy.

## Open implementation batch (17 November 2026)

The [PR map](pr-map.md) records the dependency order and morning acceptance checks.
Open PRs #8–#13 implement the map, private evidence import/review, persisted
submission choices and manual outcomes, snapshot-based adoption of manual food
repairs, real-food local drafts, and consistent backup/isolated restore. These
remain feature-branch implementations, awaiting individual live review and merge.

The deployed/main capabilities described above are unchanged until deployment.
There is still no automatic transfer of chat attachments into the configured
inbox, native barcode write, automatic Cronometer photo upload/public submission,
remote food-edit tool or real diary write. Manual food repair adoption changes our
local journal only. Operator-reported submission receipts are not independently
verified API results. Preserve these distinctions during morning testing.

## Complete follow-on PR map

The [complete PR map](pr-map.md) lists the delivered work, what to test, and simple
acceptance cases for each open PR #7–#14. Planned slots 15–35 cover all remaining
agreed work: data/source/category/serving fidelity, automatic photo delivery,
native barcode and photo handling, public submission/status, guarded edits and
custom-food retirement/deletion, diary reads/writes/recovery/correction/undo,
versioned recipes and yield portions, lower-friction dialogue, MCP compatibility,
phone voice, authenticated always-on hosting, operational recovery and the final
pilot. These future numbers are planning slots, not existing pull requests.

Local draft deletion is already implemented. Native custom-food deletion and
undo/deletion of logged diary entries remain separate future features. Neither
may be implemented by deleting a whole diary day or silently replacing foods
referenced by historical entries.

## Immediate next implementation task

Deploy the merged custom-food usability fixes and complete their live acceptance checks. Add durable photo intake/evidence review and the one-time submission-choice journal, then the reviewed existing-food correction workflow above. Keep the current creation-only transport boundary until that narrowly scoped edit path is implemented and tested. Public-submission preparation may follow user review, but direct upload/submission support remains unimplemented.

The next main logging milestone is connecting real food resolution to persistent meal drafts and verified diary add/correct/undo operations. Confirm the account timezone, meal groups and duplicate recovery before the first scoped diary test. Continue the phone voice compatibility check; select always-on hosting after the phone workflow is validated.

## Sources and limits

Sources inspected on 16 November 2026. These establish documented behavior and selected implementation details, not account-specific compatibility or an endorsement of third-party code.

- [Official Cronometer ChatGPT integration](https://cronometer.com/blog/cronometer-chatgpt-plugin/): aggregate, read-only access.
- [Cronometer Voice Logging](https://support.cronometer.com/hc/en-us/articles/47549391461780-Mobile-Voice-Logging): built-in matching/review workflow.
- [Cronometer data sources](https://support.cronometer.com/hc/en-us/articles/360018239472-Data-Sources): detailed generic database recommendations.
- [OpenAI custom MCP connections](https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt): access restrictions and connection workflow.
- [OpenAI Voice](https://help.openai.com/en/articles/20001274-chatgpt-voice): Live tools, photos, availability, and on-screen approvals.
- [Google custom connected apps](https://support.google.com/gemini/answer/17209137): present geographical and language restrictions.
- [atticoos connector](https://github.com/atticoos/cronometer-mcp): capabilities, private APIs, and self-hosting.
- [Community mobile API specification](https://github.com/atticoos/cronometer-mcp/blob/main/specs/cronometer-mobile.yaml): reverse-engineered contract, not official API documentation.
- [rollecode connector](https://github.com/rollecode/cronometer-mcp): Python alternative and documented recipe/serving behavior.
- [Inspected Python server](https://github.com/rollecode/cronometer-mcp/blob/main/src/cronometer_mcp/server.py), blob 3079c19594a9c3548678978dbaa66f6326c06b1b: source metadata and tool interfaces.
- [Inspected Python client](https://github.com/rollecode/cronometer-mcp/blob/main/src/cronometer_mcp/client.py), blob 3de764a4cd2195a62e6889527617e456c34d60d4: nutrient scaling and recipe representation. These are file blob identifiers, not whole-repository release pins.
