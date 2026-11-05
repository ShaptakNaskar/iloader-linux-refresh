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
        self.profile = {"apple_id": "test@example.invalid", "team_id": "TEAM123456"}
        self.discovered = ["com.example.app"]
        (self.data / "account-profile.json").write_text(json.dumps(self.profile))
        (self.data / "state").mkdir()
        (self.data / "rp-pairing.plist").write_bytes(b"pairing fixture")

    def inventory(self, bundles=None):
        record = {"version": 1, "team_id": self.profile["team_id"], "bundles": self.discovered if bundles is None else bundles}
        return subprocess.CompletedProcess([], 0, "Device library message\nILR_INVENTORY " + json.dumps(record) + "\n", "")

    def check_run(self, *, dry_run=False, status=0):
        refreshed = []

        def run(command, **kwargs):
            if command[0] == "secret-tool":
                return subprocess.CompletedProcess(command, 0, "test-password\n", "")
            work = kwargs["cwd"]
            account = work / "account.json"
            self.assertEqual(stat.S_IMODE(account.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(work.stat().st_mode), 0o700)
            self.assertEqual(json.loads(account.read_text()), {"apple_id": self.profile["apple_id"], "team_id": self.profile["team_id"], "password": "test-password"})
            self.assertEqual((work / "state").resolve(), self.data / "state")
            if command == [str(self.executable), "inventory"]:
                return self.inventory()
            bundle = command[3]
            self.assertEqual(command, [str(self.executable), "renew", "--bundle", bundle, "--min-days", "7" if dry_run else "0"] + (["--dry-run"] if dry_run else []))
            refreshed.append(bundle)
            return subprocess.CompletedProcess(command, status)

        with patch.object(refresh_apps.subprocess, "run", side_effect=run):
            result = refresh_apps.refresh(self.data, self.runtime, self.executable, dry_run)
        self.assertEqual(result, status)
        self.assertEqual(refreshed, self.discovered)
        self.assertEqual(list(self.runtime.iterdir()), [])
        self.assertFalse((self.data / "account.json").exists())
        self.assertEqual(json.loads((self.data / "account-profile.json").read_text()), self.profile)

    def test_credentials_are_private_temporary_and_removed_after_success(self):
        self.check_run()

    def test_dry_run_checks_installed_profiles_without_installing(self):
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
            self.inventory(),
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

    def test_legacy_two_app_list_does_not_limit_new_app_discovery(self):
        self.profile["bundles"] = ["com.example.first", "com.example.second"]
        self.discovered = ["com.example.first", "com.example.second", "com.example.third"]
        (self.data / "account-profile.json").write_text(json.dumps(self.profile))
        self.check_run()

    def test_inventory_failure_never_retries_with_broader_flags(self):
        with patch.object(refresh_apps.subprocess, "run", side_effect=[
            subprocess.CompletedProcess([], 0, "test-password\n", ""),
            subprocess.CompletedProcess([], 9, "", "phone locked"),
        ]) as run:
            with self.assertRaisesRegex(RuntimeError, "App discovery failed: phone locked"):
                refresh_apps.refresh(self.data, self.runtime, self.executable)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args.args[0], [str(self.executable), "inventory"])
        self.assertEqual(list(self.runtime.iterdir()), [])

    def test_empty_inventory_never_falls_back_to_old_profiles(self):
        self.profile["bundles"] = ["com.example.uninstalled"]
        self.discovered = []
        (self.data / "account-profile.json").write_text(json.dumps(self.profile))
        self.check_run()

    def test_failed_app_does_not_starve_other_apps_or_reset_timeout(self):
        self.discovered = ["com.example.one", "com.example.two", "com.example.three"]
        with patch.object(refresh_apps.time, "monotonic", side_effect=[0, 10, 30, 40]):
            with patch.object(refresh_apps.subprocess, "run", side_effect=[
                subprocess.CompletedProcess([], 0, "test-password\n", ""), self.inventory(),
                subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 9),
                subprocess.CompletedProcess([], 0),
            ]) as run:
                status = refresh_apps.refresh(self.data, self.runtime, self.executable)
        self.assertEqual(status, 9)
        self.assertEqual(run.call_count, 5)
        for call, timeout in zip(run.call_args_list[2:], [230, 210, 200]):
            self.assertEqual(call.kwargs["timeout"], timeout)
            self.assertNotIn("--installed-only", call.args[0])
            self.assertNotIn("--force", call.args[0])
        self.assertEqual(list(self.runtime.iterdir()), [])

    def test_untrusted_inventory_record_cannot_choose_renewal_arguments(self):
        for record in [
            {"version": 1, "team_id": "OTHERTEAM1", "bundles": ["com.example.app"]},
            {"version": 1, "team_id": "TEAM123456", "bundles": ["--force"]},
            {"version": 2, "team_id": "TEAM123456", "bundles": []},
        ]:
            with self.subTest(record=record):
                response = subprocess.CompletedProcess([], 0, "ILR_INVENTORY " + json.dumps(record), "")
                with patch.object(refresh_apps.subprocess, "run", side_effect=[
                    subprocess.CompletedProcess([], 0, "test-password\n", ""), response,
                ]) as run:
                    with self.assertRaises(RuntimeError):
                        refresh_apps.refresh(self.data, self.runtime, self.executable)
                self.assertEqual(run.call_count, 2)
                self.assertEqual(list(self.runtime.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
