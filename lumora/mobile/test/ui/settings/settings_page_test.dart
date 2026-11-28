import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:lumora_mobile/data/provider_health.dart';
import 'package:lumora_mobile/data/providers_registry.dart';
import 'package:lumora_mobile/data/settings_store.dart';
import 'package:lumora_mobile/theme/lumora_theme.dart';
import 'package:lumora_mobile/ui/settings/settings_page.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

Widget wrap(
  AppSettings settings, {
  ProviderHealthService? health,
  double systemTextScale = 1.0,
}) {
  final app = ChangeNotifierProvider<AppSettings>.value(
    value: settings,
    child: MaterialApp(
      theme: LumoraTheme.dark(settings.accent),
      // A large accessibility text size, as a phone can be set to.
      builder: (BuildContext context, Widget? child) => MediaQuery(
        data: MediaQuery.of(context).copyWith(
          textScaler: TextScaler.linear(systemTextScale),
        ),
        child: child ?? const SizedBox.shrink(),
      ),
      home: const SettingsPage(),
    ),
  );
  if (health == null) {
    return app;
  }
  return Provider<ProviderHealthService>.value(value: health, child: app);
}

/// Pump the page on a surface tall enough that every row is laid out and
/// tappable without scrolling.
Future<void> pumpSettings(
  WidgetTester tester,
  AppSettings settings, {
  ProviderHealthService? health,
  double systemTextScale = 1.0,
  double height = 2600,
}) async {
  tester.view.physicalSize = Size(420, height);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(
    wrap(settings, health: health, systemTextScale: systemTextScale),
  );
  await tester.pumpAndSettle();
}

ProviderHealthService instantHealth(AppSettings settings) {
  return ProviderHealthService(
    settings,
    client: MockClient((http.Request request) async => http.Response('', 200)),
  );
}

void main() {
  setUp(() {
    SharedPreferences.setMockInitialValues(<String, Object>{});
  });

  testWidgets('renders exactly the General and Providers tabs', (
    WidgetTester tester,
  ) async {
    final settings = AppSettings(enabledProviderIds: <String>['cinesrc']);
    await pumpSettings(tester, settings);

    expect(find.text('Settings'), findsOneWidget);
    final tabs = find.byType(Tab);
    expect(tabs, findsNWidgets(2));
    expect(find.text('General'), findsOneWidget);
    expect(find.text('Providers'), findsOneWidget);
  });

  testWidgets('General shows theme, accent, compact, size and About', (
    WidgetTester tester,
  ) async {
    await pumpSettings(tester, AppSettings());

    expect(find.text('Theme'), findsOneWidget);
    expect(find.text('System'), findsOneWidget);
    expect(find.text('Dark'), findsOneWidget);
    expect(find.text('Light'), findsOneWidget);
    expect(find.text('Accent colour'), findsOneWidget);
    for (final choice in accentChoices) {
      expect(find.byTooltip(choice.label), findsOneWidget);
    }
    expect(find.text('Compact layout'), findsOneWidget);
    expect(find.text('UI size: text, buttons and spacing follow this. 100% is '
        'the design size.'), findsOneWidget);
    expect(find.text('100%'), findsWidgets);
    expect(find.text('Reset to 100%'), findsOneWidget);
    expect(find.text(appVersion), findsOneWidget);
    expect(find.text(tmdbAttribution), findsOneWidget);
  });

  testWidgets('tapping an accent preset updates and saves the setting', (
    WidgetTester tester,
  ) async {
    final settings = AppSettings();
    await pumpSettings(tester, settings);

    await tester.tap(find.byTooltip('Teal'));
    await tester.pumpAndSettle();

    expect(settings.accent.toARGB32(), 0xFF0F9D8F);
    final reloaded = await AppSettings.load();
    expect(reloaded.accent.toARGB32(), 0xFF0F9D8F);
  });

  testWidgets('the UI size slider moves the setting and persists it', (
    WidgetTester tester,
  ) async {
    final settings = AppSettings();
    await pumpSettings(tester, settings);
    expect(settings.uiScale, 1.0);

    await tester.drag(find.byType(Slider), const Offset(60, 0));
    await tester.pumpAndSettle();

    expect(settings.uiScale, greaterThan(1.0));
    final reloaded = await AppSettings.load();
    expect(reloaded.uiScale, settings.uiScale);

    // The setter keeps the slider's 80-130% range.
    settings.uiScale = 4.0;
    expect(settings.uiScale, AppSettings.maxUiScale);
    settings.uiScale = 0.1;
    expect(settings.uiScale, AppSettings.minUiScale);
  });

  testWidgets('Reset returns the UI size to 100%', (
    WidgetTester tester,
  ) async {
    final settings = AppSettings(uiScale: 1.2);
    await pumpSettings(tester, settings);

    await tester.tap(find.text('Reset to 100%'));
    await tester.pumpAndSettle();

    expect(settings.uiScale, 1.0);
    final reloaded = await AppSettings.load();
    expect(reloaded.uiScale, 1.0);
  });

  testWidgets('switching a provider off persists it', (
    WidgetTester tester,
  ) async {
    final settings = AppSettings(
      enabledProviderIds: <String>['cinesrc', 'rivestream'],
      defaultProviderId: 'cinesrc',
    );
    await pumpSettings(tester, settings);
    await tester.tap(find.text('Providers'));
    await tester.pumpAndSettle();

    final row = find
        .ancestor(of: find.text('CineSrc'), matching: find.byType(Row))
        .first;
    await tester.tap(find.descendant(of: row, matching: find.byType(Switch)));
    await tester.pumpAndSettle();

    expect(settings.enabledProviderIds, <String>['rivestream']);
    final reloaded = await AppSettings.load();
    expect(reloaded.enabledProviderIds, <String>['rivestream']);
  });

  testWidgets('reordering changes the stored provider order', (
    WidgetTester tester,
  ) async {
    final settings = AppSettings(
      enabledProviderIds: <String>['cinesrc', 'rivestream'],
    );
    await pumpSettings(tester, settings);
    await tester.tap(find.text('Providers'));
    await tester.pumpAndSettle();

    // Every provider is listed, each with a drag handle.
    expect(find.byIcon(Icons.drag_handle), findsNWidgets(allProviders.length));

    // The list reports a drop of row 0 before row 2, exactly as a drag does.
    final list = tester.widget<ReorderableListView>(
      find.byType(ReorderableListView),
    );
    list.onReorder(0, 2);
    await tester.pumpAndSettle();

    expect(settings.enabledProviderIds, <String>['rivestream', 'cinesrc']);
    final reloaded = await AppSettings.load();
    expect(reloaded.enabledProviderIds, <String>['rivestream', 'cinesrc']);
  });

  testWidgets('the default provider is chosen with the radio control', (
    WidgetTester tester,
  ) async {
    final settings = AppSettings(
      enabledProviderIds: <String>['cinesrc', 'rivestream'],
    );
    await pumpSettings(tester, settings);
    await tester.tap(find.text('Providers'));
    await tester.pumpAndSettle();

    expect(find.byIcon(Icons.radio_button_checked), findsNothing);
    await tester.tap(find.byIcon(Icons.radio_button_unchecked).at(1));
    await tester.pumpAndSettle();

    expect(settings.defaultProviderId, 'rivestream');
    final reloaded = await AppSettings.load();
    expect(reloaded.defaultProviderId, 'rivestream');
  });

  testWidgets('Providers shows the TMDB key field and Test key', (
    WidgetTester tester,
  ) async {
    await pumpSettings(tester, AppSettings());
    await tester.tap(find.text('Providers'));
    await tester.pumpAndSettle();

    expect(find.text('TMDB API key'), findsOneWidget);
    expect(find.text('Test key'), findsOneWidget);
    expect(find.textContaining('themoviedb.org/settings/api'), findsOneWidget);
  });

  testWidgets('Providers shows the optional MyAnimeList Client ID row', (
    WidgetTester tester,
  ) async {
    await pumpSettings(tester, AppSettings());
    await tester.tap(find.text('Providers'));
    await tester.pumpAndSettle();

    expect(find.text('MyAnimeList Client ID'), findsOneWidget);
    expect(find.text('Test'), findsOneWidget);
    expect(find.textContaining('myanimelist.net/apiconfig'), findsOneWidget);

    // It is a secret: the field is obscured and never shown in clear.
    final field = tester.widget<TextField>(
      find.byKey(const Key('malClientIdField')),
    );
    expect(field.obscureText, isTrue);
  });

  testWidgets('testing an empty Client ID does not call MyAnimeList', (
    WidgetTester tester,
  ) async {
    final settings = AppSettings();
    await pumpSettings(tester, settings);
    await tester.tap(find.text('Providers'));
    await tester.pumpAndSettle();

    await tester.tap(find.text('Test'));
    await tester.pumpAndSettle();

    expect(find.text('Paste your MyAnimeList Client ID first.'), findsOneWidget);
    expect(settings.malClientId, '');
  });

  testWidgets('the automatic provider switch persists', (
    WidgetTester tester,
  ) async {
    final settings = AppSettings(enabledProviderIds: <String>['cinesrc']);
    await pumpSettings(tester, settings);
    await tester.tap(find.text('Providers'));
    await tester.pumpAndSettle();

    final row = find.ancestor(
      of: find.text('Choose the fastest provider automatically'),
      matching: find.byType(SwitchListTile),
    );
    expect(row, findsOneWidget);
    expect(
      find.text(
        'Test the providers at launch and use whichever answers quickest.',
      ),
      findsOneWidget,
    );

    await tester.tap(find.descendant(of: row, matching: find.byType(Switch)));
    await tester.pumpAndSettle();

    expect(settings.autoProvider, isFalse);
    final reloaded = await AppSettings.load();
    expect(reloaded.autoProvider, isFalse);
  });

  testWidgets('the manual provider switch mirrors the automatic one', (
    WidgetTester tester,
  ) async {
    final settings = AppSettings(enabledProviderIds: <String>['cinesrc']);
    await pumpSettings(tester, settings);
    await tester.tap(find.text('Providers'));
    await tester.pumpAndSettle();

    final auto = find.byKey(const Key('autoProviderSwitch'));
    final manual = find.byKey(const Key('manualProviderSwitch'));
    expect(tester.widget<SwitchListTile>(auto).value, isTrue);
    expect(tester.widget<SwitchListTile>(manual).value, isFalse);
    expect(find.text('I pick the provider myself'), findsOneWidget);

    // Switching the manual mode on switches the automatic choice off.
    await tester.tap(find.descendant(of: manual, matching: find.byType(Switch)));
    await tester.pumpAndSettle();

    expect(settings.autoProvider, isFalse);
    expect(tester.widget<SwitchListTile>(auto).value, isFalse);
    expect(tester.widget<SwitchListTile>(manual).value, isTrue);
    expect((await AppSettings.load()).autoProvider, isFalse);
  });

  testWidgets('picking a default provider turns the automatic choice off', (
    WidgetTester tester,
  ) async {
    final settings = AppSettings(
      enabledProviderIds: <String>['cinesrc', 'rivestream'],
    );
    await pumpSettings(tester, settings);
    await tester.tap(find.text('Providers'));
    await tester.pumpAndSettle();

    expect(settings.autoProvider, isTrue);
    await tester.tap(find.byIcon(Icons.radio_button_unchecked).at(1));
    await tester.pumpAndSettle();

    expect(settings.defaultProviderId, 'rivestream');
    expect(settings.autoProvider, isFalse);
    final reloaded = await AppSettings.load();
    expect(reloaded.defaultProviderId, 'rivestream');
    expect(reloaded.autoProvider, isFalse);
  });

  testWidgets('the default audio track is chosen and saved', (
    WidgetTester tester,
  ) async {
    final settings = AppSettings(enabledProviderIds: <String>['cinesrc']);
    await pumpSettings(tester, settings, height: 3400);
    await tester.tap(find.text('Providers'));
    await tester.pumpAndSettle();

    expect(find.text('Default audio'), findsOneWidget);
    expect(
      find.text('Sub, dub or VOSTFR where the anime provider offers it.'),
      findsOneWidget,
    );
    expect(settings.defaultAudio, 'sub');

    await tester.tap(find.byKey(const Key('defaultAudioField')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('VOSTFR').last);
    await tester.pumpAndSettle();

    expect(settings.defaultAudio, 'vostfr');
    expect((await AppSettings.load()).defaultAudio, 'vostfr');
  });

  testWidgets('the autoplay switch persists', (WidgetTester tester) async {
    final settings = AppSettings(enabledProviderIds: <String>['cinesrc']);
    await pumpSettings(tester, settings, height: 3400);
    await tester.tap(find.text('Providers'));
    await tester.pumpAndSettle();

    final row = find.byKey(const Key('autoplaySwitch'));
    expect(tester.widget<SwitchListTile>(row).value, isTrue);
    expect(
      find.text('Start playing as soon as an episode or movie opens.'),
      findsOneWidget,
    );

    await tester.tap(find.descendant(of: row, matching: find.byType(Switch)));
    await tester.pumpAndSettle();

    expect(settings.autoplay, isFalse);
    expect((await AppSettings.load()).autoplay, isFalse);
  });

  testWidgets('Check providers shows a dot, a word and a time per provider', (
    WidgetTester tester,
  ) async {
    final settings = AppSettings(
      enabledProviderIds: <String>['cinesrc', 'rivestream'],
    );
    await pumpSettings(tester, settings, health: instantHealth(settings));
    await tester.tap(find.text('Providers'));
    await tester.pumpAndSettle();

    expect(find.text('Not checked'), findsWidgets);
    // The fake HTTP client needs the real event loop, not the test's clock.
    await tester.runAsync(() async {
      await tester.tap(find.text('Check providers'));
      await tester.pump();
      await Future<void>.delayed(const Duration(milliseconds: 200));
    });
    await tester.pumpAndSettle();

    // The health service measures every provider, switched on or not.
    expect(
      find.text('Checked ${allProviders.length} providers.'),
      findsOneWidget,
    );
    expect(find.textContaining('ms'), findsWidgets);
    expect(find.text('Not checked'), findsNothing);
  });

  testWidgets('UI size fits at 0.8, 1.0 and 1.3 with large system text', (
    WidgetTester tester,
  ) async {
    for (final double uiScale in <double>[0.8, 1.0, 1.3]) {
      final settings = AppSettings(
        enabledProviderIds: <String>['cinesrc'],
        uiScale: uiScale,
      );
      await pumpSettings(
        tester,
        settings,
        health: instantHealth(settings),
        systemTextScale: 1.3,
      );

      // A RenderFlex overflow is reported as an exception during layout.
      expect(
        tester.takeException(),
        isNull,
        reason: 'General at UI size $uiScale, system text 1.3',
      );

      // The page receives the app-level text scaler as-is: it never multiplies
      // the system size by uiScale a second time.
      final BuildContext page = tester.element(find.byType(SettingsPage));
      expect(
        MediaQuery.textScalerOf(page).scale(10),
        closeTo(13, 0.001),
        reason: 'double scaling at UI size $uiScale',
      );

      await tester.tap(find.text('Providers'));
      await tester.pumpAndSettle();
      expect(
        tester.takeException(),
        isNull,
        reason: 'Providers at UI size $uiScale, system text 1.3',
      );
    }
  });

  testWidgets('Use fastest makes the quickest provider the default', (
    WidgetTester tester,
  ) async {
    final settings = AppSettings(
      enabledProviderIds: <String>['cinesrc', 'rivestream'],
    );
    await pumpSettings(tester, settings, health: instantHealth(settings));
    await tester.tap(find.text('Providers'));
    await tester.pumpAndSettle();

    await tester.runAsync(() async {
      await tester.tap(find.text('Check providers'));
      await tester.pump();
      await Future<void>.delayed(const Duration(milliseconds: 200));
    });
    await tester.pumpAndSettle();
    await tester.tap(find.text('Use fastest'));
    await tester.pumpAndSettle();

    expect(settings.defaultProviderId, isNotNull);
    expect(settings.enabledProviderIds.first, settings.defaultProviderId);
    expect(find.textContaining('Using '), findsOneWidget);
    final reloaded = await AppSettings.load();
    expect(reloaded.defaultProviderId, settings.defaultProviderId);
  });
}
