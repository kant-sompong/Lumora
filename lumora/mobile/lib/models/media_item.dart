/// What a [MediaItem] is, which decides the provider and the detail layout.
enum MediaKind { movie, tv, anime }

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
}

/// One season row on a detail page.
class SeasonInfo {
  const SeasonInfo({
    required this.number,
    this.name,
    required this.episodeCount,
  });

  final int number;
  final String? name;
  final int episodeCount;
}

/// A title plus the detail-only fields.
class MediaDetail {
  const MediaDetail({
    required this.item,
    this.seasons = const <SeasonInfo>[],
    this.runtimeMinutes,
    this.tagline,
  });

  final MediaItem item;
  final List<SeasonInfo> seasons;
  final int? runtimeMinutes;
  final String? tagline;
}

/// A named horizontal row on the home page.
class MediaShelf {
  const MediaShelf({required this.title, required this.items});

  final String title;
  final List<MediaItem> items;
}
