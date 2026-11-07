# Lumora 0.6.8

This release is the big one: the thirty features that were planned for Lumora are now in both
apps — the GTK 3 app on Linux and the Qt 6 app on Windows. Same core, same colours, same
features, two native frontends.

Everything below is in both desktops unless the line says otherwise.

## Find things faster

- **Command palette** (`Ctrl+Shift+P`) — one search box for every page, setting and action. Type
  three letters and press Enter.
- **Keyboard shortcut editor** — rebind the actions the window installs. Empty or duplicate
  bindings are refused; your changes apply the next time the window opens.
- **Search suggestions** — the search field suggests titles while you type, with one-key
  completion.
- **Advanced Browse filters** — filter by runtime, language, rating, year and genre, with a
  readable summary of what is active and a single "Reset all filters".

## Make it look the way you like

- **Compact mode** — tighter rows and smaller covers, for libraries with hundreds of titles.
- **Accent colours** — six presets plus a custom colour. Lumora keeps button and link text
  readable on every accent, in both the light and the dark theme.
- **Page transitions** — subtle cross-fade or full slide between pages, and they stay off when
  your desktop asks for reduced motion.
- **Artwork backgrounds** — the current title's artwork as a faded backdrop.
- **Home shelves and layout** — show, hide, reorder and resize the home sections, and add your
  own shelves ("Cyberpunk, top 12" and so on, up to eight).

## Discover something to watch

- **Recommendations** built from the titles you rated highly.
- **More like this** — titles that share genres and tags with the one you picked.
- **Hidden gems** — well rated but not widely known. Set a minimum score and a maximum
  popularity; both are remembered, and anime comes from AniList with live scores.
- **Complete the set** — fills in the gaps in genres your library is still missing.
- **Release calendar** — what airs this week, with real air dates from AniList for anime and
  TMDB for films and series (when you have a TMDB key). Follow a title and get a notification
  on release day.
- **Season comparison** — two anime seasons side by side: what only one has, and what carried
  over. The summary says it plainly, e.g. "Winter 2024: 2 titles, Spring 2024: 2 titles, 1 in
  both".
- **Trailer preview** — a Trailer button on the details panel plays the YouTube trailer in the
  embedded player, muted unless you turn sound on. If a lookup fails you now see "Trailer not
  available" instead of nothing.
- **Quick preview** (Linux) — hover a cover in Browse for a popover with the details; Escape
  closes it.

## Watch it in the app

- **Movies & TV** with TMDB metadata, anime via MyAnimeList/ani.pm, an episode selector and a
  provider picker.
- **The Windows player now lives inside the main window**, with real fullscreen and a proper exit
  from it, instead of a separate window.

## Organise your library

- **Favourites** on the home screen, with games beside films, series and anime.
- **Statistics** — totals, a watch heatmap, a streak, your favourite genres and metadata
  coverage.
- **Activity timeline** — what you added, watched and changed, newest first.
- **Tags** and per-field metadata edits, with the provenance of every field kept.
- **Bulk actions** — change several titles at once, with one level of undo.
- **Duplicate finder** — groups titles that look like the same thing in more than one place.
- **Collections** — named sets you build by hand; collections are separate from tags.
- **Profiles** — each profile has its own library, settings, tags, collections and history.
  Switching profiles is saved and takes effect after a **restart**, which the profile window
  says plainly.
- **Spoiler-free mode** — hides episode titles until you ask for them.

## Under the hood

- One launcher for both desktops: `launch.plan_launch` handles Windows `.exe` files, `.bat` and
  `.cmd` through COMSPEC, and the root check no longer fails where `geteuid` does not exist.
- `Lumora.exe --self-test` opens Browse, Library, Downloads, Media and the Settings sheet and
  exits non-zero on any exception; CI runs it before the "stays running" check, so a broken
  Windows build fails the build instead of shipping.
- The Linux CI job now installs Xvfb and runs the whole GTK test suite under `xvfb-run`, so the
  layout tests actually run. Before this the Linux job only imported the frontend, which is how
  a broken Tools window reached 0.6.6.

## Known limits

These are the gaps recorded in the project's own notes. Nothing here was hidden:

- Nobody has clicked through either desktop as a human. Everything is tested and rendered
  offscreen or headless; "works" means "built, tested and looked at in a sandbox".
- The Windows build has never run on real Windows hardware, and the Qt app was only ever
  rendered offscreen with Qt's offscreen platform — no real QtWebEngine.
- The embedded player, trailers and artwork backgrounds have not been exercised with real
  WebEngine or WebKit, and no trailer has ever actually played in the sandbox (no network).
- On some Linux setups the video can freeze after a pause or a 10-second skip. It is suspected
  to be WebKitGTK's GPU rendering; `WEBKIT_DISABLE_DMABUF_RENDERER=1` is worth a try. No fix has
  been made.
- Artwork backgrounds were checked only with generated images.
- Home layout supports order, visibility and size, but not free placement of shelves.
- Quick preview exists on Linux only; there is no Qt version.
- Collections membership is manual — titles are added and removed by hand.
- Season comparison matches by AniList series id, so a sequel with a new id counts as new.
- Favourites for films, series and anime are shown on the dashboard but not stored anywhere yet.
- No viewing time is recorded, so statistics show counts and streaks, not hours.
- Profile switching and keyboard shortcut changes take effect on the next start, not live.
- Bulk edits keep one level of undo, not a full history.
- The Windows Browse filter column has never been looked at on a display.
- HiDPI, Wayland and window resizing have not been checked on real hardware.
- The archive.org catalogue and covers were not tested against the live service in the build
  sandbox (no network); the release calendar's AniList source was run live, TMDB sources need
  your own key.
