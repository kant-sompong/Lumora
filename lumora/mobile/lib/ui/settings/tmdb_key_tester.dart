import 'dart:convert';

import 'package:http/http.dart' as http;

/// The outcome of checking a TMDB key, in words the settings page can show.
class TmdbKeyResult {
  const TmdbKeyResult({required this.ok, required this.message});

  final bool ok;
  final String message;

  bool get invalid => !ok;
}

/// One request against the documented TMDB API, to prove a key works.
///
/// The key is only ever put in the query string of the request; no message this
/// returns repeats it, so a failure cannot leak the key into the UI.
Future<TmdbKeyResult> testTmdbKey(String key, {http.Client? client}) async {
  final trimmed = key.trim();
  if (trimmed.isEmpty) {
    return const TmdbKeyResult(
      ok: false,
      message: 'Enter a TMDB API key first.',
    );
  }
  final ownClient = client == null;
  final http.Client httpClient = client ?? http.Client();
  try {
    final response = await httpClient.get(
      Uri.parse('https://api.themoviedb.org/3/configuration?api_key=$trimmed'),
      headers: const <String, String>{
        'Accept': 'application/json',
        'User-Agent': 'Lumora (personal mobile client)',
      },
    );
    if (response.statusCode == 200) {
      final Object? body = jsonDecode(response.body);
      if (body is Map<String, dynamic> && body['images'] != null) {
        return const TmdbKeyResult(
          ok: true,
          message: 'TMDB connection succeeded; image configuration retrieved.',
        );
      }
      return const TmdbKeyResult(
        ok: false,
        message: 'TMDB answered but the reply was not its configuration '
            'document.',
      );
    }
    if (response.statusCode == 401) {
      return const TmdbKeyResult(
        ok: false,
        message: 'TMDB rejected that key (invalid or not active yet).',
      );
    }
    if (response.statusCode == 429) {
      return const TmdbKeyResult(
        ok: false,
        message: 'TMDB is rate limiting this key; try again in a minute.',
      );
    }
    return TmdbKeyResult(
      ok: false,
      message: 'TMDB answered ${response.statusCode}. The key was not '
          'accepted.',
    );
  } on FormatException {
    return const TmdbKeyResult(
      ok: false,
      message: 'TMDB answered with something that is not JSON.',
    );
  } on Exception {
    return const TmdbKeyResult(
      ok: false,
      message: 'Could not reach TMDB. Check the connection and try again.',
    );
  } finally {
    if (ownClient) {
      httpClient.close();
    }
  }
}

/// Whether a typed key looks like the 32-hex key TMDB issues.
///
/// Mirrors `providers.validate_credential` in the PC app: a shape check before
/// the network call, and never a value in the message.
bool looksLikeTmdbKey(String key) {
  return RegExp(r'^[0-9a-fA-F]{32}$').hasMatch(key.trim());
}
