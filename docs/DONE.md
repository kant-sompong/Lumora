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

## Lessons
- 0.6.6 was never run on Linux before release; CI only imported the GTK frontend. A headless run caught the crash.
- Codebuff's test counts (406, then 412) were not re-run by Claude.
