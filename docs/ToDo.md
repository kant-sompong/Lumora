# Lumora ToDo

Everything still to do, in the order I would do it. The Linux GTK app
(`lgl/ui.py`, `lgl/movies_ui.py`) is the reference; the Windows Qt app lives in
`lgl/qt_*.py`. Tick an item when it lands and bump the date.

**Last checked: 2026-11-02, version 0.4.0** (release published, CI green:
Qt tests on Windows, PyInstaller build, Linux tarball). Checked by reading the
code and CI. The Qt tests were not run locally, and GTK was not run on a display.

## 1. Tests and safety net

- [ ] **Commit the core test suite.** `downloader`, `archive`, `library`,
      `library_store`, `integrity`, `sources` and `importer` have no tests in
      `tests/`; only the Qt tests are committed. The README no longer quotes a
      test count because of this
- [ ] **Run the Qt suite on a real desktop**, not just offscreen in CI: window
      sizes, DPI scaling, dark/light, the embedded player
- [ ] **Smoke-test the Windows zip by hand** (extract, launch, browse, download a
      small file, play a title). CI only checks that the exe starts and stays up

## 2. Windows (Qt) gaps

- [ ] **Library detail: JDownloader / AB Download Manager hand-off.** It exists in
      Browse only
- [ ] **RAWG without a key.** Sync adds posters from the keyless store search and
      nothing else; descriptions, genres and screenshots need a free RAWG key.
      Decide whether to bundle a flow for getting one (Settings → Providers)
- [ ] **Windows installer.** Only the portable zip is published. Decide if a
      per-user installer (Inno Setup) is wanted
- [ ] **Auto-update or update check.** The README says there is none; users
      re-download from Releases

## 3. Linux (GTK)

- [ ] **Review the GTK app on a running display**, screen by screen, against the
      Qt app. No bugs were found from reading the code, but it has not been run
      in this review
- [ ] **Split `ui.py` (~11,900 lines) and `movies_ui.py` (~4,000 lines)** into
      smaller modules so they can be tested. Unrelated to Windows parity
- [ ] **Package for Linux beyond the source tarball** (AppImage / Flatpak) if
      wanted; the tarball needs GTK 3, PyGObject, WebKit2GTK and GStreamer from
      the distribution

## 4. Naming and data

- [ ] **Migrate data folders to "Lumora".** They still use the old names:
      `linux-game-library` on Linux, `NativeGameLibrary` on Windows (and the app
      ID `io.github.nativegamelibrary.NativeGameLibrary`). Kept on purpose in
      0.2.6 so existing libraries keep working; moving needs a
      copy-on-first-launch step and a rollback path
- [ ] **Rename remaining old strings** that the migration will touch: user agents
      (`native-game-library`, `NativeGameLibrary/1.0`), the Linux program name and
      window class, the `.desktop` and icon file names

## 5. Housekeeping

- [ ] **Keep this file honest.** When something ships, move it out of the open
      list and update the date at the top
- [ ] **Tidy `tests/` count claim.** The 0.4.0 notes said 35 passed while the
      files define 38 tests; check the CI log and quote the real number

## Done (so nobody redoes it)

Shipped by 0.4.0, all verified present in the code:

- Qt shell: left rail (Browse, Library, Downloads, Media), Settings and About
  pinned at the bottom, back button, "Browse" naming, Downloads as a page with
  the torrent strip, live theme switching, `Ctrl+1`…`Ctrl+4`, `Ctrl+F`/`Ctrl+K`,
  `Ctrl+R`/`F5`, `Ctrl+D`, `Ctrl+,`, Esc
- Browse: batched rendering (400 at a time), full filter set with Reset, sort,
  grid and list, game details with screenshots and a viewer, "Download selected…",
  mirror check, JDownloader / AB hand-off
- Library: every control and detail button live, filter sheet, sort list matching
  GTK, Edit details, Review & launch, duplicates, export / import, forget missing,
  remove from library, add magnet
- Downloads: persistent queue, torrent / magnet strip, catalogue and Kryoto
  downloads, desktop notifications, resume-unfinished prompt
- Media: Home, Movies, TV Shows, Anime, My List, History, Search, TMDB / MAL /
  ani.pm, provider picker, episode selector, embedded playback with Qt WebEngine
- Menus and settings: 5-tab Settings dialog, Website Sources, Kryoto Catalog,
  Maintenance submenu, artwork match and RAWG sync
- Packaging: CI builds and publishes `Lumora-Windows-x64.zip` and the Linux
  tarball on every `v*` tag; README rewritten with screenshots

## How to re-check

```sh
python -m pytest tests -q          # Qt suite (set QT_QPA_PLATFORM=offscreen headless)
python -m compileall lgl           # import-level sanity across both frontends
```

Screenshots of the current Qt UI are in [`docs/screens`](screens).
