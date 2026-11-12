# Lumora 0.6.13

Windows window cleanup: less chrome on the page, and the artwork the lists were missing.

## Changed

- **The library grid follows the Linux window** — a game's tile now takes the shape of its own cover, tall for a poster and square for a square capsule, clamped to the same 110–420 px the Linux tile uses. Windows had been forcing every cover into one oversized square, which left a two-game library looking like two giant buttons.
- **The dice button is gone** from the window header. Random pick still runs when the Media page asks for it.
- **Compare seasons is gone** from the Windows Media page: the card and the season picker behind it were removed, so the home feed ends with the airing schedule.
- **The three lists show posters** — New Release, New Added and Just Completed loaded their rows without any cover request, so every entry sat behind a two-letter placeholder. The rows now fetch and paint the real poster and keep the letters only when a source has no artwork.
- **Hidden gems is gone from Browse** — the discovery shelf stays on the Media page, where its own page already lives, so Browse now opens with Recommended for you and then the grid.

## Known issues

- The Linux video freeze after a pause or a 10-second skip is still unconfirmed and unfixed.
- Streaming providers are third-party services and change without notice.
- Nothing was hand-tested on real Windows hardware, and the new library covers and list posters were not looked at on a display during the build.
