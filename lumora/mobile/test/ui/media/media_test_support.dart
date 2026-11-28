import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'package:lumora_mobile/data/media_repository.dart';
import 'package:lumora_mobile/data/settings_store.dart';
import 'package:lumora_mobile/models/media_item.dart';
import 'package:lumora_mobile/theme/lumora_theme.dart';

const Color testAccent = Color(0xFF8557EA);

const MediaItem movieItem = MediaItem(
  id: 'movie:693134',
  title: 'Dune: Part Two',
  kind: MediaKind.movie,
  year: '2024',
  rating: 8.2,
  overview: 'Paul Atreides unites with the Fremen.',
  genres: ['Science Fiction', 'Adventure'],
  tmdbId: 693134,
);

const MediaItem tvItem = MediaItem(
  id: 'tv:95396',
  title: 'Severance',
  kind: MediaKind.tv,
  year: '2022',
  rating: 8.4,
  overview: 'Mark leads a team of office workers.',
  genres: ['Drama', 'Mystery'],
  tmdbId: 95396,
);

const MediaItem adultItem = MediaItem(
  id: 'movie:adult',
  title: 'Adult Feature',
  kind: MediaKind.movie,
  year: '2023',
  adult: true,
);

const MediaItem animeItem = MediaItem(
  id: 'anime:154587',
  title: 'Frieren',
  kind: MediaKind.anime,
  year: '2023',
  rating: 9.2,
  anilistId: 154587,
);

/// A repository with no network and counters, used by every widget test.
class TestRepository implements MediaRepository {
  TestRepository({
    this.failWith,
    this.shelves = const <MediaShelf>[
      MediaShelf(title: 'Trending Now', items: [movieItem, tvItem]),
      MediaShelf(title: 'TV Shows', items: [tvItem]),
    ],
    this.results = const <MediaItem>[movieItem, tvItem, animeItem],
    this.detailOverride,
    this.episodeOverride,
    this.episodesFailWith,
  });

  final Object? failWith;
  final List<MediaShelf> shelves;
  final List<MediaItem> results;
  final MediaDetail? detailOverride;

  /// Replaces every season's episodes when set; null lets the tests get the
  /// plain numbered list their season says it has.
  final List<EpisodeInfo>? episodeOverride;
  final Object? episodesFailWith;

  final List<String> queries = <String>[];
  final List<int> episodeSeasons = <int>[];
  int homeCalls = 0;
  int detailCalls = 0;
  int episodeCalls = 0;

  @override
  Future<List<MediaShelf>> home() async {
    homeCalls++;
    if (failWith != null) throw failWith!;
    return shelves;
  }

  @override
  Future<List<MediaItem>> search(String query) async {
    queries.add(query);
    if (failWith != null) throw failWith!;
    final q = query.toLowerCase();
    return results
        .where((item) => item.title.toLowerCase().contains(q))
        .toList();
  }

  @override
  Future<MediaDetail> detail(MediaItem item) async {
    detailCalls++;
    if (failWith != null) throw failWith!;
    if (detailOverride != null) return detailOverride!;
    if (item.kind == MediaKind.movie) {
      return MediaDetail(item: item, runtimeMinutes: 166);
    }
    return MediaDetail(
      item: item,
      seasons: const [
        SeasonInfo(number: 1, episodeCount: 3),
        SeasonInfo(number: 2, name: 'Season Two', episodeCount: 2),
      ],
    );
  }

  @override
  Future<List<EpisodeInfo>> episodes(MediaItem item, SeasonInfo season) async {
    episodeCalls++;
    episodeSeasons.add(season.number);
    if (episodesFailWith != null) throw episodesFailWith!;
    final override = episodeOverride;
    if (override != null) return override;
    return <EpisodeInfo>[
      for (var number = 1; number <= season.episodeCount; number++)
        EpisodeInfo(number: number),
    ];
  }
}

/// Wraps a page in the app's theme and the two providers it reads.
Widget mediaApp(Widget child, {MediaRepository? repository, AppSettings? settings}) {
  return MultiProvider(
    providers: [
      ChangeNotifierProvider<AppSettings>.value(
        value: settings ?? AppSettings(),
      ),
      Provider<MediaRepository>.value(
        value: repository ?? TestRepository(),
      ),
    ],
    child: MaterialApp(
      theme: LumoraTheme.dark(testAccent),
      home: child,
    ),
  );
}
