# Lumora 0.6.14

A follow-up to 0.6.13, all of it polish for the two windows' chrome.

## Changed

- **The library page is compact and uses the app's own tabs** — the tab strip drew seven boxed tabs stretched across the whole window, like a browser tab bar. It is now a pill row in the same style as the Media page's nav, left-aligned and quiet, with the open tab in normal text weight. The sort pill, direction arrow, Filter and Select buttons keep their own width instead of spreading across the row, and the rescan and more-menu icons sit at the right edge.
- **A short library starts at the left margin** — with only a couple of games the grid stretched the columns it did create and floated the cards towards the middle of the window. Every column now gets its share up front and the cards are top-aligned, so a two-game library reads as a normal grid.
- **The Linux player's fullscreen is really fullscreen** — the app's icon rail is packed beside the page stack, outside the Media window, so fullscreen hid the titlebar and the Media header but left a 68 px strip of rail icons down the left of the video. Fullscreen now hides the rail as well and brings it back on exit.

## Known issues

- The Linux video freeze after a pause or a 10-second skip is still unconfirmed and unfixed.
- Streaming providers are third-party services and change without notice.
- The Windows library page was reviewed from offscreen renders, not on Windows: colours, cover art and hover behaviour on real hardware are unchecked. The Linux fullscreen fix was checked by the test suite only, not by watching a video on a display.
