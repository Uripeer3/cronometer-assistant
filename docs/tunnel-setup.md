# Connect the demo through Secure MCP Tunnel

For everyday meal logging and automatic startup on Fedora/GNOME, use the
[end-to-end setup and operations guide](setup-and-operations.md). This page covers
the temporary foreground launcher; stop the systemd service before reusing its tunnel.

The demo runs on your laptop. It needs an outbound connection to OpenAI and must stay running for ChatGPT to discover and use its tools. It does not access your real Cronometer diary.

## One-time Platform setup

1. In [Platform Tunnels](https://platform.openai.com/settings/organization/tunnels), create a tunnel associated with the ChatGPT workspace you will use.
2. In [Runtime API keys](https://platform.openai.com/settings/organization/api-keys), create a **Restricted** key named `cronometer-demo-runtime` with **Tunnels Read + Use**. The key's principal also needs access to the tunnel. The client does not need an admin key or Manage permission.
3. Keep the key private. Enter it only in the hidden terminal prompt. Optional private-file reuse is described below.

## Run from your normal terminal

In this repository's checkout:

```bash
python3 scripts/connect_demo.py --tunnel-id tunnel_YOUR_COMPLETE_ID
```

The launcher installs locked demo dependencies with `uv`, downloads the current official OpenAI tunnel client for your machine, and verifies the archive against the release's SHA-256 digest. It stores the binary under ignored `.runtime/`. If you already downloaded the supported binary from the Platform Tunnels page, pass `--client-bin /absolute/path/tunnel-client` instead.

It then asks for the restricted runtime key using a hidden terminal prompt. Paste the key there and press Enter. Nothing is echoed. Without `--save-key`, a newly entered key is not persisted. The key is passed to the official client through its process environment; it is not a command-line argument. Key selection is: `CONTROL_PLANE_API_KEY` environment variable, then an existing saved key, then the hidden prompt.

This is a foreground operator session. Keep the terminal open; Ctrl+C stops the client. Run only **one** client instance per tunnel ID: stdio initialization and subsequent calls must reach the same MCP child process. A local lock prevents two launchers from this checkout, but cannot detect another checkout or another machine using the same ID.

## Verify before finishing the ChatGPT form

The client writes its local health base URL to `data/tunnel-health.url`. Once it is running, use a second terminal:

```bash
python3 - <<'PY'
from pathlib import Path
import urllib.request
base = Path('data/tunnel-health.url').read_text().strip().rstrip('/')
with urllib.request.urlopen(base + '/readyz', timeout=5) as response:
    print(response.status, response.read().decode())
print('Local status page:', base + '/ui')
PY
```

An HTTP 200 readiness response is a prerequisite, not proof that ChatGPT can call the tools. In ChatGPT's custom MCP form, select this tunnel and **No authentication** for this demo server. The tunnel still has workspace authorization and runtime-key authentication; the demo MCP application itself has no OAuth implementation.

Finish creating/installing the connection. In a new conversation, select the plugin and ask it to create a demo meal draft. Verify that `begin_meal` is called and returns `mode=demo` with `real_diary_changed=false`. Then test a follow-up in Hebrew or English. Setup is complete only after a real tool call succeeds through ChatGPT.

If the client cannot authenticate, check the key's Tunnels Read + Use permissions and the tunnel's workspace association. If downloads fail in an agent sandbox, run the launcher directly in your normal terminal; the initial development environment blocks PyPI downloads. A key is not needed until dependency and binary setup succeeds.

## Sources

- [Official tunnel onboarding](https://github.com/openai/tunnel-client/blob/master/docs/onboarding.md)
- [Permissions and runtime keys](https://github.com/openai/tunnel-client/blob/master/docs/permissions.md)
- [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)

The launcher follows the public latest-release metadata rather than hardcoding a release URL. The Platform download is an alternative if your account requires a specifically supported build.

## Save the runtime key once

Add `--save-key` to the launcher command, alongside `--mode read-only` if using
real-food inspection. Enter the key at the hidden prompt. The launcher saves it
in `.runtime/tunnel-api-key`, mode 0600, inside the private 0700 runtime directory.
The entire `.runtime/` directory is Git-ignored. This is a plaintext local file
protected by file permissions, not encrypted storage or an OS keyring.

Future launches automatically reuse it, without needing `--save-key`. The flag
opts into saving; normal first-time launches continue to prompt without saving.
An environment key takes priority over a saved file. Shared-readable files and
symlinks are rejected. Do not print or paste the file contents into chat.

If a key was not saved, its full secret cannot be retrieved again from the
[API keys page](https://platform.openai.com/settings/organization/api-keys); create
a replacement Restricted key with Tunnels Read + Use. To replace a locally saved
key, stop the launcher, remove only `.runtime/tunnel-api-key`, and run again with
`--save-key`. Deleting this local file does not revoke the key on OpenAI.

## Evidence inbox and download reliability (feature stack)

Product mode can forward an explicit private
inbox: `--mode products --evidence-inbox /absolute/private/inbox`. The host must
place selected image attachments there first; this flag does not transfer chat
attachments automatically. The server validates directory permissions and accepts
filenames only. See [product evidence](product-evidence.md).

Public GET downloads retry bounded transient failures. CI supplies its existing
read-only GitHub token for the exact official release metadata request to avoid
shared-runner anonymous rate limits. That authenticated request refuses redirects;
asset downloads never receive the token. Runtime OpenAI keys are unrelated and
are never used for GitHub downloads. Checksums and asset URL validation still apply.
