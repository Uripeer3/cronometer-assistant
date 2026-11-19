# Setup, updates and recovery (Fedora / GNOME)

This is the end-to-end guide for the **meals** server: ChatGPT calls your local
assistant through an OpenAI MCP tunnel; the assistant uses your private
Cronometer session to search foods, review meals and make confirmed diary changes.
SQLite holds drafts and the write journal. GitHub holds the code.

**Already using the installed service?** Skip first-time setup. Use the commands
below, [update procedure](#update-the-code), and [troubleshooting](#troubleshooting).
The project location used here is `~/Documents/Projects/cronometer-assistant`.
Keep the same checkout and its `data/` when updating.

## Everyday commands

No `sudo` is needed. Run these as the desktop user who installed the service.

| Task | Command |
| --- | --- |
| Status | `systemctl --user status cronometer-assistant.service --no-pager` |
| Restart | `systemctl --user restart cronometer-assistant.service` |
| Start after a manual stop | `systemctl --user start cronometer-assistant.service` |
| Stop | `systemctl --user stop cronometer-assistant.service` |
| Recent logs | `journalctl --user -u cronometer-assistant.service -n 50 --no-pager` |
| Follow logs; Ctrl+C exits the viewer | `journalctl --user -u cronometer-assistant.service -f` |
| Disable automatic startup and stop | `systemctl --user disable --now cronometer-assistant.service` |
| Enable automatic startup and start | `systemctl --user enable --now cronometer-assistant.service` |

The service starts **when you log in**, survives closing the terminal, and restarts
after a process failure. This does not keep a sleeping/offline laptop reachable or
start it before login. After waking, check readiness if calls fail. Automatic
recovery after sleep/network loss is still awaiting acceptance testing.

Do not also start this tunnel from Ignition, another terminal, another checkout,
or another computer. One tunnel ID must reach one running stdio server.

## First-time setup

### 1. Install and sign in locally

You need Git, Python 3.12+, [uv](https://docs.astral.sh/uv/getting-started/installation/),
and a Linux desktop with a systemd user manager (`flock` is also used below).
Use your normal terminal. For a new checkout:

```bash
mkdir -p ~/Documents/Projects
cd ~/Documents/Projects
git clone https://github.com/Uripeer3/cronometer-assistant.git
cd cronometer-assistant
uv sync --locked --extra mcp --extra live --no-dev
.venv/bin/python -m cronometer_assistant login
.venv/bin/python -m cronometer_assistant check-cronometer
```

Run each command only if the previous one succeeded. For an existing checkout,
skip cloning and `cd` into it. Login prompts hide your Cronometer email/password;
complete 2FA if requested. Expect `connected: true` and
`real_diary_changed: false` from the check. The session is stored privately under
`data/cronometer-session/`; the service reuses it. This is separate from the
OpenAI API key.

### 2. Create the tunnel and runtime key

1. Open [Platform Tunnels](https://platform.openai.com/settings/organization/tunnels).
   Create or reuse a tunnel associated with the ChatGPT workspace you will use.
   Keep its complete `tunnel_…` ID.
2. Under [Runtime API keys](https://platform.openai.com/settings/organization/api-keys),
   create a **Restricted** runtime key with **Tunnels Read + Use**. Its principal
   must have access to that tunnel. An admin key is unnecessary.
3. Enter the key only at the hidden local prompt below. It will be saved in
   `.runtime/tunnel-api-key`, mode 0600. This is a private plaintext file, not
   encrypted storage. Never put it in GitHub, the ChatGPT message, or an OAuth field.

For workspace/CIDR permission details, use the
[official tunnel permissions guide](https://github.com/openai/tunnel-client/blob/master/docs/permissions.md).
Do not invent allowed CIDRs: a restriction must permit the actual intended clients.

### 3. Prepare the local runtime once

Run from the project directory. **Skip this step for an already configured service.**
It creates the profile and launcher used by systemd. It uses the repository's
checksum-verifying official-client installer, and reuses a saved private key if
one exists. It does not save your Cronometer password.

```bash
read -r -p 'Complete tunnel ID: ' crono_tunnel_id
python3 - "$crono_tunnel_id" <<'PY'
import getpass
import json
import os
import shlex
import shutil
import sys
from pathlib import Path
from scripts.connect_demo import acquire_client, load_saved_key, save_key, validate_tunnel_id

os.umask(0o077)
root = Path.cwd().resolve()
if not (root / 'src/cronometer_assistant').is_dir():
    raise SystemExit('Run this from the project directory.')
runtime = root / '.runtime'
runtime.mkdir(mode=0o700, exist_ok=True)
if (runtime / 'profile.yaml').exists() or (runtime / 'start-meals.sh').exists():
    raise SystemExit('Runtime already configured; use the restart/update instructions.')
runtime.chmod(0o700)
tunnel_id = validate_tunnel_id(sys.argv[1].strip())
client = acquire_client(runtime)
shutil.copy2(client, runtime / 'tunnel-client')
(runtime / 'tunnel-client').chmod(0o700)
key_path = runtime / 'tunnel-api-key'
if load_saved_key(key_path) is None:
    save_key(key_path, getpass.getpass('Runtime API key (hidden): '))
command = shlex.join([
    str(root / '.venv/bin/python'), '-m', 'cronometer_assistant', 'serve-meals',
    '--db', str(root / 'data/drafts.sqlite'),
    '--session', str(root / 'data/cronometer-session/session.json'),
])
profile = {
    'config_version': 1,
    'control_plane': {
        'api_key': 'file:' + str(key_path),
        'base_url': 'https://api.openai.com',
        'tunnel_id': tunnel_id,
    },
    'admin_ui': {'open_browser': False},
    'health': {'listen_addr': '127.0.0.1:0', 'url_file': str(runtime / 'health.url')},
    'log': {'level': 'warn', 'format': 'json'},
    'mcp': {'commands': [{'channel': 'main', 'command': command}]},
}
# JSON is valid YAML; the official client requires a supported .yaml filename.
(runtime / 'profile.yaml').write_text(json.dumps(profile, indent=2) + '\n')
(runtime / 'profile.yaml').chmod(0o600)
(runtime / 'start-meals.sh').write_text('''#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
umask 077
exec 9>.runtime/tunnel.lock
flock -n 9 || { echo 'Cronometer tunnel is already running.' >&2; exit 1; }
exec env -u CONTROL_PLANE_API_KEY .runtime/tunnel-client run --profile-file "$PWD/.runtime/profile.yaml"
''')
(runtime / 'start-meals.sh').chmod(0o700)
print('Runtime prepared. Install the user service next.')
PY
```

This profile layout was exercised with official tunnel-client v0.0.21. The
installer downloads the latest official release and verifies its checksum; a
future incompatible client may require following that release's onboarding help.
The daily service uses the installed binary directly, without downloading or
installing dependencies on every login.

### 4. Install the systemd user service

The following unit uses systemd's `%h` (your home directory). If you chose a
different project location, adjust both paths before installing it. Stop any old
foreground tunnel with Ctrl+C first; preserve its data rather than starting a
second copy.

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/cronometer-assistant.service <<'UNIT'
[Unit]
Description=Cronometer meal assistant and MCP tunnel

[Service]
Type=simple
WorkingDirectory=%h/Documents/Projects/cronometer-assistant
ExecStart=/bin/bash %h/Documents/Projects/cronometer-assistant/.runtime/start-meals.sh
Restart=on-failure
RestartSec=10
UMask=0077

[Install]
WantedBy=default.target
UNIT
chmod 600 ~/.config/systemd/user/cronometer-assistant.service
systemctl --user daemon-reload
systemctl --user enable --now cronometer-assistant.service
systemctl --user status cronometer-assistant.service --no-pager
```

`active (running)` immediately after startup only means a process started. Verify
the tunnel and a real tool call next.

### 5. Check readiness

From the project directory, after giving startup a few seconds:

```bash
python3 - <<'PY'
from pathlib import Path
import urllib.request
base = Path('.runtime/health.url').read_text().strip().rstrip('/')
with urllib.request.urlopen(base + '/readyz', timeout=5) as response:
    print('Readiness:', response.status, response.read().decode())
print('Local status page:', base + '/ui')
PY
```

Expect HTTP 200. Open the printed `/ui` URL on the same computer for diagnostics.
The port can change on restart; always reread the file. A foreground
`scripts/connect_demo.py` launch instead writes `data/tunnel-health.url`.
Neither address is the server URL to paste into ChatGPT.

### 6. Connect ChatGPT and test

In ChatGPT settings, open **Apps / Connectors** (the label depends on the client),
then the custom MCP connection form. If the form is unavailable, check your
account/workspace's custom MCP availability or administrator settings first.

- **Name:** `Cronometer meal assistant`.
- **Description:** `Review meals and log confirmed portions to my Cronometer diary.`
- **Connection:** choose **Tunnel**, the workspace associated above, and your tunnel.
  Use the tunnel option rather than a laptop `localhost` URL.
- **Application authentication:** **No authentication** for this stdio server.
  The tunnel still authenticates with the runtime key and workspace permissions;
  the assistant server does not implement OAuth.

Create/install the connection while the service is running. If it already exists,
refresh its tools in the connection settings instead of creating a duplicate.
Start a new chat, select the app/plugin, and send:

> Check the Cronometer connection, search for cooked white rice, and inspect one
> result. Do not log or change anything.

Expect successful calls to `check_cronometer_connection`, `resolve_food`, and
`get_food_details`. Then try:

> Read my calories and protein for today. Tell me the date and account timezone
> used. Do not change anything.

`get_daily_nutrition` should return the account's recorded totals. Confirming a
real meal later is a separate live-write test; these checks do not establish that
all write/recovery paths work. See the [current roadmap](pr-map.md).

## Update the code

For an ordinary **merged release**, use the same project directory. This procedure
updates application code/dependencies; it does not replace the tunnel binary, key,
profile or Cronometer session. A docs-only change does not require restarting.

1. Check your branch and local work before changing anything:

   ```bash
   cd ~/Documents/Projects/cronometer-assistant
   git status --short --branch
   ```

   Preserve any uncommitted changes. If testing an unmerged PR, stay on that
   intended branch until its changes are merged; do not blindly switch to `main`.
   The relocated installation may still be on `docs/current-roadmap-audit`.

2. Stop the service, then back up each database you use. For meals:

   ```bash
   systemctl --user stop cronometer-assistant.service &&
   install -d -m 700 data/backups &&
   backup_dir="data/backups/meals-$(date +%Y%m%d-%H%M%S)" &&
   .venv/bin/python -m cronometer_assistant backup-runtime --db data/drafts.sqlite --backup-dir "$backup_dir" &&
   .venv/bin/python -m cronometer_assistant verify-backup --backup-dir "$backup_dir"
   ```

   Continue only after the entire block (stop, directory creation, backup and
   verification) succeeds. If you use product mode,
   also back up `data/products.sqlite` to a separate new directory. These bundles
   include managed referenced photos, **not** keys, login sessions, inbox/legacy
   photos, or image bytes stored only at an external reference. See
   [backup and restore](backups.md).

3. With a clean checkout and the intended changes merged, update and restart:

   ```bash
   git rev-parse HEAD > .runtime/pre-update-commit &&
   git fetch origin &&
   git switch main &&
   git pull --ff-only origin main &&
   uv sync --locked --extra mcp --extra live --no-dev &&
   systemctl --user restart cronometer-assistant.service
   ```

   If fetch, switch, pull or dependency sync fails, the chain stops before starting
   the service. If startup was attempted, systemd may instead be retrying; stop it
   while investigating. Inspect the failure before starting it again. Do not reset/discard local work to make
   a pull succeed. After a failed update, use recovery below before repeating this
   step: repeating it overwrites the recorded rollback commit.

4. Check status, readiness, and the read-only ChatGPT test above. **Refresh the
   ChatGPT connection's tools if tool names, descriptions, schemas or server mode
   changed**, then start a new chat. Refreshing ChatGPT does not pull code or
   restart the local server.

Run `systemctl --user daemon-reload` before restarting **only when the service
unit changes**. A code/profile/session change needs a restart, not daemon-reload.
Do not move the folder or copy an old `.venv` during an update: relocation needs
new absolute profile paths, an adjusted unit, and a rebuilt virtual environment.

## Troubleshooting

| Symptom | Next step |
| --- | --- |
| Process crashes | systemd retries after 10 seconds. Read recent logs. After fixing the cause, run `systemctl --user reset-failed cronometer-assistant.service` then restart. A live but unhealthy process is not automatically fixed by `Restart=on-failure`. |
| `active` but calls fail | Check `/readyz`, logs, internet access, and then the read-only ChatGPT test. A running process alone is insufficient. |
| `.runtime/health.url` is missing, stale, or refused | Check service status/logs and run the check in your own terminal. The file can outlive a stopped process; an agent sandbox may not share your laptop's localhost. |
| Profile rejected after moving/setup | Use `.runtime/profile.yaml`, not `.json`, and ensure the launcher references it. Check absolute paths and executable existence. Never print the API key. |
| “Already running” / inconsistent tool calls | Stop the old terminal/checkout/other-machine instance using this tunnel ID. Keep the systemd instance only. Do not delete the lock file to bypass a running lock. |
| OpenAI authentication/permission failure | Check key validity, Tunnels Read + Use, principal access and workspace association. This is separate from Cronometer login. |
| Cronometer login/session expired | Stop the service, run `.venv/bin/python -m cronometer_assistant login` and then `check-cronometer` locally; restart after success. Complete any 2FA locally. |
| `Unknown tool`, old demo tools, missing daily totals | Confirm the profile runs `serve-meals` from this checkout. Restart the service, refresh the ChatGPT connection, and open a new chat. Refresh alone cannot change the server mode. |
| Many literal/missing-field errors mentioning `server/discover` | Known SDK discovery incompatibility: one unsupported request produces many union-validation messages. If fallback tool discovery and real calls succeed, it is not a separate failure of every named task tool. If discovery/calls fail, investigate; do not dismiss all errors. |
| Fails after waking or losing Wi-Fi | Reconnect, check readiness, then restart the service if needed. It cannot serve while the laptop sleeps. |
| A diary write timed out or outcome is uncertain | Ask for `get_logging_status` on the existing meal/operation and inspect the diary. Do not create a fresh copy of that meal, delete its journal, or restore an old backup to retry blindly. |

For a runtime key replacement: stop the service, obtain a replacement Restricted
key, and save it without putting it in shell history:

```bash
systemctl --user stop cronometer-assistant.service &&
python3 - <<'PY' &&
import getpass
from pathlib import Path
from scripts.connect_demo import save_key
save_key(Path('.runtime/tunnel-api-key'), getpass.getpass('New runtime API key (hidden): '))
PY
systemctl --user restart cronometer-assistant.service
```

Continue only if saving the replacement and restarting succeeded. Verify readiness
and a real call before revoking the old key, unless the old key
was compromised (revoke it promptly in that case). The service's `file:` reference
uses the replacement on restart.

If fetch/pull/dependency installation failed **before the updated server ever
started**, and the checkout still has no uncommitted work, return to the recorded
code and matching dependencies:

```bash
systemctl --user stop cronometer-assistant.service &&
git switch --detach "$(cat .runtime/pre-update-commit)" &&
uv sync --locked --extra mcp --extra live --no-dev &&
systemctl --user start cronometer-assistant.service
```

This leaves Git on the recorded commit (detached HEAD). Check readiness and a real
read, then switch back to the intended branch during the next successful update.
If the new server **did start or may have changed the database schema**, stop its
retries, preserve the current data, and review compatibility before using this
rollback. Restoring a database can lose later meals and write-reconciliation
history. Use [the isolated restore procedure](backups.md) before deciding to
replace live data. Review logs for credentials or personal food data before
sharing excerpts.

## Other modes and limits

`serve-meals` is the everyday diary mode. `serve-products` is a separate mode and
store for private custom-food workflows; it does not expose diary logging. Photo
inbox import is explicitly configured for products mode and does not automatically
receive chat attachments. See [product evidence](product-evidence.md) and
[private products](review-and-products.md). Native Cronometer photo upload and
public submission remain roadmap work, not a deployment setting.

For a temporary foreground demo/read-only/product session, see
[tunnel setup](tunnel-setup.md). Stop the systemd service before reusing its tunnel;
restore meals mode afterward and refresh ChatGPT whenever the tool set changes.
The demo launcher can remove the live extra from the shared virtual environment.
After stopping a temporary foreground session, return to meals with:

```bash
uv sync --locked --extra mcp --extra live --no-dev &&
systemctl --user start cronometer-assistant.service
```

Do not run both modes at once against the same tunnel.
