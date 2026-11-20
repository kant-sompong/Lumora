# Lumora 0.6.26

A patch release for the Media tabs and the episode list. Fullscreen playback no
longer takes the tab strip with it, switching, closing or leaving a tab gives
the window back, and an episode row is marked correctly even when its season was
loaded after the day's first check.

## Fixed

### Fullscreen and media tabs

- **The media tab strip stays on screen during fullscreen playback.** Fullscreen
  handed the whole screen to the video and hid every sibling on the way up to
  the window, the tab strip included, so another open title could only be
  reached by leaving fullscreen first. The strip is now the one piece of chrome
  a fullscreen player leaves in place, on both frontends.
- **Switching tabs keeps the window fullscreen.** A switch used to drop out of
  fullscreen: on GTK stopping the outgoing player unfullscreened the window, and
  on Qt suspending the tab ended the player's fullscreen. A switch to a tab that
  still has its stream now stays fullscreen and keeps that tab's saved position.
- **A tab with no stream gives the screen back.** Switching to a details-only
  tab left the window fullscreen with no video on screen and no player to catch
  Escape. It now returns to the normal layout, the way an unopened title already
  did.
- **A paused tab restored in fullscreen shows its panel.** The page chrome was
  built after fullscreen was applied, so a tab resumed from the paused panel
  kept its title, badges and notes behind the video, and a paused tab's own bar
  stayed hidden after fullscreen ended. Both are handled now.
- **Qt's fullscreen is one session for the window, not one player's.** Whichever
  tab ends it puts the window state back exactly once, even when the tab that
  follows is only paused or when the tab that started the session is closed
  while backgrounded. A closed tab's player is forgotten before its page is
  deleted, so Escape can no longer touch a widget that is already gone and
  leave the window stuck fullscreen.
- **Leaving Media ends fullscreen and stops the video.** The other pages are not
  the video, so moving to Browse, Library, Downloads or Settings brings the
  application chrome back instead of leaving the window fullscreen.

### Episode air dates

- **Episode rows are marked on the day they appear, not only after a date
  change.** The page skipped its refresh whenever the day it had recorded still
  matched the clock, so a season loaded later the same day kept the answer its
  rows computed when they were built. The minute check now re-reads every row
  the page owns — a handful of property reads — so a row added after the last
  check is marked on the same day as well as after a rollover. The date-rollover
  test no longer depends on the real clock, which is why it could pass on Linux
  and fail on Windows.

## Install

**Windows** — download `Lumora-Setup-x64.exe` (per-user installer, no
administrator prompt) or the portable `Lumora-Windows-x64.zip` and run
`Lumora.exe` from the unzipped folder. The build is self-contained and bundles
Qt WebEngine for playback. The executables are unsigned, so SmartScreen may
warn: choose **More info → Run anyway**.

**Linux** — download `Lumora-0.6.26-linux-x86_64.tar.gz`, extract it and run
`./run.sh`. It needs Python 3.10+, GTK 3 and PyGObject; WebKit2GTK and the
GStreamer plugins are optional and only used for embedded playback.
`packaging/install-desktop.sh` adds a desktop entry and icon.

Settings and the catalogue live in `$XDG_CONFIG_HOME/lumora` (default
`~/.config/lumora`) on Linux and `%APPDATA%\Lumora` on Windows. Verifying anime
needs a MyAnimeList Client ID: set `MAL_CLIENT_ID` in the environment, or add it
in Settings → Providers. Without a key, anime titles are unverified and play on
your selected provider.
