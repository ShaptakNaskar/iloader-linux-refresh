# iLoader Linux Refresh

Automatic Wi-Fi profile renewal for iPhone apps signed with an account saved in iLoader.

This is an **unofficial Linux integration**. [iLoader](https://github.com/nab138/iloader) supplies the saved login and initial pairing; [AltKeeper](https://github.com/BartolomeoRusso9/altkeeper) handles Apple authentication and provisioning-profile renewal. This project supplies the credential handling, setup, and systemd timer.

The timer checks the phone's installed apps hourly. Profiles belonging to the configured signing team are renewed when less than 24 hours remain—roughly six days after a seven-day profile is issued. Install another app with that account in iLoader and it will be picked up at the next check, without editing JSON or restarting the timer. Removed apps are no longer targeted. iLoader can be closed after setup.

## Requirements

- Linux **x86-64** with glibc **2.35+**, Python **3.10+**, and a systemd user session.
- A working Secret Service keyring, such as KDE KWallet, and `secret-tool` from libsecret.
- iLoader **2.4.1**, with the signing account saved using **Save Credentials**.
- An iPhone on **iOS 17.4+**, with Developer Mode enabled and initial pairing completed.
- The computer and iPhone on the same network when renewal is due. The phone must have been unlocked recently.

On Arch/CachyOS, install the two script dependencies with:

```sh
sudo pacman -S --needed python libsecret
```

The installer downloads the checksum-pinned **AltKeeper 0.5.6-ilr.1** integration build. This is upstream AltKeeper with read-only structured app discovery and a change that stops renewal when discovery fails. The complete patch, source commit, and build instructions are in [native/UPSTREAM.md](native/UPSTREAM.md). It does not install system packages or change the system iOS libraries.

## Setup

1. In iLoader, turn off **Settings → Don't use keyring**, then sign in and enable **Save Credentials**. A locked or disabled keyring prevents unattended sign-in.
2. Clone this repository and run setup as your desktop user:

   ```sh
   git clone https://github.com/ShaptakNaskar/iloader-linux-refresh.git
   cd iloader-linux-refresh
   python3 install.py
   ```

Setup imports iLoader's Anisette identity and server selection. It reuses a filesystem remote-pairing record when available; otherwise, it asks you to connect and unlock the intended iPhone over USB for pairing. With multiple saved accounts or pairing files, it asks you to choose one.

Confirm the Apple account in AltKeeper's local password/2FA prompts and choose the developer team that signed your apps. When asked whether to save the password, you can choose **No**: future refreshes read iLoader's existing keyring entry. Even if you choose Yes, this setup step runs inside a temporary runtime directory and only account/team metadata is copied out.

Setup runs a dry check before installing and enabling the timer. It reads the installed apps from the phone and matches their profiles to your signing team; you do not need to supply bundle IDs. Review the output, then unplug USB and verify the network path:

```sh
~/.local/bin/refresh-apps --dry-run
```

The dry run uses a seven-day threshold to exercise installed app profiles without installing new ones. A normal scheduled run uses a zero-day threshold because AltKeeper rounds remaining days down.

Already using the earlier manual app-list version? Run `git pull` and `python3 install.py` from your checkout. Your pairing and saved login are reused. Old `bundles` entries are ignored; future installs are discovered from the phone automatically.

## Daily use

```sh
# Next scheduled check
systemctl --user list-timers altserver-refresh.timer

# Check now; renew only profiles with less than 24 hours remaining
systemctl --user start altserver-refresh.service

# Inspect results and failures
journalctl --user -u altserver-refresh.service -n 30

# Stop automatic checks
systemctl --user disable --now altserver-refresh.timer
```

To add an app, sign and install it successfully through iLoader using the configured account. It will appear in the next hourly inventory check. Uninstalling an app removes it from the discovered set, even if an old provisioning profile remains. Apps belonging to other signing teams are excluded.

## Credential handling

- The Apple password stays in iLoader's Secret Service entry. It is not placed in command arguments, unit files, or persistent account metadata.
- Each run creates a private directory under the user runtime directory and a mode-600 `account.json` for AltKeeper. Normal completion, errors, and subprocess timeouts remove it. The systemd service also removes its runtime directory when it stops.
- Persistent metadata, Anisette state, and pairing keys live in `~/.local/share/altserver-refresh/`, restricted to your user. These are sensitive: **do not upload or share this directory**.
- A lock prevents overlapping runs. Discovery and all refresh attempts share a four-minute subprocess budget. Each discovered app gets a separate attempt, so one app's failure does not starve the others; the batch still reports failure to the journal.
- If the installed-app inventory cannot be read, the run stops before Apple sign-in or renewal. It never guesses from old profiles or app lists.
- The installer downloads only the pinned integration build. AltKeeper and iLoader retain their own licenses and security properties.

## Limits and verification

Verified on **CachyOS/KDE, Python 3.14, iLoader 2.4.1, and iOS 27.0.1**: keyring access, remote profile enumeration, Apple sign-in, and a new profile installed and read back over Wi-Fi. Long-term behavior after the old profile expires and other desktop environments have not been verified.

Important limits:

- Your Linux session must be active and the keyring unlocked. `Persistent=true` catches up after a missed timer event when your user session resumes; it cannot refresh while the machine is off.
- The phone must be reachable and recently unlocked during the final 24-hour renewal window. Sleeping, isolated Wi-Fi networks, firewalls, expired pairing, or new Apple 2FA requirements can prevent a run.
- Renewal uses the same Apple developer team that signed the apps. It does not recover a revoked signing certificate or bypass Apple's account, app, or certificate limits.
- This renews provisioning profiles; it does not install app updates or re-sign arbitrary IPA files. AltStore may continue displaying an old expiry date after external renewal.
- AltKeeper is experimental and uses private Apple APIs. This project cannot guarantee compatibility with future Apple changes.
- This first release supports one phone/account configuration and standard iLoader data paths. ARM Linux, Flatpak installations, and custom XDG data locations are not supported.

The `-ilr.1` build is required for automatic discovery. The wrapper reads its structured inventory and refreshes each discovered host with `--bundle`, never `--force`. Unmodified AltKeeper 0.5.6 also has an inventory-failure fallback that can bypass expiry checks; the integration build removes that fallback. It is installed separately as `~/.local/bin/altkeeper-ilr`.

## Development

No pip dependencies are required.

```sh
python3 -B -m unittest discover -s tests -v
systemd-analyze --user verify systemd/altserver-refresh.service systemd/altserver-refresh.timer
```

Tests use synthetic credentials and temporary fixture directories. They do not access your keyring, Apple account, or iPhone. When reporting a problem, redact account names, device identifiers, and authentication data from logs; never attach pairing files or the private state directory.

The native build workflow also runs upstream Rust tests plus inventory regression cases: lookup failures stay errors, an empty inventory stays empty, and a newly installed third app remains in the discovered set. See [native/UPSTREAM.md](native/UPSTREAM.md) to reproduce the binary build.

## Credits

- [nab138/iLoader](https://github.com/nab138/iloader): initial sideloading, saved credentials, and pairing.
- [BartolomeoRusso9/AltKeeper](https://github.com/BartolomeoRusso9/altkeeper): device communication, authentication, and profile renewal.
- [nab138/isideload](https://github.com/nab138/isideload) and [jkcoxson/idevice](https://github.com/jkcoxson/idevice): underlying signing and device libraries used upstream.
- The setup grew out of work on [AltServer for Arch Linux](https://github.com/elysiumorpheus/altserver-arch-linux).

Not affiliated with or endorsed by Apple, iLoader, AltKeeper, AltStore, or SideStore.

## License

The scripts, tests, unit files, and native patch in this repository are MIT licensed. See [LICENSE](LICENSE). The downloadable native archive includes upstream and third-party license notices; the upstream source commit and complete patch are linked in [native/UPSTREAM.md](native/UPSTREAM.md).
