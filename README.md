# Lumora

A cross-platform desktop app for browsing, downloading and playing games from
the Internet Archive, with a Movies & TV section for everything else.

One shared Python core, two native frontends: **GTK 3 on Linux** and
**Qt 6 / PySide6 on Windows**. Both builds use the same theme tokens, so the
Windows version looks and behaves like the Linux one.

![Lumora](docs/screenshot.png)

**Current release: 0.3.6**

---

## Platform support

| Platform | Status |
| --- | --- |
| Linux (Void, Debian, Ubuntu, Fedora, Arch, …) | **Developed & tested** — GTK 3 frontend, WebKit2 playback |
| Windows 11 | **Implemented** — Qt 6 (PySide6) frontend, portable build |
| Windows 10 (1809+) | **Implemented** — same build as Windows 11 |
| macOS | Not supported (no frontend) |

The Windows build is compiled and smoke-tested on GitHub Actions
(`windows-latest`): the executable starts, stays running and loads every page.
Interactive testing on a physical Windows desktop is still done by users, not
by CI.

---

## Features

### Browse the collection
- **Internet Archive index** from `advancedsearch.php` (≈ 471 items), cached for
  six hours with a stale-cache fallback when offline
- **Search** by title, identifier, year, creator or subject
- **Filters** — genre, size range, release year, licence, file format, with live
  counts per genre and a one-click reset
- **Sort** by title, year, size, downloads, creator or genre, ascending or
  descending
- **Views** — wide cover grid or compact list; covers lazy-load as you scroll
- **Details panel** — description, size, file list, licence, download count and
  a one-click download
- **Website sources & catalogues** — the header menu adds any catalogue page the
  shipped adapters can verify (archive.org, Kryoto, RAWG, JSON/HTML) and imports
  it into the local catalogue; a Maintenance submenu rescans, reloads and clears
  the image cache

### Local library
- **Scan** the download folder into `library.json` with per-game manifests
- **Favourites**, status badges (downloaded / installed / incomplete / missing)
- **Metadata** — developer, publisher, release date, genres, platforms, notes,
  with provenance so catalogue refreshes never overwrite what you typed
- **Compatibility** — best-effort platform detection from the files on disk
- **Integrity verification** against published archive.org checksums
  (MD5 / SHA1 / SHA256)
- **Duplicate finder**, library export/import, and "forget missing entries"
- **Reviewed launch** — you pick the executable, the path is confined to the
  download folder, and nothing is ever started as root

### Downloads
- **Persistent queue** (`downloads.json`) that survives restarts —
  pause, resume, cancel, retry, remove, clear finished
- **Segmented HTTP downloads** with Range requests and automatic resume
- **Disk-space preflight**, atomic `.part` → final rename, retry limits
- **Torrent strip** — magnet links through the built-in BitTorrent engine, or an
  external client such as `aria2c` when you configure one

### Movies & TV
- **Home** with a cinematic hero, Continue Watching and Trending carousels
- **Movies**, **TV Shows** and **Anime** sections, plus **Search**, **My List**
  and **History**
- **Provider picker** in the Media bar — pick the streaming provider for movies/TV,
  or the anime provider while the Anime section is open; the choice is saved and
  used by the next Watch Now
- **TMDB** metadata; **MyAnimeList** ranking for anime; anime titles that only
  exist in ani.pm are searchable too
- **Embedded playback** inside the app — Qt WebEngine (Chromium) on Windows,
  WebKit2GTK on Linux — with fullscreen, picture-in-picture and subtitle/audio
  track selection from the provider page. The Windows build bundles WebEngine, so
  Watch Now opens the player over the app instead of a browser tab.

### Settings
- **General** — download folder, desktop notifications, rescan on startup,
  retry count, network timeout, aria2c path, light/dark/system theme
- **Locations** — where the settings, data and downloads live
- **Providers** — RAWG, TMDB and MyAnimeList keys with save-and-test
- **Media** — streaming provider, anime provider, default audio, autoplay
- **Shortcuts** — the full keyboard reference

---

## Install

### Windows (portable, no Python needed)

1. Download **`Lumora-Windows-x64.zip`** from the
   [Releases](https://github.com/kant-sompong/Lumora/releases) page.
2. Extract it anywhere — the ZIP contains a single `Lumora` folder.
3. Run `Lumora.exe` inside that folder.

**Requirements:** Windows 10 (1809 or later) or Windows 11, 64-bit. Nothing
else has to be installed; Qt, Python and Pillow are bundled.

The first run asks nothing: the download folder defaults to
`%USERPROFILE%\Downloads\Lumora` (change it in Settings → General), and Movies
& TV waits for a free TMDB key.

> The executable is unsigned, so SmartScreen may show a warning — choose
> *More info → Run anyway*. An antivirus may also flag the PyInstaller
> bootloader; that is a known false positive.

### Linux (from source)

**Requirements:** Python 3.10+, GTK 3 with PyGObject, gdk-pixbuf, `xdg-utils`.
WebKit2GTK and GStreamer are needed for embedded Movies & TV playback.

| Distribution | Install |
| --- | --- |
| Void Linux | `sudo xbps-install -Su python3 gtk+3 python3-gobject python3-cairo gdk-pixbuf xdg-utils` |
| Debian / Ubuntu | `sudo apt install python3 python3-gi python3-gi-cairo gir1.2-gtk-3.0 xdg-utils` |
| Fedora | `sudo dnf install python3 python3-gobject gtk3 xdg-utils` |
| Arch | `sudo pacman -S python python-gobject gtk3 xdg-utils` |

Optional: `webkit2gtk` (or `webkit2gtk-4.1`) plus `gstreamer` plugins for
embedded playback.

```sh
git clone https://github.com/kant-sompong/Lumora.git
cd Lumora
./run.sh              # or: python3 -m lgl
```

A Tk fallback (`python3 -m lgl.windows_app`) still starts on machines without
GTK, but it is the legacy interface and does not have the full feature set.

### Optional: provider API keys

| Provider | Used for | Where to get it |
| --- | --- | --- |
| TMDB | Movies & TV titles, posters, backdrops | <https://www.themoviedb.org/settings/api> |
| RAWG | Extra game metadata and covers | <https://rawg.io/apidocs> |
| MyAnimeList | Anime titles and rankings (Client ID only) | <https://myanimelist.net/apiconfig> |

Add them in **Settings → Providers**. Keys live in the OS keyring
(libsecret on Linux, Credential Manager on Windows) and fall back to an
owner-only `config.json`; they are never written to the catalogue, logs or error
messages. A key can also be supplied through `.env` (`TMDB_API_KEY=…`, see
`.env.example`).

### Where data is stored

| Data | Linux | Windows |
| --- | --- | --- |
| Settings | `$XDG_CONFIG_HOME/linux-game-library/config.json` | `%APPDATA%\NativeGameLibrary\config.json` |
| Library index | `$XDG_DATA_HOME/linux-game-library/library.json` | `%LOCALAPPDATA%\NativeGameLibrary\library.json` |
| Download queue | `$XDG_DATA_HOME/linux-game-library/downloads.json` | `%LOCALAPPDATA%\NativeGameLibrary\downloads.json` |
| Cache | `$XDG_CACHE_HOME/linux-game-library/` | `%LOCALAPPDATA%\NativeGameLibrary\Cache\` |
| Downloads | Your chosen folder | Your chosen folder |

The `NativeGameLibrary` folder names are kept on Windows on purpose: the app was
renamed to Lumora in 0.2.6 and existing settings, libraries and queues carry
over without any migration step.

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

## Build from source

### Windows portable `Lumora-Windows-x64.zip`

Requires Windows 10/11 and Python 3.10+ (CI uses 3.12).

```powershell
python -m pip install --upgrade pip
pip install PySide6 PySide6-WebEngine pillow pyinstaller

python -m PyInstaller --noconfirm --clean packaging\windows\Lumora.spec
Compress-Archive -Path dist\Lumora -DestinationPath dist\Lumora-Windows-x64.zip
```

The portable application is `dist\Lumora\Lumora.exe`; the ZIP keeps that folder
as its single top-level entry.

### Linux portable tarball

```sh
sh packaging/linux/build-tarball.sh
# → dist/Lumora-<version>-linux-<arch>.tar.gz
```

The tarball ships the application, `run.sh`, icons, the desktop-entry installer
and `.env.example`; GTK, PyGObject, WebKit2GTK and GStreamer come from your
distribution.

### CI

`.github/workflows/release.yml` runs the test suite, builds the Windows ZIP and
the Linux tarball on every push and pull request. Pushing a `v*` tag (for
example `v0.3.6`) additionally publishes a GitHub release with `Lumora-Windows-x64.zip` and
the Linux tarball attached. The tag must match `lgl.__version__`.

---

## Tests

```sh
python -m pytest tests -q
```

The suite covers the Qt frontend headlessly (`QT_QPA_PLATFORM=offscreen`):
window construction, navigation, Browse filtering/sorting/rendering, the library
grid and detail panel, the download queue rows and dialogs. The archive,
library, download and metadata modules are exercised through those UI tests, so
a broken core shows up without a display.

---

## Architecture

```
lgl/
├── __main__.py          # Entry point: Qt on Windows, GTK on Linux, Tk fallback
├── ui.py                # GTK 3 frontend (Linux)
├── theme_gtk.py         # GTK CSS variables from theme.py
├── qt_main.py           # Qt 6 window: rail, header, pages, menu
├── qt_theme.py          # QSS generated from the same design tokens
├── qt_widgets.py        # Shared Qt widgets (cover art, pills, toasts, popovers)
├── qt_browse.py         # Browse: filters, grid/list, detail panel
├── qt_archive.py        # Archive filtering, sorting and formatting helpers
├── qt_library.py        # Library grid, filters and metadata editor
├── qt_downloads.py      # Download queue and torrent strip
├── qt_media.py          # Movies & TV: hero, carousels, search, player
├── qt_icons.py          # Vector icons drawn with QPainter
├── qt_settings.py       # Settings and About dialogs
├── theme.py             # Shared design tokens (colours, spacing, radii, type)
├── archive.py           # archive.org client, metadata and image cache
├── downloader.py        # Persistent queue, Range resume, segmented transfers
├── library.py           # Folder layout, manifests, local scan
├── library_store.py     # Library index, favourites, duplicates, export/import
├── metadata.py          # Normalised metadata with provenance
├── integrity.py         # Checksum verification
├── compatibility.py     # Platform / format heuristics
├── launch.py            # Reviewed launch (no shell, no root)
├── torrent.py           # Magnet queue + built-in BitTorrent engine
├── bittorrent.py        # Native engine implementation
├── tmdb.py / mal.py     # Metadata clients
├── streaming.py         # Provider URL builders
├── media_history.py     # Watch history and continue watching
├── settings.py          # Preferences and keyring-backed API keys
├── platform_support.py  # Paths, folders, URLs, free space, detached Popen
└── movies_ui.py         # GTK Movies & TV widgets
```

Frontend and core are kept apart on purpose: `qt_*.py` never imports GTK and
`ui.py` never imports Qt, so the two builds share every data path but none of
the widgets. Colours, radii and type sizes come from `theme.py` in both, which is
why the Windows build matches the Linux one exactly.

---

## Safety & limits

- **Nothing runs automatically** — every launch passes through a review dialog
- **No elevated launches** — root/administrator starts are refused
- **Path confinement** — launches stay inside the chosen download folder
- **HTTPS and host allow-lists** for provider URLs
- **No secrets in logs** — API keys are redacted everywhere
- **One connection at a time** to archive.org by default, to stay polite

---

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `ImportError: cannot import name … from gi.repository` | Install GTK 3 + PyGObject, or use the Tk fallback |
| Blank window on Linux | Check `DISPLAY` / `WAYLAND_DISPLAY`; try `GDK_BACKEND=wayland ./run.sh` |
| "Could not reach archive.org" | Test `curl -I https://archive.org/metadata/native-linux-games-collection` — a stale cache is used when available |
| HTTP 429 / slow metadata | Wait and press **Refresh**; concurrency is limited to one download |
| Media tab shows a setup card | Add a free TMDB key in Settings → Providers |
| "Playback opens in your browser" card | You are running from source without Qt WebEngine — install `PySide6` (not `-Essentials`), or use the released `Lumora-Windows-x64.zip` |
| SmartScreen warning | Unsigned PyInstaller build — *More info → Run anyway* |

---

## Known gaps

- No macOS frontend
- No built-in updater — download the new `Lumora-Windows-x64.zip`
- No Steam / itch.io / GOG import (archive.org only)
- No automatic game launch after a download finishes

---

## License

MIT. Third-party components: aria2 (GPLv2, only when bundled as `aria2c.exe`),
PyInstaller bootloader (GPLv2 with a runtime exception), Qt 6 / PySide6 (LGPLv3).
