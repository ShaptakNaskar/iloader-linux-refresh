import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("refresh_apps", Path(__file__).resolve().parents[1] / "refresh-apps.py")
refresh_apps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(refresh_apps)


class RefreshAppsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.data = root / "data"
        self.data.mkdir(mode=0o700)
        self.runtime = root / "runtime"
        self.executable = root / "altkeeper"
        self.profile = {"apple_id": "test@example.invalid", "team_id": "TEAM123456", "bundles": ["com.example.app"]}
        (self.data / "account-profile.json").write_text(json.dumps(self.profile))
        (self.data / "state").mkdir()
        (self.data / "rp-pairing.plist").write_bytes(b"pairing fixture")

    def check_run(self, *, dry_run=False, status=0):
        def run(command, **kwargs):
            if command[0] == "secret-tool":
                return subprocess.CompletedProcess(command, 0, "test-password\n", "")
            self.assertEqual(command, [str(self.executable), "renew", "--bundle", "com.example.app", "--min-days", "7" if dry_run else "0"] + (["--dry-run"] if dry_run else []))
            work = kwargs["cwd"]
            account = work / "account.json"
            self.assertEqual(stat.S_IMODE(account.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(work.stat().st_mode), 0o700)
            self.assertEqual(json.loads(account.read_text()), {"apple_id": self.profile["apple_id"], "team_id": self.profile["team_id"], "password": "test-password"})
            self.assertEqual((work / "state").resolve(), self.data / "state")
            return subprocess.CompletedProcess(command, status)

        with patch.object(refresh_apps.subprocess, "run", side_effect=run):
            result = refresh_apps.refresh(self.data, self.runtime, self.executable, dry_run)
        self.assertEqual(result, status)
        self.assertEqual(list(self.runtime.iterdir()), [])
        self.assertFalse((self.data / "account.json").exists())
        self.assertEqual(json.loads((self.data / "account-profile.json").read_text()), self.profile)

    def test_credentials_are_private_temporary_and_removed_after_success(self):
        self.check_run()

    def test_dry_run_checks_selected_profiles_without_installing(self):
        self.check_run(dry_run=True)

    def test_credentials_are_removed_and_failure_is_reported(self):
        self.check_run(status=9)

    def test_missing_keyring_password_does_not_start_renewal(self):
        with patch.object(refresh_apps.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "")) as run:
            with self.assertRaisesRegex(RuntimeError, "Unlock.*keyring"):
                refresh_apps.refresh(self.data, self.runtime, self.executable)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(list(self.runtime.iterdir()), [])

    def test_temporary_password_is_removed_after_timeout(self):
        with patch.object(refresh_apps.subprocess, "run", side_effect=[
            subprocess.CompletedProcess([], 0, "test-password\n", ""),
            subprocess.TimeoutExpired([str(self.executable)], 240),
        ]):
            with self.assertRaises(subprocess.TimeoutExpired):
                refresh_apps.refresh(self.data, self.runtime, self.executable)
        self.assertEqual(list(self.runtime.iterdir()), [])

    def test_overlapping_refresh_is_rejected_before_reading_password(self):
        with (self.data / "refresh.lock").open("a") as lock:
            refresh_apps.fcntl.flock(lock, refresh_apps.fcntl.LOCK_EX | refresh_apps.fcntl.LOCK_NB)
            with patch.object(refresh_apps.subprocess, "run") as run:
                with self.assertRaisesRegex(RuntimeError, "already running"):
                    refresh_apps.refresh(self.data, self.runtime, self.executable)
        run.assert_not_called()

    def test_insecure_runtime_directory_is_rejected_before_reading_password(self):
        self.runtime.mkdir(mode=0o755)
        with patch.object(refresh_apps.subprocess, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "mode 700"):
                refresh_apps.refresh(self.data, self.runtime, self.executable)
        run.assert_not_called()

    def test_missing_bundle_allowlist_fails_before_reading_password(self):
        profile = {key: value for key, value in self.profile.items() if key != "bundles"}
        (self.data / "account-profile.json").write_text(json.dumps(profile))
        with patch.object(refresh_apps.subprocess, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "bundle IDs"):
                refresh_apps.refresh(self.data, self.runtime, self.executable)
        run.assert_not_called()

    def test_failed_app_does_not_starve_other_apps_or_reset_timeout(self):
        self.profile["bundles"] = ["com.example.one", "com.example.two", "com.example.three"]
        (self.data / "account-profile.json").write_text(json.dumps(self.profile))
        with patch.object(refresh_apps.time, "monotonic", side_effect=[0, 10, 30, 40]):
            with patch.object(refresh_apps.subprocess, "run", side_effect=[
                subprocess.CompletedProcess([], 0, "test-password\n", ""),
                subprocess.CompletedProcess([], 0),
                subprocess.CompletedProcess([], 9),
                subprocess.CompletedProcess([], 0),
            ]) as run:
                status = refresh_apps.refresh(self.data, self.runtime, self.executable)
        self.assertEqual(status, 9)
        self.assertEqual(run.call_count, 4)
        self.assertEqual(run.call_args_list[1].kwargs["timeout"], 230)
        self.assertEqual(run.call_args_list[2].kwargs["timeout"], 210)
        self.assertEqual(run.call_args_list[3].kwargs["timeout"], 200)
        for call in run.call_args_list[1:]:
            self.assertNotIn("--installed-only", call.args[0])
            self.assertNotIn("--force", call.args[0])
        self.assertEqual(list(self.runtime.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
