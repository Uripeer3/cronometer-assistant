# Independent code audit — 19 November 2026

Audited `release-foundation` at `main` `e57a534b95f52d121589107e1aa30e81015a081c` (the earlier local `17fad34` has the same tree). This is an independent source/test review against the personal-use mission, not a live-write acceptance run. No repository files, runtime configuration, or real diary records were changed. Synthetic reproductions are preserved in [the reproduction guide](audit-reproductions.md); these use existing test fixtures and, for diary operations, the pinned real client with `httpx.MockTransport`. The coordinating reviewer reran all five demonstrations and observed the outcomes below. The 125-test baseline also passed; these demonstrations reveal missing cases rather than failures in that suite.

## Assessment

The architecture is appropriate for one person: one Python process, SQLite documents, ordinary files and the existing tunnel. Keep the durable write journal, exact serving IDs, unknown-nutrient handling and guarded HTTP boundary. No SQL server, queue, new web frontend or separate secrets service is needed. The main gaps are workflow cohesion and recovery correctness, not lack of infrastructure. Green tests do not yet establish a usable end-to-end release.

## Findings requiring implementation follow-up

### F1 — High: pending writes resume without revalidating the food/measure/timezone contract

Evidence: `src/cronometer_assistant/service.py:359–401`; `live_drafts.py:184–212,240–245`. `_planned_steps` and the food/profile/token validation run only when creating an operation. A subsequent call with an existing journal reuses `add_parameters` for every pending serving without `_plan` or the current food validity check.

Reproduced: make a valid rice review; force the first diary baseline read to fail, yielding `waiting_for_diary` and zero writes; change the mock food to `retired=True` and energy 999 instead of 130; restore diary reads; call `commit_meal` using the original review token. Result is `committed`, with one add sent. Fresh initial preflight would reject that same retired food. The issue also applies to unsent later servings in a partially completed meal; account timezone and selected measure changes need the same boundary.

Fix: before sending each still-pending serving, validate the approved food/measure/translation/timezone snapshot. Preserve completed steps. If changed, stop and provide a bounded way to re-review only unsent work without clearing uncertain attempts or duplicating successful entries. Do not simply recompute and silently accept the new profile.

Acceptance: paused-before-first-add and paused-after-one-of-two cases; change nutrients, gram measure, retired flag or timezone; zero new writes until the changed pending portion is explicitly reviewed. Existing successful servings stay unchanged and duplicate-free.

### F2 — Medium: a verified part of an incomplete meal cannot be corrected or undone

Evidence: `diary_changes.py:30–45` requires the entire operation to be `committed`, rather than just a verified owned target step.

Reproduced: two-item draft; first add succeeds but response is lost; reconcile its exact new ID, leaving first step `verified`, second `pending`, operation `writing`; `undo_item` for the verified first serving rejects with “Select an item from a verified, logged meal”. Therefore the user cannot abandon/clean up a partial meal with the supplied tools. Continuing the meal just to gain undo is the wrong recovery workflow.

Fix: allow scoped cleanup of verified operation-owned steps during partial recovery, and record cancellation/skipping of unsent steps so a later resume cannot recreate undone items. Do not add a broad diary/day delete.

Acceptance: first of three foods succeeds, second is uncertain; undo only the verified first on request, retain uncertainty for second, cancel third before send; a repeated resume/undo does not recreate or duplicate anything and leaves an unrelated manual serving untouched.

### F3 — Medium: verified food selection is global per food ID, not per draft or alias

Evidence: `live_drafts.py:58–74` stores one `verified_foods[food_id]`; `service.py:133–138` aliases retain only `food_id`; `models.py` meal items have no selection identity.

Reproduced: draft food 42 with translated selection 77. Verify another selection 88 for the same food ID. Reviewing the old draft now shows selection 88 and its different name, while the draft revision remains 1. An old review token is correctly invalidated for a new operation, so this is not an unnoticed initial-write bypass. However two aliases/drafts for translated variants sharing one canonical ID cannot coexist reliably; the most recent verification changes all of them.

Fix: persist exact selection identity and preparation on the item/verified-match reference, including translation ID, display name and relevant profile version. Alias resolution should refer to that selection rather than only food ID. Preserve backward compatibility for canonical records.

Acceptance: two aliases and simultaneous drafts for distinct translations of one canonical food retain their names and translation IDs through verify, restart, review and logging. Re-reviewing one must not change the other.

### F4 — Medium: product review has no revision/token protecting mutable proposal metadata

Evidence: `products.py:112–164` leaves category out of proposal identity and changes it in-place while prepared; `products.py:288–325` creates by proposal ID plus `reviewed=True` only.

Reproduced: prepare/review category 5, prepare identical product with category 7 (same proposal ID), then create with the old ID and `reviewed=True`. Category 7 is written and verified. There is no stale-review rejection. This matters once parallel conversations or a combined everyday mode operate on one product.

Fix: a lightweight proposal revision/review digest, covering category, label convention, serving weight and payload nutrition. Creation checks the exact reviewed revision; no new workflow engine is needed.

Acceptance: mutate category after review and refuse old confirmation without a remote request; show new proposal; create once with fresh confirmation. Repeat should still deduplicate.

### F5 — Medium: malformed nutrition-score response becomes a successful empty result

Evidence: `daily_nutrition.py:38–45` validates only the normalized helper result. Pinned upstream `get_consumed_nutrients` treats a missing `scores` or `All Targets` structure as no components and returns a well-formed empty nutrient list.

Reproduced using the real pinned client with `get_nutrition_scores` returning `{}`: `daily_nutrition` succeeds with `tracked_count=0` and unknown macros. It does **not** invent zeros, which is good, but it does not distinguish a missing/malformed payload from a valid response with no tracked targets.

Fix: validate the raw score envelope or extend the adapter contract to retain response-validity/status information. Keep valid empty days, missing nutrients and zero distinct. Do not rely solely on mocks of `get_consumed_nutrients`.

Acceptance: real-client fake HTTP cases for absent `scores`, absent/malformed target components, explicitly empty valid targets, missing amount, explicit zero, and a nonempty account day. Malformed upstream data gives an actionable read error.

### F6 — Medium: uncertainty blocks safely but has no supported “proved not applied” recovery

Evidence: `service.py:380–383` and `diary_changes.py:110–113`; the existing `test_unconfirmed_outcome_never_replays_write` explicitly proves this permanent block.

If an add/edit fails before reaching the server, an accurate readback may never match the requested outcome. Retrying an uncertain add is immediately blocked; `get_logging_status` can read candidates. Retrying an uncertain edit/undo only reads, and different changes are refused; an operation blocks draft deletion forever. This conservatism is correct for ambiguous timeouts, but the recovery workflow is incomplete.

Add an explicit bounded operator-assisted reconciliation/cancel path only where non-application can actually be established. Absence in one diary read is not proof a delayed write will never arrive. If proof cannot be established, report the limitation and preserve the unresolved journal; never offer an automatic resend button.

Acceptance: distinguish known local validation failure before any send, definitive remote rejection, and ambiguous transport timeout. Only the first two may safely release/cancel an unsent attempt. Restart does not erase uncertainty.

## Product/mission gaps to prioritize (not all bugs)

| Area | Current code | Useful next increment and acceptance |
|---|---|---|
| One everyday connection | `cli.py:138–164` product mode has creation + draft tools, meal mode has diary tools; different default DBs | One coherent mode/data folder with scoped capabilities. Create missing product, retain its evidence/choice, then log it in the same conversation without server restart/schema refresh; migrate existing data deliberately |
| Full-day diary explanation | Account daily totals available; `get_logging_status` only returns operation-owned serving IDs | Read exact day’s food entries and authoritative group metadata, including manually entered foods. Compare name, amount, date/time/group and totals with the app |
| Source/measure fidelity | Raw nutrient rows preserved; source mappings intentionally unverified; grams required by meal model | Establish source/category/measure metadata and checked household-measure-to-grams conversion. “Half a cup” uses exact food’s measure, not model arithmetic; unfamiliar measure asks one useful question |
| Meal groups | `live_diary.py:10–11` hardcodes breakfast/lunch/dinner/snack IDs 1–4 | Verify on this account and either discover real groups or clearly restrict support. Renamed/reordered/custom groups must not silently misclassify a meal |
| Follow-up friction | First food requires identity/preparation/basis review; repeated aliases available | Cache established evidence and ask only consequential unknowns; benchmark familiar product and raw/cooked rice dialogs |
| Photos | Bounded inbox import/archive and durable references implemented | Host attachment delivery still absent. A chat attachment is not necessarily archived. Complete delivery before promising automatic filing; retain originals on submission decline |
| Product administration | Creation, manual repair adoption, local barcode/submission journal | Native edit/delete/barcode/photo/public submission absent. Manual app fallback acceptable for the initial personal release |
| Recipes | Pure `portion_ingredients` scaling helper only | Versioned ingredients/batch yield + fraction consumption reuse same diary workflow; no silent historical nutrition changes |
| Diary corrections | Amount only, one owned serving at a time | Date/time/group/food correction and convenient whole-meal undo are partial/missing; add only after basic recovery fixes |
| Daily nutrition | Tracked nutrient totals only; no goal/remaining budget | Read-only target comparison may be useful later; no need to import broad goal-changing or exercise tools to satisfy meal logging |
| Runtime | Foreground launcher, saved private session/key, backup/restore code | Proven local supervision/restart and one update/rollback recipe; then mobile text/voice/photo acceptance and optional always-on host. Android remains deferred |

## Verified strengths and caveats

- Reads use original nutrient rows; positive known nutrient IDs and supported units remain typed. Missing values are preserved. The concern that upstream daily totals coerce missing amounts to zero was **not confirmed**: pinned helper uses `comp.get("amount")` and macro `.get`, retaining `None`.
- Live write plan captures food ID, gram measure, translation, day, time and group. Fresh commits have a review token. Exact-ID readback and preexisting baseline exclusions prevent common duplicate errors.
- Corrections/undo validate the exact owned serving and compare current app state before writing. A repeated operation does not intentionally resend an ambiguous write.
- Product creation deep-copies payloads before the upstream client mutates them with auth; runtime files stay outside Git.
- Backups include SQLite plus referenced archived bytes with integrity checks, excluding credentials and external image bytes represented only by host references. The reference metadata itself remains in the backed-up SQLite state. No off-machine backup schedule/retention or full deployed restore drill was established by this audit.
- Single-user SQLite with host-local locking is sufficient. Product and meal modes are the main needless user-facing complexity; do not expand to separate microservices.
- No live diary add/edit/delete was performed during this review. Existing tests use synthetic HTTP; prior live checks established reads and confirmation refusal. Actual nonempty daily totals plus real diary mutation/readback remain acceptance work.

## Documentation corrections addressed in this PR

The previous `docs/pr-map.md` described #7–#14 as unmerged and planned slots 15–35 as future; actual #15 integrated the earlier stack and actual #16 delivered meal writes/daily totals/corrections. This revision replaces planned numbers with stable backlog IDs distinct from GitHub PRs and separates code, synthetic, live-read and live-write evidence.

The previous `docs/implementation-plan.md` said live diary writes and archived-photo implementation are absent; `docs/development.md` said correction/undo and real diary backends are unavailable; `docs/personal-scope.md` had stale line count and deployment-first order. These are updated to current code and laptop-first acceptance, keeping hosted deployment optional and using relative links instead of stale branch links.

The mission is achieved by a low-friction real meal plus readback, correction, undo, restart and a usable phone interaction—not by importing every upstream tool or finishing all optional native publication features.

## Synthesis and roadmap cross-reference

| Finding | Requirement | Release treatment |
|---|---|---|
| F1: stale resumed write plan | A9, R15, R25 | High priority before resumed live-write acceptance |
| F2: cannot clean up partial meal | A2, R25–R27 | Finish bounded partial recovery before claiming correction/undo complete |
| F3: shared selection identity | A3, R15, R30 | Preserve per-item and alias identity before effortless reuse claims |
| F4: mutable product proposal approval | A10, R21, A5 | Fix before combined/concurrent product creation |
| F5: malformed daily totals accepted | A4, R35 | Validate raw response through actual client before trusting summary success |
| F6: uncertainty dead end | A2, R25, R34 | Add evidence-based resolution; never replace caution with blind retries |

The [upstream comparison](upstream-comparison.md) independently inventories 41
upstream tools. It supports adding small read-only day, recent-food, personal-library
and nutrient-definition queries when useful, not importing exercise/fasting/profile
management. Neither codebase implements native photo/barcode/public submission.

The coordinating review retains one additional usability gap as A1: draft reviews
still reflect original amounts while a separate journal records corrections/undo.
The records retain history, but a clear current-meal summary is needed. This is
not a claim that daily Cronometer totals ignore edits or that a repeated log
automatically recreates an undone entry.

These findings are documented, **not fixed**, by this PR. Runtime availability is
verified separately at handoff; a running server does not close these acceptance gaps.
