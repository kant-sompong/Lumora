# Lumora — what to do next

Last updated: 2026-11-08 (`main` after PR #43). What is finished is in [DONE.md](DONE.md).
Feature checklist: [thing.md](thing.md). Linux = GTK 3 (`lgl/ui.py`), Windows = Qt/PySide6 (`lgl/qt_*.py`), shared logic in `lgl/*.py`.

This file is a hand-over note for a new session (human or AI). Read it, then DONE.md, then start at section 1.

## Rules for any session
- The Linux app is GTK 3 only. Never `gi.require_version("Gtk", "4.0")`, `Gtk.DropDown`, `box.append()`, `set_child()`, `add_css_class()`. (0.6.6 shipped a Tools window written for GTK 4 and it crashed on Linux.)
- Work on a branch. Never force-push. One test per bug fixed.
- Run any new Linux window once on a display before calling it done (headless recipe at the bottom).
- Do not tick a checklist item that was not run. Say what was and was not run.
- Never use a token pasted in chat. Revoke any exposed token at github.com/settings/tokens.

## 1. Needs the owner's decision
- [x] Review and merge branch `fix/gtk-tools-window-gtk3` (commit 40cd056): ports the Tools window to GTK 3, sidebar instead of 10 tabs, wrapped labels so switches stay on screen, test `tests/test_gtk_tools.py`. Merged in 0.6.7 (PR #9) after 412 tests passed here; still not opened on a real desktop.
- [ ] Review and merge branch `chore/remove-generated-and-duplicate-files` (commit 7b7d158): removes 164 files (127 tracked `__pycache__`, 33 raw `docs/Screenshot_*.png`, old `screenshots/`). The 24 images in `docs/screens/` are kept.
- [ ] Decide whether to trim the gallery in `docs/SCREENSHOTS.md` (24 images).
- [ ] Decide whether to shrink the repo (about 580 MiB, mostly old screenshots in history). That needs a history rewrite, which breaks clones and branches. Do not do it without an explicit yes.
- [ ] Revoke the GitHub token that was pasted into a chat early on (not confirmed done).

## 2. Linux (GTK) bugs found on 0.6.1, not re-checked on 0.6.6
- [x] **G1** — Library tiles stretch to full window height when there are few games. Fixed on `fix/gtk-tools-window-gtk3` (`a36468f`): `valign = START` on the tile, `FILL` kept on the flow. Measured on the running app under Xvfb + xcompmgr: 3 tiles 336 px tall inside a 650 px flow in an 800 px window. `tests/test_gtk_layout.py` fails if a tile fills the flow again. Still worth a look on a real desktop with a window manager.
- [x] **G2** — An empty capsule stays in the header on Library, Downloads and Media. Fixed (`a36468f`, then `b37b3d6`): `apply_search_visibility()` hides the whole pill and marks it no-show-all, because the next `show_all()` put the capsule straight back — that only showed up in the screenshot, not in the widget state. Verified on the running app: `mapped=False` on all three pages and `True` on Browse.
- [x] **G3** — The "Availability" filter heading has nothing under it except "Reset all filters" (~6943). Removed rather than invented: there is no availability data in the model, so the heading is gone and the button sits in its own footer (`_filter_footer`). Checked in a capture of the Browse filter column.
- [ ] Re-run the whole GTK app on 0.6.6 headless (screens, Settings, About, a real download) and record the result in DONE.md.
  - Partly done 2026-11-05 on `fix/gtk-tools-window-gtk3`: Browse, Library (with tiles), Downloads, Media (first run, no TMDB key), Settings (General) and the Tools window all ran under Xvfb + xcompmgr with captures and no traceback. **Not done: About, and a real download.**
- [ ] Click through every page of the new Tools window and its actions (backup, update check, profiles, plugins). Only the layout of each page has been seen.
  - 2026-11-05: all ten pages open on GTK 3 (sidebar). Actions triggered and checked: `backup.create` wrote `lumora-backup.zip` (604 B), `diagnostics.create` wrote `support-report.txt` (274 B), `updates.check` reached the live GitHub API and reported "Lumora 0.6.6 is the latest release." **Profiles and plugin actions were not triggered**, so this stays open.
- [ ] Check on a real desktop: Settings → General toggles (not drawn in the headless render; the Tools switches do draw, so probably a sandbox quirk), and the Media home on first run with no TMDB key.
  - Headless 2026-11-05: the notifications switch responds (44×22, mapped), writes `notifications: true` to `config.json`, and survives closing and reopening the dialog; the Media home shows the "TMDB is not connected" card with Configure providers. Their **look** could not be judged: the sandbox has no Adwaita theme and degraded fontconfig, so the switch crop came out unstyled white/black. Needs a real desktop.

## 3. The 30 requested features: status on `main` (2026-11-08)

All 30 items have a first version in both apps, merged to `main` through PRs up to #43. Tests on `main`: GTK 612 passed / 2 skipped, Qt 644 passed (14 `test_launch` tests excluded; two of them fail only when run as root). GTK was opened on a display in the review sandbox (Xvfb + xcompmgr); **Qt was only rendered offscreen, never on real Windows, and nobody has clicked through either app as a human.** "Done" below means built, tested and looked at in that way, not accepted by the owner.

| # | Feature | Linux (GTK) | Windows (Qt) | Known gaps |
|---|---------|-------------|--------------|------------|
| 1 | Command palette | yes | yes | |
| 2 | Dashboard layout | order, hide, size per section, reset | same | free placement of shelves |
| 3 | Artwork backgrounds | yes | yes | checked only with generated images |
| 4 | Quick preview | yes (hover popover, Esc closes) | n/a | no Qt version |
| 5 | Compact mode | yes | yes | |
| 6 | Accent colours | yes | yes | |
| 7 | Page transitions | yes | yes | |
| 8 | Custom home shelves | yes | yes | |
| 9 | Recently viewed | yes | yes | |
| 10 | Search suggestions | yes | yes | |
| 11 | Random discovery | yes | yes | |
| 12 | Recommendations | yes | yes | |
| 13 | Trailer preview | yes | yes | no trailer ever played (no WebKit/WebEngine/network in the sandbox); autoplay needs a real test |
| 14 | Content filtering | Browse filters | Browse filters | Windows filter column never looked at on a display |
| 15 | Collections | yes | yes | membership is manual |
| 16 | Season comparison | yes | yes | a sequel with a new AniList id counts as new |
| 17 | Watch heatmap | yes | yes | |
| 18 | Similarity explorer | yes | yes | |
| 19 | Hidden gems | games (Discover) and AniList anime | same |  |
| 20 | Spoiler-free mode | episode titles hidden | same | descriptions and stills are not shown anywhere, so nothing else to hide |
| 21 | Favourites dashboard | yes | yes | media favourites are not stored anywhere yet |
| 22 | Statistics | yes | yes | no play time is recorded |
| 23 | Profile switching | separate settings, library, tags, collections, history | same | takes effect on the next start, not live |
| 24 | Duplicate detection | yes | yes | |
| 25 | Metadata editor | yes | yes | |
| 26 | Library tags | yes | yes | |
| 27 | Bulk actions | yes | yes | one level of undo |
| 28 | Collection suggestions | yes | yes | |
| 29 | Activity timeline | yes | yes | |
| 30 | Keyboard shortcuts | yes | yes | applied on next start |

Windows media player: playback now happens inside the main window (`lgl/qt_player.py`), with real fullscreen and an "exit fullscreen" call to the page. Never run with real QtWebEngine.

### 3.1 Polish pass (done 2026-11-08, PR #45)
Hidden-gems thresholds are saved and a failed first load retries; failed loads hide the empty lists; season summary says "Winter 2024"; the Qt Trailer button is in the hero row; a failed trailer lookup shows "Trailer not available"; `profiles.migrate()` removed. Still loose: in the Qt failure states the remaining lines sit far apart (expanding labels), and settings are written twice per Refresh.

## 4. Roadmap items still open (from the 0.6.6 notes in thing.md)
- [ ] Continue Watching shelf widget on the Movies & TV page (the data and rules exist in `media_lists.shelf`).
- [ ] Resume-from-position (the player is a web page; resuming needs the provider's own support).
- [ ] Screen-by-screen visual audit and pixel parity (needs a display or GPU).
- [ ] Test provider changes, window resizing and open/close cycles with a real player.
- [ ] Nobody has clicked through either desktop as a human.
- [x] Merge `qt_library._launch` and `launch.plan_launch` into one implementation. — 0.6.8: `launch.py` is platform-aware (direct .exe on Windows, COMSPEC for .bat/.cmd, root check that tolerates a missing `geteuid`), the Qt library calls it, and three tests cover the Windows branches.
- [x] Extend the Windows smoke test to open Media and Settings. — 0.6.8: `Lumora.exe --self-test` opens Browse, Library, Downloads, Media and the Settings sheet, exits non-zero on any exception, and CI runs it before the stay-running check.
- [x] Add the headless GTK run as a CI smoke job. "Tests (Linux, GTK)" now installs `xvfb`/`xauth` and runs the suite under `xvfb-run`, so `tests/test_gtk_layout.py` stops skipping; before this the Linux job only imported the frontend, which is how the 0.6.6 crash shipped.

## 5. Docs
- [x] `docs/ROADMAP.md` is stale (says no automatic updater and no Steam/itch.io/GOG import). Check what is true now and update it. Rewritten 2026-11-05 from the code: the updater still never downloads or installs (consent, interval, checksum check, manual instructions only); Steam/Epic/GOG **are** supported as folders you point at, with no account and no launcher-database access, while itch.io is not supported at all; plus the plugin, sync, GTK-3-only and auto-launch limits.
- [ ] Keep this file and DONE.md current: tick things only when they were run.

## 6. Not checked at all
A human click-through of both apps. The Windows player, trailer and artwork backgrounds with real WebEngine. The video freezing after pause or skip-10s (reported on Linux; suspected WebKitGTK GPU rendering, try `WEBKIT_DISABLE_DMABUF_RENDERER=1`; no fix made). Windows build on real hardware, HiDPI, Wayland, video playback in the embedded player, the real archive.org catalogue and covers (the cloud sandbox had no network).

## How to run the GTK app headless (cloud sandbox)
- Packages (Ubuntu 24.04): `gir1.2-gtk-3.0 gir1.2-webkit2-4.1 python3-gi python3-gi-cairo adwaita-icon-theme xvfb xcompmgr imagemagick`.
- Use `/usr/bin/python3.12` (the system PyGObject is built for 3.12; the default python3 may be 3.13). `pip install --break-system-packages pytest` for that interpreter.
- `xvfb-run -a -s "-screen 0 1600x1100x24" bash -c "xcompmgr & sleep 1; python3.12 driver.py"`. Without `xcompmgr` you get black boxes that are not app bugs.
- Screenshot: `import -window root shot.png`. Use a throw-away `HOME` and XDG folders so real data is untouched.
- Qt tests: `QT_QPA_PLATFORM=offscreen python -m pytest tests -q`.
