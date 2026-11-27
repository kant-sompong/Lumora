import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:lumora_mobile/data/media_repository.dart';
import 'package:lumora_mobile/data/settings_store.dart';
import 'package:lumora_mobile/models/media_item.dart';

String fixture(String name) =>
    File('test/data/fixtures/$name').readAsStringSync();

Map<String, dynamic> fixtureJson(String name) =>
    jsonDecode(fixture(name)) as Map<String, dynamic>;

/// A GraphQL response: the fixture body wrapped in `data`, as AniList sends
/// it.
http.Response graphqlResponse(String name, {int status = 200}) =>
    http.Response.bytes(
      utf8.encode(jsonEncode(<String, dynamic>{'data': fixtureJson(name)})),
      status,
      headers: <String, String>{
        'content-type': 'application/json; charset=utf-8',
      },
    );

/// A 200 response with the fixture's UTF-8 body (Shōgun is not latin-1).
http.Response jsonResponse(String name, {int status = 200}) =>
    http.Response.bytes(
      utf8.encode(fixture(name)),
      status,
      headers: <String, String>{
        'content-type': 'application/json; charset=utf-8',
      },
    );

void main() {
  group('TMDB parsing', () {
    test('keeps titles and drops people, blank ids and blank names', () {
      final items = parseTmdbResults(
        fixtureJson('tmdb_trending_movie.json'),
        kindOverride: MediaKind.movie,
      );
      expect(items.map((i) => i.title), <String>[
        'Dune: Part Two',
        'The Batman',
      ]);
      expect(items.first.tmdbId, 693134);
      expect(items.first.id, 'tmdb:693134');
      expect(items.first.year, '2024');
      expect(items.first.rating, 8.2);
      expect(
        items.first.posterUrl,
        'https://image.tmdb.org/t/p/w500/1pdfLvkbY9ohJlCjQH2CZjjYVvJ.jpg',
      );
      // No genres list in a results payload, only genre_ids.
      expect(items.first.genres, isEmpty);
      // Missing vote_average and dates stay null rather than 0.
      expect(items[1].rating, isNull);
      expect(items[1].year, isNull);
      expect(items[1].posterUrl, isNull);
    });

    test('infers tv from a name and movie from a title', () {
      final items = parseTmdbResults(fixtureJson('tmdb_popular_tv.json'));
      expect(items.map((i) => i.kind), everyElement(MediaKind.tv));
      expect(items.first.title, 'Severance');
      expect(items.first.year, '2022');
    });

    test('movie detail keeps runtime, tagline and genres', () {
      final detail = parseTmdbDetail(
        fixtureJson('tmdb_movie_detail.json'),
        kind: MediaKind.movie,
      );
      expect(detail, isNotNull);
      expect(detail!.runtimeMinutes, 167);
      expect(detail.tagline, 'Long live the fighters.');
      expect(detail.item.genres, <String>['Science Fiction', 'Adventure']);
      expect(detail.seasons, isEmpty);
    });

    test('tv detail sorts seasons and reads episode_run_time', () {
      final detail = parseTmdbDetail(
        fixtureJson('tmdb_tv_detail.json'),
        kind: MediaKind.tv,
      );
      expect(detail!.runtimeMinutes, 50);
      expect(detail.seasons.map((s) => s.number), <int>[0, 1, 2]);
      expect(detail.seasons.last.name, 'Season 2');
      expect(detail.seasons.last.episodeCount, 10);
      expect(detail.item.kind, MediaKind.tv);
    });

    test('falls back to the tapped item when the payload is unusable', () {
      const item = MediaItem(
        id: 'tmdb:1',
        title: 'Fallback',
        kind: MediaKind.movie,
        tmdbId: 1,
      );
      final detail = parseTmdbDetail(
        <String, dynamic>{'id': 0},
        kind: MediaKind.movie,
        fallback: item,
      );
      expect(detail!.item.title, 'Fallback');
    });
  });

  group('AniList parsing', () {
    test('reads the page, prefers English titles and scores out of ten', () {
      final items = parseAniListPage(fixtureJson('anilist_trending.json'));
      expect(items, isNotEmpty);
      final first = items.first;
      expect(first.title, 'Frieren: Beyond Journey\'s End');
      expect(first.anilistId, 154587);
      expect(first.id, 'anilist:154587');
      expect(first.rating, 9.3);
      expect(first.year, '2023');
      expect(first.kind, MediaKind.anime);
      expect(first.overview, isNot(contains('<')));
    });

    test('detail carries the episode count as one season', () {
      final node =
          fixtureJson('anilist_media_detail.json')['Media']
              as Map<String, dynamic>;
      final detail = parseAniListDetail(node);
      expect(detail!.item.title, 'Attack on Titan');
      expect(detail.item.rating, 8.5);
      expect(detail.runtimeMinutes, 24);
      expect(detail.seasons.single.number, 1);
      expect(detail.seasons.single.episodeCount, 25);
      expect(detail.item.overview, 'Humanity fights & survives.\nMore.');
    });

    test('ignores a payload without a usable id', () {
      expect(parseAniListPage(<String, dynamic>{}), isEmpty);
      expect(parseAniListPage(<String, dynamic>{'Page': 'nope'}), isEmpty);
      expect(parseAniListMedia(<String, dynamic>{'id': 0}), isNull);
    });
  });

  group('FakeMediaRepository', () {
    const repo = FakeMediaRepository();

    test('home returns the four PC shelves', () async {
      final shelves = await repo.home();
      expect(shelves.map((s) => s.title), <String>[
        'Trending Now',
        'Movies',
        'TV Shows',
        'Anime',
      ]);
      expect(shelves.every((s) => s.items.isNotEmpty), isTrue);
    });

    test('search is case-insensitive and blank returns nothing', () async {
      expect((await repo.search('dUne')).single.title, 'Dune: Part Two');
      expect(await repo.search('   '), isEmpty);
      expect(await repo.search('nothing here'), isEmpty);
    });

    test('detail shapes movies, series and anime', () async {
      final movie = await repo.detail(FakeMediaRepository.items.first);
      expect(movie.seasons, isEmpty);
      expect(movie.runtimeMinutes, 166);

      final series = await repo.detail(
        FakeMediaRepository.items.firstWhere((i) => i.id == 'tmdb:95396'),
      );
      expect(series.seasons.length, 2);
      expect(series.seasons.fold<int>(0, (sum, s) => sum + s.episodeCount), 9);

      final anime = await repo.detail(
        FakeMediaRepository.items.firstWhere((i) => i.id == 'anilist:154587'),
      );
      expect(anime.seasons.single.episodeCount, 28);
      expect(anime.runtimeMinutes, 24);
    });
  });

  group('TmdbAniListRepository', () {
    test('without a key and without a fallback it asks for the key', () async {
      final repo = TmdbAniListRepository(AppSettings());
      expect(repo.home(), throwsA(isA<MissingApiKeyException>()));
      expect(
        repo.detail(
          const MediaItem(
            id: 'tmdb:1',
            title: 'Film',
            kind: MediaKind.movie,
            tmdbId: 1,
          ),
        ),
        throwsA(isA<MissingApiKeyException>()),
      );
    });

    test('a fallback stands in for the whole catalog offline', () async {
      final repo = TmdbAniListRepository(
        AppSettings(),
        fallback: const FakeMediaRepository(),
      );
      final shelves = await repo.home();
      expect(shelves.length, 4);
      expect((await repo.search('dune')).single.tmdbId, 693134);
    });

    test('home calls TMDB with the api key and AniList for anime', () async {
      final requests = <http.Request>[];
      final client = MockClient((request) async {
        requests.add(request);
        final path = request.url.path;
        if (path.contains('/trending/movie')) {
          return jsonResponse('tmdb_trending_movie.json');
        }
        if (path.contains('/trending/tv')) {
          return jsonResponse('tmdb_popular_tv.json');
        }
        if (path.endsWith('/movie/popular') || path.endsWith('/tv/popular')) {
          return jsonResponse('tmdb_popular_tv.json');
        }
        return graphqlResponse('anilist_trending.json');
      });

      final settings = AppSettings(tmdbApiKey: 'key123');
      final repo = TmdbAniListRepository(settings, client: client);
      final shelves = await repo.home();

      expect(shelves.map((s) => s.title), <String>[
        'Trending Now',
        'Movies',
        'TV Shows',
        'Anime',
      ]);
      final tmdbRequest = requests.firstWhere(
        (r) => r.url.path.contains('/trending/movie'),
      );
      expect(tmdbRequest.url.queryParameters['api_key'], 'key123');
      expect(tmdbRequest.url.path, '/3/trending/movie/week');
      final animeRequest = requests.firstWhere(
        (r) => r.url.host == 'graphql.anilist.co',
      );
      expect(animeRequest.method, 'POST');
      expect(animeRequest.body, contains('POPULARITY_DESC'));
    });

    test('a v4 token is sent as a bearer header', () async {
      final requests = <http.Request>[];
      final client = MockClient((request) async {
        requests.add(request);
        return jsonResponse('tmdb_popular_tv.json');
      });
      final repo = TmdbAniListRepository(
        AppSettings(tmdbApiKey: 'header.payload.signature'),
        client: client,
      );
      await repo.search('severance');

      final tmdb = requests.firstWhere(
        (r) => r.url.host == 'api.themoviedb.org',
      );
      expect(tmdb.url.queryParameters.containsKey('api_key'), isFalse);
      expect(tmdb.headers['Authorization'], startsWith('Bearer '));
    });

    test('search asks TMDB only and returns its results', () async {
      final requests = <http.Request>[];
      final client = MockClient((request) async {
        requests.add(request);
        return jsonResponse('tmdb_trending_movie.json');
      });
      final repo = TmdbAniListRepository(
        AppSettings(tmdbApiKey: 'key123'),
        client: client,
      );
      final results = await repo.search('dune');

      expect(requests, hasLength(1));
      expect(requests.single.url.path, '/3/search/multi');
      expect(requests.single.url.queryParameters['include_adult'], 'true');
      expect(results.map((item) => item.title), <String>[
        'Dune: Part Two',
        'The Batman',
      ]);
      expect(results.every((item) => item.kind != MediaKind.anime), isTrue);
    });

    test(
      'search without a key asks for one instead of trying AniList',
      () async {
        final requests = <http.Request>[];
        final repo = TmdbAniListRepository(
          AppSettings(),
          client: MockClient((request) async {
            requests.add(request);
            return graphqlResponse('anilist_trending.json');
          }),
        );

        expect(
          repo.search('attack on titan'),
          throwsA(isA<MissingApiKeyException>()),
        );
        expect(requests, isEmpty);
      },
    );

    test('a failing TMDB search raises the usual errors', () async {
      final rejected = TmdbAniListRepository(
        AppSettings(tmdbApiKey: 'bad'),
        client: MockClient((_) async => http.Response('{}', 401)),
      );
      expect(rejected.search('dune'), throwsA(isA<MissingApiKeyException>()));

      final broken = TmdbAniListRepository(
        AppSettings(tmdbApiKey: 'key'),
        client: MockClient((_) async => http.Response('nope', 500)),
      );
      expect(broken.search('dune'), throwsA(isA<NetworkException>()));
    });

    test('maps HTTP failures onto the exceptions the UI shows', () async {
      final rejected = TmdbAniListRepository(
        AppSettings(tmdbApiKey: 'bad'),
        client: MockClient((_) async => http.Response('{}', 401)),
      );
      expect(rejected.home(), throwsA(isA<MissingApiKeyException>()));

      final broken = TmdbAniListRepository(
        AppSettings(tmdbApiKey: 'key'),
        client: MockClient((_) async => http.Response('nope', 500)),
      );
      expect(broken.home(), throwsA(isA<NetworkException>()));

      final badJson = TmdbAniListRepository(
        AppSettings(tmdbApiKey: 'key'),
        client: MockClient((_) async => http.Response('<html>', 200)),
      );
      expect(badJson.home(), throwsA(isA<NetworkException>()));

      final limited = TmdbAniListRepository(
        AppSettings(tmdbApiKey: 'key'),
        client: MockClient((_) async => http.Response('{}', 429)),
      );
      expect(limited.home(), throwsA(isA<NetworkException>()));
    });

    test('anime detail posts the AniList id', () async {
      String? body;
      final repo = TmdbAniListRepository(
        AppSettings(),
        client: MockClient((request) async {
          body = request.body;
          // detail() unwraps data.Media, as the real GraphQL endpoint sends it.
          return http.Response.bytes(
            utf8.encode(
              jsonEncode(<String, dynamic>{
                'data': fixtureJson('anilist_media_detail.json'),
              }),
            ),
            200,
            headers: <String, String>{
              'content-type': 'application/json; charset=utf-8',
            },
          );
        }),
      );
      final detail = await repo.detail(
        const MediaItem(
          id: 'anilist:16498',
          title: 'Attack on Titan',
          kind: MediaKind.anime,
          anilistId: 16498,
        ),
      );
      expect(detail.seasons.single.episodeCount, 25);

      expect(body, contains('"id":16498'));
    });
  });

  group('adult flag and logos', () {
    test('TMDB search keeps adult titles and reads its own flag', () async {
      final requests = <http.Request>[];
      final client = MockClient((request) async {
        requests.add(request);
        if (request.url.host == 'graphql.anilist.co') {
          return graphqlResponse('anilist_trending.json');
        }
        return jsonResponse('tmdb_search_adult.json');
      });
      final repo = TmdbAniListRepository(
        AppSettings(tmdbApiKey: 'key123'),
        client: client,
      );

      final results = await repo.search('anything');

      final tmdb = requests.firstWhere(
        (request) => request.url.host == 'api.themoviedb.org',
      );
      expect(tmdb.url.queryParameters['include_adult'], 'true');

      final adult = results.firstWhere((item) => item.tmdbId == 900);
      final family = results.firstWhere((item) => item.tmdbId == 901);
      expect(adult.adult, isTrue);
      expect(family.adult, isFalse);
      expect(results.any((item) => item.title == 'Someone'), isFalse);
    });

    test('_best_logo prefers a PNG in the wanted language', () {
      final logos = fixtureJson('tmdb_images_logos.json')['logos'];

      expect(bestTmdbLogo(logos), 'https://image.tmdb.org/t/p/w500/en.png');
      expect(
        bestTmdbLogo(logos, language: 'ja'),
        'https://image.tmdb.org/t/p/w500/ja.png',
      );
      expect(
        bestTmdbLogo(logos, language: 'fr'),
        'https://image.tmdb.org/t/p/w500/en.png',
      );
      expect(bestTmdbLogo(null), isNull);
      expect(bestTmdbLogo(<dynamic>[]), isNull);
    });

    test('a TMDB detail fills logoUrl and caches it per title', () async {
      final requests = <http.Request>[];
      final client = MockClient((request) async {
        requests.add(request);
        if (request.url.path == '/3/movie/693134/images') {
          return jsonResponse('tmdb_images_logos.json');
        }
        if (request.url.path == '/3/movie/693134') {
          return jsonResponse('tmdb_movie_detail.json');
        }
        return http.Response('{}', 404);
      });
      final repo = TmdbAniListRepository(
        AppSettings(tmdbApiKey: 'key123'),
        client: client,
      );
      const item = MediaItem(
        id: 'tmdb:693134',
        title: 'Dune: Part Two',
        kind: MediaKind.movie,
        tmdbId: 693134,
        year: '2024',
      );

      final detail = await repo.detail(item);

      expect(detail.logoUrl, 'https://image.tmdb.org/t/p/w500/en.png');
      final images = requests
          .where((request) => request.url.path.endsWith('/images'))
          .toList();
      expect(images, hasLength(1));
      // `/images` asks for no language filter at all.
      expect(
        images.single.url.queryParameters.containsKey('language'),
        isFalse,
      );
      expect(images.single.url.queryParameters['api_key'], 'key123');

      await repo.detail(item);
      expect(
        requests
            .where((request) => request.url.path.endsWith('/images'))
            .length,
        1,
      );
    });

    test('an AniList title gets the logo of its exact TMDB title', () async {
      final client = MockClient((request) async {
        if (request.url.host == 'graphql.anilist.co') {
          return graphqlResponse('anilist_frieren_detail.json');
        }
        if (request.url.path == '/3/search/multi') {
          return jsonResponse('tmdb_search_anime_logo.json');
        }
        if (request.url.path == '/3/tv/209867/images') {
          return jsonResponse('tmdb_images_anime_logo.json');
        }
        return http.Response('{}', 404);
      });
      final repo = TmdbAniListRepository(
        AppSettings(tmdbApiKey: 'key123'),
        client: client,
      );

      final detail = await repo.detail(
        const MediaItem(
          id: 'anilist:154587',
          title: "Frieren: Beyond Journey's End",
          kind: MediaKind.anime,
          anilistId: 154587,
          year: '2023',
        ),
      );

      expect(detail.seasons.single.episodeCount, 28);
      expect(detail.logoUrl, 'https://image.tmdb.org/t/p/w500/frieren.png');
    });
  });
}
