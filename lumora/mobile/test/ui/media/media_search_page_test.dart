import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:lumora_mobile/data/media_repository.dart';
import 'package:lumora_mobile/models/media_item.dart';
import 'package:lumora_mobile/ui/media/media_search_page.dart';
import 'package:lumora_mobile/ui/media/widgets/poster_card.dart';

import 'media_test_support.dart';

void main() {
  testWidgets('search waits 400 ms before asking the repository', (tester) async {
    final repository = TestRepository();
    await tester.pumpWidget(
      mediaApp(const MediaSearchPage(), repository: repository),
    );

    await tester.enterText(find.byType(TextField), 'dune');
    await tester.pump(const Duration(milliseconds: 399));
    expect(repository.queries, isEmpty, reason: 'debounce not elapsed');

    await tester.pump(const Duration(milliseconds: 2));
    await tester.pumpAndSettle();
    expect(repository.queries, ['dune']);
    expect(find.text('Dune: Part Two'), findsOneWidget);
  });

  testWidgets('typing again restarts the debounce and keeps the last query',
      (tester) async {
    final repository = TestRepository();
    await tester.pumpWidget(
      mediaApp(const MediaSearchPage(), repository: repository),
    );

    await tester.enterText(find.byType(TextField), 'du');
    await tester.pump(const Duration(milliseconds: 200));
    await tester.enterText(find.byType(TextField), 'severance');
    await tester.pump(const Duration(milliseconds: 200));
    expect(repository.queries, isEmpty);

    await tester.pump(const Duration(milliseconds: 250));
    await tester.pumpAndSettle();
    expect(repository.queries, ['severance']);
  });

  testWidgets('an empty field makes no request', (tester) async {
    final repository = TestRepository();
    await tester.pumpWidget(
      mediaApp(const MediaSearchPage(), repository: repository),
    );

    await tester.enterText(find.byType(TextField), '   ');
    await tester.pump(const Duration(milliseconds: 600));
    expect(repository.queries, isEmpty);
    expect(find.text('Search movies, TV and anime'), findsWidgets);
  });

  testWidgets('results are a three-column grid', (tester) async {
    await tester.pumpWidget(mediaApp(const MediaSearchPage()));
    await tester.enterText(find.byType(TextField), 'e');
    await tester.pump(const Duration(milliseconds: 400));
    await tester.pumpAndSettle();

    final grid = tester.widget<GridView>(find.byType(GridView));
    final delegate =
        grid.gridDelegate as SliverGridDelegateWithFixedCrossAxisCount;
    expect(delegate.crossAxisCount, 3);
    expect(find.byType(PosterCard), findsNWidgets(3));
  });

  testWidgets('kind chips filter the results', (tester) async {
    await tester.pumpWidget(mediaApp(const MediaSearchPage()));
    await tester.enterText(find.byType(TextField), 'e');
    await tester.pump(const Duration(milliseconds: 400));
    await tester.pumpAndSettle();
    expect(find.byType(PosterCard), findsNWidgets(3));

    await tester.tap(find.text('Anime'));
    await tester.pumpAndSettle();
    expect(find.byType(PosterCard), findsOneWidget);
    expect(find.text('Frieren'), findsOneWidget);
  });

  testWidgets('no results shows an empty state', (tester) async {
    await tester.pumpWidget(mediaApp(const MediaSearchPage()));
    await tester.enterText(find.byType(TextField), 'zzzz');
    await tester.pump(const Duration(milliseconds: 400));
    await tester.pumpAndSettle();

    expect(find.text('No matches for “zzzz”'), findsOneWidget);
    expect(find.byType(PosterCard), findsNothing);
  });

  testWidgets('a missing key shows the setup card', (tester) async {
    await tester.pumpWidget(
      mediaApp(
        const MediaSearchPage(),
        repository: TestRepository(
          failWith: const MissingApiKeyException('No key'),
        ),
      ),
    );
    await tester.enterText(find.byType(TextField), 'dune');
    await tester.pump(const Duration(milliseconds: 400));
    await tester.pumpAndSettle();

    expect(find.text('Add your TMDB key in Settings'), findsOneWidget);
  });

  testWidgets('tapping a result opens its detail page', (tester) async {
    await tester.pumpWidget(mediaApp(const MediaSearchPage()));
    await tester.enterText(find.byType(TextField), 'frieren');
    await tester.pump(const Duration(milliseconds: 400));
    await tester.pumpAndSettle();

    await tester.tap(find.text('Frieren'));
    await tester.pumpAndSettle();

    expect(find.text('Episodes'), findsOneWidget);
  });

  testWidgets('a search filter matches anime through the kind enum',
      (tester) async {
    const items = <MediaItem>[animeItem];
    final repository = TestRepository(results: items);
    expect(items.single.kind, MediaKind.anime);
    await tester.pumpWidget(
      mediaApp(const MediaSearchPage(), repository: repository),
    );
    await tester.enterText(find.byType(TextField), 'frieren');
    await tester.pump(const Duration(milliseconds: 400));
    await tester.pumpAndSettle();
    expect(find.text('Frieren'), findsOneWidget);
  });
}
