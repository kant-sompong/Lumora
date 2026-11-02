# Lumora — Windows

Portable Windows build of Lumora, the same application as the Linux version.

## Requirements

- Windows 10 (version 1809 or later) or Windows 11, 64-bit
- No Python installation required — the executable is self-contained
- An internet connection for the archive.org collection, artwork and media metadata
- Optional: `aria2c.exe` for the external download engines (the built-in
  BitTorrent and HTTP engines work without it)

## Running

1. Extract `Lumora-Windows-x64.zip` to a folder of your choice (for example `C:\Lumora`).
2. Run `Lumora.exe` inside it.

The ZIP contains one top-level `Lumora` folder, so extracting keeps everything
together: `Lumora\Lumora.exe`, the Qt runtime and the bundled README.

## What is in the app

The Windows build is the Qt 6 (PySide6) frontend and shares the whole Python
core with the Linux GTK build, so the two look and behave the same:

- **Browse** — Internet Archive collection with filters (genre, size, released,
  licence, file format), sorting, cover grid or compact list, and a details panel
- **Library** — local game folders with covers, favourites, status, metadata and
  integrity checks
- **Downloads** — persistent queue with pause, resume, retry and a torrent strip
- **Media** — Movies & TV: TMDB trending/popular, anime by MyAnimeList ranking,
  search, My List and History, with an in-app provider picker and playback inside
  the window (Qt WebEngine / Chromium is bundled, fullscreen supported)
- **Settings** — General, Locations, Providers, Media, Shortcuts

## Data locations

| Data | Path |
|------|------|
| Settings | `%APPDATA%\Lumora\config.json` |
| Library index | `%LOCALAPPDATA%\Lumora\library.json` |
| Download queue | `%LOCALAPPDATA%\Lumora\downloads.json` |
| Cache | `%LOCALAPPDATA%\Lumora\Cache\` |
| Downloads | Your chosen folder (Settings → Download folder) |

Until 0.6.0 these folders were called `NativeGameLibrary`. The first launch
after the upgrade copies the settings, library, queue and catalogue documents
into `Lumora` and leaves the old folders untouched, so the previous build
still works and rolling back is simply deleting the new folders. API keys are
carried over from the old credential-manager service name too, and the old
entry is kept for the same reason.

## API keys (optional)

Settings → Providers accepts:

- **RAWG** — extra game metadata and covers
- **TMDB** — Movies & TV titles, posters and backdrops
- **MyAnimeList** — anime metadata (Client ID only, no login)

Keys are stored in the Windows Credential Manager; if that is unavailable they
fall back to an owner-only `config.json`. They are never written to the
catalogue, logs or error messages. Without a TMDB key the Media tab shows a
short setup card instead of empty rows.

## Building from source (Windows)

Requires Windows 10/11 and Python 3.10+ (CI uses 3.12).

```powershell
python -m pip install --upgrade pip
pip install PySide6 PySide6-WebEngine pillow pyinstaller

python -m PyInstaller --noconfirm --clean packaging\windows\Lumora.spec
Compress-Archive -Path dist\Lumora -DestinationPath dist\Lumora-Windows-x64.zip
```

The portable application is the folder `dist\Lumora` (run `Lumora.exe` inside
it). `.github/workflows/release.yml` performs exactly these steps on a Windows
runner for every `v*` tag and attaches `Lumora-Windows-x64.zip` to the release.

## Troubleshooting

- **SmartScreen warning** — the executable is unsigned. Choose *More info* →
  *Run anyway*.
- **Antivirus false positive** — some heuristics flag PyInstaller binaries. The
  build is clean; whitelist the folder if needed.
- **Missing DLLs** — re-extract the ZIP; do not move `Lumora.exe` out of its
  folder.
- **Blank Media tab** — add a TMDB API key in Settings → Providers.

## License

MIT — see the repository `README.md`.
