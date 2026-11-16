# Development PR map

This is the review order for the next development batch. Each PR targets the previous
branch so its diff stays focused. Nothing in this batch is merged automatically.
After merging a parent, retarget/rebase its child and rerun CI before merging it.
The morning tests below are required before deploying the corresponding capability.

| Order | Branch / PR | Deliverable | Morning acceptance |
|---|---|---|---|
| 0 | `codex/product-photo-evidence` / #7 | Photo retention and optional-submission policy | Confirm the intended ask-once interaction |
| 1 | `codex/development-pr-map` | This plan and accurate capability documentation | Review sequence and scope |
| 2 | `codex/private-product-evidence` | Durable evidence references, bounded private image import, hashes, deduplication and review tools | Import a front/label pair, restart, verify bytes and roles; decline publication and retain both |
| 3 | `codex/submission-journal` | Revision-checked publication choice, validated barcode, readiness and manual-submission receipts | A requested submission resumes without another approval; declining makes no upload; no receipt means no submitted claim |
| 4 | `codex/product-repair-review` | Snapshot-based review and reconciliation of manual corrections to the same private food | Detect changes, preview the corrected state, adopt only an exact re-read; never create a replacement |
| 5 | `codex/live-meal-drafts` | Local meal drafts using actual food data, account timezone and source-aware aliases | Hebrew/English preparation questions, weighed portions and persistence; no simulated or real diary write tools in this mode |
| 6 | `codex/runtime-backups` | Consistent SQLite backup, evidence copying, integrity verification and restore drill | Restore into an isolated directory and recover drafts, evidence and submission choices |

## One complete product, optional submission

Keep one product/version linking its reviewed label, Cronometer food ID, serving,
category, barcode and original photos. Retain photos regardless of publication
choice. In the inspected web UI, barcode belongs to the private food; the observed
photo controls are in Publish Food. A separate private-food photo attachment has
not been established. Do not promise all photo bytes live in Cronometer.

Photo import must use an explicitly configured inbox, not arbitrary paths or URLs
provided by the model. A durable host reference is useful but is not a verified
local archive. Imports and references must report their actual state. No tool in
this batch may fetch URLs, reconstruct images from chat text, or expose credentials.
A host still needs to deliver chat attachments to the inbox; document that seam.

Submission is a separate journal, not a second product-creation workflow. Record
requested/declined/deferred choices once per version; do not upload merely because
photos arrived. Native upload and submission automation stay unavailable until
their actual interface is verified. Manual receipts must be explicitly attributed
to the operator, not presented as an API-verified approval.

## Live boundaries and follow-on work

This batch builds usable foundations and recovery paths. It does not turn on live
diary writes, automatic public submission, account setting changes or production
hosting. Keep the current running pilot on its reviewed revision until testing.
Do not restart it onto an unmerged feature branch.

Next slices, after these are tested:

1. Verify source mappings, category lookup, serving basis and account timezone in
   the app. A timezone discrepancy must be discussed, not silently corrected.
2. Add an explicitly opt-in diary adapter and tools: exact date/group/portion,
   durable attempt markers, returned IDs, read-back, partial-write recovery and
   scoped corrections/undo. Test a user-chosen date before using normal meals.
3. Add immutable recipe versions and cooked-yield portions; confirm whether a
   native recipe edit changes historical diary entries before selecting a backend.
4. Add guarded field-scoped remote food edits only after the protocol is verified.
   Review impact on historical diary entries; snapshot/compare before writing.
5. Verify photo upload/submission interfaces, receipts and uncertain-result
   recovery. A successful file upload is not a submitted food or public approval.
6. Test phone voice and approval friction, then choose always-on hosting,
   authentication, session-expiry recovery, backups and deployment rollback.

Tests use synthetic fixtures, mocked HTTP and real MCP SDK boundaries. Real photos,
barcodes, catalog payloads, account identifiers and sessions stay outside Git.
