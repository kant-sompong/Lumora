import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lumora_mobile/data/settings_store.dart';
import 'package:lumora_mobile/theme/lumora_theme.dart';
import 'package:lumora_mobile/ui/settings/settings_page.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// The settings page at a 390x844 logical-pixel phone viewport.
///
/// Regenerate the PNGs with `flutter test --update-goldens`.
void main() {
  setUp(() {
    SharedPreferences.setMockInitialValues(<String, Object>{});
  });

  Future<void> pumpSettings(WidgetTester tester) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final settings = AppSettings(
      enabledProviderIds: <String>['cinesrc', 'rivestream'],
      defaultProviderId: 'cinesrc',
      accent: const Color(0xFF3B82F6),
    );
    await tester.pumpWidget(
      ChangeNotifierProvider<AppSettings>.value(
        value: settings,
        child: MaterialApp(
          debugShowCheckedModeBanner: false,
          theme: LumoraTheme.dark(settings.accent),
          home: const SettingsPage(),
        ),
      ),
    );
    await tester.pumpAndSettle();
  }

  testWidgets('General tab at 390x844', (WidgetTester tester) async {
    await pumpSettings(tester);

    await expectLater(
      find.byType(SettingsPage),
      matchesGoldenFile('settings_page_general.png'),
    );
  });

  testWidgets('Providers tab at 390x844', (WidgetTester tester) async {
    await pumpSettings(tester);
    await tester.tap(find.text('Providers'));
    await tester.pumpAndSettle();

    await expectLater(
      find.byType(SettingsPage),
      matchesGoldenFile('settings_page_providers.png'),
    );
  });

  testWidgets('Providers tab after a rejected TMDB key at 390x844', (
    WidgetTester tester,
  ) async {
    await pumpSettings(tester);
    await tester.tap(find.text('Providers'));
    await tester.pumpAndSettle();

    await tester.enterText(find.byType(TextField), 'not-a-real-key');
    await tester.tap(find.text('Test key'));
    await tester.pumpAndSettle();

    // The rejected-key status line is what the golden is meant to lock down.
    expect(find.text('Test key'), findsOneWidget);

    await expectLater(
      find.byType(SettingsPage),
      matchesGoldenFile('settings_page_providers_invalid_key.png'),
    );
  });
}
