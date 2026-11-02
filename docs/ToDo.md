# Lumora ToDo

Everything still to do, in the order I would do it. The Linux GTK app
(`lgl/ui.py`, `lgl/movies_ui.py`) is the reference; the Windows Qt app lives in
`lgl/qt_*.py`. Tick an item when it lands and bump the date.

**Last checked: 2026-11-02, version 0.5.0.** Checked by running the suite and
the release pipeline. The desktop checks below need a machine with a display and
Windows, which this review did not have.

## 1. Tests and safety net

- [x] **Commit the core test suite.** `tests/test_downloader.py` (queue, pause,
  cancel, retry, duplicate and path rules against a local server),
  `tests/test_archive.py`, `tests/test_library.py`, `tests/test_integrity.py`,
  `tests/test_sources.py`, `tests/test_importer.py` (a full run over a fake
  source) and `tests/test_updates.py`. The Qt tests stay alongside them
- [ ] **Run the Qt suite on a real desktop**, not just offscreen in CI: window
  sizes, DPI scaling, dark/light, the embedded player. Needs a display; the
  offscreen suite covers construction and rules, not appearance
- [ ] **Smoke-test the Windows zip by hand** (extract, launch, browse, download a
  small file, play a title). CI checks that the exe starts and stays up, which is
  not the same thing

## 2. Windows (Qt) gaps

- [x] **Library detail: JDownloader / AB Download Manager hand-off.** Both
  buttons send the game's own magnet and source page through the documented
  local APIs in `external_dm`, and report a refusal
- [x] **RAWG key flow.** Settings → Providers has "Get a RAWG key" (opens
  rawg.io/apidocs) and the save now tests the key with a live `RawgSource` call
  instead of only storing it. Without a key the artwork sync still adds posters
  from the keyless store search and nothing else
- [x] **Windows installer.** `packaging/windows/Lumora.iss` wraps the PyInstaller
  build in a per-user Inno Setup installer; CI builds and publishes
  `Lumora-Setup-x64.exe` next to the portable zip
- [x] **Update check.** ☰ → "Check for updates…" reads the latest GitHub release
  and offers the release page when it is newer. It never installs anything

## 3. Linux (GTK)

- [ ] **Review the GTK app on a running display**, screen by screen, against the
  Qt app. Needs a display and PyGObject, neither of which the review host has;
  no bugs were found by reading the code
- [ ] **Split `ui.py` (~11,900 lines) and `movies_ui.py` (~4,000 lines)** into
  smaller modules so they can be tested. Deliberately not started: a move that
  large cannot be checked without a display, and a half-moved GTK app is worse
  than a large one file
- [ ] **Linux packaging beyond the source tarball** (AppImage or Flatpak), if
  wanted. The tarball needs GTK 3, PyGObject, WebKit2GTK and GStreamer from the
  distribution

## 4. Naming and data

- [ ] **Migrate data folders to "Lumora".** They still use the old names:
  `linux-game-library` on Linux, `NativeGameLibrary` on Windows, and the app ID
  `io.github.nativegamelibrary.NativeGameLibrary`. Kept on purpose in 0.2.6 so
  existing libraries keep working; moving needs a copy-on-first-launch step and a
  rollback path
- [ ] **Rename the remaining old strings** the migration will touch: user agents
  (`native-game-library`, `NativeGameLibrary/1.0`), the Linux program name and
  window class, the `.desktop` and icon file names (about 40 references in
  `lgl/`, `packaging/` and `.github/`)

## 5. Problems found in the 2026-11-02 recheck

- [x] **This file lost sections.** After a rewrite it stopped partway through
  section 3 and dropped the naming/data items, the Linux packaging item, the
  housekeeping items, "Done" and "How to re-check". Restored here (sections 3–7)
- [ ] **Nothing has been seen on real hardware.** CI proves the app builds and
  starts; it does not show how it looks or behaves on a Windows desktop, and the
  GTK app has not been reviewed on a running display. Covered by the unticked
  items in sections 1 and 3, which are the highest-value work left
- [ ] **Qt tests cannot run on the review host** (PySide6 cannot be installed
  there), so only the 101 core tests were re-run locally; the other tests in the
  149 that CI passes were checked through the CI log only

## 6. Housekeeping

- [ ] **Keep this file honest.** When something ships, tick it and update the
  date at the top. Check for lost sections before saving over another edit

## 7. Done (so nobody redoes it)

Verified present in the code and in the v0.5.0 release:

- Qt shell: left rail (Browse, Library, Downloads, Media), Settings and About at
  the bottom, back button, live theme switching, keyboard shortcuts
- Browse: batched rendering, full filter set, sort, grid and list, game details
  with screenshots, per-file download, mirror check
- Library: every control and detail button, filter sheet, sorts matching GTK,
  Edit details, Review & launch, duplicates, export / import, add magnet,
  JDownloader / AB hand-off
- Downloads: persistent queue, torrent strip, catalogue and Kryoto downloads,
  desktop notifications, resume prompt
- Media: Home, Movies, TV Shows, Anime, My List, History, Search, TMDB / MAL /
  ani.pm, episode selector, embedded playback (Qt WebEngine)
- Settings (5 tabs), RAWG key flow with live test, update check, artwork sync
- Tests and packaging: core test suite, Qt tests (149 tests pass in CI), portable
  zip, Inno Setup installer and Linux tarball published on every `v*` tag

## How to re-check

```sh
python -m pytest tests -q          # set QT_QPA_PLATFORM=offscreen when headless
python -m compileall lgl           # import-level sanity across both frontends
```

Screenshots of the current Qt UI are in [`docs/screens`](screens).
