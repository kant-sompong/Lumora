# Features

Detailed reference for what Lumora does. For a five-line summary, see the
[README](../README.md).

```text
Browse  →  Download  →  Library  →  Play
                     ↘  Movies & TV  →  Stream
```

## Browse and discovery

- Collection index from archive.org's `advancedsearch.php`, cached for six hours
  with a stale-cache fallback when offline
- Search by title, identifier, year, creator or subject
- Filters for genre, size, release year, licence and file format, with live counts
- Sort by title, year, size, downloads, creator or genre, ascending or descending
- Cover grid or compact list, with artwork loaded lazily as you scroll
- Details panel with the file list and one-click download
- **Website sources and catalogues** — import any catalogue page the shipped
  adapters can verify (archive.org, Kryoto, RAWG, JSON/HTML), straight into the
  local catalogue; imported games appear in Browse next to the archive collection

## Local library

- Scans the download folder into `library.json` with a manifest per game
- Favourites, status badges (downloaded / installed / incomplete / missing)
- Metadata with provenance: developer, publisher, release date, genres,
  platforms, notes — catalogue refreshes never overwrite what you typed
- Best-effort platform detection from the files on disk
- Integrity verification against published archive.org checksums
- Duplicate finder, export/import, forget-missing, and a confirmed
  **Remove from library** action that leaves your files untouched
- Reviewed launch: you choose the executable, the path stays inside the download
  folder, and elevated launches are refused

## Downloads

- Persistent queue that survives restarts — pause, resume, cancel, retry, remove
- Segmented HTTP transfers with Range requests and automatic resume
- Disk-space preflight, atomic `.part` → final rename, bounded retries
- Magnet links through a built-in BitTorrent engine, or an external client such
  as `aria2c` if you prefer one

## Movies & TV

- Cinematic home with Continue Watching and Trending carousels
- Movies, TV Shows and Anime sections, plus Search, My List and History
- TMDB metadata; MyAnimeList ranking for anime; ani.pm catalogue for titles
  missing from both
- **Episode selector** with a season picker and episode rows (number, title,
  air date)
- **Provider picker** in the Media bar, saved per library and switched
  automatically for anime
- **Embedded playback** in the app — Qt WebEngine (Chromium) on Windows,
  WebKit2GTK on Linux — with fullscreen from the provider page

## Settings

- General, Locations, Providers, Media and Shortcuts tabs
- Light, dark and system themes, applied live without a restart
- API keys stored in the OS keyring (libsecret / Credential Manager)
