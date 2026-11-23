import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lumora_mobile/data/providers_registry.dart';
import 'package:lumora_mobile/data/settings_store.dart';
import 'package:lumora_mobile/theme/lumora_theme.dart';
import 'package:lumora_mobile/ui/settings/settings_page.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

Widget wrap(AppSettings settings) {
  return ChangeNotifierProvider<AppSettings>.value(
    value: settings,
    child: MaterialApp(
      theme: LumoraTheme.dark(settings.accent),
      home: const SettingsPage(),
    ),
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
    await tester.pumpWidget(wrap(settings));

    expect(find.text('Settings'), findsOneWidget);
    final tabs = find.byType(Tab);
    expect(tabs, findsNWidgets(2));
    expect(find.text('General'), findsOneWidget);
    expect(find.text('Providers'), findsOneWidget);
  });

  testWidgets('General shows theme, accent, compact and About', (
    WidgetTester tester,
  ) async {
    await tester.pumpWidget(wrap(AppSettings()));

    expect(find.text('Theme'), findsOneWidget);
    expect(find.text('System'), findsOneWidget);
    expect(find.text('Dark'), findsOneWidget);
    expect(find.text('Light'), findsOneWidget);
    expect(find.text('Accent colour'), findsOneWidget);
    for (final choice in accentChoices) {
      expect(find.byTooltip(choice.label), findsOneWidget);
    }
    expect(find.text('Compact layout'), findsOneWidget);
    expect(find.text(appVersion), findsOneWidget);
    expect(find.text(tmdbAttribution), findsOneWidget);
  });

  testWidgets('tapping an accent preset updates and saves the setting', (
    WidgetTester tester,
  ) async {
    final settings = AppSettings();
    await tester.pumpWidget(wrap(settings));

    await tester.tap(find.byTooltip('Teal'));
    await tester.pumpAndSettle();

    expect(settings.accent.toARGB32(), 0xFF0F9D8F);
    final reloaded = await AppSettings.load();
    expect(reloaded.accent.toARGB32(), 0xFF0F9D8F);
  });

  testWidgets('switching a provider off persists it', (
    WidgetTester tester,
  ) async {
    final settings = AppSettings(
      enabledProviderIds: <String>['cinesrc', 'rivestream'],
      defaultProviderId: 'cinesrc',
    );
    await tester.pumpWidget(wrap(settings));
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
    await tester.pumpWidget(wrap(settings));
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
    await tester.pumpWidget(wrap(settings));
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
    await tester.pumpWidget(wrap(AppSettings()));
    await tester.tap(find.text('Providers'));
    await tester.pumpAndSettle();

    expect(find.text('TMDB API key'), findsOneWidget);
    expect(find.text('Test key'), findsOneWidget);
    expect(find.textContaining('themoviedb.org/settings/api'), findsOneWidget);
  });
}
