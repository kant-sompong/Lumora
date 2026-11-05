# Lumora — what to do next

Last updated: 2026-11-05 (Lumora 0.6.6). What is finished is in [DONE.md](DONE.md).
Feature checklist: [thing.md](thing.md). Linux = GTK 3 (`lgl/ui.py`), Windows = Qt/PySide6 (`lgl/qt_*.py`), shared logic in `lgl/*.py`.

This file is a hand-over note for a new session (human or AI). Read it, then DONE.md, then start at section 1.

## Rules for any session
- The Linux app is GTK 3 only. Never `gi.require_version("Gtk", "4.0")`, `Gtk.DropDown`, `box.append()`, `set_child()`, `add_css_class()`. (0.6.6 shipped a Tools window written for GTK 4 and it crashed on Linux.)
- Work on a branch. Never force-push. One test per bug fixed.
- Run any new Linux window once on a display before calling it done (headless recipe at the bottom).
- Do not tick a checklist item that was not run. Say what was and was not run.
- Never use a token pasted in chat. Revoke any exposed token at github.com/settings/tokens.

## 1. Needs the owner's decision
- [ ] Review and merge branch `fix/gtk-tools-window-gtk3` (commit 40cd056): ports the Tools window to GTK 3, sidebar instead of 10 tabs, wrapped labels so switches stay on screen, test `tests/test_gtk_tools.py`. Try Tools… from the menu on the real desktop first.
- [ ] Review and merge branch `chore/remove-generated-and-duplicate-files` (commit 7b7d158): removes 164 files (127 tracked `__pycache__`, 33 raw `docs/Screenshot_*.png`, old `screenshots/`). The 24 images in `docs/screens/` are kept.
- [ ] Decide whether to trim the gallery in `docs/SCREENSHOTS.md` (24 images).
- [ ] Decide whether to shrink the repo (about 580 MiB, mostly old screenshots in history). That needs a history rewrite, which breaks clones and branches. Do not do it without an explicit yes.
- [ ] Revoke the GitHub token that was pasted into a chat early on (not confirmed done).

## 2. Linux (GTK) bugs found on 0.6.1, not re-checked on 0.6.6
- [ ] **G1** — Library tiles stretch to full window height when there are few games. Fix: `valign = START` on every `FlowBoxChild` in `LibraryView` (`lgl/ui.py` ~3165), keep `FILL` on the flow. Verified only in memory. Check on a real compositor that the old black band (the reason for `FILL`) does not return.
- [ ] **G2** — An empty capsule stays in the header on Library, Downloads and Media. Fix: hide `self._search.get_parent()` in `_on_view_changed` (~7421), not only the entry and hint.
- [ ] **G3** — The "Availability" filter heading has nothing under it except "Reset all filters" (~6943). Fix or remove.
- [ ] Re-run the whole GTK app on 0.6.6 headless (screens, Settings, About, a real download) and record the result in DONE.md.
- [ ] Click through every page of the new Tools window and its actions (backup, update check, profiles, plugins). Only the layout of each page has been seen.
- [ ] Check on a real desktop: Settings → General toggles (not drawn in the headless render; the Tools switches do draw, so probably a sandbox quirk), and the Media home on first run with no TMDB key.

## 3. Roadmap items still open (from the 0.6.6 notes in thing.md)
- [ ] Continue Watching shelf widget on the Movies & TV page (the data and rules exist in `media_lists.shelf`).
- [ ] Resume-from-position (cannot seek because the player is the system player).
- [ ] Screen-by-screen visual audit and pixel parity (needs a display or GPU).
- [ ] Test provider changes, window resizing and open/close cycles with a real player.
- [ ] Nobody has clicked through either desktop as a human.
- [ ] Merge `qt_library._launch` and `launch.plan_launch` into one implementation (only half done).
- [ ] Extend the Windows smoke test to open Media and Settings, not only stay running (only half done).
- [ ] Add the headless GTK run as a CI smoke job.

## 4. Docs
- [ ] `docs/ROADMAP.md` is stale (says no automatic updater and no Steam/itch.io/GOG import). Check what is true now and update it.
- [ ] Keep this file and DONE.md current: tick things only when they were run.

## 5. Not checked at all
Windows build on real hardware, HiDPI, Wayland, video playback in the embedded player, the real archive.org catalogue and covers (the cloud sandbox had no network).

## How to run the GTK app headless (cloud sandbox)
- Packages (Ubuntu 24.04): `gir1.2-gtk-3.0 gir1.2-webkit2-4.1 python3-gi python3-gi-cairo adwaita-icon-theme xvfb xcompmgr imagemagick`.
- Use `/usr/bin/python3.12` (the system PyGObject is built for 3.12; the default python3 may be 3.13). `pip install --break-system-packages pytest` for that interpreter.
- `xvfb-run -a -s "-screen 0 1600x1100x24" bash -c "xcompmgr & sleep 1; python3.12 driver.py"`. Without `xcompmgr` you get black boxes that are not app bugs.
- Screenshot: `import -window root shot.png`. Use a throw-away `HOME` and XDG folders so real data is untouched.
- Qt tests: `QT_QPA_PLATFORM=offscreen python -m pytest tests -q`.
