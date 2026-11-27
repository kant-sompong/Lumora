import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

/// The public MyAnimeList API v2 (`lgl/mal.py` `API_BASE`).
const String malApiBase = 'https://api.myanimelist.net/v2';

/// The only place the Client ID travels (`lgl/mal.py` `CLIENT_ID_HEADER`).
const String malClientIdHeader = 'X-MAL-CLIENT-ID';

/// The UA the PC app sends for MAL reads, with this frontend's name.
const String malUserAgent = 'Lumora (personal mobile client)';

/// Fields requested for every anime, so search and detail payloads agree
/// (`lgl/mal.py` `LIST_FIELDS`).
const String malListFields =
    'id,title,synopsis,mean,num_episodes,status,genres,start_date,'
    'main_picture,alternative_titles,num_scoring_users,media_type,rating,'
    'average_episode_duration';

/// How many MAL search hits `AnimeIdentityService` examines in detail
/// (`lgl/anime_identity.py` `SEARCH_LIMIT`).
const int malSearchLimit = 5;

const int _maxBodyBytes = 4 * 1024 * 1024;
const int _maxSearchLimit = 100;
const Duration _timeout = Duration(seconds: 20);

final RegExp _clientIdHeaderValue = RegExp(
  r'(X-MAL-CLIENT-ID\s*[:=]\s*)([^\s,;]+)',
  caseSensitive: false,
);
final RegExp _clientIdQuery = RegExp(
  r'((?:client_id|X-MAL-CLIENT-ID)=)([^&\s]+)',
  caseSensitive: false,
);
final RegExp _bbcode = RegExp(r'\[/?[A-Za-z][^\]]{0,120}\]');
final RegExp _yearPattern = RegExp(r'\b(\d{4})\b');
final RegExp _mediaTypePattern = RegExp(r'^[a-z_]{1,20}$');

/// A MyAnimeList read failed, in a way the UI can explain.
///
/// [retryable] is true for a rate limit, a server error or a network failure.
/// Messages never contain the Client ID.
class MalException implements Exception {
  const MalException(
    this.message, {
    this.retryable = false,
    this.statusCode,
  });

  final String message;
  final bool retryable;
  final int? statusCode;

  @override
  String toString() => 'MalException: $message';
}

/// No Client ID is configured, so MyAnimeList cannot be asked at all
/// (`lgl/mal.py` `missing_client_id_message`).
class MissingMalClientIdException extends MalException {
  const MissingMalClientIdException([
    super.message =
        'No MyAnimeList Client ID is configured. Add one in Settings → '
        'Providers. Only the public anime catalogue is read; no account is '
        'needed.',
  ]);
}

/// One anime row (`lgl/mal.py` `AnimeItem` / `AnimeDetails`).
class MalAnime {
  const MalAnime({
    required this.id,
    required this.title,
    this.year,
    this.synopsis,
    this.posterUrl,
    this.rating,
    this.episodes = 0,
    this.status = '',
    this.mediaType = '',
    this.startDate = '',
    this.alternativeTitles = const <String>[],
    this.genres = const <String>[],
  });

  final int id;
  final String title;
  final String? year;
  final String? synopsis;
  final String? posterUrl;
  final double? rating;
  final int episodes;

  /// `finished_airing`, `currently_airing`, `not_yet_aired` or `''`.
  final String status;

  /// `tv`, `movie`, `ova`, `ona`, `special`, ... or `''`.
  final String mediaType;

  /// MAL's `start_date` string (`2009-04-05`), truncated by MAL.
  final String startDate;

  /// The primary title's other names: English, Japanese and synonyms.
  final List<String> alternativeTitles;
  final List<String> genres;

  String get key => 'mal:$id';
}

/// The MyAnimeList reads this app needs, with the Client ID kept private.
///
/// The Client ID is sent exclusively in the `X-MAL-CLIENT-ID` request header:
/// never in a URL, a log line or an error message.
class MalClient {
  MalClient({required String clientId, http.Client? client})
    : _clientId = clientId.trim(),
      _client = client ?? http.Client();

  final String _clientId;
  final http.Client _client;

  /// Whether a Client ID was configured at all.
  bool get configured => _clientId.isNotEmpty;

  /// Verifies the Client ID with the documented ranking endpoint, as the PC
  /// app does (`lgl/mal.py` `test_connection`).
  Future<String> testConnection() async {
    final data = await _get('/anime/ranking', <String, String>{
      'ranking_type': 'bypopularity',
      'limit': '1',
      'fields': 'id,title',
    });
    if (data['data'] is! List) {
      throw const MalException(
        'MyAnimeList answered but the response lacks expected data.',
      );
    }
    return 'MyAnimeList connection succeeded; the API answered.';
  }

  /// Anime matching [q]; empty for a blank query.
  Future<List<MalAnime>> search(String q, {int limit = 24}) async {
    final text = q.trim().replaceAll(RegExp(r'\s+'), ' ');
    if (text.isEmpty) {
      return const <MalAnime>[];
    }
    final data = await _get('/anime', <String, String>{
      'q': text.length > 200 ? text.substring(0, 200) : text,
      'limit': '${limit.clamp(1, _maxSearchLimit)}',
      'offset': '0',
      'fields': malListFields,
    });
    final rows = data['data'];
    if (rows is! List) {
      return const <MalAnime>[];
    }
    final out = <MalAnime>[];
    for (final row in rows) {
      if (row is! Map) {
        continue;
      }
      final anime = _anime(row['node']);
      if (anime != null) {
        out.add(anime);
      }
    }
    return out;
  }

  /// One anime by MAL id. `/anime/{id}` returns the object itself, not a
  /// `{"data": ...}` wrapper (`lgl/mal.py` `details`).
  Future<MalAnime> details(int malId) async {
    if (malId <= 0) {
      throw ArgumentError.value(malId, 'malId', 'must be positive');
    }
    final data = await _get('/anime/$malId', <String, String>{
      'fields': malListFields,
    });
    var payload = data;
    if (!payload.containsKey('id') && data['data'] is Map) {
      payload = Map<String, dynamic>.from(data['data'] as Map);
    }
    final anime = _anime(payload);
    if (anime == null) {
      throw const MalException(
        'MyAnimeList returned an unexpected document.',
      );
    }
    return anime;
  }

  Future<Map<String, dynamic>> _get(
    String path,
    Map<String, String> params,
  ) async {
    if (_clientId.isEmpty) {
      throw const MissingMalClientIdException();
    }
    // The Client ID never travels in the query string - only in the header.
    final uri = Uri.parse('$malApiBase$path').replace(queryParameters: params);

    http.Response response;
    try {
      response = await _client
          .get(
            uri,
            headers: <String, String>{
              'Accept': 'application/json',
              'User-Agent': malUserAgent,
              malClientIdHeader: _clientId,
            },
          )
          .timeout(_timeout);
    } on TimeoutException {
      throw const MalException(
        'Could not reach MyAnimeList: the request timed out.',
        retryable: true,
      );
    } on http.ClientException catch (error) {
      throw MalException(
        'Could not reach MyAnimeList: ${_redact(error.message)}',
        retryable: true,
      );
    } on Exception catch (error) {
      throw MalException(
        'Could not reach MyAnimeList: ${_redact('$error')}',
        retryable: true,
      );
    }

    if (response.bodyBytes.length > _maxBodyBytes) {
      throw const MalException('MyAnimeList returned more data than expected.');
    }

    if (response.statusCode < 200 || response.statusCode >= 300) {
      final status = response.statusCode;
      final detail = _errorDetail(response.bodyBytes);
      if (status == 401 ||
          status == 403 ||
          (status == 400 && detail.toLowerCase().contains('client id'))) {
        throw MalException(
          'MyAnimeList rejected the Client ID (HTTP $status'
          '${detail.isEmpty ? '' : ': $detail'}). Check the Client ID at '
          'myanimelist.net/apiconfig.',
          statusCode: status,
        );
      }
      throw MalException(
        'MyAnimeList answered HTTP $status${detail.isEmpty ? '.' : ': $detail.'}',
        statusCode: status,
        // 401/403/404 are the only answers a retry cannot fix, as on the PC.
        retryable: status != 404,
      );
    }

    final body = utf8.decode(response.bodyBytes);
    final dynamic decoded;
    try {
      decoded = jsonDecode(body);
    } on FormatException {
      throw const MalException('MyAnimeList returned invalid JSON.');
    }
    if (decoded is! Map) {
      throw const MalException(
        'MyAnimeList returned an unexpected document.',
      );
    }
    return Map<String, dynamic>.from(decoded);
  }

  /// Removes the Client ID and any header/query value that reached a string
  /// (`lgl/mal.py` `_redact`).
  String _redact(String text) {
    var value = text;
    if (_clientId.isNotEmpty) {
      value = value.replaceAll(_clientId, '***');
    }
    value = value.replaceAllMapped(
      _clientIdHeaderValue,
      (match) => '${match.group(1)}***',
    );
    return value.replaceAllMapped(
      _clientIdQuery,
      (match) => '${match.group(1)}***',
    );
  }
}

/// One MAL anime node, or null for a malformed one (`lgl/mal.py` `_item`).
MalAnime? _anime(dynamic raw) {
  if (raw is! Map) {
    return null;
  }
  final map = Map<String, dynamic>.from(raw);
  final id = _asInt(map['id']);
  if (id == null || id <= 0) {
    return null;
  }
  final title = _text(map['title']);
  if (title == null) {
    return null;
  }
  final startDate = map['start_date'] is String
      ? (map['start_date'] as String).trim()
      : '';
  return MalAnime(
    id: id,
    title: title.length > 300 ? title.substring(0, 300) : title,
    year: _year(startDate),
    synopsis: _synopsis(map['synopsis'], limit: 4000),
    posterUrl: _posterUrl(map['main_picture']),
    rating: _rating(map['mean']),
    episodes: _episodes(map['num_episodes']),
    status: _status(map['status']),
    mediaType: _mediaType(map['media_type']),
    startDate: startDate.length > 40 ? startDate.substring(0, 40) : startDate,
    alternativeTitles: _alternativeTitles(map['alternative_titles']),
    genres: _genres(map['genres']),
  );
}

String? _text(dynamic raw) {
  if (raw is! String) {
    return null;
  }
  final text = raw.split(RegExp(r'\s+')).where((part) => part.isNotEmpty).join(' ');
  return text.isEmpty ? null : text;
}

String? _posterUrl(dynamic raw) {
  if (raw is! Map) {
    return null;
  }
  final map = Map<String, dynamic>.from(raw);
  for (final name in <String>['large', 'medium']) {
    final text = map[name];
    if (text is! String) {
      continue;
    }
    final value = text.trim();
    if (value.isEmpty || value.length > 500) {
      continue;
    }
    final uri = Uri.tryParse(value);
    if (uri != null && uri.scheme == 'https' && uri.host.isNotEmpty) {
      return value;
    }
  }
  return null;
}

String? _year(String? raw) {
  final match = _yearPattern.firstMatch(raw ?? '');
  return match?.group(1);
}

String? _synopsis(dynamic raw, {required int limit}) {
  if (raw is! String) {
    return null;
  }
  final text = raw
      .replaceAll(_bbcode, ' ')
      .split(RegExp(r'\s+'))
      .where((part) => part.isNotEmpty)
      .join(' ');
  return text.isEmpty ? null : text.substring(0, text.length > limit ? limit : text.length);
}

double? _rating(dynamic raw) {
  final value = raw is num ? raw.toDouble() : null;
  if (value == null || value <= 0) {
    return null;
  }
  return (value * 10).round() / 10;
}

int _episodes(dynamic raw) {
  if (raw is int && raw >= 0) {
    return raw;
  }
  return 0;
}

String _status(dynamic raw) {
  final text = raw is String ? raw.trim().toLowerCase() : '';
  return text == 'finished_airing' ||
          text == 'currently_airing' ||
          text == 'not_yet_aired'
      ? text
      : '';
}

String _mediaType(dynamic raw) {
  final text = raw is String ? raw.trim().toLowerCase() : '';
  return _mediaTypePattern.hasMatch(text) ? text : '';
}

List<String> _alternativeTitles(dynamic raw) {
  if (raw is! Map) {
    return const <String>[];
  }
  final map = Map<String, dynamic>.from(raw);
  final names = <String>[];
  for (final key in <String>['en', 'ja']) {
    final text = _text(map[key]);
    if (text != null) {
      names.add(text.length > 200 ? text.substring(0, 200) : text);
    }
  }
  final synonyms = map['synonyms'];
  if (synonyms is List) {
    for (final entry in synonyms) {
      final text = _text(entry);
      if (text != null) {
        names.add(text.length > 200 ? text.substring(0, 200) : text);
      }
    }
  }
  final unique = <String>[];
  for (final name in names) {
    if (!unique.contains(name)) {
      unique.add(name);
    }
  }
  return unique.take(8).toList(growable: false);
}

List<String> _genres(dynamic raw) {
  if (raw is! List) {
    return const <String>[];
  }
  final names = <String>[];
  for (final entry in raw) {
    if (entry is! Map) {
      continue;
    }
    final name = _text(entry['name']);
    if (name != null) {
      names.add(name.length > 60 ? name.substring(0, 60) : name);
    }
  }
  return names.take(8).toList(growable: false);
}

String _errorDetail(List<int> body) {
  if (body.isEmpty) {
    return '';
  }
  try {
    final raw = utf8.decode(
      body.length > 4096 ? body.sublist(0, 4096) : body,
    );
    final decoded = jsonDecode(raw);
    if (decoded is Map) {
      for (final name in <String>['message', 'error', 'status_message']) {
        final value = decoded[name];
        if (value is String && value.trim().isNotEmpty) {
          final text = value.split(RegExp(r'\s+')).join(' ');
          return text.length > 200 ? text.substring(0, 200) : text;
        }
      }
    }
  } on FormatException {
    return '';
  }
  return '';
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
