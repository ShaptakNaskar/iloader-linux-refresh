#!/usr/bin/env python3
"""Discover installed iOS apps and renew their profiles using iLoader's saved login."""

import argparse
import base64
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import plistlib
import re
import stat
import subprocess
import sys
import tempfile
import time


def lookup_secret(username):
    result = subprocess.run(
        ["secret-tool", "lookup", "service", "iloader", "username", username],
        capture_output=True, text=True, timeout=10,
    )
    secret = result.stdout.removesuffix("\n")
    if result.returncode or not secret:
        raise RuntimeError("Unlock your Secret Service keyring and save this account's password in iLoader first.")
    return secret


def write_private(path, contents):
    if path.is_symlink():
        raise OSError("Refusing to overwrite a private-file symlink.")
    fd, name = tempfile.mkstemp(dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(contents)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def prepare_pairing(data, work, profile, iloader):
    entry = profile.get("pairing_entry")
    if not entry and iloader is not None:
        saved = (data / "rp-pairing.plist").read_bytes()
        matches = [path.name for path in iloader.glob("rppairing1.1_file_*") if path.read_bytes() == saved]
        if len(matches) == 1:
            entry = matches[0]
    if not entry:
        return
    if not isinstance(entry, str) or not re.fullmatch(r"rppairing1\.1_file_[A-Za-z0-9-]+", entry):
        raise RuntimeError("Invalid iLoader pairing entry in account metadata.")
    if profile.get("pairing_entry") != entry:
        profile["pairing_entry"] = entry
        write_private(data / "account-profile.json", json.dumps(profile).encode())
    result = subprocess.run(
        ["secret-tool", "lookup", "service", "iloader", "username", entry],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode or not result.stdout:
        return  # Filesystem-only pairings can have no corresponding keyring entry.
    pairing = base64.b64decode(result.stdout.removesuffix("\n"), validate=True)
    record = plistlib.loads(pairing)
    if not isinstance(record, dict) or not {"public_key", "private_key", "identifier"}.issubset(record):
        raise RuntimeError("iLoader's saved remote pairing record is invalid.")
    destination = work / "rp-pairing.plist"
    if destination.is_symlink():
        destination.unlink()
    write_private(destination, pairing)


def discover_bundles(work, executable, team, timeout):
    result = subprocess.run(
        [str(executable), "inventory"], cwd=work,
        env=dict(os.environ, ALTKEEPER_LANG="en"),
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"App discovery failed: {detail or 'no device inventory available'}")
    records = [line.removeprefix("ILR_INVENTORY ") for line in result.stdout.splitlines() if line.startswith("ILR_INVENTORY ")]
    if len(records) != 1:
        raise RuntimeError("App discovery returned no unique inventory record; reinstall the integration build.")
    record = json.loads(records[0])
    if not isinstance(record, dict) or record.get("version") != 1 or record.get("team_id") != team:
        raise RuntimeError("App inventory does not match the configured Apple team.")
    bundles = record.get("bundles")
    if not isinstance(bundles, list) or any(
        not isinstance(bundle, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", bundle)
        for bundle in bundles
    ):
        raise RuntimeError("App inventory contains invalid bundle IDs.")
    return list(dict.fromkeys(bundles))


@contextmanager
def private_workdir(data, runtime):
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    permissions = runtime.stat()
    if runtime.is_symlink() or permissions.st_uid != os.getuid() or stat.S_IMODE(permissions.st_mode) != 0o700:
        raise RuntimeError("The runtime directory must belong to you and have mode 700.")
    with tempfile.TemporaryDirectory(prefix="run-", dir=runtime) as directory:
        work = Path(directory)
        for name in ("state", "rp-pairing.plist"):
            (work / name).symlink_to(data / name)
        yield work


def refresh(data, runtime, executable, dry_run=False, force=False, iloader=None):
    with (data / "refresh.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another refresh is already running.") from None

        profile = json.loads((data / "account-profile.json").read_text())
        account = {key: profile[key] for key in ("apple_id", "team_id")}
        with private_workdir(data, runtime) as work:
            account["password"] = lookup_secret(account["apple_id"])
            prepare_pairing(data, work, profile, iloader)
            write_private(work / "account.json", json.dumps(account).encode())

            deadline = time.monotonic() + 240
            bundles = discover_bundles(work, executable, account["team_id"], 240)
            print(f"Discovered {len(bundles)} installed app(s) for the configured signing team.", flush=True)
            status = 0
            for bundle in bundles:
                # Days are rounded down: zero means less than 24 hours remain (~day six).
                command = [str(executable), "renew", "--bundle", bundle, "--min-days", "7" if dry_run else "0"]
                if dry_run:
                    command.append("--dry-run")
                if force:
                    command.append("--force")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, 240)
                result = subprocess.run(
                    command, cwd=work, env=dict(os.environ, ALTKEEPER_LANG="en"), timeout=remaining,
                )
                if result.returncode and not status:
                    status = result.returncode
            return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Check Apple login and due apps without renewing profiles.")
    parser.add_argument("--force", action="store_true", help="Renew all discovered apps regardless of remaining days.")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        return refresh(
            Path.home() / ".local/share/altserver-refresh",
            Path(os.environ["RUNTIME_DIRECTORY"]) if os.environ.get("RUNTIME_DIRECTORY")
            else Path(os.environ["XDG_RUNTIME_DIR"]) / "altserver-refresh-manual",
            Path.home() / ".local/bin/altkeeper-ilr",
            args.dry_run,
            args.force,
            Path.home() / ".local/share/me.nabdev.iloader",
        )
    except (OSError, KeyError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"Refresh failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
