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
    );
    await settings.save();

    final reloaded = await AppSettings.load();
    expect(reloaded.tmdbApiKey, 'abc123');
    expect(reloaded.enabledProviderIds, <String>['rivestream', 'cinesrc', 'ani']);
    expect(reloaded.defaultProviderId, 'rivestream');
    expect(reloaded.themeMode, ThemeMode.light);
    expect(reloaded.accent.toARGB32(), 0xFF0F9D8F);
    expect(reloaded.compactCards, isTrue);
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
}
