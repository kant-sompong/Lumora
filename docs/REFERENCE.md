# Reference

Requirements, API keys, keyboard shortcuts, file locations and troubleshooting.

## Requirements

| | Windows | Linux |
| --- | --- | --- |
| OS | Windows 10 (1809+) or Windows 11, 64-bit | Any modern distribution |
| Runtime | None — the portable build is self-contained | Python 3.10+ and GTK 3 |
| Playback | Bundled Qt WebEngine | WebKit2GTK + GStreamer (optional) |
| Disk | ~500 MB unpacked | A few MB plus dependencies |

An internet connection is needed for the archive.org collection, artwork,
metadata and streaming.

## Configuration

Add keys in **Settings → Providers**. They are stored in the OS keyring and never
written to the catalogue, logs or error messages.

| Provider | Used for | Where to get a key |
| --- | --- | --- |
| TMDB | Movies & TV titles, posters, backdrops | <https://www.themoviedb.org/settings/api> |
| RAWG | Extra game metadata and covers | <https://rawg.io/apidocs> |
| MyAnimeList | Anime titles and rankings (Client ID only) | <https://myanimelist.net/apiconfig> |

Without a TMDB key the Media tab shows a short setup card instead of empty rows.

**Streaming providers** (Settings → Media, or the picker in the Media bar):

| Provider | Movies / TV | Anime | Notes |
| :--- | :---: | :---: | :--- |
| CineSrc | ✓ | | TMDB id |
| Rive | ✓ | ✓ | TMDB id, anime by TMDB |
| ToFlix | ✓ | ✓ | AniList id |
| CineLite | ✓ | ✓ | AniList id |
| frameXTV | ✓ | | TMDB id |
| Atlantic | ✓ | | TMDB id |
| ani.pm | | ✓ | AniList or MAL id, no key required |

## Keyboard shortcuts

| Shortcut | Action |
| --- | --- |
| `Ctrl+1` | Browse |
| `Ctrl+2` | Library |
| `Ctrl+3` | Downloads |
| `Ctrl+4` | Media |
| `Ctrl+F` / `Ctrl+K` | Focus the search field |
| `Ctrl+R` / `F5` | Reload the collection, rescan the library |
| `Ctrl+,` | Settings |
| `Escape` | Dismiss a notice or clear the search |

## Where data is stored

| Data | Windows | Linux |
| --- | --- | --- |
| Settings | `%APPDATA%\Lumora\config.json` | `$XDG_CONFIG_HOME/lumora/config.json` |
| Library index | `%LOCALAPPDATA%\Lumora\library.json` | `$XDG_DATA_HOME/lumora/library.json` |
| Download queue | `%LOCALAPPDATA%\Lumora\downloads.json` | `$XDG_DATA_HOME/lumora/downloads.json` |
| Cache | `%LOCALAPPDATA%\Lumora\Cache\` | `$XDG_CACHE_HOME/lumora/` |
| Downloads | Your chosen folder | Your chosen folder |

Until 0.6.0 these folders were called `NativeGameLibrary` (Windows) and
`linux-game-library` (Linux). The first launch after the upgrade copies the
settings, library, queue and catalogue documents into the new folders and
leaves the old ones untouched, so an older build still works and rolling back
is simply deleting the new folders. The image cache is not copied: it is
regenerable artwork and is rebuilt on demand.

## Safety & limits

- **Nothing runs automatically** — every launch passes through a review dialog
- **No elevated launches** — root/administrator starts are refused
- **Path confinement** — launches stay inside the chosen download folder
- **HTTPS and host allow-lists** for provider URLs
- **No secrets in logs** — API keys are redacted everywhere
- **Polite to the sources** — bounded request rates and one transfer at a time

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `ImportError: cannot import name … from gi.repository` | Install GTK 3 + PyGObject, or use the Tk fallback |
| Blank window on Linux | Check `DISPLAY` / `WAYLAND_DISPLAY`; try `GDK_BACKEND=wayland ./run.sh` |
| "Could not reach archive.org" | Test `curl -I https://archive.org/metadata/native-linux-games-collection`; a stale cache is used when available |
| HTTP 429 / slow metadata | Wait and press **Refresh**; request rates are bounded on purpose |
| Media tab shows a setup card | Add a free TMDB key in Settings → Providers |
| Playback opens in a browser | Running from source without Qt WebEngine — install `PySide6` **and** `PySide6-WebEngine`, or use the released ZIP |
