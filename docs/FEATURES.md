# Features

A detailed guide to Lumora's current features. For the short project overview,
see the [README](../README.md).

```text
Browse → Download → Library → Play
                 ↘ Movies & TV → Discover → Stream
```

## Browse and game catalogues

- Browse the Internet Archive games collection, indexed from
  `archive.org/advancedsearch.php` and cached for six hours with a stale-cache
  fallback when offline
- Search by title, identifier, year, creator or subject
- Filter by genre, size, release year, licence and file format, with live counts
- Sort by title, year, size, downloads, creator or genre, ascending or descending
- Switch between a cover grid and compact list; artwork loads lazily as you scroll
- Open a details panel with the selected game's file list and download action
- Import supported website catalogues, including Kryoto, RAWG, archive.org and
  compatible JSON/HTML catalogue pages; imported games appear alongside the
  archive collection

## Downloads

- Persistent download queue that survives restarts, with pause, resume, cancel,
  retry and remove actions
- Segmented HTTP transfers using Range requests, with automatic resume
- Disk-space preflight, temporary `.part` files, atomic final renames and
  bounded retries
- Magnet links handled by the built-in BitTorrent engine or an external client
  such as `aria2c`

## Game library and launch safety

- Scan the download folder into `library.json`, with a manifest for each game
- Track favourites and downloaded, installed, incomplete or missing status
- Edit metadata such as developer, publisher, release date, genres, platforms
  and notes; metadata records its provenance so catalogue refreshes do not
  overwrite your own edits
- Detect platforms from files on disk where possible
- Verify files against published archive.org checksums
- Find duplicates, export/import library data and forget missing entries
- Remove a game from the library without deleting its downloaded files
- Reviewed launch: choose the executable yourself; launch paths must remain
  inside the download folder, and elevated launches are refused

## Movies, TV and anime

- Dedicated Movies, TV Shows and Anime areas, with Search, My List and History
- TMDB metadata for movies and TV, MyAnimeList ranking data for anime, and
  ani.pm catalogue lookup for titles not found through the other anime sources
- Episode selection with a season picker and episode rows showing episode
  numbers, titles and air dates
- Provider selection in the Media bar, saved per library and switched
  automatically for anime
- Embedded playback for supported providers: Qt WebEngine on Windows and
  WebKit2GTK on Linux; fullscreen is controlled from the provider player
- Trailer lookup and preview where a trailer is available and the platform's
  embedded browser supports it

### Media home and feed

The GTK media home includes a cinematic hero and a set of poster shelves:

- Continue Watching
- Trending
- Movies
- TV
- For You
- Hidden Gems
- More Like This

The feed also includes a three-column discovery area and a release schedule
strip beneath the hero. Rows are populated from watch history, TMDB discovery,
recommendations, similarity results, hidden-gem data and release-feed data.
Some sections depend on configured services or available history, so they may
be empty until data is available.

## Discovery and personalisation

- Random discovery and recommendations based on liked titles
- Similar-title exploration and hidden-gem discovery
- Suggestions for titles that may complete an incomplete collection
- Saved home shelves built from filters, with configurable names, genres and
  item limits
- Recently viewed history for returning to titles
- Spoiler-free viewing options for episode titles
- Season comparison to group and compare episodes across seasons
- Release calendar for upcoming anime and TV episodes, with notifications for
  followed titles when supported data is available

## Library tools

- Collections: create, rename and delete collections, and add or remove titles
- Tags and metadata editing for organising a personal library
- Duplicate detection and bulk metadata updates
- Statistics, including activity summaries, a watch/activity heatmap and
  timeline views
- Profile separation for library and settings data; switching profiles takes
  effect on the next launch

## Interface and controls

- Command palette, opened with `Ctrl+Shift+P`, for finding pages and actions
- Keyboard shortcuts, with an editor for supported bindings
- Compact mode, accent-colour presets and page transitions
- Light, dark and system themes, with theme changes applied live
- Artwork backgrounds and configurable home layouts
- Home sections can be ordered, hidden, resized and reset; custom shelves can
  be added to Browse
- Quick preview on hover in the GTK interface

## Tools and settings

- A shared Tools area for managing providers, downloads, notifications, home
  screen options, profiles, local files and game sources
- Release calendar, watch-history sync, plugin management, updates, backup and
  support/diagnostic tools
- Settings for General, Locations, Providers, Media and Shortcuts
- API keys stored in the operating system keyring (libsecret on Linux and
  Credential Manager on Windows)

## Platform notes

Lumora uses a native GTK 3 frontend on Linux and a Qt 6 / PySide6 frontend on
Windows, backed by shared Python logic. A small number of screens and interface
details are frontend-specific; for example, hover quick preview is available in
the GTK interface. Embedded playback also depends on the relevant WebKit2GTK
or Qt WebEngine runtime and on the selected provider supporting in-app playback.
