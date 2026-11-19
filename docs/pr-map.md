# Current roadmap and acceptance tests

Updated 19 November 2026. Audited baseline: `main` at
`e57a534b95f52d121589107e1aa30e81015a081c` (merged PR #16).
Sequence 1 implements the audit corrections below; live pilot acceptance is tracked separately.
This is one person's meal-logging utility: one Python service, SQLite, private
files and the existing MCP tunnel. Complete the everyday flow before adding more
interfaces. Android hosting is deferred at the user's request.

This document is the current status source. The [implementation plan](implementation-plan.md)
retains design requirements; the [code audit](code-review-2026-11-19.md) and
[upstream comparison](upstream-comparison.md) explain the findings behind this revision.

## How to read status

- **Implemented:** present in the audited main tree. This does not mean deployed,
  tested against a real account, or complete for every input.
- **Partial:** a useful subset exists; the missing part is named explicitly.
- **Pending:** no end-to-end implementation exists.
- **Deferred:** retained as a possible improvement, not a prerequisite for daily use.
- **Live evidence:** observed during earlier account/plugin tests, separately from
  synthetic tests. This audit performs no diary writes or public submissions.

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
| [#13](https://github.com/Uripeer3/cronometer-assistant/pull/13): backup and isolated restore | Implemented through #15 | Synthetic SQLite/evidence integrity tests. Actual selected runtime backup/restore and reauthentication drill pending. |
| [#14](https://github.com/Uripeer3/cronometer-assistant/pull/14): launcher wiring and bounded download retry | Implemented through #15 | Automated mode/inbox/download tests. A systemd user service is now installed and a read-only MCP call succeeded; sleep/wake acceptance remains pending. |
| [#15](https://github.com/Uripeer3/cronometer-assistant/pull/15): foundation integration | Merged | Integrated #7–#14; 110 tests and CI passed at integration. #8–#14 were closed as superseded after verifying their exact heads are ancestors of main. |
| [#16](https://github.com/Uripeer3/cronometer-assistant/pull/16): live meal pilot | Merged; live-write acceptance pending | Reviewed add, recovery, daily totals, amount correction and per-item undo; 125 automated tests passed. Live food/daily-total reads and unconfirmed-write refusal exercised. No actual diary add/correct/delete acceptance yet. |

## Available modes and their limits

| Launcher mode | What the assistant can do | What it cannot do |
|---|---|---|
| `demo` | Synthetic foods, drafts, simulated logging | Access or change the real account |
| `read-only` | Connection check, real food search/details | Drafts, products, diary reads/writes |
| `drafts` | Real lookup, verified food profiles, persistent drafts, aliases, unlogged draft deletion | Log consumption or create foods |
| `products` | Draft tools plus private food proposals/creation, evidence import/review, submission choices and manual repair adoption | Log diary servings, edit/delete foods remotely, upload native photos or submit to CRDB |
| `meals` | Real lookup/drafts, full-day tracked nutrient totals, reviewed serving adds, uncertain-add reconciliation, amount correction and item undo | Product creation/archive tools, native recipes, general diary editing, automatic targets/trends |

`products` defaults to `data/products.sqlite`; `drafts` and `meals` default to
`data/drafts.sqlite`. Those are **separate stores**, not synchronized product
memory. Shared document namespaces are already possible within one database,
but a unified supported mode and migration are not implemented. Back up each
active database. Changing a tunnel mode requires a server restart and tool refresh;
a schema refresh alone does not change the running Python code.

Food inspection includes measures and raw nutrient rows. Live drafts/logging take
**grams**, not a selected cup/scoop quantity; the assistant currently handles any
household-measure interpretation. Source-ranking policy exists, but verified live
source mappings are not configured. Do not claim NCCDB/USDA prioritization is
proven in the live adapter merely because demo ranking tests pass.

## Delivery order: a small usable milestone first

| Sequence | Proposed PR scope | Completion check |
|---|---|---|
| 1 | Implemented: audit correctness/recovery findings (A1–A4, A9–A10), using the existing journal and adapters | 137 synthetic tests pass, including focused audit regressions and pinned-client HTTP cases. Two independent reviews (internal and upstream comparison); live acceptance remains sequence 2. |
| 2 | Finish the ordinary-meal pilot (R15, R23–R27, R35); bring forward the custom-food-in-meal flow from A5 after the reported chocolate limitation | One scoped live meal: inspect date/time/group/grams and non-empty daily totals, repeat without duplication, correct and undo only its entries |
| 3 | One everyday mode and reliable local start/update (A5, R31, R34) | Product creation and meal logging work in one conversation; restart retains all state; refreshed tools match the running revision |
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
| **R15 Food/source/serving fidelity** | Partial: inspected raw nutrition; label units and carbohydrate conventions; selected translation/measure IDs; verified profiles; account timezone | Establish source mappings, category lookup and representative gram/serving behavior. Compare generic rice and an exact package with the app, including 42 g and 50 g portions, missing nutrients and polyols. Verify changed profiles and distinct translated selections. |
| **R16 Automatic chat-photo delivery** | Pending; inbox import/archive exists | Send front/label photos in chat and retain actual bytes without manual copying. Restart and reimport without duplication; an interrupted transfer must not claim archived. |
| **R17 Native barcode linkage/reuse** | Pending; checksum and local metadata only | Link the barcode to the existing private food, scan it in the app, verify the same food/variant; repeat without duplicate creation. |
| **R18 Native photo attachments** | Pending; private local archive exists | Establish whether the native interface retains private photos, upload to the intended food and verify both roles. If unsupported, document archive + manual publication upload. Decline publication and retain originals. |
| **R19 Public-food submission** | Pending; persisted choice and checklist only | Submit the existing reviewed product only when requested; receipt proves sent/pending, not accepted. Missing evidence prompts only for missing information. |
| **R20 Submission recovery/status** | Partial local operator-outcome journal; automated status pending | Lose a submit response and inspect status before resending; distinguish unknown, pending, accepted and rejected. Keep private food/photos on rejection. |
| **R21 Remote private-food editing** | Pending; manual repair snapshot/adoption exists | Correct category/serving on the same food ID with fresh comparison, retained measure IDs and read-back. An external edit blocks stale writes; formulation change preserves history using a new version. |
| **R22 Food retirement/deletion** | Pending | Check supported retirement/delete and diary/recipe references. Retire a disposable owned food without damaging earlier nutrition, unrelated foods, aliases or evidence. |
| **R23 Diary entries/groups/time** | Partial internal diary reader and own-entry status; standard groups hardcoded | Add a small read-only day view and discover/validate account groups. Compare manual + assistant entries, custom/renamed groups and midnight dates. Empty day differs from read failure. Preserve reported time and account timezone; do not assume Jerusalem and Damascus always share an offset. |
| **R24 Reviewed live logging** | Implemented for regular non-recipe foods; live acceptance pending | Confirm exact foods/grams/date/time/group and current review token; add then inspect in app. Preparing writes nothing; repeated same event adds nothing; a separate identical meal is allowed. |
| **R25 Partial/crash recovery** | Partial: durable per-serving add state, baseline IDs, read-back and explicit reconciliation | Test interruptions before/after every add, including a response lost after success. No blind retry or claiming a pre-existing identical entry. Revalidate still-pending plans before resume (A9), allow partial cleanup, and add a supported resolution for attempts conclusively not sent and correction/undo ambiguity (A2). |
| **R26 Logged-meal correction** | Partial: amount of one assistant-owned serving only | Live amount check; then date/group/food replacement if useful. Distinguish additional portion from revised total. Preserve stable identity; stop on app-side conflicts. Audit corrected status/review consistency (A1). |
| **R27 Undo/deletion** | Partial: exact assistant-owned item undo; no whole-meal tool | Live item undo and repeated undo; then multi-item undo with partial recovery. Leave manually logged identical foods untouched. Draft deletion already exists and is separate from diary/custom-food deletion. |
| **R28 Saved recipe versions** | Pending; no native recipe integration | Save ingredients/IDs/amounts/preparation and immutable versions. Editing oil creates version 2 without changing version 1 or old meals. Choose local ingredient portions before native synchronization unless native history behavior is verified. |
| **R29 Batch yield/portions** | Partial pure arithmetic helper only | Persist finished weight or explicit fraction/servings, handle unknown yield honestly. A homogeneous 1,000 g/2,000 kcal batch yields 500 kcal for 250 g. Cooking is not consumption; do not equate raw and finished weight. |
| **R30 Follow-ups and usual products** | Partial: bilingual host instructions, questions, estimates, aliases | Reuse exact product/translation and ask only quantity. Ask dry/cooked and meaningful oils; request a crop for unreadable critical text. Add measured household units and one-off vs saved-default corrections (A6); avoid repeated basis certification. |
| **R31 MCP discovery/session behavior** | Partial: SDK stdio and tunnel work; unsupported discovery/fallback and stale sessions observed | Fresh connect/refresh/restart plus sequential calls without misleading validation dumps or stale tool names. Expired Cronometer session guides local login. Prefer small protocol fixes over blanket dependency upgrades. |
| **R32 Mobile voice** | Early bilingual draft experience exercised; complete live flow pending | On the actual phone: speak a meal, answer follow-up, send label, resume interruption and confirm once. Record taps/questions/time. A text-tool success is not proof of voice access. |
| **R33 Always-on hosting** | Pending; current architecture requires awake connected host | Keep tunnel + SQLite/files on one chosen persistent Linux device/VM. Reboot and call while laptop sleeps. Android work deferred; no account purchase/deployment implied. Dropping photos does not make Pages a Python host. |
| **R34 Operations/recovery** | Partial: protected session/key files, bounded launcher downloads, backup/isolated restore | Reliable supervision/autostart, tested update/rollback, session expiry, database-version migration and private readable diagnostics. Restore actual selected state without replaying uncertain writes. Password-file re-login optional, unimplemented. |
| **R35 Personal-use acceptance** | Partial: automated suite, live reads/drafts; real diary write pilot pending | First core milestone: generic + exact product, repeated log, correction, undo, positive daily totals and restart. Later extend to photos, recipe and voice when implemented; optional public submission is not a gate to daily logging. |

## Additional review-driven work

| ID | Improvement | Priority and acceptance |
|---|---|---|
| **A1 Current meal state after edits** | Reconcile original draft/journal with corrected or undone servings; distinguish historical intent from current recorded amounts | Before calling corrections complete: 150 g → 120 g → undo must give an unambiguous current summary, with history retained and no recreation on repeated log. |
| **A2 Recovery that can reach a resolved state** | Handle definitely-not-sent attempts, partial-meal cleanup and unresolved edits/deletes without permanent dead ends | Exercise pre-send failure, post-send timeout and external changes separately. Allow undo of a verified item in a partial operation and cancel unsent remainder without recreating it. Resolve using evidence; never reset blindly or resend an ambiguous mutation. |
| **A3 Exact selection identity** | Preserve food + translation + preparation per item/alias, not only a shared food ID | Verify two search variants sharing one food ID, use both in separate drafts, remember one and reverify the other: neither draft nor alias silently switches. |
| **A4 Daily nutrient provenance** | Audit raw totals vs client-normalized defaults and missingness | Feed absent/null/explicit-zero nutrient totals through the actual pinned client, not only a mocked helper. Missing must remain unknown; malformed raw score envelopes must fail instead of returning a successful empty summary. Compare positive live totals with the app. |
| **A5 One everyday mode/store** | Combine products and meals in one supported surface; consolidate existing SQLite state deliberately | Create/reuse a private product, retain photos, log it and read totals without switching modes. Migrate a copy first and preserve account bindings, IDs, journals and aliases; retain backup/rollback. |
| **A6 Verified household quantities** | Backend conversion for exact food measures, with explicit assumptions for estimates | A cup/scoop count uses that entry's verified gram weight; custom serving 42 g and 50 g log consistently. Liquids require density when converting volume to mass. Never treat an arbitrary Atomic unit as grams. |
| **A7 Small useful nutrition summary** | Optional read-only existing targets/remaining and short date-range totals, if useful | Amount vs target vs unknown remain distinct. Sum two known days without turning unavailable data into zero; no new target-setting, dieting advice or dashboard required. |
| **A8 Keep exposed scope deliberate** | Document upstream feature omissions and prefer small shared helpers over parallel implementations | No exercise, fasting, account-target writes or broad delete tools just for parity. Audit modes/tool descriptions against real registration; maintain one canonical roadmap and a short regression checklist. |
| **A9 Revalidate resumed writes** | Persist and check the approved snapshot before sending each still-pending serving | High priority (audit F1): stop a resumed operation if food nutrients, retirement, measure or account timezone changed; re-review unsent work without resending successful/uncertain steps. |
| **A10 Product proposal review revision** | Bind creation confirmation to exact mutable category/serving/nutrient metadata | Audit F4: prepare category 5, review, change to 7, attempt old approval: reject before sending. Fresh review creates once; repeats deduplicate. |
| **A11 Small discovery tools** | Bounded recent-food, own-product/local proposal and named nutrient-definition lookup | Reuse a product after restart without knowing its proposal ID. Find the correct micronutrient ID/unit without guessing. Paginate only growing results; do not silently turn recency into an exact match. |

The code audit records which items have a reproduced defect versus a design gap.
Sequence 1 fixes the documented cases. See [recovery semantics](sequence-1-recovery.md) for bounded cancellation, legacy compatibility and remaining ambiguity.

## Next PR and merge rules

1. Build one small change from current `main`, cite the relevant R/A IDs and state
   its observable before/after behavior. Preserve the existing working runtime/data.
2. Add focused tests for concrete nutrition or write/recovery risks; run the suite
   with both MCP and live dependencies. The 125-test baseline passed this review,
   but green tests do not close the audit findings or prove a real write.
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
