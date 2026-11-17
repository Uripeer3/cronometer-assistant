# Cronometer conversational assistant

A private prototype for logging meals through an existing AI assistant. The assistant handles Hebrew/English conversation and attached photos; this Python service owns persistent meal drafts, food-source policy, product memory, nutrient validation, and write reconciliation.

**Current milestone: tested conversational demo, real-food inspection and a controlled private custom-food creation. Serving, category and label-display fixes are merged. Automatic photo intake, public submission and real Cronometer diary writes are not implemented on main.** Demo food profiles are invented fixtures; their NCCDB/USDA source labels exercise ranking policy and are not authentic database extracts.

## Try the working demo

Python 3.12+ on Linux/macOS. The core has no third-party runtime dependencies:

```bash
PYTHONPATH=src python3 -m cronometer_assistant demo
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

The demo starts with an ambiguous rice entry, produces a preparation question, records a resolved bilingual meal, verifies the simulated diary, and repeats the same request to demonstrate deduplication. Its temporary database is deleted at exit. Add `--db data/demo.sqlite` to retain the example diary between invocations; each new demo invocation represents a new meal.

## Run the MCP server

Install [uv](https://docs.astral.sh/uv/), then:

```bash
uv sync --locked --extra mcp --group dev
uv run --locked --extra mcp cronometer-assistant serve --db data/demo.sqlite
```

The transport is **local stdio**, using the official MCP Python SDK. See [assistant setup](docs/assistant-setup.md) for a local-client configuration and the phone/ChatGPT compatibility gate. A public URL, OAuth server, and remote deployment are deliberately not part of this milestone.

To test it in ChatGPT through an OpenAI private tunnel, use the [tunnel setup guide](docs/tunnel-setup.md) and `python3 scripts/connect_demo.py --tunnel-id YOUR_TUNNEL_ID`. The launcher prompts for a restricted runtime key privately in your terminal.

## Implemented

- Persistent drafts with revisions, consumption intent, and unresolved questions.
- Revision-checked deletion of unlogged drafts; logged/uncertain meals are protected.
- Source-aware food ranking: identity/preparation first, then database preference.
- Verified product aliases, including Hebrew names, persisted across restarts.
- Validation of structured label data, including serving basis, kJ/kcal, mass units, salt equivalent, and explicit density for ml-to-g conversion. The assistant reads the image; the backend validates extracted values.
- Nutrient subtotals with missing-data coverage; absent values never become zero.
- A persistent simulated diary and operation journal. Repeated commits reuse the same meal operation; a new identical meal is still valid.
- Durable markers before writes, read-back verification, and reconciliation after an ambiguous timeout. No claim of exactly-once delivery against Cronometer.
- A read-only adapter interface around the pinned existing Cronometer client, tested with synthetic response fixtures.
- Separate real-food inspection tools with hidden local login, one-time 2FA, private session storage and blocked diary writes. See the [read-only guide](docs/cronometer-read-only.md).
- A pure recipe-portion calculation using finished batch weight. Saved recipe versions and native recipe writes are future work.

## Tool workflow

1. `begin_meal` creates a draft; preparing a dish does not log consumption.
2. `resolve_food` returns candidates with food IDs, sources, and nutrient profiles.
3. `update_meal_draft` replaces the complete item list using the current revision. Record estimates and unresolved questions explicitly.
4. `get_meal_review` returns suggested questions and known nutrient totals with coverage.
5. Once the user identifies the consumed portion, set `intent=eaten` and review again.
6. `commit_meal` writes only to the simulated diary. It reports committed only after read-back checks.
7. `get_logging_status` lets a later session resume the same draft or inspect an unfinished operation.
8. `delete_meal_draft` discards an unlogged draft when the user asks. It cannot undo diary entries.

Photo-based product entry uses `propose_product_from_label` followed by `save_verified_product`. Product saving is currently local to the demo catalog. `remember_product` records an alias only after the user establishes the preference.

## Development and ownership

Our application is independent of the upstream connector. Only the optional `live` extra depends on [rollecode/cronometer-mcp](https://github.com/rollecode/cronometer-mcp), pinned to commit `e123a4721093efa55adac665e10a546c105eb117`. Nothing from that project has been copied into this source tree. Live login uses hidden local terminal prompts; credentials and real writes are never exposed through MCP.

Use short branches and pull requests. CI checks formatting/lint, domain tests, package installation, and a real SDK/stdio round trip. Test fixtures need no food/health data, credentials, or repository secrets. Read [development notes](docs/development.md) and the [implementation plan](docs/implementation-plan.md).

## Next milestones

1. The initial Hebrew/English conversational draft test passed; continue measuring voice friction.
2. Run the [real-account read-only check](docs/cronometer-read-only.md), including interactive login.
3. Establish source-code mappings and nutrient/serving fidelity against the app.
4. Implement controlled live writes and corrections with explicit integration tests.
5. Add saved/versioned recipes, deployment authentication, backups, and the real-meal voice pilot.

GitHub stores source and synthetic fixtures. Runtime SQLite files, credentials, session tokens, and package-label photographs remain outside the repository.

## Review fixes and private custom foods

The [implementation review](docs/review-and-products.md) records the label,
unit, duplication, and creation-recovery fixes and the product-mode test procedure.
Label carbs now require an explicit fiber convention; raw values and sugar alcohols
are preserved. The current read-only connection stays read-only.

After updating, an explicit `--mode products` launch adds reviewed private-food
creation with an account-bound journal and read-back checks. It cannot edit
existing foods, write a diary, link barcodes, or publish to CRDB. The public
submission tool is a checklist, not a submission API. A controlled private-food creation and UI correction have been exercised; automated
tests use synthetic data and do not create real foods.

See the [development PR map](docs/pr-map.md) for the next review and test sequence.


## Open development batch

The [PR map](docs/pr-map.md) links the unmerged implementation stack and morning
checks. New feature branches add private photo evidence, submission choices,
manual-repair reconciliation, real-food local drafts and backup/restore. These
are not deployed capabilities until reviewed, tested and merged.

After merging the stack, use [product evidence](docs/product-evidence.md),
[manual repairs](docs/product-repairs.md), [real-food drafts](docs/live-drafts.md)
and [backup/restore](docs/backups.md). Real diary writes and automatic public
submission remain subsequent milestones.
