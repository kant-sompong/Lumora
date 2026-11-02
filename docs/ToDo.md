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

## 3. Folders and naming

- [x] **Data folders and app id renamed to Lumora.** 0.6.0 moves settings to
  `%APPDATA%\Lumora` / `$XDG_CONFIG_HOME/lumora` and the library, queues and
  catalogue to the matching data folders. On the first launch after the
  upgrade `lgl/migration.py` **copies** the old documents across: nothing is
  moved or deleted, so an older build still works, and rolling back is deleting
  the new folders (the old ones were never touched). Existing new data always
  wins, so a second launch never overwrites anything. A `migration.json`
  marker records what was copied and how to roll back. The image cache is not
  copied — it is regenerable artwork
- [x] **API keys carried over.** `Settings` now stores keys under the
  `Lumora` keyring service and reads the pre-0.6.0 services
  (`linux-game-library`, `NativeGameLibrary`) once, copying the key forward
  while leaving the old entry in place for a rollback
- [x] **Old names retired.** User agents, the GTK program class and prgname
  (`lumora`), the Windows AppUserModelID, the desktop entry
  (`io.github.lumora.Lumora.desktop`) and the icon files all say Lumora now.
  The Inno Setup `AppId` GUID deliberately stays: it is an opaque install
  identity, so the installer keeps upgrading the same installation instead of
  leaving a second one behind

## 4. Linux (GTK)

- [ ] **Review the GTK app on a running display**, screen by screen, against the
  Qt app. Needs a display and PyGObject, neither of which the review host has;
  no bugs were found by reading the code
- [ ] **Split `ui.py` (~11,900 lines) and `movies_ui.py` (~4,000 lines)** into
  smaller modules so they can be tested. Deliberately not started: a move that
  large cannot be checked without a display, and a half-moved GTK app is worse
  than a large one file. This stays last, after the desktop review

## 5. How to run the checks

```sh
python -m compileall lgl
QT_QPA_PLATFORM=offscreen python -m pytest tests -q        # 164 tests
LD_LIBRARY_PATH=/tmp/libs/root/usr/lib/x86_64-linux-gnu \
  QT_QPA_PLATFORM=offscreen python ../../qt_shots.py shots/1x 1
```

The second command needs PySide6 with its system libraries; the shots script
lives beside the repo and writes PNGs into `shots/`. This VM has no fontconfig,
so it loads DejaVu from `fonts/` first — a real desktop needs none of that.
