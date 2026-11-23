import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lumora_mobile/data/media_repository.dart';
import 'package:lumora_mobile/data/settings_store.dart';
import 'package:lumora_mobile/models/media_item.dart';
import 'package:lumora_mobile/nav.dart';
import 'package:provider/provider.dart';

MediaItem movie() => MediaItem(
      id: '1',
      title: 'The Quiet Orbit',
      kind: MediaKind.movie,
      tmdbId: 603,
    );

MediaItem anime() => MediaItem(
      id: '2',
      title: 'Some Anime',
      kind: MediaKind.anime,
      tmdbId: 99,
      anilistId: 4242,
    );

void main() {
  group('pickProvider', () {
    test('uses the default provider when it is enabled and can play', () {
      final settings = AppSettings(
        enabledProviderIds: <String>['cinesrc', 'rivestream'],
        defaultProviderId: 'rivestream',
      );
      expect(pickProvider(settings, movie())?.id, 'rivestream');
    });

    test('falls back to the first enabled provider that supports the kind', () {
      final settings =
          AppSettings(enabledProviderIds: <String>['ani', 'cinesrc']);
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

    test('keeps the user order for anime and the TMDB providers', () {
      final settings = AppSettings(
        enabledProviderIds: <String>['toflix', 'ani'],
      );
      expect(pickProvider(settings, anime())?.id, 'toflix');
    });
  });

  group('playbackTargets', () {
    test('builds one target per enabled provider that can play, in order', () {
      final settings = AppSettings(
        enabledProviderIds: <String>['rivestream', 'ani', 'cinesrc'],
      );
      final targets = playbackTargets(settings, movie());
      expect(
        targets.map((t) => t.providerId).toList(),
        <String>['rivestream', 'cinesrc'],
      );
      expect(
        targets.first.url.toString(),
        'https://www.rivestream.app/embed?type=movie&id=603',
      );
    });

    test('carries season and episode into a series URL', () {
      final settings = AppSettings(enabledProviderIds: <String>['cinesrc']);
      final series = MediaItem(
        id: '3',
        title: 'Night Market',
        kind: MediaKind.tv,
        tmdbId: 1399,
      );
      final targets = playbackTargets(settings, series, season: 2, episode: 5);
      expect(
        targets.single.url.toString(),
        'https://cinesrc.st/embed/tv/1399?s=2&e=5',
      );
    });

    test('skips a provider whose route needs an id the title lacks', () {
      final settings = AppSettings(enabledProviderIds: <String>['ani']);
      final noAniList = MediaItem(
        id: '4',
        title: 'No id',
        kind: MediaKind.anime,
      );
      expect(playbackTargets(settings, noAniList), isEmpty);
    });
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
