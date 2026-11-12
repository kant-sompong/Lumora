# Lumora 0.6.16

One addition to the Linux media player.

## Added

- **Next episode** — while a TV episode is playing, the watch page's title line now ends with a `Next episode` button on the right. It plays the following episode in the season, rolls on to the first episode of the next season when a season ends, and is simply not there on a series finale, on a movie, or when the details page never loaded an episode list. The tooltip names what it will play (`Play S04E02 · A Quiet Case`), it uses the app's own dark button style rather than covering the video, and it disappears with the rest of the chrome when the player goes fullscreen.

## Known issues

- The Next episode button is only in the Linux/GTK player; the Windows player does not offer it yet.
- The Linux video freeze after a pause or a 10-second skip is still unconfirmed and unfixed.
- Streaming providers are third-party services and change without notice.
- The new button was checked with unit tests and the GTK suite, not by watching an episode on a display.
