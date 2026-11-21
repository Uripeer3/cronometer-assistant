# Setup, Client Configuration & Operations

## 1. Prerequisites & Install

- Linux or macOS
- Python 3.12+ and [`uv`](https://docs.astral.sh/uv/)
- A Cronometer account (free or Gold)

```bash
uv sync --locked --extra mcp --extra live --no-dev
```

## 2. Authenticate with Cronometer

Run once in an interactive terminal:

```bash
.venv/bin/python -m cronometer_assistant login
.venv/bin/python -m cronometer_assistant check
```

How authentication stays safe:
- Email, password, and 2FA code are read with hidden terminal input and discarded immediately after login.
- Only `user_id`, the session token, and your account timezone are written atomically to `data/cronometer-session/session.json` (`0600` file inside a `0700` directory).
- Your account timezone is synced automatically from Cronometer's profile on use.
- If a session expires, run `.venv/bin/python -m cronometer_assistant login` again—never enter credentials in chat.

---

## 3. Connecting AI Clients

### ChatGPT (OpenAI MCP Tunnel + systemd)

Run interactively in a terminal:

```bash
python3 scripts/run_tunnel.py --tunnel-id YOUR_TUNNEL_ID --save-key
```

The launcher downloads and SHA-256 verifies OpenAI's official `tunnel-client`, saves the runtime API key privately under `.runtime/tunnel-api-key` (`0600`), and bridges ChatGPT to `cronometer-assistant serve`.

If you run it as a background user service via systemd (`.runtime/cronometer-assistant.service` + `.runtime/profile.yaml`):

- Point `.runtime/profile.yaml`'s `mcp.commands[0].command` at:
  ```text
  /abs/path/.venv/bin/python -m cronometer_assistant serve --db /abs/path/data/assistant.sqlite --session /abs/path/data/cronometer-session/session.json
  ```
- Day-to-day service commands:
  ```bash
  systemctl --user status cronometer-assistant.service
  systemctl --user restart cronometer-assistant.service
  journalctl --user -u cronometer-assistant.service -n 50 --no-pager
  ```
- Updating to a newer version:
  ```bash
  git pull --ff-only
  uv sync --locked --extra mcp --extra live --no-dev
  systemctl --user restart cronometer-assistant.service
  ```
  After restarting, refresh the connector's tools in ChatGPT and open a fresh chat so the updated tool schemas are active.

### Claude Code, Claude Desktop, Gemini CLI, Cursor (Local `stdio`)

Any local MCP client connects directly over `stdio`:

```json
{
  "mcpServers": {
    "cronometer": {
      "command": "/abs/path/to/cronometer-assistant/.venv/bin/python",
      "args": [
        "-m",
        "cronometer_assistant",
        "serve",
        "--db",
        "/abs/path/to/cronometer-assistant/data/assistant.sqlite",
        "--session",
        "/abs/path/to/cronometer-assistant/data/cronometer-session/session.json"
      ]
    }
  }
}
```

Optional flags you can append to `args`:
- `"--core-only"` — expose only the 19 core meal-logging tools.
- `"--include-tracking"` — also expose the 13 biometrics, exercise, and fasting tools.

### Claude.ai Web/Mobile or Remote Gemini (`streamable-http`)

Run the server over Streamable HTTP on localhost (configured with a 24-hour idle session timeout and a thread-safe Cronometer client):

```bash
.venv/bin/python -m cronometer_assistant serve \
  --transport streamable-http \
  --host 127.0.0.1 \
  --port 8765
```

The MCP endpoint is served at `http://127.0.0.1:8765/mcp`. Put an authenticating HTTPS proxy (e.g., an OAuth 2.1 proxy + Cloudflare Tunnel for Claude.ai, or a bearer-token reverse proxy for remote Gemini/Claude Code) in front of it. Do not expose `--host 0.0.0.0` directly without authentication.

---

## 4. Available Tools by Tier

| Tier | Tools |
|---|---|
| **Core (always enabled, 18 + `get_result_page`)** | `check_connection`, `get_food_log`, `search_foods`, `get_food_details`, `get_my_foods`, `list_nutrients`, `log_food_entries`, `edit_food_entry`, `remove_food_entries`, `create_custom_food`, `update_custom_food`, `retire_custom_food`, `save_recipe`, `remember_food_alias`, `delete_food_alias`, `save_meal_draft`, `list_meal_drafts`, `delete_meal_draft`, `get_result_page` |
| **Nutrition & Diary Extras (enabled by default; disable with `--core-only`, 10)** | `mark_day_complete`, `copy_day`, `find_entries_by_food`, `add_note`, `edit_note`, `get_targets`, `set_nutrient_target`, `get_macro_targets`, `get_nutrition_scores`, `get_streak` |
| **Non-Food Tracking (opt-in with `--include-tracking`, 13)** | `list_biometrics`, `get_biometrics`, `add_biometric`, `edit_biometric`, `remove_biometrics`, `add_exercise`, `edit_exercise`, `remove_exercises`, `get_fasting_history`, `get_fasting_stats`, `add_fast`, `edit_fast`, `delete_fast` |

---

## 5. Local Data & Troubleshooting

All private state stays outside Git under `data/`:
- `data/cronometer-session/session.json` — Cronometer session token (`0600`).
- `data/assistant.sqlite` — Personal food aliases, `request_id` write deduplication, and optional cooking drafts (`0600`, account-bound).

**Backup:** Stop the service (or use SQLite's online backup) and copy `data/assistant.sqlite`:
```bash
sqlite3 data/assistant.sqlite ".backup /path/to/backup-assistant.sqlite"
```

**Common issues:**
- *"Cronometer session is missing or expired"*: Run `.venv/bin/python -m cronometer_assistant login`, then restart the service.
- *"Session directory must be owned by you with mode 0700"*: Run `chmod 700 data/cronometer-session && chmod 600 data/cronometer-session/session.json`.
- *ChatGPT shows old tool names after an update*: Restart `cronometer-assistant.service`, click refresh on the connector in ChatGPT, and start a new conversation.

---

## 6. Localization & Architecture Notes

### Adding Label Aliases for Other Languages

The assistant works in **any language**—Unicode food names, personal aliases, and Cronometer search work out of the box, and AI hosts automatically translate nutrition-label headings to Cronometer's canonical English keys (`protein`, `carbs`, `saturated`, `sodium`, etc.).

If you also want `create_custom_food` / `update_custom_food` to accept raw label headings directly in your language (English and Hebrew are included), add normalized entries to `LABEL_ALIASES` in `src/cronometer_assistant/nutrition.py`:

```python
LABEL_ALIASES = {
    ...
    "eiweiß": "protein",
    "kohlenhydrate": "carbs",
    "gesättigte_fettsäuren": "saturated",
    "salz": "salt",
}
```

Keys are matched after `normalize_key()` (Unicode `NFKC`, case-folded, spaces replaced with `_`).

### Security & Architecture Choices

- **No stored passwords or TOTP seeds:** Unlike headless env-var setups, `login` stores only the session token (`0600`), preventing plaintext account credentials from sitting on disk.
- **No raw profile PII in MCP:** `check_connection` exposes only account timezone and boolean nutrition settings (`net_carbs_enabled`, `diary_groups_enabled`, `timestamps_enabled`, `gold`), omitting email, birthdate, and third-party integration tokens.
- **Localhost-only HTTP:** `--transport streamable-http` binds `127.0.0.1` by default and keeps auth out of the Python process so you can pair it with your preferred reverse proxy or OAuth 2.1 layer without bundling Node/Nginx here.
- **Measure-ID & read-back safety:** Diary writes verify every entry against Cronometer after writing, custom-food edits refuse to drop existing `measureId`s (which would corrupt past diary entries), and `state.py` binds SQLite to one `user_id` with `fcntl` + WAL locking.
