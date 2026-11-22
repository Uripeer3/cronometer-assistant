# Setup, Configuration & Architecture Notes

## 1. Connecting Your AI Client

Choose the setup for the client you use (you can configure more than one):

| # | AI Client | How it connects | Auto-start on boot? |
|---|---|---|---|
| **1** | **ChatGPT** (web, mobile, desktop) | OpenAI MCP Tunnel (`scripts/run_tunnel.py`) | Recommended (optional) |
| **2** | **Claude.ai** (web, mobile) or **remote Gemini** | Local `streamable-http` + your HTTPS auth proxy | Recommended (optional) |
| **3** | **Local apps** (Claude Desktop, Claude Code, Gemini CLI, Cursor) | Local `stdio` (app manages the server) | Not needed |

---

### 1. ChatGPT (Web, Mobile, or Desktop)

Uses OpenAI's official `tunnel-client` to run this MCP server on your computer and connect outbound to ChatGPT—no firewall ports needed.

**Connect:**
1. Create a private tunnel at [OpenAI Platform → Tunnels](https://platform.openai.com/organization/tunnels) (copy its `tunnel_…` ID) and an API key with **Tunnels Read + Use** permissions ([guide](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels), [onboarding](https://github.com/openai/tunnel-client/blob/master/docs/onboarding.md)).
2. Run this in a terminal to download `tunnel-client`, save the key in `.runtime/tunnel-api-key`, and connect:
   ```bash
   python3 scripts/run_tunnel.py --tunnel-id YOUR_TUNNEL_ID --save-key
   ```
3. In ChatGPT's custom MCP settings, select your tunnel and verify tools load.

**Keep running after reboots (optional, Linux `systemd`):**
Running `run_tunnel.py` in a terminal stops whenever you close the window or reboot. To make ChatGPT stay connected automatically whenever your computer is powered on:

1. Create `.runtime/profile.yaml` (mode `0600`):
   ```json
   {
     "config_version": 1,
     "control_plane": {
       "api_key": "file:/abs/path/.runtime/tunnel-api-key",
       "base_url": "https://api.openai.com",
       "tunnel_id": "YOUR_TUNNEL_ID"
     },
     "mcp": {
       "commands": [
         {
           "channel": "main",
           "command": "/abs/path/.venv/bin/python -m cronometer_assistant serve --db /abs/path/data/assistant.sqlite --session /abs/path/data/cronometer-session/session.json"
         }
       ]
     }
   }
   ```
2. Create `~/.config/systemd/user/cronometer-assistant.service`:
   ```ini
   [Unit]
   Description=Cronometer MCP assistant
   After=network-online.target
   Wants=network-online.target

   [Service]
   Type=simple
   WorkingDirectory=/abs/path
   ExecStart=/abs/path/.runtime/tunnel-client run --profile-file /abs/path/.runtime/profile.yaml
   Restart=on-failure
   RestartSec=10
   UMask=0077

   [Install]
   WantedBy=default.target
   ```
3. Enable and start it with the [service commands below](#managing-the-systemd-service).

---

### 2. Claude.ai (Web/Mobile) or Remote Gemini

Runs this MCP server as a local HTTP endpoint (`http://127.0.0.1:8765/mcp`, 24-hour idle session timeout) behind your own authenticating HTTPS proxy.

**Connect:**
1. Start the local HTTP server:
   ```bash
   .venv/bin/python -m cronometer_assistant serve --transport streamable-http --host 127.0.0.1 --port 8765
   ```
2. Put an OAuth 2.1 proxy in front of `http://127.0.0.1:8765/mcp` (see the [MCP Authorization guide](https://modelcontextprotocol.io/docs/concepts/authorization) or [`rollecode/cronometer-mcp`](https://github.com/rollecode/cronometer-mcp)'s `auth-server.js` + Nginx example) and expose it over HTTPS (for example via [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/)). Never bind `--host 0.0.0.0` directly without authentication.
3. Add your HTTPS `/mcp` URL as a custom connector in Claude.ai ([Remote MCP docs](https://platform.claude.com/docs/en/agents-and-tools/remote-mcp-servers)) or Gemini.

**Keep running after reboots (optional, Linux `systemd`):**
To keep the HTTP server up automatically whenever your computer powers on or restarts, create `~/.config/systemd/user/cronometer-assistant.service` using the same `[Unit]` / `[Install]` sections as above with:

```ini
[Service]
Type=simple
WorkingDirectory=/abs/path
ExecStart=/abs/path/.venv/bin/python -m cronometer_assistant serve --transport streamable-http --host 127.0.0.1 --port 8765 --db /abs/path/data/assistant.sqlite --session /abs/path/data/cronometer-session/session.json
Restart=on-failure
RestartSec=10
UMask=0077
```

Then enable and start it with the [service commands below](#managing-the-systemd-service).

#### Managing the `systemd` service

```bash
systemctl --user daemon-reload
systemctl --user enable --now cronometer-assistant.service   # start now & on future logins
loginctl enable-linger "$USER"                               # optional: start on boot before desktop login
```

Check status, restart after code updates (`git pull --ff-only && uv sync --locked --extra mcp --extra live --no-dev`), or view logs:

```bash
systemctl --user status cronometer-assistant.service
systemctl --user restart cronometer-assistant.service
journalctl --user -u cronometer-assistant.service -n 50 --no-pager
```

After restarting for an update, refresh tools in your AI client and start a new chat.

---

### 3. Local Apps — Claude Desktop, Claude Code, Gemini CLI, Cursor

Local apps launch and stop this MCP server automatically on demand via `stdio`—no background service is needed.

**Connect:** Add this to your app's MCP configuration file:

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

---

## 2. Tool Tiers

Pass an optional flag to `serve` to control which tool groups are exposed:

| Flag | Exposed Tools | Count |
|---|---|---:|
| *(default)* | **Core:** `check_connection`, `get_food_log`, `search_foods`, `get_food_details`, `get_my_foods`, `list_nutrients`, `log_food_entries`, `edit_food_entry`, `remove_food_entries`, `create_custom_food`, `update_custom_food`, `retire_custom_food`, `save_recipe`, `remember_food_alias`, `delete_food_alias`, `save_meal_draft`, `list_meal_drafts`, `delete_meal_draft`, `get_result_page`<br>**Extras:** `mark_day_complete`, `copy_day`, `find_entries_by_food`, `add_note`, `edit_note`, `get_targets`, `set_nutrient_target`, `get_macro_targets`, `get_nutrition_scores`, `get_streak` | **29** |
| `--core-only` | Core tools only | **19** |
| `--include-tracking` | Default + **Tracking:** `list_biometrics`, `get_biometrics`, `add_biometric`, `edit_biometric`, `remove_biometrics`, `add_exercise`, `edit_exercise`, `remove_exercises`, `get_fasting_history`, `get_fasting_stats`, `add_fast`, `edit_fast`, `delete_fast` | **42** |

---

## 3. Security & Reliability Choices

- **Private local login:** `login` prompts with hidden terminal input and writes only `user_id`, the session token, and account timezone to `data/cronometer-session/session.json` (`0600` in a `0700` directory). Passwords and 2FA codes are never stored; HTTP/token loggers are disabled.
- **Minimal profile exposure:** `check_connection` returns only timezone, today's date, and nutrition feature flags—never email, birthdate, or third-party integration tokens.
- **Verified writes & safe retries:** Every diary add, edit, and delete is read back from Cronometer and checked against the requested food, grams, measure, group, and time. Passing `request_id` journals each step in SQLite before writing so interrupted batches reconcile without duplicating entries.
- **Measure & label safety:** Custom-food edits preserve existing `measureId`s so past diary entries stay intact. Missing nutrients stay unknown (never `0`), and EU/UK/AU/NZ/Israeli labels (`excludes_fiber`) require explicit fiber so total carbohydrates are stored accurately in Cronometer.

---

## 4. Languages & Custom Label Aliases

All text handling is Unicode-native, so conversations, food searches, and personal aliases work in any language. Your AI maps nutrition labels to Cronometer's canonical nutrient names automatically.

To also accept raw localized label headings directly in `create_custom_food` / `update_custom_food`, add entries to `LABEL_ALIASES` in `src/cronometer_assistant/nutrition.py` (keys are case-folded with spaces replaced by `_`; English and Hebrew are included):

```python
LABEL_ALIASES = {
    ...
    "eiweiß": "protein",
    "kohlenhydrate": "carbs",
    "gesättigte_fettsäuren": "saturated",
    "salz": "salt",
}
```

---

## 5. Local Data & Troubleshooting

Private runtime files live under `data/` (git-ignored):
- `data/cronometer-session/session.json` — Cronometer session token.
- `data/assistant.sqlite` — Saved aliases, write-dedup journal, and optional meal drafts (bound to one Cronometer account). Back up anytime with:
  ```bash
  sqlite3 data/assistant.sqlite ".backup /path/to/backup-assistant.sqlite"
  ```

Common fixes:
- **Session expired:** Run `.venv/bin/python -m cronometer_assistant login` and restart the server.
- **Permission error on session path:** Run `chmod 700 data/cronometer-session && chmod 600 data/cronometer-session/session.json`.
- **Stale tools in ChatGPT:** Restart the service, click refresh on the connector, and start a new chat.
