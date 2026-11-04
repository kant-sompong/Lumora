# Developing

Architecture, building from source, CI and the test suite. For how to install a
release, see the [README](../README.md).

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

## Building from source

### Windows portable ZIP

Requires Windows 10/11 and Python 3.10+ (CI uses 3.12).

```powershell
python -m pip install --upgrade pip
pip install PySide6 PySide6-WebEngine pillow pyinstaller

python -m PyInstaller --noconfirm --clean packaging\windows\Lumora.spec
Compress-Archive -Path dist\Lumora -DestinationPath dist\Lumora-Windows-x64.zip

# Optional: the installer (requires Inno Setup 6)
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /DMyAppVersion=0.6.6 `
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

[`.github/workflows/release.yml`](../.github/workflows/release.yml) runs the test
suite, builds the Windows ZIP and the Inno Setup installer, builds the Linux
tarball on every push and pull request, and verifies that the WebEngine player
files are actually bundled. Pushing a `v*` tag publishes a GitHub release with
all three assets; the tag must match `lgl.__version__`.

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
check's version comparison.

## Contributing

Issues and pull requests are welcome. Please:

- describe bugs with reproduction steps and the platform you used
- include relevant logs, with API keys redacted
- run `python -m pytest tests -q` before opening a pull request

Open work and the roadmap checklist live in [thing.md](thing.md): tick an
item there when it lands, and add a dated note under it instead of rewriting the
entry it replaces.

Screenshots live in [`docs/screens`](screens) — regenerating them for a UI
change makes review much easier, and updating [SCREENSHOTS.md](SCREENSHOTS.md)
keeps this documentation honest.
