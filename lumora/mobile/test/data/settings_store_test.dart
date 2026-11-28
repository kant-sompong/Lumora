import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lumora_mobile/data/settings_store.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() {
    SharedPreferences.setMockInitialValues(<String, Object>{});
  });

  test('starts with the contract defaults', () {
    final settings = AppSettings();
    expect(settings.tmdbApiKey, '');
    expect(settings.enabledProviderIds, isEmpty);
    expect(settings.defaultProviderId, isNull);
    expect(settings.themeMode, ThemeMode.system);
    expect(settings.accent.toARGB32(), 0xFF8557EA);
    expect(settings.compactCards, isFalse);
    expect(settings.malClientId, '');
    expect(settings.autoProvider, isTrue);
    expect(settings.uiScale, 1.0);
    expect(settings.defaultAudio, 'sub');
    expect(settings.autoplay, isTrue);
  });

  test('drops unknown and duplicate provider ids but keeps the order', () {
    final settings = AppSettings(
      enabledProviderIds: <String>['rivestream', 'nope', 'cinesrc', 'rivestream'],
    );
    expect(settings.enabledProviderIds, <String>['rivestream', 'cinesrc']);
  });

  test('reports and toggles providers', () {
    final settings = AppSettings(enabledProviderIds: <String>['cinesrc']);
    expect(settings.isProviderEnabled('cinesrc'), isTrue);
    expect(settings.isProviderEnabled('rivestream'), isFalse);

    settings.setProviderEnabled('rivestream', true);
    expect(settings.enabledProviderIds, <String>['cinesrc', 'rivestream']);
    expect(settings.isProviderEnabled('rivestream'), isTrue);

    settings.setProviderEnabled('cinesrc', false);
    expect(settings.enabledProviderIds, <String>['rivestream']);
    expect(settings.isProviderEnabled('cinesrc'), isFalse);
  });

  test('setProviderEnabled ignores unknown ids and no-op changes', () {
    final settings = AppSettings(enabledProviderIds: <String>['cinesrc']);
    var notifications = 0;
    settings.addListener(() => notifications++);

    settings.setProviderEnabled('not-a-provider', true);
    settings.setProviderEnabled('cinesrc', true);
    expect(notifications, 0);
    expect(settings.enabledProviderIds, <String>['cinesrc']);
  });

  test('setEnabledProviderIds replaces the order and notifies', () {
    final settings = AppSettings(
      enabledProviderIds: <String>['cinesrc', 'rivestream'],
    );
    var notifications = 0;
    settings.addListener(() => notifications++);

    settings.setEnabledProviderIds(<String>['rivestream', 'cinesrc']);
    expect(settings.enabledProviderIds, <String>['rivestream', 'cinesrc']);
    expect(notifications, 1);

    settings.setProviderEnabled('rivestream', false);
    expect(settings.enabledProviderIds, <String>['cinesrc']);
    expect(notifications, 2);
  });

  test('clears a default provider that gets switched off', () {
    final settings = AppSettings(
      enabledProviderIds: <String>['cinesrc', 'rivestream'],
      defaultProviderId: 'cinesrc',
    );
    settings.setProviderEnabled('cinesrc', false);
    expect(settings.defaultProviderId, isNull);
  });

  test('the default provider setter takes a known id and notifies', () {
    final settings = AppSettings(enabledProviderIds: <String>['rivestream']);
    var notifications = 0;
    settings.addListener(() => notifications++);

    settings.defaultProviderId = 'rivestream';
    expect(settings.defaultProviderId, 'rivestream');
    expect(notifications, 1);

    settings.defaultProviderId = 'nope';
    expect(settings.defaultProviderId, 'rivestream');
    expect(notifications, 1);
  });

  test('accent compares by ARGB value', () {
    final settings = AppSettings();
    var notifications = 0;
    settings.addListener(() => notifications++);

    settings.accent = const Color(0xFF0F9D8F);
    expect(settings.accent.toARGB32(), 0xFF0F9D8F);
    settings.accent = const Color(0xFF0F9D8F);
    expect(notifications, 1);
  });

  test('save and load round-trip every setting', () async {
    final settings = AppSettings(
      tmdbApiKey: 'abc123',
      enabledProviderIds: <String>['rivestream', 'cinesrc', 'ani'],
      defaultProviderId: 'rivestream',
      themeMode: ThemeMode.light,
      accent: const Color(0xFF0F9D8F),
      compactCards: true,
      defaultAudio: 'dub',
      autoplay: false,
    );
    await settings.save();

    final reloaded = await AppSettings.load();
    expect(reloaded.tmdbApiKey, 'abc123');
    expect(reloaded.enabledProviderIds, <String>['rivestream', 'cinesrc', 'ani']);
    expect(reloaded.defaultProviderId, 'rivestream');
    expect(reloaded.themeMode, ThemeMode.light);
    expect(reloaded.accent.toARGB32(), 0xFF0F9D8F);
    expect(reloaded.compactCards, isTrue);
    expect(reloaded.defaultAudio, 'dub');
    expect(reloaded.autoplay, isFalse);
  });

  test('load seeds every provider in PC order on a first run', () async {
    final settings = await AppSettings.load();
    expect(settings.enabledProviderIds, AppSettings.defaultProviderIds);
    expect(settings.enabledProviderIds.first, 'cinesrc');
    expect(settings.enabledProviderIds.last, 'ani');
    expect(settings.defaultProviderId, isNull);
  });

  test('load drops provider ids this build no longer knows', () async {
    final settings = AppSettings(
      enabledProviderIds: <String>['cinesrc', 'gone'],
    );
    await settings.save();
    final reloaded = await AppSettings.load();
    expect(reloaded.enabledProviderIds, <String>['cinesrc']);
  });

  test('theme mode names round-trip and junk falls back to system', () {
    for (final mode in ThemeMode.values) {
      expect(
        AppSettings.parseThemeMode(AppSettings.themeModeName(mode)),
        mode,
      );
    }
    expect(AppSettings.parseThemeMode(null), ThemeMode.system);
    expect(AppSettings.parseThemeMode('nonsense'), ThemeMode.system);
  });

  test('the MAL Client ID is trimmed and never compared in the clear', () {
    final settings = AppSettings();
    var notifications = 0;
    settings.addListener(() => notifications++);

    settings.malClientId = '  abc123  ';
    expect(settings.malClientId, 'abc123');
    settings.malClientId = 'abc123';
    expect(notifications, 1);
  });

  test('the UI scale is clamped to 0.8-1.3', () {
    final settings = AppSettings();
    var notifications = 0;
    settings.addListener(() => notifications++);

    settings.uiScale = 0.5;
    expect(settings.uiScale, AppSettings.minUiScale);
    settings.uiScale = 2.0;
    expect(settings.uiScale, AppSettings.maxUiScale);
    settings.uiScale = 1.15;
    expect(settings.uiScale, 1.15);
    settings.uiScale = 1.15;
    expect(notifications, 3);
    expect(AppSettings(uiScale: 9).uiScale, AppSettings.maxUiScale);
  });

  test('the new settings save and load with their snake_case keys', () async {
    final settings = AppSettings(
      malClientId: ' mal-client ',
      autoProvider: false,
      uiScale: 1.2,
    );
    await settings.save();

    final prefs = await SharedPreferences.getInstance();
    expect(prefs.getString(AppSettings.keyMalClientId), 'mal-client');
    expect(prefs.getBool(AppSettings.keyAutoProvider), isFalse);
    expect(prefs.getDouble(AppSettings.keyUiScale), 1.2);

    final reloaded = await AppSettings.load();
    expect(reloaded.malClientId, 'mal-client');
    expect(reloaded.autoProvider, isFalse);
    expect(reloaded.uiScale, 1.2);
  });

  test('junk stored values fall back to the new defaults', () async {
    SharedPreferences.setMockInitialValues(<String, Object>{
      AppSettings.keyMalClientId: 7,
      AppSettings.keyAutoProvider: 'yes',
      AppSettings.keyUiScale: 'big',
      AppSettings.keyDefaultAudio: 3,
      AppSettings.keyAutoplay: 'no',
    });

    final settings = await AppSettings.load();
    expect(settings.malClientId, '');
    expect(settings.autoProvider, isTrue);
    expect(settings.uiScale, 1.0);
    expect(settings.defaultAudio, 'sub');
    expect(settings.autoplay, isTrue);
  });

  test('the audio track saves and loads, and junk falls back to sub', () async {
    final settings = AppSettings(defaultAudio: 'vostfr', autoplay: false);
    await settings.save();

    final prefs = await SharedPreferences.getInstance();
    expect(prefs.getString(AppSettings.keyDefaultAudio), 'vostfr');
    expect(prefs.getBool(AppSettings.keyAutoplay), isFalse);

    final reloaded = await AppSettings.load();
    expect(reloaded.defaultAudio, 'vostfr');
    expect(reloaded.autoplay, isFalse);

    // Only the tracks the players accept are kept.
    reloaded.defaultAudio = 'klingon';
    expect(reloaded.defaultAudio, 'sub');
    expect(AppSettings(defaultAudio: 'DUB').defaultAudio, 'dub');
  });

  test('an out-of-range stored scale is clamped on load', () async {
    SharedPreferences.setMockInitialValues(<String, Object>{
      AppSettings.keyUiScale: 4.2,
    });

    final settings = await AppSettings.load();
    expect(settings.uiScale, AppSettings.maxUiScale);
  });
}
