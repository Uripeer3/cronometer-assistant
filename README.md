# Cronometer conversational assistant

A private prototype for logging meals through an existing AI assistant. The assistant handles Hebrew/English conversation and attached photos; this Python service owns persistent meal drafts, food-source policy, product memory, nutrient validation, and write reconciliation.

## Log meals in ChatGPT

**Start here:** [End-to-end setup, automatic startup, updates and recovery](docs/setup-and-operations.md). It covers the local service, private key, tunnel, ChatGPT connection and everyday restart commands. Already set up? Jump to its everyday commands or update procedure.

Use the **meals** server mode for a personal Cronometer diary. The server runs on your computer and ChatGPT reaches it through the existing private MCP tunnel. On a new installation, install the locked dependencies and sign in to Cronometer once from a terminal in the repository.

```bash
uv sync --locked --extra mcp --extra live --no-dev
.venv/bin/python -m cronometer_assistant login
```

Start the server from the checkout that holds your private session and SQLite data. Stop any previous launcher using this tunnel first.

```bash
python3 scripts/connect_demo.py --mode meals --tunnel-id YOUR_TUNNEL_ID
```

Keep the launcher running. Once the tunnel is ready, refresh the connection in ChatGPT and select the Cronometer assistant. For a first read, ask **“How many calories and how much protein have I logged today?”** The `get_daily_nutrition` tool reads Cronometer's tracked totals for the chosen account day, including foods entered through the app. It returns amounts with units and leaves missing values unknown.

To log a meal, describe what you ate in Hebrew or English. For example, **“For lunch at 13:00 I ate 150 g of cooked white rice.”** The assistant finds the food, verifies its preparation and nutrient basis, and shows the selected food, grams, date, account-local time and meal group. Once you confirm that review, `log_meal` sends each weighed serving and verifies the returned entry in the diary. The journal prevents a repeated request from adding a second serving. An uncertain response can be checked with `get_logging_status`.

To change an assistant-logged serving, say **“Change that rice from 150 g to 120 g”** or **“Undo the rice.”** The assistant identifies the particular serving, asks for confirmation, then verifies the result. The tools are `correct_logged_amount` and `undo_logged_item`. A meal can contain several servings.

The [current roadmap](docs/pr-map.md) and [independent code review](docs/code-review-2026-11-19.md) separate implemented features from acceptance and unresolved issues. The meal pilot is not yet declared ready for unattended everyday use.

The installed ChatGPT connection has been tested with live food reads and daily totals. Automated tests exercise add, correct, undo and uncertain responses using the pinned Cronometer client and simulated HTTP. The final live write check is a real meal you choose to log, followed by a check of its date, amount and meal group in Cronometer.

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
- Source-aware ranking policy: identity/preparation first, then database preference. Live source mappings remain unverified; demo policy tests do not establish live database prioritization.
- Verified product aliases, including Hebrew names, persisted across restarts.
- Validation of structured label data, including serving basis, kJ/kcal, mass units, salt equivalent, and explicit density for ml-to-g conversion. The assistant reads the image; the backend validates extracted values.
- Nutrient subtotals with missing-data coverage; absent values never become zero.
- A persistent simulated diary and operation journal. Repeated commits reuse the same meal operation; a new identical meal is still valid.
- Durable markers before writes, read-back verification, and reconciliation after an ambiguous timeout. No claim of exactly-once delivery against Cronometer.
- A read-only adapter interface around the pinned existing Cronometer client, tested with synthetic response fixtures.
- Separate real-food inspection tools with hidden local login, one-time 2FA, private session storage and blocked diary writes. See the [read-only guide](docs/cronometer-read-only.md).
- A pure recipe-portion calculation using finished batch weight. Saved recipe versions and native recipe writes are future work.

## Demo tool workflow

1. `begin_meal` creates a draft; preparing a dish does not log consumption.
2. `resolve_food` returns candidates with food IDs, sources, and nutrient profiles.
3. `update_meal_draft` replaces the complete item list using the current revision. Record estimates and unresolved questions explicitly.
4. `get_meal_review` returns suggested questions and known nutrient totals with coverage.
5. Once the user identifies the consumed portion, set `intent=eaten` and review again.
6. `commit_meal` writes only to the simulated diary. It reports committed only after read-back checks.
7. `get_logging_status` lets a later session resume the same draft or inspect an unfinished operation.
8. `delete_meal_draft` discards an unlogged draft when the user asks. It cannot undo diary entries.

Photo-based product entry uses `propose_product_from_label` followed by `save_verified_product`. In demo mode the product is saved in the local demo catalog. `remember_product` records an alias only after the user establishes the preference.

See [meal item fields and selected-food logging](docs/meal-draft-input.md) when
resuming a dated draft or leaving an unresolved food for later.

## Development and ownership

Our application is independent of the upstream connector. Only the optional `live` extra depends on [rollecode/cronometer-mcp](https://github.com/rollecode/cronometer-mcp), pinned to commit `e123a4721093efa55adac665e10a546c105eb117`. Nothing from that project has been copied into this source tree. Live login uses hidden local terminal prompts. The meals MCP mode exposes reviewed serving writes through the account-bound diary workflow.

Use short branches and pull requests. CI checks formatting/lint, domain tests, package installation, and a real SDK/stdio round trip. Test fixtures need no food/health data, credentials, or repository secrets. Read [development notes](docs/development.md) and the [implementation plan](docs/implementation-plan.md).

GitHub stores source and synthetic fixtures. Runtime SQLite files, credentials, session tokens, and package-label photographs remain outside the repository.

## Review fixes and private custom foods

The [implementation review](docs/review-and-products.md) records the label,
unit, duplication, and creation-recovery fixes and the product-mode test procedure.
Label carbs now require an explicit fiber convention; raw values and sugar alcohols
are preserved. The current read-only connection stays read-only.

After updating, `--mode meals` includes reviewed private-food creation alongside
meal logging, with account-bound journals and read-back checks. An exact product
missing from search can be prepared from its label, saved privately, verified,
and added to a separately reviewed meal in the same conversation. The narrower
`--mode products` remains available without diary access. Neither mode can edit
existing foods, link native barcodes, or publish to CRDB. The public
submission tool is a checklist, not a submission API. A controlled private-food creation and UI correction have been exercised; automated
tests use synthetic data and do not create real foods.

See the [development PR map](docs/pr-map.md) for the next review and test sequence.


## Pilot guides

The merged pilot includes [private product evidence](docs/product-evidence.md), [manual food repair reconciliation](docs/product-repairs.md), [local drafts using real foods](docs/live-drafts.md) and [backup/restore](docs/backups.md). The [PR map](docs/pr-map.md) records current status, priorities and acceptance tests; the [upstream comparison](docs/upstream-comparison.md) inventories the original connector and our intentional omissions.
