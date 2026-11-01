# Native Game Library

A small desktop application for browsing the Internet Archive's public
**native-linux-games-collection**, downloading individual files into an
organized local library, and keeping track of what you have.

Two native frontends share one platform-neutral core:

- **Linux** — GTK 3, running natively on Wayland or X11.
- **Windows 10/11** — Tkinter, shipped as a standalone executable that does not
  require Python.

Nothing is executed automatically. Downloads never overwrite existing files,
launching requires explicit review and confirmation, and the app refuses to run
games with administrator/root privileges.

> **Scope note:** the collection is a public, user-uploaded archive. Most game
> payloads are third-party self-extracting `.sh` installers (LinuxRuleZ!, GOG
> rips, Steam rips, etc.), not verified native binaries. This application treats
> them as untrusted files: it identifies likely installers/executables, explains
> what platform they probably target, and asks for explicit review before
> anything is run.

---

## Platform support

| Platform | Status |
| --- | --- |
| Linux (Void, and other GTK 3 distributions) | Developed and tested on Void Linux / Wayland |
| Windows 11 | Frontend implemented; build produced by Windows CI. **Not yet verified on a Windows machine by the author.** |
| Windows 10 | Same as above. Windows 10 is past standard support; see [Windows notes](#windows-notes) |
| macOS | Not supported; the platform adapter has paths, but no frontend is built or tested |

Do not read "implemented" as "verified". The Windows executable is built on
`windows-latest` in CI and smoke-tested there (it must start and stay running);
interactive behavior on a real Windows desktop still needs manual testing.

---

## Features

### Browsing and metadata

- Collection index from archive.org's public `advancedsearch.php` (471 items at
  the time of writing), cached for six hours with a stale-cache fallback when
  offline.
- Search by title, identifier, year, creator, and subject.
- Details from the public `metadata/{identifier}` endpoint: title, creator,
  date, subjects, license/rights link, plain-text description, total size.
- Poster/cover image with disk caching and a quiet placeholder when an item has
  none.
- Link to the original archive.org item page.

### Downloads

- Multi-file selection into `<chosen folder>/<identifier>/`.
- One active download at a time, on purpose: polite to the archive and
  deterministic for pause/resume.
- Live progress, transferred/total, speed, ETA, status, and error text.
- Pause, resume, cancel, retry, remove, clear-finished, resume-all.
- **Persistent queue:** task state is written to the per-user data directory, so
  unfinished downloads survive a crash, reboot, or app restart. Restored tasks
  stay paused until you resume them.
- Resume via HTTP `Range` from a `.part` file, with strict validation: a
  misaligned `Content-Range`, a server that ignores `Range`, or a short transfer
  restarts cleanly instead of corrupting the partial file.
- Free-space preflight (remaining bytes plus a 64 MiB reserve) with a clear
  message instead of a failed write.
- Expired/blocked URLs (403/404) are re-resolved from current archive metadata
  once before the task is marked failed.
- Existing final files are never overwritten; completed size is verified before
  the `.part` is renamed into place.
- Duplicate protection: a file path, or the same URL for the same item, cannot
  be queued twice while a task for it is still active. Cancelled and failed
  tasks do not block a fresh attempt.
- Manual retries are bounded (default three, configurable 0–20). The retry
  button disables itself and the row says “attempt N of M” once the limit is
  reached. Automatic backoff inside one attempt is separate and unchanged.
- The connect timeout is a setting (5–300 s) applied to new requests.
- Desktop notifications on completion/failure on Linux (when `notify-send`
  exists). Windows uses the in-app status; no extra dependency is added.

### Local library

- Dedicated library tab with a cover grid and a details pane.
- Search, status filters (downloaded, installed, incomplete, missing),
  favorites, compatibility filter, a genre filter that lists only the genres
  actually present (tags that are titles or upload provenance are not
  offered as genres), and sorting by title, recency, recently added, recently
  played, year, or size.
- Cover grid and a compact list view, switchable from the toolbar and
  remembered across restarts. Selection, filters, and scroll behave the same in
  both.
- Covers keep their own shape: the tile height follows the picture so tall
  posters are shown whole and artwork fills the tile instead of being padded.
- Cover art is chosen deliberately: files named cover/poster/boxart/capsule/
  app-icon/icon/logo count, sprite sheets and texture atlases never do.  A game
  with no usable artwork gets a generated colour/initials tile, so every folder
  — including games that are not in the archive collection — has an icon.
- The last selected archive game and library entry are remembered, and the
  library grid keeps its scroll position when you switch views.
- Folders that are only an installer or a desktop tool (setup packs, Hamachi and
  similar) are not treated as games; “Show all folders” reveals them anyway.
- “Group copies” collapses two downloads of the same title into one tile with a
  “+N copies” badge and lists the other folders in the details pane.
- Remove a game from the library index (files stay on disk) and pick which file
  in the folder the Launch button should review and run.
- Duplicate detection by name+size and, for files up to 64 MiB, by SHA-256.
  Duplicates are reported only; nothing is deleted.
- Disk usage: library total, game count, free space, largest entries.
- Export/import of library metadata as JSON for backup or migration. Favorites
  in the current store win on merge; imports never touch files.
- `game.json` metadata snapshot written beside each download.

### Game metadata, notes, and magnet links

- “Edit details…” in the library pane edits title, creator, year, developer,
  publisher, release date, version, genres, platforms, source page, description,
  your own notes, and an optional magnet link.
- Every metadata field carries its provenance. Anything you type is recorded as
  yours and is never overwritten when catalog metadata is merged; values from a
  different provider are kept and reported as conflicts instead of silently
  replacing one another.
- Untrusted provider data is normalized on the way in: descriptions are
  flattened to plain text, URLs must be plain `http(s)` with no credentials,
  release dates must be ISO-shaped, lists are de-duplicated and bounded, and
  unknown field names are refused so a provider cannot write `folder`,
  `status`, or `favorite`.
- Magnet links are metadata only. A pasted `magnet:` URI is parsed and stored as
  *extracted*; a bare infohash (40 hex or 32 base32) is only converted when it
  validates, and is labelled *built from infohash*. Nothing is fetched, resolved,
  or downloaded by the app, and an entry without a magnet link is unaffected.
- Copy, open (configured client or the desktop `magnet:` handler, always without
  a shell), or remove the link. A syntactically valid magnet proves nothing
  about reachability, completeness, or licensing, and the UI says so.
- The library schema is version 2. Documents written by version 1 load with
  empty defaults for the new fields and are upgraded on the next save; no
  existing entry is rewritten or dropped.

### Website sources and catalog import

Menu → **Website Sources…** wires the source system into the app:

- Enter a URL and press **Inspect**. A source is only usable when this build has
  an adapter for it, or when the URL is a public sitemap XML, a public
  `.json`/`.jsonl`/`.ndjson` catalog, or — with the structured-data checkbox on —
  a page carrying JSON-LD/Open Graph game metadata. Any other host is refused
  with an explanation instead of being crawled.
- The preview reports each capability as **verified**, **probable**,
  **unsupported**, or **unknown**, with the reason. For the Internet Archive the
  catalog, pagination, details, artwork, and mirror listing are verified from
  live responses; magnets are reported unsupported, with the reason. The same
  panel names the source kind, the pagination method, the response format, the
  authentication it needs, and what its download links actually are.
- Inspecting also reads the first page (one bounded request) and lists a few real
  record titles plus the reported total, so a large import can be judged before
  it starts. An unreadable first page is explained instead of being guessed.
- A configured source is selected automatically when the dialog opens, so its
  URL does not need to be entered again. **Inspect** refreshes its capabilities;
  **Import all** uses the last saved inspection otherwise.
- **Import all** walks the source's own pagination in a worker thread. Records
  are stored a page at a time in one batch, and the catalog document is rewritten
  on a throttle (every ten pages or two seconds) rather than once per page, so a
  6,000-record run costs ~0.9 s and nine writes instead of one write per page.
  Every checkpoint is kept in memory and the catalog is always written before the
  run ends, so cancelling cannot lose a checkpoint. Closing the dialog or pressing
  **Cancel import** stops at the next page boundary; **Resume** continues from the
  saved cursor.
- Catalog enumeration finishes first, with no per-game requests. Details,
  artwork, mirrors, and magnets are a separate pass controlled by the
  **Also fetch details…** checkbox. That pass is checkpointed by game key, works
  through records in batches (four rounds of the configured concurrency), and can
  be cancelled or resumed without losing catalog records. Records whose page was
  already read — or was read and published nothing usable — are never requested
  again.
- **Details from the record's own page:** when a catalog (a local JSON/CSV file, a
  public JSON catalog, or a game page) gives each record a source URL, the details
  pass opens that page and reads its published JSON-LD (`VideoGame`, `Product`,
  `SoftwareApplication`, `ItemList`) or Open Graph metadata: description, release
  date/year, developer, publisher, genres, platforms, and cover artwork. Only the
  page's own structured data is read, `robots.txt` and the public-address policy
  apply, and nothing is written when the page publishes none. Artwork hosts the
  page actually named are recorded per source, which is what lets Browse show the
  cover; unsupported pages are counted ("N without page metadata") instead of
  failing the run.
- Completion is only claimed when the source reported no further pages and no
  page failed. Repeated cursors, repeated page URLs, and a page that stops
  advancing are stopped as **blocked** with an error entry, never looped on.
- The diagnostics line reports connection format, pagination method, pages
  processed, records discovered/inserted/updated, duplicates, skipped records,
  retry count, completion state, and the last error.
- One bad record is skipped and logged; it never aborts the page or the import.
- Imported catalog records appear in **Browse** with search, filters, and sort.
  The list materialises 400 rows at a time and appends more as you scroll, so a
  large catalog does not freeze the window. Records survive a restart.
- Imported catalog records are separate from your library: nothing is installed,
  downloaded, or launched. Artwork URLs, mirror records, and checksums are the
  source's own claims; magnets are only stored when a source actually exposes a
  valid one, otherwise the state is "unsupported".
- **Remove source** forgets that source's catalog records, magnets, mirrors, and
  jobs. It never touches files on disk or the local library.
- **Record limit** stops a run after N games (0 = the whole catalog), capped at
  32768. Large sources such as RAWG report hundreds of thousands of records, so a
  bounded first run is the safe way to check the result in Browse. The limit
  stops the run as **partial** with an explanation rather than pretending the
  catalog is complete.
- **Import file…** reads a local `.json`, `.jsonl`/`.ndjson`, or `.csv` catalog.
  Column aliases such as `name`/`title`, `released`/`release_date`, `pageUrl` /
  `page_url` / `url`, `productId`, `poster`/`image`/`cover`, and
  `releaseTimestamp` are recognized (camelCase and snake_case both map), boolean
  platform maps (`{"windows": true, "linux": false}`) become platform names, a
  stable provider ID is derived from an explicit ID column or the canonical URL —
  never from a row number — and unrecognized fields (including any torrent,
  magnet, or checksum link a file happens to carry) are kept verbatim under
  `source_fields` and shown in the game details as *reported by the source*, never
  turned into download claims. Identifier fields the `id` aliases did not use
  (for example `productId` when `slug` became the game id) are kept there too, so
  a later import can still match a record on its real catalog key. When records
  carry source pages, the details pass is switched on automatically after the
  catalog import. A file is read back to back: the per-page politeness delay
  applies to remote hosts only, so a 6,440-record catalog imports in about 2 s
  and a re-import over the same records takes about 3 s.
- **Size and link state in Browse.** An imported record shows the size its
  source reported (`size: 7.69 GB` under `source_fields`) in the row and in the
  **Size** sort, and carries what is actually known about its links: *link
  verified*, *link unchecked*, *no download link*, or *link broken*. Only a
  mirror check that ran may say *verified*; a magnet stays *link unchecked*. The
  **Download** sort puts usable links first and broken ones last.
- **Sync poster & details…** (imported records only) searches RAWG by the
  game's name and attaches its poster, description, developer, publisher,
  release date, year, genres, platforms, and gameplay screenshots. It fills gaps
  only and never overwrites what is already stored, and it records which RAWG
  entry the artwork came from. A single exact title match is applied directly;
  anything ambiguous opens a picker, because a name is not an identity. Needs a
  free RAWG key under **Settings → Providers**. Because you asked for this
  game's poster, the button replaces it and keeps the old URL under
  `extra["artwork"]["previous_cover"]`. A poster whose host the window may not
  fetch (anything outside the archive hosts and the ones that source named)
  can never be displayed, so the bulk run repairs it rather than keeping it.
- **Sync artwork for collection** (under the collection heading, shown once you
  have imported records) runs the same lookup for every imported game that has
  no displayable poster. It keeps eight lookups in flight, reuses the
  screenshots already stored instead of fetching them again, saves every 100
  records, and the button becomes **Stop artwork sync** while it runs (it stops
  after the requests already in flight). A game with no match is remembered for
  a week, so a second run only asks about what changed instead of re-searching
  every posterless game. Games whose name matches more than one RAWG entry are
  skipped and counted, not guessed at; sync those one at a time from the game's
  panel.

### Supported adapters

| Adapter | URL shape | Auth | Provides |
|---|---|---|---|
| `ArchiveOrgSource` | `archive.org/details/…`, collection URLs | none | catalog, pagination, details, artwork, mirrors |
| `RawgSource` | `rawg.io`, `api.rawg.io` | API key | catalog, page-number pagination, details, artwork, store links |
| `SitemapSource` | a public `sitemap.xml` or sitemap index | none | entry links only (titles derived from the URL) |
| `JsonCatalogSource` | a public `.json`/`.jsonl`/`.ndjson` catalog URL | none | whatever fields the document carries, plus each record's own page for details/artwork |
| `LocalFileSource` | a local `.json`/`.jsonl`/`.ndjson`/`.csv` file | none | file fields, plus each record's own page for details/artwork |
| `ConfiguredJsonSource` | any public JSON endpoint you map yourself | none | the fields you map, plus each record's own page for details/artwork |
| `KryotoSource` | `kryo.to` (offset-paginated public API) | none | catalog, details, artwork, download links, checksums, versions |
| `FreeToGameSource` | `www.freetogame.com/api/...` with optional `platform`, `category`, `sort-by`, `tag` | none | catalog, `/game?id=` details, thumbnails, official game page |
| `StructuredHtmlSource` | an explicitly opted-in HTML page | none |JSON-LD `ItemList`/`VideoGame`/`Product` or Open Graph fields on that one page |

A record's own page is read only when the record names one with `https`, the page
permits automated access (`robots.txt`), and it publishes JSON-LD or Open Graph
game metadata. Nothing is invented for records without a page, and an unsupported
page is counted rather than treated as a failure.

### Kryoto catalog (`kryo.to`)

Menu → **Kryoto Catalog…** opens a dedicated dashboard over the same
`catalog.json` store. It discovers through Kryoto's public JSON API, then lists
the merged catalog (Kryoto records plus any JSON/GOG-style catalog you already
imported) with search, provider and link-state filters, sorting, and a detail
pane. The existing Browse view still shows every record too.

The routes below were checked against live responses on 2026-10-30. There is no
published API documentation (`/api/docs` answers 404), so the shapes are
observations, not invented contracts.

| Route | Verified behaviour |
|---|---|
| `GET /api/games?limit=N&offset=M` | `{"games": [...], "count": N}`. `offset` works; `page` is ignored. `count` is the returned page size, **not** a total. |
| `GET /api/games/search?q=…&limit=N` | `{"results": [...]}` with lightweight records and no download links. |
| `GET /api/games/{slug}` | `{"game": {...}, "related": [...], "art": {...}}`. |
| `GET /api/games/{slug}/downloads` | Links, SHA-256 hashes and versioned `releases`; relative Kryo URLs are resolved against the origin. |
| `GET /api/games/{slug}/mirrors` | Host/state/progress of the site's own mirror-building jobs. These are **not** download URLs and are never used as one. |

Because the API publishes no total, the adapter never sets `total_estimate`.
A page is complete only when it returns fewer records than requested, and a
bounded or interrupted scan stays **partial** rather than claiming full coverage.
The live catalog answered at offsets up to 2857 (2,858 records) on 2026-10-30;
that total was probed, not produced by a completed full scan.

**Refresh Kryoto Catalog** walks offset pages (`limit=25`), then runs the existing
enrichment pass for detail metadata and source links. The importer checkpoints
the cursor and each enriched game key, so **Cancel** stops at the next page or
record and **Resume** continues without repeating finished work. Details already
read are skipped on the next run; page results are cached in memory for the same
run and `force` is used for an explicit refresh.

The page walk is not strictly serial: while the importer stores the page it has,
the adapter keeps up to **four** page requests in flight (plain offset
arithmetic, 25 records each). Measured against the live API on 2026-10-31, eight
pages took 72.2 s one at a time (2.77 games/s) versus 42.7 s and 46.4 s with
look-ahead (4.69 and 4.31 games/s). More is worse — six concurrent pages pushed
median per-request latency from 14-16 s to 25-37 s — so the cap stays at four.
Speculative requests are only made for full 25-record pages, stop once a short
page proves the catalog has ended, and a prefetched page that fails is raised
when it is consumed rather than silently skipped.

A record can be:

- **listed** (the API returned it) — no download claim;
- **linked** (the source reported one or more URLs) — still unchecked; or
- **verified** (a check actually observed a file response) — only then does the
  download button queue the link. It is shown on every link, but an unchecked
  one is probed first and nothing is queued unless that probe saw a file; a
  page answer points you at **Open in Browser** instead.

Link checks: `HEAD` first, then a one-byte ranged `GET` when `HEAD` is refused.
Only HTTPS public hosts are requested, `robots.txt` is enforced, and the response
is classified as a file, a page, a redirect outside the host, blocked, missing,
or failed. A bulk check stays on Kryoto hosts; the per-link **Check** action may
probe an external mirror. Open in Browser and Copy Link are always available.

### External download managers

**Send to JDownloader** in the Kryoto detail pane hands every source link of the
selected game to a JDownloader 2 instance running on `127.0.0.1:9666`, through
its documented Click'n'Load interface (`GET /jdcheck.js`, form
`POST /flash/add`). The links are posted unchanged, duplicates collapsed; that
application performs the download, not this one. Nothing is sent unless you
press the button, and no access control is circumvented — the manager fetches
the link the way a browser would.

**Send to AB Download Manager** uses that project's own REST API instead
(`REST-API.yml` and its integration server; `apiEnabled` defaults to on, port
`15151`). Each link is posted to `POST /start-headless-download` as
`{"downloadSource": {"link": …}, "folder": …, "name": …}`, so AB Download
Manager starts it immediately with its own engine and saves into this app's
download folder. `/add` is deliberately not used: it only fills that app's link
list. If its API authentication is switched on, the request is refused and the
toast says so rather than guessing a key. This project is not affiliated with
either tool.

URLs are preserved exactly (query parameters and fragments included). A relative
Kryo URL keeps its original spelling in `raw_url` and gets an absolute URL for
opening. A URL's shape (`provider-endpoint`, `file-candidate`, `webpage`) is not
treated as proof that it downloads a file. Display labels fall back from the
provider's `label` to `name` to `host`.

Only a verified file response reaches the existing download queue; the queue's
URL guard is narrowed to the verified host instead of being disabled. Browser
interaction pages (Gofile, Pixeldrain, and similar mirrors) show **Open in
Browser**, never a fake aria2 download. Nothing is executed after download.

Measured on 2026-10-30: a bounded live scan of 30 records with enrichment took
26.3 s, all 30 had metadata, all 30 listed source links (192 link records total),
and no link was checked automatically. The API is slow for large pages, so a full
2,858-record refresh takes roughly 20–30 minutes; it runs in a worker thread and
is resumable.

### Configurable JSON mapping

**Website Sources → Custom JSON mapping (advanced)** reads any public HTTPS JSON
endpoint without new code. The mapping is a small JSON object, saved with the
source and reused on later runs:

```json
{
  "records": "data.items",
  "fields": {"id": "productId", "title": "name", "source_url": "pageUrl",
             "cover_url": "art", "description": "summary"},
  "pagination": {"type": "page", "param": "page", "size_param": "limit",
                 "start": 1, "total_path": "meta.count"}
}
```

- `records` — dotted path to the array (``""`` means the document root). A
  mapping path or field name is validated; a typo is reported, never guessed.
- `fields` — canonical field → the source's own key. Canonical names are `id`,
  `title`, `description`, `release_date`, `year`, `developer`, `publisher`,
  `genres`, `platforms`, `cover_url`, `source_url`, `tags`, `rating`.
- `pagination` — `type` is `none`, `page`, `offset`, or `cursor` (follow
  `next_path`). Optional `param`, `size_param`, `start`, `total_path`.
- The mapping cannot express a download: it produces metadata only, and a
  record's file links are never derived from it. Records that name their own
  page still get the details/artwork pass.
- A configured mapping never replaces a registered adapter, and it is stored on
  the source record, so re-running needs no retyping. A malformed stored mapping
  is dropped at load time rather than breaking the catalog.

Every adapter declares `source_kind`, `auth_kind`, `requires_auth`,
`download_capability`, its hosts, image hosts, response format, and pagination
method. `download_capability` is `none`, `store-links`, or `files`: only an
adapter that actually publishes files may offer a download, and metadata
providers never do. RAWG, for example, is `store-links` — its store URLs are
shown as metadata and no download button is invented.

### FreeToGame

Paste a FreeToGame API URL into **Website Sources** —
`https://www.freetogame.com/api/games`, or the same endpoint with the documented
filters, for example
`https://www.freetogame.com/api/games?platform=pc&category=shooter&sort-by=release-date` —
and the adapter reads it with no credentials:

- `/games` (optionally `platform`, `category`, `sort-by`), `/game?id=` for the
  long description, status, screenshots, and minimum system requirements, and
  `/filter?tag=…&platform=…`. Those are the only parameters that are sent; a
  value outside the documented vocabulary (`pc`/`browser`/`all`, the four sort
  names, a tag-shaped slug) is refused with a reason instead of being guessed.
- FreeToGame documents no pagination, so one document holds the whole filtered
  list. **Search** is applied locally in the adapter, and the catalogue's own
  search, genre/platform filters, and sort work on the imported records.
- Requests are spaced to **10 per second** at most and responses are cached for
  five minutes, so re-inspecting a source does not re-hit the API.
- The details pass runs up to four concurrent requests and no longer pauses
  between batches, so throughput follows the host's latency instead of a fixed
  sleep. `enrich_rate` can cap request starts per second when a host needs it.
- `game_url` is the game's official page, never an installer: it is stored as the
  record's source page, `download_capability` stays `store-links`, and mirrors
  and magnets are reported unsupported. A `PC` or `Browser` game is shown as
  **Windows** or **Browser** — never as a native Linux game, because the API
  does not verify a Linux build.
- The source panel shows the required attribution link to
  <https://www.freetogame.com/>.

### Magnet links in a published JSON sidecar

Some catalogs name a small machine-readable document next to a game
(`.../game.json`). When a record's preserved fields contain an `https` URL whose
path ends in `.json`, the enrichment pass reads that one document and looks for a
magnet URI or a bare torrent infohash:

- a published `magnet:` URI is used as published, keeping its trackers and name;
- a bare value is only read when the key it sits under names an infohash
  (`infohash`, `info_hash`, `infohash_v1`, `btih`). A `sha1`, `md5`, or plain
  `hash` field is never treated as one, and the value must validate before a
  magnet URI is built from it;
- the link is stored as a normal magnet record (origin `extracted` or
  `constructed`) with source attribution, de-duplicated by infohash, and the
  outcome is kept on the record so that URL is requested once.

Reading a sidecar is limited to adapters that declare they may distribute files
(`download_capability` of `public-files` or `authorized-files`) and to the hosts
that adapter declares. A store-page catalog and a mapped endpoint declare
`store-links`/`none`, so their `.json` values stay informational text and are
never fetched.

A local JSON catalog is metadata-only by default. Ticking **Read each record's
.json link for magnet or infohash** in the import dialog changes only that file's
run: the source declares `authorized-files`, the hosts named by those links are
approved, and the enrichment pass requests each link once. That switch exists
because the decision — "this file is mine and its links may be read" — belongs
to the person importing it, not to a URL heuristic.

The body is capped at 512 KiB, the request has a 15 s timeout, a redirect must
stay an HTTPS `.json` URL, and every failure is recorded as a status on the
record instead of breaking the import.

### Downloading a magnet

Magnets are downloaded by a **BitTorrent client that ships with the app**
(`lgl/bittorrent.py`). Nothing else has to be installed: the engine is Python
standard library only, so it works the same on a packaged Linux build and on
Windows.

What the built-in engine implements:

- **Magnet metadata** (BEP 9/10): the infohash is fetched from peers, then the
  SHA-1 of the received `info` dictionary is checked against it.
- **Peer discovery**: HTTP and UDP trackers (BEP 3 / BEP 15), the mainline DHT
  (BEP 5) for trackerless magnets, peer exchange (BEP 11), and `x.pe=` peer
  hints carried in the link itself.
- **Transfer**: the peer wire protocol (BEP 3) over up to 80 TCP connections,
  16 KiB blocks pipelined 24 deep per peer, one peer per piece so a slow peer
  cannot hold every piece, and SHA-1 verification of every piece before it is
  written to disk.
- **Listening**: inbound peer connections are accepted on TCP 6881-6889, which
  is what lets a swarm push data to this client instead of waiting to be polled.

Limits, stated plainly:

- No uploading and no seeding. Peers are left choked, and the DHT never gets an
  `announce_peer`, so a download is not advertised anywhere it was not already
  listed.
- No uTP (UDP peer transport), no v2/`btmh` torrents, no web seeds, and no
  `wss://` trackers. A link that needs one of those is reported as unsupported
  rather than silently crawling.
- One torrent is active at a time; the rest wait in `torrents.json`. Nothing
  resumes by itself after a restart — unfinished tasks come back paused, like
  the HTTP queue.
- A peer that drops is retried up to 8 times, 7 seconds apart. A torrent that
  sits at **zero peers with no progress for three minutes** is failed with
  "No peers … the swarm is dead or unseeded" instead of being shown as a slow
  download.
- Inbound peers still need a router port forward to TCP 6881-6889; nothing here
  can open one (there is no UPnP/NAT-PMP implementation). Without it the client
  connects out only.
- The DHT is only used to find peers for the infohash you asked for.

**aria2 is still supported.** If a path is set in Settings → Downloads, that
`aria2c` binary is used instead, driven over JSON-RPC exactly as before. Left
empty, the built-in engine is used. Which one is active is shown in the torrent
panel; `Built-in torrent engine` and `aria2c 1.37.0` are never confused.

When aria2 is the engine it runs with `--no-conf=true`, which also turns its DHT
and local peer discovery **off**, so both are enabled explicitly along with
`--bt-max-peers=100`, `--bt-request-peer-speed-limit=20M`, a stable
`--listen-port`/`--dht-listen-port` of 6881-6889 and 10 s tracker timeouts. No
speed limit is passed. `~/.aria2/aria2.conf` has no effect on those downloads.

Install aria2 only if you prefer it as the backend:

```sh
sudo xbps-install -S aria2        # Void
sudo apt install aria2            # Debian/Ubuntu
sudo dnf install aria2            # Fedora
```

On Windows, put `aria2c.exe` next to `NativeGameLibrary.exe` (a `tools` folder
beside the executable also works), install aria2 separately, or build the
package with `-BundleAria2` as described below. Settings → Downloads can point
at a specific executable when it lives elsewhere.

- **Remove** forgets the queue entry and never deletes downloaded files.
- A magnet is metadata, not proof: reachability, completeness, and licensing
  are unknown. Only download content you have the right to download.
- The link is handed to an engine only after a confirmation dialog, and the app
  never claims a magnet works before it transfers a byte.

Measured on this machine, both engines see the same ceiling because it is the
swarm's upload, not the client: a Debian netinst swarm peaked at ~17 MiB/s while
an Ubuntu ISO swarm on the same line reached ~47 MiB/s. A torrent with 6 peers
moving 15 KB/s is the swarm's total upload, not an app limit.

### Provider API keys

Menu → **Settings… → Providers** holds credentials for three metadata
providers; nothing is embedded in the source tree and no key is written into
`catalog.json` or `library.json`. The key goes to the desktop keyring (Secret
Service / Keychain / Credential Locker) when the optional `keyring` module and a
working backend are present; otherwise it is kept in `config.json` under
`api_keys`, which is written with mode `0600` on POSIX. A key saved through the
keyring is removed from the file, so it never exists twice. The value is
validated as opaque token text and is redacted from every error message the
adapter produces (`key=***`). The dialog never loads the stored secret back into
a widget: it says where the key lives and whether it has been tested, and
**Save & test** with an empty field re-tests the saved key. A provider is
reported as *connected* only after a live request succeeds; saving a key alone
does not mark it verified.

| Provider | Used for | Credential | Environment variable |
|---|---|---|---|
| **RAWG** | game metadata and artwork sync | API key | `RAWG_API_KEY` |
| **TMDB** | Movies & TV metadata, artwork, search, details | v3 API key **or** v4 Read Access Token | `TMDB_API_KEY` |
| **MyAnimeList** | anime search, details, seasons, rankings | Client ID (public read API) | `MAL_CLIENT_ID` |

- **TMDB** accepts either a v3 key (sent as `api_key`) or a v4 Read Access
  Token (sent as `Authorization: Bearer`); the client detects which one it was
  given. **Save & test** calls the documented `/3/configuration` endpoint.
  TMDB's terms require attribution: *"This product uses the TMDB API but is not
  endorsed or certified by TMDB."*
- **MyAnimeList** uses the public read endpoints with the `X-MAL-CLIENT-ID`
  header only. No account password, OAuth token, or redirect URI is requested,
  because user lists and writes are not implemented. A Client ID is created at
  myanimelist.net/apiconfig. **Save & test** performs a one-record anime read.
- **RAWG** keys are free from rawg.io/apidocs; the API is read-only metadata and
  attribution is required for the data.

The providers are independent: clearing one leaves the others configured, and a
membership change survives a restart. Credentials are never logged, returned in
error text, or shown in the UI after saving.

Sitemap, JSON catalog, structured-HTML, and local-file sources need no
credentials, so a user is never forced to configure an unrelated API.

Source adapters live in `lgl/sources.py` and implement one interface: inspect
capabilities, page through a catalog, fetch details, and optionally expose
magnets and mirrors. An unsupported operation raises instead of returning an
empty result, so the UI cannot present a guess as data.

### Compatibility information

Every downloaded folder gets a best-effort status:

**Native Linux**, **Windows**, **DOS**, **Java**, **Source only**, or
**Unknown** — derived from file suffixes, magic bytes (ELF, PE, MZ), AppImage
names, JAR contents, and archive members read in memory. The UI explains when
Wine, Proton, Java, or DOSBox might be needed.

Detection is a hint from headers and names. It inspects files without executing
or extracting them, cannot see inside `.7z`/`.rar`/`.zst` archives, and never
guarantees that a game runs.

### Integrity

- SHA-256 is verified when archive metadata publishes it; that is the only
  result reported as authoritative.
- Archive.org normally publishes MD5 and SHA-1 instead. A match is reported as
  "verified with SHA-1/MD5 — no SHA-256 published", never as SHA-256.
- Files with no published checksum are labelled plainly; no verification claim
  is made.
- Hashing runs in a worker thread, is cancellable, and is read-only.

### Interface and quality of life

- Flat, quiet layout: one toolbar row plus a Filters popover per view, hairline
  separators, and plain cover tiles instead of boxed cards.
- Posters and screenshots are fetched four at a time in the background, so an
  item page no longer waits for each image in turn.
- Keyboard shortcuts: Ctrl+1 archive, Ctrl+2 library, Ctrl+F search, F5
  refresh/rescan, Ctrl+, settings.
- Window size, maximized state, last view, sort choice **and direction**,
  favorites, filters, and download directory are remembered.
- Library sort by title, last seen, recently added, recently played, release
  year, installed size, developer, genre, or platform, each in either
  direction; entries with no value for the sorted field (no year, no size, no
  genre, never seen) always go last.
- Optional **Hide adult titles** filter: it reads NSFW/adult/18+ tags that are
  already in an entry's genres, notes, or title, so it is a metadata heuristic,
  not a content rating.
- Native folder picker for the download directory and native file pickers for
  library import/export; “Open folder” uses the platform file manager.
- Archive list sorting by title, year, size, downloads, creator, or genre in
  either ascending or descending order; the choice is remembered. Records
  without the sorted value always go last. A `subject` sort saved by an older
  build is read as `genre`.
- Archive filters cover genre, year, license, and main file format; the genre
  list is built from the `subject` tags the loaded records actually carry,
  because archive.org has no separate genre field. Only tags that read as a
  genre are offered: `subject` also holds the item's title, its series, and
  upload provenance (`GOG Games`, `ACTiVATED`, `Uploaded by …`), and those are
  left out.
- Light, dark, and follow-the-desktop themes, switchable from Settings.
- Theme-aware styling on GTK; system-neutral `ttk` styling on Windows.
- Accessible labels, focus order, clear empty states, and inline error banners
  rather than popups for routine failures.
- Cache and metadata make the browser usable offline with manual refresh.

---

## Architecture

```
                 ┌──────────────────────────────┐
   Linux GTK 3 ──┤ lgl/ui.py                    │
   Windows Tk ───┤ lgl/windows_app.py           │
                 └──────────────┬───────────────┘
                                │  threading + idle/after marshalling
                 ┌──────────────▼───────────────┐
                 │ platform-neutral core        │
                 │  archive     metadata+cache  │
                 │  downloader  queue+Range     │
                 │  library     folder+manifest │
                 │  library_store favorites etc │
                 │  metadata    validated merge │
                 │  magnet      parse/validate  │
                 │  sources     adapters+policy │
                 │  importer    paging/retries  │
                 │  catalog_store  JSON tables  │
                 │  integrity   checksums       │
                 │  compatibility detection     │
                 │  launch      reviewed launch │
                 │  settings    preferences     │
                 └──────────────┬───────────────┘
                                │
                 ┌──────────────▼───────────────┐
                 │ lgl/platform_support.py      │
                 │ XDG / AppData paths, native  │
                 │ open, notify, admin check,   │
                 │ detached processes, disk     │
                 └──────────────────────────────┘
```

**Why these choices**

- **GTK 3 stays on Linux.** It is already installed, uses the native Wayland
  backend, and the existing UI is polished. Replacing it with a cross-platform
  toolkit would mean rewriting the interface and shipping a large runtime.
- **Tkinter on Windows.** It is part of the standard library, so the packaged
  build has no GUI dependency beyond the bundled Python runtime, and PyInstaller
  handles it reliably. The core is shared, so both frontends behave the same.
- **Platform adapters, not conditionals.** All OS-specific behavior lives in
  `platform_support.py`; the rest of the code only sees `pathlib.Path`.
- **Standard library networking** (`urllib`), no `requests`.
- **Portable filenames everywhere.** Windows-invalid characters, reserved device
  names, trailing dots, and length limits are sanitized even on Linux, so a
  library can be copied between systems.
- **One download worker.** Request volume stays modest and pause/cancel is
  deterministic.

---

## Install and run

### Linux

Runtime packages (Void Linux):

| Package | Why |
| --- | --- |
| `python3` | runtime (3.10+; tested with 3.14) |
| `gtk+3` | native GTK 3 toolkit |
| `python3-gobject` | PyGObject bindings |
| `python3-cairo` | Cairo bindings used by PyGObject |
| `gdk-pixbuf` | image loading |
| `xdg-utils` | optional “Open folder” integration |

```sh
sudo xbps-install -Su python3 gtk+3 python3-gobject python3-cairo gdk-pixbuf xdg-utils
```

On Debian/Ubuntu the equivalent packages are `python3-gi`, `python3-gi-cairo`,
and `gir1.2-gtk-3.0`. Fedora: `python3-gobject`, `gtk3`.

Run from a checkout:

```sh
cd ~/Apps/linux-game-library
./run.sh          # or: python3 -m lgl
```

The Tkinter frontend used on Windows also runs on Linux, which is handy for
testing it without building the Windows package:

```sh
python3 -m lgl.windows_app
```

If GTK is unavailable, `python3 -m lgl` falls back to the Tkinter frontend, so
the app still starts.

Portable tarball:

```sh
sh packaging/linux/build-tarball.sh   # dist/NativeGameLibrary-<version>-linux-<arch>.tar.gz
```

Desktop entry (per-user, no root, no autostart):

```sh
./packaging/install-desktop.sh
```

### Windows

Download `NativeGameLibrary-<version>-windows-x64.zip` from a release or CI
artifact, extract it, and run `NativeGameLibrary.exe`. No Python installation
and no administrator rights are required. A per-user installer is also
produced when Inno Setup is available on the build machine.

See [`packaging/windows/README-Windows.md`](packaging/windows/README-Windows.md)
for installation, storage locations, troubleshooting, and uninstall steps.

---

## Where data lives

| Data | Linux | Windows |
| --- | --- | --- |
| Settings | `$XDG_CONFIG_HOME/linux-game-library/config.json` | `%APPDATA%\NativeGameLibrary\config.json` |
| Download queue | `$XDG_DATA_HOME/linux-game-library/downloads.json` | `%LOCALAPPDATA%\NativeGameLibrary\downloads.json` |
| Library metadata | `$XDG_DATA_HOME/linux-game-library/library.json` | `%LOCALAPPDATA%\NativeGameLibrary\library.json` |
| Imported catalog | `$XDG_DATA_HOME/linux-game-library/catalog.json` | `%LOCALAPPDATA%\NativeGameLibrary\catalog.json` |
| Cache (metadata, covers) | `$XDG_CACHE_HOME/linux-game-library/` | `%LOCALAPPDATA%\NativeGameLibrary\Cache\` |
| Downloads | folder chosen in Settings | folder chosen in Settings |

Defaults for the XDG variables are `~/.config`, `~/.local/share`, and `~/.cache`.
Each downloaded item gets a subfolder named after its archive.org identifier,
containing the payload, `game.json`, and optionally `launch.log`.

`library.json` is schema version 2. Version-1 files load unchanged and are
written back as version 2 on the next save; the old fields keep their values.

---

## Using the app

1. Wait for the collection list (first fetch is cached for six hours; Refresh
   forces a new fetch).
2. Search by title, identifier, creator, subject, or year. Optionally narrow by
   license or file format.
3. Select a game to see its poster, description, and file list. “Show auxiliary
   files” reveals screenshots, metadata, and torrents.
4. Choose a download folder, select files, and press “Download selected”.
5. Pause/resume/cancel/retry as needed. Cancelled or failed transfers keep their
   `.part` file. If the app closes or crashes, the queue is restored on next
   start with tasks paused.
6. After completion, switch to **Library**, rescan, and browse the cover grid.
   Favorites, filters, and sort order persist.
7. “Review & launch” requires the confirmation checkbox. For ELF/AppImage files
   without an execute bit, a second “Make executable” confirmation is required.
   Shell installers run as `[/bin/sh, <path>]` with the working directory inside
   the game folder; output goes to `launch.log`.
8. Verify integrity from the library details pane. The result says whether it
   was SHA-256, legacy SHA-1/MD5, a mismatch, or that no checksum exists.
9. Use “Edit details…” to add metadata, notes, or a magnet link. Fields you save
   are yours and survive later catalog merges. Settings → Downloads controls the
   retry limit, network timeout, and the aria2c path used for magnet downloads.
10. Open **Website Sources…** from the menu. The configured source is already
    selected; press **Import all** (or **Inspect** first to refresh the
    capability report). Catalog enumeration completes in seconds for the sample
    collection. Tick **Also fetch details…** only if you want the slower per-game
    metadata, artwork, mirror, and magnet pass. Imported records appear in the
    existing **Browse** list; data lives in `catalog.json` and is independent of
    installed games.

---

## Safety and limits

- **No elevation:** launching refuses to run as root/administrator. The app never
  calls `sudo`, `pkexec`, or a shell.
- **Path confinement:** archive filenames are sanitized and writes must resolve
  inside the chosen download root. Symlinks are not followed during scans.
- **No silent overwrite or delete:** existing files cause an error; partials are
  resumed only by explicit action; duplicates are reported, never removed.
- **No auto-execution:** only the launch review dialog can start a downloaded
  file, and unknown types are refused.
- **No credentials:** public endpoints only; no accounts, cookies, or tokens.
- **Rate limits:** one download at a time, two automatic retries with backoff,
  clear 429/5xx messages.
- **Restrictions:** items marked restricted and files marked private are not
  offered for download. Access controls are never bypassed.
- **Licensing:** the collection is user-uploaded; rights vary per item. Check the
  item's license/rights metadata and your local law.
- **Third-party installers:** `.sh` payloads can contain arbitrary code and may
  download more data from external sites. Classification is a hint, not a safety
  guarantee.
- **No sandboxing:** a launched game runs with your user privileges. Consider
  `bubblewrap`/`firejail` or a separate user if you need isolation; that is not
  wired in.
- **Compatibility is a guess:** headers and names only. Wine/Proton, Java, and
  DOSBox are never bundled or installed.

### Windows notes

Windows 10 reached the end of standard support in October 2025. The build is
kept compatible with Windows 10 where the runtime allows, but the operating
system no longer receives security updates from Microsoft. Prefer Windows 11.
The executable is unsigned, so SmartScreen may warn; that is expected for an
unsigned PyInstaller build. The application runs as a normal user, writes only
to your profile and the download folder you choose, and does not register
services or file associations.

---

## Building the Windows package

The executable must be produced on Windows; a Linux-built binary is not a
supported artifact.

```powershell
python -m pip install pyinstaller
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
```

`build.ps1` also installs Pillow, which the Tkinter frontend uses for cover
thumbnails; without it the app still runs and shows a placeholder instead of a
cover. The GTK frontend does not use Pillow.

Pass `-BundleAria2` to include `aria2c.exe` so magnet downloads work without a
separate install:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1 -BundleAria2
```

The switch downloads aria2 1.37.0 from its official GitHub release, copies
`aria2c.exe` into `packaging\windows\tools\` (picked up by the spec file), and
includes the GPLv2 license text. Redistributing that binary means honoring
aria2's GPLv2; its source is at <https://github.com/aria2/aria2>. Without the
switch the package simply reports that no engine is installed.

Outputs `dist\NativeGameLibrary\` plus
`dist\NativeGameLibrary-<version>-windows-x64.zip`, and an Inno Setup installer
when `ISCC.exe` is installed. The same steps run in
`.github/workflows/build.yml` on `windows-latest`, which also verifies that the
frozen executable starts and stays running. That smoke test is not a substitute
for using the app on a real desktop.

---

## Tests

```sh
python3 -m unittest discover -s tests -v
```

The suite is offline. It covers archive parsing and caching, poster selection
and caching, download queue persistence and recovery, strict Range validation,
disk-space preflight, URL refresh, duplicate prevention, retry limits,
path/identifier sanitization, local file detection, library scanning, favorites,
genre filtering, sorting, duplicates, export/import, schema-v1 migration,
metadata normalization and provenance merges, magnet parsing/validation/
construction, integrity verification (SHA-256 and legacy), compatibility
detection, platform adapter paths, settings persistence and validation, and
launch confirmation boundaries.

`tests/test_ui_library.py` builds real GTK widgets against a temporary XDG
profile: the cover-grid/list switch, selection restore, metadata dialog save,
magnet actions, and the Website Sources dialog (URL refusal, capability
rendering). It is skipped automatically when GTK or a display is missing, so the
rest of the suite stays headless.

The source/importer tests use mocked JSON responses and a synthetic adapter:
capability honesty, multi-page discovery, cursor and URL repetition, retries,
cancellation and resume, dedupe and conflict handling, magnet infohash dedupe,
multiple mirrors, missing checksums, malformed records, catalog persistence, a
6,000-record catalog, catalog-before-enrichment ordering, resuming an
interrupted enrichment pass, and the record/page request limits. The RAWG
adapter is covered with mocked JSON: page-number paging that stops at `count`,
`401`/`429` handling with a redacted message, missing results shapes, and a
connection test that fails before any key is stored. Feed parsing, sitemap
indexes, structured HTML, and redirect/private-address rejection have their own
fixtures. No test contacts a real website.

Network behavior is exercised with local HTTP fixtures, never archive.org.

---

## Website sources

`lgl/catalog.py` holds the only code that talks to an arbitrary website. A
source is a `SourcePolicy`, which cannot be constructed without an allowlisted
host **and** a written `permitted_reason` — the human decision that downloading
from those hosts is allowed. The policy enforces:

- `https` only, default port, no credentials, no path traversal;
- every host resolves to public addresses only (loopback, private, link-local,
  CGNAT, multicast, and reserved ranges are refused);
- redirects are followed by hand, and each hop is re-validated, so an
  allowlisted host cannot forward a request to an internal address;
- `robots.txt` is read once per host and honoured, including `Crawl-delay`; a
  missing one allows access, an unreadable one, an error status, or an
  interstitial HTML page denies it;
- responses are size-capped and a non-HTML page is never recorded as a file.

`SitemapCatalogAdapter` is the reference adapter: it reads a site's own
`sitemap.xml` (one level of sitemap index included) and returns
`CatalogEntry` records, skipping any URL whose host is not allowlisted.
`probe_link()` reports what a server actually said — `page`, `landing`,
`verified-file`, `unverified`, `external`, or `unreachable` — using `HEAD` and,
when that is refused, a one-byte ranged `GET`. A URL ending in `.zip` that
answers `text/html` is reported as a landing page, not a download.

This module is a policy/guard layer for arbitrary declared hosts; it is not
wired into a frontend, and it is separate from `lgl/sources.py`, which holds the
adapters the Website Sources dialog actually uses.

---

## Project structure

```text
linux-game-library/
├── run.sh                       # Linux convenience launcher
├── README.md
├── .github/workflows/build.yml  # tests + Windows/Linux artifacts
├── packaging/
│   ├── install-desktop.sh       # per-user Linux launcher entry
│   ├── linux/build-tarball.sh   # portable Linux tarball
│   └── windows/
│       ├── build.ps1            # PyInstaller + optional Inno Setup
│       ├── NativeGameLibrary.spec
│       ├── installer.iss
│       ├── launcher.py
│       └── README-Windows.md
├── lgl/
│   ├── __init__.py              # version, app id
│   ├── __main__.py              # frontend selection
│   ├── ui.py                    # GTK 3 frontend (Linux)
│   ├── windows_app.py           # Tkinter frontend (Windows, Linux fallback)
│   ├── style.css                # GTK theme styling
│   ├── archive.py               # archive.org client + cache + images
│   ├── catalog.py               # website policy guards, robots, link probes
│   ├── sources.py               # source adapters + capability reporting
│   ├── catalog_store.py         # imported catalog, magnets, mirrors, jobs
│   ├── importer.py              # resumable paged catalog importer
│   ├── downloader.py            # persistent queue, Range resume, retries
│   ├── library.py               # folder layout, manifest, local scan
│   ├── library_store.py         # library index, favorites, duplicates, export
│   ├── metadata.py              # normalized metadata + provenance merges
│   ├── magnet.py                # magnet URI parsing, validation, construction
│   ├── integrity.py             # checksum verification
│   ├── compatibility.py         # platform/format heuristics
│   ├── platform_support.py      # OS adapters
│   ├── settings.py              # preferences, window state
│   ├── launch.py                # reviewed launch, no shell/root
│   ├── filetypes.py             # extension/magic classification
│   └── util.py                  # sanitizing, paths, JSON helpers
├── tests/
└── docs/screenshot.png
```

---

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `ImportError: cannot import name ... from gi.repository` | Install `gtk+3` and `python3-gobject`; the Tk fallback also works. |
| Window blank / no display | Ensure `WAYLAND_DISPLAY` or `DISPLAY` is set; try `GDK_BACKEND=wayland ./run.sh`. |
| “Could not reach archive.org” | Test `curl -I https://archive.org/metadata/native-linux-games-collection`; stale cache is used when available. |
| HTTP 429 / slow metadata | Wait and press Refresh; concurrency is already limited to one download. |
| Download stuck at 0% | Check free space, permissions, and whether the item/file is restricted. The error column shows the last failure. |
| Resume does not start | Press Retry. If the server ignores `Range`, the partial file is restarted safely rather than appended. |
| Queue not restored | Confirm `downloads.json` exists in the data directory and is valid JSON; an unreadable file is ignored rather than crashing. |
| Integrity says “no published checksum” | Archive.org publishes MD5/SHA-1 for most files and SHA-256 only sometimes. That is a metadata gap, not a failure. |
| Compatibility says Unknown | Archives (`.7z`/`.rar`), shell installers, and documents cannot be classified from headers. Review the contents yourself. |
| Launch refused | File is outside the library, not a recognized shell/ELF/AppImage, or lacks an execute bit (use the explicit Make executable step). |
| Magnet download says “engine not found” | The app drives aria2c. Install it, put `aria2c.exe` beside the Windows build, or set its full path under Settings → Downloads. The app does not implement BitTorrent itself. |
| Torrent stays at 0% | That is a swarm/tracker problem, not a UI problem: no peers means no bytes. The row shows the peer count and the engine's error. Remove the task if it stays dead; files are kept. |
| Metadata edit is rejected | Source pages must be plain `http(s)` URLs; release dates must be `YYYY`, `YYYY-MM`, or `YYYY-MM-DD`; an infohash must be 40 hex or 32 base32 characters. |
| “No adapter is registered for …” | Only hosts implemented in `lgl/sources.py` can be added. That is deliberate: an adapter is code with verified interfaces, not a crawler pointed at any URL. |
| Import says `blocked` | The source repeated a cursor/page or stopped advancing. Check the job's error log; nothing was fabricated to fill the gap. |
| Import says `partial` | Some pages or records failed; imported records are kept and the run is resumable. Press Resume, or re-run Import all to merge. If the phase is `enrichment`, the catalog is complete and only the optional per-game pass had failures. |
| Import seems stuck on page 1 | Older builds fetched one details request per game before writing page 1. Current builds enumerate the whole catalog first; enable the optional enrichment checkbox only afterwards. |
| Installer fails after launch | Its own dependencies are missing. Read `launch.log`; install only what you trust. |

---

## Not implemented

- Segmented/multi-connection downloads.
- A self-contained BitTorrent implementation. Magnet downloads use the external
  aria2c binary; without it the app stores and opens magnet links but cannot
  download them itself.
- Seeding or ratio management (seeding is disabled).
- Automatic extraction of magnet links or infohashes from arbitrary web pages.
  Only a record's own `.json` link is read, and only when the import asks for it.
- A dedicated browsing grid for imported catalog records; they appear in the
  existing Browse list instead of a separate catalog page.
- Source-specific authentication, per-source rate-limit controls in the UI, and
  a configurable page size. The importer supports bounded enrichment workers
  internally, but the dialog does not expose those timing controls.
- Any adapter for gog-rev.com or similar rip/repack distributors. They are not
  fetched, indexed, or integrated.
- Bundled Wine/Proton, Java, or DOSBox integration.
- macOS frontend.
- Code signing for the Windows executable.
- Automatic deletion or deduplication of files.

---

## Verified during development

Run on the Void Linux / Wayland machine, not merely planned:

- Offline suite: `python3 -m unittest discover -s tests -v` (676 tests,
  including 19 GTK widget tests under Wayland).
- Live Kryoto check on 2026-10-30: `/api/games`, `/api/games/search`,
  `/api/games/{slug}`, `/downloads`, and `/mirrors` all answered. Capabilities
  reported catalog/details/artwork/mirrors **verified** and magnets
  **unsupported**; no total was invented.
- Live Kryoto look-ahead measurement (2026-10-31): 8 pages, `limit=25`, took
  72.2 s sequential (2.77 games/s) against 42.7 s / 46.4 s with four pages in
  flight (4.69 / 4.31 games/s); six concurrent pages were slower per request.
- Live bounded Kryoto scan (30 records, enrichment on): status **partial** by
  design (record limit), 30 metadata records, 30 with source links, 192 link
  records, 0 automatic link checks. A separate offset probe found 2,858
  catalog records; a full scan was **not** completed.
- Live source check on 2026-10-29 against
  `collection:native-linux-games-collection`: `advancedsearch.php` answered
  471 records, so the adapter reported "verified" with `total_estimate=471`.
- Live end-to-end import of that source into a temporary `CatalogStore`
  (`page_size=5`, enrichment off): status `complete`, 95 pages processed, 471
  games discovered and persisted, 0 errors. With the default page size this is
  five requests.
- Not verified live: per-game details, artwork, and mirror records from the
  Internet Archive. Those paths are covered by mocked tests only, so their
  capability states remain `probable` after a live inspect.
- Metadata editing through the real GTK dialog and detail pane, with provenance
  persisted in `library.json`.
- Cover grid/list switching, selection restore, and duplicate/retry limits in the
  download queue, all covered by the tests above.
- Live collection fetch: 471 items.
- Live metadata and poster fetch, including disk-cached images.
- A real file download with progress, speed, ETA, atomic rename, and `game.json`.
- GTK browser and Library view under Wayland: cover grid, status badges,
  search filtering, favorites controls, statistics line, settings dialog.
- Queue restoration across a restart (paused task recovered from
  `downloads.json`, nothing auto-started).
- Tkinter frontend under Linux Tk: Browse with poster and file list,
  Downloads with a restored task, Library with rescan and filtering.
- Screenshot capture (`docs/screenshot.png`).

Windows: the frontend and packaging are implemented and exercised on Linux Tk
plus the CI job; no interactive Windows verification has been performed here.
