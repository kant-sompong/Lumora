import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

import 'providers_registry.dart';
import 'settings_store.dart';

/// How quick a provider answered (`lgl/provider_health.py`).
enum HealthStatus { fast, ok, slow, unreachable, timedOut }

/// One provider's health, as measured and cached.
class ProviderHealth {
  const ProviderHealth({
    required this.providerId,
    required this.status,
    this.ms,
    this.httpStatus,
    required this.checkedAt,
  });

  final String providerId;
  final HealthStatus status;

  /// Median milliseconds to first byte, when the provider answered.
  final int? ms;

  /// The HTTP status of the first non-2xx/3xx answer, when there was one. An
  /// HTTP error is still a response, so the provider stays reachable.
  final int? httpStatus;
  final DateTime checkedAt;

  /// Whether the provider answered at all.
  bool get reachable =>
      status != HealthStatus.unreachable && status != HealthStatus.timedOut;
}

/// `http` in `lgl/provider_health.py`: one attempt's status and milliseconds
/// to first byte.
typedef HealthProbe =
    Future<({int statusCode, int ms})> Function(Uri uri, Duration timeout);

/// Checks the streaming providers' own base hosts, never a player or a video.
///
/// One plain `HEAD` per attempt, three attempts, median time to first byte, a
/// five second timeout, at most four at once, and a ten minute cache
/// (`lgl/provider_health.py`).
class ProviderHealthService {
  ProviderHealthService(
    this.settings, {
    http.Client? client,
    HealthProbe? probe,
    DateTime Function()? clock,
  }) : _client = client ?? http.Client(),
       _probe = probe,
       _clock = clock ?? DateTime.now;

  final AppSettings settings;
  final http.Client _client;

  /// Test seams: a canned probe and a cache clock. Production callers leave
  /// them alone.
  final HealthProbe? _probe;
  final DateTime Function() _clock;

  /// The `shared_preferences` key holding the last results.
  static const String cacheKey = 'provider_health_cache';

  /// Attempts per provider (`ATTEMPTS`).
  static const int attempts = 3;

  /// Seconds to wait for the provider's first byte (`TIMEOUT`).
  static const Duration timeout = Duration(seconds: 5);

  /// How long a result stays fresh (`CACHE_TTL`).
  static const Duration cacheTtl = Duration(minutes: 10);

  /// Worker count used by [checkAll] (`MAX_CONCURRENCY`).
  static const int maxConcurrency = 4;

  /// Under this many milliseconds a provider is `fast` (`FAST_MS`).
  static const int fastMs = 300;

  /// From this many milliseconds a provider is `slow` (`OK_MS`).
  static const int okMs = 1000;

  static const String userAgent =
      'Mozilla/5.0 (compatible; Lumora; private mobile client)';

  final Map<String, ProviderHealth> _cache = <String, ProviderHealth>{};
  Future<void>? _loading;

  /// Checks every provider, reusing fresh cached answers unless [force].
  ///
  /// Returns the results fastest first; unreachable providers are last
  /// (`lgl/provider_health.py` `check_all` / `sort_results`).
  Future<List<ProviderHealth>> checkAll({bool force = false}) async {
    await _ensureLoaded();
    final ids = <String>[for (final provider in allProviders) provider.id];

    final known = <String, ProviderHealth>{};
    if (!force) {
      for (final id in ids) {
        final health = cached(id);
        if (health != null) {
          known[id] = health;
        }
      }
    }

    final missing = <String>[
      for (final id in ids)
        if (!known.containsKey(id)) id,
    ];
    if (missing.isNotEmpty) {
      final checked = await _checkMany(missing);
      known.addAll(checked);
      _cache.addAll(checked);
      await _persist();
    }

    final rows = <ProviderHealth>[];
    for (final id in ids) {
      final health = known[id];
      if (health != null) {
        rows.add(health);
      }
    }
    rows.sort(_rank);
    return rows;
  }

  /// A fresh cached result for [id], or null when there is none.
  ProviderHealth? cached(String id) {
    final health = _cache[id];
    if (health == null || !_isFresh(health)) {
      return null;
    }
    return health;
  }

  /// The fastest reachable provider among [ids], or null.
  String? fastest(Iterable<String> ids) {
    final rows = <ProviderHealth>[];
    for (final id in ids.toSet()) {
      final health = cached(id);
      if (health != null) {
        rows.add(health);
      }
    }
    rows.sort(_rank);
    for (final health in rows) {
      if (health.reachable && health.ms != null) {
        return health.providerId;
      }
    }
    return null;
  }

  /// `CineSrc · 180 ms` / `Moonflix · unreachable`
  /// (`lgl/provider_health.py` `describe`).
  String describe(ProviderHealth health) {
    final label = providerById(health.providerId)?.name ?? health.providerId;
    if (health.reachable && health.ms != null) {
      return '$label · ${health.ms} ms';
    }
    return '$label · ${statusWord(health.status)}';
  }

  /// The status as the PC app words it (`timed out`, not `timedOut`).
  static String statusWord(HealthStatus status) {
    switch (status) {
      case HealthStatus.fast:
        return 'fast';
      case HealthStatus.ok:
        return 'ok';
      case HealthStatus.slow:
        return 'slow';
      case HealthStatus.unreachable:
        return 'unreachable';
      case HealthStatus.timedOut:
        return 'timed out';
    }
  }

  Future<ProviderHealth> _check(String id) async {
    final uri = _baseUri(id);
    if (uri == null) {
      return ProviderHealth(
        providerId: id,
        status: HealthStatus.unreachable,
        checkedAt: _clock(),
      );
    }

    final timings = <int>[];
    int? httpStatus;
    for (var attempt = 0; attempt < attempts; attempt++) {
      try {
        final result = await _runProbe(uri);
        timings.add(result.ms);
        if (result.statusCode < 200 || result.statusCode >= 400) {
          httpStatus ??= result.statusCode;
        }
      } on TimeoutException {
        return ProviderHealth(
          providerId: id,
          status: HealthStatus.timedOut,
          checkedAt: _clock(),
        );
      } on Exception {
        return ProviderHealth(
          providerId: id,
          status: HealthStatus.unreachable,
          checkedAt: _clock(),
        );
      }
    }

    if (timings.isEmpty) {
      return ProviderHealth(
        providerId: id,
        status: HealthStatus.unreachable,
        checkedAt: _clock(),
      );
    }
    final ms = _median(timings).round();
    return ProviderHealth(
      providerId: id,
      status: _word(ms),
      ms: ms,
      httpStatus: httpStatus,
      checkedAt: _clock(),
    );
  }

  Future<({int statusCode, int ms})> _runProbe(Uri uri) {
    final probe = _probe;
    if (probe != null) {
      return probe(uri, timeout);
    }
    return _httpProbe(uri);
  }

  /// Plain HEAD request; returns `(status, ms to first byte)`.
  Future<({int statusCode, int ms})> _httpProbe(Uri uri) async {
    final watch = Stopwatch()..start();
    final response = await _client
        .head(
          uri,
          headers: const <String, String>{
            'User-Agent': userAgent,
            'Accept': '*/*',
          },
        )
        .timeout(timeout);
    watch.stop();
    return (statusCode: response.statusCode, ms: watch.elapsedMilliseconds);
  }

  /// The provider's own base host, never a player route
  /// (`lgl/provider_health.py` `base_url`).
  Uri? _baseUri(String id) {
    final provider = providerById(id);
    if (provider == null) {
      return null;
    }
    final templates = urlTemplates(provider);
    if (templates.isEmpty) {
      return null;
    }
    final parsed = Uri.tryParse(templates.first);
    if (parsed == null || parsed.host.isEmpty) {
      return null;
    }
    return Uri(scheme: 'https', host: parsed.host, path: '/');
  }

  Future<Map<String, ProviderHealth>> _checkMany(List<String> ids) async {
    final queue = List<String>.from(ids);
    final out = <String, ProviderHealth>{};

    Future<void> worker() async {
      while (queue.isNotEmpty) {
        final id = queue.removeAt(0);
        out[id] = await _check(id);
      }
    }

    final workers = <Future<void>>[
      for (var i = 0; i < maxConcurrency && i < ids.length; i++) worker(),
    ];
    await Future.wait(workers);
    return out;
  }

  bool _isFresh(ProviderHealth health) =>
      _clock().difference(health.checkedAt) < cacheTtl;

  Future<void> _ensureLoaded() {
    return _loading ??= _load();
  }

  Future<void> _load() async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString(cacheKey);
    if (raw == null) {
      return;
    }
    try {
      final decoded = jsonDecode(raw);
      if (decoded is! Map) {
        return;
      }
      final stamp = _asInt(decoded['checked_at']);
      if (stamp == null) {
        return;
      }
      final checkedAt = DateTime.fromMillisecondsSinceEpoch(stamp * 1000);
      if (_clock().difference(checkedAt) >= cacheTtl) {
        return;
      }
      final results = decoded['results'];
      if (results is! Map) {
        return;
      }
      for (final entry in results.entries) {
        final value = entry.value;
        if (value is! Map) {
          continue;
        }
        final status = _statusFromName(value['status']);
        if (status == null) {
          continue;
        }
        _cache[entry.key.toString()] = ProviderHealth(
          providerId: entry.key.toString(),
          status: status,
          ms: _asInt(value['ms']),
          httpStatus: _asInt(value['http_status']),
          checkedAt: checkedAt,
        );
      }
    } on FormatException {
      return;
    }
  }

  Future<void> _persist() async {
    final prefs = await SharedPreferences.getInstance();
    final results = <String, dynamic>{
      for (final entry in _cache.entries)
        entry.key: <String, dynamic>{
          'status': entry.value.status.name,
          'ms': entry.value.ms,
          'http_status': entry.value.httpStatus,
        },
    };
    await prefs.setString(
      cacheKey,
      jsonEncode(<String, dynamic>{
        'checked_at': _clock().millisecondsSinceEpoch ~/ 1000,
        'results': results,
      }),
    );
  }

  static int _rank(ProviderHealth a, ProviderHealth b) {
    final aOk = a.reachable && a.ms != null;
    final bOk = b.reachable && b.ms != null;
    if (aOk != bOk) {
      return aOk ? -1 : 1;
    }
    if (aOk && bOk) {
      final byMs = a.ms!.compareTo(b.ms!);
      if (byMs != 0) {
        return byMs;
      }
    }
    return a.providerId.compareTo(b.providerId);
  }

  /// The median, as a double so three even values keep their middle
  /// (`lgl/provider_health.py` `_median`).
  static double _median(List<int> values) {
    final ordered = List<int>.from(values)..sort();
    final middle = ordered.length ~/ 2;
    if (ordered.length.isOdd) {
      return ordered[middle].toDouble();
    }
    return (ordered[middle - 1] + ordered[middle]) / 2.0;
  }

  static HealthStatus _word(int ms) {
    if (ms < fastMs) {
      return HealthStatus.fast;
    }
    if (ms < okMs) {
      return HealthStatus.ok;
    }
    return HealthStatus.slow;
  }

  static HealthStatus? _statusFromName(dynamic raw) {
    for (final status in HealthStatus.values) {
      if (status.name == raw) {
        return status;
      }
    }
    return null;
  }

  static int? _asInt(dynamic raw) {
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
}
