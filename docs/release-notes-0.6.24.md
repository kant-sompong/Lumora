# Lumora 0.6.24

Media tabs arrive on both frontends: a title you open stays in its own tab with its own page, its own place and its own playback, and it survives moving around the app. A MAL-verified anime also starts on ani.pm whatever provider you saved, and switching providers is a decision for that play only.

## Added

- **Browser-style media tabs (Linux and Windows).** Opening a movie, TV show or anime puts it in a tab in the strip under the header, labelled with the title and a small `S2 · EP5` / `Movie` / `S1 · EP12` subtitle. Opening a title that is already open selects its tab instead of adding a duplicate, and every tab has its own `×`.
- **A tab is a session, not a bookmark.** Each tab remembers its own details page, fetched seasons, scroll position, the season and episode it was on and its Next episode, so coming back to it does not rebuild the page or refetch the catalogue.
- **Switching tabs pauses instead of dropping.** The tab you leave keeps its stream, provider and last known position; the tab you return to comes back as a paused panel with **Resume** and where it stopped. Nothing starts playing again on its own.
- **The home hero plays in its own tab.** Starting the hero from the Media home opens the hero's own tab (or brings its paused stream back) rather than playing over the tab in front.
- **Ani.pm first for a verified anime (both frontends).** A title MAL verifies as anime is attempted on ani.pm first, whatever anime provider is saved. The saved provider only orders the alternatives kept behind ani.pm.
- **Provider switching for one session.** While a verified anime plays, the existing provider controls — GTK's **Try another provider** and header provider menu, Qt's provider menu — replay the same title and the same season/episode on the picked provider. The saved anime provider preference is never rewritten.

## Changed

- **ani.pm is a player, not a catalogue.** Search asks TMDB (and MyAnimeList on Windows); ani.pm is reached only after MAL verification, and it is no longer advertised as a search source anywhere in the UI or the docs.
- **A required provider failing is an error, not a fallback.** A verified anime whose AniList entry cannot be resolved, or whose ani.pm URL cannot be built, stops with an explanation and an explicit choice of the other providers instead of quietly opening one of them.

## Fixed

- **A tab keeps its verified session through navigation (Linux).** Back to the details page, opening another title, moving around the Media pages and leaving Media all keep the tab's verified anime session saved with it; restoring the tab or pressing **Resume** brings back the provider badge, the anime-only provider menu and the mapped AniList episode.
- **Only a genuine end clears that session.** A play that replaces it, a failed play or an error screen, or closing the tab that owns it are the only teardowns, so a cleared session can never be resurrected by a later tab switch.
- **The provider badge and menu follow the tab in front.** An open title's details or player offers that tab's own providers, so an anime tab keeps anime providers while the rail says Home, and a movie/TV tab offers movie/TV providers after an anime tab. Home, Browse and Search keep their own section.
- **Closing the last tab clears the stale session and provider state**, instead of leaving a badge and menu describing a tab that no longer exists.
- **Leaving Media on Windows stops playback.** Audio no longer continues behind Browse, Library, Downloads or Settings, and returning to Media shows the same tab paused rather than a second player.
- **Windows no longer changes page when a tab closes in the background.** Closing the last tab from Browse stays on Browse, and closing a tab whose page is not in front does not activate a successor.
- **Reopening an open title keeps its session (Linux).** The tab is restored from its own saved state instead of being rebuilt, because a play that belongs to another title is never captured into the tab in front.
- **A late position answer can no longer overwrite a tab.** The player's position is ignored when it arrives from a view that has since been switched away from, closed, torn down or superseded.
- **Back or Forward out of Media stops playback** rather than leaving the player running behind the previous page.

## Install

**Windows** — download `Lumora-Setup-x64.exe` (per-user installer, no administrator prompt) or the portable `Lumora-Windows-x64.zip` and run `Lumora.exe` from the unzipped folder. The build is self-contained and bundles Qt WebEngine for playback. The executables are unsigned, so SmartScreen may warn: choose **More info → Run anyway**.

**Linux** — download `Lumora-0.6.24-linux-x86_64.tar.gz`, extract it and run `./run.sh`. It needs Python 3.10+, GTK 3 and PyGObject; WebKit2GTK and the GStreamer plugins are optional and only used for embedded playback. `packaging/install-desktop.sh` adds a desktop entry and icon.

Settings and the catalogue live in `$XDG_CONFIG_HOME/lumora` (default `~/.config/lumora`) on Linux and `%APPDATA%\Lumora` on Windows. Verifying anime needs a MyAnimeList Client ID: set `MAL_CLIENT_ID` in the environment, or add it in Settings → Providers. Without a key, anime titles are unverified and play on your selected provider.

## Known issues

- **Media tabs are session-only.** They live until the app closes and are not written to disk, so a restart starts with no tabs.
- **Playback position depends on the provider's player.** It is read from the page's video element; a provider that plays inside a cross-origin iframe resumes without an offset even though the URL keeps its start hint.
- **On Linux the tab strip lives inside the Movies & TV page**, so it is not drawn while Library, Downloads or Settings is the visible view. The tabs themselves and their paused streams survive that navigation. The Windows strip is window-level and stays visible everywhere.
- **Windows anime cards still play episode 1** with no season or episode picker, so a provider switch there preserves the title and the resolved AniList entry only.
- **The Linux video freeze after a pause or a 10-second skip is still open.**
- **ani.pm hard-sub and hard-dub are not integrated.** Only the `sub` and `dub` tracks can be built from an AniList id and episode number; a hard-sub or hard-dub track can be unavailable even when the title has one on the site.
- Streaming providers are third-party services and change without notice; ani.pm and TMDB outages are reported, not worked around.
