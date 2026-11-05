# Lumora — what is finished

Last updated: 2026-11-05 (Lumora 0.6.6). What is still open is in [TODO.md](TODO.md).

## Background
- The Linux GTK app is the main one. The owner wanted the Windows app to look and feel the same.
- Windows became a Qt/PySide6 app (`lgl/qt_*.py`). Linux stays GTK 3. Both take colours and spacing from the shared `lgl/theme.py`. The old Tkinter app (`lgl/windows_app.py`) is still the fallback if Qt or GTK will not import.
- The 0.6.0 release renamed the data folders to `lumora` / `Lumora` with a copy-on-first-launch migration (`lgl/migration.py`).

## Done in the review sessions (Claude)
- Ported the Archive view and the Downloads panel to Qt (merged into `main`).
- Reviewed 0.6.1 (code, tests, ruff, Qt offscreen renders) and reproduced these bugs: B1 a partial migration was never retried, B2 `make_executable` changed the mode before checking the file, B3 `.sh` was always run by `/bin/sh`, B4 a frontend that failed to start gave no reason, plus smaller issues L1–L4.
- Wrote the review as `docs/reports.md` (renamed from `ToDo.md`, merged through PRs #3 and #4). That file was later replaced by `docs/thing.md`; its full text is in git history (commit 2d1e1ca).
- Ran the real GTK app (0.6.1) headless under Xvfb with a compositor: every screen, a real download, Settings and About, in dark and light. No crashes and no GTK warnings. Found G1–G3 (see TODO.md) and reproduced L3 (`.exe` offered but refused; fixed in 0.6.2).
- Found that the 0.6.6 Tools window crashed on Linux (it asked for GTK 4 in a GTK 3 app). Fixed on branch `fix/gtk-tools-window-gtk3` (40cd056): GTK 3 port, sidebar, wrapped labels, new test. All 10 pages open headless. **Not merged.**
- Cleanup branch `chore/remove-generated-and-duplicate-files` (7b7d158) removes 164 unneeded files. **Not merged.**
- Wrote the headless GTK recipe (see TODO.md).

## Done by Codebuff (another AI tool; authored the `main` commits after 0.6.1). Not tested by Claude.
- 0.6.2: fixed B1–B4 and L1–L3 (partial migration retried, executable check first, shebang honoured, startup errors logged and shown, launch log falls back, Qt launcher checks, `.exe` through Wine) and added a Linux CI job.
- 0.6.3: Windows media lookup is part of search, the library grid re-flows with the detail panel, catalogue import shows progress.
- 0.6.4: README showcase, social preview card, QA log for the lost Windows import.
- 0.6.5: shared core for collections, backups, diagnostics and notifications.
- 0.6.6: one Tools window for both desktops (providers, downloads, notifications, home screen, profiles, local files and game sources, release calendar, watch-history sync, plugins, updates/backup/support), metadata cache, search filters/sorting/debounce, playback recovery, shortcuts, keyboard focus rings, palette contrast test, new docs (BACKUP, UPDATING, PLUGINS, PLATFORMS, UI-PARITY). Fixed three Windows CI failures (drive-root guard, bundled tzdata, one follow-on) and named the header icon buttons for screen readers.

## Done on 2026-11-05 (Buffy / Codebuff), branch `fix/gtk-tools-window-gtk3`
- Fixed and **ran** G1, G2, G3 from TODO.md section 2 (`a36468f`, `b37b3d6`), each with a test in `tests/test_gtk_layout.py` that runs against a real X display and real widget allocations.
  - G1: tiles keep their own height (336 px in a 650 px flow, 800 px window); the flow still fills.
  - G2: the whole search pill is hidden, and no-show-all — a plain `set_visible(False)` was undone by `show_all()`, which the screenshot caught and the widget log did not.
  - G3: the empty "Availability" heading is gone; "Reset all filters" stands alone.
- Ran the real GTK app headless (Xvfb :99 + xcompmgr, GTK 3.24.41) through Browse, Library, Downloads, Media, Settings and the Tools window, with pixel captures. No traceback. About and a real download were not exercised.
- Checked the Settings → General toggles functionally (state, `config.json`, survives reopening) and the Media home on first run without a TMDB key (setup card). Their visual look could not be judged in the sandbox: no Adwaita theme CSS and broken fontconfig, so the switch crop renders unstyled.
- Rewrote `docs/ROADMAP.md` from the code (updater, storefront folders vs accounts, plugins, sync, GTK 3 only).
- Made the Linux CI job run the GTK tests under `xvfb-run`, so the layout tests stop skipping. Verified locally as the same pytest command with `DISPLAY=:99`; the `xvfb-run` wrapper itself only runs on the GitHub runner.
- Full suite on GTK 3.24: **354 passed, 6 skipped** (Qt skipped, no PySide6 in the sandbox).
- Sandbox recipe used: GTK 3, Xvfb, xcompmgr and pycairo extracted into a home prefix from Ubuntu 24.04 debs (no root), `/usr/bin/python3.12`, and a small `LD_PRELOAD` shim so the X server could find `xkbcomp`. Not part of the repo; a fresh sandbox needs it again.
- Tools window actions, run for real on GTK 3: `backup.create` wrote a 604-byte `lumora-backup.zip`, `diagnostics.create` wrote a 274-byte `support-report.txt`, and `updates.check` reached the live GitHub API and answered "Lumora 0.6.6 is the latest release." Profile and plugin actions were not triggered.

## Lessons
- 0.6.6 was never run on Linux before release; CI only imported the GTK frontend. A headless run caught the crash.
- Codebuff's test counts (406, then 412) were not re-run by Claude.

## Done on 2026-11-05 (Buffy / Codebuff), branch `feature/shared-ui-and-discovery`

Started the 30 features in `docs/TODO.md` §3 from the shared side, so neither frontend has to
own the logic. New modules, all pure Python with no GTK/Qt imports:

- `lgl/palette.py` — command list for both frontends, ranked search (prefix, word start,
  substring, subsequence), page/settings/action mapping.
- `lgl/view_prefs.py` — compact mode, accent colour (validated, with a contrast-safe label
  colour and six presets), page transitions, artwork backgrounds, spoiler-free, quick preview.
- `lgl/suggestions.py` — search-box suggestions and single completion, de-duplicated.
- `lgl/content_filters.py` — runtime, language, rating, year and genre filters with
  junk-tolerant parsing and a human summary.
- `lgl/discovery.py` — random pick, recommendations from liked titles, similarity, hidden gems,
  and suggestions for incomplete collections.
- `lgl/season_compare.py` — season grouping plus shared/only-first/only-second comparison.
- `lgl/stats.py` — activity heatmap, totals with streak, favourite genres, timeline, game/media
  split and metadata coverage.
- `lgl/library_tools.py` — tags (normalised, persisted), duplicate groups, bulk field updates and
  per-field edits.
- `lgl/trailers.py` — trailer link detection (non-http rejected), YouTube vs local, autoplay
  policy (off unless enabled) and muted default.
- `lgl/shelves.py` — saved filter shelves, capped at eight, filled through `content_filters`.

`lgl/tool_panels.py` gained an **Interface** section with six rows wired to `view_prefs`, so the
same page appears in both the GTK and the Qt Tools window without touching either frontend.

### What was run here

- `python3 -m pytest tests -q` with GTK 3.24.41 on Xvfb: **372 passed, 6 skipped** (the six are
  the Qt tests; PySide6 is not installed in this sandbox).
- `tests/test_shared_features.py`: 18 new tests covering palette ranking, preference round-trips
  and fallbacks, suggestion ranking and de-duplication, filter parsing/matching, discovery
  ordering and exclusions, season comparison, heatmap/streak/timeline maths, tags, duplicates and
  edits, trailer policy, shelves and the panel wiring.
- `ruff check` clean on every new file; `lgl/tool_panels.py` has the same 7 pre-existing findings
  as before this change (verified against `git show HEAD:lgl/tool_panels.py`), so no new lint debt.
- GTK Tools window opened under Xvfb: **11 pages** (was 10), the Interface page renders all six
  rows with switches and combo boxes, and toggling compact/accent/transitions wrote
  `compact_mode`, `accent_color` and `page_transitions` to `config.json`
  (`shots-gtk/07-interface.png`, `shots-gtk/08-interface-toggled.png`).

### What was not run

- The Qt frontend, for the same reason as above.
- Nothing behind the new logic has real data yet: discovery, statistics, tags and shelves were
  exercised with synthetic dicts, not with a real library or live AniList/TMDB responses.
- No screen for the palette, suggestions, dashboard, editor, timeline or suggestions shelves, so
  no TODO item is ticked; §3a of `docs/TODO.md` lists what each item still needs.

