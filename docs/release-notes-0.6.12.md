# Lumora 0.6.12

A two-bug follow-up to 0.6.11, both from the Linux Media page.

## Fixed

- **Opening an anime title no longer crashes** — the anime details page loaded its hero image through the movie page's handler, and that handler also feeds the blurred artwork backdrop, which only the movie page builds. The worker callback raised `AttributeError: 'MoviesWindow' object has no attribute '_artwork'` and the page's image never arrived. A missing (or dead) backdrop is now skipped instead of raising.
- **New Release shows its posters** — the anime lists got their cover art in 0.6.11, but the release calendar keeps its entries for three hours, so a calendar saved by an earlier version had no cover URLs and was served unchanged. The saved feed is versioned now and an older file is fetched once more; if a row is still blank after that, its source returned no poster (movies and TV need a TMDB key; anime covers come from AniList).

## Known issues

- The Linux video freeze after a pause or a 10-second skip is still unconfirmed and unfixed; it is suspected to be WebKitGTK's GPU rendering, and `WEBKIT_DISABLE_DMABUF_RENDERER=1` is worth a try.
- Streaming providers are third-party services and change without notice.
- Nothing was hand-tested on real Windows hardware, and the fixed anime page and the refreshed list posters were not looked at on a display during the build.
