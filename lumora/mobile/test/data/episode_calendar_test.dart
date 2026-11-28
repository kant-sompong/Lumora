import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:lumora_mobile/data/episode_calendar.dart';
import 'package:lumora_mobile/data/settings_store.dart';

final DateTime now = DateTime.utc(2027, 1, 15);
final DateTime horizon = now.add(const Duration(days: 90));

http.Response jsonResponse(Object body) => http.Response.bytes(
  utf8.encode(jsonEncode(body)),
  200,
  headers: <String, String>{
    'content-type': 'application/json; charset=utf-8',
  },
);

String day(int offset) =>
    now.add(Duration(days: offset)).toIso8601String().substring(0, 10);

int epoch(int offset) =>
    now.add(Duration(days: offset)).millisecondsSinceEpoch ~/ 1000;

Map<String, dynamic> anilistBody(List<Map<String, dynamic>> media) =>
    <String, dynamic>{
      'data': <String, dynamic>{
        'Page': <String, dynamic>{'media': media},
      },
    };

Map<String, dynamic> anilistMedia({
  int id = 5,
  String title = 'Alpha',
  List<Map<String, dynamic>> nodes = const [],
}) => <String, dynamic>{
  'id': id,
  'title': <String, dynamic>{'english': title},
  'airingSchedule': <String, dynamic>{'nodes': nodes},
};

Map<String, dynamic> seasonBody(List<Map<String, dynamic>> episodes) =>
    <String, dynamic>{'episodes': episodes};

Map<String, dynamic> episode(int number, String? airDate) =>
    <String, dynamic>{'episode_number': number, 'air_date': airDate};

/// A client that answers whatever [handler] returns for each request.
MockClient clientFor(
  Future<http.Response> Function(http.Request request) handler,
) => MockClient(handler);

void main() {
  group('AniList parsing', () {
    test('keeps exact air times and drops what already aired', () {
      final media = anilistMedia(nodes: [
        {'episode': 1, 'airingAt': epoch(-3)},
        {'episode': 2, 'airingAt': epoch(2)},
      ]);
      final airings = parseAniListAirings(media, now: now, horizon: horizon);

      expect(airings.map((a) => a.episode), <int>[2]);
      final airing = airings.single;
      expect(airing.confirmed, isTrue);
      expect(airing.dateOnly, isFalse);
      expect(airing.label, 'E2');
      expect(airing.status, confirmedStatus);
      expect(airing.seriesId, 'anilist:5');
    });

    test('skips a media node with no id or title', () {
      expect(
        parseAniListAirings(
          <String, dynamic>{'id': 0, 'title': 'Nope'},
          now: now,
          horizon: horizon,
        ),
        isEmpty,
      );
    });
  });

  group('TMDB season parsing', () {
    test('confirmed dates stay confirmed and unknown dates are listed', () {
      final airings = parseTmdbSeasonAirings(
        seasonBody([episode(1, day(1)), episode(2, null)]),
        title: 'Show',
        seriesId: 'tmdb:tv:1',
        season: 2,
        now: now,
        horizon: horizon,
      );

      final confirmed = airings.first;
      expect(confirmed.confirmed, isTrue);
      expect(confirmed.dateOnly, isTrue);
      expect(confirmed.label, 'S2E1');
      expect(confirmed.status, confirmedStatus);

      final unknown = airings.last;
      expect(unknown.known, isFalse);
      expect(airingDateLabel(unknown), unknownAirDate);
      expect(unknown.status, '');
    });

    test('a weekly cadence projects only the next three episodes', () {
      final airings = parseTmdbSeasonAirings(
        seasonBody([
          episode(1, day(0)),
          episode(2, day(7)),
          episode(3, day(14)),
          episode(4, null),
          episode(5, null),
          episode(6, null),
          episode(7, null),
        ]),
        title: 'Weekly',
        seriesId: 'tmdb:tv:1',
        season: 1,
        now: now,
        horizon: horizon,
      );

      final byNumber = <int, EpisodeAiring>{
        for (final airing in airings) airing.episode!: airing,
      };
      expect(byNumber[1]!.confirmed, isTrue);
      for (final number in <int>[4, 5, 6]) {
        expect(byNumber[number]!.estimated, isTrue);
        expect(byNumber[number]!.status, estimatedStatus);
      }
      expect(byNumber[7]!.known, isFalse);
      expect(
        byNumber[4]!.airAt!.difference(byNumber[3]!.airAt!).inDays,
        7,
      );
    });

    test('a lone date or a patchy schedule earns no projections', () {
      final lone = parseTmdbSeasonAirings(
        seasonBody([episode(1, day(1)), episode(2, null)]),
        title: 'Lone',
        seriesId: 'tmdb:tv:1',
        season: 1,
        now: now,
        horizon: horizon,
      );
      expect(lone.where((a) => a.estimated), isEmpty);

      final patchy = parseTmdbSeasonAirings(
        seasonBody([
          episode(1, day(0)),
          episode(2, day(3)),
          episode(3, day(20)),
          episode(4, null),
        ]),
        title: 'Patchy',
        seriesId: 'tmdb:tv:1',
        season: 1,
        now: now,
        horizon: horizon,
      );
      expect(patchy.last.estimated, isFalse);
      expect(patchy.last.known, isFalse);
    });

    test('lists at most two undated episodes', () {
      final airings = parseTmdbSeasonAirings(
        seasonBody([
          episode(1, day(1)),
          episode(2, null),
          episode(3, null),
          episode(4, null),
        ]),
        title: 'Quiet',
        seriesId: 'tmdb:tv:1',
        season: 1,
        now: now,
        horizon: horizon,
      );
      expect(
        airings.where((a) => !a.known).map((a) => a.episode),
        <int>[2, 3],
      );
    });
  });

  group('estimateAirDates', () {
    test('needs two anchors and a steady weekly gap', () {
      expect(
        estimateAirDates(
          <MapEntry<int, DateTime>>[MapEntry(1, now)],
          <int>[2],
          now: now,
          horizon: horizon,
        ),
        isEmpty,
      );
      final projected = estimateAirDates(
        <MapEntry<int, DateTime>>[
          MapEntry(1, now),
          MapEntry(2, now.add(const Duration(days: 7))),
        ],
        <int>[3, 4],
        now: now,
        horizon: horizon,
      );
      expect(projected.map((e) => e.key), <int>[3, 4]);
      expect(
        projected.first.value.difference(
          now.add(const Duration(days: 7))).inDays,
        7,
      );
    });

    test('does not project past the horizon', () {
      final projected = estimateAirDates(
        <MapEntry<int, DateTime>>[
          MapEntry(1, now),
          MapEntry(2, now.add(const Duration(days: 7))),
        ],
        <int>[3],
        now: now,
        horizon: now.add(const Duration(days: 3)),
      );
      expect(projected, isEmpty);
    });
  });

  group('merging', () {
    test('one AniList series wins over its TMDB duplicate', () {
      final merged = mergeCalendarSeries(<CalendarSeries>[
        CalendarSeries(
          title: 'Alpha',
          source: 'AniList',
          airings: [EpisodeAiring(series: 'Alpha', episode: 1, airAt: now)],
        ),
        CalendarSeries(
          title: 'alpha',
          source: 'TMDB',
          airings: [
            EpisodeAiring(
              series: 'alpha',
              season: 1,
              episode: 1,
              airAt: now,
              dateOnly: true,
            ),
          ],
        ),
      ]);
      expect(merged, hasLength(1));
      expect(merged.single.source, 'AniList');
    });

    test('keeps one row per (series, season, episode)', () {
      final airing = EpisodeAiring(
        series: 'Show',
        season: 1,
        episode: 2,
        airAt: now,
        dateOnly: true,
      );
      final merged = mergeCalendarSeries(<CalendarSeries>[
        CalendarSeries(title: 'Show', source: 'TMDB', airings: [airing, airing]),
      ]);
      expect(merged.single.airings, hasLength(1));
    });
  });

  group('EpisodeCalendarService', () {
    test('finds a TV show and its season', () async {
      final paths = <String>[];
      final client = clientFor((request) async {
        paths.add(request.url.path);
        if (request.method == 'POST') {
          return jsonResponse(anilistBody(const <Map<String, dynamic>>[]));
        }
        if (request.url.path.endsWith('/search/tv')) {
          return jsonResponse(<String, dynamic>{
            'results': <dynamic>[
              <String, dynamic>{'id': 10, 'name': 'Weekly'},
            ],
          });
        }
        if (request.url.path.endsWith('/tv/10')) {
          return jsonResponse(<String, dynamic>{
            'seasons': <dynamic>[
              <String, dynamic>{'season_number': 1},
            ],
          });
        }
        if (request.url.path.endsWith('/tv/10/season/1')) {
          return jsonResponse(seasonBody([
            episode(1, day(0)),
            episode(2, day(7)),
            episode(3, null),
          ]));
        }
        throw StateError('unexpected ${request.url}');
      });

      final service = EpisodeCalendarService(
        settings: AppSettings(tmdbApiKey: 'key123'),
        client: client,
        anilistEndpoint: 'https://anilist.test/graphql',
      );
      final result = await service.search('weekly', now: now);

      expect(result.errors, isEmpty);
      expect(result.groups.single.title, 'Weekly');
      expect(result.groups.single.source, 'TMDB');
      expect(
        result.airings.map((a) => a.episode),
        <int>[1, 2, 3],
      );
      expect(result.airings.last.estimated, isTrue);
      expect(
        paths,
        containsAll(<String>[
          '/3/search/tv',
          '/3/tv/10',
          '/3/tv/10/season/1',
        ]),
      );
    });

    test('anime works without a TMDB key and says TV needs one', () async {
      final client = clientFor((request) async {
        return jsonResponse(
          anilistBody([
            anilistMedia(nodes: [
              {'episode': 4, 'airingAt': epoch(2)},
            ]),
          ]),
        );
      });
      final service = EpisodeCalendarService(
        settings: AppSettings(),
        client: client,
        anilistEndpoint: 'https://anilist.test/graphql',
      );
      final result = await service.search('alpha', now: now);

      expect(result.groups.single.title, 'Alpha');
      expect(result.groups.single.source, 'AniList');
      expect(result.airings.single.label, 'E4');
      expect(
        result.errors.join(' '),
        contains('TMDB key'),
      );
    });

    test('a failed provider becomes a message, not an exception', () async {
      final client = clientFor((request) async {
        return http.Response('nope', 500);
      });
      final service = EpisodeCalendarService(
        settings: AppSettings(tmdbApiKey: 'key'),
        client: client,
        anilistEndpoint: 'https://anilist.test/graphql',
      );
      final result = await service.search('alpha', now: now);

      expect(result.groups, isEmpty);
      final errors = result.errors.join(' ');
      expect(errors, contains('AniList'));
      expect(errors, contains('TMDB'));
    });

    test('a blank search asks nothing', () async {
      final client = clientFor((request) async {
        throw StateError('no request expected');
      });
      final service = EpisodeCalendarService(
        settings: AppSettings(),
        client: client,
        anilistEndpoint: 'https://anilist.test/graphql',
      );
      final result = await service.search('   ');
      expect(result.groups, isEmpty);
      expect(result.errors, isEmpty);
    });
  });

  group('wording', () {
    test('labels and dates read naturally', () {
      expect(episodeLabel(2, 5), 'S2E5');
      expect(episodeLabel(null, 5), 'E5');
      expect(episodeLabel(2, null), 'S2');
      expect(episodeLabel(null, null), '');

      final dated = EpisodeAiring(
        series: 'Show',
        season: 2,
        episode: 5,
        airAt: DateTime.utc(2027, 1, 15),
        dateOnly: true,
        confirmed: true,
      );
      expect(airingDateLabel(dated), 'Fri 15 Jan 2027');
      final unknown = EpisodeAiring(series: 'Show', episode: 6);
      expect(airingDateLabel(unknown), unknownAirDate);
    });
  });
}
