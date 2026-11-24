import 'media_item.dart';

/// A site that plays media in the full-screen web view.
class StreamProvider {
  const StreamProvider({
    required this.id,
    required this.name,
    required this.urlTemplate,
    required this.kinds,
  });

  /// Stable id persisted in `AppSettings.enabledProviderIds`; the same key as
  /// `lgl/streaming.py`.
  final String id;

  /// Label shown in the player and the settings list (`lgl/streaming.py`
  /// `LABELS`).
  final String name;

  /// Embed URL templates, one per supported kind, joined with `' | '`.
  ///
  /// Segments are in [MediaKind] declaration order — `movie | tv | anime` —
  /// and a provider ships one segment per kind it supports, so a film/series
  /// provider has two and an anime-only provider has one. Placeholders:
  ///
  /// * `{id}` — the item's [MediaItem.tmdbId]
  /// * `{anilist}` — the item's [MediaItem.anilistId]
  /// * `{season}` — the season number
  /// * `{episode}` — the episode number
  ///
  /// `buildWatchUrl` in `lib/data/providers_registry.dart` is the only builder;
  /// see `docs/mobile-providers.md` for the full format.
  final String urlTemplate;

  /// The kinds this provider can play.
  final Set<MediaKind> kinds;

  bool supports(MediaKind kind) => kinds.contains(kind);
}

/// Thrown when a title has no TMDB/AniList id to build a watch URL from.
///
/// Extends [ArgumentError] because `buildWatchUrl`'s contract is "throws
/// `ArgumentError`", and callers filter providers with a single `on
/// ArgumentError` catch.
class MissingMediaIdException extends ArgumentError {
  MissingMediaIdException(this.providerName, this.title, this.idKind)
    : super('$title has no $idKind id for $providerName.');

  final String providerName;
  final String title;

  /// `TMDB` or `AniList`.
  final String idKind;
}
