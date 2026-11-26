import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:lumora_mobile/data/mal_client.dart';

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

void main() {
  group('MalClient', () {
    test('sends the Client ID only in the header and parses search rows', () async {
      final requests = <http.Request>[];
      final mal = MalClient(
        clientId: 'client-secret',
        client: MockClient((request) async {
          requests.add(request);
          return jsonResponse('mal_search_fullmetal.json');
        }),
      );

      final results = await mal.search('fullmetal', limit: 5);

      final request = requests.single;
      expect(request.url.toString(), isNot(contains('client-secret')));
      expect(request.url.queryParameters.containsKey('client_id'), isFalse);
      expect(request.headers['X-MAL-CLIENT-ID'], 'client-secret');
      expect(request.url.queryParameters['limit'], '5');

      expect(results.map((anime) => anime.id), <int>[5114, 121]);
      expect(results.first.title, 'Fullmetal Alchemist: Brotherhood');
      expect(results.first.year, '2009');
      expect(results.first.rating, 9.1);
      expect(results.first.episodes, 64);
      expect(results.first.mediaType, 'tv');
      expect(results.first.alternativeTitles, contains('FMAB'));
      expect(
        results.first.posterUrl,
        'https://cdn.myanimelist.net/images/anime/1208/94745l.jpg',
      );
    });

    test('details parses alternative titles, genres and stripped synopsis', () async {
      final mal = MalClient(
        clientId: 'id',
        client: MockClient((_) async => jsonResponse('mal_anime_5114.json')),
      );

      final anime = await mal.details(5114);

      expect(anime.id, 5114);
      expect(anime.genres, <String>['Action', 'Adventure']);
      expect(anime.synopsis, 'Two brothers search for the Philosopher\'s Stone.');
      expect(
        anime.alternativeTitles,
        contains('Hagane no Renkinjutsushi: Fullmetal Alchemist'),
      );
      expect(anime.startDate, '2009-04-05');
    });

    test('testConnection uses the ranking endpoint and reports success', () async {
      final requests = <http.Request>[];
      final mal = MalClient(
        clientId: 'id',
        client: MockClient((request) async {
          requests.add(request);
          return jsonResponse('mal_ranking.json');
        }),
      );

      final message = await mal.testConnection();

      expect(message, contains('succeeded'));
      expect(requests.single.url.path, '/v2/anime/ranking');
      expect(requests.single.url.queryParameters['ranking_type'], 'bypopularity');
    });

    test('403 is an auth failure that must not be retried', () async {
      final mal = MalClient(
        clientId: 'secret-id',
        client: MockClient(
          (_) async => http.Response('{"message":"forbidden"}', 403),
        ),
      );

      try {
        await mal.search('anything');
        fail('expected MalException');
      } on MalException catch (error) {
        expect(error.retryable, isFalse);
        expect(error.statusCode, 403);
        expect(error.message, contains('Client ID'));
        expect(error.message, isNot(contains('secret-id')));
      }
    });

    test('a server error and a rate limit are retryable', () async {
      for (final status in <int>[429, 500, 503]) {
        final mal = MalClient(
          clientId: 'id',
          client: MockClient((_) async => http.Response('{}', status)),
        );
        try {
          await mal.search('anything');
          fail('expected MalException');
        } on MalException catch (error) {
          expect(error.retryable, isTrue, reason: 'HTTP $status');
          expect(error.statusCode, status);
        }
      }
    });

    test('invalid JSON and an unexpected document are not retryable', () async {
      final mal = MalClient(
        clientId: 'id',
        client: MockClient((_) async => http.Response('<html>', 200)),
      );
      try {
        await mal.search('anything');
        fail('expected MalException');
      } on MalException catch (error) {
        expect(error.message, contains('invalid JSON'));
        expect(error.retryable, isFalse);
      }
    });

    test('a missing Client ID never reaches the network', () async {
      var called = false;
      final mal = MalClient(
        clientId: '   ',
        client: MockClient((_) async {
          called = true;
          return http.Response('{}', 200);
        }),
      );

      expect(mal.configured, isFalse);
      await expectLater(
        mal.search('anything'),
        throwsA(isA<MissingMalClientIdException>()),
      );
      expect(called, isFalse);
      expect(mal.testConnection(), throwsA(isA<MalException>()));
    });

    test('a blank query returns nothing without a request', () async {
      var called = false;
      final mal = MalClient(
        clientId: 'id',
        client: MockClient((_) async {
          called = true;
          return http.Response('{}', 200);
        }),
      );

      expect(await mal.search('   '), isEmpty);
      expect(called, isFalse);
    });

    test('a network error never repeats the Client ID', () async {
      final mal = MalClient(
        clientId: 'client-secret',
        client: MockClient(
          (_) async => throw http.ClientException('failed client-secret'),
        ),
      );

      try {
        await mal.search('anything');
        fail('expected MalException');
      } on MalException catch (error) {
        expect(error.message, contains('***'));
        expect(error.message, isNot(contains('client-secret')));
        expect(error.retryable, isTrue);
      }
    });
  });
}
