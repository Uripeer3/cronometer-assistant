#!/usr/bin/env python3
"""Launch the OpenAI MCP tunnel connected to the Cronometer assistant stdio server."""

from __future__ import annotations

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
RELEASE_TAG_API_PREFIX = "https://api.github.com/repos/openai/tunnel-client/releases/tags/"
ASSET_PREFIX = "https://github.com/openai/tunnel-client/releases/download/"
VERSION_TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
MAX_DOWNLOAD = 150 * 1024 * 1024
ROOT = Path(__file__).resolve().parents[1]


def validate_key(key: str) -> str:
    key = key.strip()
    if not key or len(key) > 8192 or any(c.isspace() for c in key):
        raise ValueError("Runtime API key must be non-empty, <= 8192 chars, with no whitespace")
    return key


def load_saved_key(path: Path) -> str | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, encoding="utf-8") as source:
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


def save_key(path: Path, key: str) -> None:
    key = validate_key(key)
    info = path.parent.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError("Runtime-key directory must be private with mode 0700")
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as target:
        tmp = Path(target.name)
        try:
            os.fchmod(target.fileno(), 0o600)
            target.write(key + "\n")
            target.flush()
            os.fsync(target.fileno())
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)


def validate_tunnel_id(value: str) -> str:
    if not re.fullmatch(r"tunnel_(?:[a-z0-9]{4}_)?[a-z0-9]{32}", value):
        raise ValueError("Expected the complete tunnel_... ID returned by OpenAI")
    return value


class NoCredentialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.URLError("Refusing redirect on authenticated release lookup")


def read_public_url(url: str, maximum: int) -> bytes:
    headers = {"User-Agent": "cronometer-assistant-setup"}
    is_release_api = url == RELEASE_API or url.startswith(RELEASE_TAG_API_PREFIX)
    token = os.environ.get("TUNNEL_RELEASE_API_TOKEN") if is_release_api else None
    if token:
        if len(token) > 8192 or any(c.isspace() for c in token):
            raise ValueError("Release lookup token has an invalid format")
        headers["Authorization"] = "Bearer " + token
    for attempt in range(3):
        request = urllib.request.Request(url, headers=headers)
        try:
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
        raise ValueError("Supports Linux/macOS on x86_64 or ARM64")
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
        raise ValueError("No SHA-256 digest supplied; refusing unverified download")
    return asset


def install_archive(payload: bytes, digest: str, destination: Path) -> None:
    if "sha256:" + hashlib.sha256(payload).hexdigest() != digest:
        raise ValueError("Tunnel-client archive checksum mismatch")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        members = [
            m
            for m in archive.infolist()
            if not m.is_dir() and m.filename.rsplit("/", 1)[-1] == "tunnel-client"
        ]
        if len(members) != 1 or members[0].file_size > MAX_DOWNLOAD:
            raise ValueError("Unexpected tunnel-client archive contents")
        executable = archive.read(members[0])
    if destination.parent.is_symlink() or destination.is_symlink():
        raise ValueError(f"Refusing to install tunnel-client through symbolic link: {destination}")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as output:
        tmp = Path(output.name)
        try:
            output.write(executable)
            output.flush()
            os.fsync(output.fileno())
            tmp.chmod(0o700)
            os.replace(tmp, destination)
        finally:
            tmp.unlink(missing_ok=True)


def validate_client_version(value: str) -> str:
    if not VERSION_TAG_RE.fullmatch(value):
        raise ValueError("Client version must use semantic tag format like v1.2.3")
    return value


def _cached_binary(version_dir: Path) -> Path | None:
    if not version_dir.is_dir() or version_dir.is_symlink():
        return None
    candidate = version_dir / "tunnel-client"
    if candidate.is_file() and not candidate.is_symlink() and os.access(candidate, os.X_OK):
        return candidate
    return None


def find_cached_client(folder: Path, *, version: str | None = None) -> Path | None:
    """Return an already-installed tunnel-client binary without network access."""
    if not folder.is_dir() or folder.is_symlink():
        return None
    if version is not None:
        return _cached_binary(folder / validate_client_version(version))
    cached: list[tuple[tuple[int, int, int], Path]] = []
    for entry in folder.iterdir():
        match = VERSION_TAG_RE.fullmatch(entry.name)
        candidate = _cached_binary(entry) if match else None
        if match and candidate is not None:
            semver = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
            cached.append((semver, candidate))
    if not cached:
        return None
    cached.sort(key=lambda item: item[0], reverse=True)
    return cached[0][1]


def acquire_client(folder: Path, *, version: str | None = None, update: bool = False) -> Path:
    if not update:
        cached = find_cached_client(folder, version=version)
        if cached is not None:
            print(f"Using cached tunnel-client {cached.parent.name}.", flush=True)
            return cached
    api_url = (
        RELEASE_TAG_API_PREFIX + validate_client_version(version)
        if version is not None
        else RELEASE_API
    )
    release = json.loads(read_public_url(api_url, 2 * 1024 * 1024))
    asset = choose_asset(release, platform.system(), platform.machine())
    destination = folder / release["tag_name"] / "tunnel-client"
    payload = read_public_url(asset["browser_download_url"], MAX_DOWNLOAD)
    install_archive(payload, asset["digest"], destination)
    print(f"Verified official tunnel-client {release['tag_name']}.", flush=True)
    return destination


def launch_arguments(
    client: Path,
    tunnel_id: str,
    python: Path,
    db_path: Path,
    session_path: Path,
    health_file: Path,
) -> list[str]:
    validate_tunnel_id(tunnel_id)
    command = shlex.join(
        [
            str(python),
            "-m",
            "cronometer_assistant",
            "serve",
            "--db",
            str(db_path),
            "--session",
            str(session_path),
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
        str(health_file),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tunnel-id", required=True, type=validate_tunnel_id)
    parser.add_argument("--save-key", action="store_true", help="Save runtime key privately")
    parser.add_argument("--client-bin", type=Path, help="Use an installed tunnel-client binary")
    parser.add_argument(
        "--client-version",
        type=validate_client_version,
        help="Pin a specific official tunnel-client release tag (e.g. v1.2.3)",
    )
    parser.add_argument(
        "--update-client",
        action="store_true",
        help="Re-fetch and verify tunnel-client from GitHub instead of reusing a cached binary",
    )
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "assistant.sqlite")
    parser.add_argument(
        "--session", type=Path, default=ROOT / "data" / "cronometer-session" / "session.json"
    )
    args = parser.parse_args()
    os.umask(0o077)
    uv = shutil.which("uv")
    if not uv:
        raise ValueError("Install uv from https://docs.astral.sh/uv/ before running setup")
    runtime = ROOT / ".runtime"
    runtime.mkdir(mode=0o700, exist_ok=True)
    with (runtime / "tunnel.lock").open("a") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("A tunnel launcher is already active for this checkout") from error
        print("Syncing locked dependencies...", flush=True)
        subprocess.run(
            [uv, "sync", "--locked", "--extra", "mcp", "--extra", "live", "--no-dev"],
            cwd=ROOT,
            check=True,
        )
        client = (
            args.client_bin.resolve()
            if args.client_bin
            else acquire_client(runtime, version=args.client_version, update=args.update_client)
        )
        if not client.is_file() or not os.access(client, os.X_OK):
            raise ValueError("tunnel-client binary is missing or not executable")
        key_path = runtime / "tunnel-api-key"
        key = os.environ.get("CONTROL_PLANE_API_KEY", "") or load_saved_key(key_path)
        if not key:
            if not sys.stdin.isatty():
                raise ValueError("Run this in a terminal to enter the tunnel API key")
            key = getpass.getpass("Paste restricted runtime API key (hidden): ").strip()
        key = validate_key(key)
        if args.save_key:
            save_key(key_path, key)
        env = dict(os.environ)
        env["CONTROL_PLANE_API_KEY"] = key
        args.db.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        command = launch_arguments(
            client,
            args.tunnel_id,
            ROOT / ".venv/bin/python",
            args.db,
            args.session,
            runtime / "health.url",
        )
        print("Starting Cronometer assistant MCP tunnel. Press Ctrl+C to stop.", flush=True)
        return subprocess.run(command, cwd=ROOT, env=env).returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nTunnel stopped.", file=sys.stderr)
        raise SystemExit(130) from None
    except (ValueError, OSError, urllib.error.URLError, subprocess.CalledProcessError) as error:
        print(f"Setup stopped: {error}", file=sys.stderr)
        raise SystemExit(1) from None
