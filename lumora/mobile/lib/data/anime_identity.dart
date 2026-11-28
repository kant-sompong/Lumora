import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

import '../models/media_item.dart';
import 'mal_client.dart';
import 'settings_store.dart';

/// AniList's public GraphQL endpoint (`lgl/anime_identity.py` `GRAPHQL_URL`).
const String anilistGraphQlUrl = 'https://graphql.anilist.co';

/// The UA this frontend sends to AniList.
const String animeIdentityUserAgent = 'Lumora (personal mobile client)';

/// How long a verified answer is cached (`lgl/anime_identity.py`
/// `TTL_VERIFIED`).
const Duration verifiedIdentityTtl = Duration(days: 7);

/// How long every other answer is cached (`TTL_OTHER`).
const Duration otherIdentityTtl = Duration(days: 1);

/// The three answers callers must distinguish (`lgl/anime_identity.py`
/// `IdentityStatus`).
enum IdentityStatus {
  /// A real MAL anime entry matched.
  anime,

  /// Never produced: MAL is the only proof of anime, and the PC app never
  /// treats "no match" as "not anime" (that is [unverified]). The value exists
  /// because the contract names it.
  notAnime,

  /// No reliable MAL match, or MAL could not be asked. Not "not anime".
  unverified,
}

/// The answer for one catalogue title.
///
/// [malId] and [anilistId] are different identifiers and are never
/// interchangeable. [anilistId] is set only when AniList confirmed the link.
class AnimeIdentity {
  const AnimeIdentity({
    required this.status,
    this.malId,
    this.anilistId,
    this.reason = '',
  });

  final IdentityStatus status;
  final int? malId;
  final int? anilistId;

  /// A machine-readable [reason] value; see the `reason*` constants.
  final String reason;

  /// Machine-readable reason values, copied from `lgl/anime_identity.py`
  /// `Reason`.
  static const String reasonMalId = 'mal-id';
  static const String reasonMalTitle = 'mal-title';
  static const String reasonNoMalMatch = 'no-mal-match';
  static const String reasonAmbiguousMalMatch = 'ambiguous-mal-match';
  static const String reasonLookupUnavailable = 'lookup-unavailable';
  static const String reasonAniListIdMal = 'anilist-idmal';
  static const String reasonAniListCrosswalk = 'anilist-crosswalk';
  static const String reasonAniListMissing = 'anilist-missing';
  static const String reasonAniListAmbiguous = 'anilist-ambiguous';
  static const String reasonAniListUnvalidated = 'anilist-unvalidated';
  static const String reasonAniListLookupFailed = 'anilist-lookup-failed';
  static const String reasonAniListTitle = 'anilist-title';

  /// Whether MAL verified the title as anime.
  bool get verified => status == IdentityStatus.anime;

  /// The id anipm.pk may receive: an AniList id, never a MAL id.
  int? get playableAnilistId => verified ? anilistId : null;
}

/// Decides whether a catalogue title is anime, using MyAnimeList as the only
/// authority, then resolves the AniList id anipm.pk plays from.
///
/// Matching is exact (`lgl/anime_identity.py` `matches_catalogue`): the
/// normalised title must equal MAL's primary or one alternative title, the
/// years must agree when both are known, and the MAL media type must fit the
/// catalogue kind. There is no fuzzy scoring; two matches are ambiguous.
///
/// Unmatched titles are [IdentityStatus.unverified], never "not anime" and
/// never hidden.
class AnimeIdentityService {
  AnimeIdentityService(
    this.settings, {
    MalClient? malClient,
    http.Client? client,
    Duration malMinInterval = const Duration(seconds: 1),
    DateTime Function()? clock,
    Future<void> Function(Duration duration)? sleep,
  }) : _mal = malClient,
       _client = client ?? http.Client(),
       _malMinInterval = malMinInterval < Duration.zero
           ? Duration.zero
           : malMinInterval,
       _clock = clock ?? DateTime.now,
       _sleep = sleep ?? _defaultSleep;

  final AppSettings settings;
  final http.Client _client;

  /// Test seams: the MAL throttle (one request a second) and the cache clock.
  /// Production callers leave them alone.
  final Duration _malMinInterval;
  final DateTime Function() _clock;
  final Future<void> Function(Duration duration) _sleep;

  MalClient? _mal;
  DateTime? _lastMalRequest;

  static const String _prefsPrefix = 'anime_identity:';
  static const Duration _anilistTimeout = Duration(seconds: 15);

  /// MAL media types that can sit behind a catalogue kind (`_KIND_TYPES`).
  static const Map<String, Set<String>> _kindTypes = <String, Set<String>>{
    'tv': <String>{'tv', 'ona', 'ova'},
    'movie': <String>{'movie', 'special'},
  };

  /// The AniList formats that fit each catalogue kind, in AniList's spelling
  /// (`lgl/anime_identity.py` `_ANILIST_KINDS`).
  static const Map<String, Set<String>> _anilistKinds = <String, Set<String>>{
    'tv': <String>{'tv', 'tv_short', 'ona', 'ova'},
    'movie': <String>{'movie', 'special'},
  };

  /// The cache key for [item], as the PC app keys its own cache.
  static String cacheKeyFor(MediaItem item) {
    if (item.kind == MediaKind.anime && item.anilistId != null) {
      return 'anilist:${item.anilistId}';
    }
    final kind = item.kind == MediaKind.movie ? 'movie' : 'tv';
    final tmdbId = item.tmdbId;
    if (tmdbId != null && tmdbId > 0) {
      return '$kind:tmdb:$tmdbId';
    }
    return '$kind:${normaliseTitle(item.title)}:${yearOf(item.year)}';
  }

  /// A TMDB title's identity, cached in `shared_preferences` for
  /// [verifiedIdentityTtl] when anime and [otherIdentityTtl] otherwise.
  ///
  /// Only an answer MAL actually gave is cached. A lookup failure (no Client
  /// ID, a network error, MAL 403/429/5xx, a timeout) is returned but never
  /// stored, so fixing the Client ID in Settings takes effect on the next
  /// lookup instead of being pinned for a day.
  Future<AnimeIdentity> identifyTmdb(MediaItem item) async {
    final key = cacheKeyFor(item);
    final cached = await _readCache(key);
    if (cached != null) {
      return cached;
    }
    final identity = await _identify(item);
    if (_cachesAnswer(identity)) {
      await _writeCache(key, identity);
    }
    return identity;
  }

  /// Whether [identity] is a real MAL answer rather than a failed lookup.
  static bool _cachesAnswer(AnimeIdentity identity) =>
      identity.reason != AnimeIdentity.reasonLookupUnavailable;

  Future<AnimeIdentity> _identify(MediaItem item) async {
    if (item.kind == MediaKind.anime && item.anilistId != null) {
      return _identifyAniListId(item.anilistId!);
    }

    final title = item.title.trim();
    if (title.isEmpty) {
      return const AnimeIdentity(
        status: IdentityStatus.unverified,
        reason: AnimeIdentity.reasonNoMalMatch,
      );
    }
    final kind = item.kind == MediaKind.movie ? 'movie' : 'tv';

    final List<MalAnime> found;
    try {
      found = await _malSearch(title);
    } on MalException {
      return const AnimeIdentity(
        status: IdentityStatus.unverified,
        reason: AnimeIdentity.reasonLookupUnavailable,
      );
    }

    final matches = <MalAnime>[];
    for (final candidate in found.take(malSearchLimit)) {
      try {
        final details = await _malDetails(candidate.id);
        if (malMatchesCatalogue(
          details,
          title: title,
          year: item.year ?? '',
          kind: kind,
        )) {
          matches.add(details);
        }
      } on MalException {
        continue; // one bad entry must not hide a good one
      }
    }

    if (matches.length != 1) {
      // MAL has no single answer. AniList can still name the entry exactly and
      // carry its own `idMal` link, which MAL then has to confirm
      // (`lgl/anime_identity.py` `search_anilist_title`), so a title MAL's
      // title search cannot find is not silently "not anime".
      final rescued = await _verifyThroughAniList(
        title: title,
        year: item.year ?? '',
        kind: kind,
      );
      if (rescued != null) {
        return rescued;
      }
      return AnimeIdentity(
        status: IdentityStatus.unverified,
        reason: matches.isEmpty
            ? AnimeIdentity.reasonNoMalMatch
            : AnimeIdentity.reasonAmbiguousMalMatch,
      );
    }

    final match = matches.single;
    final resolution = await _resolveAniList(
      match.id,
      title: title,
      malTitle: match.title,
      kind: kind,
    );
    // MAL is the authority: a matching entry is anime even when AniList could
    // not resolve an id. `anilistId` stays null so anipm.pk is never handed a
    // MAL id, and provider_choice only forces anipm.pk when an id exists.
    return AnimeIdentity(
      status: IdentityStatus.anime,
      malId: match.id,
      anilistId: resolution.anilistId,
      reason: resolution.reason,
    );
  }

  /// An AniList detail page has no MAL id, so AniList's own `idMal` link finds
  /// the entry MAL then has to confirm (`lgl/anime_identity.py` `identify`'s
  /// `anilist_id` path).
  Future<AnimeIdentity> _identifyAniListId(int anilistId) async {
    final answer = await _malIdForAniList(anilistId);
    if (answer.failed) {
      return const AnimeIdentity(
        status: IdentityStatus.unverified,
        reason: AnimeIdentity.reasonLookupUnavailable,
      );
    }
    final malId = answer.id;
    if (malId == null) {
      return const AnimeIdentity(
        status: IdentityStatus.unverified,
        reason: AnimeIdentity.reasonNoMalMatch,
      );
    }
    try {
      final details = await _malDetails(malId);
      return AnimeIdentity(
        status: IdentityStatus.anime,
        malId: details.id,
        anilistId: anilistId,
        reason: AnimeIdentity.reasonAniListIdMal,
      );
    } on MalException {
      return const AnimeIdentity(
        status: IdentityStatus.unverified,
        reason: AnimeIdentity.reasonLookupUnavailable,
      );
    }
  }

  /// AniList's own `Media(idMal:)` link first, then a search hit that carries
  /// that exact MAL link. The PC app reads its bundled Fribb crosswalk between
  /// the two; the mobile app has no bundled map, so it asks AniList by the
  /// catalogue title and then by MAL's own primary title
  /// (`lgl/anime_identity.py` `resolve_anilist` then `anilist_link_for_mal`).
  /// A MAL id is never used as an AniList id.
  Future<({int? anilistId, String reason})> _resolveAniList(
    int malId, {
    required String title,
    required String malTitle,
    required String kind,
  }) async {
    final linked = await _anilistIdForMal(malId);
    if (linked.id != null) {
      return (anilistId: linked.id, reason: AnimeIdentity.reasonAniListIdMal);
    }

    var reason = linked.failed
        ? AnimeIdentity.reasonAniListLookupFailed
        : AnimeIdentity.reasonAniListMissing;

    // AniList's own search of the catalogue title, keeping only hits whose
    // `idMal` is this MAL entry; the matches are still candidates until
    // AniList's reverse link confirms one.
    final candidates = await _linkedCandidates(malId, title);
    if (candidates.failed) {
      reason = AnimeIdentity.reasonAniListLookupFailed;
    } else if (candidates.ids.length > 1) {
      reason = AnimeIdentity.reasonAniListAmbiguous;
    } else if (candidates.ids.length == 1) {
      final candidate = candidates.ids.single;
      final confirmed = await _malIdForAniList(candidate);
      if (confirmed.id == malId) {
        return (
          anilistId: candidate,
          reason: AnimeIdentity.reasonAniListCrosswalk,
        );
      }
      reason = AnimeIdentity.reasonAniListUnvalidated;
    }

    // The PC app's last resort: search AniList by the MAL entry's own primary
    // title, where the catalogue title was a translation or a synonym.
    final malName = malTitle.trim();
    if (!candidates.failed &&
        malName.isNotEmpty &&
        normaliseTitle(malName) != normaliseTitle(title)) {
      final byMalTitle = await _linkedCandidates(malId, malName);
      if (byMalTitle.ids.length > 1) {
        reason = AnimeIdentity.reasonAniListAmbiguous;
      } else if (byMalTitle.ids.length == 1) {
        return (
          anilistId: byMalTitle.ids.single,
          reason: AnimeIdentity.reasonAniListTitle,
        );
      }
    }
    return (anilistId: null, reason: reason);
  }

  /// MAL's title search gave no single answer, so AniList's exact name match
  /// gets to nominate one entry - and MAL still has to confirm that entry as an
  /// anime of the catalogue's kind (`lgl/anime_identity.py`
  /// `_verified_through_anilist`).
  Future<AnimeIdentity?> _verifyThroughAniList({
    required String title,
    required String year,
    required String kind,
  }) async {
    final search = await _searchAniList(title);
    if (search.failed) {
      return null;
    }
    final exact = <int, AniListTitle>{};
    for (final entry in search.entries) {
      if (entry.malId == null) {
        continue;
      }
      if (!matchesAniListTitle(entry, title: title, year: year, kind: kind)) {
        continue;
      }
      exact[entry.anilistId] = entry;
    }
    if (exact.length != 1) {
      return null;
    }
    final entry = exact.values.single;
    final MalAnime details;
    try {
      details = await _malDetails(entry.malId!);
    } on MalException {
      return null;
    }
    final media = details.mediaType.trim().toLowerCase();
    final allowed = _kindTypes[kind] ?? _kindTypes['tv']!;
    if (media.isNotEmpty && !allowed.contains(media)) {
      return null;
    }
    return AnimeIdentity(
      status: IdentityStatus.anime,
      malId: details.id,
      anilistId: entry.anilistId,
      reason: AnimeIdentity.reasonAniListTitle,
    );
  }

  // -- MyAnimeList ---------------------------------------------------------

  MalClient _requireMal() {
    final injected = _mal;
    if (injected != null) {
      return injected;
    }
    final clientId = settings.malClientId.trim();
    if (clientId.isEmpty) {
      throw const MissingMalClientIdException();
    }
    return _mal = MalClient(clientId: clientId);
  }

  /// At most one MAL request per [malMinInterval] seconds
  /// (`lgl/anime_identity.py` `MAL_MIN_INTERVAL`).
  Future<void> _throttleMal() async {
    if (_malMinInterval <= Duration.zero) {
      return;
    }
    final last = _lastMalRequest;
    if (last != null) {
      final wait = _malMinInterval - _clock().difference(last);
      if (wait > Duration.zero) {
        await _sleep(wait);
      }
    }
    _lastMalRequest = _clock();
  }

  Future<List<MalAnime>> _malSearch(String title) async {
    final client = _requireMal();
    await _throttleMal();
    return client.search(title, limit: malSearchLimit);
  }

  Future<MalAnime> _malDetails(int malId) async {
    final client = _requireMal();
    await _throttleMal();
    return client.details(malId);
  }

  // -- AniList -------------------------------------------------------------

  static const String _linkQuery =
      'query (\$malId: Int) { Media(type: ANIME, idMal: \$malId) '
      '{ id idMal } }';
  static const String _reverseQuery =
      'query (\$id: Int) { Media(type: ANIME, id: \$id) { id idMal } }';
  static const String _searchQuery =
      'query (\$search: String) { Page(perPage: 10) { '
      'media(search: \$search, type: ANIME, sort: SEARCH_MATCH) '
      '{ id idMal seasonYear format synonyms title { romaji english native } } '
      '} }';

  Future<Map<String, dynamic>?> _anilist(
    String query,
    Map<String, dynamic> variables,
  ) async {
    try {
      final response = await _client
          .post(
            Uri.parse(anilistGraphQlUrl),
            headers: const <String, String>{
              'Content-Type': 'application/json',
              'Accept': 'application/json',
              'User-Agent': animeIdentityUserAgent,
            },
            body: jsonEncode(<String, dynamic>{
              'query': query,
              'variables': variables,
            }),
          )
          .timeout(_anilistTimeout);
      if (response.statusCode != 200) {
        return null;
      }
      final decoded = jsonDecode(utf8.decode(response.bodyBytes));
      if (decoded is! Map) {
        return null;
      }
      final errors = decoded['errors'];
      if (errors is List && errors.isNotEmpty) {
        return null;
      }
      final data = decoded['data'];
      return data is Map ? Map<String, dynamic>.from(data) : null;
    } on Exception {
      return null;
    }
  }

  Future<_AniListAnswer> _anilistIdForMal(int malId) async {
    final data = await _anilist(_linkQuery, <String, dynamic>{'malId': malId});
    if (data == null) {
      return const _AniListAnswer(failed: true);
    }
    final media = _media(data);
    if (media == null) {
      return const _AniListAnswer();
    }
    // AniList can answer with a different entry; only its own link counts.
    if (_asInt(media['idMal']) != malId) {
      return const _AniListAnswer();
    }
    return _AniListAnswer(id: _asInt(media['id']));
  }

  Future<_AniListAnswer> _malIdForAniList(int anilistId) async {
    final data = await _anilist(_reverseQuery, <String, dynamic>{
      'id': anilistId,
    });
    if (data == null) {
      return const _AniListAnswer(failed: true);
    }
    final media = _media(data);
    if (media == null || _asInt(media['id']) != anilistId) {
      return const _AniListAnswer();
    }
    return _AniListAnswer(id: _asInt(media['idMal']));
  }

  /// AniList search hits that carry this exact MAL link, by title.
  Future<_AniListCandidates> _linkedCandidates(
    int malId,
    String title,
  ) async {
    final search = await _searchAniList(title);
    if (search.failed) {
      return const _AniListCandidates(<int>[], failed: true);
    }
    final ids = <int>[];
    for (final entry in search.entries) {
      if (entry.malId != malId) {
        continue;
      }
      if (entry.anilistId != malId && !ids.contains(entry.anilistId)) {
        ids.add(entry.anilistId);
      }
    }
    return _AniListCandidates(ids, failed: false);
  }

  /// AniList's catalogue search for [title], most relevant first, with every
  /// name each hit goes by (`lgl/anime_identity.py` `search_titles`).
  Future<({List<AniListTitle> entries, bool failed})> _searchAniList(
    String title,
  ) async {
    final text = title.trim();
    if (text.isEmpty) {
      return (entries: const <AniListTitle>[], failed: false);
    }
    final data = await _anilist(_searchQuery, <String, dynamic>{
      'search': text,
    });
    if (data == null) {
      return (entries: const <AniListTitle>[], failed: true);
    }
    final page = data['Page'];
    if (page is! Map) {
      return (entries: const <AniListTitle>[], failed: true);
    }
    final rows = page['media'];
    if (rows is! List) {
      return (entries: const <AniListTitle>[], failed: false);
    }
    final entries = <AniListTitle>[];
    for (final row in rows) {
      if (row is! Map) {
        continue;
      }
      final anilistId = _asInt(row['id']);
      if (anilistId == null) {
        continue;
      }
      final names = <String>[];
      final titles = row['title'];
      if (titles is Map) {
        for (final key in <String>['romaji', 'english', 'native']) {
          final name = titles[key];
          if (name is String && name.trim().isNotEmpty) {
            names.add(name);
          }
        }
      }
      final synonyms = row['synonyms'];
      if (synonyms is List) {
        for (final name in synonyms) {
          if (name is String && name.trim().isNotEmpty) {
            names.add(name);
          }
        }
      }
      entries.add(
        AniListTitle(
          anilistId: anilistId,
          malId: _asInt(row['idMal']),
          titles: List<String>.unmodifiable(names),
          year: int.tryParse(yearOf(row['seasonYear']?.toString() ?? '')),
          format: (row['format'] is String ? row['format'] as String : '')
              .trim(),
        ),
      );
    }
    return (entries: entries, failed: false);
  }

  static Map<String, dynamic>? _media(Map<String, dynamic> payload) {
    final media = payload['Media'];
    return media is Map ? Map<String, dynamic>.from(media) : null;
  }

  // -- cache ---------------------------------------------------------------

  Future<AnimeIdentity?> _readCache(String key) async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString(_prefsPrefix + key);
    if (raw == null) {
      return null;
    }
    try {
      final decoded = jsonDecode(raw);
      if (decoded is! Map) {
        return null;
      }
      final expires = _asInt(decoded['expires_at']);
      if (expires == null || expires <= _clock().millisecondsSinceEpoch) {
        return null;
      }
      final status = _statusFromName(decoded['status']);
      if (status == null) {
        return null;
      }
      return AnimeIdentity(
        status: status,
        malId: _asInt(decoded['mal_id']),
        anilistId: _asInt(decoded['anilist_id']),
        reason: decoded['reason'] is String ? decoded['reason'] as String : '',
      );
    } on FormatException {
      return null;
    }
  }

  Future<void> _writeCache(String key, AnimeIdentity identity) async {
    final ttl = identity.status == IdentityStatus.anime
        ? verifiedIdentityTtl
        : otherIdentityTtl;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(
      _prefsPrefix + key,
      jsonEncode(<String, dynamic>{
        'status': identity.status.name,
        'mal_id': identity.malId,
        'anilist_id': identity.anilistId,
        'reason': identity.reason,
        'expires_at': _clock().add(ttl).millisecondsSinceEpoch,
      }),
    );
  }

  static IdentityStatus? _statusFromName(dynamic raw) {
    for (final status in IdentityStatus.values) {
      if (status.name == raw) {
        return status;
      }
    }
    return null;
  }
}

Future<void> _defaultSleep(Duration duration) =>
    Future<void>.delayed(duration);

class _AniListAnswer {
  const _AniListAnswer({this.id, this.failed = false});

  final int? id;
  final bool failed;
}

class _AniListCandidates {
  const _AniListCandidates(this.ids, {required this.failed});

  final List<int> ids;
  final bool failed;
}

/// One AniList search hit: its id, its MAL link and the names it goes by
/// (`lgl/anime_identity.py` `AniListTitle`).
class AniListTitle {
  const AniListTitle({
    required this.anilistId,
    this.malId,
    this.titles = const <String>[],
    this.year,
    this.format = '',
  });

  final int anilistId;
  final int? malId;

  /// Romaji, English, native and synonym names, as AniList spells them.
  final List<String> titles;
  final int? year;
  final String format;
}

/// Is [entry] the same title as the catalogue row, under exactly that name?
///
/// The same discipline as [malMatchesCatalogue]
/// (`lgl/anime_identity.py` `matches_anilist_title`): the normalised catalogue
/// title must equal one of AniList's own names, the years must agree when both
/// are known, and the format must fit the catalogue kind.
bool matchesAniListTitle(
  AniListTitle entry, {
  required String title,
  String year = '',
  required String kind,
}) {
  final wanted = normaliseTitle(title);
  if (wanted.isEmpty) {
    return false;
  }
  if (!entry.titles.map(normaliseTitle).contains(wanted)) {
    return false;
  }
  final wantedYear = yearOf(year);
  final entryYear = entry.year ?? 0;
  if (wantedYear.isNotEmpty && entryYear > 0 && wantedYear != '$entryYear') {
    return false;
  }
  final shape = entry.format.trim().toUpperCase();
  if (shape.isEmpty) {
    return true;
  }
  final allowed =
      AnimeIdentityService._anilistKinds[kind.toLowerCase()] ??
      AnimeIdentityService._anilistKinds['tv']!;
  return allowed.contains(shape.toLowerCase());
}

/// Is [match] the same title as the catalogue entry?
///
/// Exactness is the point (`lgl/anime_identity.py` `matches_catalogue`): the
/// normalised catalogue title must equal MAL's primary or one alternative
/// title, the years must agree when both are known, and the MAL media type
/// must fit the catalogue kind. A near miss is not a match.
bool malMatchesCatalogue(
  MalAnime match, {
  required String title,
  String year = '',
  required String kind,
}) {
  final wanted = normaliseTitle(title);
  if (wanted.isEmpty) {
    return false;
  }
  final names = <String>{normaliseTitle(match.title)};
  names.addAll(match.alternativeTitles.map(normaliseTitle));
  if (!names.contains(wanted)) {
    return false;
  }
  final wantedYear = yearOf(year);
  final matchYear = yearOf(match.startDate);
  if (wantedYear.isNotEmpty &&
      matchYear.isNotEmpty &&
      wantedYear != matchYear) {
    return false;
  }
  final media = match.mediaType.trim().toLowerCase();
  if (media.isEmpty) {
    return true;
  }
  final allowed = AnimeIdentityService._kindTypes[kind.toLowerCase()] ??
      AnimeIdentityService._kindTypes['tv']!;
  return allowed.contains(media);
}

/// Letters and digits of every script; everything else separates words.
final RegExp _nonWords = RegExp(r'[^\p{L}\p{N}]+', unicode: true);

/// Casefolded, accent-free, punctuation-free, single-spaced words
/// (`lgl/anime_identity.py` `_normalise`).
///
/// Every script's letters are kept, not just ASCII: Japanese, Cyrillic and
/// other names only match when they survive normalisation, and the PC app's
/// `str.isalnum()` keeps them too. `_diacritics` stands in for NFKD's Latin
/// decomposition, so `Shōgun` still matches `Shogun`.
String normaliseTitle(String value) {
  final buffer = StringBuffer();
  for (final rune in value.toLowerCase().runes) {
    final character = String.fromCharCode(rune);
    buffer.write(_diacritics[character] ?? character);
  }
  final words = buffer.toString().replaceAll(_nonWords, ' ').trim();
  return words.split(RegExp(r'\s+')).where((word) => word.isNotEmpty).join(' ');
}

/// The first four digits of [value], or `''` (`_year`).
String yearOf(String? value) {
  final digits = (value ?? '').replaceAll(RegExp('[^0-9]'), '');
  return digits.length >= 4 ? digits.substring(0, 4) : '';
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

/// Latin letters NFKD would decompose, so `Shōgun` matches `Shogun`.
const Map<String, String> _diacritics = <String, String>{
  'à': 'a', 'á': 'a', 'â': 'a', 'ã': 'a', 'ä': 'a', 'å': 'a', 'ā': 'a',
  'ă': 'a', 'ą': 'a', 'ç': 'c', 'ć': 'c', 'č': 'c', 'ď': 'd', 'đ': 'd',
  'è': 'e', 'é': 'e', 'ê': 'e', 'ë': 'e', 'ē': 'e', 'ė': 'e', 'ę': 'e',
  'ě': 'e', 'ì': 'i', 'í': 'i', 'î': 'i', 'ï': 'i', 'ī': 'i', 'į': 'i',
  'ł': 'l', 'ñ': 'n', 'ń': 'n', 'ň': 'n', 'ò': 'o', 'ó': 'o', 'ô': 'o',
  'õ': 'o', 'ö': 'o', 'ø': 'o', 'ō': 'o', 'ő': 'o', 'ř': 'r', 'ś': 's',
  'š': 's', 'ş': 's', 'ß': 'ss', 'ť': 't', 'ţ': 't', 'ù': 'u', 'ú': 'u',
  'û': 'u', 'ü': 'u', 'ū': 'u', 'ů': 'u', 'ű': 'u', 'ý': 'y', 'ÿ': 'y',
  'ź': 'z', 'ż': 'z', 'ž': 'z', 'æ': 'ae', 'œ': 'oe',
};
