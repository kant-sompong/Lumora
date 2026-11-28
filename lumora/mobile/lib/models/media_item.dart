/// What a [MediaItem] is, which decides the provider and the detail layout.
enum MediaKind { movie, tv, anime }

/// Sentinel for [MediaItem.copyWith]'s nullable fields, so `null` can clear one
/// and an omitted argument keeps the current value.
const Object _unset = Object();

/// One poster-sized entry: a movie, a TV show or an anime.
class MediaItem {
  const MediaItem({
    required this.id,
    required this.title,
    required this.kind,
    this.posterUrl,
    this.backdropUrl,
    this.year,
    this.rating,
    this.overview,
    this.genres = const <String>[],
    this.tmdbId,
    this.anilistId,
    this.adult = false,
  });

  /// Stable repository id (`tmdb:603`, `anilist:154587`, ...).
  final String id;
  final String title;
  final MediaKind kind;
  final String? posterUrl;
  final String? backdropUrl;
  final String? year;

  /// Rating out of 10 (TMDB 0-10, AniList 100-scale score divided by 10).
  final double? rating;
  final String? overview;
  final List<String> genres;

  /// TMDB id, used by the movie/TV providers.
  final int? tmdbId;

  /// AniList id, used by the anime providers and the AniList detail query.
  final int? anilistId;

  /// TMDB's own adult flag (`lgl/tmdb.py` `MediaItem.adult`).
  ///
  /// Search includes adult titles, so the cards badge the results that carry
  /// this instead of hiding them.
  final bool adult;

  /// A copy with [MediaKind], the ids, [adult] or any other field replaced.
  ///
  /// Nullable fields accept an explicit `null` to clear them; omitting one
  /// keeps the current value.
  MediaItem copyWith({
    String? id,
    String? title,
    MediaKind? kind,
    Object? posterUrl = _unset,
    Object? backdropUrl = _unset,
    Object? year = _unset,
    Object? rating = _unset,
    Object? overview = _unset,
    List<String>? genres,
    Object? tmdbId = _unset,
    Object? anilistId = _unset,
    bool? adult,
  }) {
    return MediaItem(
      id: id ?? this.id,
      title: title ?? this.title,
      kind: kind ?? this.kind,
      posterUrl: identical(posterUrl, _unset)
          ? this.posterUrl
          : posterUrl as String?,
      backdropUrl: identical(backdropUrl, _unset)
          ? this.backdropUrl
          : backdropUrl as String?,
      year: identical(year, _unset) ? this.year : year as String?,
      rating: identical(rating, _unset) ? this.rating : rating as double?,
      overview: identical(overview, _unset)
          ? this.overview
          : overview as String?,
      genres: genres ?? this.genres,
      tmdbId: identical(tmdbId, _unset) ? this.tmdbId : tmdbId as int?,
      anilistId: identical(anilistId, _unset)
          ? this.anilistId
          : anilistId as int?,
      adult: adult ?? this.adult,
    );
  }
}

/// One season row on a detail page.
class SeasonInfo {
  const SeasonInfo({
    required this.number,
    this.name,
    required this.episodeCount,
    this.posterUrl,
  });

  final int number;
  final String? name;
  final int episodeCount;

  /// The season's own poster from TMDB's `poster_path`, or null when the
  /// provider has none. Anime seasons come from AniList and never carry one.
  final String? posterUrl;
}

/// One episode row: its preview still and its own title, when the provider has
/// them. A row without either still plays — the tile falls back to the number.
class EpisodeInfo {
  const EpisodeInfo({
    required this.number,
    this.name,
    this.overview,
    this.stillUrl,
    this.airDate,
    this.runtimeMinutes,
  });

  final int number;
  final String? name;
  final String? overview;

  /// Episode preview art (TMDB `still_path`, AniList's streaming thumbnail).
  final String? stillUrl;
  final String? airDate;
  final int? runtimeMinutes;
}

/// A title plus the detail-only fields.
class MediaDetail {
  const MediaDetail({
    required this.item,
    this.seasons = const <SeasonInfo>[],
    this.runtimeMinutes,
    this.tagline,
    this.logoUrl,
  });

  final MediaItem item;
  final List<SeasonInfo> seasons;
  final int? runtimeMinutes;
  final String? tagline;

  /// The title's own logo from TMDB's `/images` (a `w500` URL), or null when
  /// TMDB has none (`lgl/tmdb.py` `MediaImages.logo_url` / `_best_logo`).
  final String? logoUrl;
}

/// A named horizontal row on the home page.
class MediaShelf {
  const MediaShelf({required this.title, required this.items});

  final String title;
  final List<MediaItem> items;
}
