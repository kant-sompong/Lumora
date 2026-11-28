# Mobile providers

`lib/data/providers_registry.dart` mirrors `lgl/streaming.py`: the same ids, the
same labels, the same embed URL templates, in the same order.

```text
allProviders = cinesrc, rivestream, toflix, cinelite, framextv, atlantic,
               vidy, chillflix, vidcore, velarium, afterdusk, moonflix, ani
```

`ani` is anime-only and last, as in `ANIME_PROVIDERS` (`PROVIDERS`, then `ani`).
`cinesrc` and `ani` are the PC defaults; nothing is enabled until the user turns
it on (`AppSettings.load()` seeds all of them on a first run).

## `StreamProvider`

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | `String` | Persisted key; the `lgl/streaming.py` key (`cinesrc`, `ani`, ...) |
| `name` | `String` | Label from `LABELS` (`CineSrc`, `Rive`, `anipm.pk (anime)`, ...) |
| `urlTemplate` | `String` | Embed templates, one per kind, joined with `' | '` |
| `kinds` | `Set<MediaKind>` | Which of `movie`, `tv`, `anime` it can play |

## `urlTemplate` format

One provider carries its per-kind routes in a single string, joined with the
exact separator `' | '` (space, pipe, space):

```text
movieTemplate | tvTemplate | animeTemplate
```

* Segments are in **`MediaKind` declaration order**: `movie`, then `tv`, then
  `anime`.
* A provider ships **one segment per kind in `kinds`**, so a film/series provider
  has two segments, `ani` (anime only) has one, and `toflix`/`cinelite`/`vidy`
  have three. `urlTemplates(provider).length` therefore equals
  `templateKinds(provider).length`; `templateFor(provider, kind)` does the index
  lookup for you.

### Placeholders

Only these five tokens exist. Anything else is a bug, and a template that still
holds a token after substitution makes `buildWatchUrl` throw.

| Token | Replaced with | Source |
| --- | --- | --- |
| `{id}` | the TMDB id | `MediaItem.tmdbId` |
| `{anilist}` | the AniList id | `MediaItem.anilistId` |
| `{season}` | the season number (1-based) | `buildWatchUrl(..., season:)` |
| `{episode}` | the episode number (1-based) | `buildWatchUrl(..., episode:)` |
| `{language}` | `sub`, `dub` or `vostfr` | `buildWatchUrl(..., language:)`, from Settings → Default audio (anything else becomes `sub`) |

Which id a route uses is decided by the segment, not by the provider: a segment
containing `{anilist}` is keyed by `MediaItem.anilistId`, every other segment by
`MediaItem.tmdbId`. No fallback parsing of `MediaItem.id` — a title without the
id its route needs is refused, never turned into a URL with `0` or an empty id.

### Current templates

| id | movie | tv | anime |
| --- | --- | --- | --- |
| `cinesrc` | `https://cinesrc.st/embed/movie/{id}` | `https://cinesrc.st/embed/tv/{id}?s={season}&e={episode}` | — |
| `rivestream` | `https://www.rivestream.app/embed?type=movie&id={id}` | `https://www.rivestream.app/embed?type=tv&id={id}&season={season}&episode={episode}` | — |
| `toflix` | `https://toflix.co/embed/movie/{id}` | `https://toflix.co/embed/tv/{id}/{season}/{episode}` | `https://toflix.co/embed/anime/{anilist}/{episode}` |
| `cinelite` | `https://embed.cinelite.xyz/{id}` | `https://embed.cinelite.xyz/{id}/{season}/{episode}` | `https://embed.cinelite.xyz/anime/{anilist}/{episode}` |
| `framextv` | `https://framextv.xyz/embed/{id}` | `https://framextv.xyz/embed/{id}/{season}/{episode}` | — |
| `atlantic` | `https://atlantic.st/embed/movie/{id}` | `https://atlantic.st/embed/tv/{id}/{season}/{episode}` | — |
| `vidy` | `https://www.vidy.st/movie/{id}` | `https://www.vidy.st/tv/{id}/{season}/{episode}` | `https://www.vidy.st/anime/{anilist}/{episode}` |
| `chillflix` | `https://chillflix.lol/embed/movie/{id}` | `https://chillflix.lol/embed/tv/{id}/{season}/{episode}` | — |
| `vidcore` | `https://vidcore.io/movie/{id}` | `https://vidcore.io/tv/{id}/{season}/{episode}` | — |
| `velarium` | `https://velarium.cc/en/watch/movie/{id}` | `https://velarium.cc/en/watch/tv/{id}-{season}-{episode}` | — |
| `afterdusk` | `https://afterdusk.app/embed/movie/{id}` | `https://afterdusk.app/embed/tv/{id}/{season}/{episode}` | — |
| `moonflix` | `https://player.moonflix.website/movie/{id}` | `https://player.moonflix.website/tv/{id}/{season}/{episode}` | — |
| `ani` | — | — | `https://anipm.pk/embed/ani/{anilist}/{episode}/{language}` |

## API

```dart
const List<StreamProvider> allProviders;
StreamProvider? providerById(String id);            // null for an unknown id
List<StreamProvider> providersForKind(MediaKind kind);
List<String> urlTemplates(StreamProvider provider);
List<MediaKind> templateKinds(StreamProvider provider);
String? templateFor(StreamProvider provider, MediaKind kind);

Uri buildWatchUrl(
  StreamProvider provider,
  MediaItem item, {
  int season = 1,
  int episode = 1,
});
```

`buildWatchUrl` is the **only** URL builder; nothing else concatenates a
provider URL. It throws `ArgumentError` when:

* the provider has no route for `item.kind` (e.g. `ani` for a movie),
* `season` or `episode` is below 1,
* the title lacks the id the chosen route needs — `MissingMediaIdException`,
  which extends `ArgumentError` so callers can filter with one
  `on ArgumentError` catch,
* the template leaves a placeholder, or the finished string is not an absolute
  URL.

An id-free or zero id is never substituted: the URL is refused instead. This is
what `navigation`'s `playbackTargets` relies on to skip a provider instead of
opening a dead player.
