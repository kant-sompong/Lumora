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
- [x] **G1** — Library tiles stretch to full window height when there are few games. Fixed on `fix/gtk-tools-window-gtk3` (`a36468f`): `valign = START` on the tile, `FILL` kept on the flow. Measured on the running app under Xvfb + xcompmgr: 3 tiles 336 px tall inside a 650 px flow in an 800 px window. `tests/test_gtk_layout.py` fails if a tile fills the flow again. Still worth a look on a real desktop with a window manager.
- [x] **G2** — An empty capsule stays in the header on Library, Downloads and Media. Fixed (`a36468f`, then `b37b3d6`): `apply_search_visibility()` hides the whole pill and marks it no-show-all, because the next `show_all()` put the capsule straight back — that only showed up in the screenshot, not in the widget state. Verified on the running app: `mapped=False` on all three pages and `True` on Browse.
- [x] **G3** — The "Availability" filter heading has nothing under it except "Reset all filters" (~6943). Removed rather than invented: there is no availability data in the model, so the heading is gone and the button sits in its own footer (`_filter_footer`). Checked in a capture of the Browse filter column.
- [ ] Re-run the whole GTK app on 0.6.6 headless (screens, Settings, About, a real download) and record the result in DONE.md.
  - Partly done 2026-11-05 on `fix/gtk-tools-window-gtk3`: Browse, Library (with tiles), Downloads, Media (first run, no TMDB key), Settings (General) and the Tools window all ran under Xvfb + xcompmgr with captures and no traceback. **Not done: About, and a real download.**
- [ ] Click through every page of the new Tools window and its actions (backup, update check, profiles, plugins). Only the layout of each page has been seen.
  - 2026-11-05: all ten pages open on GTK 3 (sidebar). Actions triggered and checked: `backup.create` wrote `lumora-backup.zip` (604 B), `diagnostics.create` wrote `support-report.txt` (274 B), `updates.check` reached the live GitHub API and reported "Lumora 0.6.6 is the latest release." **Profiles and plugin actions were not triggered**, so this stays open.
- [ ] Check on a real desktop: Settings → General toggles (not drawn in the headless render; the Tools switches do draw, so probably a sandbox quirk), and the Media home on first run with no TMDB key.
  - Headless 2026-11-05: the notifications switch responds (44×22, mapped), writes `notifications: true` to `config.json`, and survives closing and reopening the dialog; the Media home shows the "TMDB is not connected" card with Configure providers. Their **look** could not be judged: the sandbox has no Adwaita theme and degraded fontconfig, so the switch crop came out unstyled white/black. Needs a real desktop.

## 3. Features the owner asked for (new work)

Requested 2026-11-05. Ask for each: shared logic in `lgl/` with tests, then both frontends (GTK 3 and Qt), one branch per feature, run once on a display before calling it done.

### 3.0 Release calendar with real dates (asked first)
The owner's words: "tell what anime/tv/movie episode come out what day".
- [ ] Show which anime episode, TV episode or movie comes out on which day.
- Status: **in progress** on branch `feature/release-calendar` (commit 51e5920, work in progress, not merged). Written and tested with fake data only; nothing has been run against the live AniList or TMDB servers (the sandbox cannot reach them):
  - `lgl/release_feed.py` fetches anime air times (AniList), TV next episodes and upcoming movies (TMDB, needs the user's key), with a 3-hour cache and per-source error messages.
  - `lgl/gtk_calendar.py` and `lgl/qt_calendar.py` add a "Release calendar…" window (day headings, Everything/Following/Anime/TV/Movies filter, Follow button, Refresh) opened from the app menu.
  - Extended `lgl/anime_calendar.py` (date-only entries, local timezone) and added `TmdbClient.get`.
  - Still to do: run the full test suite and ruff, look at the Qt render, try it with a real TMDB key and the live servers, release-day notifications for followed titles, and a Follow button on title pages.
- Why: `lgl/anime_calendar.py` already existed (0.6.6) but nothing fed it data, so the Tools → Release calendar page was always empty.

### 3.1 UI and quality of life
1. [x] Command Palette — quickly access pages, settings and actions with a keyboard shortcut.
   GTK 3: `lgl/gtk_palette.py`, `Ctrl+Shift+P` and the application menu. Run on a display
   2026-11-05: typing `libr` showed `Go to Library`, Enter switched the view and closed the
   palette (`shots-gtk/10-palette.png`). **Qt: not run**, there is no PySide6 here.
2. [ ] Custom Dashboard Layout — rearrange, resize or hide homepage sections.
3. [ ] Dynamic Artwork Backgrounds — use blurred poster artwork as page backgrounds.
4. [ ] Quick Preview — preview title information without opening its detail page.
5. [x] Compact Mode — fit more content on screen.
   GTK 3: the Interface switch adds `window.compact` (tighter paddings, denser rows) and
   shrinks covers from 180 to 140 px in the Library and 150 to 118 px in Browse. Run on a
   display 2026-11-05: covers went from 192 to 157 px wide on screen
   (`shots-gtk/09-interface-default.png` against `shots-gtk/11-interface-compact-violet.png`).
   **Qt: not run.**
6. [x] Custom Accent Colors — personalize the interface's color scheme.
   GTK 3: `theme.with_accent` retints the accent family, keeping white labels at AA on the
   filled shade and moving the text shade away from the page background. Run on a display
   2026-11-05: picking Violet turned the search ring and the active rail entry violet.
   **Qt: not run.**
7. [x] Smooth Page Transitions — add subtle animations between pages.
   GTK 3: the Interface choice sets the main stack to `crossfade`/140 ms (subtle),
   `slide-left-right`/260 ms (full) or none, and still respects the desktop's reduced-motion
   preference. Run on a display 2026-11-05: the stack reported `crossfade 140` and then
   `none 0` after choosing Off. **Qt: not run.**
8. [ ] Custom Home Sections — create homepage shelves using saved filters.
9. [ ] Recently Viewed History — quickly revisit previously opened titles.
10. [ ] Smart Search Suggestions — show suggestions, filters and matching titles while typing.

### 3.2 Movies, TV and anime
11. [ ] Random Discovery — pick a random movie, anime or series.
12. [ ] Smart Recommendations — recommend titles based on viewing preferences.
13. [ ] Trailer Autoplay Preview — preview trailers when selecting a title.
14. [ ] Advanced Content Filtering — filter by runtime, language, rating, year and genre.
15. [ ] Custom Collections — organize titles into personally created collections.
16. [ ] Anime Season Comparison — compare anime across different seasons.
17. [ ] Watch Progress Heatmap — visualize viewing activity over time.
18. [ ] Content Similarity Explorer — discover titles with similar themes and genres.
19. [ ] Hidden Gems Discovery — find lesser-known titles matching your interests.
20. [ ] Spoiler-Free Mode — hide episode descriptions and potentially revealing artwork.

### 3.3 Library and productivity
21. [ ] Unified Favorites Dashboard — browse favorite games, movies and anime together.
22. [ ] Personal Statistics Dashboard — view viewing time, completed series and favorite genres.
23. [ ] Profile Switching — maintain separate preferences and histories for different users.
24. [ ] Smart Duplicate Detection — find duplicate titles and conflicting metadata.
25. [ ] Metadata Editor — manually correct posters, descriptions, titles and release dates.
26. [ ] Library Tags — add custom labels to organize content.
27. [ ] Bulk Library Actions — edit, organize or remove multiple entries together.
28. [ ] Smart Content Suggestions — suggest titles based on incomplete collections.
29. [ ] Activity Timeline — see recently watched, added, updated and launched content.
30. [ ] Customizable Keyboard Controls — configure shortcuts for navigation and playback.

Note for whoever picks these up: since 0.6.6 several of them already exist as shared code in `lgl/` but only as settings in the Tools window, not as real screens (profiles, shortcuts, custom collections, home layout, release calendar, metadata cache, search filters). Check `lgl/tool_panels.py` and `docs/thing.md` before writing them again; what is missing for those is usually the visible UI. Not yet checked which of the 30 are fully done.

Delivery chosen by the owner: one branch per feature.

### 3.4 What the shared layer provides (added 2026-11-05, branch `feature/shared-ui-foundation`)

Ten new modules in `lgl/` now hold the logic behind the 30 items above, with 18 tests in
`tests/test_shared_features.py`, on top of the hand-off notes in 3.0 to 3.3. Items 1, 5, 6 and 7
above are ticked for the GTK frontend only, with the display run recorded next to each; the
remainder still need their visible screen, and the Qt frontend cannot be run in this sandbox
(no PySide6).
What exists today, item by item:

| # | Item | What is in `lgl/` now | Still missing |
|---|------|----------------------|---------------|
| 3.1.1 | Command Palette | `palette.py` plus `gtk_palette.py`: ranked command list, `Ctrl+Shift+P`, menu entry | the Qt palette window |
| 3.1.2 | Dashboard layout | existing `home_layout.py` (unchanged) | resize support; only order/show-hide today |
| 3.1.3 | Artwork backgrounds | `view_prefs.artwork_backgrounds` + Tools switch | drawing the blurred artwork |
| 3.1.4 | Quick preview | `view_prefs.quick_preview` + Tools switch | the popover itself |
| 3.1.5 | Compact mode | `view_prefs.compact`, Tools switch, `window.compact` CSS and smaller cover grids | the Qt side |
| 3.1.6 | Accent colours | validated accent, contrast-safe fill and text shades, live retint of every window | the Qt side |
| 3.1.7 | Page transitions | `off`/`subtle`/`full` mapped to the main stack, reduced-motion aware | the Qt side |
| 3.1.8 | Custom home sections | `shelves.py`: saved filter + limit, stored, filled and rendered as rows | drawing them on Home |
| 3.1.9 | Recently viewed | existing `media_history.py` covers media only | one history for games and media, plus the screen |
| 3.1.10 | Smart search suggestions | `suggestions.py`: prefix/word/substring/subsequence ranking, de-duplicated, completion | wiring it to the search entry |
| 3.2.11 | Random discovery | `discovery.random_pick` (seeded, empty-safe) | menu entry / button |
| 3.2.12 | Smart recommendations | `discovery.recommend` from liked titles, never recommends owned | the row on Home |
| 3.2.13 | Trailer autoplay | `trailers.py`: link detection (rejects non-http), YouTube vs local, autoplay off unless enabled | any player surface |
| 3.2.14 | Advanced filtering | `content_filters.py`: runtime, language, rating, year, include/exclude genres, junk-tolerant parsing, `describe` | Browse filter UI rebuilt on it |
| 3.2.15 | Custom collections | existing `collections.py` (unchanged) | the editor screen |
| 3.2.16 | Season comparison | `season_compare.py`: grouping, shared/only-first/only-second, summary | the compare window |
| 3.2.17 | Watch progress heatmap | `stats.heatmap` (7×N grid), `busiest_day` | drawing the grid |
| 3.2.18 | Similarity explorer | `discovery.similar` (Jaccard over genres/tags) | the explorer screen |
| 3.2.19 | Hidden gems | `discovery.hidden_gems` (popularity ceiling + rating floor) | the shelf |
| 3.2.20 | Spoiler-free mode | `view_prefs.spoiler_free` + Tools switch | hiding descriptions/artwork |
| 3.3.21 | Unified favourites | `stats.split` (games vs media buckets) | the dashboard |
| 3.3.22 | Statistics | `stats.totals` (events, days, streak), `favourite_genres`, `coverage` | the dashboard |
| 3.3.23 | Profile switching | existing `profiles.py` (unchanged) | visible switcher |
| 3.3.24 | Duplicate detection | `library_tools.duplicates` (normalised title + size) | conflicts view |
| 3.3.25 | Metadata editor | `library_tools.apply_edit` for title/description/year/poster, year validated | the dialog |
| 3.3.26 | Library tags | `library_tools.add_tag`/`remove_tag`/`tag_counts`, normalised and persisted | tag UI and filtering |
| 3.3.27 | Bulk actions | `library_tools.bulk_update` over chosen keys | multi-select UI |
| 3.3.28 | Collection suggestions | `discovery.suggestions` from uncovered genres | the shelf |
| 3.3.29 | Activity timeline | `stats.timeline` (newest first, junk timestamps dropped) | the screen |
| 3.3.30 | Keyboard controls | existing `shortcuts.py` (unchanged) | remapping dialog |

One visible change did land: `lgl/tool_panels.py` gained an **Interface** section (compact
layout, accent, transitions, artwork backgrounds, spoiler-free, quick preview), and because both
frontends render that panel, GTK and Qt both get the page without extra code. The GTK page was
opened and screenshotted under a display; the Qt page was not run.


## 4. Roadmap items still open (from the 0.6.6 notes in thing.md)
- [ ] Continue Watching shelf widget on the Movies & TV page (the data and rules exist in `media_lists.shelf`).
- [ ] Resume-from-position (cannot seek because the player is the system player).
- [ ] Screen-by-screen visual audit and pixel parity (needs a display or GPU).
- [ ] Test provider changes, window resizing and open/close cycles with a real player.
- [ ] Nobody has clicked through either desktop as a human.
- [ ] Merge `qt_library._launch` and `launch.plan_launch` into one implementation (only half done).
- [ ] Extend the Windows smoke test to open Media and Settings, not only stay running (only half done).
- [x] Add the headless GTK run as a CI smoke job. "Tests (Linux, GTK)" now installs `xvfb`/`xauth` and runs the suite under `xvfb-run`, so `tests/test_gtk_layout.py` stops skipping; before this the Linux job only imported the frontend, which is how the 0.6.6 crash shipped.

## 5. Docs
- [x] `docs/ROADMAP.md` is stale (says no automatic updater and no Steam/itch.io/GOG import). Check what is true now and update it. Rewritten 2026-11-05 from the code: the updater still never downloads or installs (consent, interval, checksum check, manual instructions only); Steam/Epic/GOG **are** supported as folders you point at, with no account and no launcher-database access, while itch.io is not supported at all; plus the plugin, sync, GTK-3-only and auto-launch limits.
- [ ] Keep this file and DONE.md current: tick things only when they were run.

## 6. Not checked at all
Windows build on real hardware, HiDPI, Wayland, video playback in the embedded player, the real archive.org catalogue and covers (the cloud sandbox had no network).

## How to run the GTK app headless (cloud sandbox)
- Packages (Ubuntu 24.04): `gir1.2-gtk-3.0 gir1.2-webkit2-4.1 python3-gi python3-gi-cairo adwaita-icon-theme xvfb xcompmgr imagemagick`.
- Use `/usr/bin/python3.12` (the system PyGObject is built for 3.12; the default python3 may be 3.13). `pip install --break-system-packages pytest` for that interpreter.
- `xvfb-run -a -s "-screen 0 1600x1100x24" bash -c "xcompmgr & sleep 1; python3.12 driver.py"`. Without `xcompmgr` you get black boxes that are not app bugs.
- Screenshot: `import -window root shot.png`. Use a throw-away `HOME` and XDG folders so real data is untouched.
- Qt tests: `QT_QPA_PLATFORM=offscreen python -m pytest tests -q`.
