# Lumora 0.6.22

Season lists that open where you can see them, say which episodes have not aired yet, and fit a screen narrower than the panel — plus the TMDB logos that anime titles were missing.

## Fixed

- **Anime titles find their TMDB logo.** A title like *Frieren: Beyond Journey's End* has logos on TMDB and still showed plain text, while *Death Note* worked. Two shared bugs: ani.pm sends a title's episode count as an object (`{"total": 28, "sub": 28, "dub": 28}`) and it was read as a number, so every catalogue row arrived with zero episodes; the Linux page then read "no episodes" as "a film" and looked the title up on TMDB's movie index, where a series does not exist. Death Note only worked because a same-named film exists. The episode count is now read from either shape (which also fixes the episode range on the anime page), and the title's own format decides TV or movie, the same rule the Windows page already used.
- **A logo in any language is a logo.** The images request used to ask TMDB for the display language, English and untagged artwork only, so a title whose logos are tagged Japanese — most anime — came back with nothing to draw. The request now asks for every image and the choice is made locally: display language, then English, then untagged, then any usable PNG or SVG, best voted. A missing or null language field is a normal logo, and the text title still comes back when there is no logo at all.
- **The Windows season picker opens under its own header.** The popup could open wherever the window system decided; it now hangs directly below the header, flips above when there is no room below, and is clamped to the available screen on every side. It also follows the window while it is open.
- **A narrow screen wins over the panel's 300 px width (Windows).** The placement worked out a width that fits the screen but applied only the height, so on a screen narrower than 300 px the popup hung off the edge. It is now 300 px whenever there is room, narrower when there is not, kept inside the screen at either edge, and back to 300 px when the window moves to a wider screen.

## Added

- **"Not aired yet" on episodes that have not aired.** The Windows season list marks an upcoming episode's title in the danger ink with a `Not aired yet` label, and re-checks the rows when the calendar day changes. The day itself counts as aired; a missing or unreadable air date stays neutral, and the row is never disabled, so play behaves exactly as before.
- **IMDb and Rotten Tomatoes scores, when you have a key.** A title's detail page shows TMDB's user score, and adds IMDb's rating and Rotten Tomatoes' score when an OMDb key is saved in Settings. Both builds use the same parsing. Without a key only TMDB's percentage appears; a slow or failed OMDb read shows what came back and never blocks the page.
- **A season picker with season artwork.** Both builds replace the plain season dropdown with a quiet header — `Season 2` and a chevron — that opens a panel of 76 px rows: 44×64 portrait art or the season number, the name and episode count, a check on the selected row, arrow-key and Enter/Escape handling, and a 120 ms fade. Poster art is loaded after the panel opens, so it never delays it.

## Also in this build

These were merged before this release's four fixes and are part of the same build:

- **One component sheet for both builds.** Buttons, fields, cards and dialogs now share the same metrics, radii and states on Linux and Windows, and the Linux dialogs that still used GTK's own buttons moved onto the same sheet.
- **Keyboard focus you can see.** Cards and controls draw a focus ring again: the rules asked for `:focus-visible`, which GTK 3 does not have, so they had been silently dropped. Enter or Space opens a focused card.
- **Contrast-checked accent and danger inks.** Accent, accent fill and the danger labels are separate tokens now, every text pair measures at least 4.5:1 in both themes, and the danger button's hover tint no longer swallows its label.
- **The sidebar is 72 px wide again**, as its own layout promises; the active item's accent bar is an inset shadow instead of a border, which had been inflating the rail to 74 px.

## Notes

- Logos and wide art come from TMDB and are only as good as its catalogue: a brand-new or obscure title may still fall back to the text title and a darkened poster. That fallback is intentional, not an error.
- OMDb is optional. The key is stored in Settings and never appears in a message or a log; with no key, Lumora makes no OMDb request at all.
- The Windows portable build is still unzip-and-run, and the installer is built from the same tag; both read settings and catalogue from `%APPDATA%\Lumora`.

## Known issues

- The Linux video freeze after a pause or a 10-second skip is still open.
- Streaming providers are third-party services and change without notice.
