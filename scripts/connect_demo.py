#!/usr/bin/env python3
"""Run the official tunnel client for demo or read-only Cronometer food inspection."""

import argparse
import fcntl
import getpass
import hashlib
import io
import json
import os
import platform
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

RELEASE_API = "https://api.github.com/repos/openai/tunnel-client/releases/latest"
ASSET_PREFIX = "https://github.com/openai/tunnel-client/releases/download/"
MAX_DOWNLOAD = 150 * 1024 * 1024
ROOT = Path(__file__).resolve().parents[1]


def validate_key(key: str) -> str:
    key = key.strip()
    if len(key) > 8192:
        raise ValueError("Runtime API key exceeds the size limit")
    if not key or any(char.isspace() for char in key):
        raise ValueError("Runtime API key is empty or contains whitespace")
    return key


def load_saved_key(path: Path) -> str | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd) as source:
        info = os.fstat(source.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise ValueError("Saved runtime key must be a private regular file with mode 0600")
        key = source.read(8193)
        if len(key) > 8192:
            raise ValueError("Saved runtime key exceeds the size limit")
        return validate_key(key)


def save_key(path: Path, key: str):
    key = validate_key(key)
    info = path.parent.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError("Runtime-key directory must be private with mode 0700")
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as target:
        temporary = Path(target.name)
        try:
            os.fchmod(target.fileno(), 0o600)
            target.write(key + "\n")
            target.flush()
            os.fsync(target.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def validate_tunnel_id(value: str) -> str:
    if not re.fullmatch(r"tunnel_(?:[a-z0-9]{4}_)?[a-z0-9]{32}", value):
        raise ValueError("Expected the complete tunnel_… ID returned by OpenAI")
    return value


class NoCredentialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.URLError("Refusing a redirect of the authenticated release lookup")


def read_public_url(url: str, maximum: int) -> bytes:
    headers = {"User-Agent": "cronometer-assistant-setup"}
    token = os.environ.get("TUNNEL_RELEASE_API_TOKEN") if url == RELEASE_API else None
    if token:
        if len(token) > 8192 or any(c.isspace() for c in token):
            raise ValueError("Release lookup token has an invalid format")
        headers["Authorization"] = "Bearer " + token
    for attempt in range(3):
        request = urllib.request.Request(url, headers=headers)
        try:
            # CI's read-only GitHub token is scoped to this exact metadata URL.
            # Refuse redirects entirely on that request; asset downloads get no token.
            opener = urllib.request.build_opener(NoCredentialRedirect()) if token else None
            open_url = opener.open if opener else urllib.request.urlopen
            with open_url(request, timeout=30) as response:
                payload = response.read(maximum + 1)
            if len(payload) > maximum:
                raise ValueError("Official download exceeds the setup size limit")
            return payload
        except urllib.error.HTTPError as error:
            retry = error.code in {429, 500, 502, 503, 504} or (
                error.code == 403 and error.headers.get("X-RateLimit-Remaining") == "0"
            )
            if not retry or attempt == 2:
                raise
        except (TimeoutError, urllib.error.URLError):
            if attempt == 2 or token:
                raise
        time.sleep(0.5 * (attempt + 1))
    raise RuntimeError("Official download did not complete")


def choose_asset(release: dict, system: str, machine: str) -> dict:
    os_name = {"Linux": "linux", "Darwin": "darwin"}.get(system)
    arch = {"x86_64": "amd64", "AMD64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(machine)
    if not os_name or not arch:
        raise ValueError("This launcher supports Linux/macOS on x86_64 or ARM64")
    tag = release.get("tag_name", "")
    if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise ValueError("Official release has an unexpected version format")
    name = f"tunnel-client-{tag}-{os_name}-{arch}.zip"
    matches = [asset for asset in release.get("assets", []) if asset.get("name") == name]
    if len(matches) != 1:
        raise ValueError("No unique official client download matches this machine")
    asset = matches[0]
    if asset.get("browser_download_url") != ASSET_PREFIX + tag + "/" + name:
        raise ValueError("Unexpected official download URL")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", asset.get("digest", "")):
        raise ValueError("No SHA-256 digest supplied; refusing an unverified download")
    return asset


def install_archive(payload: bytes, digest: str, destination: Path):
    if "sha256:" + hashlib.sha256(payload).hexdigest() != digest:
        raise ValueError("Tunnel-client archive checksum does not match the official metadata")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        members = [
            m
            for m in archive.infolist()
            if not m.is_dir() and m.filename.rsplit("/", 1)[-1] == "tunnel-client"
        ]
        if len(members) != 1 or members[0].file_size > MAX_DOWNLOAD:
            raise ValueError("Unexpected tunnel-client archive contents")
        executable = archive.read(members[0])
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Read only the binary; never extract archive-supplied filesystem paths.
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write(executable)
            output.flush()
            os.fsync(output.fileno())
            temporary.chmod(0o700)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)


def acquire_client(folder: Path) -> Path:
    release = json.loads(read_public_url(RELEASE_API, 2 * 1024 * 1024))
    asset = choose_asset(release, platform.system(), platform.machine())
    destination = folder / release["tag_name"] / "tunnel-client"
    # Verify a freshly downloaded archive on every setup, including an existing version.
    # This is infrequent setup traffic, rather than an automatic update of a running daemon.
    payload = read_public_url(asset["browser_download_url"], MAX_DOWNLOAD)
    install_archive(payload, asset["digest"], destination)
    print(f"Verified official tunnel-client {release['tag_name']}.", flush=True)
    return destination


def launch_arguments(
    client: Path,
    tunnel_id: str,
    python: Path,
    database: Path,
    *,
    mode: str = "demo",
    evidence_inbox: Path | None = None,
) -> list[str]:
    validate_tunnel_id(tunnel_id)
    if mode not in {"demo", "read-only", "products", "drafts"}:
        raise ValueError("mode must be demo, read-only, products or drafts")
    server_args = (
        ["serve", "--db", str(database)]
        if mode == "demo"
        else [
            "serve-read-only",
            "--session",
            str(database.parent / "cronometer-session/session.json"),
        ]
    )
    if mode in {"products", "drafts"}:
        server_args = [
            "serve-products" if mode == "products" else "serve-drafts",
            "--db",
            str(database.parent / ("products.sqlite" if mode == "products" else "drafts.sqlite")),
            "--session",
            str(database.parent / "cronometer-session/session.json"),
        ]
    if evidence_inbox is not None:
        if mode != "products":
            raise ValueError("Photo import requires products mode")
        server_args += ["--evidence-inbox", str(evidence_inbox)]
    command = shlex.join(
        [
            str(python),
            "-m",
            "cronometer_assistant",
            *server_args,
        ]
    )
    return [
        str(client),
        "run",
        "--control-plane.tunnel-id",
        tunnel_id,
        "--mcp.command",
        command,
        "--health.listen-addr",
        "127.0.0.1:0",
        "--health.url-file",
        str(database.parent / "tunnel-health.url"),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tunnel-id", required=True, type=validate_tunnel_id)
    parser.add_argument(
        "--mode", choices=["demo", "read-only", "products", "drafts"], default="demo"
    )
    parser.add_argument(
        "--evidence-inbox", type=Path, help="Private host-managed product image inbox"
    )
    parser.add_argument(
        "--save-key",
        action="store_true",
        help="Save the entered runtime key privately for future launches (Git-ignored)",
    )
    parser.add_argument(
        "--client-bin", type=Path, help="Use an already installed official tunnel-client binary"
    )
    args = parser.parse_args()
    os.umask(0o077)
    uv = shutil.which("uv")
    if not uv:
        raise ValueError("Install uv from https://docs.astral.sh/uv/ before running setup")
    runtime = ROOT / ".runtime"
    runtime.mkdir(mode=0o700, exist_ok=True)
    # One active instance for this checkout; do not run another instance elsewhere
    # using the same tunnel ID. Stdio requests require the same MCP child process.
    with (runtime / "tunnel.lock").open("a") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("A tunnel launcher is already active for this checkout") from error
        print("Installing the locked dependencies…", flush=True)
        extras = ["--extra", "live"] if args.mode != "demo" else []
        subprocess.run(
            [uv, "sync", "--locked", "--extra", "mcp", *extras, "--no-dev"], cwd=ROOT, check=True
        )
        client = args.client_bin.resolve() if args.client_bin else acquire_client(runtime)
        if not client.is_file() or not os.access(client, os.X_OK):
            raise ValueError("The official tunnel-client binary is missing or not executable")
        subprocess.run([str(client), "help", "quickstart"], check=True)
        key_path = runtime / "tunnel-api-key"
        key = os.environ.get("CONTROL_PLANE_API_KEY", "") or load_saved_key(key_path)
        if not key:
            if not sys.stdin.isatty():
                raise ValueError("Run this in your terminal for private API-key entry")
            key = getpass.getpass("Paste restricted runtime API key (hidden): ").strip()
        key = validate_key(key)
        if args.save_key:
            save_key(key_path, key)
            print("Runtime key saved privately for future launches (Git-ignored).", flush=True)
        environment = dict(os.environ)
        environment["CONTROL_PLANE_API_KEY"] = key
        database = ROOT / "data" / "demo.sqlite"
        database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        command = launch_arguments(
            client,
            args.tunnel_id,
            ROOT / ".venv/bin/python",
            database,
            mode=args.mode,
            evidence_inbox=args.evidence_inbox,
        )
        print(
            f"Starting {args.mode.upper()} tunnel. Keep this terminal open; Ctrl+C stops it.",
            flush=True,
        )
        print(
            "The key is passed through the process environment, never command arguments.",
            flush=True,
        )
        print(
            f"Local health/UI URL will be written to {database.parent / 'tunnel-health.url'}.",
            flush=True,
        )
        print(
            "Wait for readiness, then refresh the ChatGPT connection's available tools.",
            flush=True,
        )
        # Persistence is opt-in; the runtime key is always absent from command arguments.
        return subprocess.run(command, cwd=ROOT, env=environment).returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nTunnel stopped.", file=sys.stderr)
        raise SystemExit(130) from None
    except (ValueError, OSError, urllib.error.URLError, subprocess.CalledProcessError) as error:
        # Do not include subprocess environments or authentication values in diagnostics.
        print(f"Setup stopped: {error}", file=sys.stderr)
        raise SystemExit(1) from None
