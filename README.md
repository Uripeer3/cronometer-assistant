# Cronometer conversational assistant

A private prototype for logging meals through an existing AI assistant. The assistant handles Hebrew/English conversation and attached photos; this Python service owns persistent meal drafts, food-source policy, product memory, nutrient validation, and write reconciliation.

**Current milestone: local MCP tools and a simulated diary. No real Cronometer diary writes are implemented.** Demo food profiles are invented fixtures; their NCCDB/USDA source labels exercise ranking policy and are not authentic database extracts.

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
- Source-aware food ranking: identity/preparation first, then database preference.
- Verified product aliases, including Hebrew names, persisted across restarts.
- Validation of structured label data, including serving basis, kJ/kcal, mass units, salt equivalent, and explicit density for ml-to-g conversion. The assistant reads the image; the backend validates extracted values.
- Nutrient subtotals with missing-data coverage; absent values never become zero.
- A persistent simulated diary and operation journal. Repeated commits reuse the same meal operation; a new identical meal is still valid.
- Durable markers before writes, read-back verification, and reconciliation after an ambiguous timeout. No claim of exactly-once delivery against Cronometer.
- A read-only adapter interface around the pinned existing Cronometer client, tested with synthetic response fixtures.
- A pure recipe-portion calculation using finished batch weight. Saved recipe versions and native recipe writes are future work.

## Tool workflow

1. `begin_meal` creates a draft; preparing a dish does not log consumption.
2. `resolve_food` returns candidates with food IDs, sources, and nutrient profiles.
3. `update_meal_draft` replaces the complete item list using the current revision. Record estimates and unresolved questions explicitly.
4. `get_meal_review` returns suggested questions and known nutrient totals with coverage.
5. Once the user identifies the consumed portion, set `intent=eaten` and review again.
6. `commit_meal` writes only to the simulated diary. It reports committed only after read-back checks.
7. `get_logging_status` lets a later session resume the same draft or inspect an unfinished operation.

Photo-based product entry uses `propose_product_from_label` followed by `save_verified_product`. Product saving is currently local to the demo catalog. `remember_product` records an alias only after the user establishes the preference.

## Development and ownership

Our application is independent of the upstream connector. Only the optional `live` extra depends on [rollecode/cronometer-mcp](https://github.com/rollecode/cronometer-mcp), pinned to commit `e123a4721093efa55adac665e10a546c105eb117`. Nothing from that project has been copied into this source tree. The live adapter accepts an explicitly supplied authenticated client; this milestone does not expose live authentication or real writes through MCP.

Use short branches and pull requests. CI checks formatting/lint, domain tests, package installation, and a real SDK/stdio round trip. Test fixtures need no food/health data, credentials, or repository secrets. Read [development notes](docs/development.md) and the [implementation plan](docs/implementation-plan.md).

## Next milestones

1. Verify custom write-tool availability and phone voice behavior in the chosen assistant; measure required taps.
2. Review the upstream authentication/session behavior and authenticate interactively without credentials in chat.
3. Establish source-code mappings and nutrient/serving fidelity against the account.
4. Implement controlled live writes and corrections with explicit integration tests.
5. Add saved/versioned recipes, deployment authentication, backups, and the real-meal voice pilot.

GitHub stores source and synthetic fixtures. Runtime SQLite files, credentials, session tokens, and package-label photographs remain outside the repository.
