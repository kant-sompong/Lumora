# Native Game Library on Windows

Native Game Library browses and downloads public Linux games from the
Internet Archive collection, and keeps a local library of what you download.
This document covers the Windows build: installation, where your files live,
and how to troubleshoot it.

## Two ways to run it

### Portable ZIP (recommended)

1. Download `NativeGameLibrary-<version>-windows-x64.zip` from the release or
   CI artifact.
2. Extract it anywhere you can write, for example `%USERPROFILE%\Apps\`.
3. Run `NativeGameLibrary.exe` from the extracted folder.

No installer, no Python, and no administrator rights are needed. Keep the
whole folder together; the executable needs the `_internal` directory beside
it.

### Installer

`NativeGameLibrary-<version>-windows-x64-setup.exe` installs per-user under
`%LOCALAPPDATA%\Programs\NativeGameLibrary` and adds Start Menu and optional
desktop shortcuts. It does not request elevation. Uninstalling removes the
application files only; your downloads and settings are left alone.

Windows SmartScreen may warn about an unsigned executable. There is no code
signing certificate for this project, so that warning is expected. Verify that
you downloaded the file from this repository's release page before choosing
"More info" → "Run anyway".

## Where things are stored

| What | Location |
| --- | --- |
| Settings | `%APPDATA%\NativeGameLibrary\config.json` |
| Download queue | `%LOCALAPPDATA%\NativeGameLibrary\downloads.json` |
| Library metadata, favorites | `%LOCALAPPDATA%\NativeGameLibrary\library.json` |
| Cached metadata and covers | `%LOCALAPPDATA%\NativeGameLibrary\Cache\` |
| Default download folder | your Windows **Downloads** folder, chosen in Settings |

Downloads go into `<chosen folder>\<archive identifier>\`. Changing the
download folder never moves existing files.

## What the app does and does not do

- It downloads files and never executes them automatically.
- Launching is only available from the review dialog, requires an explicit
  confirmation, and is refused while running as administrator.
- Existing files are never overwritten; downloads resume from a `.part` file
  after a pause, crash, or reboot.
- Games in this collection are mostly native Linux builds. The Windows app can
  download and inspect them, but running them on Windows is out of scope. The
  compatibility panel says what a game *likely* is and whether Wine, Proton,
  Java, or DOSBox might be involved; it never guarantees that a game runs.

## Requirements

- Windows 10 (build 1809 or later) or Windows 11, 64-bit.
- An internet connection.
- Roughly 200 MB free for the application; downloads need their own space.

Windows 10 is past the end of standard support. The build stays compatible
where the bundled runtime allows, but it does not receive security updates
from Microsoft. Prefer Windows 11; if you must use Windows 10, keep it patched
as far as your support options allow and run the app as a normal user.

## Troubleshooting

**The window does not appear.** Launch it from a terminal with
`NativeGameLibrary.exe` to keep a console attached, then read the message.
The app also writes `launch.log` beside any game it launches.

**Defender quarantines the file.** The executable is unsigned, and PyInstaller
builds are frequently flagged by heuristics. Add the extracted folder to your
exclusions only if you built it yourself or trust the release source.

**Downloads fail with 403 or 404.** The archive.org URL may have changed.
Retry once; the app asks archive.org for a fresh URL before giving up.

**"Not enough free disk space."** Free the amount named in the message, or
choose another download folder in Settings.

**Antivirus or Controlled Folder Access blocks writes.** Point the download
folder at a location your user account can write, such as `%USERPROFILE%`.

## Uninstalling

- Portable build: close the app and delete the extracted folder. Optionally
  delete `%APPDATA%\NativeGameLibrary` and `%LOCALAPPDATA%\NativeGameLibrary`
  to remove settings and library metadata.
- Installer: Settings → Apps → Native Game Library → Uninstall.

Neither path deletes your downloads.

## Building it yourself

The Windows binary must be built on Windows. From a checkout:

```powershell
python -m pip install pyinstaller
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
```

`dist\NativeGameLibrary\` holds the application, and the ZIP is written beside
it. Install Inno Setup 6 first if you also want the installer; `build.ps1`
picks it up automatically. The repository's GitHub Actions workflow
(`.github/workflows/build.yml`) runs the same steps on `windows-latest`, runs
the offline test suite, and uploads both artifacts.
