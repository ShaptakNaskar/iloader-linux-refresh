#!/usr/bin/env python3
"""Install the per-user iLoader refresh job. Run without sudo."""

import argparse
import base64
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("refresh_apps", ROOT / "refresh-apps.py")
refresh_apps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(refresh_apps)

VERSION = "0.5.6-ilr.1"
ARCHIVE = f"altkeeper-{VERSION}-linux-x86_64.tar.gz"
SHA256 = "ef5b57fb9045095340d2fb06a4a9f78f2cbf036a4f68f7b96098b5f5da8a148c"


def install_altkeeper(binary):
    if binary.exists():
        result = subprocess.run([str(binary), "--version"], capture_output=True, text=True, check=True)
        if result.stdout.strip() != f"altkeeper {VERSION}":
            raise RuntimeError(f"{binary} is not AltKeeper {VERSION}; move it aside before installing.")
        return
    binary.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://github.com/ShaptakNaskar/iloader-linux-refresh/releases/download/altkeeper-v{VERSION}/{ARCHIVE}"
    print(f"Downloading the verified AltKeeper {VERSION} integration build...", flush=True)
    with tempfile.TemporaryDirectory(dir=binary.parent) as directory:
        archive = Path(directory) / ARCHIVE
        digest = hashlib.sha256()
        with urlopen(Request(url, headers={"User-Agent": "iloader-linux-refresh"}), timeout=60) as response, archive.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                digest.update(chunk)
                output.write(chunk)
        if digest.hexdigest() != SHA256:
            raise RuntimeError("AltKeeper archive checksum mismatch; nothing was installed.")
        staged = Path(directory) / "altkeeper"
        with tarfile.open(archive, "r:gz") as bundle:
            member = bundle.getmember(f"altkeeper-{VERSION}-linux-x86_64/altkeeper-ilr")
            if not member.isfile():
                raise RuntimeError("The AltKeeper archive does not contain a regular executable.")
            with bundle.extractfile(member) as source, staged.open("wb") as output:
                shutil.copyfileobj(source, output)
        staged.chmod(0o755)
        staged.replace(binary)


def choose(items, label):
    if not items:
        raise RuntimeError(f"No {label} found. Sign in to iLoader and enable Save Credentials first.")
    if len(items) == 1:
        return items[0]
    for number, item in enumerate(items, 1):
        print(f"{number}. {item}")
    answer = input(f"Choose {label} (number): ")
    if not answer.isdecimal() or not 1 <= int(answer) <= len(items):
        raise RuntimeError("Invalid selection; nothing was selected.")
    return items[int(answer) - 1]


def import_anisette(data, iloader):
    state = data / "state"
    state.mkdir(mode=0o700, exist_ok=True)
    state.chmod(0o700)
    if (state / "anisette_state").exists():
        return
    preferences = json.loads((iloader / "preferences.json").read_text())
    server = preferences.get("anisetteServer3-1", "mac-ani.jkcoxson.com")
    server = server if "://" in server else f"https://{server}"
    url = urlsplit(server)
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
        raise RuntimeError("iLoader's Anisette URL is invalid; correct it in iLoader first.")
    try:
        identity = base64.b64decode(refresh_apps.lookup_secret("anisette_state"), validate=True)
    except RuntimeError:
        identity = (iloader / "anisette_state").read_bytes()
    if not isinstance(plistlib.loads(identity), dict):
        raise RuntimeError("iLoader's Anisette state is not a property-list dictionary.")
    refresh_apps.write_private(state / "anisette_state", identity)
    for name in ("anisette-url", "anisette-effective-url"):
        refresh_apps.write_private(state / name, server.rstrip("/").encode())


def configure(data, runtime, binary, iloader):
    profile_path = data / "account-profile.json"
    if profile_path.exists():
        existing = json.loads(profile_path.read_text())
        profile = dict(existing)
        profile.pop("password", None)
        if profile != existing:
            refresh_apps.write_private(profile_path, json.dumps(profile).encode())
        profile_path.chmod(0o600)
        return
    stored = json.loads((iloader / "data.json").read_text()).get("ids", [])
    if not isinstance(stored, list) or any(not isinstance(item, str) or not item.strip() or item.startswith("-") for item in stored):
        raise RuntimeError("iLoader's saved account list has an unsupported format.")
    email = choose(stored, "saved Apple account")
    pairing_entry = None
    refresh_apps.lookup_secret(email)
    import_anisette(data, iloader)
    if not (data / "rp-pairing.plist").exists():
        pairing_files = sorted(iloader.glob("rppairing1.1_file_*"))
        if pairing_files:
            selected = choose(pairing_files, "remote pairing file")
            pairing_entry = selected.name
            pairing = selected.read_bytes()
            record = plistlib.loads(pairing)
            if not isinstance(record, dict) or not {"public_key", "private_key", "identifier"}.issubset(record):
                raise RuntimeError("The selected file is not an iLoader remote pairing record.")
            refresh_apps.write_private(data / "rp-pairing.plist", pairing)
        else:
            print("Connect only the intended iPhone by USB, unlock it, and accept any pairing prompt.")
            input("Press Enter when ready: ")
            subprocess.run([str(binary), "pair-usb"], cwd=data, env=dict(os.environ, ALTKEEPER_LANG="en"), check=True)

    print("Confirm this account in AltKeeper's password/2FA prompts. Credentials stay in the temporary runtime directory.")
    with refresh_apps.private_workdir(data, runtime) as work:
        subprocess.run([str(binary), "login", email], cwd=work, env=dict(os.environ, ALTKEEPER_LANG="en"), check=True)
        account = json.loads((work / "account.json").read_text())
        team = account.get("team_id")
        if not isinstance(team, str) or len(team) != 10 or not team.isascii() or not team.isalnum():
            raise RuntimeError("AltKeeper did not return a valid Apple developer team ID.")
        profile = {"apple_id": email, "team_id": team}
        if pairing_entry:
            profile["pairing_entry"] = pairing_entry
        refresh_apps.write_private(profile_path, json.dumps(profile).encode())


def install():
    if sys.platform != "linux" or platform.machine() not in ("x86_64", "amd64"):
        raise RuntimeError("This first release supports Linux x86-64 only.")
    if os.getuid() == 0:
        raise RuntimeError("Run this installer as your desktop user, without sudo.")
    for program in ("secret-tool", "systemctl"):
        if not shutil.which(program):
            raise RuntimeError(f"Missing {program}; install the prerequisites listed in README.md.")
    os.umask(0o077)
    home = Path.home()
    data = home / ".local/share/altserver-refresh"
    data.mkdir(mode=0o700, parents=True, exist_ok=True)
    data.chmod(0o700)
    runtime = Path(os.environ["XDG_RUNTIME_DIR"]) / "altserver-refresh-setup"
    binary = home / ".local/bin/altkeeper-ilr"
    install_altkeeper(binary)
    with (data / "refresh.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        configure(data, runtime, binary, home / ".local/share/me.nabdev.iloader")
    print("Checking Apple sign-in and installed profiles without renewing them...", flush=True)
    if refresh_apps.refresh(data, runtime, binary, dry_run=True, iloader=home / ".local/share/me.nabdev.iloader"):
        raise RuntimeError("The dry run failed. The timer was not enabled; check the error above.")
    shutil.copyfile(ROOT / "refresh-apps.py", home / ".local/bin/refresh-apps")
    (home / ".local/bin/refresh-apps").chmod(0o755)
    if shutil.which("fish"):
        functions = home / ".config/fish/functions"
        functions.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "fish/refresh-apps.fish", functions / "refresh-apps.fish")
    units = home / ".config/systemd/user"
    units.mkdir(parents=True, exist_ok=True)
    for name in ("altserver-refresh.service", "altserver-refresh.timer"):
        shutil.copyfile(ROOT / "systemd" / name, units / name)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", "altserver-refresh.timer"], check=True)
    print("Hourly refresh checks enabled. Profiles are renewed during their last 24 hours (~day six).")


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    try:
        install()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Setup failed: {error}", file=sys.stderr)
        sys.exit(1)
