import 'package:flutter_test/flutter_test.dart';
import 'package:lumora_mobile/data/settings_store.dart';
import 'package:lumora_mobile/models/media_item.dart';
import 'package:lumora_mobile/nav_stubs.dart';

/// Covers the stand-in URL helpers. When agent A's registry lands and
/// `lib/nav_stubs.dart` is deleted, this file goes with it.
void main() {
  MediaItem movie() => MediaItem(
        id: '1',
        title: 'The Quiet Orbit',
        kind: MediaKind.movie,
        tmdbId: 603,
      );

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

  group('watchUrlFor', () {
    test('returns the chosen provider URL', () {
      final settings = AppSettings(
        enabledProviderIds: <String>['rivestream', 'cinesrc'],
        defaultProviderId: 'cinesrc',
      );
      expect(
        watchUrlFor(settings, movie()).toString(),
        'https://cinesrc.st/embed/movie/603',
      );
    });

    test('returns null when nothing enabled can play the title', () {
      expect(watchUrlFor(AppSettings(), movie()), isNull);
      expect(
        watchUrlFor(
          AppSettings(
            enabledProviderIds: <String>['ani'],
          ),
          movie(),
        ),
        isNull,
      );
    });
  });
}
