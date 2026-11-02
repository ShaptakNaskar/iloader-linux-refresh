# iLoader Linux Refresh

Automatic Wi-Fi profile renewal for iPhone apps signed with an account saved in iLoader.

This is an **unofficial Linux integration**. [iLoader](https://github.com/nab138/iloader) supplies the saved login and initial pairing; [AltKeeper](https://github.com/BartolomeoRusso9/altkeeper) handles Apple authentication and provisioning-profile renewal. This project supplies the credential handling, setup, and systemd timer.

The timer checks hourly. Selected app profiles are renewed when less than 24 hours remain—roughly six days after a seven-day profile is issued. It tries again at the next check if the phone is unavailable. iLoader can be closed after setup.

## Requirements

- Linux **x86-64**, Python **3.10+**, and a systemd user session.
- A working Secret Service keyring, such as KDE KWallet, and `secret-tool` from libsecret.
- iLoader **2.4.1**, with the signing account saved using **Save Credentials**.
- An iPhone on **iOS 17.4+**, with Developer Mode enabled and initial pairing completed.
- The computer and iPhone on the same network when renewal is due. The phone must have been unlocked recently.

On Arch/CachyOS, install the two script dependencies with:

```sh
sudo pacman -S --needed python libsecret
```

The installer downloads **AltKeeper 0.5.5** from its upstream GitHub release and verifies a pinned SHA-256 checksum. It does not install system packages or change the system iOS libraries.

## Setup

1. In iLoader, turn off **Settings → Don't use keyring**, then sign in and enable **Save Credentials**. A locked or disabled keyring prevents unattended sign-in.
2. Find your apps' **host app bundle IDs** under iLoader's **App IDs**. Copy the complete registered identifiers, including any team suffix. Choose the main app, not an extension or widget. Examples: `com.example.app.ABCDE12345` and `com.example.other.ABCDE12345`.
3. Clone this repository and run setup as your desktop user:

   ```sh
   git clone https://github.com/ShaptakNaskar/iloader-linux-refresh.git
   cd iloader-linux-refresh
   python3 install.py
   ```

Setup imports iLoader's Anisette identity and server selection. It reuses a filesystem remote-pairing record when available; otherwise, it asks you to connect and unlock the intended iPhone over USB for pairing. With multiple saved accounts or pairing files, it asks you to choose one.

Confirm the Apple account in AltKeeper's local password/2FA prompts and choose the developer team that signed your apps. When asked whether to save the password, you can choose **No**: future refreshes read iLoader's existing keyring entry. Even if you choose Yes, this setup step runs inside a temporary runtime directory and only account/team metadata is copied out.

Enter the host bundle IDs you want refreshed. Setup runs a dry check before installing and enabling the timer. Review the output: the selected apps should appear. Then unplug USB and verify the network path:

```sh
~/.local/bin/refresh-apps --dry-run
```

The dry run uses a seven-day threshold to exercise the selected profiles without installing new ones. A normal scheduled run uses a zero-day threshold because AltKeeper rounds remaining days down.

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

To add or remove apps, edit the `bundles` list in `~/.local/share/altserver-refresh/account-profile.json`, then run the dry check again. Only the selected apps and their related extension profiles are targeted. If you uninstall an app, remove it from this list too: a remaining provisioning profile can still be renewed.

## Credential handling

- The Apple password stays in iLoader's Secret Service entry. It is not placed in command arguments, unit files, or persistent account metadata.
- Each run creates a private directory under the user runtime directory and a mode-600 `account.json` for AltKeeper. Normal completion, errors, and subprocess timeouts remove it. The systemd service also removes its runtime directory when it stops.
- Persistent metadata, Anisette state, and pairing keys live in `~/.local/share/altserver-refresh/`, restricted to your user. These are sensitive: **do not upload or share this directory**.
- A lock prevents overlapping runs. The whole batch has a four-minute subprocess budget; one failed app does not starve the remaining selections, and the batch still reports failure to the journal.
- The installer downloads only the pinned upstream executable. AltKeeper and iLoader retain their own licenses and security properties.

## Limits and verification

Verified on **CachyOS/KDE, Python 3.14, iLoader 2.4.1, and iOS 27.0.1**: keyring access, remote profile enumeration, Apple sign-in, dry-run selection, and a new profile installed and read back over Wi-Fi. Long-term behavior after the old profile expires and other desktop environments have not been verified.

Important limits:

- Your Linux session must be active and the keyring unlocked. `Persistent=true` catches up after a missed timer event when your user session resumes; it cannot refresh while the machine is off.
- The phone must be reachable and recently unlocked during the final 24-hour renewal window. Sleeping, isolated Wi-Fi networks, firewalls, expired pairing, or new Apple 2FA requirements can prevent a run.
- Renewal uses the same Apple developer team that signed the apps. It does not recover a revoked signing certificate or bypass Apple's account, app, or certificate limits.
- This renews provisioning profiles; it does not install app updates or re-sign arbitrary IPA files. AltStore may continue displaying an old expiry date after external renewal.
- AltKeeper is experimental and uses private Apple APIs. This project cannot guarantee compatibility with future Apple changes.
- This first release supports one phone/account configuration and standard iLoader data paths. ARM Linux, Flatpak installations, and custom XDG data locations are not supported.

Pinned AltKeeper 0.5.5 has an inventory-failure fallback that can bypass expiry checks. To avoid that path, this wrapper always passes an explicit configured host bundle ID and never passes `--installed-only` or `--force`.

## Development

No pip dependencies are required.

```sh
python3 -B -m unittest discover -s tests -v
systemd-analyze --user verify systemd/altserver-refresh.service systemd/altserver-refresh.timer
```

Tests use synthetic credentials and temporary fixture directories. They do not access your keyring, Apple account, or iPhone. When reporting a problem, redact account names, device identifiers, and authentication data from logs; never attach pairing files or the private state directory.

## Credits

- [nab138/iLoader](https://github.com/nab138/iloader): initial sideloading, saved credentials, and pairing.
- [BartolomeoRusso9/AltKeeper](https://github.com/BartolomeoRusso9/altkeeper): device communication, authentication, and profile renewal.
- [nab138/isideload](https://github.com/nab138/isideload) and [jkcoxson/idevice](https://github.com/jkcoxson/idevice): underlying signing and device libraries used upstream.
- The setup grew out of work on [AltServer for Arch Linux](https://github.com/elysiumorpheus/altserver-arch-linux).

Not affiliated with or endorsed by Apple, iLoader, AltKeeper, AltStore, or SideStore.

## License

The scripts, tests, and unit files in this repository are MIT licensed. See [LICENSE](LICENSE). Upstream binaries and projects are not redistributed here.
