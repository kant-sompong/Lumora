# Lumora 0.6.17

The Next episode button now actually appears — in both players.

## Fixed

- **Linux (GTK): the Next episode button was never shown.** 0.6.16 added it, but its target was read from the details page's season list, which only carries each season's name, air date and episode count. The episodes are fetched separately when a season tab is opened, so the lookup always found nothing and the button was never created. The page now keeps the seasons it has loaded and looks in both places.
- **Windows (Qt): the player has the button now.** It sits at the right of the title line, next to Back, uses the same dark button style, and disappears with the rest of the chrome in fullscreen.
- **A season finale moves on.** When the episode playing is the last one of its season, the app fetches the next season in the background and offers its first episode, instead of ending the offer at the season break.

Both players behave the same way: the tooltip names the target (`Play S04E02 · A Quiet Case`), the button is absent on a movie, on an anime title, on a series finale, and when the episode list has not loaded.

## Known issues

- The Linux video freeze after a pause or a 10-second skip is still unconfirmed and unfixed.
- Streaming providers are third-party services and change without notice.
- The Next episode button is covered by unit tests in both frontends, not by watching an episode on a display.
