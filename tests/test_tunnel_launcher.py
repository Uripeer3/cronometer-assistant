import hashlib
import importlib.util
import io
import shlex
import tempfile
import unittest
import zipfile
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "connect_demo", Path(__file__).resolve().parents[1] / "scripts/connect_demo.py"
)
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class TunnelLauncherTests(unittest.TestCase):
    def test_launch_command_handles_spaces_and_has_no_key_argument(self):
        args = launcher.launch_arguments(
            Path("/some client/tunnel-client"), "tunnel_" + "a" * 32,
            Path("/path with spaces/python"), Path("/some data/demo.sqlite"),
        )
        child = shlex.split(args[args.index("--mcp.command") + 1])
        self.assertEqual(child[0], "/path with spaces/python")
        self.assertEqual(child[-1], "/some data/demo.sqlite")
        self.assertFalse(any("api-key" in arg for arg in args))
        self.assertIn("127.0.0.1:0", args)

    def test_tunnel_identifiers_are_validated(self):
        self.assertEqual(launcher.validate_tunnel_id("tunnel_ab12_" + "a" * 32),
                         "tunnel_ab12_" + "a" * 32)
        with self.assertRaises(ValueError):
            launcher.validate_tunnel_id("tunnel_incomplete; command")

    def test_only_exact_official_platform_asset_is_selected(self):
        name = "tunnel-client-v0.0.21-linux-amd64.zip"
        asset = {"name": name, "digest": "sha256:" + "a" * 64,
                 "browser_download_url": launcher.ASSET_PREFIX + "v0.0.21/" + name}
        release = {"tag_name": "v0.0.21", "assets": [asset]}
        self.assertEqual(launcher.choose_asset(release, "Linux", "x86_64"), asset)
        asset["browser_download_url"] = "https://example.com/untrusted.zip"
        with self.assertRaises(ValueError):
            launcher.choose_asset(release, "Linux", "x86_64")

    def test_checksum_failure_never_installs(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "client"
            with self.assertRaises(ValueError):
                launcher.install_archive(b"wrong", "sha256:" + "0" * 64, destination)
            self.assertFalse(destination.exists())

    def test_archive_paths_are_not_extracted(self):
        content = io.BytesIO()
        with zipfile.ZipFile(content, "w") as archive:
            archive.writestr("../../tunnel-client", b"test executable")
            archive.writestr("../../other-file", b"must not be extracted")
        payload = content.getvalue()
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "bin" / "tunnel-client"
            launcher.install_archive(payload, "sha256:" + hashlib.sha256(payload).hexdigest(),
                                     destination)
            self.assertEqual(destination.read_bytes(), b"test executable")
            self.assertEqual(list(Path(folder).rglob("*")), [destination.parent, destination])
