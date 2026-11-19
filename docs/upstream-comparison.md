# Upstream capability comparison — 19 November 2026

## Scope and provenance

Independent source review of `rollecode/cronometer-mcp`, the Claude-oriented connector actually used by this project's optional `live` dependency. GitHub's commit API was queried on 19 November 2026: upstream `main` resolves to **e123a4721093efa55adac665e10a546c105eb117**, version **1.11.1**. That is exactly the revision pinned by our `pyproject.toml` and `uv.lock`; there is no observed upstream drift to catch up with. The README's 1.9.2 badge is stale; package/source metadata wins.

Our reviewed source is the merged main tree (also matching earlier local `17fad34`), at `e57a534b95f52d121589107e1aa30e81015a081c`. This is source-level comparison, not a live acceptance test of every upstream endpoint. No account mutation or server restart was performed for this audit.

We depend on `CronometerClient`; we do **not** run upstream `server.py`, import its whole MCP tool catalog, or use its Node OAuth proxy. Therefore installing the dependency does not automatically expose its other capabilities through our plugin. Its methods still encounter our HTTP allowlist when used with our protected client.

Pinned evidence:

- [Upstream commit](https://github.com/rollecode/cronometer-mcp/commit/e123a4721093efa55adac665e10a546c105eb117)
- [Tool definitions](https://github.com/rollecode/cronometer-mcp/blob/e123a4721093efa55adac665e10a546c105eb117/src/cronometer_mcp/server.py)
- [Private API client](https://github.com/rollecode/cronometer-mcp/blob/e123a4721093efa55adac665e10a546c105eb117/src/cronometer_mcp/client.py)
- [Result paging](https://github.com/rollecode/cronometer-mcp/blob/e123a4721093efa55adac665e10a546c105eb117/src/cronometer_mcp/paging.py)
- [Setup and user-facing behavior](https://github.com/rollecode/cronometer-mcp/blob/e123a4721093efa55adac665e10a546c105eb117/README.md)
- [OAuth proxy](https://github.com/rollecode/cronometer-mcp/blob/e123a4721093efa55adac665e10a546c105eb117/auth-server.js)

## Complete MCP inventory

AST inspection finds **40 tools in upstream server.py plus `get_result_page`, registered by paging.py: 41 total**. The following table accounts for all 41. “Available” means our code exposes the equivalent capability, not that every real-account write has been tested. The server.py line identifies the pinned upstream function. Our current CLI meal mode exposes 17 tools; product mode exposes 24 (3 inspection + 13 product/evidence/repair + 8 draft tools), but cannot log a diary. Those modes are not yet one combined everyday surface.

| Upstream tool (server.py line unless noted) | Our equivalent/current boundary | Personal-use decision |
|---|---|---|
| `get_food_log` (150) | Partial: adapter reads diary internally; `get_logging_status` exposes only entries associated with one assistant meal. No complete day's named food list, per-entry nutrient contributions or target/remaining summary. | Add a small read-only day view and energy target/remaining; useful for checking actual intake. |
| `add_food_entry` (235) | `log_meal` adds reviewed ordinary food servings with exact date/time/group/translation, durable journal and read-back. No native recipe servings. Live add acceptance remains pending. | Keep narrower reviewed workflow; extend recipe behavior only with verified units. |
| `remove_food_entry` (309) | `undo_logged_item` removes one assistant-owned verified serving; cannot select arbitrary app-created entries or undo the whole meal in one operation. | Finish whole-meal undo/recovery; arbitrary diary administration is optional. |
| `mark_day_complete` (349) | Absent. | Optional convenience after the basic pilot; not needed to count intake. |
| `copy_day` (380) | Absent. Upstream copies the **previous day to the destination day**, additively, rather than an arbitrary source/destination tool. | Defer bulk copy; saved usual meals/recipes are more deliberate and fit our journal. |
| `get_daily_nutrition` (415) | Available but narrower: selected account day's tracked nutrients only, `include_untracked=False`; missing amounts stay unknown, but malformed raw score responses need validation (audit F5). No synthesized untracked totals. | Keep reliable tracked view; add optional untracked coverage only with explicit incomplete-data accounting. |
| `get_nutrition_scores` (465) | Used internally for tracked totals, not exposed as score/category tool. | Defer scores unless requested; raw intake/targets are more directly useful. |
| `search_foods` (507) | `search_cronometer_foods` and ranked `resolve_food`. Bounded candidates/detail reads, raw unverified source codes. | Keep; verify source mappings and add recent/usual matches to reduce queries. |
| `get_food_details` (555) | `inspect_cronometer_food`: raw nutrient values, units, measures, category and label type. No trusted household-measure conversion tool. | Finish tested measure conversion and source/basis fidelity, retaining explicit unknowns. |
| `add_custom_food` (588) | Product mode `prepare_cronometer_product` / `create_verified_cronometer_food`, stronger serving/category/fiber convention validation and journal. Meal mode cannot create food. | Combine product and meal workflows; do not revert to upstream's Atomic-only creation default. |
| `get_recent_foods` (657) | Absent. Explicit verified aliases exist but are not the account's recent-food list. | High-value small addition for low-friction matching; recency is a hint, not identity proof. |
| `get_streak` (671) | Absent. | Defer gamification. |
| `get_profile` (684) | Only account timezone/connection metadata exposed; no full birthdate/gender/language profile. | Keep minimal metadata needed for dates; full profile unnecessary. |
| `create_recipe` (693) | Absent as saved/native workflow; only pure batch-portion arithmetic exists. | Add versioned local recipes/yield first; evaluate upstream representation deliberately. |
| `list_custom_foods` (768) | Absent as personal-library enumeration; ordinary search can find foods and local proposal review needs its ID. | Add bounded private-library/proposal discovery for reuse and repair. |
| `list_recipes` (791) | Absent. | Add alongside recipe feature, not a separate platform project. |
| `find_entries_by_food` (812) | Absent. Upstream scans a date range day-by-day, default roughly last 30 days; not proof of all historical references. | Add bounded impact inspection before remote food edits/retirement; report scanned range, not “no historical usage.” |
| `update_custom_food` (847) | Absent remotely. `prepare_product_repair` / `adopt_manual_product_repair` reconcile an app-side repair locally. | Planned targeted edits preserving IDs; inspect history and re-read changes before write. |
| `update_recipe` (890) | Absent. | Versioned recipe changes first; avoid silently changing prior meals. |
| `retire_custom_food` (926) | Absent. Upstream supports retire/unretire, **not guaranteed permanent deletion**. | Implement retirement only when needed; preserve referenced history and local evidence. |
| `list_nutrients` (951) | Definitions used internally; no standalone named catalog tool. Product payload supports 11 friendly keys plus validated `cronometer_ID` keys. | Add a small discoverable nutrient lookup to avoid making the assistant invent micronutrient IDs. |
| `get_macro_targets` (991) | Absent. Upstream returns schedule/templates, not simply today's final energy target. | Reuse appropriate target source in day summary; defer schedule editing. |
| `get_fasting_history` (1024) | Absent. | Outside meal logging scope. |
| `get_fasting_stats` (1062) | Absent. | Outside scope. |
| `list_biometrics` (1089) | Absent. | Outside scope unless user adds weight tracking. |
| `get_biometrics` (1128) | Absent. | Outside scope. |
| `edit_food_entry` (1176) | `correct_logged_amount` changes grams only, assistant-owned entries only. Upstream also edits **time**, but its `date` selects the existing day's entry; it does not expose moving it to a new date/group. | Finish requested date/time/group correction deliberately; upstream is not a complete solution for all three. |
| `add_note` (1213) | Absent remotely; assumptions retained locally in drafts. | Optional later, not needed for reliable meal writes. |
| `edit_note` (1231) | Absent. | Defer with remote notes. |
| `add_biometric` (1252) | Absent. | Outside scope. |
| `edit_biometric` (1287) | Absent. | Outside scope. |
| `remove_biometric` (1303) | Absent. | Outside scope. |
| `add_exercise` (1336) | Absent. | Outside scope; read Cronometer-adjusted target rather than duplicating exercise management. |
| `edit_exercise` (1371) | Absent. | Outside scope. |
| `remove_exercise` (1398) | Absent. | Outside scope. |
| `add_fast` (1431) | Absent. | Outside scope. |
| `edit_fast` (1455) | Absent. | Outside scope. |
| `delete_fast` (1488) | Absent. | Outside scope. |
| `get_targets` (1514) | Absent. | Add read-only nutrient goals/limits/visibility if needed for “how much remains?”; distinguish targets from amounts eaten. |
| `set_nutrient_target` (1529) | Absent. | Defer account-setting writes; Cronometer app remains the simple settings UI. |
| `get_result_page` (paging.py 185) | Absent. Current search/draft bounds limit some responses, but there is no generic pagination. | Add bounded pagination with new library/day/history reads where useful; no need to copy upstream's entire response cache. |

## Capabilities neither implementation currently supplies

No upstream tool or client method was found for native barcode linkage/scanning, food-photo attachment/upload, public CRDB submission, or submission-result reconciliation. Our barcode validation/local metadata, private evidence archive, submission-choice journal and manual checklist do **not** supply those native actions either. These remain separate feasibility work; exposing another upstream tool will not finish them.

Neither current wrapper supplies account-specific group discovery: upstream maps standard groups to 1–4 and optionally chooses by hour; ours also maps breakfast/lunch/dinner/snack to 1–4. Do not call this verified support for renamed/custom account groups. Both require additional evidence if that scope is added.

Native recipe history is not solved by upstream recipe CRUD. Its default `serving_type="weight"` computes nutrients locally and stores a normal food (`meal=false`) with ingredient IDs/grams in notes. The alternative `serving_type="servings"` stores a native ingredient list but has documented weight/UI limitations. `cooked_grams` is applied by the weight branch, not the native servings branch. Editing in place changes nutrition used by existing diary references; it is not immutable versioning.

## Upstream client versus our transport

The client offers batch food reads, recent food lookup, own-library enumeration, bounded-by-caller reference history, native food/recipe changes, target reads/writes, nutrition scores, profiles and measurements. Our HTTP guard intentionally allows only catalog reads by default, new-food creation in product mode, and narrowly shaped serving/diary operations in meal mode. Calls like upstream `list_own_foods` need batch `/get_foods` plus different search parameters, and new endpoints require reviewed guards and synthetic/live acceptance. Merely adding a Python tool wrapper is insufficient.

The two apps are genuinely different layers: upstream is a broad Cronometer API toolbox; ours adds a persistent conversational workflow, verified aliases and preparation, label-convention handling, durable approval/revision and write journals, uncertain-outcome reconciliation, private photo evidence, manual repair/submission tracking and backups. Those additions are appropriate for this user; copying all 41 tools would enlarge the surface without improving ordinary meals.

## Runtime differences worth retaining or learning from

| Area | Upstream | Our current position / recommendation |
|---|---|---|
| Remote access | Python stdio or localhost streamable HTTP; separate Node OAuth 2.1/static-token proxy, nginx and external HTTPS/tunnel documented | Keep current official outbound MCP tunnel and stdio for simplest deployment. Do not inherit Node/nginx/OAuth merely for parity. |
| Local supervision | Installer writes systemd service setup for Python and authentication proxy | Borrow the operational goal: one tested supervised laptop/home service, clear status and restart, persistent data. Not yet proven in ours. |
| Credentials | Private username/password env file, cached session, optional TOTP seed for unattended re-login | Our interactive hidden login + cached session avoids retaining password/TOTP seed. Optional credential-file re-login is a conscious later choice, not a missing prerequisite. |
| Session longevity | Upstream HTTP MCP idle timeout explicitly 24 hours and proxy disconnect/deadline handling | Ours is stdio through a different tunnel; investigate actual refresh/discovery/session failures rather than copying an HTTP timeout blindly. |
| Response size | 80,000-character budget; eight in-memory retained results; filter/project/page tool | Prefer small named queries and explicit limits; add paging for genuinely growing histories/libraries. |
| Identity metadata | Package version and icons advertised | Expose safe build/version and active capability mode so stale plugin/server diagnosis is easy. Icons optional. |
| Writes | Direct tools with broad write annotations, auth retry, no equivalent durable per-meal journal | Keep our before-write journal and read-back; do not infer idempotency from upstream MCP annotations. |

## Recommended additions to the roadmap

1. **Day intake view plus read-only target/remaining calories.** Use Cronometer's diary energy target rather than fixed macro-template guesses; include app-created foods, account day, named quantities and missing coverage. Acceptance: compare a nonempty app day and a changed expenditure/target day; preserve absent values as unknown.
2. **Recent and own-product discovery.** Bounded recent foods/private-library/local proposal listing; aliases retain precedence only for an explicitly matching product. Acceptance: “my usual yogurt” resolves without redundant questions; a changed flavor/label still requires clarification; list remains usable after restart.
3. **Named nutrient lookup and optional untracked detail.** Discover definitions/units for arbitrary label micronutrients; optionally report untracked totals as coverage-qualified estimates, not complete data. Acceptance: caffeine present without a target is distinguishable from missing data and known zero; unknown nutrient key cannot silently disappear.
4. **History-aware edits/retirement.** Reuse relevant client methods only behind ours' exact-target journal and preservation checks; state history-scan date range. Acceptance: category/serving fix keeps IDs; retirement does not claim permanent deletion; out-of-range usage remains unknown.
5. **One everyday mode, safe runtime identity, and supervised restart.** More valuable before expanding the tool inventory than metrics/fasts/exercises/streaks. Acceptance: create a missing food, retain evidence, and log it without switching servers/databases; restart preserves records and reports the actual deployed version.
6. **Versioned local batches/yield.** Upstream has useful client code to study, but immutable recipe versions and correct app-visible servings need their own design/tests. Acceptance: changed recipe leaves older diary intake unchanged; 250 g from a homogeneous 1,000 g/2,000 kcal batch logs 500 kcal.

All proposals remain one Python application, SQLite and private files. Explicitly defer fasting, exercise CRUD, biometric CRUD, day-copy, streaks, broad account settings and an upstream-style public OAuth hosting stack. Native photo/submission features are requested backlog items, but should not delay a correct ordinary-meal pilot.

## Roadmap placement

Keep the [current map](pr-map.md) authoritative: day/group reads belong to R23,
recipe/library work to R28–R30, bounded food-history checks to R21–R22, safe runtime
identity/supervision to R31/R34, and combined operation to A5. Additional small
queries (recent foods, own products/proposals and named nutrient definitions) are
A11; optional read-only targets/remaining are A7. These do not require exposing
all upstream tools or adding its public HTTP/OAuth deployment stack.
