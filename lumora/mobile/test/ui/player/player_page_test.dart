import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lumora_mobile/ui/player/player_page.dart';
import 'package:lumora_mobile/ui/player/player_panel.dart';

PlaybackTarget target(String id, String name) => PlaybackTarget(
  providerId: id,
  providerName: name,
  url: Uri.parse('https://$id.example/embed/1'),
);

Widget player({
  List<PlaybackTarget>? targets,
  int season = 1,
  int episode = 3,
}) {
  final list = targets ?? <PlaybackTarget>[
    target('cinesrc', 'CineSrc'),
    target('rivestream', 'Rive'),
  ];
  return MaterialApp(
    home: PlayerPage(
      url: list.first.url,
      title: 'The Quiet Orbit',
      targets: list,
      season: season,
      episode: episode,
      // Tests have no platform view: stand in for the WebView.
      webViewForTest: const ColoredBox(color: Colors.black),
    ),
  );
}

/// The panel's current horizontal offset: 0 open, negative closed.
double panelLeft(WidgetTester tester) {
  final slide = tester.widget<AnimatedPositioned>(
    find.ancestor(
      of: find.byType(PlayerPanel),
      matching: find.byType(AnimatedPositioned),
    ),
  );
  return slide.left ?? 0;
}

void main() {
  testWidgets('starts with the panel off-screen and no top bar', (
    WidgetTester tester,
  ) async {
    await tester.pumpWidget(player());
    await tester.pump();

    expect(find.byType(AppBar), findsNothing);
    expect(find.byType(PlayerPanel), findsOneWidget);
    expect(panelLeft(tester), lessThan(0));
    expect(find.byKey(PlayerPage.handleKey), findsOneWidget);
  });

  testWidgets('the left handle and the edge strip both open the panel', (
    WidgetTester tester,
  ) async {
    await tester.pumpWidget(player());
    await tester.pump();

    await tester.tap(find.byKey(PlayerPage.handleKey));
    await tester.pumpAndSettle();
    expect(panelLeft(tester), 0);

    await tester.tap(find.byTooltip('Close menu'));
    await tester.pumpAndSettle();
    expect(panelLeft(tester), lessThan(0));

    // Below the handle, so this hits the strip itself.
    expect(find.byKey(PlayerPage.edgeStripKey), findsOneWidget);
    await tester.tapAt(const Offset(12, 540));
    await tester.pumpAndSettle();
    expect(panelLeft(tester), 0);
  });

  testWidgets('tapping outside the panel closes it', (
    WidgetTester tester,
  ) async {
    await tester.pumpWidget(player());
    await tester.pump();

    await tester.tap(find.byKey(PlayerPage.handleKey));
    await tester.pumpAndSettle();
    expect(panelLeft(tester), 0);

    // Far from the 280 dp panel and away from the left edge.
    await tester.tapAt(const Offset(700, 300));
    await tester.pumpAndSettle();
    expect(panelLeft(tester), lessThan(0));
  });

  testWidgets('system back closes the panel first, then the player', (
    WidgetTester tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        home: Builder(
          builder: (BuildContext context) => Scaffold(
            body: Center(
              child: TextButton(
                onPressed: () => Navigator.of(context).push(
                  MaterialPageRoute<void>(
                    builder: (_) => PlayerPage(
                      url: target('cinesrc', 'CineSrc').url,
                      title: 'The Quiet Orbit',
                      targets: <PlaybackTarget>[
                        target('cinesrc', 'CineSrc'),
                        target('rivestream', 'Rive'),
                      ],
                      webViewForTest: const ColoredBox(color: Colors.black),
                    ),
                  ),
                ),
                child: const Text('Play'),
              ),
            ),
          ),
        ),
      ),
    );
    await tester.tap(find.text('Play'));
    await tester.pumpAndSettle();
    expect(find.byType(PlayerPage), findsOneWidget);

    await tester.tap(find.byKey(PlayerPage.handleKey));
    await tester.pumpAndSettle();
    expect(panelLeft(tester), 0);

    // First back: only the panel closes.
    await tester.binding.handlePopRoute();
    await tester.pumpAndSettle();
    expect(panelLeft(tester), lessThan(0));
    expect(find.byType(PlayerPage), findsOneWidget);

    // Second back: the player leaves.
    await tester.binding.handlePopRoute();
    await tester.pumpAndSettle();
    expect(find.byType(PlayerPage), findsNothing);
  });

  testWidgets('locks landscape and immersive, restores portrait on exit', (
    WidgetTester tester,
  ) async {
    final calls = <MethodCall>[];
    tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
      SystemChannels.platform,
      (MethodCall call) async {
        calls.add(call);
        return null;
      },
    );
    addTearDown(
      () => tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
        SystemChannels.platform,
        null,
      ),
    );

    await tester.pumpWidget(player());
    await tester.pump();

    final orientations = calls
        .where((MethodCall c) => c.method == 'SystemChrome.setPreferredOrientations')
        .toList();
    expect(orientations, isNotEmpty);
    expect(
      orientations.first.arguments,
      <String>[
        'DeviceOrientation.landscapeLeft',
        'DeviceOrientation.landscapeRight',
      ],
    );
    final uiModes = calls
        .where((MethodCall c) => c.method == 'SystemChrome.setEnabledSystemUIMode')
        .toList();
    expect(uiModes.first.arguments, 'SystemUiMode.immersiveSticky');

    calls.clear();
    await tester.pumpWidget(const MaterialApp(home: SizedBox()));
    await tester.pump();

    final restore = calls
        .where((MethodCall c) => c.method == 'SystemChrome.setPreferredOrientations')
        .toList();
    expect(restore.single.arguments, <String>['DeviceOrientation.portraitUp']);
    expect(
      calls
          .where((MethodCall c) => c.method == 'SystemChrome.setEnabledSystemUIMode')
          .single
          .arguments,
      'SystemUiMode.edgeToEdge',
    );
  });

  testWidgets('Next provider moves the panel selection and closes it', (
    WidgetTester tester,
  ) async {
    await tester.pumpWidget(player());
    await tester.pump();
    await tester.tap(find.byKey(PlayerPage.handleKey));
    await tester.pumpAndSettle();

    expect(
      tester.widget<PlayerPanel>(find.byType(PlayerPanel)).selectedIndex,
      0,
    );
    await tester.tap(find.text('Next provider'));
    await tester.pumpAndSettle();

    expect(panelLeft(tester), lessThan(0));
    expect(
      tester.widget<PlayerPanel>(find.byType(PlayerPanel)).selectedIndex,
      1,
    );

    // The panel lists every provider, with the current one first.
    await tester.tap(find.byKey(PlayerPage.handleKey));
    await tester.pumpAndSettle();
    final panel = tester.widget<PlayerPanel>(find.byType(PlayerPanel));
    expect(panel.targets.map((PlaybackTarget t) => t.providerName), <String>[
      'CineSrc',
      'Rive',
    ]);
    expect(find.text('Season 1 · Episode 3'), findsOneWidget);
  });
}
