# Lumora 0.6.10

A follow-up to 0.6.8 that finishes the Media page and fixes what the last release left broken.
No new sections, no new features to learn: the Media page now looks and behaves the same on both
desktops, and the start-up crash and light-mode colour problems are gone.

Everything below is in both desktops unless the line says otherwise.

## New

- **Media feed, rebuilt on both desktops** — the Linux Media page is now the same streaming feed
  the Windows one had: a hero, seven poster rows, the three-column lists and the week strip. Each
  section loads on its own, so one slow source no longer holds up the page.
- **One provider button** — the provider you are using is a single badge in the Media header, and
  pressing it changes the provider. Provider health no longer covers the tabs.
- **Compact Compare seasons card** — the comparison opens as a compact card from the menu, keeps
  its lines at the top, and "Show all" folds the columns back.
- **Surprise me draws from the page you are on** — the dice on Browse and Library picks games, on
  Movies picks movies, on TV picks TV, and on Anime and Hidden gems picks anime.
- **Right-click library tools** — the game actions moved onto a context menu, and the Library lost
  its second search box and header filter; its empty page, tab titles and search hints were tidied
  with it.
- **Library and Browse restructured** — Discover is two Browse rows and a Library tab, the poster
  rows are capped at one card's height with room for the loading state, and the Browse discovery
  rows stay up when the catalogue list is empty.

## Fixed

- **Start-up crash** — deleting the old Media stylesheet left two `provider` blocks behind in the
  Media window, which raised `NameError` before the window could open. Both are gone, and a test
  parses the module so that class of leftover cannot come back.
- **Missing Media artwork** — TMDB answers with WebP image paths that a GdkPixbuf build without a
  WebP loader cannot decode, so posters and backdrops stayed blank; a failed decode now retries the
  original JPEG. Hidden Gems reads the poster field its feed actually publishes, detail pages paint
  the row's own backdrop before the request answers, and old history records keep their poster.
- **Your provider choice is respected** — anime playback no longer falls back to ani.pm behind your
  back, and the health check picks the fastest provider instead of a fixed one.
- **Calendar keeps its tab lit**, and the week strip no longer resets a pinned clock when its data
  arrives (this is what made the strip show an empty week after midnight).
- **The dice stays on the page it was pressed on**, instead of pulling a card from another section.
- **Linux dark and light colours** — the Media page no longer paints its own dark sheet over a
  light window. Section titles, the active tab and the primary button name their own colours, and
  the shell shares one surface colour instead of four near-black shades.
- **Hero text is readable in light mode** — the labels drawn on the always-dark hero use their own
  light inks instead of the page theme, so the title, subtitle, rating, meta line and synopsis no
  longer turn into dark text on dark artwork.

## Known issues

- The Linux video freeze after a pause or a 10-second skip is still unconfirmed and unfixed; it is
  suspected to be WebKitGTK's GPU rendering, and `WEBKIT_DISABLE_DMABUF_RENDERER=1` is worth a try.
- Streaming providers are third-party services. Their pages and APIs change without notice, so a
  provider that works today can stop working tomorrow.
- Nothing was hand-tested on real Windows hardware: the Windows build is exercised offscreen and in
  CI, not on a desktop someone is sitting at.
