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
/// of the test font's boxes.
///
/// Both come from the running Flutter SDK's `material_fonts` artifacts, which
/// is where the app's fonts come from too; nothing extra is committed.
Future<void> loadGoldenFonts() async {
  TestWidgetsFlutterBinding.ensureInitialized();
  final dir = _materialFontsDir();
  if (dir == null) {
    throw StateError(
      'Could not find bin/cache/artifacts/material_fonts under the Flutter '
      'SDK; run `flutter precache --universal` and retry.',
    );
  }
  await _load(dir, 'Roboto', const [
    'Roboto-Regular.ttf',
    'Roboto-Medium.ttf',
    'Roboto-Bold.ttf',
  ]);
  await _load(dir, 'MaterialIcons', const ['MaterialIcons-Regular.otf']);

  goldenFontFallback.clear();
  // DejaVu carries the arrows Roboto and Noto Sans lack (the "Settings →
  // Providers" copy); it is only a per-glyph fallback, not the body font.
  final dejaVu = File('/usr/share/fonts/TTF/DejaVuSans.ttf');
  if (dejaVu.existsSync()) {
    final loader = FontLoader('DejaVuSans')
      ..addFont(Future.value(ByteData.sublistView(await dejaVu.readAsBytes())));
    await loader.load();
    goldenFontFallback.add('DejaVuSans');
  }
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
  final loader = FontLoader(family);
  var loaded = 0;
  for (final name in files) {
    final file = File('${dir.path}/$name');
    if (!file.existsSync()) continue;
    final bytes = await file.readAsBytes();
    loader.addFont(Future.value(ByteData.sublistView(bytes)));
    loaded++;
  }
  if (loaded == 0) {
    throw StateError('No font files for $family in ${dir.path}');
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
