# Real Cronometer food-data check

The user's initial Hebrew/English conversational demo passed through ChatGPT.
This next mode inspects real Cronometer foods. Its three MCP tools cannot create
drafts, save products, or read/write diary entries. The demo remains separate.

## Sign in privately

Run from this checkout in your normal terminal:

```bash
uv sync --locked --extra mcp --extra live --no-dev
.venv/bin/python -m cronometer_assistant login
.venv/bin/python -m cronometer_assistant check-cronometer
```

Email and password use hidden terminal prompts. If Cronometer requests 2FA, enter
a current authenticator code. Keep 2FA enabled; do not share its permanent secret
or paste credentials into chat. Non-TTY login refuses to prompt. This uses the
pinned unofficial mobile-API client; actual account compatibility remains untested.

Only the reusable session token, account ID and timezone are saved in
`data/cronometer-session/session.json`. Its directory must be private (0700); the
file is atomically written as 0600. Email, password and 2FA code are not saved.
This is a local token file, not an OS-keyring entry. Deleting it ends local reuse,
but does not revoke the remote session. Runtime files are Git-ignored.

Upstream credential and timezone environment overrides are ignored. Login sends
a null timezone so it does not overwrite the account setting. Missing/invalid
account timezone stops the check rather than silently choosing another zone.
The MCP server never prompts or logs in automatically. On expiry, stop the server,
run login locally, and restart it.

## Switch the tunnel to read-only mode

Stop the existing demo launcher with Ctrl+C first. Never run two clients for the
same tunnel. Then run:

```bash
python3 scripts/connect_demo.py --tunnel-id YOUR_TUNNEL_ID --mode read-only
```

Enter the restricted OpenAI runtime key privately as before. It authenticates
the tunnel and is separate from the Cronometer session. The launcher installs the
locked live extra and selects the separate `serve-read-only` server. Keep the
terminal open, then refresh the existing ChatGPT connection's tool list. Keep
application authentication set to No authentication; OAuth is not implemented.
Expected tools:

- `check_cronometer_connection`
- `search_cronometer_foods`
- `inspect_cronometer_food`

If demo tools are still advertised, refresh the connection before testing.
To return to demo mode, stop the launcher, run without `--mode read-only`, and
refresh tools again. Neither switch alters the existing demo database.

## Compare with the app

Ask: "Check the Cronometer connection, search for cooked white rice, and inspect
one matching entry. Do not log anything."

Repeat with dry rice and an exact packaged product you use. Hebrew searches may
need an English translation. Compare the same food ID/name, preparation, database
source, grams per serving and nutrient amounts against the entry in the app.
Selected food fields are returned; raw account responses, diary entries, email
and tokens are not returned to ChatGPT.

Source codes remain unverified; do not guess NCCDB/USDA mappings. Raw nutrient
rows preserve zero, missing amounts and original units including IU. The per-100-g
basis follows the pinned client and still needs live comparison. This diagnostic
mode does not calculate meal nutrition or make foods loggable in drafts.

The HTTP allowlist blocks inherited upstream writes and all v3 requests before
transport. Only login, food search, food details and nutrient definitions are
allowed. Upstream exception payloads and token-prefix logging are suppressed.
Tests use synthetic credentials and mocked HTTP with the real pinned client;
they do not establish that this account's login or food data works.

After comparison, implement an explicitly scoped add/read-back/correct test on a
user-selected diary date. This change does not enable that capability.

## Demo draft deletion

The original demo had no deletion tool: a conversational deletion claim did not
prove deletion from storage. `delete_meal_draft` now removes a specific unlogged
draft at its current revision. Any logging operation, including partial/uncertain
writes, blocks deletion. Diary entries, products and aliases are unaffected.
Report success only after the tool returns `deleted=true`.
