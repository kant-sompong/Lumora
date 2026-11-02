# Windows (Qt) parity checklist

Goal: the Windows Qt app should match the Linux GTK app 1:1. GTK (`lgl/ui.py`,
`lgl/movies_ui.py`) is the reference; Qt lives in `lgl/qt_*.py`.

**Last verified: 2026-11-02, version 0.4.0**, by reading both frontends and
running the Qt suite (`python -m pytest tests -q` → 35 passed). GTK was not run
on a display, so Linux behaviour is read from the code.

The first draft of this file described the pre-0.3 Qt app (top tabs named
"Archive" and "Library", a Downloads drawer, a 1,063-test claim). Almost all of
that has since shipped; the list below is the current state. Tick an item when it
lands and bump the date above.

## 1. Done — Qt now matches GTK

### Shell and navigation
- [x] **Layout**: left icon rail (Browse, Library, Downloads, Media) with
      Settings and About pinned at the bottom, plus the back button — matches the
      GTK rail
- [x] **Naming**: "Archive" is gone; the page is "Browse" everywhere
- [x] **Downloads is a destination page**, not a drawer CTA, with the torrent
      strip at the bottom
- [x] **Keyboard shortcuts**: `Ctrl+1`…`Ctrl+4`, `Ctrl+F`/`Ctrl+K`, `Ctrl+R`/`F5`,
      `Ctrl+,` (`qt_main.py`, `_install_shortcuts`)
- [x] **Live theme switching**: system/light/dark restyles the running window
      (`Window.apply_theme`), no restart
- [x] **Header ↻ and ☰** are wired to refresh and the app menu
- [x] **Media search tab removed** (0.3.6) — typing in the search field still
      opens results, so the tab only duplicated it

### Browse
- [x] **Batched rendering**: 400 cards at a time with a
      "Showing N of M matches — scroll for more" footer instead of building a
      widget for all ~9,800 games (`qt_browse.py`, `_maybe_more`)
- [x] **Filters**: genre with full counts, size, released, licence, file format,
      availability, and **Reset all filters**
- [x] **Sort**: title, year, size, downloads, creator, genre, either direction
- [x] **Grid and compact list**, both lazily loading artwork

### Library
- [x] **Every control is live**: filter sheet, grid/list toggle, sort menu with a
      direction button, duplicates, export, import, refresh
- [x] **Every detail button is live**: Open folder, Show files, Verify integrity,
      Favorite, Edit details, Review & launch, Remove from library
- [x] **Sort list matches `LIBRARY_SORTS`**: Recently seen/added/played, Release
      year, Installed size, Developer, Genre, Title
- [x] **Filter sheet**: Status, Platform, Genre, plus Show non-game folders,
      Show missing folders, Group copies, Hide adult titles, Clear filters
- [x] **Edit details** with the metadata fields and their provenance
- [x] **Review & launch** dialog with the "Start with" file picker, path
      confinement and a refused elevated launch
- [x] **Remove from library** (0.3.6): confirmed, index-only, files untouched
- [x] **Duplicate files** dialog
- [x] **Export / import library, forget missing entries**

### Downloads
- [x] **Persistent queue** rows with pause, resume, cancel, retry, remove
- [x] **Torrent / magnet strip** driven by the built-in engine

### Media (Movies & TV)
- [x] **All sections**: Home (hero, Continue Watching, Trending), Movies, TV
      Shows, Anime, My List, History, Search
- [x] **TMDB, MyAnimeList and ani.pm backends**
- [x] **Provider picker** in the Media bar, saved per library and switched
      automatically for anime
- [x] **Embedded playback** through Qt WebEngine, with fullscreen; CI verifies
      that `QtWebEngineProcess.exe` and the `.pak` files are actually bundled
- [x] **Episode selector** (0.3.6): season dropdown plus episode rows with
      number, title and air date

### Menus, settings and docs
- [x] **Settings dialog, 5 tabs**: General, Locations, Providers (RAWG / TMDB /
      MAL keys), Media, Shortcuts
- [x] **Website Sources and Kryoto Catalog dialogs**
- [x] **Maintenance submenu**: rescan library, reload collection, clear image
      cache, open data folder — plus the download queue and About items
- [x] **README rewritten** for 0.3.x with screenshots; it no longer mentions
      `build.ps1`, `installer.iss` or tab layouts that do not exist

## 2. Shipped in 0.4.0 — the last gaps

- [x] **Catalogue and Kryoto downloads** — ☰ → Maintenance → “Download from
      catalogue…” queues a record's magnets in the torrent queue and its http(s)
      links in the download queue, showing the importer's own link status
      (`qt_catalog.CatalogDownloadDialog`)
- [x] **JDownloader and AB Download Manager hand-off** — the Browse detail panel
      sends an item's links to either local API (`external_dm`) and reports a
      refusal in the toast
- [x] **Desktop notifications on finish or failure** — the
      `desktop_notifications` setting is now wired: `notify-send` on Linux, a
      tray balloon on Windows, once per task, so a restored queue does not
      re-announce old results
- [x] **Resume-unfinished prompt** — a banner counts downloads left from the last
      session and offers Resume all / Not now
- [x] **Artwork match dialog and RAWG sync** — Maintenance → “Sync catalogue
      artwork” fills every record whose poster could never load; “Re-check games
      with no artwork match” ignores remembered misses; each record also has
      “Match artwork…” (`qt_artwork`)
- [x] **Magnet link field** — Library → Add magnet… validates the URI and queues
      it in the torrent strip
- [x] **Browse details**: description, screenshots with a lightbox, the file list
      with “Download selected…”, open source page, mirror check (a HEAD request
      per link) and copy identifier
- [x] **AniList lookup** — Media → “Lookup…” resolves a title through AniList for
      the two AniList-id providers

### Still open

- [ ] The JDownloader/AB hand-off is offered from Browse only; the Library
      detail panel could offer it too
- [ ] RAWG descriptions, genres and screenshots need a RAWG key. Without one the
      sync adds posters from the keyless store search and nothing else

## 3. Linux side

- [ ] **No bugs found from reading the code**, but GTK has never been reviewed
      against a running display in this checklist — that review is still owed
- [ ] `ui.py` (~11,900 lines) and `movies_ui.py` (~4,000 lines) are hard to
      maintain; splitting them is worthwhile but unrelated to Windows parity
- [ ] Data folders still use the old names (`linux-game-library` on Linux,
      `NativeGameLibrary` on Windows). Kept on purpose in 0.2.6; migrating to
      "Lumora" needs a copy-on-first-launch step

## 4. Tests

- [x] **Qt suite committed**: `tests/test_qt_smoke.py`, `test_qt_archive.py`,
      `test_qt_downloads.py` — 35 tests, headless, building the real window,
      Browse filtering, library grid and detail, downloads against a local HTTP
      server, media cards and the catalogue dialog
- [ ] **Core suite is still not in `tests/`** — `downloader`, `archive`,
      `library`, `library_store`, `integrity`, `sources` and `importer` have no
      committed tests. The README no longer claims a test count because of this

## 5. Next up

1. Catalog/Kryoto rows into the download queue, then notifications on finish
2. Browse details page with screenshots, lightbox and per-file selection
3. Artwork match / RAWG sync
4. Commit the core test suite
5. Linux review against a running GTK session

## How to re-check

```sh
python -m pytest tests -q                 # Qt suite, needs QT_QPA_PLATFORM=offscreen on CI
python -m compileall lgl                  # import-level sanity across both frontends
```

Screenshots of the current Qt UI live in [`docs/screens`](screens) and are used
by the README.
