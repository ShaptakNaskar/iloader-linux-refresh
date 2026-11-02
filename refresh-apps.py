#!/usr/bin/env python3
"""Renew selected iOS profiles using iLoader's Secret Service keyring login."""

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
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
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as output:
        os.fchmod(output.fileno(), 0o600)
        output.write(contents)


def validate_bundles(bundles):
    if not isinstance(bundles, list) or not bundles or any(
        not isinstance(bundle, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", bundle)
        for bundle in bundles
    ):
        raise RuntimeError("Choose the host app bundle IDs with install.py before enabling refresh.")
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


def refresh(data, runtime, executable, dry_run=False):
    with (data / "refresh.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another refresh is already running.") from None

        profile = json.loads((data / "account-profile.json").read_text())
        account = {key: profile[key] for key in ("apple_id", "team_id")}
        bundles = validate_bundles(profile.get("bundles"))
        with private_workdir(data, runtime) as work:
            account["password"] = lookup_secret(account["apple_id"])
            write_private(work / "account.json", json.dumps(account).encode())

            deadline = time.monotonic() + 240
            status = 0
            for bundle in bundles:
                # Explicit bundles avoid v0.5.5's inventory-failure fallback, which ignores expiry.
                # Days are rounded down: zero means less than 24 hours remain (~day six).
                command = [str(executable), "renew", "--bundle", bundle, "--min-days", "7" if dry_run else "0"]
                if dry_run:
                    command.append("--dry-run")
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
    args = parser.parse_args()
    os.umask(0o077)
    try:
        return refresh(
            Path.home() / ".local/share/altserver-refresh",
            Path(os.environ["RUNTIME_DIRECTORY"]) if os.environ.get("RUNTIME_DIRECTORY")
            else Path(os.environ["XDG_RUNTIME_DIR"]) / "altserver-refresh-manual",
            Path.home() / ".local/bin/altkeeper",
            args.dry_run,
        )
    except (OSError, KeyError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"Refresh failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
