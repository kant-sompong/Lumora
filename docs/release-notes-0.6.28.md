# Lumora 0.6.28

Lumora comes to Android. The Movies & TV app that has shipped for Linux and
Windows now runs on a phone, with a player built for landscape and settings that
fit a small screen. Every platform gains a searchable release calendar, the
anime provider moves to its new anipm.pk domain, and the anime player is now
framed so it plays instead of redirecting.

## New

### Lumora for Android

- **The phone app, Movies & TV only.** One tab, the same catalogue as the
  desktop's Media pages: a home page of shelves (Trending Now, Movies, TV Shows
  and Anime), title pages, the player, the episode calendar and Settings. The
  desktop's Library and Downloads pages are not included.
- **Search is TMDB's.** Results come from TMDB alone, one row per series rather
  than one per matching episode, and a title TMDB flags as adult carries a small
  **18+** pill on its poster.
- **Title pages show the title's own logo.** TMDB's image set provides the logo
  above the details, falling back to the plain title while it loads or when TMDB
  has none, and the backdrop, poster, score, genres and overview come with it.
- **A player built for the phone.** Opening a stream turns the phone to
  landscape and hides the system bars. A panel on the left edge is the player's
  only chrome — back, what is playing, the provider it is playing from, the
  switch list and Reload — and nothing is drawn over the video until the panel
  is asked for. The edge itself is tappable, and fullscreen video from the
  provider's own button gets the whole screen.
- **Provider health, and the fastest provider first.** The Providers settings
  check every enabled provider, cache the answer, show the measured status and
  time, and order the list fastest first. "Use fastest" makes the quickest
  reachable provider the default; unreachable providers go last instead of
  disappearing.
- **Anime is identified through MAL.** A MyAnimeList match is the proof of an
  anime title, resolved to its AniList id for episode data; a verified anime
  title plays on the anime provider (ani, anipm.pk) first and then the owner's
  order, whether it was found as a movie, a TV show or anime.
- **Settings in two tabs.** General holds the theme, the accent colour, a
  compact layout, a **UI size** slider that scales text, buttons and spacing
  (with a reset to 100%) and the version; Providers holds the order, the
  enable switches and the health check.

### Release calendar

- **Both desktop calendars have a search field.** The GTK and Qt calendars can
  be searched for a series instead of only showing the days around today, and a
  result lists the next episodes it can confirm.
- **The phone gets the same calendar.** A Calendar page opened from the Movies &
  TV app bar shows this week's releases for the titles on screen and searches
  the same sources with the same rules.
- **Season posters and episode previews.** A season now carries its own TMDB
  poster, and each episode row can show a still, its title and its description —
  AniList streaming thumbnails for anime, and a plain numbered row when the
  artwork request fails, so playback never waits on art.

### Anime provider

- **The provider moved to anipm.pk.** Links, the host check and the provider
  label now use the new domain; the retired ani.pm is no longer accepted.
- **The anime embed is framed, so it plays.** anipm.pk redirects an embed opened
  as a page to the title's page, so both desktop players now load a small
  wrapper document that holds the embed in a full-screen frame (the phone does
  the same). Only the anime provider is framed, and only for a URL that passes
  the existing provider host check; every other provider still loads directly.

## Changed

### App icon

- **The Lumora mark on every platform.** Windows and Linux icons were
  regenerated from the transparent branding artwork (the ICO at every size, the
  Linux PNGs plus a scalable SVG), Android gets adaptive icons on the same
  background, and the desktop entry, the GTK window icon and the About dialog
  all point at the installed `io.github.lumora.Lumora` icon.

## Known limits

- **Windows anime tabs resume from the start.** With the framed player the tab's
  position sampler no longer sees the video element, so reopening an anime tab
  resumes at the beginning. Nothing errors; the same tabs on Linux and the phone
  behave as before.
- **The Android build is signed with a debug key.** It is installed by
  sideloading the APK, not through a store: Android will ask to allow installs
  from that source, Play Protect may warn, and it cannot be updated through the
  Play Store.
- **MAL verification still needs a Client ID** for anime identification, as on
  the desktop: set `MAL_CLIENT_ID` or add it in Settings → Providers. Without a
  key, anime titles are unverified and play on the selected provider.

## Install

**Windows** — download `Lumora-Setup-x64.exe` (per-user installer, no
administrator prompt) or the portable `Lumora-Windows-x64.zip` and run
`Lumora.exe` from the unzipped folder. The build is self-contained and bundles
Qt WebEngine for playback. The executables are unsigned, so SmartScreen may
warn: choose **More info → Run anyway**.

**Linux** — download `Lumora-0.6.28-linux-x86_64.tar.gz`, extract it and run
`./run.sh`. It needs Python 3.10+, GTK 3 and PyGObject; WebKit2GTK and the
GStreamer plugins are optional and only used for embedded playback.
`packaging/install-desktop.sh` adds a desktop entry and icon.

**Android** — download `Lumora-0.6.28-android.apk` and open it on the phone,
allowing installs from your browser or file manager when Android asks. The APK
is signed with a debug key, so Play Protect
may ask you to confirm the install, and updates have to be sideloaded the same
way.

Settings and the catalogue live in `$XDG_CONFIG_HOME/lumora` (default
`~/.config/lumora`) on Linux and `%APPDATA%\Lumora` on Windows. Verifying anime
needs a MyAnimeList Client ID: set `MAL_CLIENT_ID` in the environment, or add it
in Settings → Providers. Without a key, anime titles are unverified and play on
your selected provider.
