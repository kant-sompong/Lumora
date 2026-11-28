import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:lumora_mobile/data/anime_identity.dart';
import 'package:lumora_mobile/data/media_repository.dart';
import 'package:lumora_mobile/data/provider_health.dart';
import 'package:lumora_mobile/data/settings_store.dart';
import 'package:lumora_mobile/models/media_item.dart';
import 'package:lumora_mobile/nav.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

MediaItem movie() => const MediaItem(
  id: '1',
  title: 'The Quiet Orbit',
  kind: MediaKind.movie,
  tmdbId: 603,
);

MediaItem series() => const MediaItem(
  id: '2',
  title: 'Some Show',
  kind: MediaKind.tv,
  tmdbId: 1399,
);

MediaItem anime() => const MediaItem(
  id: '3',
  title: 'Some Anime',
  kind: MediaKind.anime,
  tmdbId: 99,
  anilistId: 4242,
);

/// A health service whose `cinesrc.st` host answers slowly.
ProviderHealthService slowCineSrc(AppSettings settings) {
  return ProviderHealthService(
    settings,
    client: MockClient((http.Request request) async {
      if (request.url.host == 'cinesrc.st') {
        await Future<void>.delayed(const Duration(milliseconds: 40));
      }
      return http.Response('', 200);
    }),
  );
}

/// A health service with canned timings, so no request leaves the test.
ProviderHealthService timedHealth(
  AppSettings settings,
  Map<String, int> milliseconds,
) {
  return ProviderHealthService(
    settings,
    probe: (Uri uri, Duration timeout) async => (
      statusCode: 200,
      ms: milliseconds[uri.host] ?? 500,
    ),
  );
}

void main() {
  setUp(() {
    SharedPreferences.setMockInitialValues(<String, Object>{});
  });

  group('pickProvider', () {
    test('uses the default provider when it is enabled and can play', () {
      final settings = AppSettings(
        enabledProviderIds: <String>['cinesrc', 'rivestream'],
        defaultProviderId: 'rivestream',
      );
      expect(pickProvider(settings, movie())?.id, 'rivestream');
    });

    test('falls back to the first enabled provider that supports the kind', () {
      final settings = AppSettings(
        enabledProviderIds: <String>['ani', 'cinesrc'],
      );
      expect(pickProvider(settings, movie())?.id, 'cinesrc');
    });

    test('ignores a default that cannot play the kind', () {
      final settings = AppSettings(
        enabledProviderIds: <String>['ani', 'cinesrc'],
        defaultProviderId: 'ani',
      );
      expect(pickProvider(settings, movie())?.id, 'cinesrc');
    });

    test('returns null when nothing enabled can play the title', () {
      final settings = AppSettings(enabledProviderIds: <String>['ani']);
      expect(pickProvider(settings, movie()), isNull);
      expect(pickProvider(AppSettings(), movie()), isNull);
    });

    test('automatic choice opens the fastest reachable provider', () async {
      final settings = AppSettings(
        enabledProviderIds: <String>['cinesrc', 'rivestream'],
        defaultProviderId: 'cinesrc',
      );
      final health = timedHealth(settings, <String, int>{
        'cinesrc.st': 900,
        'www.rivestream.app': 120,
      });
      await health.checkAll();

      expect(
        pickProvider(settings, movie(), health: health)?.id,
        'rivestream',
      );
      // Without the health data the saved default still wins.
      expect(pickProvider(settings, movie())?.id, 'cinesrc');
    });

    test('starts anime on anipm.pk even when it is not first in the order', () {
      final settings = AppSettings(
        enabledProviderIds: <String>['toflix', 'ani'],
        defaultProviderId: 'toflix',
      );
      expect(pickProvider(settings, anime())?.id, 'ani');
      expect(pickProvider(settings, movie())?.id, 'toflix');
    });
  });

  group('needsIdentityLookup', () {
    test('only for non-anime titles with a Client ID', () {
      expect(needsIdentityLookup(movie(), AppSettings()), isFalse);
      expect(
        needsIdentityLookup(movie(), AppSettings(malClientId: 'abc')),
        isTrue,
      );
      expect(
        needsIdentityLookup(series(), AppSettings(malClientId: 'abc')),
        isTrue,
      );
      expect(
        needsIdentityLookup(anime(), AppSettings(malClientId: 'abc')),
        isFalse,
      );
      expect(
        needsIdentityLookup(movie(), AppSettings(malClientId: '  ')),
        isFalse,
      );
    });
  });

  group('planPlayback', () {
    test('never looks a title up without a Client ID', () async {
      var looked = false;
      final plan = await planPlayback(
        movie(),
        AppSettings(),
        lookup: (MediaItem item, AppSettings settings) async {
          looked = true;
          return const AnimeIdentity(
            status: IdentityStatus.anime,
            anilistId: 42,
          );
        },
      );
      expect(looked, isFalse);
      expect(plan.item.kind, MediaKind.movie);
      expect(plan.identity, isNull);
    });

    test('an anime title is not looked up again', () async {
      var looked = false;
      final plan = await planPlayback(
        anime(),
        AppSettings(malClientId: 'abc'),
        lookup: (MediaItem item, AppSettings settings) async {
          looked = true;
          return const AnimeIdentity(status: IdentityStatus.unverified);
        },
      );
      expect(looked, isFalse);
      expect(plan.item.anilistId, 4242);
    });

    test('a verified match becomes anime with the AniList id', () async {
      final plan = await planPlayback(
        movie(),
        AppSettings(malClientId: 'abc'),
        lookup: (MediaItem item, AppSettings settings) async =>
            const AnimeIdentity(
              status: IdentityStatus.anime,
              malId: 21,
              anilistId: 42,
            ),
      );
      expect(plan.item.kind, MediaKind.anime);
      expect(plan.item.anilistId, 42);
      // The TMDB id is kept: the film providers still work.
      expect(plan.item.tmdbId, 603);
      expect(plan.identity?.anilistId, 42);
    });

    test('unverified and not-anime leave the title alone', () async {
      for (final status in <IdentityStatus>[
        IdentityStatus.unverified,
        IdentityStatus.notAnime,
      ]) {
        final plan = await planPlayback(
          movie(),
          AppSettings(malClientId: 'abc'),
          lookup: (MediaItem item, AppSettings settings) async =>
              AnimeIdentity(status: status),
        );
        expect(plan.item.kind, MediaKind.movie);
        expect(plan.item.anilistId, isNull);
      }
    });

    test('a throwing lookup never blocks playback', () async {
      final plan = await planPlayback(
        movie(),
        AppSettings(malClientId: 'abc'),
        lookup: (MediaItem item, AppSettings settings) async =>
            throw StateError('offline'),
      );
      expect(plan.item.kind, MediaKind.movie);
    });
  });

  group('playbackTargets', () {
    test('anime goes to anipm.pk first, then the user order', () {
      final settings = AppSettings(
        enabledProviderIds: <String>['toflix', 'ani'],
      );
      final targets = playbackTargets(PlaybackPlan(item: anime()), settings);
      expect(targets.map((t) => t.providerId), <String>['ani', 'toflix']);
    });

    test('a film uses the default provider', () {
      final settings = AppSettings(
        enabledProviderIds: <String>['cinesrc', 'rivestream'],
        defaultProviderId: 'rivestream',
      );
      final targets = playbackTargets(PlaybackPlan(item: movie()), settings);
      expect(targets.first.providerId, 'rivestream');
      expect(targets.map((t) => t.providerId), <String>[
        'rivestream',
        'cinesrc',
      ]);
    });

    test('anime asks the provider for the chosen audio track', () {
      final settings = AppSettings(
        enabledProviderIds: <String>['ani'],
        defaultAudio: 'dub',
      );
      final targets = playbackTargets(
        PlaybackPlan(item: anime()),
        settings,
        episode: 4,
      );
      expect(
        targets.single.url.toString(),
        'https://anipm.pk/embed/ani/4242/4/dub',
      );
      // sub is what a fresh install asks for.
      final fresh = playbackTargets(
        PlaybackPlan(item: anime()),
        AppSettings(enabledProviderIds: <String>['ani']),
        episode: 4,
      );
      expect(fresh.single.url.toString(), 'https://anipm.pk/embed/ani/4242/4/sub');
    });

    test('a verified identity routes a TMDB film to anipm.pk', () {
      final settings = AppSettings(
        enabledProviderIds: <String>['cinesrc', 'ani'],
      );
      final plan = PlaybackPlan(
        item: movie().copyWith(kind: MediaKind.anime, anilistId: 42),
        identity: const AnimeIdentity(
          status: IdentityStatus.anime,
          anilistId: 42,
        ),
      );
      final targets = playbackTargets(plan, settings);
      expect(targets.map((t) => t.providerId), <String>['ani']);
      expect(targets.single.url.path, contains('/42/'));
    });

    test('automatic choice puts the fastest reachable provider first', () async {
      final settings = AppSettings(
        enabledProviderIds: <String>['cinesrc', 'rivestream'],
        defaultProviderId: 'cinesrc',
      );
      final health = slowCineSrc(settings);
      await health.checkAll();
      expect(health.fastest(settings.enabledProviderIds), 'rivestream');

      final targets = playbackTargets(
        PlaybackPlan(item: movie()),
        settings,
        health: health,
      );
      expect(targets.first.providerId, 'rivestream');
    });

    test('automatic choice off keeps the default first', () async {
      final settings = AppSettings(
        enabledProviderIds: <String>['cinesrc', 'rivestream'],
        defaultProviderId: 'cinesrc',
        autoProvider: false,
      );
      final health = slowCineSrc(settings);
      await health.checkAll();

      final targets = playbackTargets(
        PlaybackPlan(item: movie()),
        settings,
        health: health,
      );
      expect(targets.first.providerId, 'cinesrc');
    });

    test('disabled and kind-incompatible providers are never listed', () {
      final settings = AppSettings(
        enabledProviderIds: <String>['cinesrc', 'ani'],
      );
      final targets = playbackTargets(PlaybackPlan(item: movie()), settings);
      expect(targets.map((t) => t.providerId), <String>['cinesrc']);
    });
  });

  testWidgets('the player order comes from the app-level health service', (
    WidgetTester tester,
  ) async {
    final settings = AppSettings(
      enabledProviderIds: <String>['cinesrc', 'rivestream', 'ani', 'toflix'],
      defaultProviderId: 'cinesrc',
    );
    final health = timedHealth(settings, <String, int>{
      'cinesrc.st': 900,
      'www.rivestream.app': 120,
      'anipm.pk': 700,
      'toflix.co': 80,
    });
    await health.checkAll();

    late BuildContext context;
    await tester.pumpWidget(
      MultiProvider(
        providers: [
          ChangeNotifierProvider<AppSettings>.value(value: settings),
          // The same app-level instance Settings writes its check into.
          Provider<ProviderHealthService>.value(value: health),
        ],
        child: MaterialApp(
          home: Builder(
            builder: (BuildContext inner) {
              context = inner;
              return const Scaffold(body: SizedBox());
            },
          ),
        ),
      ),
    );

    // The function openPlayer uses to build the player's list: the quickest
    // reachable provider opens, not the saved default (cinesrc).
    final film = playerTargets(context, PlaybackPlan(item: movie()), settings);
    expect(film.first.providerId, 'toflix');
    expect(film.first.providerId, isNot(settings.defaultProviderId));

    // Anime still goes to anipm.pk first, even though toflix answered faster.
    final animated = playerTargets(
      context,
      PlaybackPlan(item: anime()),
      settings,
    );
    expect(animated.first.providerId, 'ani');
  });

  testWidgets('openPlayer asks for a provider when none are enabled', (
    WidgetTester tester,
  ) async {
    final settings = AppSettings();
    late BuildContext context;
    await tester.pumpWidget(
      MultiProvider(
        providers: [
          ChangeNotifierProvider<AppSettings>.value(value: settings),
          Provider<MediaRepository>(create: (_) => FakeMediaRepository()),
        ],
        child: MaterialApp(
          home: Builder(
            builder: (BuildContext inner) {
              context = inner;
              return const Scaffold(body: SizedBox());
            },
          ),
        ),
      ),
    );

    await openPlayer(context, movie());
    await tester.pump();

    expect(find.text('Enable a provider in Settings'), findsOneWidget);
    expect(find.text('Settings'), findsOneWidget);
  });
}
