# Windows (Qt) parity checklist

Goal: the Windows Qt app should match the Linux GTK app 1:1. GTK (`lgl/ui.py`,
`lgl/movies_ui.py`) is the reference; Qt lives in `lgl/qt_*.py`.

Written from a code review of both apps on 2026-11-02. GTK could not be run
while reviewing, so Linux behaviour is read from the code only. Tick items off
as they land.

## 1. Fix: already in Qt but broken or wrong

- [ ] **Dead Library controls**: Filters, grid/list toggle, the ⋮ menu items
      (duplicates, export, import), header ↻ and ☰ menu do nothing
- [ ] **Dead Library detail buttons**: Open folder, Show files, Verify integrity,
      Favorite, Review & launch
- [ ] **Archive builds a widget for every game** (about 9,800) and rebuilds all of
      them on each keystroke or filter click; GTK loads 400 at a time with
      "scroll for more"
- [ ] **Library sort list**: use GTK's `LIBRARY_SORTS` (Title, Recently
      seen/added/played, Release year, Installed size, Developer, Genre,
      Platform), add the direction button, remember the choice
- [ ] **No keyboard shortcuts**: Ctrl+1, Ctrl+2, Ctrl+F, Ctrl+D, Ctrl+, F5, Esc
- [ ] **Layout**: GTK has a left icon rail (Browse, Library, Downloads, Media;
      Settings and About at the bottom) and a back button. Qt has top tabs named
      "Archive" and "Library" and a Downloads drawer
- [ ] **Theme only applies at startup**: switch system/light/dark live
- [ ] **README**: it claims Browse/Downloads/Library/Media tabs and Qt WebEngine
      on Windows, and lists `build.ps1` and `installer.iss`, which do not exist
- [ ] **Tests**: the README says 1063 tests, but `tests/` only holds the Qt
      tests. Commit the core test suite (downloader, archive, library, ...)

## 2. Add: in GTK, missing in Qt

### Library
- [ ] Filters popover: Status, Platform, Genre, Favorites, Show non-game folders,
      Show missing folders, Group copies, Hide adult titles, Clear filters
- [ ] Edit details dialog (`MetadataDialog`)
- [ ] Review & launch, with the "Start with" file picker
- [ ] Remove from library, magnet link field
- [ ] Show duplicate files dialog
- [ ] Export / import library, Forget missing entries
- [ ] Artwork match dialog, RAWG artwork sync

### Archive (Browse)
- [ ] Size, File format and Availability filters, and Reset all filters
- [ ] Full genre list with counts (Qt shows the top 12 only)
- [ ] Game details page: description, screenshots with lightbox, file list with
      "Download selected…", open source page, mirror check, copy
- [ ] "Showing N of M, scroll for more" paging

### Downloads
- [ ] Catalog and Kryoto downloads (currently a "not ported yet" toast)
- [ ] Torrent / magnet section using the built-in engine
- [ ] JDownloader and AB Download Manager hand-off
- [ ] Desktop notifications on finish or failure
- [ ] Resume-unfinished prompt and queue banner

### Menus and dialogs
- [ ] Settings dialog, 5 tabs: General, Locations, Providers (RAWG / TMDB / MAL
      keys with Save & test), Media, Shortcuts
- [ ] Website Sources dialog and Kryoto Catalog dialog
- [ ] Maintenance submenu (sync artwork, re-check games with no match)
- [ ] Real About dialog, Show Download Queue item

### Media (Movies & TV)
- [ ] Home (hero, Continue Watching, Trending), Movies, TV Shows, Anime, Search,
      My List, History
- [ ] TMDB, MyAnimeList and ani.pm backends, provider and playback settings
- [ ] Embedded playback (needs QtWebEngine; `Lumora.spec` currently excludes it,
      so the zip will grow a lot)

## 3. Linux side (no bugs found from reading the code)

- [ ] `ui.py` (about 11,900 lines) and `movies_ui.py` (about 4,000 lines) are
      hard to maintain and test; consider splitting
- [ ] Data folders still use the old names (`linux-game-library` on Linux,
      `NativeGameLibrary` on Windows). Kept on purpose in 0.2.6; migrating to
      "Lumora" needs a copy-on-first-launch step

## Suggested order

1. Section 1: dead controls, paging, sorting, shortcuts
2. Layout: rail, Browse naming, Downloads as a page
3. Settings dialog and filters
4. Details and launch
5. Catalog and torrents
6. Media
