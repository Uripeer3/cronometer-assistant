# Cronometer Assistant

An MCP server that lets you log and manage your [Cronometer](https://cronometer.com) nutrition by talking to an AI assistant (**ChatGPT**, **Claude**, or **Gemini**) while you cook or eat.

## Goal

Logging food manually takes too many taps, unit conversions, and label transcriptions. This server lets your AI handle natural conversation (in any language) and nutrition-label photos, while Python handles accurate Cronometer API calls, unit math, label conventions, and write verification.

Built on [`rollecode/cronometer-mcp`](https://github.com/rollecode/cronometer-mcp)'s mobile API client, it works with free or Gold Cronometer accounts.

## Example Conversations

- *"For lunch I had 150g cooked white rice, 2 large eggs, and 1 tbsp olive oil."*
- *"Log my usual yogurt, 200g."*
- *"What did I eat today and how many calories and protein do I have left?"*
- *"Change the rice to 120g and move it to dinner at 19:15."*
- *(Attach a package photo)* *"Add this to my custom foods."*
- *"Save this chicken & rice batch as a recipe, 950g cooked, 4 servings."*

## Quick Start

Requires Python 3.12+ and [`uv`](https://docs.astral.sh/uv/).

```bash
# Install dependencies
uv sync --locked --extra mcp --extra live --no-dev

# Sign in once privately in your terminal (hidden email, password, and 2FA)
.venv/bin/python -m cronometer_assistant login
.venv/bin/python -m cronometer_assistant check

# Run over stdio (Claude Code, Claude Desktop, Gemini CLI, Cursor)
.venv/bin/python -m cronometer_assistant serve

# Or connect to ChatGPT via OpenAI's MCP tunnel
python3 scripts/run_tunnel.py --tunnel-id YOUR_TUNNEL_ID --save-key
```

See [docs/setup.md](docs/setup.md) for ChatGPT, Claude, and Gemini configs, Streamable HTTP setup, tool tiers, security details, and language customization.

## Architecture

```text
AI Host (ChatGPT / Claude / Gemini) ──MCP──▶ cronometer-assistant ──HTTPS──▶ mobile.cronometer.com
```

The codebase separates everyday meal logging from optional features and MCP wiring:

```text
src/cronometer_assistant/
├── auth.py              # Private terminal login, 0600 session file, thread-safe client
├── nutrition.py         # Label normalization (EU/US carbs, kJ, salt, ml density), barcodes & measures
├── state.py             # Single SQLite DB (food aliases, retry dedup, optional cooking drafts)
├── core/                # Everyday meal logging (foods, diary, custom foods & recipes)
├── extras/              # Isolated extras (day utilities, targets/scores, biometrics/exercise/fasting)
├── tools/               # MCP tool definitions split by tier (core, extras, tracking)
├── service.py           # Facade composing core + extras
├── server.py            # FastMCP server (stdio & Streamable HTTP)
└── cli.py               # CLI: login | check | serve
```

## Development

```bash
uv sync --locked --extra mcp --extra live --group dev
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked python -m unittest discover -s tests -v
```
