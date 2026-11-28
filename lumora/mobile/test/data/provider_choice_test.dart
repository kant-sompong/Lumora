import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:lumora_mobile/data/anime_identity.dart';
import 'package:lumora_mobile/data/provider_choice.dart';
import 'package:lumora_mobile/data/provider_health.dart';
import 'package:lumora_mobile/data/settings_store.dart';
import 'package:lumora_mobile/models/media_item.dart';
import 'package:shared_preferences/shared_preferences.dart';

const MediaItem _movie = MediaItem(
  id: 'tmdb:1',
  title: 'A Film',
  kind: MediaKind.movie,
  tmdbId: 1,
);

const MediaItem _show = MediaItem(
  id: 'tmdb:2',
  title: 'A Show',
  kind: MediaKind.tv,
  tmdbId: 2,
);

const MediaItem _anime = MediaItem(
  id: 'anilist:1',
  title: 'An Anime',
  kind: MediaKind.anime,
  anilistId: 1,
);

Future<ProviderHealthService> healthFor(Map<String, int> milliseconds) async {
  final health = ProviderHealthService(
    AppSettings(),
    probe: (uri, timeout) async {
      final ms = milliseconds[uri.host];
      if (ms == null) {
        throw http.ClientException('unreachable');
      }
      return (statusCode: 200, ms: ms);
    },
  );
  await health.checkAll();
  return health;
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() {
    SharedPreferences.setMockInitialValues(<String, Object>{});
  });

  test('anime plays on anipm.pk first when it is enabled', () {
    final settings = AppSettings(
      enabledProviderIds: <String>['cinesrc', 'toflix', 'ani'],
    );

    expect(
      orderedProviders(item: _anime, settings: settings),
      <String>['ani', 'toflix'],
    );
  });

  test('a disabled anipm.pk leaves the user order, with no anime-only extras', () {
    final settings = AppSettings(
      enabledProviderIds: <String>['cinesrc', 'toflix'],
    );

    expect(
      orderedProviders(item: _anime, settings: settings),
      <String>['toflix'],
    );
  });

  test('a verified anime from a TV row is routed as anime', () {
    final settings = AppSettings(
      enabledProviderIds: <String>['cinesrc', 'toflix', 'ani'],
    );
    const identity = AnimeIdentity(
      status: IdentityStatus.anime,
      malId: 1,
      anilistId: 9001,
    );

    expect(
      orderedProviders(item: _show, settings: settings, identity: identity),
      <String>['ani', 'toflix'],
    );
  });

  test('an unverified identity leaves a TV row on the normal path', () {
    final settings = AppSettings(
      enabledProviderIds: <String>['cinesrc', 'toflix', 'ani'],
    );
    const identity = AnimeIdentity(
      status: IdentityStatus.unverified,
      reason: AnimeIdentity.reasonNoMalMatch,
    );

    expect(
      orderedProviders(item: _show, settings: settings, identity: identity),
      <String>['cinesrc', 'toflix'],
    );
  });

  test('automatic mode puts the fastest reachable provider first', () async {
    final settings = AppSettings(
      enabledProviderIds: <String>['cinesrc', 'rivestream'],
      defaultProviderId: 'rivestream',
    );
    final health = await healthFor(<String, int>{
      'cinesrc.st': 120,
      'www.rivestream.app': 500,
    });

    expect(
      orderedProviders(item: _movie, settings: settings, health: health),
      <String>['cinesrc', 'rivestream'],
    );
  });

  test('with automatic mode off the saved default comes first', () async {
    final settings = AppSettings(
      enabledProviderIds: <String>['cinesrc', 'rivestream'],
      defaultProviderId: 'rivestream',
      autoProvider: false,
    );
    final health = await healthFor(<String, int>{
      'cinesrc.st': 120,
      'www.rivestream.app': 500,
    });

    expect(
      orderedProviders(item: _movie, settings: settings, health: health),
      <String>['rivestream', 'cinesrc'],
    );
  });

  test('without health data the saved default comes first', () {
    final settings = AppSettings(
      enabledProviderIds: <String>['cinesrc', 'rivestream'],
      defaultProviderId: 'rivestream',
    );

    expect(
      orderedProviders(item: _movie, settings: settings),
      <String>['rivestream', 'cinesrc'],
    );
  });

  test('a disabled default provider is dropped', () {
    final settings = AppSettings(
      enabledProviderIds: <String>['cinesrc'],
      defaultProviderId: 'rivestream',
    );

    expect(
      orderedProviders(item: _movie, settings: settings),
      <String>['cinesrc'],
    );
  });

  test('a provider that cannot play the kind is never listed', () {
    final settings = AppSettings(
      enabledProviderIds: <String>['ani', 'cinesrc'],
    );

    expect(
      orderedProviders(item: _show, settings: settings),
      <String>['cinesrc'],
    );
    expect(
      orderedProviders(item: _anime, settings: settings),
      <String>['ani'],
    );
  });

  test('when nothing is reachable the saved default still leads', () async {
    final settings = AppSettings(
      enabledProviderIds: <String>['cinesrc', 'rivestream'],
      defaultProviderId: 'rivestream',
    );
    final health = await healthFor(<String, int>{});

    expect(
      orderedProviders(item: _movie, settings: settings, health: health),
      <String>['rivestream', 'cinesrc'],
    );
  });
}
