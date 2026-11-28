import '../models/media_item.dart';
import '../models/stream_provider.dart';

/// Every media streaming provider the PC app offers, in PC order
/// (`lgl/streaming.py`: `PROVIDERS`, then the anime-only `ani`).
///
/// The ids, labels and URL templates are copied from `lgl/streaming.py`
/// (`SPECS`, `movie_url`, `episode_url`, `anime_url`, `ani_url`). Templates are
/// per-kind segments joined with ` | `; see [StreamProvider.urlTemplate] and
/// `docs/mobile-providers.md`.
const List<StreamProvider> allProviders = <StreamProvider>[
  // lgl/streaming.py movie_url/episode_url: /embed/movie/<tmdb> and
  // /embed/tv/<tmdb>?s=<season>&e=<episode>.
  StreamProvider(
    id: 'cinesrc',
    name: 'CineSrc',
    urlTemplate:
        'https://cinesrc.st/embed/movie/{id} | '
        'https://cinesrc.st/embed/tv/{id}?s={season}&e={episode}',
    kinds: <MediaKind>{MediaKind.movie, MediaKind.tv},
  ),
  StreamProvider(
    id: 'rivestream',
    name: 'Rive',
    urlTemplate:
        'https://www.rivestream.app/embed?type=movie&id={id} | '
        'https://www.rivestream.app/embed?type=tv&id={id}'
        '&season={season}&episode={episode}',
    kinds: <MediaKind>{MediaKind.movie, MediaKind.tv},
  ),
  // ToFlix documents an AniList-keyed anime route too, so it plays all three
  // kinds (lgl/streaming.py ANILIST_ANIME_PROVIDERS).
  StreamProvider(
    id: 'toflix',
    name: 'ToFlix',
    urlTemplate:
        'https://toflix.co/embed/movie/{id} | '
        'https://toflix.co/embed/tv/{id}/{season}/{episode} | '
        'https://toflix.co/embed/anime/{anilist}/{episode}',
    kinds: <MediaKind>{MediaKind.movie, MediaKind.tv, MediaKind.anime},
  ),
  StreamProvider(
    id: 'cinelite',
    name: 'CineLite',
    urlTemplate:
        'https://embed.cinelite.xyz/{id} | '
        'https://embed.cinelite.xyz/{id}/{season}/{episode} | '
        'https://embed.cinelite.xyz/anime/{anilist}/{episode}',
    kinds: <MediaKind>{MediaKind.movie, MediaKind.tv, MediaKind.anime},
  ),
  StreamProvider(
    id: 'framextv',
    name: 'frameXTV',
    urlTemplate:
        'https://framextv.xyz/embed/{id} | '
        'https://framextv.xyz/embed/{id}/{season}/{episode}',
    kinds: <MediaKind>{MediaKind.movie, MediaKind.tv},
  ),
  StreamProvider(
    id: 'atlantic',
    name: 'Atlantic',
    urlTemplate:
        'https://atlantic.st/embed/movie/{id} | '
        'https://atlantic.st/embed/tv/{id}/{season}/{episode}',
    kinds: <MediaKind>{MediaKind.movie, MediaKind.tv},
  ),
  StreamProvider(
    id: 'vidy',
    name: 'Vidy',
    urlTemplate:
        'https://www.vidy.st/movie/{id} | '
        'https://www.vidy.st/tv/{id}/{season}/{episode} | '
        'https://www.vidy.st/anime/{anilist}/{episode}',
    kinds: <MediaKind>{MediaKind.movie, MediaKind.tv, MediaKind.anime},
  ),
  StreamProvider(
    id: 'chillflix',
    name: 'Chillflix',
    urlTemplate:
        'https://chillflix.lol/embed/movie/{id} | '
        'https://chillflix.lol/embed/tv/{id}/{season}/{episode}',
    kinds: <MediaKind>{MediaKind.movie, MediaKind.tv},
  ),
  StreamProvider(
    id: 'vidcore',
    name: 'VidCore',
    urlTemplate:
        'https://vidcore.io/movie/{id} | '
        'https://vidcore.io/tv/{id}/{season}/{episode}',
    kinds: <MediaKind>{MediaKind.movie, MediaKind.tv},
  ),
  StreamProvider(
    id: 'velarium',
    name: 'Velarium',
    urlTemplate:
        'https://velarium.cc/en/watch/movie/{id} | '
        'https://velarium.cc/en/watch/tv/{id}-{season}-{episode}',
    kinds: <MediaKind>{MediaKind.movie, MediaKind.tv},
  ),
  StreamProvider(
    id: 'afterdusk',
    name: 'Afterdusk',
    urlTemplate:
        'https://afterdusk.app/embed/movie/{id} | '
        'https://afterdusk.app/embed/tv/{id}/{season}/{episode}',
    kinds: <MediaKind>{MediaKind.movie, MediaKind.tv},
  ),
  StreamProvider(
    id: 'moonflix',
    name: 'Moonflix',
    urlTemplate:
        'https://player.moonflix.website/movie/{id} | '
        'https://player.moonflix.website/tv/{id}/{season}/{episode}',
    kinds: <MediaKind>{MediaKind.movie, MediaKind.tv},
  ),
  // anipm.pk (https://anipm.pk/developers) is anime-only and keyed by AniList
  // id; the last path segment is the audio track (`lgl/streaming.py` ani_url).
  StreamProvider(
    id: 'ani',
    name: 'anipm.pk (anime)',
    urlTemplate: 'https://anipm.pk/embed/ani/{anilist}/{episode}/{language}',
    kinds: <MediaKind>{MediaKind.anime},
  ),
];

/// The audio tracks a provider may be asked for (`lgl/streaming.py`
/// `LANGUAGES['ani']`), in the desktop's order.
const List<String> audioLanguages = <String>['sub', 'dub', 'vostfr'];

/// How each track is written on screen (`lgl/streaming.py`
/// `LANGUAGE_LABELS`).
const Map<String, String> audioLanguageLabels = <String, String>{
  'sub': 'Sub',
  'dub': 'Dub',
  'vostfr': 'VOSTFR',
};

/// What the player asks for unless the user picks another track
/// (`lgl/streaming.py` `DEFAULT_LANGUAGE`).
const String defaultAudioLanguage = 'sub';

/// Clamps a stored or requested track to one the players accept, as
/// `lgl/streaming.py` `clean_language` does.
String cleanAudioLanguage(Object? value) {
  final text = value is String ? value.trim().toLowerCase() : '';
  return audioLanguages.contains(text) ? text : defaultAudioLanguage;
}

const Set<String> templateTokens = <String>{
  '{id}',
  '{anilist}',
  '{season}',
  '{episode}',
  '{language}',
};

/// Separator between the per-kind templates of one provider.
const String templateSeparator = ' | ';



/// Looks up a provider by its persisted id, or null when it is unknown.
///
/// Callers use the null to skip an id left in stored settings by an older
/// build instead of crashing.
StreamProvider? providerById(String id) {
  for (final provider in allProviders) {
    if (provider.id == id) {
      return provider;
    }
  }
  return null;
}

/// All providers that can play [kind], in PC order.
List<StreamProvider> providersForKind(MediaKind kind) {
  return allProviders.where((provider) => provider.supports(kind)).toList();
}

/// [provider]'s templates, split out of [StreamProvider.urlTemplate].
List<String> urlTemplates(StreamProvider provider) {
  return provider.urlTemplate.split(templateSeparator);
}

/// The kinds [provider] has a template for, in [MediaKind] declaration order.
///
/// This is the order the template segments are written in, so
/// `urlTemplates(provider)[index]` is the route for
/// `templateKinds(provider)[index]`.
List<MediaKind> templateKinds(StreamProvider provider) {
  return MediaKind.values
      .where((kind) => provider.supports(kind))
      .toList(growable: false);
}

/// The embed template [provider] uses for [kind], or null when it has none.
String? templateFor(StreamProvider provider, MediaKind kind) {
  final kinds = templateKinds(provider);
  final templates = urlTemplates(provider);
  if (templates.length != kinds.length) {
    return null;
  }
  final index = kinds.indexOf(kind);
  return index < 0 ? null : templates[index];
}

/// Builds the full-screen player URL for [item] on [provider].
///
/// * movies use `tmdbId` and the movie segment,
/// * TV uses `tmdbId` with [season] and [episode],
/// * anime uses `anilistId`, [episode] and [language].
///
/// [language] is `sub`, `dub` or `vostfr`; anything else falls back to
/// [defaultAudioLanguage]. Only providers with a `{language}` segment use it.
///
/// Throws [ArgumentError] when the provider cannot play the item's kind, when
/// season/episode is below 1, or when the title has no id the selected route
/// needs ([MissingMediaIdException], which is an [ArgumentError]). A URL with a
/// `0`, an empty id or an unexpanded placeholder is never returned.
Uri buildWatchUrl(
  StreamProvider provider,
  MediaItem item, {
  int season = 1,
  int episode = 1,
  String language = defaultAudioLanguage,
}) {
  final template = templateFor(provider, item.kind);
  if (template == null) {
    throw ArgumentError.value(
      item.kind.name,
      'item.kind',
      '${provider.name} has no ${item.kind.name} route',
    );
  }
  if (season < 1) {
    throw ArgumentError.value(season, 'season', 'must be 1 or more');
  }
  if (episode < 1) {
    throw ArgumentError.value(episode, 'episode', 'must be 1 or more');
  }

  final usesAniList = template.contains('{anilist}');
  final id = usesAniList ? item.anilistId : item.tmdbId;
  if (id == null || id <= 0) {
    throw MissingMediaIdException(
      provider.name,
      item.title,
      usesAniList ? 'AniList' : 'TMDB',
    );
  }

  final raw = template
      .replaceAll('{id}', id.toString())
      .replaceAll('{anilist}', id.toString())
      .replaceAll('{season}', season.toString())
      .replaceAll('{episode}', episode.toString())
      .replaceAll('{language}', cleanAudioLanguage(language));

  // A template that still holds a placeholder is a registry bug; refuse it
  // rather than load a broken player.
  for (final token in templateTokens) {
    if (raw.contains(token)) {
      throw ArgumentError('${provider.name} left $token unexpanded.');
    }
  }

  final uri = Uri.tryParse(raw);
  if (uri == null || !uri.hasScheme || uri.host.isEmpty) {
    throw ArgumentError('${provider.name} has an invalid URL template.');
  }
  return uri;
}
