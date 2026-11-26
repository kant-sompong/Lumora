# Lumora 0.6.25

A patch release for Linux: the Media tab strip that 0.6.24 introduced is now
actually drawn under the header.

## Fixed

- **The media tab strip is visible on Linux.** The strip was created hidden so
  the empty bar would not take space at launch, but the widget that scrolls the
  tabs was never shown when the first tab opened: the bar reported itself
  visible and allocated a single pixel of height, so open titles had no tab to
  click and no way back to a previous title. Showing the strip's scroller with
  the bar fixes it; closing every tab still hides the bar.
- **Windows was not affected.** The Qt tab bar has a fixed height and does not
  use GTK's show-all rule, so the strip was already drawn there.

## Install

**Windows** — download `Lumora-Setup-x64.exe` (per-user installer, no
administrator prompt) or the portable `Lumora-Windows-x64.zip` and run
`Lumora.exe` from the unzipped folder. The build is self-contained and bundles
Qt WebEngine for playback. The executables are unsigned, so SmartScreen may
warn: choose **More info → Run anyway**.

**Linux** — download `Lumora-0.6.25-linux-x86_64.tar.gz`, extract it and run
`./run.sh`. It needs Python 3.10+, GTK 3 and PyGObject; WebKit2GTK and the
GStreamer plugins are optional and only used for embedded playback.
`packaging/install-desktop.sh` adds a desktop entry and icon.

Settings and the catalogue live in `$XDG_CONFIG_HOME/lumora` (default
`~/.config/lumora`) on Linux and `%APPDATA%\Lumora` on Windows. Verifying anime
needs a MyAnimeList Client ID: set `MAL_CLIENT_ID` in the environment, or add it
in Settings → Providers. Without a key, anime titles are unverified and play on
your selected provider.
