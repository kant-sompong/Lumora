# Updating Lumora

Lumora never replaces its own files. It reports what the latest release is and
links to it; installing is a decision the user makes in their own file manager or
package tool.

## Checking

- **Settings → Tools → Updates, backup and support** shows the running version
  and a `Check now` button.
- Automatic checks are **off by default**. Turning them on allows at most one
  check every seven days (configurable from 1 to 30) and only while online.
- A failed check is reported as a failure. It never claims the app is up to date.

## Linux

1. Download `Lumora-<version>-linux-x86_64.tar.gz` from the
   [releases page](https://github.com/kant-sompong/Lumora/releases).
2. Unpack it somewhere permanent, replacing the old directory.
3. Your library, collections and settings live in the config directory, not in
   the unpacked folder, so they survive the replacement.

## Windows

1. Download `Lumora-Setup-x64.exe` (per-user install) or
   `Lumora-Windows-x64.zip` (portable).
2. Run the installer, or unpack the zip over the old folder.
3. The installer upgrades in place; no uninstall step is needed.

## Verifying a download

Every release publishes SHA-256 checksums in its notes. Compare with:

```bash
sha256sum Lumora-0.6.6-linux-x86_64.tar.gz          # Linux
certutil -hashfile Lumora-Setup-x64.exe SHA256      # Windows
```

`updates.verify_artifact()` performs the same comparison in code. A file with no
published checksum is treated as untrusted rather than assumed good.

## If something breaks

Lumora keeps the previous version's data compatible: settings carry a version
number and migrate forward on start. If a new build will not start, the support
report (`Tools → Save support report`) lists versions and configuration without
including secrets, and the previous release is still on the releases page.
