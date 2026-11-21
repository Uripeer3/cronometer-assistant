# Setup, Configuration & Architecture Notes

## 1. Connecting Your AI Client

### ChatGPT (OpenAI MCP Tunnel)

Run in a terminal:

```bash
python3 scripts/run_tunnel.py --tunnel-id YOUR_TUNNEL_ID --save-key
```

For a background Linux user service (`systemd`), point `.runtime/profile.yaml`'s MCP command at:

```text
/abs/path/.venv/bin/python -m cronometer_assistant serve --db /abs/path/data/assistant.sqlite --session /abs/path/data/cronometer-session/session.json
```

Useful commands:

```bash
systemctl --user status cronometer-assistant.service
systemctl --user restart cronometer-assistant.service
journalctl --user -u cronometer-assistant.service -n 50 --no-pager
```

After updating code (`git pull --ff-only && uv sync --locked --extra mcp --extra live --no-dev`), restart the service, refresh tools in ChatGPT, and start a new chat.

### Claude Code, Claude Desktop, Gemini CLI, Cursor (`stdio`)

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
