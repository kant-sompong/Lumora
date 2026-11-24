import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:lumora_mobile/data/media_repository.dart';
import 'package:lumora_mobile/data/settings_store.dart';
import 'package:lumora_mobile/models/media_item.dart';
import 'package:lumora_mobile/ui/media/media_home_page.dart';
import 'package:lumora_mobile/ui/media/media_search_page.dart';
import 'package:lumora_mobile/ui/media/widgets/poster_card.dart';

import 'media_test_support.dart';

void main() {
  testWidgets('home shows the shelves from the repository', (tester) async {
    await tester.pumpWidget(mediaApp(const MediaHomePage()));
    await tester.pumpAndSettle();

    expect(find.text('Movies & TV'), findsOneWidget);
    expect(find.text('Trending Now'), findsOneWidget);
    expect(find.text('TV Shows'), findsOneWidget);
    // The movie appears on the shelf; the TV show appears on both shelves.
    expect(find.text('Dune: Part Two'), findsOneWidget);
    expect(find.text('Severance'), findsNWidgets(2));
    expect(find.byType(PosterCard), findsNWidgets(3));
    expect(find.byIcon(Icons.search), findsOneWidget);
    expect(find.byIcon(Icons.settings_outlined), findsOneWidget);
  });

  testWidgets('missing TMDB key shows the setup card', (tester) async {
    await tester.pumpWidget(
      mediaApp(
        const MediaHomePage(),
        repository: TestRepository(
          failWith: const MissingApiKeyException('No key'),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('Add your TMDB key in Settings'), findsOneWidget);
    expect(find.text('Open Settings'), findsOneWidget);
    expect(find.text('Retry'), findsNothing);
  });

  testWidgets('a setup card button opens Settings', (tester) async {
    await tester.pumpWidget(
      mediaApp(
        const MediaHomePage(),
        repository: TestRepository(
          failWith: const MissingApiKeyException('No key'),
        ),
      ),
    );
    await tester.pumpAndSettle();

    await tester.tap(find.text('Open Settings'));
    await tester.pumpAndSettle();

    expect(find.text('Providers'), findsWidgets);
  });

  testWidgets('a failed shelf load offers Retry', (tester) async {
    final repository = TestRepository(failWith: Exception('offline'));
    await tester.pumpWidget(
      mediaApp(const MediaHomePage(), repository: repository),
    );
    await tester.pumpAndSettle();

    expect(find.text('Retry'), findsOneWidget);
    await tester.ensureVisible(find.text('Retry'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Retry'));
    await tester.pumpAndSettle();
    expect(repository.homeCalls, 2);
  });

  testWidgets('home is empty when every shelf is empty', (tester) async {
    await tester.pumpWidget(
      mediaApp(
        const MediaHomePage(),
        repository: TestRepository(
          shelves: const <MediaShelf>[],
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('Nothing to show yet'), findsOneWidget);
  });

  testWidgets('tapping a poster opens the detail page', (tester) async {
    await tester.pumpWidget(mediaApp(const MediaHomePage()));
    await tester.pumpAndSettle();

    await tester.tap(find.text('Dune: Part Two'));
    await tester.pumpAndSettle();

    expect(find.text('Watch'), findsOneWidget);
    expect(find.text('Science Fiction'), findsOneWidget);
  });

  testWidgets('the search icon opens search', (tester) async {
    await tester.pumpWidget(mediaApp(const MediaHomePage()));
    await tester.pumpAndSettle();

    await tester.tap(find.byIcon(Icons.search));
    await tester.pumpAndSettle();

    expect(find.byType(MediaSearchPage), findsOneWidget);
  });

  testWidgets('compact cards come from the setting', (tester) async {
    final settings = AppSettings(compactCards: true);
    await tester.pumpWidget(
      mediaApp(const MediaHomePage(), settings: settings),
    );
    await tester.pumpAndSettle();

    final card = tester.widget<PosterCard>(find.byType(PosterCard).first);
    expect(card.compact, isTrue);
  });
}
