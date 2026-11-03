# Lumora — reports and what to do

This file was `docs/ToDo.md`. It has two parts:

1. **Reports** — what was checked and found, newest first, each with its date,
   the version it covers and how it was checked.
2. **What to do** — everything still open, in the order I would do it, plus what
   is already done so nobody redoes it.

The Linux GTK app (`lgl/ui.py`, `lgl/movies_ui.py`) is the reference; the Windows
Qt app lives in `lgl/qt_*.py`. Tick an item when it lands and bump the date. When
a report finds a problem, give it an ID (B1, B2 …) and link the fix item in part 2
to that ID.

---

# 1. Reports

## 1.1 Review of 0.6.1 — 2026-11-03

**Covers:** `main` at commit `5e04f35` (Release 0.6.1), Linux and Windows.

### How it was checked

- `python -m compileall` over `lgl`, `packaging` and `tests`: clean.
- Full test suite with `QT_QPA_PLATFORM=offscreen` (Python 3.13, PySide6 6.12):
  **164 passed.**
- `ruff` with bug-focused rules (undefined names, redefinitions, loop-variable
  closures, bare excepts, mutable defaults): 17 hits, none a real bug (below).
- A headless run of the Windows Qt app as a user would drive it: build the
  window, then open Archive, Library, Downloads and Media, toggle Downloads, open
  all five Settings tabs and About, in dark **and** light, at 900×600, 1000×700,
  1280×800 and 1920×1080. 34 steps, **0 uncaught exceptions.**
- Read in full: `__main__.py`, `packaging/windows/Lumora.spec`,
  `.github/workflows/release.yml`, `lgl/migration.py`, `lgl/updates.py`,
  `lgl/launch.py`, `lgl/platform_support.py`, the launch flow in
  `lgl/qt_library.py` and `lgl/windows_app.py`, the `aria2c` lookup in
  `lgl/torrent.py`.

### Not covered (be careful with "no bug found")

- **The GTK app was not run.** PyGObject does not import on the review host, so
  everything said about `ui.py` comes from reading code and from calling the
  shared modules it uses (`launch.py`, `migration.py`) directly.
- Tkinter (`windows_app.py`) was not run: no `tkinter` on the review host.
- No real Windows machine and no real display: DPI scaling, GPU rendering,
  pointer behaviour and the embedded player are unchecked.
- Torrent engine, catalogue import, Kryoto and streaming providers were not
  exercised beyond what the existing tests cover.

### Bugs found and reproduced

| ID | Where | OS | Severity |
|----|-------|----|----------|
| B1 | `lgl/migration.py`, `migrate_once` | Linux + Windows | Medium–high |
| B2 | `lgl/launch.py`, `make_executable` | Linux (GTK) | Low–medium |
| B3 | `lgl/launch.py`, `plan_launch` / `SHELL_INTERPRETER` | Linux (GTK) | Medium |

**B1 — A failed copy during the 0.6.0 folder migration is never retried.**
`migrate_once` writes the "done" marker even when some files failed to copy. The
next launch reads the marker, says "Already migrated" and does nothing, so a file
that failed once is never copied.

- *Reproduced:* simulated `PermissionError` on `library.json` during the first
  run (what happens on Windows while the old build still has the file open).
  Run 1 reported "1 could not be copied". Run 2 reported "Already migrated".
  `library.json` was still missing from the new folder; the marker said
  `status: copied`.
- *Effect:* the app starts with an empty library. The old folder is untouched, so
  no data is destroyed, but the user has no way to know it can be recovered.
- *Fix:* write the marker only when `errors` is empty (or write a `partial`
  status that the next launch retries). Retrying must also skip the
  "new folder already holds `config.json`" check, otherwise the files that did
  copy trigger the skip branch. Copying never overwrites, so retrying is safe.
  Add a test that fails one file and then relaunches.

**B2 — `make_executable` marks the file executable even when it refuses.**
`lgl/launch.py` runs `os.chmod(... | 0o100)` first and only then checks that the
file is an AppImage or ELF. For anything else it raises "not a recognized native
executable", but the execute bit is already set.

- *Reproduced:* a non-ELF data file inside a library folder. Before: not
  executable. The call raised the error. After: executable bit set.
- *Effect:* the safety dialog says the file was refused and the file is changed
  anyway. The Linux app calls this function (`ui.py`, "Make executable").
- *Fix:* check `is_appimage` / `looks_like_elf` first, then `chmod`.

**B3 — Shell scripts are always run with `/bin/sh`, ignoring the shebang.**
`plan_launch` builds `["/bin/sh", script]` for every `.sh` file.

- *Reproduced:* the plan for a script starting `#!/bin/bash` is
  `['/bin/sh', '<path>/start.sh']`. On the review host `/bin/sh` is `dash`, and
  `[[ -n $x ]]` fails there with `[[: not found`.
- *Effect:* on Debian, Ubuntu, Mint and anything else where `/bin/sh` is dash,
  game start scripts that use bash features fail. Not tried on a real game
  script.
- *Fix:* read the first line; use bash when the shebang names bash, otherwise
  `/bin/sh`. Keep `shell=False` and the "reviewed first" dialog.

### Problem found by reading only (not reproduced)

**B4 — Startup failures are hidden by a silent fallback (`lgl/__main__.py`).**
On Windows, `from .qt_main import main` is wrapped in `except ImportError` and
falls back to the legacy Tkinter UI. A PySide6 failure such as "DLL load failed"
(a missing Visual C++ runtime) is an `ImportError`, so the user silently gets the
old UI with no message. On Linux the same happens if `ui.py` fails to import, and
if Tk is also missing the user gets a confusing second traceback.

- *Effect:* a broken Qt install looks like "the Windows app is ugly" instead of
  "Qt failed to start".
- *Fix:* log the original error, and show one line ("Qt failed to start: …,
  using the basic window") before falling back. The module docstring is also out
  of date: it says Windows runs Tkinter.

### Smaller notes (from reading code, not reproduced)

- **L1 — `launch.log` is written inside the game's own folder** (both the GTK
  `launch()` and the Qt `_launch`). If that folder is read-only, `open()` fails
  and the launch is refused with "Could not start the program" even though the
  game itself could run.
- **L2 — Two separate launch implementations.** `lgl/qt_library.py::_launch`
  does not use `launch.plan_launch`, so it skips the "file is inside the
  library" check (`is_within`) and the file-type check. It does not hit the
  `os.geteuid()` problem (that function does not exist on Windows), which is good,
  but the two copies can drift apart.
- **L3 — Windows `.exe` files on Linux (not checked).** `launcher_rank` ranks
  `.exe` / `.bat` / `.cmd` files "for Wine/Proton", but `plan_launch` refuses
  anything that is not a shell script, AppImage or ELF. Not checked how `ui.py`
  handles a chosen `.exe`.
- **L4 — Release CI leaves Linux untested.** The test job runs only on
  `windows-latest`; the Linux job only builds the tarball. Nothing imports
  `ui.py` or `movies_ui.py` in CI, so a Linux-only break can ship unnoticed.
  The Windows smoke test only proves `Lumora.exe` stays up for 10 seconds, not
  that Media, Settings or Downloads open.

### Checked and fine

- Migration order is correct in all three frontends: `ensure_app_dirs()` runs
  before `Settings()` in `qt_main.py`, `ui.py` and `windows_app.py`, so
  `config.json` / `library.json` cannot appear early and cause the skip.
- PyInstaller spec: nothing it excludes is imported by `lgl/`; no `print()` or
  `sys.stdout` use in the windowed (`console=False`) build outside GTK-only
  code; the bundled `aria2c` lookup covers both `_MEIPASS` and the exe folder.
- `platform_support`: `open_folder`, `open_url`, `open_magnet` and `notify` never
  use a shell; Windows uses `os.startfile`, Linux uses `xdg-open` then `gio`.
- `updates.py`: version compare handles `0.6.10` vs `0.6.1`, sizes and errors are
  bounded, a failed check never claims "up to date".
- Qt launch path rejects running as administrator and handles `.bat` / `.cmd`
  through `cmd.exe /c` without a shell string.

### ruff hits that are not bugs

- `artwork.py` lines 91, 114, 128: `CatalogGame` is undefined, but only inside
  annotations, which `from __future__ import annotations` never evaluates.
  Import it under `TYPE_CHECKING` to silence it.
- `qt_browse.py:1101`: the closure uses `client = self.client`, which is the
  same object on every loop pass.
- `qt_library.py:1016`: `pixmap_from_bytes` is imported twice; harmless.
- `sources.py:1794`: a `canonical_url` property overrides a class attribute of
  the same name on purpose; the attribute is dead code.
- Unused locals in `artwork.py`, `importer.py`, `qt_media.py`, `qt_settings.py`;
  `global` use in `movies_ui.py`, `sources.py`, `theme_gtk.py`, `tk_style.py`.

## 1.2 Earlier checks

**2026-11-02, version 0.6.0** (from the previous `ToDo.md` header). Checked by
running the suite, rendering the Qt windows offscreen at 1×, 2× and the minimum
window size, and running the release pipeline. The desktop checks need a machine
with a display and Windows, which that review did not have.

**2026-11-02, rename branch.** The 0.6.0 work (folder and app-ID rename with a
copy-on-first-launch migration) lived on `feature/lumora-id-migration`, two
commits ahead of 0.5.0 `main`. It is merged; `main` is 0.6.1.

---

# 2. What to do

**Last checked: 2026-11-03, version 0.6.1** (report 1.1). Desktop checks still
need a machine with a display and Windows.

## 2.1 Fix the bugs from report 1.1

- [ ] **B1 — Retry a partial migration.** Do not write the "done" marker when
  files failed; retry only the missing ones next launch (existing files still
  win). Add a test: fail one file, relaunch, expect it copied
- [ ] **B2 — `make_executable`: check first, then `chmod`.** Add a test that a
  refused file keeps its mode
- [ ] **B3 — Honour the shebang for `.sh` files.** Use bash when the first line
  names bash, `/bin/sh` otherwise. Add a test on the planned command
- [ ] **B4 — Say why Qt (or GTK) did not start.** Log the real `ImportError` and
  show one visible line before falling back; fix the stale `__main__.py`
  docstring
- [ ] **L1 — Write `launch.log` somewhere writable** (the data folder, per game),
  or ignore a failure to write it instead of refusing the launch
- [ ] **L2 — One launch implementation.** Make `qt_library.py::_launch` use the
  same checks as `launch.plan_launch` (inside the library, file type), with the
  Windows branches kept separate
- [ ] **L3 — Check how GTK handles a chosen `.exe`** and make `launcher_rank`
  and `plan_launch` agree
- [ ] **L4 — Give CI a Linux test job.** Run `compileall`, the core tests, and
  an import of `lgl.ui` (needs `python3-gi` in the runner). Extend the Windows
  smoke test to open Media and Settings, not only to stay running
- [ ] **Tidy:** import `CatalogGame` under `TYPE_CHECKING` in `artwork.py`; drop
  the dead `canonical_url` attribute and the duplicate import

## 2.2 Tests and safety net

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

## 2.3 Windows (Qt) gaps

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

## 2.4 Naming and data

- [x] **Migrate data folders to "Lumora".** 0.6.0 moves settings to
  `%APPDATA%\Lumora` / `$XDG_CONFIG_HOME/lumora` and the library, queues and
  catalogue to the matching data folders, replacing `linux-game-library`,
  `NativeGameLibrary` and the app ID `io.github.nativegamelibrary.NativeGameLibrary`.
  `lgl/migration.py` **copies** the old documents across on the first launch:
  nothing is moved or deleted, so an older build still works and rolling back is
  deleting the new folders (the old ones were never touched). Existing new data
  always wins, so a second launch never overwrites anything, and a
  `migration.json` marker records what was copied and how to roll back. The
  image cache is not copied — it is regenerable artwork. *(Open bug: a failed
  copy is never retried — see B1.)*
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

## 2.5 Linux (GTK)

- [ ] **Review the GTK app on a running display**, screen by screen, against the
  Qt app. Needs a display and PyGObject, neither of which the review host has;
  no bugs were found by reading the code *(report 1.1 did find B2 and B3 in the
  launch code the GTK app uses, so "none found by reading" is not proof)*
- [ ] **Split `ui.py` (~11,900 lines) and `movies_ui.py` (~4,000 lines)** into
  smaller modules so they can be tested. Deliberately not started: a move that
  large cannot be checked without a display, and a half-moved GTK app is worse
  than a large one file. This stays last, after the desktop review
- [ ] **Linux packaging beyond the source tarball** (AppImage or Flatpak), if
  wanted. The tarball needs GTK 3, PyGObject, WebKit2GTK and GStreamer from the
  distribution

## 2.6 Problems found in the 2026-11-02 recheck

- [x] **This file lost sections.** After a rewrite it stopped partway through
  section 3 and dropped the naming/data items, the Linux packaging item, the
  housekeeping items, "Done" and "How to re-check". Restored here; the 0.6.0
  rewrite merged with them instead of replacing them
- [ ] **Nothing has been seen on real hardware.** CI proves the app builds and
  starts; it does not show how it looks or behaves on a Windows desktop, and the
  GTK app has not been reviewed on a running display. Covered by the unticked
  items in 2.2 and 2.5, which are the highest-value work left
- [x] **Qt tests can run on the review host now.** This entry previously said
  only the 101 core tests could be re-run locally. PySide6 with its Qt libraries
  is installed under `.venv` and `/tmp/libs`, so the whole suite — Qt tests
  included — runs here: 164 tests pass. What still cannot run here is a real
  desktop, which is the unticked item in 2.2

## 2.7 Housekeeping

- [ ] **Keep this file honest.** When something ships, tick it and update the
  date at the top. Check for lost sections before saving over another edit
- [ ] **Keep part 1 append-only.** Add a new dated report above the old ones;
  do not rewrite an old report, mark an old finding "fixed in <version>" instead

## 2.8 Done (so nobody redoes it)

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

## 2.9 How to re-check

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
