import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import '../models/media_item.dart';
import 'media_repository.dart';
import 'settings_store.dart';

/// How far ahead the calendar searches, in days.
const int calendarHorizonDays = 90;

/// Series shown per provider, so one search cannot pull in a catalogue.
const int maxCalendarSeries = 3;

/// Future episodes a regular cadence may project.
const int maxCalendarEstimates = 3;

/// Undated episodes listed per season before giving up.
const int maxCalendarUnknown = 2;

/// A cadence outside these days is not regular enough to project from.
const int minCadenceDays = 5;
const int maxCadenceDays = 10;

const String unknownAirDate = 'Air date unknown';
const String confirmedStatus = 'Confirmed';
const String estimatedStatus = 'Estimated';

/// One episode of a series, with how sure we are of its date.
class EpisodeAiring {
  const EpisodeAiring({
    required this.series,
    this.episode,
    this.season,
    this.airAt,
    this.confirmed = false,
    this.source = '',
    this.seriesId = '',
    this.dateOnly = false,
  });

  final String series;
  final int? episode;
  final int? season;

  /// The air date: a bare day (UTC midnight) for TMDB, an instant for AniList.
  final DateTime? airAt;
  final bool confirmed;
  final String source;
  final String seriesId;

  /// The provider gave a day but no time (TMDB): show the date only.
  final bool dateOnly;

  bool get known => airAt != null;

  /// A projected date: we know roughly when, not exactly.
  bool get estimated => airAt != null && !confirmed;

  String get label => episodeLabel(season, episode);

  String get status =>
      !known ? '' : (confirmed ? confirmedStatus : estimatedStatus);
}

/// A series and its upcoming episodes, soonest first.
class CalendarSeries {
  const CalendarSeries({
    required this.title,
    required this.source,
    required this.airings,
  });

  final String title;
  final String source;
  final List<EpisodeAiring> airings;
}

/// One search's answer: grouped airings, plus anything that went wrong.
class EpisodeCalendarResult {
  const EpisodeCalendarResult({
    this.query = '',
    this.groups = const <CalendarSeries>[],
    this.errors = const <String>[],
  });

  final String query;
  final List<CalendarSeries> groups;
  final List<String> errors;

  bool get isEmpty => groups.isEmpty;

  List<EpisodeAiring> get airings => <EpisodeAiring>[
    for (final group in groups) ...group.airings,
  ];
}

/// Runs one search; tests inject this instead of a service.
typedef EpisodeSearch = Future<EpisodeCalendarResult> Function(String query);

/// `S2E5` when both are known, `E5` or `S2` when only one is.
String episodeLabel(int? season, int? episode) {
  if (season != null && season > 0 && episode != null && episode > 0) {
    return 'S${season}E$episode';
  }
  if (episode != null && episode > 0) {
    return 'E$episode';
  }
  if (season != null && season > 0) {
    return 'S$season';
  }
  return '';
}

const List<String> _weekdays = <String>[
  'Mon',
  'Tue',
  'Wed',
  'Thu',
  'Fri',
  'Sat',
  'Sun',
];
const List<String> _months = <String>[
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
];

/// The date a row shows: a day alone for a bare date, date and time for an
/// exact air time (in the phone's zone).
String airingDateLabel(EpisodeAiring airing) {
  final when = airing.airAt;
  if (when == null) {
    return unknownAirDate;
  }
  // A bare date is a calendar day, not an instant: never shift it by zone.
  final moment = airing.dateOnly ? when.toUtc() : when.toLocal();
  final day =
      '${_weekdays[moment.weekday - 1]} ${moment.day.toString().padLeft(2, '0')} '
      '${_months[moment.month - 1]} ${moment.year}';
  if (airing.dateOnly) {
    return day;
  }
  final hour = moment.hour.toString().padLeft(2, '0');
  final minute = moment.minute.toString().padLeft(2, '0');
  return '$day $hour:$minute';
}

/// Projects dates for undated episodes, or returns nothing without a pattern.
///
/// `anchors` are confirmed `(episode, date)` pairs and `numbers` the episodes
/// still missing a date. A projection needs at least two confirmed dates, a
/// median gap inside 5-10 days, and no wild gap; projected dates must still be
/// ahead of [now] and inside [horizon].
List<MapEntry<int, DateTime>> estimateAirDates(
  List<MapEntry<int, DateTime>> anchors,
  List<int> numbers, {
  required DateTime now,
  required DateTime horizon,
  int limit = maxCalendarEstimates,
}) {
  if (anchors.length < 2 || numbers.isEmpty) {
    return const <MapEntry<int, DateTime>>[];
  }
  final gaps = <double>[
    for (var index = 1; index < anchors.length; index++)
      anchors[index].value.difference(anchors[index - 1].value).inHours / 24,
  ];
  if (gaps.isEmpty) {
    return const <MapEntry<int, DateTime>>[];
  }
  final sorted = List<double>.from(gaps)..sort();
  final middle = sorted.length ~/ 2;
  final step = sorted.length.isOdd
      ? sorted[middle]
      : (sorted[middle - 1] + sorted[middle]) / 2;
  if (step < minCadenceDays || step > maxCadenceDays) {
    return const <MapEntry<int, DateTime>>[];
  }
  if (gaps.any(
    (gap) => gap < minCadenceDays - 1 || gap > maxCadenceDays + 2,
  )) {
    return const <MapEntry<int, DateTime>>[];
  }

  final last = anchors.last.value;
  final projected = <MapEntry<int, DateTime>>[];
  for (var index = 1; index <= numbers.length; index++) {
    final when = last.add(
      Duration(milliseconds: (index * step * 86400000).round()),
    );
    if (when.isBefore(now) || when.isAfter(horizon)) {
      break;
    }
    projected.add(MapEntry<int, DateTime>(numbers[index - 1], when));
    if (projected.length >= limit) {
      break;
    }
  }
  return projected;
}

/// AniList's search answer: media with their not-yet-aired schedule.
List<Map<String, dynamic>> parseAniListCalendar(Map<String, dynamic> data) {
  final page = data['Page'];
  if (page is! Map) {
    return const <Map<String, dynamic>>[];
  }
  final media = page['media'];
  if (media is! List) {
    return const <Map<String, dynamic>>[];
  }
  return <Map<String, dynamic>>[
    for (final entry in media)
      if (entry is Map) entry.map((key, value) => MapEntry('$key', value)),
  ];
}

/// One AniList media node as airings: every scheduled episode in the window.
List<EpisodeAiring> parseAniListAirings(
  Map<String, dynamic> media, {
  required DateTime now,
  required DateTime horizon,
}) {
  final id = _asInt(media['id']);
  final titles = media['title'];
  final titleMap = titles is Map ? titles : const <String, dynamic>{};
  final title = _firstNonEmpty(<dynamic>[
    titleMap['english'],
    titleMap['romaji'],
  ]);
  if (id == null || id <= 0 || title == null) {
    return const <EpisodeAiring>[];
  }
  final schedule = media['airingSchedule'];
  final nodes = schedule is Map ? schedule['nodes'] : null;
  if (nodes is! List) {
    return const <EpisodeAiring>[];
  }
  final airings = <EpisodeAiring>[];
  for (final entry in nodes) {
    if (entry is! Map) {
      continue;
    }
    final when = _epoch(entry['airingAt']);
    if (when == null || when.isBefore(now.subtract(const Duration(days: 1)))) {
      continue;
    }
    if (when.isAfter(horizon)) {
      continue;
    }
    airings.add(
      EpisodeAiring(
        series: title,
        episode: _asInt(entry['episode']),
        airAt: when,
        confirmed: true,
        source: 'AniList',
        seriesId: 'anilist:$id',
      ),
    );
  }
  airings.sort(_byDate);
  return airings;
}

/// One TMDB season's episodes in the window, plus in-season projections and
/// at most [maxCalendarUnknown] undated episodes.
List<EpisodeAiring> parseTmdbSeasonAirings(
  Map<String, dynamic> json, {
  required String title,
  required String seriesId,
  required int season,
  required DateTime now,
  required DateTime horizon,
}) {
  final raw = json['episodes'];
  if (raw is! List) {
    return const <EpisodeAiring>[];
  }
  final anchors = <MapEntry<int, DateTime>>[];
  final undated = <int>[];
  final airings = <EpisodeAiring>[];

  for (final entry in raw) {
    if (entry is! Map) {
      continue;
    }
    final number = _asInt(entry['episode_number']);
    if (number == null || number <= 0) {
      continue;
    }
    final when = _day(entry['air_date']);
    if (when != null) {
      if (when.isBefore(now.subtract(const Duration(days: 1))) ||
          when.isAfter(horizon)) {
        continue;
      }
      anchors.add(MapEntry<int, DateTime>(number, when));
      airings.add(
        EpisodeAiring(
          series: title,
          episode: number,
          season: season,
          airAt: when,
          confirmed: true,
          source: 'TMDB',
          seriesId: seriesId,
          dateOnly: true,
        ),
      );
    } else {
      undated.add(number);
    }
  }

  anchors.sort((a, b) => a.key.compareTo(b.key));
  final last = anchors.isEmpty ? 0 : anchors.last.key;
  final afterLast = <int>[
    for (final number in undated.toList()..sort())
      if (number > last) number,
  ];
  final projected = estimateAirDates(
    anchors,
    afterLast,
    now: now,
    horizon: horizon,
  );
  final taken = <int>{for (final entry in projected) entry.key};
  for (final entry in projected) {
    airings.add(
      EpisodeAiring(
        series: title,
        episode: entry.key,
        season: season,
        airAt: entry.value,
        source: 'TMDB',
        seriesId: seriesId,
        dateOnly: true,
      ),
    );
  }
  for (final number in afterLast.where((n) => !taken.contains(n)).take(
    maxCalendarUnknown,
  )) {
    airings.add(
      EpisodeAiring(
        series: title,
        episode: number,
        season: season,
        source: 'TMDB',
        seriesId: seriesId,
        dateOnly: true,
      ),
    );
  }
  airings.sort(_byDate);
  return airings;
}

int _byDate(EpisodeAiring a, EpisodeAiring b) {
  if (a.airAt == null && b.airAt == null) {
    return (a.episode ?? 0).compareTo(b.episode ?? 0);
  }
  if (a.airAt == null) {
    return 1;
  }
  if (b.airAt == null) {
    return -1;
  }
  return a.airAt!.compareTo(b.airAt!);
}

/// Drops repeated episodes, and a TMDB show AniList already covers.
List<CalendarSeries> mergeCalendarSeries(List<CalendarSeries> series) {
  final animeTitles = <String>{
    for (final group in series)
      if (group.source == 'AniList') _normaliseTitle(group.title),
  };
  final seen = <String>{};
  final merged = <CalendarSeries>[];
  for (final group in series) {
    final normal = _normaliseTitle(group.title);
    if (group.source == 'TMDB' && animeTitles.contains(normal)) {
      continue;
    }
    final kept = <EpisodeAiring>[];
    for (final airing in group.airings) {
      final key = '$normal|${airing.season}|${airing.episode}';
      if (!seen.add(key)) {
        continue;
      }
      kept.add(airing);
    }
    if (kept.isNotEmpty) {
      merged.add(
        CalendarSeries(
          title: group.title,
          source: group.source,
          airings: kept,
        ),
      );
    }
  }
  merged.sort((a, b) {
    final aFirst = a.airings
        .where((airing) => airing.airAt != null)
        .map((airing) => airing.airAt!)
        .fold<DateTime?>(null, (best, when) => best == null || when.isBefore(best) ? when : best);
    final bFirst = b.airings
        .where((airing) => airing.airAt != null)
        .map((airing) => airing.airAt!)
        .fold<DateTime?>(null, (best, when) => best == null || when.isBefore(best) ? when : best);
    if (aFirst == null && bFirst == null) {
      return a.title.toLowerCase().compareTo(b.title.toLowerCase());
    }
    if (aFirst == null) {
      return 1;
    }
    if (bFirst == null) {
      return -1;
    }
    final order = aFirst.compareTo(bFirst);
    return order != 0
        ? order
        : a.title.toLowerCase().compareTo(b.title.toLowerCase());
  });
  return merged;
}

// ---------------------------------------------------------------------------
// The network service: AniList for anime, TMDB for TV.
// ---------------------------------------------------------------------------

const String _anilistSearch = '''
query (\$search: String) {
  Page(page: 1, perPage: 8) {
    media(search: \$search, type: ANIME, isAdult: false, sort: SEARCH_MATCH) {
      id
      title { romaji english }
      airingSchedule(notYetAired: true, perPage: 20) {
        nodes { episode airingAt }
      }
    }
  }
}
''';

/// Searchable episode calendar over AniList (anime) and TMDB (TV).
///
/// TV needs the user's TMDB key; anime does not. Each provider fails on its
/// own: a failure becomes a message, never an exception out of [search].
class EpisodeCalendarService {
  EpisodeCalendarService({
    required this.settings,
    http.Client? client,
    this.tmdbBaseUrl = 'https://api.themoviedb.org/3',
    this.anilistEndpoint = 'https://graphql.anilist.co',
  }) : _client = client ?? http.Client();

  final AppSettings settings;
  final String tmdbBaseUrl;
  final String anilistEndpoint;
  final http.Client _client;

  static const Duration _timeout = Duration(seconds: 20);

  bool get hasTmdbKey => settings.tmdbApiKey.trim().isNotEmpty;

  void dispose() => _client.close();

  Future<EpisodeCalendarResult> search(
    String query, {
    int days = calendarHorizonDays,
    DateTime? now,
  }) async {
    final text = query.trim();
    if (text.isEmpty) {
      return const EpisodeCalendarResult();
    }
    final start = (now ?? DateTime.now()).toUtc();
    final horizon = start.add(Duration(days: days));
    final found = <CalendarSeries>[];
    final errors = <String>[];

    try {
      found.addAll(await _anilist(text, start, horizon));
    } catch (error) {
      errors.add('Could not search AniList: $error');
    }
    if (!hasTmdbKey) {
      errors.add('TV search needs a TMDB key (Settings → Providers).');
    } else {
      try {
        found.addAll(await _tmdb(text, start, horizon));
      } catch (error) {
        errors.add('Could not search TMDB: $error');
      }
    }
    return EpisodeCalendarResult(
      query: text,
      groups: mergeCalendarSeries(found),
      errors: errors,
    );
  }

  Future<List<CalendarSeries>> _anilist(
    String query,
    DateTime now,
    DateTime horizon,
  ) async {
    final response = await _client
        .post(
          Uri.parse(anilistEndpoint),
          headers: const <String, String>{
            'Accept': 'application/json',
            'Content-Type': 'application/json',
          },
          body: jsonEncode(<String, dynamic>{
            'query': _anilistSearch,
            'variables': <String, dynamic>{'search': query},
          }),
        )
        .timeout(_timeout);
    if (response.statusCode != 200) {
      throw NetworkException(
        'AniList request failed (${response.statusCode}).',
        statusCode: response.statusCode,
      );
    }
    final decoded = jsonDecode(response.body);
    if (decoded is! Map) {
      throw const NetworkException('AniList returned an unexpected response.');
    }
    final errors = decoded['errors'];
    if (errors is List && errors.isNotEmpty) {
      throw const NetworkException('AniList returned an error.');
    }
    final data = decoded['data'];
    if (data is! Map) {
      throw const NetworkException('AniList returned no data.');
    }
    final page = data['Page'];
    if (page is! Map) {
      throw const NetworkException('AniList returned an unexpected document.');
    }
    final groups = <CalendarSeries>[];
    final media = page['media'];
    for (final entry in media is List ? media : const <dynamic>[]) {
      if (entry is! Map) {
        continue;
      }
      final map = entry.map((key, value) => MapEntry('$key', value));
      final airings = parseAniListAirings(map, now: now, horizon: horizon);
      if (airings.isNotEmpty) {
        groups.add(
          CalendarSeries(
            title: airings.first.series,
            source: 'AniList',
            airings: airings,
          ),
        );
      }
      if (groups.length >= maxCalendarSeries) {
        break;
      }
    }
    return groups;
  }

  Future<List<CalendarSeries>> _tmdb(
    String query,
    DateTime now,
    DateTime horizon,
  ) async {
    final listing = await _get('/search/tv', <String, String>{
      'query': query,
      'include_adult': 'false',
      'page': '1',
    });
    final results = listing['results'];
    final groups = <CalendarSeries>[];
    for (final entry in results is List ? results : const <dynamic>[]) {
      if (entry is! Map) {
        continue;
      }
      final id = _asInt(entry['id']);
      final title = _asString(entry['name']);
      if (id == null || id <= 0 || title == null) {
        continue;
      }
      final airings = await _showAirings(
        id,
        title,
        now: now,
        horizon: horizon,
      );
      if (airings.isNotEmpty) {
        groups.add(
          CalendarSeries(title: title, source: 'TMDB', airings: airings),
        );
      }
      if (groups.length >= maxCalendarSeries) {
        break;
      }
    }
    return groups;
  }

  Future<List<EpisodeAiring>> _showAirings(
    int id,
    String title, {
    required DateTime now,
    required DateTime horizon,
  }) async {
    final detail = await _get('/tv/$id', const <String, String>{});
    final seasons = detail['seasons'];
    final numbers = <int>[
      for (final season in seasons is List ? seasons : const <dynamic>[])
        if (season is Map && (_asInt(season['season_number']) ?? 0) > 0)
          _asInt(season['season_number'])!,
    ]..sort((a, b) => b.compareTo(a));
    final airings = <EpisodeAiring>[];
    for (final number in numbers.take(2)) {
      Map<String, dynamic> season;
      try {
        season = await _get(
          '/tv/$id/season/$number',
          const <String, String>{},
        );
      } on Exception {
        continue;
      }
      airings.addAll(
        parseTmdbSeasonAirings(
          season,
          title: title,
          seriesId: 'tmdb:tv:$id',
          season: number,
          now: now,
          horizon: horizon,
        ),
      );
    }
    airings.sort(_byDate);
    return airings;
  }

  bool get _isV4Token => settings.tmdbApiKey.trim().contains('.');

  Future<Map<String, dynamic>> _get(
    String path,
    Map<String, String> params,
  ) async {
    final query = <String, String>{'language': 'en-US', ...params};
    if (!_isV4Token) {
      query['api_key'] = settings.tmdbApiKey.trim();
    }
    final uri = Uri.parse('$tmdbBaseUrl$path').replace(queryParameters: query);
    final response = await _client
        .get(
          uri,
          headers: <String, String>{
            'Accept': 'application/json',
            if (_isV4Token)
              'Authorization': 'Bearer ${settings.tmdbApiKey.trim()}',
          },
        )
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
    return decoded.map((key, value) => MapEntry('$key', value));
  }
}

// ---------------------------------------------------------------------------
// Small defensive helpers (the repository's are private to its library).
// ---------------------------------------------------------------------------

String _normaliseTitle(String value) =>
    value.toLowerCase().replaceAll(RegExp('[^0-9a-z]+'), ' ').trim();

DateTime? _epoch(dynamic raw) {
  final seconds = _asInt(raw);
  if (seconds == null || seconds <= 0) {
    return null;
  }
  return DateTime.fromMillisecondsSinceEpoch(seconds * 1000, isUtc: true);
}

DateTime? _day(dynamic raw) {
  final text = _asString(raw);
  if (text == null || text.length < 10) {
    return null;
  }
  final parsed = DateTime.tryParse(text.substring(0, 10));
  return parsed == null
      ? null
      : DateTime.utc(parsed.year, parsed.month, parsed.day);
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

/// Re-exported so a page can hand a plain media item to the service.
typedef CalendarMedia = MediaItem;
