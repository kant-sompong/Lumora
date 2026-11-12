# Lumora 0.6.15

Three fixes for the Windows window, all of them in the library page.

## Fixed

- **The library tabs no longer trap you** — the pill row for Favourites, Collections, Activity, Statistics, Recently viewed and Complete the set was packed inside the Games page, so opening one of those tabs hid the strip with it and there was no way back to the library. The strip now sits above the tab widget, where it stays put whichever tab is open.
- **Starting up and resizing is much quicker on a big library** — every tile was built in one go, and every window resize threw the grid away and rebuilt every tile from scratch. A 300-game library measured:
  - building the tiles: 1.1 s before, 0.11 s now — the first 60 appear at once and the rest follow a batch per event-loop turn, so the window is usable immediately;
  - four window resizes: 2.7 s before, 0.11 s now — a resize only re-places the tiles it already has, and only when the column count actually changes.
- **The schedule's day chips highlight again** — the calendar's seven day buttons had no styling at all, so clicking a day changed the list underneath without marking the chip. They now use the same chip look as the Linux window: muted with a quiet border, the selected day filled, and today outlined in the accent colour.

## Known issues

- The Linux video freeze after a pause or a 10-second skip is still unconfirmed and unfixed.
- Streaming providers are third-party services and change without notice.
- The Windows fixes were checked with offscreen renders and the test suite, not on real Windows hardware.
