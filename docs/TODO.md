# Lumora — what to do next

Last updated: 2026-11-10 (`main` after PR #55). What is finished is in [DONE.md](DONE.md).
Feature checklist: [thing.md](thing.md). Linux = GTK 3 (`lgl/ui.py`), Windows = Qt/PySide6 (`lgl/qt_*.py`), shared logic in `lgl/*.py`.

This file is a hand-over note for a new session (human or AI). Read it, then [DONE.md](DONE.md), then start at section 1.

## Current hand-over (2026-11-10)

- Current `main`: `66a1c1ae3cbd284b1d5e5122d8356f98470128fc` (PR #55).
- PR #54 added the GTK three-column feed screenshots under `pic/`.
- The GTK feed column-visibility fix (Agent 1, Slice 1) is already on `main`; see [DONE.md](DONE.md). Do not redo it.
- Agent 1 is working on **GTK Feed Slice 2** on a separate feature branch. Approved scope: seven poster rows (Continue, Trending, Movies, TV, For You, Hidden Gems, More Like This), plus the three-column block and schedule strip below the existing 430 px hero. Its 32 `feed-*` CSS rules belong in `_CINEMATIC_CSS` in `movies_ui.py`. This work is not yet reviewed, merged, or complete.
- Agent 2 finished the Qt test-isolation task in PR #55 and is idle. Do not assign it overlapping work without the owner's instruction.
- Ani.pm playback currently opens a title page rather than an embedded player in the affected path. Developer docs specify an iframe wrapper; implementation is optional and has not started.
- The owner manually reviews and merges PRs. Do not merge or release as part of a documentation update.



## Rules for any session
- The Linux app is GTK 3 only. Never `gi.require_version("Gtk", "4.0")`, `Gtk.DropDown`, `box.append()`, `set_child()`, `add_css_class()`. (0.6.6 shipped a Tools window written for GTK 4 and it crashed on Linux.)
- Work on a branch. Never force-push. One test per bug fixed.
- Run any new Linux window once on a display before calling it done (headless recipe at the bottom).
- Do not tick a checklist item that was not run. Say what was and was not run.
- Never use a token pasted in chat. Revoke any exposed token at github.com/settings/tokens.

## 1. Needs the owner's decision
- [ ] Merge `chore/remove-generated-and-duplicate-files` (`7b7d158`): removes the 15 raw `docs/Screenshot_*.png` still on `main` and old `screenshots/`.
- [ ] Trim the gallery in `docs/SCREENSHOTS.md` (24 images), or keep it.
- [ ] Shrink the repo (about 580 MiB, mostly old screenshots in history)? Needs a history rewrite that breaks clones and branches. Not without an explicit yes.
- [ ] Revoke the GitHub token that was pasted into a chat early on (not confirmed done): github.com/settings/tokens.
- [ ] Release 0.6.8 (current is 0.6.7). A DeepSeek prompt prepares the version bump and notes without tagging; pushing the `v0.6.8` tag publishes (the workflow builds the Windows zip, installer and Linux tarball). Owner says when.

## 2. Open checks (a human, on real machines)
Nobody has used either app by hand. In particular:
- [ ] Windows with real QtWebEngine: Watch plays inside the main window, fullscreen hides everything, Escape then fullscreen again works, a trailer plays, autoplay is muted.
- [ ] Linux: the video freezing after pause or skip-10s. Suspect WebKitGTK GPU rendering; try `WEBKIT_DISABLE_DMABUF_RENDERER=1 python3 -m lgl`. No fix made.
- [ ] Profile switching on both desktops (restart takes the new profile; the library, tags, collections and history are separate).
- [ ] Artwork backgrounds, quick preview and the Settings switches on a real desktop with a theme (the sandbox has no Adwaita, so switches did not draw).
- [ ] Both apps: screen-by-screen visual audit and window resizing with a real player.

## 3. The 30 requested features: original implementation status (snapshot 2026-11-08)

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

### 3.2 Updates after the original 30-feature snapshot (2026-11-10)

- **GTK feed column visibility:** landed on `main` before PR #54. The renderer now reveals only the populated column instead of recursively showing empty siblings. Seven regression cases were added. Agent 1 reported 26 passes in `tests/test_gtk_feed.py`; the wider GTK run had environment limitations recorded in [DONE.md](DONE.md).
- **Feed screenshots:** PR #54 added four three-column screenshots under `pic/`; these are test/layout evidence, not a completion of the planned Feed Slice 2 redesign.
- **Qt test isolation:** PR #55 added shared Qt worker-pool fixtures and made profile/header tests deterministic and offline. Agent 2 reported 823 passed, 0 failed, 0 errors across foreground chunks; see [DONE.md](DONE.md) for the exact scope and what was not run.
- **GTK Feed Slice 2:** in progress on Agent 1's separate branch. Do not tick it complete until its diff, tests and screenshots are reviewed and the owner merges it.

## 4. Ideas not built
- Continue Watching shelf on Movies & TV (the data and rules exist in `media_lists.shelf`).
- Resume from position (the player is a web page, so this needs the provider's support).
- A Qt quick preview, live profile switching (today a restart is needed), per-episode watched state, media favourites storage.

## 5. Docs
Keep this file and DONE.md current: tick things only when they were run. `docs/ROADMAP.md` was rewritten on 2026-11-05 and may be behind `main` again.

## How to run the GTK app headless (cloud sandbox)
- Packages (Ubuntu 24.04): `gir1.2-gtk-3.0 gir1.2-webkit2-4.1 python3-gi python3-gi-cairo adwaita-icon-theme xvfb xcompmgr imagemagick`.
- Use `/usr/bin/python3.12` (the system PyGObject is built for 3.12; the default python3 may be 3.13). `pip install --break-system-packages pytest` for that interpreter.
- `xvfb-run -a -s "-screen 0 1600x1100x24" bash -c "xcompmgr & sleep 1; python3.12 driver.py"`. Without `xcompmgr` you get black boxes that are not app bugs.
- Screenshot: `import -window root shot.png`. Use a throw-away `HOME` and XDG folders so real data is untouched.
- Qt tests: `QT_QPA_PLATFORM=offscreen python -m pytest tests -q`.
