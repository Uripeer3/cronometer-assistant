# Current roadmap and acceptance tests

Updated 21 November 2026. Current deployed baseline: `main` at
`935e3d1cd431399cd5147518aecbebd6a585e5e9` (merged PR #21).
The original audit covered PR #16; sequence 1 (#18) fixed its bounded correctness
findings and sequence 2 (#19) combined the tools and passed the scoped live pilot.
The relocated checkout is now clean on this main revision. A live MCP connection
check succeeds, and refreshed discovery exposes product creation, meal logging
and unsent-item cancellation together. Both runtime backups verify successfully.
The first conversational pilot exposed a draft-input schema gap: the tool
advertised generic objects while the service accepted seven specific fields.
PR #21 exposes those fields in live/demo MCP schemas and validates
malformed replacements before food lookup or draft mutation. It is deployed;
the user's conversational retry successfully saved the selected items, but the earlier live pilot did not
exercise this failure. See [draft input and selected-food logging](meal-draft-input.md).
The subsequent conversational retry saved all selected foods, but exposed a
second blocker: the cached session zone differed from the fresh account profile,
and the preflight error offered no acknowledgement action. The follow-up adds
`confirm_meal_timezone` for unlogged drafts, current-profile timezone reporting
in meals mode, and fresh-zone defaults for new meals and daily totals. Synthetic tests cover
revision/token invalidation and continued blocking after a second profile change.
Runtime deployment and actual diary upload remain separate acceptance steps.
This is one person's meal-logging utility: one Python service, SQLite, private
files and the existing MCP tunnel. Complete the everyday flow before adding more
interfaces. Android hosting is deferred at the user's request.

This document is the current status source. The [implementation plan](implementation-plan.md)
retains design requirements; the [code audit](code-review-2026-11-19.md) and
[upstream comparison](upstream-comparison.md) explain the findings behind this revision.

## How to read status

- **Implemented:** present in the current main tree. This does not mean deployed,
  tested against a real account, or complete for every input.
- **Partial:** a useful subset exists; the missing part is named explicitly.
- **Pending:** no end-to-end implementation exists.
- **Deferred:** retained as a possible improvement, not a prerequisite for daily use.
- **Live evidence:** observed during earlier account/plugin tests, separately from
  synthetic tests. The original audit performed no writes. Sequence 2 performed one explicitly scoped
  add/correct/undo drill; its test entry was removed. No public submission occurred.

Backlog IDs **R15–R35** preserve the old planned slots. They are requirements,
**not GitHub PR numbers**. Actual PR #15 integrated the foundation and actual
PR #16 implemented the meal pilot; neither means old slot 15/16 is complete.
New audit items use **A1–A11** below. Future PRs should reference these IDs and take
whatever number GitHub assigns; do not reserve a long chain of branches.

## What is on main

| Delivered work | Code status | Evidence and remaining acceptance |
|---|---|---|
| PRs [#1](https://github.com/Uripeer3/cronometer-assistant/pull/1)–[#6](https://github.com/Uripeer3/cronometer-assistant/pull/6): demo, tunnel, hidden login, real lookup, private food creation and serving/category/label fixes | Implemented | Earlier bilingual demo, real food inspection, controlled custom-food creation and manual app repair exercised. This is not proof of all foods or nutrient conventions. |
| [#7](https://github.com/Uripeer3/cronometer-assistant/pull/7): retain photos and offer submission separately | Policy implemented, integrated | Policy does not transfer photos or publish foods. Verify an existing request/decline is respected. |
| [#8](https://github.com/Uripeer3/cronometer-assistant/pull/8): scope and complete plan | Integrated through #15; superseded status updated here | Closed as superseded after confirming its head was integrated through #15. |
| [#9](https://github.com/Uripeer3/cronometer-assistant/pull/9): private evidence archive | Implemented through #15 | Synthetic import/hash/dedup/restart tests. Requires files already in a private inbox; chat attachment delivery remains pending. |
| [#10](https://github.com/Uripeer3/cronometer-assistant/pull/10): submission choice, local barcode and manual outcome | Implemented through #15 | Synthetic revision/choice/checksum tests. No native barcode linkage or API submission; reported outcomes are operator observations. |
| [#11](https://github.com/Uripeer3/cronometer-assistant/pull/11): adopt manual food repairs | Implemented through #15 | Snapshot/conflict tests; adoption updates our journal, not Cronometer. Live same-food repair/adoption still needs a full recorded drill. |
| [#12](https://github.com/Uripeer3/cronometer-assistant/pull/12): real-food drafts and aliases | Implemented through #15 | Real food lookup and bilingual/local draft checks exercised; changed-profile and account-bound tests. Repeated-product friction needs a real-meal pilot. |
| [#13](https://github.com/Uripeer3/cronometer-assistant/pull/13): backup and isolated restore | Implemented through #15 | Synthetic SQLite/evidence integrity tests. Both selected runtime database backups verified after the update. An isolated restore and reauthentication drill remain pending. |
| [#14](https://github.com/Uripeer3/cronometer-assistant/pull/14): launcher wiring and bounded download retry | Implemented through #15 | Automated mode/inbox/download tests. A systemd user service is now installed and a read-only MCP call succeeded; sleep/wake acceptance remains pending. |
| [#15](https://github.com/Uripeer3/cronometer-assistant/pull/15): foundation integration | Merged | Integrated #7–#14; 110 tests and CI passed at integration. #8–#14 were closed as superseded after verifying their exact heads are ancestors of main. |
| [#16](https://github.com/Uripeer3/cronometer-assistant/pull/16): initial live meal tools | Merged; scoped live acceptance subsequently passed in #19 | Reviewed add, recovery, daily totals, amount correction and item undo; 125 tests at that milestone. See #18/#19 for subsequent fixes and evidence. |
| [#17](https://github.com/Uripeer3/cronometer-assistant/pull/17): roadmap audit and setup guide | Merged | Historical audit, upstream comparison and end-to-end setup/update/recovery instructions. |
| [#18](https://github.com/Uripeer3/cronometer-assistant/pull/18): sequence 1 correctness/recovery | Merged and deployed | A1–A4 and A9–A10 regressions fixed; 137 tests, CI and two independent reviews passed. |
| [#19](https://github.com/Uripeer3/cronometer-assistant/pull/19): sequence 2 combined meal/product tools | Merged and deployed | 139 tests, CI and two independent reviews passed. Live ProFlex 42 g → 50 g → undo matched app/API totals; repeat calls deduplicated, test entry removed. Checkout, both backups, connection and combined tool discovery verified after local update. |

## Available modes and their limits

| Launcher mode | What the assistant can do | What it cannot do |
|---|---|---|
| `demo` | Synthetic foods, drafts, simulated logging | Access or change the real account |
| `read-only` | Connection check, real food search/details | Drafts, products, diary reads/writes |
| `drafts` | Real lookup, verified food profiles, persistent drafts, aliases, unlogged draft deletion | Log consumption or create foods |
| `products` | Draft tools plus private food proposals/creation, evidence import/review, submission choices and manual repair adoption | Log diary servings, edit/delete foods remotely, upload native photos or submit to CRDB |
| `meals` | Real lookup/drafts, reviewed private-food creation and evidence tools, full-day tracked nutrient totals, reviewed serving adds, uncertain-add reconciliation, amount correction and item undo | Native recipes, general diary editing, native photo upload/public submission, automatic targets/trends |

`products` defaults to `data/products.sqlite`; `drafts` and `meals` keep drafts
in `data/drafts.sqlite`. Meals mode now also opens the sibling `products.sqlite`
(or explicit `--products-db`) for product proposals/evidence. This is one tool
surface with **two preserved stores**: no automatic migration, alias merging or
data loss. Back up both active databases. Changing a tunnel mode requires a server restart and tool refresh;
a schema refresh alone does not change the running Python code.

Food inspection includes measures and raw nutrient rows. Live drafts/logging take
**grams**, not a selected cup/scoop quantity; the assistant currently handles any
household-measure interpretation. Source-ranking policy exists, but verified live
source mappings are not configured. Do not claim NCCDB/USDA prioritization is
proven in the live adapter merely because demo ranking tests pass.

## Delivery order: a small usable milestone first

Immediate acceptance: after deploying the draft-schema fix and refreshing tools,
resume a historical draft, save five verified foods and keep an unresolved food
in a separate draft. Verify the date/time, review and log only the ready draft,
then read back its status. Synthetic SDK tests cover this path without account
writes. There is no atomic subset/split tool yet; keep that as a small R30
follow-up if separate-draft handling is too fragile. Do not mark the ordinary
conversational meal pilot complete until the actual retry succeeds.

| Sequence | Proposed PR scope | Completion check |
|---|---|---|
| 1 | Implemented: audit correctness/recovery findings (A1–A4, A9–A10), using the existing journal and adapters | 137 synthetic tests pass, including focused audit regressions and pinned-client HTTP cases. Two independent reviews (internal and upstream comparison); subsequent scoped live acceptance passed in sequence 2. |
| 2 | Combined custom-food/meal surface implemented; scoped ordinary-meal pilot passed (R15, R24, own-entry portions of R26–R27, R35) | 139 automated tests; live ProFlex 42 g → 50 g → undo, app/API totals comparison and repeat dedup passed. Combined surface is deployed and discovered in ChatGPT; a normal conversational dinner is the next acceptance test. R23 general day view/custom groups remains pending. |
| 3 | Mostly delivered: one everyday mode and local update (A5, R31, R34); keep two simple stores | User completed update/restart; checkout, backups, MCP read and combined tool discovery verified. Remaining: normal conversational product-to-meal trial, sleep/wake/reboot and isolated restore/rollback drills. Database consolidation is deferred. |
| 4 | Reduce meal-entry friction (A6, R30), then add reusable batches (R28–R29) | Usual product requires only quantity; known cup/scoop uses verified grams; one recipe version and portion survive restart |
| 5 | Complete photo intake (R16) and phone test (R32); choose always-on host only if wanted (R33) | Chat photos reach the archive; a short mobile meal works; laptop-off use only after an actual host is deployed |
| Optional | Native barcode/photos, public submission/status and food administration (R17–R22) | Verify the actual native interface and its read-back; manual app flow remains an acceptable fallback |

A phone draft smoke test can happen early. Hosting is not a prerequisite for
finishing laptop logging. No additional database server, web app, queue, cloud
account or credential service is required for this milestone.

## Sequence 1 implementation evidence

A1 exposes current journal-derived quantities separately from the original draft,
including uncertain changes and history. A2 permits cleanup of verified items in
partial operations, cancellation of pending/definitively rejected items, and
safe reconfirmation of changes proved not applied. Timeouts remain protected.
A3 binds immutable selection references to items/aliases and rejects ambiguous
omission. A4 validates the diary and raw scores used in the same daily-total read.
A9 checks approved food, measure and timezone before each pending send; legacy
operations without snapshots require cancellation/review of unsent work.
A10 requires the exact proposal review token before custom-food creation.

Changed unsent work is handled by cancelling only those items, then reviewing a
new draft containing only the cancelled remainder. Successful/uncertain items
must never be copied for retry. This deliberately reuses the existing draft flow.

## Complete requirement map

| ID / feature | Current implementation | What remains / simple acceptance case |
|---|---|---|
| **R15 Food/source/serving fidelity** | Partial: inspected raw nutrition; label units and carbohydrate conventions; selected translation/measure IDs; verified profiles; account timezone | Establish source mappings, category lookup and representative gram/serving behavior. Exact ProFlex 42 g/50 g portions and polyols passed app/API comparison; generic multi-item live acceptance and representative missing-nutrient cases remain. Verify changed profiles and distinct translated selections. |
| **R16 Automatic chat-photo delivery** | Pending; inbox import/archive exists | Send front/label photos in chat and retain actual bytes without manual copying. Restart and reimport without duplication; an interrupted transfer must not claim archived. |
| **R17 Native barcode linkage/reuse** | Pending; checksum and local metadata only | Link the barcode to the existing private food, scan it in the app, verify the same food/variant; repeat without duplicate creation. |
| **R18 Native photo attachments** | Pending; private local archive exists | Establish whether the native interface retains private photos, upload to the intended food and verify both roles. If unsupported, document archive + manual publication upload. Decline publication and retain originals. |
| **R19 Public-food submission** | Pending; persisted choice and checklist only | Submit the existing reviewed product only when requested; receipt proves sent/pending, not accepted. Missing evidence prompts only for missing information. |
| **R20 Submission recovery/status** | Partial local operator-outcome journal; automated status pending | Lose a submit response and inspect status before resending; distinguish unknown, pending, accepted and rejected. Keep private food/photos on rejection. |
| **R21 Remote private-food editing** | Pending; manual repair snapshot/adoption exists | Correct category/serving on the same food ID with fresh comparison, retained measure IDs and read-back. An external edit blocks stale writes; formulation change preserves history using a new version. |
| **R22 Food retirement/deletion** | Pending | Check supported retirement/delete and diary/recipe references. Retire a disposable owned food without damaging earlier nutrition, unrelated foods, aliases or evidence. |
| **R23 Diary entries/groups/time** | Partial internal diary reader and own-entry status; standard groups hardcoded | Add a small read-only day view and discover/validate account groups. Compare manual + assistant entries, custom/renamed groups and midnight dates. Empty day differs from read failure. Preserve reported time and account timezone; do not assume Jerusalem and Damascus always share an offset. |
| **R24 Reviewed live logging** | Implemented; one scoped real add/repeat/app readback passed on 20 November | Confirm exact foods/grams/date/time/group and current review token; add then inspect in app. Preparing writes nothing; repeated same event adds nothing; a separate identical meal is allowed. |
| **R25 Partial/crash recovery** | Partial: durable add journal/reconciliation plus #18 pending-plan validation, partial cleanup and cancellation of proven-unsent items | Synthetic failure/restart tests pass. Broader real interruption drills remain; ambiguous writes must stay protected. Never retry blindly or claim a pre-existing identical entry. |
| **R26 Logged-meal correction** | Partial: amount of one assistant-owned serving; live 42 g → 50 g and app readback passed | Date/group/food replacement remains pending if useful. Distinguish additional portion from revised total. Preserve stable identity; stop on app-side conflicts. A1 corrected/undone summaries are implemented and regression-tested. |
| **R27 Undo/deletion** | Partial: exact assistant-owned item undo; live undo/repeat and cleanup passed; no whole-meal tool | Multi-item undo with partial recovery remains pending. Leave manually logged identical foods untouched. Draft deletion already exists and is separate from diary/custom-food deletion. |
| **R28 Saved recipe versions** | Pending; no native recipe integration | Save ingredients/IDs/amounts/preparation and immutable versions. Editing oil creates version 2 without changing version 1 or old meals. Choose local ingredient portions before native synchronization unless native history behavior is verified. |
| **R29 Batch yield/portions** | Partial pure arithmetic helper only | Persist finished weight or explicit fraction/servings, handle unknown yield honestly. A homogeneous 1,000 g/2,000 kcal batch yields 500 kcal for 250 g. Cooking is not consumption; do not equate raw and finished weight. |
| **R30 Follow-ups and usual products** | Partial: bilingual host instructions, questions, estimates, aliases | Reuse exact product/translation and ask only quantity. Ask dry/cooked and meaningful oils; request a crop for unreadable critical text. Add measured household units and one-off vs saved-default corrections (A6); avoid repeated basis certification. |
| **R31 MCP discovery/session behavior** | Partial: combined tools discovered and a post-update MCP read succeeded; discovery noise/session recovery remain | Fresh connect/refresh/restart plus sequential calls without misleading validation dumps or stale tool names. Expired Cronometer session guides local login. Prefer small protocol fixes over blanket dependency upgrades. |
| **R32 Mobile voice** | Early bilingual draft experience exercised; complete live flow pending | On the actual phone: speak a meal, answer follow-up, send label, resume interruption and confirm once. Record taps/questions/time. A text-tool success is not proof of voice access. |
| **R33 Always-on hosting** | Pending; current architecture requires awake connected host | Keep tunnel + SQLite/files on one chosen persistent Linux device/VM. Reboot and call while laptop sleeps. Android work deferred; no account purchase/deployment implied. Dropping photos does not make Pages a Python host. |
| **R34 Operations/recovery** | Partial: systemd service configured; local update/restart reported successful; both runtime backups verified; protected session/key files | Sleep/wake/reboot, isolated restore/rollback, session expiry, database-version migration and private readable diagnostics remain. Backup-parent permissions must be 0700; improve the generic CLI error to expose a safe actionable cause. Restore actual selected state without replaying uncertain writes. Password-file re-login optional, unimplemented. |
| **R35 Personal-use acceptance** | Partial: 139 automated tests and scoped exact-product live pilot passed | Deployment, tool refresh and connection read verified. A normal conversational dinner and generic + exact multi-item live meal remain; sleep/wake/reboot acceptance is separate. Later extend to photos, recipe and voice when implemented; optional public submission is not a gate to daily logging. |

## Additional review-driven work

| ID | Improvement | Priority and acceptance |
|---|---|---|
| **A1 Current meal state after edits** | **Implemented in #18.** Reconcile original draft/journal with corrected or undone servings; distinguish historical intent from current recorded amounts | Synthetic 150 g → 120 g → undo and live 42 g → 50 g → undo passed; history is retained and repeated log does not recreate the entry. |
| **A2 Recovery that can reach a resolved state** | **Bounded recovery implemented in #18.** Handle definitely-not-sent attempts and partial-meal cleanup; genuinely ambiguous writes remain blocked until evidence resolves them | Synthetic pre-send failure, post-send timeout and external-change tests pass. Verified partial-operation undo and cancellation of unsent remainder are regression-tested. Resolve using evidence; never reset blindly or resend an ambiguous mutation. |
| **A3 Exact selection identity** | **Implemented in #18.** Preserve food + translation + preparation per item/alias, not only a shared food ID | Regression passed: two search variants share one food ID; separate drafts and aliases keep their exact selection after reverification and restart. |
| **A4 Daily nutrient provenance** | **Implemented in #18.** Audit raw totals vs client-normalized defaults and missingness | Feed absent/null/explicit-zero nutrient totals through the actual pinned client, not only a mocked helper. Missing must remain unknown; malformed raw score envelopes must fail instead of returning a successful empty summary. Positive live ProFlex totals matched the app in sequence 2. |
| **A5 One everyday mode/store** | Combined meals/product surface deployed in sequence 2; two existing stores retained deliberately | Exercise creation → verification → log in one connection and after restart. Back up both stores. Consolidation is deferred; no migration is needed for this personal-use milestone. Chat attachment delivery remains R16. |
| **A6 Verified household quantities** | **Pending.** Backend conversion for exact food measures, with explicit assumptions for estimates | A cup/scoop count uses that entry's verified gram weight; custom serving 42 g and 50 g log consistently. Liquids require density when converting volume to mass. Never treat an arbitrary Atomic unit as grams. |
| **A7 Small useful nutrition summary** | Optional read-only existing targets/remaining and short date-range totals, if useful | Amount vs target vs unknown remain distinct. Sum two known days without turning unavailable data into zero; no new target-setting, dieting advice or dashboard required. |
| **A8 Keep exposed scope deliberate** | Document upstream feature omissions and prefer small shared helpers over parallel implementations | No exercise, fasting, account-target writes or broad delete tools just for parity. Audit modes/tool descriptions against real registration; maintain one canonical roadmap and a short regression checklist. |
| **A9 Revalidate resumed writes** | **Implemented in #18.** Persist and check the approved snapshot before sending each still-pending serving | Regression-tested (audit F1): stop a resumed operation if food nutrients, retirement, measure or account timezone changed; re-review unsent work without resending successful/uncertain steps. |
| **A10 Product proposal review revision** | **Implemented in #18.** Bind creation confirmation to exact mutable category/serving/nutrient metadata | Regression-tested (audit F4): prepare category 5, review, change to 7, attempt old approval: rejected before sending. Fresh review creates once; repeats deduplicate. |
| **A11 Small discovery tools** | **Pending.** Bounded recent-food, own-product/local proposal and named nutrient-definition lookup | Reuse a product after restart without knowing its proposal ID. Find the correct micronutrient ID/unit without guessing. Paginate only growing results; do not silently turn recency into an exact match. |

The code audit records which items have a reproduced defect versus a design gap.
Sequence 1 fixes the documented cases. See [recovery semantics](sequence-1-recovery.md) for bounded cancellation, legacy compatibility and remaining ambiguity.

## Next PR and merge rules

1. Build one small change from current `main`, cite the relevant R/A IDs and state
   its observable before/after behavior. Preserve the existing working runtime/data.
2. Add focused tests for concrete nutrition or write/recovery risks; run the suite
   with both MCP and live dependencies. Current baseline: 139 passing tests.
   Green tests alone do not prove a live write or deployment.
3. Independent review, CI, then a scoped live check where relevant. Document
   **synthetic**, **live read**, **live write** and **not tested** distinctly.
4. Merge reviewed changes and update this map in the same PR. Restart the chosen
   runtime, refresh tools when schemas change, verify health and an actual tool call.
   A merged branch or installed schema alone is not a deployment.
5. Superseded #8–#14 are closed; do not re-merge or rebuild those features.

Keep secrets, raw diary data and label photographs out of Git. Retain private
originals regardless of publication choice. Public submission is separate from
private creation, and submission is separate from acceptance. Unknown nutrients
and uncertain side effects must remain explicit throughout the flow.

## Sequence 2 evidence and limits

See [the pilot report](sequence-2-pilot.md) for the exact live measurements and
synthetic combined-tool coverage. The chocolate limitation was a mode-registration
gap: `serve-meals` now includes existing product tools, with separate product and
meal confirmations. It preserves both SQLite files, accepts an explicit product
store path, and can import host-staged evidence without changing modes.

Post-merge deployment is now verified: the user completed the local update and
reported the service running; the relocated checkout is clean at #19, both
drafts/products backups pass integrity verification, and a real MCP connection
check succeeds. Refreshed discovery includes product creation and logging in
one connection. No new diary write was performed for this documentation update.

Next acceptance: log an ordinary dinner through the refreshed connection. If an
exact packaged food is missing, prepare/review/create it, then separately review
and log its portion. Check reported date/time/group, read totals, and preserve
existing meals. Creating a product does not replace an already logged food;
R26 replacement remains pending (explicit undo plus a new reviewed entry is the
current workaround). After that, prioritize A11 discovery and A6 household
measures to reduce friction. Native photo upload, public submission, custom
groups and general diary replacement are not implied.
