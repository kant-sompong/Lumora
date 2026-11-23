import 'package:flutter_test/flutter_test.dart';
import 'package:material_ui/material_ui.dart';

import 'package:lumora_mobile/data/media_repository.dart';
import 'package:lumora_mobile/ui/media/media_detail_page.dart';
import 'package:lumora_mobile/ui/media/media_home_page.dart';
import 'package:lumora_mobile/ui/media/media_search_page.dart';

import 'golden_support.dart';
import 'media_test_support.dart';

/// Screenshot tests for the phone UI at 390 x 844, the size the goldens are
/// cut at. Regenerate with `flutter test --update-goldens`.
void main() {
  setUpAll(loadGoldenFonts);

  testWidgets('golden: home with shelves', (tester) async {
    useGoldenScreen(tester);
    await tester.pumpWidget(goldenApp(const MediaHomePage()));
    await tester.pumpAndSettle();

    await expectLater(
      find.byType(MediaHomePage),
      matchesGoldenFile('goldens/home_shelves.png'),
    );
  });

  testWidgets('golden: home with the missing API key card', (tester) async {
    useGoldenScreen(tester);
    await tester.pumpWidget(
      goldenApp(
        const MediaHomePage(),
        repository: TestRepository(
          failWith: const MissingApiKeyException(),
        ),
      ),
    );
    await tester.pumpAndSettle();

    await expectLater(
      find.byType(MediaHomePage),
      matchesGoldenFile('goldens/home_missing_api_key.png'),
    );
  });

  testWidgets('golden: search with results', (tester) async {
    useGoldenScreen(tester);
    await _openSearch(tester);

    await tester.enterText(find.byType(TextField), 'e');
    await tester.pump(MediaSearchPage.debounce);
    await tester.pumpAndSettle();

    await expectLater(
      find.byType(MediaSearchPage),
      matchesGoldenFile('goldens/search_results.png'),
    );
  });

  testWidgets('golden: search empty state', (tester) async {
    useGoldenScreen(tester);
    await _openSearch(tester);

    await tester.enterText(find.byType(TextField), 'zzzz');
    await tester.pump(MediaSearchPage.debounce);
    await tester.pumpAndSettle();

    await expectLater(
      find.byType(MediaSearchPage),
      matchesGoldenFile('goldens/search_empty.png'),
    );
  });

  testWidgets('golden: movie detail', (tester) async {
    useGoldenScreen(tester);
    await tester.pumpWidget(goldenApp(const MediaDetailPage(item: movieItem)));
    await tester.pumpAndSettle();

    await expectLater(
      find.byType(MediaDetailPage),
      matchesGoldenFile('goldens/detail_movie.png'),
    );
  });

  testWidgets('golden: TV detail with episodes', (tester) async {
    useGoldenScreen(tester);
    await tester.pumpWidget(goldenApp(const MediaDetailPage(item: tvItem)));
    await tester.pumpAndSettle();

    await expectLater(
      find.byType(MediaDetailPage),
      matchesGoldenFile('goldens/detail_tv_episodes.png'),
    );
  });
}

/// Pushes search from the home page, the way a thumb reaches it, so the golden
/// includes the back arrow and the real app bar.
Future<void> _openSearch(WidgetTester tester) async {
  await tester.pumpWidget(goldenApp(const MediaHomePage()));
  await tester.pumpAndSettle();
  await tester.tap(find.byIcon(Icons.search));
  await tester.pumpAndSettle();
}
