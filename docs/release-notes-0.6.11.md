# Lumora 0.6.11

A small follow-up to 0.6.10 with three fixes taken from the running Linux app. The Windows build is
the same code base; its Media page already had these.

## Fixed

- **The three Media lists show their posters** — New Release, New Added and Just Completed drew an
  empty thumbnail slot because the rows carried no image URL. The anime lists now ask AniList for
  cover art (and drop their old poster-less cache once), the release feed keeps the poster it
  already received from TMDB and AniList, and each row fetches its cover once and reuses it.
- **The Estimated Schedule says what a row is** — every airing now reads like "TV · Episode 5",
  "Movie · 12 Nov" or "Anime · Episode 3", derived from the entry it came from.
- **A shorter Media header** — the Provider health button and the Calendar tab are gone from the
  Media header, and the app-wide die is hidden while Media is on screen. The home page already
  carries the provider picker, the Estimated Schedule and the calendar, and provider health is
  still one click away under the provider menu's "Test connections now".

## Known issues

- The Linux video freeze after a pause or a 10-second skip is still unconfirmed and unfixed; it is
  suspected to be WebKitGTK's GPU rendering, and `WEBKIT_DISABLE_DMABUF_RENDERER=1` is worth a try.
- Streaming providers are third-party services and change without notice.
- Nothing was hand-tested on real Windows hardware, and the new list thumbnails, the trimmed header
  and the schedule labels were not looked at on a display during the build.
