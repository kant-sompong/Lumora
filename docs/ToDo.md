# Lumora ToDo

Everything still to do, in the order I would do it. The Linux GTK app
(`lgl/ui.py`, `lgl/movies_ui.py`) is the reference; the Windows Qt app lives in
`lgl/qt_*.py`. Tick an item when it lands and bump the date.

**Last checked: 2026-11-02, version 0.6.0.** Checked by running the suite,
rendering the Qt windows offscreen at 1×, 2× and the minimum window size, and
running the release pipeline. The desktop checks below need a machine with a
display and Windows, which this review did not have.

## 1. Tests and safety net

- [x] **Commit the core test suite.** `tests/test_downloader.py` (queue, pause,
  cancel, retry, duplicate and path rules against a local server),
  `tests/test_archive.py`, `tests/test_library.py`, `tests/test_integrity.py`,
  `tests/test_sources.py`, `tests/test_importer.py` (a full run over a fake
  source), `tests/test_updates.py` and `tests/test_migration.py` (the folder
  rename, copy-on-first-launch, rollback and keyring carry-over). The Qt tests
  stay alongside them
- [x] **Review the Qt windows as real pixels.** `qt_shots.py` renders the main
  window (all four pages), the library detail panel, the browse detail panel,
  every Settings tab and About in dark and light at 1×, 2× and the 980×640
  minimum size. Layout holds: the rail labels fit, the search pill and header
  controls stay inside the window, the library grid reflows to four columns at
  the minimum size and the detail panel scrolls. What this cannot show is real
  rendering on a desktop GPU, HiDPI on Windows, pointer behaviour or the
  embedded player — those still need a real desktop
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

## 3. Naming and data

- [x] **Migrate data folders to "Lumora".** 0.6.0 moves settings to
  `%APPDATA%\Lumora` / `$XDG_CONFIG_HOME/lumora` and the library, queues and
  catalogue to the matching data folders, replacing `linux-game-library`,
  `NativeGameLibrary` and the app ID `io.github.nativegamelibrary.NativeGameLibrary`.
  `lgl/migration.py` **copies** the old documents across on the first launch:
  nothing is moved or deleted, so an older build still works and rolling back is
  deleting the new folders (the old ones were never touched). Existing new data
  always wins, so a second launch never overwrites anything, and a
  `migration.json` marker records what was copied and how to roll back. The
  image cache is not copied — it is regenerable artwork
- [x] **Rename the remaining old strings.** User agents, the GTK program name and
  window class (`lumora` / `Lumora`), the Windows AppUserModelID, the `.desktop`
  entry (`io.github.lumora.Lumora.desktop`, `StartupWMClass=lumora`) and the icon
  file names all say Lumora now. `install-desktop.sh` also removes the stale old
  launcher entry and icons. The Inno Setup `AppId` GUID deliberately stays: it is
  an opaque install identity, so the installer keeps upgrading the same
  installation instead of leaving a second one behind
- [x] **API keys carried over.** `Settings` stores keys under the `Lumora`
  keyring service and reads the pre-0.6.0 services (`linux-game-library`,
  `NativeGameLibrary`) once, copying the key forward while leaving the old entry
  in place for a rollback

## 4. Linux (GTK)

- [ ] **Review the GTK app on a running display**, screen by screen, against the
  Qt app. Needs a display and PyGObject, neither of which the review host has;
  no bugs were found by reading the code
- [ ] **Split `ui.py` (~11,900 lines) and `movies_ui.py` (~4,000 lines)** into
  smaller modules so they can be tested. Deliberately not started: a move that
  large cannot be checked without a display, and a half-moved GTK app is worse
  than a large one file. This stays last, after the desktop review
- [ ] **Linux packaging beyond the source tarball** (AppImage or Flatpak), if
  wanted. The tarball needs GTK 3, PyGObject, WebKit2GTK and GStreamer from the
  distribution

## 5. Problems found in the 2026-11-02 recheck

- [x] **This file lost sections.** After a rewrite it stopped partway through
  section 3 and dropped the naming/data items, the Linux packaging item, the
  housekeeping items, "Done" and "How to re-check". Restored here (sections 3–7);
  the 0.6.0 rewrite merged with them instead of replacing them
- [ ] **Nothing has been seen on real hardware.** CI proves the app builds and
  starts; it does not show how it looks or behaves on a Windows desktop, and the
  GTK app has not been reviewed on a running display. Covered by the unticked
  items in sections 1 and 4, which are the highest-value work left
- [x] **Qt tests can run on the review host now.** This entry previously said
  only the 101 core tests could be re-run locally. PySide6 with its Qt libraries
  is installed under `.venv` and `/tmp/libs`, so the whole suite — Qt tests
  included — runs here: 164 tests pass. What still cannot run here is a real
  desktop, which is the unticked item in section 1

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

Added by 0.6.0:

- Folder and app-ID rename with copy-on-first-launch migration, rollback marker
  and keyring carry-over (`lgl/migration.py`, `tests/test_migration.py`)
- Old user agents, program class, `.desktop` entry and icon names retired
- Offscreen pixel review of every Qt window at 1×, 2× and the minimum size

## How to re-check

```sh
python -m compileall lgl
python -m pytest tests -q          # set QT_QPA_PLATFORM=offscreen when headless
```

The second command needs PySide6 with its Qt system libraries; on this review
host they live in `/tmp/libs/root/usr/lib/x86_64-linux-gnu`. To re-render the
Qt windows as pixels, run the `qt_shots.py` harness that sits beside the repo
(it loads DejaVu from `fonts/` first, because this host has no fontconfig) and
read the PNGs it writes.

Screenshots of the current Qt UI are in [`docs/screens`](screens).
