# Lumora 0.6.27

A patch release for fullscreen playback. The media tab strip is now hidden while
a player is fullscreen, so the video gets the whole screen, and it comes back as
soon as fullscreen ends.

## Fixed

### Fullscreen and media tabs

- **The media tab strip no longer sits above a fullscreen player.** 0.6.26 kept
  the strip in place during fullscreen playback so another open title stayed one
  click away, but it also meant the strip stayed drawn over the video. The strip
  is now hidden whenever fullscreen is active — on both the GTK and the Qt
  frontend — and the player gets the whole screen the way the other window
  chrome already did.
- **The strip comes back when fullscreen ends.** Leaving fullscreen with Escape,
  by closing the player or by switching to a tab that has no stream restores the
  strip immediately, with the tabs it had before. A tab that has no tabs left to
  show still leaves the strip hidden.
- **A tab switch cannot bring the strip back mid-fullscreen.** Switching between
  open titles while fullscreen re-renders the tab list; that refresh now leaves
  the strip hidden for as long as the window stays fullscreen and restores it
  when the session ends. Switching between tabs still keeps the window
  fullscreen and each tab's saved playback position, as it did in 0.6.26.

## Install

**Windows** — download `Lumora-Setup-x64.exe` (per-user installer, no
administrator prompt) or the portable `Lumora-Windows-x64.zip` and run
`Lumora.exe` from the unzipped folder. The build is self-contained and bundles
Qt WebEngine for playback. The executables are unsigned, so SmartScreen may
warn: choose **More info → Run anyway**.

**Linux** — download `Lumora-0.6.27-linux-x86_64.tar.gz`, extract it and run
`./run.sh`. It needs Python 3.10+, GTK 3 and PyGObject; WebKit2GTK and the
GStreamer plugins are optional and only used for embedded playback.
`packaging/install-desktop.sh` adds a desktop entry and icon.

Settings and the catalogue live in `$XDG_CONFIG_HOME/lumora` (default
`~/.config/lumora`) on Linux and `%APPDATA%\Lumora` on Windows. Verifying anime
needs a MyAnimeList Client ID: set `MAL_CLIENT_ID` in the environment, or add it
in Settings → Providers. Without a key, anime titles are unverified and play on
your selected provider.
