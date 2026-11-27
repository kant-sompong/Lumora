import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import '../models/media_item.dart';
import '../models/stream_provider.dart';
import 'search_collapse.dart';
import 'settings_store.dart';

/// The TMDB key is empty, so movies/TV metadata cannot be loaded.
class MissingApiKeyException implements Exception {
  const MissingApiKeyException([
    this.message = 'Add a TMDB API key in Settings → Providers.',
  ]);

  final String message;

  @override
  String toString() => 'MissingApiKeyException: $message';
}

/// A request failed, timed out, or returned something unusable.
class NetworkException implements Exception {
  const NetworkException(this.message, {this.statusCode});

  final String message;
  final int? statusCode;

  @override
  String toString() => 'NetworkException: $message';
}

/// Loads the Movies & TV catalog.
abstract class MediaRepository {
  /// Shelves for the home page, in PC order.
  Future<List<MediaShelf>> home();

  /// Titles matching [query]; empty for a blank query.
  Future<List<MediaItem>> search(String query);

  /// Full detail for [item], including seasons for TV/anime.
  Future<MediaDetail> detail(MediaItem item);
}

/// Canned catalog so the UI can be built and tested offline.
class FakeMediaRepository implements MediaRepository {
  const FakeMediaRepository();

  static const List<MediaItem> items = <MediaItem>[
    MediaItem(
      id: 'tmdb:693134',
      title: 'Dune: Part Two',
      kind: MediaKind.movie,
      tmdbId: 693134,
      posterUrl:
          'https://image.tmdb.org/t/p/w500/1pdfLvkbY9ohJlCjQH2CZjjYVvJ.jpg',
      backdropUrl:
          'https://image.tmdb.org/t/p/w780/xOMo8BRK7PfcJv9JCnx7s5hj0PX.jpg',
      year: '2024',
      rating: 8.2,
      overview:
          'Paul Atreides unites with the Fremen to wage war against the '
          'House Harkonnen.',
      genres: <String>['Science Fiction', 'Adventure'],
    ),
    MediaItem(
      id: 'tmdb:872585',
      title: 'Oppenheimer',
      kind: MediaKind.movie,
      tmdbId: 872585,
      posterUrl:
          'https://image.tmdb.org/t/p/w500/8Gxv8gSFCU0XGDykEGv7zR1n2ua.jpg',
      backdropUrl:
          'https://image.tmdb.org/t/p/w780/fm6KqXpk3M2HVveHwCrBSSBaO0V.jpg',
      year: '2023',
      rating: 8.1,
      overview:
          'The story of J. Robert Oppenheimer and the making of the atomic '
          'bomb.',
      genres: <String>['Drama', 'History'],
    ),
    MediaItem(
      id: 'tmdb:414906',
      title: 'The Batman',
      kind: MediaKind.movie,
      tmdbId: 414906,
      backdropUrl:
          'https://image.tmdb.org/t/p/w780/5P8SmMzSNYikXpxil6BYzJ16611.jpg',
      year: '2022',
      rating: 7.7,
      overview: 'A young Batman follows a trail of corruption in Gotham.',
      genres: <String>['Crime', 'Mystery'],
    ),
    MediaItem(
      id: 'tmdb:545611',
      title: 'Everything Everywhere All at Once',
      kind: MediaKind.movie,
      tmdbId: 545611,
      posterUrl:
          'https://image.tmdb.org/t/p/w500/u68AjlvlutfEIcpmbYpKcdi09ut.jpg',
      backdropUrl:
          'https://image.tmdb.org/t/p/w780/ss0Os3uWJAn12XBFw5tfTGc8fDP.jpg',
      year: '2022',
      rating: 7.8,
      overview: 'A laundromat owner discovers she can access parallel lives.',
      genres: <String>['Action', 'Adventure', 'Science Fiction'],
    ),
    MediaItem(
      id: 'tmdb:335984',
      title: 'Blade Runner 2049',
      kind: MediaKind.movie,
      tmdbId: 335984,
      posterUrl:
          'https://image.tmdb.org/t/p/w500/gajva2L0rPYkEWjzgFlBXCAVBE5.jpg',
      year: '2017',
      rating: 7.6,
      overview: 'A blade runner uncovers a secret that could upset society.',
      genres: <String>['Science Fiction', 'Drama'],
    ),
    MediaItem(
      id: 'tmdb:95396',
      title: 'Severance',
      kind: MediaKind.tv,
      tmdbId: 95396,
      posterUrl:
          'https://image.tmdb.org/t/p/w500/lFf6LLrQjYldcZItzOkGmMMigP7.jpg',
      backdropUrl:
          'https://image.tmdb.org/t/p/w780/u8M5jg1Q9rotijIOMSAKVCojx0.jpg',
      year: '2022',
      rating: 8.4,
      overview:
          'Office workers have their memories surgically split between work '
          'and personal lives.',
      genres: <String>['Drama', 'Mystery', 'Science Fiction'],
    ),
    MediaItem(
      id: 'tmdb:100088',
      title: 'The Last of Us',
      kind: MediaKind.tv,
      tmdbId: 100088,
      posterUrl:
          'https://image.tmdb.org/t/p/w500/uKvVjHNqB5VmOrdxqAt2F7J78ED.jpg',
      backdropUrl:
          'https://image.tmdb.org/t/p/w780/uDgy6hyPd82kOHh6I95FLtLnj6p.jpg',
      year: '2023',
      rating: 8.6,
      overview:
          'A smuggler escorts a teenage girl across a post-pandemic America.',
      genres: <String>['Drama', 'Action & Adventure'],
    ),
    MediaItem(
      id: 'tmdb:126308',
      title: 'Shōgun',
      kind: MediaKind.tv,
      tmdbId: 126308,
      backdropUrl:
          'https://image.tmdb.org/t/p/w780/5uV3f0lS9w7GZ6Qm4yYq3o5G2bK.jpg',
      year: '2024',
      rating: 8.6,
      overview:
          'A shipwrecked English sailor enters the power struggles of feudal '
          'Japan.',
      genres: <String>['Drama', 'War & Politics'],
    ),
    MediaItem(
      id: 'tmdb:83867',
      title: 'Andor',
      kind: MediaKind.tv,
      tmdbId: 83867,
      posterUrl:
          'https://image.tmdb.org/t/p/w500/59SVNwLfoMnZPPB6ukW6dlPxAdI.jpg',
      year: '2022',
      rating: 8.4,
      overview: 'Cassian Andor becomes a revolutionary in the early Rebellion.',
      genres: <String>['Drama', 'Science Fiction'],
    ),
    MediaItem(
      id: 'tmdb:94605',
      title: 'Arcane',
      kind: MediaKind.tv,
      tmdbId: 94605,
      posterUrl:
          'https://image.tmdb.org/t/p/w500/fqldf2t8ztc9aiwn3k6mlX3tvRT.jpg',
      year: '2021',
      rating: 8.7,
      overview: 'Two sisters end up on opposite sides of a city at war.',
      genres: <String>['Animation', 'Drama', 'Science Fiction'],
    ),
    MediaItem(
      id: 'anilist:154587',
      title: 'Frieren: Beyond Journey’s End',
      kind: MediaKind.anime,
      anilistId: 154587,
      posterUrl:
          'https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx154587-qQTzQnEJJ3jP.jpg',
      year: '2023',
      rating: 9.3,
      overview:
          'An elf mage retraces the journey of the party she once travelled '
          'with.',
      genres: <String>['Adventure', 'Drama', 'Fantasy'],
    ),
    MediaItem(
      id: 'anilist:16498',
      title: 'Attack on Titan',
      kind: MediaKind.anime,
      anilistId: 16498,
      posterUrl:
          'https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx16498-C6FPmWm59CyP.jpg',
      backdropUrl:
          'https://s4.anilist.co/file/anilistcdn/media/anime/banner/16498-8jpFCOcDmneX.jpg',
      year: '2013',
      rating: 8.5,
      overview: 'Humanity fights for survival against man-eating giants.',
      genres: <String>['Action', 'Drama', 'Fantasy'],
    ),
    MediaItem(
      id: 'anilist:113415',
      title: 'Jujutsu Kaisen',
      kind: MediaKind.anime,
      anilistId: 113415,
      posterUrl:
          'https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx113415-LHBAeoZDY27h.jpg',
      year: '2020',
      rating: 8.6,
      overview: 'A student swallows a cursed relic and joins a sorcery school.',
      genres: <String>['Action', 'Supernatural'],
    ),
    MediaItem(
      id: 'anilist:1',
      title: 'Cowboy Bebop',
      kind: MediaKind.anime,
      anilistId: 1,
      posterUrl:
          'https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx1-CXtrrkMpJ8Zq.png',
      year: '1998',
      rating: 8.8,
      overview: 'A crew of bounty hunters drifts through the solar system.',
      genres: <String>['Action', 'Drama', 'Science Fiction'],
    ),
    MediaItem(
      id: 'anilist:21',
      title: 'One Piece',
      kind: MediaKind.anime,
      anilistId: 21,
      backdropUrl:
          'https://s4.anilist.co/file/anilistcdn/media/anime/banner/21-wf37VakJmZqs.jpg',
      year: '1999',
      rating: 8.8,
      overview: 'Monkey D. Luffy searches for the world’s greatest treasure.',
      genres: <String>['Action', 'Adventure', 'Comedy'],
    ),
  ];

  static const Map<String, int> _episodeCounts = <String, int>{
    'tmdb:95396': 9,
    'tmdb:100088': 9,
    'tmdb:126308': 10,
    'tmdb:83867': 12,
    'tmdb:94605': 9,
    'anilist:154587': 28,
    'anilist:16498': 25,
    'anilist:113415': 24,
    'anilist:1': 26,
    'anilist:21': 1100,
  };

  static const Map<String, int> _seasonCounts = <String, int>{
    'tmdb:95396': 2,
    'tmdb:100088': 1,
    'tmdb:126308': 1,
    'tmdb:83867': 2,
    'tmdb:94605': 2,
  };

  @override
  Future<List<MediaShelf>> home() async {
    return <MediaShelf>[
      MediaShelf(
        title: 'Trending Now',
        items: _pick(<String>[
          'tmdb:693134',
          'tmdb:95396',
          'anilist:154587',
          'tmdb:872585',
          'tmdb:126308',
          'anilist:16498',
        ]),
      ),
      MediaShelf(
        title: 'Movies',
        items: _pick(<String>[
          'tmdb:872585',
          'tmdb:414906',
          'tmdb:545611',
          'tmdb:335984',
          'tmdb:693134',
        ]),
      ),
      MediaShelf(
        title: 'TV Shows',
        items: _pick(<String>[
          'tmdb:95396',
          'tmdb:100088',
          'tmdb:126308',
          'tmdb:83867',
          'tmdb:94605',
        ]),
      ),
      MediaShelf(
        title: 'Anime',
        items: _pick(<String>[
          'anilist:154587',
          'anilist:16498',
          'anilist:113415',
          'anilist:1',
          'anilist:21',
        ]),
      ),
    ];
  }

  @override
  Future<List<MediaItem>> search(String query) async {
    final needle = query.trim().toLowerCase();
    if (needle.isEmpty) {
      return const <MediaItem>[];
    }
    return items
        .where((item) => item.title.toLowerCase().contains(needle))
        .toList(growable: false);
  }

  @override
  Future<MediaDetail> detail(MediaItem item) async {
    final found = items.firstWhere(
      (candidate) => candidate.id == item.id,
      orElse: () => item,
    );
    if (found.kind == MediaKind.movie) {
      return MediaDetail(
        item: found,
        runtimeMinutes: 166,
        tagline: found.id == 'tmdb:693134' ? 'Long live the fighters.' : null,
      );
    }

    final total = _episodeCounts[found.id] ?? 0;
    final seasonCount = found.kind == MediaKind.anime
        ? 1
        : (_seasonCounts[found.id] ?? 1);
    final perSeason = seasonCount == 0 ? 0 : (total / seasonCount).ceil();
    return MediaDetail(
      item: found,
      runtimeMinutes: found.kind == MediaKind.anime ? 24 : 55,
      seasons: <SeasonInfo>[
        for (var index = 0; index < seasonCount; index++)
          SeasonInfo(
            number: index + 1,
            name: 'Season ${index + 1}',
            episodeCount: index == seasonCount - 1
                ? total - perSeason * (seasonCount - 1)
                : perSeason,
          ),
      ],
    );
  }

  static List<MediaItem> _pick(List<String> ids) {
    return <MediaItem>[
      for (final id in ids) items.firstWhere((item) => item.id == id),
    ];
  }
}

/// Real catalog: TMDB for movies/TV and AniList for anime.
///
/// [fallback] replaces the whole catalog when no TMDB key is configured, which
/// is how the app runs in a demo/offline build.
class TmdbAniListRepository implements MediaRepository {
  TmdbAniListRepository(
    this.settings, {
    this.fallback,
    http.Client? client,
    String tmdbBaseUrl = 'https://api.themoviedb.org/3',
    String anilistEndpoint = 'https://graphql.anilist.co',
  }) : _client = client ?? http.Client(),
       _tmdbBaseUrl = tmdbBaseUrl,
       _anilistEndpoint = anilistEndpoint;

  final AppSettings settings;
  final FakeMediaRepository? fallback;
  final http.Client _client;
  final String _tmdbBaseUrl;
  final String _anilistEndpoint;

  /// Wide art is cached per title, as `lgl/tmdb.py` caches it, so reopening a
  /// detail page asks TMDB nothing.
  final Map<String, String?> _logoCache = <String, String?>{};

  static const Duration _timeout = Duration(seconds: 20);

  static const String _anilistFields = '''
    id
    title { romaji english native }
    coverImage { extraLarge large }
    bannerImage
    startDate { year }
    averageScore
    description
    genres
    episodes
    duration
    format
  ''';

  /// Search also asks for the relations, which say whether an entry is a
  /// sequel of another anime.
  static const String _anilistSearchFields =
      '''
    $_anilistFields
    relations { edges { relationType node { id type format } } }
  ''';

  bool get _hasTmdbKey => settings.tmdbApiKey.trim().isNotEmpty;

  /// True when no key is configured and canned data should answer instead.
  bool get _usesFallback => !_hasTmdbKey && fallback != null;

  void dispose() => _client.close();

  @override
  Future<List<MediaShelf>> home() async {
    if (_usesFallback) {
      return fallback!.home();
    }
    if (!_hasTmdbKey) {
      throw const MissingApiKeyException();
    }

    // The PC home order is Trending Now (movies + shows), Movies, TV Shows,
    // then one Anime shelf (lgl/qt_media.py `_load_home` / `_load_anime`).
    final requests = <Future<MediaShelf>>[
      _trendingShelf(),
      _tmdbShelf('Movies', '/movie/popular', MediaKind.movie),
      _tmdbShelf('TV Shows', '/tv/popular', MediaKind.tv),
      _animeShelf('Anime', 'POPULARITY_DESC'),
    ];

    final results = await Future.wait(requests.map(_captureShelf));
    final shelves = <MediaShelf>[];
    Object? firstError;
    for (final result in results) {
      firstError ??= result.error;
      final shelf = result.shelf;
      if (shelf != null && shelf.items.isNotEmpty) {
        shelves.add(shelf);
      }
    }

    if (shelves.isEmpty) {
      if (firstError != null) {
        throw firstError;
      }
      throw const NetworkException('No shelves could be loaded.');
    }
    return shelves;
  }

  @override
  Future<List<MediaItem>> search(String query) async {
    final needle = query.trim();
    if (needle.isEmpty) {
      return const <MediaItem>[];
    }
    if (_usesFallback) {
      return fallback!.search(needle);
    }

    var tmdbHits = const <SearchCandidate>[];
    Object? tmdbError;
    if (_hasTmdbKey) {
      try {
        tmdbHits = await _searchTmdb(needle);
      } catch (error) {
        tmdbError = error;
      }
    }

    var animeHits = const <SearchCandidate>[];
    Object? animeError;
    try {
      animeHits = await _searchAniList(needle);
    } catch (error) {
      animeError = error;
    }

    // AniList lists each season, film and OVA separately, so the merged list
    // is collapsed to one entry per series before it reaches the UI.
    final results = collapseSearchResults(tmdb: tmdbHits, anime: animeHits);
    if (results.isEmpty) {
      if (tmdbError != null) {
        throw tmdbError;
      }
      if (animeError != null) {
        throw animeError;
      }
    }
    return results;
  }

  @override
  Future<MediaDetail> detail(MediaItem item) async {
    if (item.kind == MediaKind.anime) {
      if (_usesFallback) {
        return fallback!.detail(item);
      }
      final id = item.anilistId ?? _idFromString(item.id);
      if (id == null || id <= 0) {
        throw MissingMediaIdException('AniList', item.title, 'AniList');
      }
      final data = await _postAniList(
        'query (\$id: Int) { Media(id: \$id, type: ANIME) { $_anilistFields } }',
        <String, dynamic>{'id': id},
      );
      final media = data['Media'];
      if (media is! Map) {
        throw const NetworkException('AniList returned no title.');
      }
      final detail = parseAniListDetail(_asMap(media), fallback: item);
      if (detail == null) {
        throw const NetworkException('AniList returned an unusable title.');
      }
      return MediaDetail(
        item: detail.item,
        seasons: detail.seasons,
        runtimeMinutes: detail.runtimeMinutes,
        tagline: detail.tagline,
        // Anime has no TMDB id, so the desktop looks its logo up by exact
        // title (`lgl/qt_media.py` `_fetch_anime_images`).
        logoUrl: await _animeLogo(item),
      );
    }

    if (_usesFallback) {
      return fallback!.detail(item);
    }
    if (!_hasTmdbKey) {
      throw const MissingApiKeyException();
    }
    final id = item.tmdbId ?? _idFromString(item.id);
    if (id == null || id <= 0) {
      throw MissingMediaIdException('TMDB', item.title, 'TMDB');
    }
    final path = item.kind == MediaKind.tv ? '/tv/$id' : '/movie/$id';
    final json = await _getJson(_tmdbUri(path));
    final detail = parseTmdbDetail(json, kind: item.kind, fallback: item);
    if (detail == null) {
      throw const NetworkException('TMDB returned an unusable title.');
    }
    return MediaDetail(
      item: detail.item,
      seasons: detail.seasons,
      runtimeMinutes: detail.runtimeMinutes,
      tagline: detail.tagline,
      logoUrl: await _tmdbLogo(item.kind, id),
    );
  }

  /// The title's own logo from TMDB's `/images` (best effort: wide art is
  /// decoration, so a failure never stops a detail page from opening).
  Future<String?> _tmdbLogo(MediaKind kind, int id) async {
    final key = '${kind.name}:$id';
    if (_logoCache.containsKey(key)) {
      return _logoCache[key];
    }
    if (!_hasTmdbKey) {
      return null;
    }
    final path = kind == MediaKind.tv ? '/tv/$id/images' : '/movie/$id/images';
    try {
      // No language filter: TMDB would drop every logo tagged differently.
      final json = await _getJson(_tmdbUriWithoutLanguage(path));
      final logo = bestTmdbLogo(json['logos'], language: 'en');
      _logoCache[key] = logo;
      return logo;
    } on Exception {
      return null;
    }
  }

  /// Anime titles come from AniList and have no TMDB id, so one exact-title
  /// search finds the logo, as `lgl/tmdb.py` `tmdb_images_for_title` does.
  Future<String?> _animeLogo(MediaItem item) async {
    final wanted = _normaliseTitle(item.title);
    if (wanted.isEmpty || !_hasTmdbKey) {
      return null;
    }
    final key = 'anime:$wanted:${item.year ?? ''}';
    if (_logoCache.containsKey(key)) {
      return _logoCache[key];
    }
    try {
      final json = await _getJson(
        _tmdbUri('/search/multi', <String, String>{
          'query': item.title,
          'include_adult': 'true',
          'page': '1',
        }),
      );
      final wantedYear = int.tryParse(item.year ?? '');
      // Anime is a series or a film; without MAL's media type the TV side is
      // where almost every one lives (`lgl/qt_media.py` `_anime_tmdb_kind`).
      for (final found in parseTmdbResults(json)) {
        if (found.kind != MediaKind.tv) {
          continue;
        }
        if (_normaliseTitle(found.title) != wanted) {
          continue;
        }
        final foundYear = int.tryParse(found.year ?? '');
        if (wantedYear != null &&
            foundYear != null &&
            (wantedYear - foundYear).abs() > 1) {
          continue;
        }
        final id = found.tmdbId;
        if (id == null) {
          continue;
        }
        final logo = await _tmdbLogo(MediaKind.tv, id);
        _logoCache[key] = logo;
        return logo;
      }
      _logoCache[key] = null;
      return null;
    } on Exception {
      return null;
    }
  }

  /// PC "Trending Now": the week's trending movies followed by trending TV,
  /// capped at 20 titles.
  Future<MediaShelf> _trendingShelf() async {
    final responses = await Future.wait(<Future<Map<String, dynamic>>>[
      _getJson(_tmdbUri('/trending/movie/week')),
      _getJson(_tmdbUri('/trending/tv/week')),
    ]);
    final items = <MediaItem>[
      ...parseTmdbResults(responses[0], kindOverride: MediaKind.movie),
      ...parseTmdbResults(responses[1], kindOverride: MediaKind.tv),
    ];
    return MediaShelf(
      title: 'Trending Now',
      items: items.take(20).toList(growable: false),
    );
  }

  Future<MediaShelf> _tmdbShelf(
    String title,
    String path,
    MediaKind kind,
  ) async {
    final json = await _getJson(_tmdbUri(path, <String, String>{'page': '1'}));
    return MediaShelf(
      title: title,
      items: parseTmdbResults(json, kindOverride: kind),
    );
  }

  Future<MediaShelf> _animeShelf(String title, String sort) async {
    final data = await _postAniList(
      'query (\$page: Int) { Page(page: \$page, perPage: 24) { '
      'media(type: ANIME, sort: $sort, isAdult: false) { $_anilistFields } } }',
      <String, dynamic>{'page': 1},
    );
    return MediaShelf(title: title, items: parseAniListPage(data));
  }

  Future<List<SearchCandidate>> _searchTmdb(String query) async {
    final json = await _getJson(
      _tmdbUri('/search/multi', <String, String>{
        'query': query,
        // The PC searches with adult titles included and badges the results
        // TMDB flags (`lgl/tmdb.py` `INCLUDE_ADULT`).
        'include_adult': 'true',
        'page': '1',
      }),
    );
    return tmdbSearchCandidates(json);
  }

  Future<List<SearchCandidate>> _searchAniList(String query) async {
    final data = await _postAniList(
      'query (\$search: String) { Page(page: 1, perPage: 24) { '
      'media(type: ANIME, search: \$search, sort: SEARCH_MATCH, '
      'isAdult: false) { $_anilistSearchFields } } }',
      <String, dynamic>{'search': query},
    );
    return aniListSearchCandidates(data);
  }

  Uri _tmdbUri(String path, [Map<String, String>? extra]) {
    final params = <String, String>{'language': 'en-US', ...?extra};
    if (!_isV4Token) {
      params['api_key'] = settings.tmdbApiKey.trim();
    }
    return Uri.parse('$_tmdbBaseUrl$path').replace(queryParameters: params);
  }

  /// A TMDB read with no `language` parameter; only `/images` asks for that.
  Uri _tmdbUriWithoutLanguage(String path) {
    final base = Uri.parse('$_tmdbBaseUrl$path');
    if (_isV4Token) {
      return base;
    }
    return base.replace(
      queryParameters: <String, String>{'api_key': settings.tmdbApiKey.trim()},
    );
  }

  bool get _isV4Token => settings.tmdbApiKey.trim().contains('.');

  Map<String, String> get _tmdbHeaders {
    if (_isV4Token) {
      return <String, String>{
        'Accept': 'application/json',
        'Authorization': 'Bearer ${settings.tmdbApiKey.trim()}',
      };
    }
    return const <String, String>{'Accept': 'application/json'};
  }

  Future<Map<String, dynamic>> _getJson(Uri uri) async {
    try {
      final response = await _client
          .get(uri, headers: _tmdbHeaders)
          .timeout(_timeout);
      if (response.statusCode == 401 || response.statusCode == 403) {
        throw const MissingApiKeyException('TMDB rejected the API key.');
      }
      if (response.statusCode != 200) {
        throw NetworkException(
          'TMDB request failed (${response.statusCode}).',
          statusCode: response.statusCode,
        );
      }
      final decoded = jsonDecode(response.body);
      if (decoded is! Map) {
        throw const NetworkException('TMDB returned an unexpected response.');
      }
      return _asMap(decoded);
    } on MissingApiKeyException {
      rethrow;
    } on NetworkException {
      rethrow;
    } on TimeoutException {
      throw const NetworkException('The TMDB request timed out.');
    } on FormatException {
      throw const NetworkException('TMDB returned invalid JSON.');
    } on http.ClientException {
      throw const NetworkException('Could not reach TMDB.');
    } on Exception catch (error) {
      throw NetworkException('Could not reach TMDB: $error');
    }
  }

  Future<Map<String, dynamic>> _postAniList(
    String query,
    Map<String, dynamic> variables,
  ) async {
    try {
      final response = await _client
          .post(
            Uri.parse(_anilistEndpoint),
            headers: const <String, String>{
              'Accept': 'application/json',
              'Content-Type': 'application/json',
            },
            body: jsonEncode(<String, dynamic>{
              'query': query,
              'variables': variables,
            }),
          )
          .timeout(_timeout);
      if (response.statusCode == 429) {
        throw const NetworkException(
          'AniList is rate limiting requests. Try again shortly.',
          statusCode: 429,
        );
      }
      if (response.statusCode != 200) {
        throw NetworkException(
          'AniList request failed (${response.statusCode}).',
          statusCode: response.statusCode,
        );
      }
      final decoded = jsonDecode(response.body);
      if (decoded is! Map) {
        throw const NetworkException(
          'AniList returned an unexpected response.',
        );
      }
      final map = _asMap(decoded);
      final errors = map['errors'];
      if (errors is List && errors.isNotEmpty) {
        final first = errors.first;
        final message = first is Map ? _asString(first['message']) : null;
        throw NetworkException(message ?? 'AniList returned an error.');
      }
      final data = map['data'];
      if (data is! Map) {
        throw const NetworkException('AniList returned no data.');
      }
      return _asMap(data);
    } on NetworkException {
      rethrow;
    } on TimeoutException {
      throw const NetworkException('The AniList request timed out.');
    } on FormatException {
      throw const NetworkException('AniList returned invalid JSON.');
    } on http.ClientException {
      throw const NetworkException('Could not reach AniList.');
    } on Exception catch (error) {
      throw NetworkException('Could not reach AniList: $error');
    }
  }

  static Future<_ShelfResult> _captureShelf(Future<MediaShelf> future) async {
    try {
      return _ShelfResult(shelf: await future, error: null);
    } catch (error) {
      return _ShelfResult(shelf: null, error: error);
    }
  }
}

class _ShelfResult {
  const _ShelfResult({required this.shelf, required this.error});

  final MediaShelf? shelf;
  final Object? error;
}

// ---------------------------------------------------------------------------
// Parsers (public so fixtures can be tested without any network access).
// ---------------------------------------------------------------------------

List<MediaItem> parseTmdbResults(
  Map<String, dynamic> json, {
  MediaKind? kindOverride,
}) {
  final raw = json['results'];
  if (raw is! List) {
    return const <MediaItem>[];
  }
  final out = <MediaItem>[];
  for (final entry in raw) {
    if (entry is! Map) {
      continue;
    }
    final item = parseTmdbItem(_asMap(entry), kindOverride: kindOverride);
    if (item != null) {
      out.add(item);
    }
  }
  return out;
}

/// TMDB search hits with every name TMDB knows them by, so a duplicate of an
/// AniList entry can be spotted by either spelling.
List<SearchCandidate> tmdbSearchCandidates(Map<String, dynamic> json) {
  final raw = json['results'];
  if (raw is! List) {
    return const <SearchCandidate>[];
  }
  final out = <SearchCandidate>[];
  for (final entry in raw) {
    if (entry is! Map) {
      continue;
    }
    final map = _asMap(entry);
    final item = parseTmdbItem(map);
    if (item == null) {
      continue;
    }
    final titles = <String>[item.title];
    for (final key in const <String>[
      'name',
      'original_name',
      'title',
      'original_title',
    ]) {
      final title = _asString(map[key]);
      if (title != null) {
        titles.add(title);
      }
    }
    out.add(SearchCandidate(item: item, titles: titles));
  }
  return out;
}

MediaItem? parseTmdbItem(Map<String, dynamic> raw, {MediaKind? kindOverride}) {
  // Multi-search mixes people in; never render them as titles, even when the
  // caller forces a kind for an endpoint.
  if (_asString(raw['media_type']) == 'person') {
    return null;
  }
  final kind = kindOverride ?? _tmdbKind(raw);
  if (kind == null) {
    return null;
  }
  final id = _asInt(raw['id']);
  if (id == null || id <= 0) {
    return null;
  }
  final title = _firstNonEmpty(<dynamic>[
    kind == MediaKind.tv ? raw['name'] : raw['title'],
    raw['name'],
    raw['title'],
    raw['original_title'],
    raw['original_name'],
  ]);
  if (title == null) {
    return null;
  }

  return MediaItem(
    id: 'tmdb:$id',
    title: title,
    kind: kind,
    tmdbId: id,
    posterUrl: _tmdbImage(raw['poster_path'], 'w500'),
    backdropUrl: _tmdbImage(raw['backdrop_path'], 'w780'),
    year: _year(raw['release_date'] ?? raw['first_air_date'] ?? raw['year']),
    rating: _rating(raw['vote_average']),
    overview: _asString(raw['overview']),
    genres: _genreNames(raw['genres']),
    adult: raw['adult'] == true,
  );
}

MediaDetail? parseTmdbDetail(
  Map<String, dynamic> json, {
  required MediaKind kind,
  MediaItem? fallback,
}) {
  var item = parseTmdbItem(json, kindOverride: kind);
  if (item == null && fallback != null) {
    item = fallback;
  }
  if (item == null) {
    return null;
  }

  final runtime =
      _asInt(json['runtime']) ?? _firstInt(json['episode_run_time']);
  final tagline = _asString(json['tagline']);
  return MediaDetail(
    item: item,
    runtimeMinutes: runtime != null && runtime > 0 ? runtime : null,
    tagline: tagline == null || tagline.isEmpty ? null : tagline,
    seasons: _seasons(json['seasons']),
  );
}

List<MediaItem> parseAniListPage(Map<String, dynamic> data) {
  final page = data['Page'];
  if (page is! Map) {
    return const <MediaItem>[];
  }
  final raw = _asMap(page)['media'];
  if (raw is! List) {
    return const <MediaItem>[];
  }
  final out = <MediaItem>[];
  for (final entry in raw) {
    if (entry is! Map) {
      continue;
    }
    final item = parseAniListMedia(_asMap(entry));
    if (item != null) {
      out.add(item);
    }
  }
  return out;
}

/// AniList search hits with their format and whether another anime is their
/// prequel (the search query asks for `relations`).
List<SearchCandidate> aniListSearchCandidates(Map<String, dynamic> data) {
  final page = data['Page'];
  if (page is! Map) {
    return const <SearchCandidate>[];
  }
  final raw = _asMap(page)['media'];
  if (raw is! List) {
    return const <SearchCandidate>[];
  }
  final out = <SearchCandidate>[];
  for (final entry in raw) {
    if (entry is! Map) {
      continue;
    }
    final map = _asMap(entry);
    final item = parseAniListMedia(map);
    if (item == null) {
      continue;
    }
    final titles = <String>[item.title];
    final titleMap = map['title'];
    if (titleMap is Map) {
      final names = _asMap(titleMap);
      for (final key in const <String>['romaji', 'english', 'native']) {
        final title = _asString(names[key]);
        if (title != null) {
          titles.add(title);
        }
      }
    }
    out.add(
      SearchCandidate(
        item: item,
        titles: titles,
        format: _asString(map['format']),
        hasPrequel: _hasAnimePrequel(map['relations']),
      ),
    );
  }
  return out;
}

/// True when any relation edge points at an anime that came before this one.
bool _hasAnimePrequel(dynamic relations) {
  if (relations is! Map) {
    return false;
  }
  final edges = _asMap(relations)['edges'];
  if (edges is! List) {
    return false;
  }
  for (final edge in edges) {
    if (edge is! Map) {
      continue;
    }
    final map = _asMap(edge);
    final type = (_asString(map['relationType']) ?? '').toUpperCase();
    if (type != 'PREQUEL') {
      continue;
    }
    final node = map['node'];
    if (node is Map &&
        (_asString(_asMap(node)['type']) ?? '').toUpperCase() == 'ANIME') {
      return true;
    }
  }
  return false;
}

MediaItem? parseAniListMedia(Map<String, dynamic> raw) {
  final id = _asInt(raw['id']);
  if (id == null || id <= 0) {
    return null;
  }
  final titleMap = raw['title'];
  String? title;
  if (titleMap is Map) {
    final titles = _asMap(titleMap);
    title = _firstNonEmpty(<dynamic>[
      titles['english'],
      titles['romaji'],
      titles['native'],
    ]);
  }
  if (title == null) {
    return null;
  }

  final cover = raw['coverImage'];
  String? poster;
  if (cover is Map) {
    final images = _asMap(cover);
    poster = _firstNonEmpty(<dynamic>[
      _asString(images['extraLarge']),
      _asString(images['large']),
      _asString(images['medium']),
    ]);
  }

  final start = raw['startDate'];
  int? year;
  if (start is Map) {
    year = _asInt(_asMap(start)['year']);
  }
  year ??= _asInt(raw['seasonYear']);

  final score = _asInt(raw['averageScore']);
  return MediaItem(
    id: 'anilist:$id',
    title: title,
    kind: MediaKind.anime,
    anilistId: id,
    posterUrl: poster,
    backdropUrl: _asString(raw['bannerImage']),
    year: year?.toString(),
    rating: score == null ? null : score / 10,
    overview: _cleanAniListText(_asString(raw['description'])),
    genres: _stringList(raw['genres']),
  );
}

MediaDetail? parseAniListDetail(
  Map<String, dynamic> raw, {
  MediaItem? fallback,
}) {
  var item = parseAniListMedia(raw);
  if (item == null && fallback != null) {
    item = fallback;
  }
  if (item == null) {
    return null;
  }

  final episodes = _asInt(raw['episodes']) ?? 0;
  final duration = _asInt(raw['duration']);
  return MediaDetail(
    item: item,
    runtimeMinutes: duration != null && duration > 0 ? duration : null,
    seasons: episodes > 0
        ? <SeasonInfo>[SeasonInfo(number: 1, episodeCount: episodes)]
        : const <SeasonInfo>[],
  );
}

// ---------------------------------------------------------------------------
// Small defensive helpers.
// ---------------------------------------------------------------------------

/// The best logo among a TMDB `/images` `logos` list
/// (`lgl/tmdb.py` `_best_logo`).
///
/// Only PNG and SVG logos are used; PNG wins within a language, then the
/// best-voted one. Language only decides which logo is preferred: [language],
/// then English, then a language-neutral logo, then any logo at all. A title
/// with only a foreign-language logo still gets that logo.
String? bestTmdbLogo(dynamic raw, {String? language}) {
  if (raw is! List) {
    return null;
  }
  final wanted = _logoLanguage(language);
  final order = <String?>[if (wanted != null) wanted, 'en', null];
  final ranked = <String?, List<Map<String, dynamic>>>{};
  final usable = <Map<String, dynamic>>[];

  for (final entry in raw) {
    if (entry is! Map) {
      continue;
    }
    final map = _asMap(entry);
    final path = (_asString(map['file_path']) ?? '').toLowerCase();
    if (!path.endsWith('.png') && !path.endsWith('.svg')) {
      continue;
    }
    usable.add(map);
    ranked
        .putIfAbsent(
          _logoLanguage(map['iso_639_1']),
          () => <Map<String, dynamic>>[],
        )
        .add(map);
  }

  for (final code in order) {
    final entries = ranked[code];
    if (entries != null && entries.isNotEmpty) {
      return _tmdbImage(_bestLogoEntry(entries)['file_path'], 'w500');
    }
  }
  if (usable.isNotEmpty) {
    return _tmdbImage(_bestLogoEntry(usable)['file_path'], 'w500');
  }
  return null;
}

Map<String, dynamic> _bestLogoEntry(List<Map<String, dynamic>> entries) {
  var best = entries.first;
  for (final entry in entries.skip(1)) {
    if (_logoPreferred(entry, best)) {
      best = entry;
    }
  }
  return best;
}

bool _logoPreferred(Map<String, dynamic> candidate, Map<String, dynamic> best) {
  final candidatePng = (_asString(candidate['file_path']) ?? '')
      .toLowerCase()
      .endsWith('.png');
  final bestPng = (_asString(best['file_path']) ?? '').toLowerCase().endsWith(
    '.png',
  );
  if (candidatePng != bestPng) {
    return candidatePng;
  }
  return _logoVotes(candidate) > _logoVotes(best);
}

double _logoVotes(Map<String, dynamic> entry) =>
    _asDouble(entry['vote_average']) ?? 0.0;

/// A TMDB language tag reduced to a two-letter code, or null.
String? _logoLanguage(dynamic raw) {
  final text = _asString(raw)?.toLowerCase();
  if (text == null || text == 'null' || text == 'none') {
    return null;
  }
  return text.length >= 2 ? text.substring(0, 2) : text;
}

String _normaliseTitle(String value) {
  return value.toLowerCase().replaceAll(RegExp('[^0-9a-z]+'), ' ').trim();
}

const String _tmdbImageBase = 'https://image.tmdb.org/t/p';

MediaKind? _tmdbKind(Map<String, dynamic> raw) {
  final type = _asString(raw['media_type'])?.toLowerCase();
  if (type == 'movie') {
    return MediaKind.movie;
  }
  if (type == 'tv') {
    return MediaKind.tv;
  }
  if (type == 'person') {
    return null;
  }
  final hasTitle = _asString(raw['title']) != null;
  final hasName = _asString(raw['name']) != null;
  if (!hasTitle && hasName) {
    return MediaKind.tv;
  }
  if (hasTitle) {
    return MediaKind.movie;
  }
  return null;
}

List<SeasonInfo> _seasons(dynamic raw) {
  if (raw is! List) {
    return const <SeasonInfo>[];
  }
  final seasons = <SeasonInfo>[];
  for (final entry in raw) {
    if (entry is! Map) {
      continue;
    }
    final season = _asMap(entry);
    final number = _asInt(season['season_number']);
    if (number == null) {
      continue;
    }
    seasons.add(
      SeasonInfo(
        number: number,
        name: _asString(season['name']),
        episodeCount: _asInt(season['episode_count']) ?? 0,
      ),
    );
  }
  seasons.sort((a, b) => a.number.compareTo(b.number));
  return seasons;
}

String? _tmdbImage(dynamic path, String size) {
  final raw = _asString(path);
  if (raw == null || raw.isEmpty) {
    return null;
  }
  if (raw.startsWith('http://') || raw.startsWith('https://')) {
    return raw;
  }
  return '$_tmdbImageBase/$size$raw';
}

String? _year(dynamic raw) {
  final text = _asString(raw);
  if (text == null || text.length < 4) {
    return null;
  }
  final year = text.substring(0, 4);
  return int.tryParse(year) == null ? null : year;
}

double? _rating(dynamic raw) {
  final value = _asDouble(raw);
  if (value == null || value <= 0) {
    return null;
  }
  return value;
}

List<String> _genreNames(dynamic raw) {
  if (raw is! List) {
    return const <String>[];
  }
  final out = <String>[];
  for (final entry in raw) {
    if (entry is Map) {
      final name = _asString(_asMap(entry)['name']);
      if (name != null && name.isNotEmpty) {
        out.add(name);
      }
    }
  }
  return out;
}

List<String> _stringList(dynamic raw) {
  if (raw is! List) {
    return const <String>[];
  }
  final out = <String>[];
  for (final entry in raw) {
    final text = _asString(entry);
    if (text != null && text.isNotEmpty) {
      out.add(text);
    }
  }
  return out;
}

String? _cleanAniListText(String? raw) {
  if (raw == null) {
    return null;
  }
  var text = raw.replaceAll(RegExp(r'<br\s*/?>', caseSensitive: false), '\n');
  text = text.replaceAll(RegExp('<[^>]*>'), ' ');
  text = text
      .replaceAll('&amp;', '&')
      .replaceAll('&quot;', '"')
      .replaceAll('&#039;', "'")
      .replaceAll('&apos;', "'")
      .replaceAll('&lt;', '<')
      .replaceAll('&gt;', '>')
      .replaceAll('&nbsp;', ' ');
  text = text.replaceAll(RegExp(r'[ \t]+'), ' ');
  text = text.replaceAll(RegExp(r'\n{3,}'), '\n\n');
  text = text.trim();
  return text.isEmpty ? null : text;
}

Map<String, dynamic> _asMap(Map<dynamic, dynamic> raw) {
  return raw.map((key, value) => MapEntry(key.toString(), value));
}

String? _asString(dynamic raw) {
  if (raw is String) {
    final trimmed = raw.trim();
    return trimmed.isEmpty ? null : trimmed;
  }
  if (raw is num) {
    return raw.toString();
  }
  return null;
}

int? _asInt(dynamic raw) {
  if (raw is int) {
    return raw;
  }
  if (raw is num) {
    return raw.toInt();
  }
  if (raw is String) {
    return int.tryParse(raw.trim());
  }
  return null;
}

double? _asDouble(dynamic raw) {
  if (raw is num) {
    return raw.toDouble();
  }
  if (raw is String) {
    return double.tryParse(raw.trim());
  }
  return null;
}

String? _firstNonEmpty(List<dynamic> values) {
  for (final value in values) {
    final text = _asString(value);
    if (text != null) {
      return text;
    }
  }
  return null;
}

int? _firstInt(dynamic raw) {
  if (raw is! List || raw.isEmpty) {
    return null;
  }
  return _asInt(raw.first);
}

int? _idFromString(String id) {
  final tail = id.contains(':') ? id.split(':').last : id;
  return int.tryParse(tail);
}
