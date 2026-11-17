# Lumora 0.6.23

Anime is decided by MyAnimeList now, and a verified anime always plays on ani.pm — on both Linux and Windows — with split seasons, TMDB episode groups and adult titles handled properly along the way.

## Added

- **A verified anime plays on ani.pm, whatever provider is selected (Linux and Windows).** Identity is no longer inferred from a title, a language or a genre. The shared core asks the official MyAnimeList API, matches the catalogue title against a real MAL entry (exact title or alternative title, compatible year and media type), and only then resolves the AniList entry that ani.pm plays. A verified title is routed to ani.pm even when another provider is selected or was saved for anime; the AniList id that reaches the player is never a MAL id and is never guessed. When no reliable MAL match exists, or the lookup cannot run at all, the title is *unverified* and keeps the provider you selected — it is never hidden or excluded.
- **Split seasons and TMDB episode groups.** A season that is several AniList entries on ani.pm — Attack on Titan season 3, Re:Zero's split seasons — is mapped entry by entry, with TMDB episode offsets so episode 15 plays as the second part's episode 3. TMDB episode groups are read as an alternate season layout, alternate orders are accepted, specials are recognised from their own episodes, and the episode numbers you see stay TMDB numbers.
- **"Which part" when a season cannot be told apart.** If a TMDB season maps to more than one AniList entry and the mapping cannot choose, the Windows build asks which part instead of playing the wrong one, with the entries, their ranges and their artwork.
- **Adult titles are searched for and marked (Linux and Windows).** Every TMDB search now includes adult results, and a title TMDB flags as adult carries an `18+` badge on its card and its detail page instead of silently disappearing from a search.
- **An anime page that just plays.** The Linux anime hero is `Watch` and `Back` again: the episode spinner and audio-track picker are gone, because `Watch` already knows the episode (the next one after a play, otherwise the first) and the track comes from Settings.

## Changed

- **No heuristic can verify an anime any more.** A Japanese original language plus TMDB's Animation genre used to be reported as anime; it is now simply unverified, so such a title keeps your selected provider. Only a MAL match verifies, and only a verified title is rerouted.
- **A verified anime whose AniList entry is missing or ambiguous is an error, not a fallback.** If MAL confirms the anime but its AniList entry cannot be resolved safely, the play stops with an explanation (unresolved or ambiguous) rather than quietly opening another provider.
- **The anime detail page routes by identity too.** MAL and AniList catalogue pages, not only TMDB cards, go through the same check — an AniList id alone is not identity; the MAL entry behind it has to verify, and an AniList page whose entry has no MAL link is treated as unverified.

## Fixed

- **A season's episodes no longer mis-number when a season is two AniList entries.** The offset in the mapping list is applied before the episode reaches ani.pm, so the second half of a split season starts at its own episode 1.
- **An ambiguous "which part" answer can no longer play a guessed entry.** The candidates are shown and play waits for a choice.

## Notes

- Verifying anime needs a MyAnimeList Client ID: set `MAL_CLIENT_ID` in the environment, or add it in Settings → Providers. Only the public anime catalogue is read (no account, no OAuth) and the key is never written to a log or a message. Without a key, anime titles are unverified and play on your selected provider — nothing breaks, and nothing is guessed.
- Identity answers are cached: a verified answer for a week, an unverified one for a day, and a failed lookup is not cached at all, so an offline moment is retried on the next play.
- Episode mapping comes from the public Fribb/anime-lists mapping list and TMDB, and is cached like the other catalogues. It decides the episode, never the identity.
- The Windows portable build is still unzip-and-run, and the installer is built from the same tag; both read settings and catalogue from `%APPDATA%\Lumora`. The Linux tarball is a source tarball and uses the distribution's GTK 3 and WebKit2GTK.

## Known issues

- **ani.pm hard-sub and hard-dub are not integrated yet.** ani.pm reports four tracks - `sub`, `dub`, `subHard`, `dubHard` - but only `sub` and `dub` can be built from an AniList id and episode number; the hard-sub and hard-dub variants exist only when ani.pm's API hands back its own embed URL, and there is no dedicated API integration for them yet. A hard-sub or hard-dub track can therefore be unavailable even when the title has one on the site.
- The Linux video freeze after a pause or a 10-second skip is still open.
- Streaming providers are third-party services and change without notice; ani.pm and TMDB outages are reported, not worked around.
- Anime mapping is only as complete as the Fribb list and TMDB: a brand-new or obscure title may be unverified, or fall back to the identity's own AniList entry with the episode number unchanged.
