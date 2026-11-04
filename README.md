<h1 align="center">Lumora</h1>

<p align="center">
  <strong>A desktop game library for the Internet Archive's Linux game collection — browse, download, organise and play — with a Movies&nbsp;&amp;&nbsp;TV section for everything else.</strong><br>
  One shared Python core, two native frontends: GTK&nbsp;3 on Linux, Qt&nbsp;6 on Windows.
</p>

<p align="center">
  <a href="https://github.com/kant-sompong/Lumora/releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/kant-sompong/Lumora?style=flat-square&label=release"></a>
  <a href="https://github.com/kant-sompong/Lumora/actions/workflows/release.yml"><img alt="Build status" src="https://img.shields.io/github/actions/workflow/status/kant-sompong/Lumora/release.yml?branch=main&style=flat-square&label=CI"></a>
  <img alt="Platforms" src="https://img.shields.io/badge/platforms-Windows%20%7C%20Linux-2f6feb?style=flat-square">
  <a href="#license"><img alt="License" src="https://img.shields.io/badge/license-MIT-3fb950?style=flat-square"></a>
</p>

<img src="docs/screens/qt-browse-catalogue.png" width="100%" alt="Lumora's Browse page: a cover grid of Internet Archive games with filters for genre, size, year and format, and a details panel for the selected game">

<p align="center"><sub>Browse the collection, filter it, and download in one click — the same window on Linux and Windows.</sub></p>

| Library | Downloads | Movies &amp; TV |
| :---: | :---: | :---: |
| ![Library with a selected game](docs/screens/qt-library-selection.png) | ![Download queue](docs/screens/qt-downloads.png) | ![Media home](docs/screens/media-home.png) |

## Features

- **Browse and filter** — the whole archive.org collection with search, genre/size/year/licence/format filters, lazy artwork, a cover grid or compact list, and a details panel with the file list
- **Downloads that finish** — a queue that survives restarts, segmented transfers with resume, disk-space preflight, atomic renames and bounded retries; magnets through the built-in BitTorrent engine or `aria2c`
- **A library you own** — folder scan into `library.json`, favourites, metadata with provenance, checksum verification, duplicate finder, export/import
- **Reviewed launch** — you pick the executable, the path must stay inside your download folder, and elevated launches are refused
- **Movies &amp; TV** — TMDB metadata, anime via MyAnimeList/ani.pm, episode selector, provider picker and embedded playback
- **Website catalogues** — import Kryoto, RAWG, archive.org or any JSON/HTML catalogue page into the same library

## Install

### Windows

Download **`Lumora-Setup-x64.exe`** (per-user installer, no administrator prompt) or the portable **`Lumora-Windows-x64.zip`** from the [latest release](https://github.com/kant-sompong/Lumora/releases/latest). Nothing else to install — the build is self-contained and bundles Qt WebEngine for playback.

The executable is unsigned, so SmartScreen may warn: choose **More info → Run anyway**. Some antivirus heuristics also flag PyInstaller binaries; that is a known false positive.

### Linux

```sh
git clone https://github.com/kant-sompong/Lumora.git
cd Lumora
./run.sh              # or: python3 -m lgl
```

Needs Python 3.10+ and GTK 3; WebKit2GTK and the GStreamer plugins are optional and only used for embedded playback.

<details>
<summary><strong>Distribution packages</strong></summary>

| Distribution | Command |
| --- | --- |
| Void Linux | `sudo xbps-install -Su python3 gtk+3 python3-gobject python3-cairo gdk-pixbuf xdg-utils` |
| Debian / Ubuntu | `sudo apt install python3 python3-gi python3-gi-cairo gir1.2-gtk-3.0 xdg-utils` |
| Fedora | `sudo dnf install python3 python3-gobject gtk3 xdg-utils` |
| Arch | `sudo pacman -S python python-gobject gtk3 xdg-utils` |

</details>

## Docs

- [Features](docs/FEATURES.md) — everything the app does, section by section
- [Screenshots](docs/SCREENSHOTS.md) — every screen, including Settings, the Windows build and the Kryoto catalogue
- [Reference](docs/REFERENCE.md) — requirements, API keys, streaming providers, keyboard shortcuts, where data is stored, troubleshooting
- [Developing](docs/DEVELOPING.md) — architecture, building from source, CI, tests
- [Roadmap](docs/ROADMAP.md) — known gaps

## License

MIT. Third-party components: aria2 (GPLv2, only when bundled as `aria2c.exe`), PyInstaller bootloader (GPLv2 with a runtime exception), Qt 6 / PySide6 (LGPLv3), and Chromium via Qt WebEngine (BSD-style licences).

<p align="center"><sub>Built with Python, GTK and Qt — for people who still own their files.</sub></p>
