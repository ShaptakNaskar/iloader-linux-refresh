# AltKeeper build used by iLoader Linux Refresh

This directory contains a small MIT-licensed change to AltKeeper's renewal policy, not a separate signing implementation.

- Upstream: https://github.com/BartolomeoRusso9/altkeeper
- Base release: **0.5.6**
- Exact source commit: **50dc10e25485e508e89d1114f7413da50587d343**
- Integration version: **0.5.6-ilr.1**
- Changes: [fail-closed.patch](fail-closed.patch)

Upstream's `--installed-only` mode already discovers installed apps, filters their profiles to the configured Apple developer team, groups host apps/extensions, and checks expiry. However, the original lookup-error path renews every known team profile regardless of expiry. This patch makes that error stop the run before Apple sign-in or renewal. It does not change signing, certificates, app IDs, pairing, or successful inventory handling.

`build.sh` fetches the exact source commit, applies the patch, runs the upstream Rust tests, and packages the executable with upstream license/notices, this patch, and the compiler version. `Cargo.lock` remains locked; its package version is changed to distinguish the patched executable from the upstream release. Builds run on Ubuntu 22.04 for glibc 2.35 compatibility.

To reproduce the build on Linux x86-64 with Git and a current stable Rust toolchain:

```sh
bash native/build.sh
```

The result is written to `dist/`. Normal users download the checksum-pinned binary through `install.py` and do not need Rust.

Modified upstream MPL-2.0 sources are available at https://github.com/BartolomeoRusso9/altkeeper/tree/50dc10e25485e508e89d1114f7413da50587d343/vendor/omnisette and are unchanged by this integration. Other third-party notices are included in the archive's `THIRD-PARTY-NOTICES.md`.
