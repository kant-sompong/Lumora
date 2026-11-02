<p align="center">
  <img src="docs/screens/browse-grid.png" alt="Lumora — Browse the Internet Archive collection" width="960">
</p>

<h1 align="center">Lumora</h1>

<p align="center">
  <strong>Browse, download, organise and play your game library — plus a Movies&nbsp;&amp;&nbsp;TV section for everything else.</strong><br>
  One shared Python core, two native frontends: GTK&nbsp;3 on Linux, Qt&nbsp;6 on Windows.
</p>

<p align="center">
  <a href="https://github.com/kant-sompong/Lumora/releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/kant-sompong/Lumora?style=flat-square&label=release"></a>
  <a href="https://github.com/kant-sompong/Lumora/actions/workflows/release.yml"><img alt="Build status" src="https://img.shields.io/github/actions/workflow/status/kant-sompong/Lumora/release.yml?branch=main&style=flat-square&label=CI"></a>
  <img alt="Platforms" src="https://img.shields.io/badge/platforms-Windows%20%7C%20Linux-2f6feb?style=flat-square">
  <img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-3776ab?style=flat-square">
  <img alt="Qt" src="https://img.shields.io/badge/Qt-6%20%2F%20PySide6-41cd52?style=flat-square">
  <a href="#license"><img alt="License" src="https://img.shields.io/badge/license-MIT-3fb950?style=flat-square"></a>
</p>

---

Lumora is a desktop application for the Internet Archive's
[`native-linux-games-collection`](https://archive.org/details/native-linux-games-collection)
with a local library, a persistent download queue, and a TMDB-powered Movies & TV
section. It never runs anything you haven't reviewed, never launches as root,
and keeps every file inside the folder you choose.

```text
Browse  →  Download  →  Library  →  Play
                     ↘  Movies & TV  →  Stream
```

## Table of contents

- [Screenshots](#screenshots)
- [Windows build](#windows-build)
- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Keyboard shortcuts](#keyboard-shortcuts)
- [Where data is stored](#where-data-is-stored)
- [Building from source](#building-from-source)
- [Testing](#testing)
- [Architecture](#architecture)
- [Safety & limits](#safety--limits)
- [Troubleshooting](#troubleshooting)
- [Roadmap & known gaps](#roadmap--known-gaps)
- [Contributing](#contributing)
- [License](#license)

---

## Screenshots

### Browse the collection

| Cover grid | Wide covers | Compact list |
| :---: | :---: | :---: |
| ![Browse grid](docs/screens/browse-grid.png) | ![Wide covers](docs/screens/browse-covers.png) | ![List view](docs/screens/browse-list.png) |

Filters for genre, size, release year, licence and file format; sorting by year,
size, downloads or creator; lazy-loaded artwork; and a details panel with the
file list and one-click download.

### Library, downloads and sources

| Library with selected game | Download queue | Website sources |
| :---: | :---: | :---: |
| ![Library](docs/screens/library-detail.png) | ![Downloads](docs/screens/downloads.png) | ![Website sources](docs/screens/website-sources.png) |

### Movies & TV

| Home | Movies | TV Shows |
| :---: | :---: | :---: |
| ![Media home](docs/screens/media-home.png) | ![Movies](docs/screens/media-movies.png) | ![TV Shows](docs/screens/media-tv.png) |

| Anime | My List | History |
| :---: | :---: | :---: |
| ![Anime](docs/screens/media-anime.png) | ![My List](docs/screens/media-list.png) | ![History](docs/screens/media-history.png) |

### Settings

| General | Providers | Media & playback | Shortcuts |
| :---: | :---: | :---: | :---: |
| ![General](docs/screens/settings-general.png) | ![Providers](docs/screens/settings-providers.png) | ![Media settings](docs/screens/settings-media.png) | ![Shortcuts](docs/screens/settings-shortcuts.png) |

<details>
<summary><strong>More screenshots</strong> — catalogue browser, locations, Kryoto catalog</summary>

| Kryoto catalogue | Library grid | Locations |
| :---: | :---: | :---: |
| ![Kryoto catalog](docs/screens/kryoto-catalog.png) | ![Library](docs/screens/library.png) | ![Locations](docs/screens/settings-locations.png) |

</details>

---

## Windows build

The Windows frontend is Qt 6 (PySide6) and shares the entire Python core with the
Linux build, so both look and behave the same: same rail, same header, same
sections, same theme tokens.

| Browse | Library | Providers |
| :---: | :---: | :---: |
| ![Windows browse](docs/screens/windows-browse.png) | ![Windows library](docs/screens/windows-library.png) | ![Windows settings](docs/screens/windows-settings.png) |

The portable build bundles Chromium (Qt WebEngine) for in-app playback and needs
no Python installation:

```text
Lumora-Windows-x64.zip   →   Lumora\Lumora.exe
```

---

## Features

### Browse and discovery
- Collection index from archive.org's `advancedsearch.php`, cached for six hours
  with a stale-cache fallback when offline
- Search by title, identifier, year, creator or subject
- Filters for genre, size, release year, licence and file format, with live counts
- Sort by title, year, size, downloads, creator or genre, ascending or descending
- Cover grid or compact list, with artwork loaded lazily as you scroll
- **Website sources and catalogues** — import any catalogue page the shipped
  adapters can verify (archive.org, Kryoto, RAWG, JSON/HTML), straight into the
  local catalogue

### Local library
- Scans the download folder into `library.json` with a manifest per game
- Favourites, status badges (downloaded / installed / incomplete / missing)
- Metadata with provenance: developer, publisher, release date, genres,
  platforms, notes — catalogue refreshes never overwrite what you typed
- Best-effort platform detection from the files on disk
- Integrity verification against published archive.org checksums
- Duplicate finder, export/import, forget-missing, and a confirmed
  **Remove from library** action that leaves your files untouched
- Reviewed launch: you choose the executable, the path stays inside the download
  folder, and elevated launches are refused

### Downloads
- Persistent queue that survives restarts — pause, resume, cancel, retry, remove
- Segmented HTTP transfers with Range requests and automatic resume
- Disk-space preflight, atomic `.part` → final rename, bounded retries
- Magnet links through a built-in BitTorrent engine, or an external client such
  as `aria2c` if you prefer one

### Movies & TV
- Cinematic home with Continue Watching and Trending carousels
- Movies, TV Shows and Anime sections, plus Search, My List and History
- TMDB metadata; MyAnimeList ranking for anime; ani.pm catalogue for titles
  missing from both
- **Episode selector** with a season picker and episode rows (number, title,
  air date)
- **Provider picker** in the Media bar, saved per library and switched
  automatically for anime
- **Embedded playback** in the app — Qt WebEngine (Chromium) on Windows,
  WebKit2GTK on Linux — with fullscreen from the provider page

### Settings
- General, Locations, Providers, Media and Shortcuts tabs
- Light, dark and system themes, applied live without a restart
- API keys stored in the OS keyring (libsecret / Credential Manager)

---

## Requirements

| | Windows | Linux |
| --- | --- | --- |
| OS | Windows 10 (1809+) or Windows 11, 64-bit | Any modern distribution |
| Runtime | None — the portable build is self-contained | Python 3.10+ and GTK 3 |
| Playback | Bundled Qt WebEngine | WebKit2GTK + GStreamer (optional) |
| Disk | ~500 MB unpacked | A few MB plus dependencies |

An internet connection is needed for the archive.org collection, artwork,
metadata and streaming.

---

## Installation

### Windows

Every release publishes two builds:

- **`Lumora-Setup-x64.exe`** — a per-user installer (no administrator prompt),
  with Start-menu and optional desktop shortcuts and a normal uninstaller.
- **`Lumora-Windows-x64.zip`** — portable: extract the single `Lumora` folder
  anywhere and run `Lumora.exe`.

The executable is unsigned, so SmartScreen may warn: choose **More info → Run
anyway**. Some antivirus heuristics also flag PyInstaller binaries; that is a
known false positive.

### Linux (from source)

| Distribution | Command |
| --- | --- |
| Void Linux | `sudo xbps-install -Su python3 gtk+3 python3-gobject python3-cairo gdk-pixbuf xdg-utils` |
| Debian / Ubuntu | `sudo apt install python3 python3-gi python3-gi-cairo gir1.2-gtk-3.0 xdg-utils` |
| Fedora | `sudo dnf install python3 python3-gobject gtk3 xdg-utils` |
| Arch | `sudo pacman -S python python-gobject gtk3 xdg-utils` |

```sh
git clone https://github.com/kant-sompong/Lumora.git
cd Lumora
./run.sh              # or: python3 -m lgl
```

Optional for embedded playback: `webkit2gtk` (or `webkit2gtk-4.1`) and the
GStreamer plugin set.

---

## Configuration

Add keys in **Settings → Providers**. They are stored in the OS keyring and never
written to the catalogue, logs or error messages.

| Provider | Used for | Where to get a key |
| --- | --- | --- |
| TMDB | Movies & TV titles, posters, backdrops | <https://www.themoviedb.org/settings/api> |
| RAWG | Extra game metadata and covers | <https://rawg.io/apidocs> |
| MyAnimeList | Anime titles and rankings (Client ID only) | <https://myanimelist.net/apiconfig> |

Without a TMDB key the Media tab shows a short setup card instead of empty rows.

**Streaming providers** (Settings → Media, or the picker in the Media bar):

| Provider | Movies / TV | Anime | Notes |
| :--- | :---: | :---: | :--- |
| CineSrc | ✓ | | TMDB id |
| Rive | ✓ | ✓ | TMDB id, anime by TMDB |
| ToFlix | ✓ | ✓ | AniList id |
| CineLite | ✓ | ✓ | AniList id |
| frameXTV | ✓ | | TMDB id |
| Atlantic | ✓ | | TMDB id |
| ani.pm | | ✓ | AniList or MAL id, no key required |

---

## Keyboard shortcuts

| Shortcut | Action |
| --- | --- |
| `Ctrl+1` | Browse |
| `Ctrl+2` | Library |
| `Ctrl+3` | Downloads |
| `Ctrl+4` | Media |
| `Ctrl+F` / `Ctrl+K` | Focus the search field |
| `Ctrl+R` / `F5` | Reload the collection, rescan the library |
| `Ctrl+,` | Settings |
| `Escape` | Dismiss a notice or clear the search |

---

## Where data is stored

| Data | Windows | Linux |
| --- | --- | --- |
| Settings | `%APPDATA%\NativeGameLibrary\config.json` | `$XDG_CONFIG_HOME/linux-game-library/config.json` |
| Library index | `%LOCALAPPDATA%\NativeGameLibrary\library.json` | `$XDG_DATA_HOME/linux-game-library/library.json` |
| Download queue | `%LOCALAPPDATA%\NativeGameLibrary\downloads.json` | `$XDG_DATA_HOME/linux-game-library/downloads.json` |
| Cache | `%LOCALAPPDATA%\NativeGameLibrary\Cache\` | `$XDG_CACHE_HOME/linux-game-library/` |
| Downloads | Your chosen folder | Your chosen folder |

The `NativeGameLibrary` folder names are kept on purpose: the app was renamed to
Lumora in 0.2.6 and existing settings, libraries and queues carry over unchanged.

---

## Building from source

### Windows portable ZIP

Requires Windows 10/11 and Python 3.10+ (CI uses 3.12).

```powershell
python -m pip install --upgrade pip
pip install PySide6 PySide6-WebEngine pillow pyinstaller

python -m PyInstaller --noconfirm --clean packaging\windows\Lumora.spec
Compress-Archive -Path dist\Lumora -DestinationPath dist\Lumora-Windows-x64.zip

# Optional: the installer (requires Inno Setup 6)
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /DMyAppVersion=0.5.0 `
  packaging\windows\Lumora.iss
# → dist/Lumora-Setup-x64.exe
```

Qt 6.12 ships WebEngine as a separate `PySide6-WebEngine` wheel; without it the
build compiles but playback falls back to the browser.

### Linux portable tarball

```sh
sh packaging/linux/build-tarball.sh
# → dist/Lumora-<version>-linux-<arch>.tar.gz
```

### CI

[`.github/workflows/release.yml`](.github/workflows/release.yml) runs the test
suite, builds the Windows ZIP and the Inno Setup installer, builds the Linux
tarball on every push and pull request, and verifies that the WebEngine player
files are actually bundled. Pushing a `v*` tag publishes a GitHub release with
all three assets; the tag must match `lgl.__version__`.

---

## Testing

```sh
python -m pytest tests -q
```

The Qt tests run headlessly (`QT_QPA_PLATFORM=offscreen`) and build the real
window: navigation, Browse filtering and rendering, the library grid and detail
panel, download rows end to end against a local HTTP server, media cards and
posters, the episode picker, provider switching, the catalogue dialog, the
artwork picker and the screenshot viewer.

The core has its own tests, which need no display: the download queue (pause,
cancel, retry, duplicate and path rules, queue persistence), archive URL and
poster rules, folder scanning and the library index, checksum verification,
source configuration, a full import run over a fake source, and the update
check's version comparison. 149 tests in total.

---

## Architecture

```text
lgl/
├── __main__.py          # Entry point: Qt on Windows, GTK on Linux, Tk fallback
│
├── Frontends
│   ├── ui.py            # GTK 3 (Linux)
│   ├── theme_gtk.py     # GTK CSS generated from theme.py
│   ├── qt_main.py       # Qt 6 window: rail, header, pages, menu
│   ├── qt_theme.py      # QSS generated from the same tokens
│   ├── qt_widgets.py    # Shared Qt widgets
│   ├── qt_browse.py     # Browse: filters, grid/list, details
│   ├── qt_library.py    # Library grid, filters, metadata editor
│   ├── qt_downloads.py  # Queue and torrent strip
│   ├── qt_media.py      # Movies & TV: hero, carousels, search, player
│   ├── qt_settings.py   # Settings and About
│   ├── qt_catalog.py    # Website sources / catalogue import
│   └── qt_icons.py      # Vector icons drawn with QPainter
│
├── Core
│   ├── theme.py         # Shared design tokens
│   ├── archive.py       # archive.org client, metadata, image cache
│   ├── downloader.py    # Persistent queue, Range resume
│   ├── library.py       # Folder layout, manifests, local scan
│   ├── library_store.py # Index, favourites, duplicates, export/import
│   ├── metadata.py      # Normalised metadata with provenance
│   ├── integrity.py     # Checksum verification
│   ├── compatibility.py # Platform / format heuristics
│   ├── launch.py        # Reviewed launch (no shell, no root)
│   ├── torrent.py       # Magnet queue + BitTorrent engine
│   ├── bittorrent.py    # Native engine
│   ├── sources.py       # Adapter framework for website catalogues
│   ├── importer.py      # Resumable catalogue importer
│   ├── catalog_store.py # Imported catalogues, magnets, mirrors
│   ├── tmdb.py / mal.py # Metadata clients
│   ├── streaming.py     # Provider URL builders
│   └── settings.py      # Preferences + keyring-backed API keys
```

The two frontends never import each other's toolkit: `qt_*.py` imports no GTK and
`ui.py` imports no Qt. They share the core and the design tokens, which is why
the Windows build matches the Linux one.

---

## Safety & limits

- **Nothing runs automatically** — every launch passes through a review dialog
- **No elevated launches** — root/administrator starts are refused
- **Path confinement** — launches stay inside the chosen download folder
- **HTTPS and host allow-lists** for provider URLs
- **No secrets in logs** — API keys are redacted everywhere
- **Polite to the sources** — bounded request rates and one transfer at a time

---

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `ImportError: cannot import name … from gi.repository` | Install GTK 3 + PyGObject, or use the Tk fallback |
| Blank window on Linux | Check `DISPLAY` / `WAYLAND_DISPLAY`; try `GDK_BACKEND=wayland ./run.sh` |
| "Could not reach archive.org" | Test `curl -I https://archive.org/metadata/native-linux-games-collection`; a stale cache is used when available |
| HTTP 429 / slow metadata | Wait and press **Refresh**; request rates are bounded on purpose |
| Media tab shows a setup card | Add a free TMDB key in Settings → Providers |
| Playback opens in a browser | Running from source without Qt WebEngine — install `PySide6` **and** `PySide6-WebEngine`, or use the released ZIP |
| SmartScreen warning | Unsigned PyInstaller build — *More info → Run anyway* |

---

## Roadmap & known gaps

- No macOS frontend
- No automatic updater — *Check for updates…* reports a newer release and
  opens its page; installing it stays a manual step
- No Steam / itch.io / GOG import (archive.org and imported catalogues only)
- No automatic game launch after a download finishes
- Windows builds are compiled and smoke-tested in CI, but interactive testing on
  physical hardware is done by users

---

## Contributing

Issues and pull requests are welcome. Please:

- describe bugs with reproduction steps and the platform you used
- include relevant logs, with API keys redacted
- run `python -m pytest tests -q` before opening a pull request

Screenshots live in [`docs/screens`](docs/screens) — regenerating them for a UI
change makes review much easier.

---

## License

MIT. Third-party components: aria2 (GPLv2, only when bundled as `aria2c.exe`),
PyInstaller bootloader (GPLv2 with a runtime exception), Qt 6 / PySide6
(LGPLv3), and Chromium via Qt WebEngine (BSD-style licences).

<p align="center"><sub>Built with Python, GTK and Qt — for people who still own their files.</sub></p>
