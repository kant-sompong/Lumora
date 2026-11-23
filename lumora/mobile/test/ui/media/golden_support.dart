import 'dart:io';

import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:material_ui/material_ui.dart';
import 'package:provider/provider.dart';

import 'package:lumora_mobile/data/media_repository.dart';
import 'package:lumora_mobile/data/settings_store.dart';
import 'package:lumora_mobile/theme/lumora_theme.dart';

import 'media_test_support.dart';

/// A phone screen: 390 x 844 logical pixels, the size the goldens are cut at.
const Size goldenScreen = Size(390, 844);

/// Families tried after Roboto, so glyphs Roboto lacks ("→", for one) still
/// render instead of showing as boxes.
final List<String> goldenFontFallback = <String>[];

/// Loads Roboto and the Material icon font so goldens show real text instead
/// of the test font's boxes, plus DejaVu Sans for the few glyphs Roboto lacks
/// ("→" in "Settings → Providers").
///
/// These PNGs are **local-only**: they are pixel-exact against the fonts and
/// rasteriser of the machine that generated them. Run them there with
/// `flutter test --tags golden`; regenerate with
/// `flutter test --update-goldens --tags golden`.
///
/// A missing font fails the goldens instead of falling back to the test font's
/// boxes, because a box-rendered "pass" would be a lie about the UI.
Future<void> loadGoldenFonts() async {
  TestWidgetsFlutterBinding.ensureInitialized();
  final dir = _materialFontsDir();
  if (dir == null) {
    throw StateError(_missingOnThisMachine(
      'the Flutter SDK fonts in bin/cache/artifacts/material_fonts',
    ));
  }
  await _load(dir, 'Roboto', const [
    'Roboto-Regular.ttf',
    'Roboto-Medium.ttf',
    'Roboto-Bold.ttf',
  ]);
  await _load(dir, 'MaterialIcons', const ['MaterialIcons-Regular.otf']);

  // DejaVu carries the arrows Roboto and Noto Sans lack; it is only a
  // per-glyph fallback, never the body font.
  const dejaVuPath = '/usr/share/fonts/TTF/DejaVuSans.ttf';
  final dejaVu = File(dejaVuPath);
  if (!dejaVu.existsSync()) {
    throw StateError(_missingOnThisMachine('DejaVu Sans', dejaVuPath));
  }
  final loader = FontLoader('DejaVuSans')
    ..addFont(Future.value(ByteData.sublistView(await dejaVu.readAsBytes())));
  await loader.load();
  goldenFontFallback
    ..clear()
    ..add('DejaVuSans');
}

String _missingOnThisMachine(String what, [String? path]) {
  final where = path == null ? '' : ' ($path)';
  return 'Golden screenshots are local-only and this machine is missing '
      '$what$where. They were cut on the machine that has the Flutter SDK '
      'fonts and DejaVu Sans; run the goldens there with '
      '`flutter test --tags golden`, or regenerate them here with '
      '`flutter test --update-goldens --tags golden` and commit the new PNGs.';
}

Directory? _materialFontsDir() {
  final envRoot = Platform.environment['FLUTTER_ROOT'];
  final candidates = <String>[
    if (envRoot != null && envRoot.isNotEmpty)
      '$envRoot/bin/cache/artifacts/material_fonts',
  ];
  // flutter test runs <root>/bin/cache/dart-sdk/bin/dart; walk up to find the
  // SDK root rather than guessing a fixed number of parents.
  var dir = File(Platform.resolvedExecutable).parent;
  for (var i = 0; i < 8; i++) {
    candidates.add('${dir.path}/cache/artifacts/material_fonts');
    candidates.add('${dir.path}/artifacts/material_fonts');
    dir = dir.parent;
  }
  for (final path in candidates) {
    final candidate = Directory(path);
    if (candidate.existsSync()) return candidate;
  }
  return null;
}

Future<void> _load(
  Directory dir,
  String family,
  List<String> files,
) async {
  final missing = <String>[
    for (final name in files)
      if (!File('${dir.path}/$name').existsSync()) name,
  ];
  if (missing.isNotEmpty) {
    throw StateError(_missingOnThisMachine(
      'the $family font files ${missing.join(', ')}',
      dir.path,
    ));
  }
  final loader = FontLoader(family);
  for (final name in files) {
    final bytes = await File('${dir.path}/$name').readAsBytes();
    loader.addFont(Future.value(ByteData.sublistView(bytes)));
  }
  await loader.load();
}

/// [mediaApp] with Roboto wired into the theme, which is what the phone build
/// gets from the platform. Keeps the palette and sizes from [LumoraTheme].
Widget goldenApp(
  Widget child, {
  MediaRepository? repository,
  AppSettings? settings,
}) {
  final theme = LumoraTheme.dark(testAccent);
  return MultiProvider(
    providers: [
      ChangeNotifierProvider<AppSettings>.value(
        value: settings ?? AppSettings(),
      ),
      Provider<MediaRepository>.value(value: repository ?? TestRepository()),
    ],
    child: MaterialApp(
      debugShowCheckedModeBanner: false,
      theme: theme.copyWith(
        textTheme: theme.textTheme.apply(
          fontFamily: 'Roboto',
          fontFamilyFallback: goldenFontFallback,
        ),
        primaryTextTheme: theme.primaryTextTheme.apply(
          fontFamily: 'Roboto',
          fontFamilyFallback: goldenFontFallback,
        ),
      ),
      home: child,
    ),
  );
}

/// Pins the surface to [goldenScreen] and clears it afterwards.
void useGoldenScreen(WidgetTester tester) {
  tester.view.physicalSize = goldenScreen;
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
}
