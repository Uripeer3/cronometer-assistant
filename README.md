# Cronometer Assistant

A simple, thorough **MCP server** for logging nutrition in [Cronometer](https://cronometer.com) through a conversation with an AI assistant (**ChatGPT**, **Claude**, or **Gemini**) while you cook or eat.

## Goal

Manually logging meals in Cronometer takes a lot of clicking, unit math, and label transcription—especially while cooking or dealing with Israeli/EU nutrition labels.

This project splits the work cleanly:
- **Your AI assistant** handles natural conversation in any language, voice input, and reading nutrition-label photos.
- **This MCP server** handles accurate Cronometer API calls, unit/measure conversions, EU/US carbohydrate math, read-back verification, and lightweight personal memory.

It builds on [`rollecode/cronometer-mcp`](https://github.com/rollecode/cronometer-mcp)'s Android mobile API client (`mobile.cronometer.com`), works with free or Gold Cronometer accounts, and avoids complex multi-mode workflows.

## Everyday Usage

Once connected, you just talk to your assistant naturally in any language:

- **Log while cooking:** *"For lunch I'm having 150g cooked white rice, 2 large eggs, and 1 tbsp olive oil."* — Logs all items in one verified call, converting household units (`large`, `tbsp`, `pieces`, `cup`) using Cronometer's exact measures.
- **Log usuals fast:** *"Log my usual yogurt, 200g."* — Checks your saved personal aliases, recent foods, and custom foods in one lookup.
- **Check your day:** *"What did I eat today and how many calories and protein do I have left?"* — Returns every logged food (including entries added in the Cronometer app), calorie target/consumed/remaining, and both tracked and untracked nutrients.
- **Fix mistakes:** *"Change that rice to 120g and move it to dinner at 19:15"* or *"Delete the yogurt."* — Edits amount, measure, time, meal group, or date on any serving, or removes multiple servings at once.
- **Add missing packaged foods (EU/UK/AU/Israeli or US labels):** Send a label photo and say *"Add this to my custom foods."* — Supports all 94 Cronometer nutrients, localized label aliases, EU/international `excludes_fiber` vs US `includes_fiber` carb conversion, `Weight` measures (`serving` + `g`), and native Cronometer barcode linking.
- **Batch cooking:** *"Save this chicken & rice batch as a recipe, final cooked weight is 950g, 4 servings."* — Adjusts per-100g nutrition for water loss during cooking.

## Architecture

```text
  AI Host (ChatGPT / Claude / Gemini)
         │  MCP (stdio or Streamable HTTP)
         ▼
  cronometer-assistant
  ├── tools/        Flat MCP schemas split into core, extras, and tracking tiers
  ├── core/         Meal logging, food discovery, custom foods & recipes
  ├── extras/       Day management, targets, scores, streaks, biometrics/exercise/fasting
  ├── nutrition.py  Pure label math (EU/US carbs, kJ, salt, ml density, exact value/amount measures)
  ├── state.py      Single local SQLite DB (aliases, request_id dedup, optional cooking drafts)
  └── auth.py       Private terminal login, mode-0600 session file, thread-safe client
         │  HTTPS (Cronometer mobile API via cronometer-mcp client)
         ▼
  mobile.cronometer.com
```

### Core Design & Security Choices

1. **Complete hooks, no silent gaps:** Batch food enrichment (`get_foods`), exact multi-unit measure math (`grams_per_unit = value / amount`), Weight + Atomic + Recipe logging, native barcodes, and measure-ID preservation on custom-food edits.
2. **Safe writes by default:** Every diary add, edit, and delete is verified via read-back against Cronometer. Optional `request_id` deduplication prevents duplicate logs on network retries.
3. **Honest nutrition data:** Missing nutrients stay unknown (never coerced to `0`). EU/international labels (`excludes_fiber`) require explicit fiber so Cronometer never double-subtracts fiber.
4. **Private, minimal-exposure auth:** Login runs interactively in your local terminal (hidden input + 2FA)—passwords and TOTP seeds are never stored on disk or in env vars, token loggers are silenced, `check_connection` omits raw profile PII, and `streamable-http` defaults to `127.0.0.1` so remote exposure is always handled by an explicit authenticating proxy.
5. **Works in any language:** All text normalization (`nutrition.normalize_key`) is Unicode-native (`NFKC`), so food names, searches, aliases, and notes work in any script. To let an AI pass native label headers directly for another language, add entries to `LABEL_ALIASES` in `src/cronometer_assistant/nutrition.py` (English and Hebrew are included out of the box).

## Codebase Layout

```text
src/cronometer_assistant/
├── auth.py              # Private login, mode-0600 session, thread-safe client (RLock)
├── state.py             # Single SQLite store (aliases, idempotency, optional drafts)
├── nutrition.py         # Pure label normalization, extensible LABEL_ALIASES, barcode & measure math
├── parsing.py           # Shared date/time/meal-group/serving-scaling helpers
├── core/                # Core everyday meal logging
│   ├── foods.py         # Connection, batch search, details, my_foods, nutrients, aliases
│   ├── diary.py         # Full-day food_log + targets, batch log, edit, remove, drafts
│   └── custom.py        # Custom foods & recipes
├── extras/              # Cleanly separated secondary features
│   ├── diary_extras.py  # mark_day_complete, copy_day, find_entries_by_food, diary notes
│   ├── targets.py       # Nutrient targets, macro schedules, nutrition scores, streaks
│   └── tracking.py      # Opt-in biometrics, exercise, and fasting
├── tools/               # MCP tool registration split by tier (core / extras / tracking)
├── service.py           # Thin facade composing core + extras
├── server.py            # FastMCP server (stdio + Streamable HTTP, 24h idle timeout)
└── cli.py               # login | check | serve
```

### Tool Tiers

Control how many tools are exposed when running `serve`:

| Flag | Included Tiers | Tool Count |
|---|---|---:|
| *(default)* | Core meal logging (18) + Nutrition & diary extras (10) + Paging (1) | **29** |
| `--core-only` | Core meal logging (18) + Paging (1) | **19** |
| `--include-tracking` | Default (29) + Biometrics, Exercise & Fasting (13) | **42** |

## Quick Start

Requires Python 3.12+ and [`uv`](https://docs.astral.sh/uv/).

```bash
# 1. Install locked dependencies
uv sync --locked --extra mcp --extra live --no-dev

# 2. Authenticate once privately in your terminal (hidden email, password, and optional 2FA)
.venv/bin/python -m cronometer_assistant login

# 3. Verify live connection and account timezone
.venv/bin/python -m cronometer_assistant check

# 4a. Run over local stdio (Claude Code, Claude Desktop, Gemini CLI, Cursor)
.venv/bin/python -m cronometer_assistant serve

# 4b. Run over Streamable HTTP (Claude.ai web/mobile or remote Gemini behind an HTTPS auth proxy)
.venv/bin/python -m cronometer_assistant serve --transport streamable-http --host 127.0.0.1 --port 8765

# 4c. Connect to ChatGPT via OpenAI MCP tunnel
python3 scripts/run_tunnel.py --tunnel-id YOUR_TUNNEL_ID --save-key
```

See [docs/setup.md](docs/setup.md) for full client configuration, systemd auto-start, tool reference, and troubleshooting.

## Development & Tests

```bash
uv sync --locked --extra mcp --extra live --group dev
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked python -m unittest discover -s tests -v
```
