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

Newest report first by date; numbers are stable, so 1.5 sits on top.

## 1.5 The Windows catalogue import was thrown away — 2026-11-04 (on 0.6.3)

**Covers:** `main` after 0.6.3, Windows Qt only. Found while re-checking the
catalogue import that report 1.1 called done.

### How it was checked

- `pytest tests -q` with PySide6 and its Qt libraries present: **183 passed**,
  two of them new here.
- A live import through the real dialog against kryo.to: two pages, 50 records.
- The path was walked again after the fix: 50 records in `catalog.json` on disk,
  and Browse listed 50.

### The bug

**W1 — `CatalogDialog` imported into a store that could never save anything.**
Both `_summary()` and the import worker built `CatalogStore()` with no path. The
importer filled that throw-away store, `count_games()` read it back, and the
dialog announced "Import finished. 50 catalogue games are stored" — while
nothing had been written and Browse, which reads the window's own store, never
changed. The status line before an import was equally wrong in the other
direction: a fresh pathless store is always empty, so it always said "No
catalogue games stored yet".

- *Effect:* on Windows, Website Sources and Kryoto imports did nothing except
  show progress. The progress counters added in report 1.1 made this look like
  it worked.
- *Cause worth remembering:* the GTK app imports into `self.catalog`, the store
  the rest of the window reads. Porting the dialog to Qt lost that wiring, and
  no test imported a catalogue and then looked at the listing.
- *Fix:* the dialog takes the window's store (`catalog=`) and an `on_imported`
  callback, calls `save()` and says so when the save fails instead of reporting
  a count. `BrowsePage` gained `reload_catalog()`, `_catalog_summaries()` and
  `_merge_catalog()`; `qt_main` passes both, shows a toast with the new total
  and switches to Browse, the way the GTK build does after Kryoto.
  Tests: `test_catalog_import_lands_in_the_window_store`,
  `test_browse_lists_imported_catalogue_records`

### Not covered

A real Windows desktop (the dialog is driven offscreen); the Website Sources
address rather than kryo.to (the fake importer in the tests returns early);
importing into a catalogue that already holds games, since both tests import
once and read the listing back.

### State

Fixed on `main`, **not released**: 0.6.3 is the newest tag, so the fix reaches
users with the next release.

## 1.4 Linux GTK app run for real — 2026-11-04 (on 0.6.1)

**Covers:** `main` at `5e04f35` (0.6.1): `lgl/ui.py`, `lgl/movies_ui.py`. This
is the first time the GTK app was actually started in these reviews (report 1.2
could only read it). Everything below describes 0.6.1; the launch fixes in
report 1.1 (0.6.2) and the 0.6.3 library spacing came afterwards and were **not**
re-run on the GTK app.

### How it was checked

- The real GTK 3.24 app (Python 3.12 + PyGObject 3.48, WebKit2 4.1) on a virtual
  X display (Xvfb, 1600×1100) **with a compositor (`xcompmgr`)**, using a
  throw-away `HOME` / XDG folders.
- Seeded data: a cached catalogue of 12 fake games (the sandbox cannot reach
  archive.org, so covers fall back to monograms), 3 fake downloaded games, and a
  local test HTTP server.
- A driver visited Browse, Library, Downloads and Media, typed a search, changed
  the sort and direction, rescanned the library, ran a **real download through
  the app's own download manager**, opened Settings (General, Locations,
  Providers, Shortcuts) and About, in dark and light: 21 steps per theme, with a
  screenshot of each screen.
- Setup on Ubuntu 24.04: `apt install gir1.2-gtk-3.0 gir1.2-webkit2-4.1 python3-gi
  python3-gi-cairo adwaita-icon-theme xvfb xcompmgr imagemagick`, then
  `xvfb-run -s "-screen 0 1600x1100x24"` with `xcompmgr &` running, and
  `import -window root shot.png` for screenshots. In the sandbox the default
  `python3` was 3.13 while PyGObject was built for 3.12, so `/usr/bin/python3.12`
  had to be used.

### Result

0 failed steps, 0 uncaught Python exceptions, **0 lines on stderr** (no GTK
warnings), and the 5 MiB test download completed at exactly 5,242,880 bytes. The
GTK app does not crash through any of these paths. The Linux layout (left rail,
back/forward arrows, search pill, Browse filter column) now matches the Qt app.

### Not covered

A real desktop (theme, fonts, HiDPI, Wayland, pointer behaviour); the real
archive.org catalogue and covers (no network); video playback in the embedded
player; the actions behind the Providers and Media settings (pages were only
opened); the launch dialogs (the functions behind them were tested directly).

### Problems found and reproduced

| ID | Where | Severity |
|----|-------|----------|
| G1 | `ui.py` `LibraryView`, `self._flow.set_valign(Gtk.Align.FILL)` (~line 3165) | Medium (visible) |
| G2 | `ui.py` header search pill, hide logic (~line 7421) | Low (cosmetic) |
| G3 | `ui.py` Browse filter column, "Availability" section (~line 6943) | Low (cosmetic) |
| L3 | `launch.py`, `.exe` offered but refused (fixed in 0.6.2) | Medium (dead end) |

**G1 — Library tiles stretch to the full window height when there are few
games.** With 3 games the cover floats in the middle of a tall tile, the title
sits about 120 px below it, and the selected highlight covers a full-height
column. The Qt app and the Browse grid (12 games, two rows) look correct. An
older real-desktop screenshot of the Library shows the same stretch, so this is
not a sandbox artefact.

- *Cause:* the flow is set to `valign = FILL` on purpose; a code comment says
  this was done because transparent margins "left an unpainted band that
  rendered black on some compositors".
- *Fix, verified in memory (repo not changed):* keep `FILL` on the flow and call
  `child.set_valign(Gtk.Align.START)` on every `FlowBoxChild`. The render then
  shows covers top-aligned with the title directly below. Re-check on a real
  compositor that the black band does not come back.

**G2 — An empty capsule stays in the middle of the header on Library, Downloads
and Media.** The search entry and its "Ctrl K" hint are hidden when you leave
Browse, but the box that holds them (`pill`, CSS class `search-pill`, set as the
header's custom title) is not.

- *Fix, verified in memory:* hide and show `self._search.get_parent()` instead.
  With the whole pill hidden the stray shape is gone.

**G3 — The "Availability" heading has nothing under it except "Reset all
filters".** Nothing in `ui.py` ever adds options to that section. Rename the
heading, drop it, or add the options.

**L3 (reproduced on 0.6.1; fixed in 0.6.2 through Wine) — `.exe` files were offered as start
files but can never be launched on Linux.** `launcher_rank('Gamma.exe',
title='Gamma Game')` returns 20 (offered); `plan_launch` then refuses with
"This file is not a shell script, AppImage, or ELF executable. It was not
launched." The GTK dialog calls `launch()` straight after the review dialog
(`ui.py` ~8809), so the user confirms and then gets an error.

**B2 and B3 applied to the GTK app (0.6.1; fixed in 0.6.2, see report 1.1).** `ui.py` (~8801, ~8809) calls
`make_executable(path, download_dir)` and then `launch(path, download_dir)`, the
same functions as in report 1.2, so the execute-bit-before-check bug (B2) and
the always-`/bin/sh` script bug (B3) are in the real Linux launch flow.

### Not confirmed (check on a real desktop)

- **Settings → General toggles.** In my render the two switches (Desktop
  notifications, Rescan library on startup) are not drawn: that area is a single
  flat colour, and the dialog also shows black margins. The widgets exist, are
  mapped and checked, and the stylesheet colours checked switches with the
  accent, so this is probably a quirk of a setup with no window manager. It
  cannot be ruled out as a real bug.
- **Media home** shows blank panels with no message in the sandbox (no network,
  no TMDB key). Worth checking what a first-run user without a key sees.

### Rendering artefacts to ignore

Without a compositor, a black rectangle and black frames appeared in the first
screenshots. They disappeared with `xcompmgr` running, so they are not app bugs.

## 1.1 Fixes for the 0.6.1 review — 2026-11-03
**Covers:** `main` after the review in 1.2; the launcher and migration fixes
shipped in `v0.6.2`, the media-search and library-layout work in `v0.6.3`.
Linux and Windows.
### What changed
- **B1 — a partial migration is retried.** `migrate_once` now records
  `status: "partial"` when any file failed and resumes on the next launch
  instead of writing `copied` and doing nothing. Resuming skips the "the new
  folders already hold settings" guard, which would otherwise block it, copies
  only what is missing, and still never overwrites. Once the old folders are
  gone there is nothing left to retry, so the marker is finalised as `skipped`.
  `summary()` tells the user it will try again.
  Test: `test_a_partial_migration_is_retried_on_the_next_launch`
- **B2 — `make_executable` checks before it changes anything.** The
  AppImage/ELF check now runs first, so a file the dialog refuses keeps its
  mode.
  Tests: `test_make_executable_refuses_before_changing_the_mode`,
  `test_make_executable_will_not_leave_the_library`
- **B3 — the shebang is honoured.** `plan_launch` reads the first line of a
  `.sh` file: bash (also through `#!/usr/bin/env bash`) runs under bash, and
  everything else keeps `/bin/sh`. `shell=False` and the review dialog are
  unchanged.
  Tests: `test_a_bash_script_is_run_by_bash`,
  `test_a_bash_script_found_through_env_is_run_by_bash`,
  `test_a_plain_script_still_uses_sh`
- **B4 — a frontend that does not start says why.** `__main__.py` logs the real
  `ImportError` to stderr and to `startup.log` in the cache folder, shows a
  dialog in the windowed build (which has no stderr at all), and reports a
  missing Tk fallback as one line instead of a second traceback. The stale
  module docstring is fixed.
- **L1 — a log is no longer a reason to refuse a launch.** New
  `launch.open_launch_log()` tries the game's folder, then
  `<data>/launch-logs/<game>.log`, then starts the program with no log at all.
  All three launchers use it (GTK, Qt, Tkinter).
  Tests: `test_the_launch_log_falls_back_to_the_data_folder`,
  `test_the_launch_log_uses_the_game_folder_when_it_can`
- **L2 — the Qt launcher makes the same checks.** `qt_library._launch` refuses
  a path outside the download folder and a file that is not `.exe`, `.com`,
  `.bat` or `.cmd` before it starts anything. The Windows command branches stay
  separate from `plan_launch`, which is POSIX-only (`os.geteuid`).
- **L3 — `launcher_rank` and `plan_launch` agree.** A `.exe` / `.bat` / `.cmd`
  is now a "Windows build": `plan_launch` starts it through Wine (or `wine64`,
  with `cmd /c` for batch files) and, when Wine is missing, refuses with a
  message that names Wine instead of "not a shell script, AppImage, or ELF".
  Tests: `test_a_windows_build_says_wine_is_needed`,
  `test_a_windows_build_runs_through_wine_when_it_is_installed`,
  `test_a_windows_batch_file_is_run_through_cmd`
- **L4 (half) — CI now tests Linux.** A new `Tests (Linux, GTK)` job runs
  `compileall`, the core tests and `import lgl.ui` on `ubuntu-latest` with
  `python3-gi` and GTK 3 from apt. The other half of L4 — the Windows smoke
  test opening Media and Settings instead of only staying up — is still open in
  2.2, because it needs UI automation the runner does not have.
- **Tidy:** `CatalogGame` is imported under `TYPE_CHECKING` in `artwork.py`;
  the duplicate `pixmap_from_bytes` import and FreeToGame's dead
  `canonical_url` attribute are gone.
### Windows media and library changes (v0.6.3)
- **The media lookup is the search bar.** The separate "Lookup…" button and its
  title dialog are gone: one search resolves TMDB, MyAnimeList and AniList in
  the same pass and offers the AniList match as a single "Play … on
  <provider>" button, which is how the GTK build behaves with one field.
- **The library grid follows its own width.** Columns were only recomputed on a
  *page* resize, so opening the detail panel — a *viewport* resize — left the
  grid with too many columns and the last card was clipped behind the panel.
  It now re-flows on the viewport's own resize, uses 12/14 px gutters, and the
  detail actions follow the details instead of being pushed to the bottom. The
  GTK library flow gets the same tighter spacing.
- **Catalogue imports report progress.** The Website Sources and Kryoto dialogs
  show the importer's page and record counters plus a progress bar, instead of
  one line that never changed during a paced multi-page walk. Verified against
  live kryo.to: two pages, 50 records, counters advancing.
- Checked offscreen at 1180×900: the grid re-flows, no column is clipped, and
  the actions sit under the START WITH block. The renders had no fonts on this
  host, so they prove geometry, not typography.

### How it was checked
- `python -m compileall -q lgl packaging tests`: clean.
- `QT_QPA_PLATFORM=offscreen python -m pytest tests -q` excluding the four Qt
  files: **128 passed**, 12 of them new (11 in `tests/test_launch.py`, 1 in
  `tests/test_migration.py`).
- Not run here: the Qt files (PySide6 needs `libxkbcommon`, which this host does
  not have), the GTK app and any real desktop. The Windows CI job runs the
  whole suite and is what has to pass before this is trusted.

## 1.2 Review of 0.6.1 — 2026-11-03

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


## 1.3 Earlier checks

**2026-11-02, version 0.6.0** (from the previous `ToDo.md` header). Checked by
running the suite, rendering the Qt windows offscreen at 1×, 2× and the minimum
window size, and running the release pipeline. The desktop checks need a machine
with a display and Windows, which that review did not have.

**2026-11-02, rename branch.** The 0.6.0 work (folder and app-ID rename with a
copy-on-first-launch migration) lived on `feature/lumora-id-migration`, two
commits ahead of 0.5.0 `main`. It is merged; `main` is 0.6.1.

---

# 2. What to do

**Last checked: 2026-11-04, version 0.6.3** (reports 1.1, 1.2 and 1.5). The W1 fix
is on `main` but is not in a release yet, so 0.6.3 as published still loses a
Windows catalogue import. Desktop checks still need a machine with a display and
Windows.

## 2.1 Fix the bugs from report 1.2

- [x] **B1 — Retry a partial migration.** *Fixed in report 1.1:* a failed
  copy is recorded as `partial` and only the missing files are retried on the
  next launch; a test fails one file, relaunches, and expects it copied.
- [x] **B2 — `make_executable`: check first, then `chmod`.** *Fixed in
  report 1.1:* the AppImage/ELF check runs first, so a refused file keeps its
  mode; covered by a test.
- [x] **B3 — Honour the shebang for `.sh` files.** *Fixed in report 1.1:*
  bash (also via `#!/usr/bin/env bash`) runs under bash, everything else keeps
  `/bin/sh`; the test asserts the planned command.
- [x] **B4 — Say why Qt (or GTK) did not start.** *Fixed in report 1.1:*
  the real `ImportError` is written to stderr and `startup.log` and shown in a
  dialog in the windowed build; a missing Tk fallback is one line, and the
  `__main__.py` docstring is current.
- [x] **L1 — Write `launch.log` somewhere writable.** *Fixed in report 1.1:*
  `open_launch_log()` tries the game folder, then
  `<data>/launch-logs/<game>.log`, then runs the program with no log at all, so
  the log can never refuse a launch.
- [x] **L2 — One launch implementation.** *Half done in report 1.1:*
  `qt_library._launch` now refuses a path outside the download folder and a file
  type it cannot start, and shares the log opener. The two implementations are
  still separate; merging them is not done.
- [x] **L3 — Check how GTK handles a chosen `.exe`.** *Fixed in report 1.1:*
  a `.exe` / `.bat` / `.cmd` is a "Windows build", started through Wine when it
  is installed and refused with a message that names Wine when it is not, so
  `launcher_rank` and `plan_launch` agree.
- [x] **L4 — Give CI a Linux test job.** *Half done in report 1.1:*
  `compileall`, the core tests and `import lgl.ui` run on `ubuntu-latest` with
  GTK 3 from apt. Extending the Windows smoke test to open Media and Settings
  is still open (see 2.2).
- [x] **Tidy.** *Done in report 1.1:* `CatalogGame` is imported under
  `TYPE_CHECKING` in `artwork.py`; the duplicate `pixmap_from_bytes` import and
  FreeToGame's dead `canonical_url` attribute are gone.
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

- [x] **W1 — Keep the imported catalogue.** *Fixed after report 1.5:* the dialog
  imports into the window's store, saves it, reports a save it could not do, and
  tells Browse to reload and jump there. Covered by two Qt tests
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
  image cache is not copied — it is regenerable artwork. *(B1, the failed-copy retry, was fixed after that
  review; see 1.1.)*
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

- [ ] **G1 — Library tiles stretched** (report 1.4, on 0.6.1; re-check on 0.6.3).
  Set `valign = START` on every `FlowBoxChild`, keep `FILL` on the flow, and check
  on a real compositor that the old black band does not return
- [ ] **G2 — Hide the whole header search pill** off Browse (report 1.4)
- [ ] **G3 — Fix the empty "Availability" heading** in the Browse filters (report 1.4)
- [ ] **Check on a real Linux desktop:** Settings → General toggles, the Media home
  on first run without a TMDB key, and the Library after G1 (report 1.4)
- [ ] **Review the GTK app on a running display**, screen by screen, against the
  Qt app. *(Done once on a virtual display, report 1.4; a real desktop is still open.)* Needs a display and PyGObject, neither of which the review host has;
  no bugs were found by reading the code *(report 1.2 did find B2 and B3 in the
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
- Tests and packaging: core test suite, Qt tests (183 in the suite, run in CI), portable
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
