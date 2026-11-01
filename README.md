# Native Game Library

A cross-platform desktop application for browsing, downloading, and managing games from the Internet Archive's **native-linux-games-collection**. Runs on **Linux (GTK 3)** and **Windows 10/11 (Tkinter)** with a shared Python core.

---

## Platform support

| Platform | Status |
| --- | --- |
| Linux (Void, Debian, Ubuntu, Fedora, Arch, etc.) | **Developed & tested** — GTK 3 frontend with embedded WebKit2 media player |
| Windows 11 | **Implemented** — Tkinter frontend; portable ZIP & optional installer via CI |
| Windows 10 | **Implemented** — Same as Windows 11; Windows 10 is past standard support |
| macOS | Not supported (no frontend) |

> **Do not read "implemented" as "verified on hardware".** The Windows executable is built on `windows-latest` in CI and smoke-tested there (starts and stays running). Interactive testing on a real Windows desktop has not been performed by the author.

---

## Features

### Game library & discovery
- **Collection index** from archive.org `advancedsearch.php` (≈ 471 items), cached for 6 hours with stale-cache fallback
- **Search** by title, identifier, year, creator, subject
- **Details** from archive.org metadata: title, creator, date, subjects, license, description, file list with sizes
- **Filters** by genre, size range, release year, license, file format
- **Sort** by title, date, size, creator, year (asc/desc)
- **Views**: cover grid or compact list

### Local library management
- **Scan** download folder → builds `library.json` index with manifests
- **Favorites**, status badges (installed / downloaded / incomplete / missing)
- **Duplicate finder** (by name + size)
- **Integrity verification** against archive.org checksums (MD5, SHA1, SHA256)
- **Export / import** library index as JSON
- **Per-game notes & magnet links** stored in `library.json`

### Downloads
- **Persistent queue** (`downloads.json`) — survives restart, supports pause/resume/cancel/retry
- **Parallel segmented downloads** with Range requests, automatic resume on reconnect
- **Disk-space preflight** before starting
- **Atomic rename** on completion (`.part` → final name)
- **External download manager hand-off** (JDownloader 2, AB Download Manager) for difficult mirrors
- **Magnet link support** via built-in BitTorrent engine (no external client required) or optional `aria2c`

### Metadata & artwork
- **Automatic artwork sync** from archive.org screenshots / covers
- **Manual artwork matching** when auto-detection is uncertain
- **Provenance tracking** — every metadata field knows its source (archive.org, user edit, sidecar)
- **Sidecar JSON** (`game.json`) written per download with full metadata

### Movies & TV (Linux GTK frontend only)
- **Trending, popular, search** via TMDB
- **Anime tab** — MAL popular ranking, episode lists, sub/dub selector
- **Embedded playback** via WebKit2 (frameXTV, VidRift, Atlantic, ani.pm)
- **Watchlist & history** persisted locally
- **Autoplay / default audio language** settings

### Windows Tk frontend
- Same **Browse / Downloads / Library** tabs
- **Media tab** — browse movies, TV, anime; click **Watch** to open provider in system browser
- **Settings** — download folder, theme, provider API keys (RAWG, TMDB, MAL), streaming provider, anime provider, default audio, autoplay
- No WebKit dependency — playback always opens in browser

---

## Architecture

```
lgl/
├── __main__.py          # Entry point: picks GTK (Linux) or Tk (Windows)
├── platform_support.py  # OS abstraction: paths, open_folder, open_url, admin check, detached Popen
├── ui.py                # GTK 3 frontend (Linux) — 12k lines
├── windows_app.py       # Tkinter frontend (Windows + Linux fallback) — 4.5k lines
├── windows_media.py     # Media browser for Tk frontend
├── movies_ui.py         # Movies & TV (GTK + WebKit2)
├── archive.py           # archive.org client + image cache
├── catalog.py           # Source policy, robots.txt, URL validation
├── sources.py           # Adapter framework (archive.org, FreeToGame, etc.)
├── catalog_store.py     # Imported catalogs, magnets, mirrors
├── importer.py          # Resumable paged catalog importer
├── downloader.py        # Persistent queue, Range resume, parallel segments
├── library.py           # Folder layout, manifest, local scan
├── library_store.py     # Library index, favorites, duplicates, export/import
├── metadata.py          # Normalized metadata + provenance merges
├── magnet.py            # Magnet URI parsing & validation
├── integrity.py         # Checksum verification
├── compatibility.py     # Platform/format heuristics
├── launch.py            # Reviewed launch (no shell, no root)
├── torrent.py           # Built-in BitTorrent + optional aria2c RPC
├── bittorrent.py        # Native engine implementation
├── settings.py          # Preferences + OS keyring for API keys
├── tmdb.py              # TMDB v3 client
├── mal.py               # MyAnimeList client (Client-ID only)
├── streaming.py         # Provider URL builders (frameXTV, VidRift, Atlantic, ani.pm)
├── media_history.py     # Playback history store
├── style.css            # GTK theme (CSS variables)
├── tk_style.py          # Tk theme, fonts, DPI awareness
└── ...                  # 40+ modules total
```

---

## Install & run

### Linux (from source)

**Runtime dependencies (Void Linux):**
```sh
sudo xbps-install -Su python3 gtk+3 python3-gobject python3-cairo gdk-pixbuf xdg-utils
```
Debian/Ubuntu: `python3-gi python3-gi-cairo gir1.2-gtk-3.0`  
Fedora: `python3-gobject gtk3`

**Run:**
```sh
cd ~/Apps/linux-game-library
./run.sh            # or: python3 -m lgl
```

**Tk fallback** (if GTK unavailable):
```sh
python3 -m lgl.windows_app
```

### Windows

1. Download `NativeGameLibrary-<version>-windows-x64.zip` from [Releases](https://github.com/kant-sompong/Lumora/releases) or CI artifacts.
2. Extract and run `NativeGameLibrary.exe`. No Python install needed.
3. Optional per-user installer (`.exe`) is also published when available.

**Storage locations (Windows):**
| Data | Path |
|------|------|
| Settings | `%APPDATA%\NativeGameLibrary\config.json` |
| Library index | `%LOCALAPPDATA%\NativeGameLibrary\library.json` |
| Download queue | `%LOCALAPPDATA%\NativeGameLibrary\downloads.json` |
| Cache | `%LOCALAPPDATA%\NativeGameLibrary\Cache\` |
| Downloads | User-chosen folder (Settings → Download folder) |

### Linux storage locations
| Data | Path |
|------|------|
| Settings | `$XDG_CONFIG_HOME/linux-game-library/config.json` (`~/.config/...`) |
| Library index | `$XDG_DATA_HOME/linux-game-library/library.json` (`~/.local/share/...`) |
| Download queue | `$XDG_DATA_HOME/linux-game-library/downloads.json` |
| Cache | `$XDG_CACHE_HOME/linux-game-library/` (`~/.cache/...`) |
| Downloads | User-chosen folder (Settings → Download folder) |

---

## Building packages

### Linux portable tarball
```sh
sh packaging/linux/build-tarball.sh
# → dist/NativeGameLibrary-<version>-linux-<arch>.tar.gz
```
Creates a reproducible tarball with the application, `run.sh`, icons, desktop-entry installer, and `.env.example`. GTK 3, PyGObject, WebKit2GTK, and GStreamer come from the distribution.

### Windows portable ZIP (must run on Windows)
```powershell
python -m pip install pyinstaller
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
# → dist\NativeGameLibrary-<version>-windows-x64.zip
```
Add `-BundleAria2` to include `aria2c.exe` (GPLv2) for magnet support:
```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1 -BundleAria2
```
An Inno Setup installer (`.exe`) is also produced if Inno Setup 6 is installed.

---

## Configuration

**Provider API keys** (Settings → Providers on GTK, Settings → Providers card on Tk):
- **RAWG** — game metadata & covers
- **TMDB** — movies/TV metadata, posters, backdrops
- **MyAnimeList** — anime metadata (Client-ID only, no OAuth)

Keys are stored in the OS keyring (libsecret on Linux, Credential Manager on Windows). If the keyring is unavailable, they fall back to the owner-only config file (`config.json`, mode 0600).

**Streaming providers** (Settings → Media):
| Provider | Movies/TV | Anime | Notes |
|----------|-----------|-------|-------|
| frameXTV | ✓ | ✗ | TMDB ID |
| VidRift | ✓ | ✓ (via TMDB) | TMDB ID, `?dub=1` for dub |
| Atlantic | ✓ | ✗ | TMDB ID |
| ani.pm | ✗ | ✓ | AniList or MAL ID, no key required |

---

## Tests

```sh
python3 -m unittest discover -s tests -v
```
**1063 tests** covering:
- Archive parsing & caching
- Artwork selection & caching
- Download queue persistence & recovery
- Range validation & resume
- Duplicate prevention & retry limits
- URL refresh & host validation
- Library scanning, favorites, genre filtering, sorting, export/import
- Schema v1 → v2 migration
- Metadata normalization & provenance
- Magnet parsing & validation
- Torrent engine discovery & native engine
- Platform support (paths, admin, execute bits, pwrite fallback)
- UI widget tests (GTK, headless)

---

## Safety & limits

- **No auto-execution** — every launch goes through a review dialog
- **No root/elevated launches** — refused explicitly
- **Path confinement** — all launches confined to the chosen download folder
- **HTTPS only** — provider URLs validated against allow-listed hosts
- **No secrets in logs** — API keys redacted everywhere
- **Unsigned Windows binary** — SmartScreen may warn (expected for PyInstaller)
- **Single-threaded downloads** — one connection at a time to be polite to archive.org

---

## Project structure

```
linux-game-library/
├── run.sh                       # Linux launcher
├── README.md
├── .github/workflows/build.yml  # CI: tests + Linux/Windows artifacts
├── packaging/
│   ├── install-desktop.sh       # Per-user Linux .desktop entry
│   ├── linux/build-tarball.sh   # Portable Linux tarball
│   └── windows/
│       ├── build.ps1            # PyInstaller + optional Inno Setup
│       ├── NativeGameLibrary.spec
│       ├── installer.iss
│       ├── launcher.py
│       └── README-Windows.md
├── lgl/                         # Application package (see Architecture)
├── tests/                       # 1063 unit/integration tests
└── docs/screenshot.png
```

---

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `ImportError: cannot import name ... from gi.repository` | Install `gtk+3` + `python3-gobject`; Tk fallback also works |
| Blank window / no display | Ensure `WAYLAND_DISPLAY` or `DISPLAY` is set; try `GDK_BACKEND=wayland ./run.sh` |
| "Could not reach archive.org" | Test `curl -I https://archive.org/metadata/native-linux-games-collection`; stale cache is used when available |
| HTTP 429 / slow metadata | Wait and press **Refresh**; concurrency limited to 1 download |
| Windows SmartScreen warning | Expected for unsigned PyInstaller build; click "More info → Run anyway" |

---

## Not implemented / known gaps

- No macOS frontend
- Windows media playback opens in browser (no WebKit embed)
- No automatic game launch on download completion
- No multi-user / multi-profile support
- No Steam/itch.io/GOG library integration (archive.org only)
- No built-in updater (re-download new release)

---

## License

MIT License — see `LICENSE` (to be added).  
Third-party components: aria2 (GPLv2, bundled only with `-BundleAria2`), PyInstaller bootloader (GPLv2 with runtime exception).

---

## Contributing

Issues and PRs welcome. Please:
- Describe bugs with reproduction steps
- Include relevant logs (redact API keys)
- Run `python3 -m unittest discover -s tests -q` before submitting

---

## Verified during development (on Void Linux / Wayland)

- Offline suite: **1063 tests pass**
- Live archive.org collection fetch (471 items)
- Live metadata & poster fetch with disk cache
- Real file download with progress, speed, ETA, atomic rename, `game.json`
- GTK browser & Library view under Wayland: cover grid, status badges, search, favorites, stats
- Queue restoration across restart (paused task recovered, nothing auto-started)
- Tkinter frontend on Linux Tk: Browse with posters, Downloads with restored task, Library with rescan
- Screenshot: `docs/screenshot.png`

Windows: frontend & packaging implemented and exercised on Linux Tk + CI; **no interactive Windows verification performed**.