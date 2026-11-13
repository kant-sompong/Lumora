# Lumora 0.6.21

Titles now look like a streaming service: their own logo over real wide art, and season lists you can read at a glance.

## Added

- **Title logos on the hero.** Both the Windows and Linux builds ask TMDB for a title's own logo, English first, and draw it where the name used to sit: at most 110 px tall and 45% of the hero's width, left-aligned, scaled with the hero and set on a soft shadow. The text title stays the accessible name and the tooltip, and it comes straight back when a title has no logo or the image fails.
- **Real wide art, with the poster only as a fallback.** Every card now asks TMDB's images endpoint for a textless backdrop, so a list result without one still gets real art. The poster is used only when nothing wide exists, and it is marked as a fallback rather than passed off as a backdrop: Linux softens and darkens it, Windows keeps it out of the backdrop slot and records it separately.
- **Streaming-style episode rows.** Season lists show a large episode number, a 200×112 still with a `★ 6.6` pill in the corner, a bold title, a dim `66 min · Sep 1, 2022` line and a three-line synopsis, with the play action on the right. The pill disappears when TMDB has no rating, and the runtime disappears when it is unknown.
- **TMDB attribution.** "This product uses the TMDB API but is not endorsed or certified by TMDB." appears on the details page in both builds, as TMDB's terms require.

## Notes

- Logos and wide art come from TMDB and are only as good as its catalogue: a brand-new or obscure title may still fall back to the text title and a darkened poster. That fallback is intentional, not an error.
- Episode stills are fetched at 780 px wide, which is sharp on a 200 px row without pulling 4K files.
- Nothing new is requested from any other host, and the API key still never appears in a message or a log.

## Known issues

- The Linux video freeze after a pause or a 10-second skip is still open.
- Streaming providers are third-party services and change without notice.
