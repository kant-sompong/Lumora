import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:lumora_mobile/data/anime_identity.dart';
import 'package:lumora_mobile/data/mal_client.dart';
import 'package:lumora_mobile/data/settings_store.dart';
import 'package:lumora_mobile/models/media_item.dart';
import 'package:shared_preferences/shared_preferences.dart';

String fixture(String name) =>
    File('test/data/fixtures/$name').readAsStringSync();

http.Response jsonResponse(String name, {int status = 200}) =>
    http.Response.bytes(
      utf8.encode(fixture(name)),
      status,
      headers: <String, String>{
        'content-type': 'application/json; charset=utf-8',
      },
    );

MockClient malClient({
  String search = 'mal_search_empty.json',
  Map<int, String> details = const <int, String>{},
  int status = 200,
  List<http.Request>? log,
}) {
  return MockClient((request) async {
    log?.add(request);
    if (status != 200) {
      return http.Response('{}', status);
    }
    final path = request.url.path;
    if (path == '/v2/anime') {
      return jsonResponse(search);
    }
    final match = RegExp(r'^/v2/anime/(\d+)$').firstMatch(path);
    if (match != null) {
      final name = details[int.parse(match.group(1)!)];
      if (name == null) {
        return http.Response('{"message":"Not Found"}', 404);
      }
      return jsonResponse(name);
    }
    return http.Response('{}', 404);
  });
}

MockClient aniListClient({
  Map<int, String> idmal = const <int, String>{},
  Map<int, String> reverse = const <int, String>{},
  String search = 'anilist_search_fmab.json',
  bool unavailable = false,
  List<Map<String, dynamic>>? log,
}) {
  return MockClient((request) async {
    if (unavailable) {
      return http.Response('{}', 503);
    }
    final body = jsonDecode(request.body) as Map<String, dynamic>;
    final query = body['query'] as String;
    final variables = body['variables'] as Map<String, dynamic>;
    log?.add(<String, dynamic>{'query': query, 'variables': variables});
    if (query.contains('Page(')) {
      return jsonResponse(search);
    }
    if (query.contains('idMal:')) {
      final id = variables['malId'] as int;
      return jsonResponse(idmal[id] ?? 'anilist_idmal_none.json');
    }
    final id = variables['id'] as int;
    return jsonResponse(reverse[id] ?? 'anilist_reverse_none.json');
  });
}

const MediaItem _brotherhood = MediaItem(
  id: 'tmdb:1',
  title: 'Fullmetal Alchemist: Brotherhood',
  kind: MediaKind.tv,
  tmdbId: 1,
  year: '2009',
);

AnimeIdentityService service({
  required MalClient mal,
  http.Client? anilist,
  DateTime Function()? clock,
  Future<void> Function(Duration duration)? sleep,
  Duration interval = Duration.zero,
}) {
  return AnimeIdentityService(
    AppSettings(malClientId: 'client-secret'),
    malClient: mal,
    client: anilist ?? aniListClient(),
    clock: clock,
    sleep: sleep,
    malMinInterval: interval,
  );
}

Future<Map<String, dynamic>> storedRecord(String key) async {
  final prefs = await SharedPreferences.getInstance();
  final raw = prefs.getString('anime_identity:$key');
  return jsonDecode(raw!) as Map<String, dynamic>;
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() {
    SharedPreferences.setMockInitialValues(<String, Object>{});
  });

  group('MAL matching', () {
    test('an exact title, year and TV type is verified anime', () async {
      final anilist = <Map<String, dynamic>>[];
      final identity = await service(
        mal: MalClient(
          clientId: 'client-secret',
          client: malClient(
            search: 'mal_search_fullmetal.json',
            details: <int, String>{
              5114: 'mal_anime_5114.json',
              121: 'mal_anime_121.json',
            },
          ),
        ),
        anilist: aniListClient(
          idmal: <int, String>{5114: 'anilist_idmal_fmab.json'},
          log: anilist,
        ),
      ).identifyTmdb(_brotherhood);

      expect(identity.status, IdentityStatus.anime);
      expect(identity.malId, 5114);
      expect(identity.anilistId, 9001);
      expect(identity.reason, AnimeIdentity.reasonAniListIdMal);
      expect(identity.playableAnilistId, 9001);

      final link = anilist.firstWhere(
        (entry) => (entry['query'] as String).contains('idMal:'),
      );
      expect((link['variables'] as Map<String, dynamic>)['malId'], 5114);
    });

    test('an alternative title matches exactly', () async {
      final identity = await service(
        mal: MalClient(
          clientId: 'id',
          client: malClient(
            search: 'mal_search_fullmetal.json',
            details: <int, String>{
              5114: 'mal_anime_5114.json',
              121: 'mal_anime_121.json',
            },
          ),
        ),
        anilist: aniListClient(
          idmal: <int, String>{5114: 'anilist_idmal_fmab.json'},
        ),
      ).identifyTmdb(
        const MediaItem(
          id: 'tmdb:1',
          title: 'Hagane no Renkinjutsushi: Fullmetal Alchemist',
          kind: MediaKind.tv,
          tmdbId: 1,
          year: '2009',
        ),
      );

      expect(identity.status, IdentityStatus.anime);
      expect(identity.malId, 5114);
    });

    test('two exact matches are ambiguous, never a coin toss', () async {
      final identity = await service(
        mal: MalClient(
          clientId: 'id',
          client: malClient(
            search: 'mal_search_ambiguous.json',
            details: <int, String>{
              700: 'mal_anime_700.json',
              701: 'mal_anime_701.json',
            },
          ),
        ),
      ).identifyTmdb(
        const MediaItem(
          id: 'tmdb:2',
          title: 'Berserk',
          kind: MediaKind.tv,
          tmdbId: 2,
          year: '1997',
        ),
      );

      expect(identity.status, IdentityStatus.unverified);
      expect(identity.reason, AnimeIdentity.reasonAmbiguousMalMatch);
      expect(identity.anilistId, isNull);
    });

    test('an unmatched title is unverified, not "not anime"', () async {
      final identity = await service(
        mal: MalClient(
          clientId: 'id',
          client: malClient(search: 'mal_search_empty.json'),
        ),
      ).identifyTmdb(
        const MediaItem(
          id: 'tmdb:3',
          title: 'A Plain Documentary',
          kind: MediaKind.movie,
          tmdbId: 3,
          year: '2021',
        ),
      );

      expect(identity.status, IdentityStatus.unverified);
      expect(identity.status, isNot(IdentityStatus.notAnime));
      expect(identity.reason, AnimeIdentity.reasonNoMalMatch);
      expect(identity.malId, isNull);
      expect(identity.anilistId, isNull);
    });

    test('a media type that does not fit the kind is no match', () {
      const anime = MalAnime(
        id: 1,
        title: 'Some Show',
        mediaType: 'ona',
        startDate: '2020-01-01',
      );

      expect(
        malMatchesCatalogue(anime, title: 'Some Show', year: '2020', kind: 'tv'),
        isTrue,
      );
      expect(
        malMatchesCatalogue(
          anime,
          title: 'Some Show',
          year: '2020',
          kind: 'movie',
        ),
        isFalse,
      );
    });
  });

  group('AniList resolution', () {
    test('a conflicting idMal answer falls back to a validated crosswalk', () async {
      final requests = <Map<String, dynamic>>[];
      final identity = await service(
        mal: MalClient(
          clientId: 'id',
          client: malClient(
            search: 'mal_search_fullmetal.json',
            details: <int, String>{
              5114: 'mal_anime_5114.json',
              121: 'mal_anime_121.json',
            },
          ),
        ),
        anilist: aniListClient(
          idmal: <int, String>{5114: 'anilist_idmal_conflict.json'},
          reverse: <int, String>{9001: 'anilist_reverse_9001.json'},
          log: requests,
        ),
      ).identifyTmdb(_brotherhood);

      expect(identity.status, IdentityStatus.anime);
      expect(identity.malId, 5114);
      expect(identity.anilistId, 9001);
      expect(identity.reason, AnimeIdentity.reasonAniListCrosswalk);

      // The MAL id is never asked for as an AniList id.
      final reverseIds = <int>[
        for (final request in requests)
          if (!(request['query'] as String).contains('idMal:') &&
              !(request['query'] as String).contains('Page('))
            (request['variables'] as Map<String, dynamic>)['id'] as int,
      ];
      expect(reverseIds, <int>[9001]);
    });

    test('a crosswalk candidate AniList cannot confirm is not used', () async {
      final identity = await service(
        mal: MalClient(
          clientId: 'id',
          client: malClient(
            search: 'mal_search_fullmetal.json',
            details: <int, String>{
              5114: 'mal_anime_5114.json',
              121: 'mal_anime_121.json',
            },
          ),
        ),
        anilist: aniListClient(
          idmal: <int, String>{5114: 'anilist_idmal_none.json'},
          reverse: <int, String>{9001: 'anilist_reverse_unvalidated.json'},
        ),
      ).identifyTmdb(_brotherhood);

      expect(identity.status, IdentityStatus.anime);
      expect(identity.malId, 5114);
      expect(identity.anilistId, isNull);
      expect(identity.reason, AnimeIdentity.reasonAniListUnvalidated);
    });

    test('several crosswalk candidates are ambiguous', () async {
      final identity = await service(
        mal: MalClient(
          clientId: 'id',
          client: malClient(
            search: 'mal_search_fullmetal.json',
            details: <int, String>{
              5114: 'mal_anime_5114.json',
              121: 'mal_anime_121.json',
            },
          ),
        ),
        anilist: aniListClient(
          idmal: <int, String>{5114: 'anilist_idmal_none.json'},
          search: 'anilist_search_ambiguous.json',
        ),
      ).identifyTmdb(_brotherhood);

      expect(identity.anilistId, isNull);
      expect(identity.reason, AnimeIdentity.reasonAniListAmbiguous);
    });

    test('AniList being unreachable is a structured resolution failure', () async {
      final identity = await service(
        mal: MalClient(
          clientId: 'id',
          client: malClient(
            search: 'mal_search_fullmetal.json',
            details: <int, String>{
              5114: 'mal_anime_5114.json',
              121: 'mal_anime_121.json',
            },
          ),
        ),
        anilist: aniListClient(unavailable: true),
      ).identifyTmdb(_brotherhood);

      expect(identity.status, IdentityStatus.anime);
      expect(identity.anilistId, isNull);
      expect(identity.reason, AnimeIdentity.reasonAniListLookupFailed);
    });

    test('an AniList detail page verifies through its own idMal link', () async {
      final identity = await service(
        mal: MalClient(
          clientId: 'id',
          client: malClient(
            details: <int, String>{5114: 'mal_anime_5114.json'},
          ),
        ),
        anilist: aniListClient(
          reverse: <int, String>{16498: 'anilist_for_anilist_16498.json'},
        ),
      ).identifyTmdb(
        const MediaItem(
          id: 'anilist:16498',
          title: 'Attack on Titan',
          kind: MediaKind.anime,
          anilistId: 16498,
        ),
      );

      expect(identity.status, IdentityStatus.anime);
      expect(identity.malId, 5114);
      expect(identity.anilistId, 16498);
    });
  });

  group('lookup failures', () {
    test('a 403 from MAL is lookup unavailable', () async {
      final anilist = <Map<String, dynamic>>[];
      final identity = await service(
        mal: MalClient(
          clientId: 'id',
          client: malClient(status: 403),
        ),
        anilist: aniListClient(log: anilist),
      ).identifyTmdb(_brotherhood);

      expect(identity.status, IdentityStatus.unverified);
      expect(identity.reason, AnimeIdentity.reasonLookupUnavailable);
      expect(anilist, isEmpty);
    });

    test('a missing Client ID never turns into a title search', () async {
      final identity = await service(
        mal: MalClient(
          clientId: '',
          client: MockClient((_) async => http.Response('{}', 200)),
        ),
      ).identifyTmdb(_brotherhood);

      expect(identity.status, IdentityStatus.unverified);
      expect(identity.reason, AnimeIdentity.reasonLookupUnavailable);
    });
  });

  group('cache', () {
    test('a verified answer is cached for seven days', () async {
      final clock = DateTime.utc(2026, 1, 1, 12);
      final identity = await service(
        clock: () => clock,
        mal: MalClient(
          clientId: 'id',
          client: malClient(
            search: 'mal_search_fullmetal.json',
            details: <int, String>{
              5114: 'mal_anime_5114.json',
              121: 'mal_anime_121.json',
            },
          ),
        ),
        anilist: aniListClient(
          idmal: <int, String>{5114: 'anilist_idmal_fmab.json'},
        ),
      ).identifyTmdb(_brotherhood);

      expect(identity.status, IdentityStatus.anime);
      final record = await storedRecord('tv:tmdb:1');
      expect(
        record['expires_at'],
        clock.add(verifiedIdentityTtl).millisecondsSinceEpoch,
      );
    });

    test('an unverified answer is cached for one day', () async {
      final clock = DateTime.utc(2026, 1, 1, 12);
      await service(
        clock: () => clock,
        mal: MalClient(
          clientId: 'id',
          client: malClient(search: 'mal_search_empty.json'),
        ),
      ).identifyTmdb(
        const MediaItem(
          id: 'tmdb:4',
          title: 'Unknown',
          kind: MediaKind.tv,
          tmdbId: 4,
        ),
      );

      final record = await storedRecord('tv:tmdb:4');
      expect(
        record['expires_at'],
        clock.add(otherIdentityTtl).millisecondsSinceEpoch,
      );
    });

    test('a fresh cached answer is served without any request', () async {
      final current = DateTime.utc(2026, 1, 1, 12);
      await service(
        clock: () => current,
        mal: MalClient(
          clientId: 'id',
          client: malClient(
            search: 'mal_search_fullmetal.json',
            details: <int, String>{
              5114: 'mal_anime_5114.json',
              121: 'mal_anime_121.json',
            },
          ),
        ),
        anilist: aniListClient(
          idmal: <int, String>{5114: 'anilist_idmal_fmab.json'},
        ),
      ).identifyTmdb(_brotherhood);

      final requests = <http.Request>[];
      final identity = await service(
        clock: () => current,
        mal: MalClient(
          clientId: 'id',
          client: malClient(
            search: 'mal_search_empty.json',
            log: requests,
          ),
        ),
      ).identifyTmdb(_brotherhood);

      expect(requests, isEmpty);
      expect(identity.status, IdentityStatus.anime);
      expect(identity.anilistId, 9001);
    });

    test('an expired answer is looked up again', () async {
      var current = DateTime.utc(2026, 1, 1, 12);
      await service(
        clock: () => current,
        mal: MalClient(
          clientId: 'id',
          client: malClient(
            search: 'mal_search_fullmetal.json',
            details: <int, String>{
              5114: 'mal_anime_5114.json',
              121: 'mal_anime_121.json',
            },
          ),
        ),
        anilist: aniListClient(
          idmal: <int, String>{5114: 'anilist_idmal_fmab.json'},
        ),
      ).identifyTmdb(_brotherhood);

      current = current.add(const Duration(days: 8));
      final requests = <http.Request>[];
      final identity = await service(
        clock: () => current,
        mal: MalClient(
          clientId: 'id',
          client: malClient(
            search: 'mal_search_fullmetal.json',
            details: <int, String>{
              5114: 'mal_anime_5114.json',
              121: 'mal_anime_121.json',
            },
            log: requests,
          ),
        ),
        anilist: aniListClient(
          idmal: <int, String>{5114: 'anilist_idmal_fmab.json'},
        ),
      ).identifyTmdb(_brotherhood);

      expect(requests, isNotEmpty);
      expect(identity.status, IdentityStatus.anime);
    });
  });

  test('MAL requests are throttled to one per second', () async {
    var now = DateTime.utc(2026, 1, 1, 12);
    final slept = <Duration>[];
    Future<void> sleep(Duration duration) async {
      slept.add(duration);
      now = now.add(duration);
    }

    final identity = await service(
      interval: const Duration(seconds: 1),
      clock: () => now,
      sleep: sleep,
      mal: MalClient(
        clientId: 'id',
        client: malClient(
          search: 'mal_search_fullmetal.json',
          details: <int, String>{
            5114: 'mal_anime_5114.json',
            121: 'mal_anime_121.json',
          },
        ),
      ),
      anilist: aniListClient(
        idmal: <int, String>{5114: 'anilist_idmal_fmab.json'},
      ),
    ).identifyTmdb(_brotherhood);

    expect(identity.status, IdentityStatus.anime);
    expect(slept, <Duration>[
      const Duration(seconds: 1),
      const Duration(seconds: 1),
    ]);
  });

  test('titles are normalised without accents or punctuation', () {
    expect(normaliseTitle('Shōgun: Part 1'), 'shogun part 1');
    expect(normaliseTitle('Frieren: Beyond Journey’s End'), 'frieren beyond journey s end');
  });
}
