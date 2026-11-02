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
