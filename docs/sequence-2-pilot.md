# Sequence 2: one meal connection and a scoped diary pilot

20 November 2026. Builds on merged sequence 1 (PR #18). The reported chocolate
failure occurred because meals mode did not register the existing custom-product
tools. Meals mode now exposes both services. No new nutrition writer or database
server was introduced.

## What changed

- `serve-meals` enables the existing private-food and diary request guards and
  registers product, draft and meal tools in the same MCP server.
- Drafts, selections and diary journals stay in the existing `--db` path.
  Product proposals, creation journals and evidence use sibling `products.sqlite`
  by default, or explicit `--products-db`. No files or aliases are migrated.
  Explicitly pointing both arguments at one file uses existing separate
  namespaces; the default is two files. Back up every active database.
- Creating a custom food requires review of the exact label proposal and token.
  Logging that food requires a separate meal review/confirmation. Instructions
  require the verified selection ID and explain replacement of a logged serving.
- Both meals and products launchers accept an optional host-managed photo inbox.
  This does not implement chat attachment transfer or native photo upload.
- Read-only/drafts modes still cannot create foods; products mode cannot log.

## Automated acceptance

139 tests passed with the locked MCP/live dependencies. The new combined-flow
test invokes real MCP tools with the pinned HTTP client against synthetic HTTP
responses. It covers custom chocolate search, label preparation, refusal of
unreviewed/stale-token creation, creation/readback, 25 g and 1 g measures, EU
carbohydrate conversion with separate fiber/polyols, exact energy preservation,
food verification, a 37.5 g meal, refusal of an unconfirmed write, log/repeat,
40 g correction/repeat, undo/repeat and restart persistence. Separate database
namespaces and CLI default/explicit paths and narrower modes are checked.
Two independent Sol 6.1 high reviews approved the changes: internal correctness
and pinned-upstream comparison. Their backup-documentation correction was applied.

These HTTP fixtures are not evidence that a new real chocolate was created in
this run. The live drill reused the already reviewed ProFlex custom food.

## Live acceptance

Using the new source against the existing private session, an isolated local
pilot journal added one serving on 20 November in Snacks at 03:07 account wall
time. The serving ID was retained for all subsequent operations. The baseline
had no food servings; the existing Garmin activity was left untouched.

| Check | Observed result |
|---|---|
| Add 42 g ProFlex chocolate cereal | Committed and read back; one row under Snacks in the app |
| Repeat the same logging request after restarting the client | `already_logged=true`, no extra serving |
| Read positive daily totals | API matched the visible app values after rounding; table below |
| Correct the owned serving to 50 g | Same serving ID, date, time, group, measure and translation retained; app showed 50 g |
| Undo that exact serving | Readback verified absence; app returned to its baseline food totals |
| Repeat undo after restarting the client | `already_applied=true`, no further write |
| Cleanup | Serving-ID set matched baseline; original activity remained visible |

| Nutrient | API at 42 g | API at 50 g | App comparison |
|---|---:|---:|---|
| Energy (kcal) | 148.26 | 176.5 | 148.3 / 176.5 |
| Protein (g) | 15.12 | 18 | 15.1 / 18.0 |
| Canonical total carbs (g) | 17.64 | 21 | 17.6 recorded at 42 g |
| Net carbs (g) | 8.82 | 10.5 | 8.8 / 10.5 |
| Fat (g) | 3.78 | 4.5 | 3.8 / 4.5 |
| Fiber (g) | 4.62 | 5.5 | 4.6 / 5.5 |

ProFlex's label excludes fiber from its carbohydrate row. Canonical total carbs
include fiber: at 42 g, label carbs are 13.02 g, fiber 4.62 g and polyols 4.2 g;
canonical carbs are 17.64 g and net carbs are 8.82 g. Package rounding explains
the displayed label values 13 g and 8.8 g. No extra carbohydrate was invented.

## Deployment follow-up and remaining acceptance

During the pilot this environment could not update the relocated installation
or access the systemd user bus. The live drill therefore tested new source
directly, not through the deployed ChatGPT connection.

After PR #19 merged, the user completed the local update/restart. On 20 November
the checkout was verified clean on main at merge 6b0c118, both runtime backups
passed verification, a live MCP read succeeded, and discovery exposed both
product creation and meal logging. The initial backup failure was a parent
directory with mode 0755; changing it to 0700 resolved that prerequisite.

For future updates use the [update/restart instructions](setup-and-operations.md#update-the-code).
The next acceptance is an ordinary dinner in a new chat and, when needed,
create an exact missing package from its label in the same conversation.

A general diary-day view, renamed/custom groups, multi-item live acceptance,
whole-meal replacement/undo and automatic attachment delivery remain roadmap
items. A routine server restart and sleep/wake drill are separate from the
client-restart checks above. Public submission and native photos/barcodes remain
manual; this pilot neither created another public record nor published images.
