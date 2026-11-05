import base64
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import plistlib
import stat
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("installer", ROOT / "install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.data = self.root / "data"
        self.data.mkdir(mode=0o700)
        self.runtime = self.root / "runtime"
        self.binary = self.root / "bin/altkeeper"
        self.iloader = self.root / "iloader"
        self.iloader.mkdir()
        (self.iloader / "data.json").write_text(json.dumps({"ids": ["test@example.invalid"]}))
        (self.iloader / "preferences.json").write_text("{}")
        pairing = {"public_key": b"public", "private_key": b"private", "identifier": "test"}
        (self.iloader / "rppairing1.1_file_TEST").write_bytes(plistlib.dumps(pairing))

    def archive(self):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w:gz") as bundle:
            for name, content in [
                (f"altkeeper-{installer.VERSION}-linux-x86_64/altkeeper-ilr", b"test executable"),
                ("../../escape", b"must not extract"),
            ]:
                member = tarfile.TarInfo(name)
                member.size = len(content)
                bundle.addfile(member, io.BytesIO(content))
        return stream.getvalue()

    def test_verified_archive_installs_only_the_expected_executable(self):
        archive = self.archive()
        with patch.object(installer, "urlopen", return_value=io.BytesIO(archive)):
            with patch.object(installer, "SHA256", hashlib.sha256(archive).hexdigest()):
                installer.install_altkeeper(self.binary)
        self.assertEqual(self.binary.read_bytes(), b"test executable")
        self.assertEqual(stat.S_IMODE(self.binary.stat().st_mode), 0o755)
        self.assertFalse((self.root / "escape").exists())

    def test_checksum_failure_installs_nothing(self):
        with patch.object(installer, "urlopen", return_value=io.BytesIO(b"corrupt archive")):
            with self.assertRaisesRegex(RuntimeError, "checksum"):
                installer.install_altkeeper(self.binary)
        self.assertFalse(self.binary.exists())

    def test_an_existing_different_binary_is_not_overwritten(self):
        self.binary.parent.mkdir()
        self.binary.write_bytes(b"existing executable")
        with patch.object(installer.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "altkeeper other\n", "")):
            with self.assertRaisesRegex(RuntimeError, "move it aside"):
                installer.install_altkeeper(self.binary)
        self.assertEqual(self.binary.read_bytes(), b"existing executable")

    def configure(self, team="TEAM123456"):
        identity = plistlib.dumps({"keychain_identifier": bytes(16), "adi_pb": b"fixture"})

        def lookup(username):
            return base64.b64encode(identity).decode() if username == "anisette_state" else "stored-test-password"

        def login(command, **kwargs):
            self.assertEqual(command, [str(self.binary), "login", "test@example.invalid"])
            self.assertEqual(stat.S_IMODE(kwargs["cwd"].stat().st_mode), 0o700)
            (kwargs["cwd"] / "account.json").write_text(json.dumps({
                "apple_id": "test@example.invalid", "team_id": team, "password": "typed-test-password",
            }))
            return subprocess.CompletedProcess(command, 0)

        with patch.object(installer.refresh_apps, "lookup_secret", side_effect=lookup):
            with patch.object(installer.subprocess, "run", side_effect=login):
                with patch("builtins.input", side_effect=AssertionError("Setup must not ask for app bundle IDs")):
                    installer.configure(self.data, self.runtime, self.binary, self.iloader)

    def test_setup_imports_pairing_but_never_persists_the_password(self):
        self.configure()
        profile = json.loads((self.data / "account-profile.json").read_text())
        self.assertEqual(profile, {"apple_id": "test@example.invalid", "team_id": "TEAM123456"})
        self.assertEqual(stat.S_IMODE((self.data / "account-profile.json").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.data / "rp-pairing.plist").stat().st_mode), 0o600)
        self.assertEqual(list(self.runtime.iterdir()), [])
        for path in self.data.rglob("*"):
            if path.is_file():
                self.assertNotIn(b"typed-test-password", path.read_bytes())
                self.assertNotIn(b"stored-test-password", path.read_bytes())

    def test_invalid_team_cleans_up_credentials_without_saving_configuration(self):
        with self.assertRaisesRegex(RuntimeError, "team ID"):
            self.configure(team=None)
        self.assertFalse((self.data / "account-profile.json").exists())
        self.assertEqual(list(self.runtime.iterdir()), [])

    def test_existing_configuration_is_reused_and_old_password_fields_are_removed(self):
        profile = {"apple_id": "test@example.invalid", "team_id": "TEAM123456", "password": "legacy-password"}
        (self.data / "account-profile.json").write_text(json.dumps(profile))
        with patch("builtins.input", side_effect=AssertionError("Setup must not ask for app bundle IDs")):
            with patch.object(installer.refresh_apps, "lookup_secret") as lookup:
                installer.configure(self.data, self.runtime, self.binary, self.iloader)
        lookup.assert_not_called()
        saved = json.loads((self.data / "account-profile.json").read_text())
        self.assertNotIn("password", saved)
        self.assertNotIn("bundles", saved)

    def test_credential_bearing_anisette_url_is_rejected(self):
        (self.iloader / "preferences.json").write_text(json.dumps({"anisetteServer3-1": "https://user:password@example.invalid"}))
        with patch.object(installer.refresh_apps, "lookup_secret") as lookup:
            with self.assertRaisesRegex(RuntimeError, "URL is invalid"):
                installer.import_anisette(self.data, self.iloader)
        lookup.assert_not_called()

    def test_installer_help_has_no_setup_side_effects(self):
        result = subprocess.run(["python3", str(ROOT / "install.py"), "--help"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("without sudo", result.stdout)


if __name__ == "__main__":
    unittest.main()
