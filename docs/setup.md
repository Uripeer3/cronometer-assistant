# Setup, Configuration & Architecture Notes

## 1. Connecting Your AI Client

Start by setting up and testing whichever client(s) you use interactively in a terminal. Once that works, see [Section 2](#2-keeping-it-always-running-across-reboots-systemd) to keep remote clients reachable automatically after every reboot.

### ChatGPT (OpenAI MCP Tunnel)

First, create a private tunnel in [OpenAI Platform → Tunnels](https://platform.openai.com/organization/tunnels) (copy its `tunnel_…` ID) and create a runtime API key with **Tunnels Read + Use** permissions (see OpenAI's [Secure MCP Tunnels guide](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels) and [tunnel-client onboarding](https://github.com/openai/tunnel-client/blob/master/docs/onboarding.md)).

Then run this in a terminal to save your tunnel key and connect (then select the tunnel in ChatGPT's custom MCP settings):

```bash
python3 scripts/run_tunnel.py --tunnel-id YOUR_TUNNEL_ID --save-key
```

### Claude Code, Claude Desktop, Gemini CLI, Cursor (`stdio`)

Add this to your client's MCP configuration. Local `stdio` clients start and stop the server on demand themselves, so they do not need a background service:

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

### Claude.ai Web/Mobile or Remote Gemini (`streamable-http`)

```bash
.venv/bin/python -m cronometer_assistant serve --transport streamable-http --host 127.0.0.1 --port 8765
```

This serves MCP at `http://127.0.0.1:8765/mcp` with a 24-hour idle session timeout. Place an authenticating HTTPS proxy (such as an OAuth 2.1 proxy + Cloudflare Tunnel) in front of it—never expose `--host 0.0.0.0` directly without authentication.

---

## 2. Keeping It Always Running Across Reboots (`systemd`)

Remote AI clients (ChatGPT via the MCP tunnel, or web/mobile apps using `streamable-http`) can only reach your assistant while the server process is running on your computer. Running it manually in a terminal stops as soon as you close the terminal, crash, or reboot.

Setting up a Linux user `systemd` service starts the MCP server automatically whenever your computer powers up or restarts—and restarts it automatically if it ever exits—so your agents can always reach Cronometer as long as your computer is on, with no terminal window needed. *(Local `stdio` clients like Claude Code or Gemini CLI don't need this step.)*

### Option A: Auto-start the ChatGPT MCP Tunnel

1. After running `run_tunnel.py --tunnel-id YOUR_TUNNEL_ID --save-key` once, create `.runtime/profile.yaml` (mode `0600`):
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
2. Install `~/.config/systemd/user/cronometer-assistant.service`:
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

### Option B: Auto-start `streamable-http` instead

Use the same `~/.config/systemd/user/cronometer-assistant.service` file, replacing `ExecStart` with:

```ini
ExecStart=/abs/path/.venv/bin/python -m cronometer_assistant serve --transport streamable-http --host 127.0.0.1 --port 8765 --db /abs/path/data/assistant.sqlite --session /abs/path/data/cronometer-session/session.json
```

### Enable, start, and manage the service

```bash
systemctl --user daemon-reload
systemctl --user enable --now cronometer-assistant.service
systemctl --user status cronometer-assistant.service
```

Optional—start on boot even before you log into a desktop session:

```bash
loginctl enable-linger "$USER"
```

Useful day-to-day commands:

```bash
systemctl --user restart cronometer-assistant.service
journalctl --user -u cronometer-assistant.service -n 50 --no-pager
```

After updating code (`git pull --ff-only && uv sync --locked --extra mcp --extra live --no-dev`), restart the service, refresh tools in ChatGPT, and start a new chat.

---

## 3. Tool Tiers

Pass an optional flag to `serve` to control which tool groups are exposed:

| Flag | Exposed Tools | Count |
|---|---|---:|
| *(default)* | **Core:** `check_connection`, `get_food_log`, `search_foods`, `get_food_details`, `get_my_foods`, `list_nutrients`, `log_food_entries`, `edit_food_entry`, `remove_food_entries`, `create_custom_food`, `update_custom_food`, `retire_custom_food`, `save_recipe`, `remember_food_alias`, `delete_food_alias`, `save_meal_draft`, `list_meal_drafts`, `delete_meal_draft`, `get_result_page`<br>**Extras:** `mark_day_complete`, `copy_day`, `find_entries_by_food`, `add_note`, `edit_note`, `get_targets`, `set_nutrient_target`, `get_macro_targets`, `get_nutrition_scores`, `get_streak` | **29** |
| `--core-only` | Core tools only | **19** |
| `--include-tracking` | Default + **Tracking:** `list_biometrics`, `get_biometrics`, `add_biometric`, `edit_biometric`, `remove_biometrics`, `add_exercise`, `edit_exercise`, `remove_exercises`, `get_fasting_history`, `get_fasting_stats`, `add_fast`, `edit_fast`, `delete_fast` | **42** |

---

## 4. Security & Reliability Choices

- **Private local login:** `login` prompts with hidden terminal input and writes only `user_id`, the session token, and account timezone to `data/cronometer-session/session.json` (`0600` in a `0700` directory). Passwords and 2FA codes are never stored; HTTP/token loggers are disabled.
- **Minimal profile exposure:** `check_connection` returns only timezone, today's date, and nutrition feature flags—never email, birthdate, or third-party integration tokens.
- **Verified writes & safe retries:** Every diary add, edit, and delete is read back from Cronometer and checked against the requested food, grams, measure, group, and time. Passing `request_id` journals each step in SQLite before writing so interrupted batches reconcile without duplicating entries.
- **Measure & label safety:** Custom-food edits preserve existing `measureId`s so past diary entries stay intact. Missing nutrients stay unknown (never `0`), and EU/UK/AU/NZ/Israeli labels (`excludes_fiber`) require explicit fiber so total carbohydrates are stored accurately in Cronometer.

---

## 5. Languages & Custom Label Aliases

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

## 6. Local Data & Troubleshooting

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
