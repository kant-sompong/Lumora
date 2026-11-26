import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:lumora_mobile/ui/settings/tmdb_key_tester.dart';

void main() {
  group('testTmdbKey', () {
    test('accepts a key TMDB answers with its configuration', () async {
      final client = MockClient((http.Request request) async {
        expect(request.url.host, 'api.themoviedb.org');
        expect(request.url.path, '/3/configuration');
        expect(request.url.queryParameters['api_key'], 'a' * 32);
        return http.Response(
            '{"images":{"base_url":"http://image.tmdb.org"}}', 200);
      });
      final result = await testTmdbKey('a' * 32, client: client);
      expect(result.ok, isTrue);
      expect(result.message, contains('succeeded'));
    });

    test('reports an invalid key without echoing it', () async {
      final key = 'b' * 32;
      final client = MockClient((_) async => http.Response('{}', 401));
      final result = await testTmdbKey(key, client: client);
      expect(result.ok, isFalse);
      expect(result.invalid, isTrue);
      expect(result.message, isNot(contains(key)));
    });

    test('reports rate limiting as a retryable failure', () async {
      final client = MockClient((_) async => http.Response('{}', 429));
      final result = await testTmdbKey('c' * 32, client: client);
      expect(result.ok, isFalse);
      expect(result.message, contains('rate limiting'));
    });

    test('rejects an empty key before making a request', () async {
      final client = MockClient((_) async {
        fail('no request should be made for an empty key');
      });
      final result = await testTmdbKey('   ', client: client);
      expect(result.ok, isFalse);
      expect(result.message, contains('Enter a TMDB API key'));
    });

    test('turns a network failure into a message, not an exception', () async {
      final client = MockClient(
        (_) async => throw http.ClientException('offline'),
      );
      final result = await testTmdbKey('d' * 32, client: client);
      expect(result.ok, isFalse);
      expect(result.message, contains('Could not reach TMDB'));
    });
  });

  test('looksLikeTmdbKey checks the 32-hex shape', () {
    expect(looksLikeTmdbKey('0123456789abcdef0123456789abcdef'), isTrue);
    expect(looksLikeTmdbKey('not-a-key'), isFalse);
    expect(looksLikeTmdbKey(''), isFalse);
  });
}
