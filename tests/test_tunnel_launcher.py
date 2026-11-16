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
    def test_product_mode_uses_its_own_database_and_explicit_command(self):
        args = launcher.launch_arguments(
            Path("/client"), "tunnel_" + "a" * 32,
            Path("/python"), Path("/data/demo.sqlite"), mode="products",
        )
        child = shlex.split(args[args.index("--mcp.command") + 1])
        self.assertIn("serve-products", child)
        self.assertEqual(child[child.index("--db") + 1], "/data/products.sqlite")

    def test_oversized_saved_key_is_rejected_without_echo(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "key"
            launcher.save_key(path, "a" * 8193)
            with self.assertRaises(ValueError) as error:
                launcher.load_saved_key(path)
            self.assertNotIn("a" * 100, str(error.exception))

    def test_saved_key_round_trip_is_private(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "runtime-key"
            self.assertIsNone(launcher.load_saved_key(path))
            launcher.save_key(path, "synthetic-key")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(launcher.load_saved_key(path), "synthetic-key")
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                launcher.load_saved_key(path)

    def test_saved_key_rejects_symlinks_and_shared_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "runtime-key"
            path.symlink_to(Path(folder) / "elsewhere")
            with self.assertRaises(OSError):
                launcher.load_saved_key(path)
            Path(folder).chmod(0o755)
            with self.assertRaises(ValueError):
                launcher.save_key(path, "synthetic-key")

    def test_invalid_key_is_never_saved(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "runtime-key"
            for value in ("", "two words", "one\ntwo"):
                with self.assertRaises(ValueError):
                    launcher.save_key(path, value)
            self.assertFalse(path.exists())

    def test_read_only_mode_uses_separate_server_and_private_session(self):
        args = launcher.launch_arguments(
            Path("/client"),
            "tunnel_" + "a" * 32,
            Path("/python"),
            Path("/some data/demo.sqlite"),
            mode="read-only",
        )
        child = shlex.split(args[args.index("--mcp.command") + 1])
        self.assertIn("serve-read-only", child)
        self.assertNotIn("serve", child)
        self.assertNotIn("--db", child)
        self.assertEqual(child[-1], "/some data/cronometer-session/session.json")

    def test_launch_command_handles_spaces_and_has_no_key_argument(self):
        args = launcher.launch_arguments(
            Path("/some client/tunnel-client"),
            "tunnel_" + "a" * 32,
            Path("/path with spaces/python"),
            Path("/some data/demo.sqlite"),
        )
        child = shlex.split(args[args.index("--mcp.command") + 1])
        self.assertEqual(child[0], "/path with spaces/python")
        self.assertEqual(child[-1], "/some data/demo.sqlite")
        self.assertFalse(any("api-key" in arg for arg in args))
        self.assertIn("127.0.0.1:0", args)

    def test_tunnel_identifiers_are_validated(self):
        self.assertEqual(
            launcher.validate_tunnel_id("tunnel_ab12_" + "a" * 32), "tunnel_ab12_" + "a" * 32
        )
        with self.assertRaises(ValueError):
            launcher.validate_tunnel_id("tunnel_incomplete; command")

    def test_only_exact_official_platform_asset_is_selected(self):
        name = "tunnel-client-v0.0.21-linux-amd64.zip"
        asset = {
            "name": name,
            "digest": "sha256:" + "a" * 64,
            "browser_download_url": launcher.ASSET_PREFIX + "v0.0.21/" + name,
        }
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
            launcher.install_archive(
                payload, "sha256:" + hashlib.sha256(payload).hexdigest(), destination
            )
            self.assertEqual(destination.read_bytes(), b"test executable")
            self.assertEqual(list(Path(folder).rglob("*")), [destination.parent, destination])
