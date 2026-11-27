import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:lumora_mobile/data/provider_health.dart';
import 'package:lumora_mobile/data/providers_registry.dart';
import 'package:lumora_mobile/data/settings_store.dart';
import 'package:shared_preferences/shared_preferences.dart';

HealthProbe fixedProbe(Map<String, int> milliseconds) {
  return (uri, timeout) async {
    final ms = milliseconds[uri.host];
    if (ms == null) {
      return (statusCode: 200, ms: 100);
    }
    return (statusCode: 200, ms: ms);
  };
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() {
    SharedPreferences.setMockInitialValues(<String, Object>{});
  });

  test('three attempts report the median time and a status word', () async {
    final calls = <String, int>{};
    final health = ProviderHealthService(
      AppSettings(),
      probe: (uri, timeout) async {
        if (uri.host == 'cinesrc.st') {
          final index = calls['cinesrc.st'] ?? 0;
          calls['cinesrc.st'] = index + 1;
          return (statusCode: 200, ms: <int>[100, 900, 500][index]);
        }
        return (statusCode: 200, ms: 100);
      },
    );

    final results = await health.checkAll();

    expect(results.length, allProviders.length);
    expect(calls['cinesrc.st'], ProviderHealthService.attempts);
    final cinesrc = results.firstWhere((row) => row.providerId == 'cinesrc');
    expect(cinesrc.ms, 500);
    expect(cinesrc.status, HealthStatus.ok);
    expect(cinesrc.reachable, isTrue);
    // Fastest first.
    expect(results.first.providerId, isNot('cinesrc'));
  });

  test('the thresholds are fast <300, ok 300-999 and slow >=1000', () async {
    final health = ProviderHealthService(
      AppSettings(),
      probe: fixedProbe(<String, int>{
        'cinesrc.st': 299,
        'www.rivestream.app': 300,
        'toflix.co': 999,
        'embed.cinelite.xyz': 1000,
      }),
    );

    final results = await health.checkAll();
    HealthStatus statusOf(String id) =>
        results.firstWhere((row) => row.providerId == id).status;

    expect(statusOf('cinesrc'), HealthStatus.fast);
    expect(statusOf('rivestream'), HealthStatus.ok);
    expect(statusOf('toflix'), HealthStatus.ok);
    expect(statusOf('cinelite'), HealthStatus.slow);
    expect(ProviderHealthService.statusWord(HealthStatus.timedOut), 'timed out');
  });

  test('a timeout is timedOut and a connection failure is unreachable', () async {
    final health = ProviderHealthService(
      AppSettings(),
      probe: (uri, timeout) async {
        if (uri.host == 'cinesrc.st') {
          throw TimeoutException('slow');
        }
        if (uri.host == 'player.moonflix.website') {
          throw http.ClientException('refused');
        }
        return (statusCode: 200, ms: 100);
      },
    );

    final results = await health.checkAll();
    final timedOut = results.firstWhere((row) => row.providerId == 'cinesrc');
    final unreachable = results.firstWhere(
      (row) => row.providerId == 'moonflix',
    );

    expect(timedOut.status, HealthStatus.timedOut);
    expect(timedOut.ms, isNull);
    expect(timedOut.reachable, isFalse);
    expect(unreachable.status, HealthStatus.unreachable);
    expect(unreachable.httpStatus, isNull);
    expect(results.last.providerId, anyOf('cinesrc', 'moonflix'));
  });

  test('an HTTP error is a response, so the provider stays reachable', () async {
    final health = ProviderHealthService(
      AppSettings(),
      probe: (uri, timeout) async {
        if (uri.host == 'cinesrc.st') {
          return (statusCode: 403, ms: 120);
        }
        return (statusCode: 200, ms: 100);
      },
    );

    final results = await health.checkAll();
    final cinesrc = results.firstWhere((row) => row.providerId == 'cinesrc');

    expect(cinesrc.status, HealthStatus.fast);
    expect(cinesrc.httpStatus, 403);
    expect(cinesrc.reachable, isTrue);
  });

  test('one HEAD per attempt to the base host only', () async {
    final requests = <http.Request>[];
    final health = ProviderHealthService(
      AppSettings(),
      client: MockClient((request) async {
        requests.add(request);
        return http.Response('', 200);
      }),
    );

    await health.checkAll();

    expect(
      requests.length,
      allProviders.length * ProviderHealthService.attempts,
    );
    expect(requests.every((request) => request.method == 'HEAD'), isTrue);
    expect(requests.every((request) => request.url.path == '/'), isTrue);
    final cinesrc = requests
        .where((request) => request.url.host == 'cinesrc.st')
        .toList();
    expect(cinesrc.length, ProviderHealthService.attempts);
    expect(cinesrc.first.url.toString(), 'https://cinesrc.st/');
  });

  test('fresh results are cached and force rechecks', () async {
    var calls = 0;
    final health = ProviderHealthService(
      AppSettings(),
      probe: (uri, timeout) async {
        calls++;
        return (statusCode: 200, ms: 100);
      },
    );

    await health.checkAll();
    final first = calls;
    expect(first, allProviders.length * ProviderHealthService.attempts);

    await health.checkAll();
    expect(calls, first);

    await health.checkAll(force: true);
    expect(calls, first * 2);
  });

  test('the cache survives a new service and expires after ten minutes', () async {
    var calls = 0;
    var now = DateTime.utc(2026, 1, 1, 12);
    Future<({int statusCode, int ms})> probe(Uri uri, Duration timeout) async {
      calls++;
      return (statusCode: 200, ms: 100);
    }

    await ProviderHealthService(
      AppSettings(),
      probe: probe,
      clock: () => now,
    ).checkAll();
    final first = calls;

    // A fresh service reads the persisted cache.
    await ProviderHealthService(
      AppSettings(),
      probe: probe,
      clock: () => now,
    ).checkAll();
    expect(calls, first);

    now = now.add(const Duration(minutes: 9));
    await ProviderHealthService(
      AppSettings(),
      probe: probe,
      clock: () => now,
    ).checkAll();
    expect(calls, first);

    now = now.add(const Duration(minutes: 2));
    await ProviderHealthService(
      AppSettings(),
      probe: probe,
      clock: () => now,
    ).checkAll();
    expect(calls, first * 2);
  });

  test('fastest skips unreachable providers and honours the allowed ids', () async {
    final health = ProviderHealthService(
      AppSettings(),
      probe: (uri, timeout) async {
        switch (uri.host) {
          case 'cinesrc.st':
            return (statusCode: 200, ms: 500);
          case 'www.rivestream.app':
            return (statusCode: 200, ms: 120);
          case 'player.moonflix.website':
            throw http.ClientException('refused');
          default:
            return (statusCode: 200, ms: 100);
        }
      },
    );
    await health.checkAll();

    expect(
      health.fastest(<String>['cinesrc', 'rivestream', 'moonflix']),
      'rivestream',
    );
    expect(health.fastest(<String>['moonflix']), isNull);
    expect(health.fastest(<String>['not-a-provider']), isNull);
  });

  test('describe uses the provider label and the PC wording', () {
    final now = DateTime.utc(2026, 1, 1);
    final health = ProviderHealthService(AppSettings());

    expect(
      health.describe(
        ProviderHealth(
          providerId: 'cinesrc',
          status: HealthStatus.fast,
          ms: 180,
          checkedAt: now,
        ),
      ),
      'CineSrc · 180 ms',
    );
    expect(
      health.describe(
        ProviderHealth(
          providerId: 'moonflix',
          status: HealthStatus.unreachable,
          checkedAt: now,
        ),
      ),
      'Moonflix · unreachable',
    );
    expect(
      health.describe(
        ProviderHealth(
          providerId: 'moonflix',
          status: HealthStatus.timedOut,
          checkedAt: now,
        ),
      ),
      'Moonflix · timed out',
    );
  });

  test('cached is null once a result is stale', () async {
    var now = DateTime.utc(2026, 1, 1, 12);
    final health = ProviderHealthService(
      AppSettings(),
      probe: fixedProbe(<String, int>{}),
      clock: () => now,
    );
    await health.checkAll();

    expect(health.cached('cinesrc'), isNotNull);
    now = now.add(const Duration(minutes: 11));
    expect(health.cached('cinesrc'), isNull);
  });
}
