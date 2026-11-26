import 'package:flutter_test/flutter_test.dart';

import 'package:lumora_mobile/data/media_repository.dart';
import 'package:lumora_mobile/ui/media/media_detail_page.dart';

import 'media_test_support.dart';

void main() {
  testWidgets('a TV detail lists the episodes of the first season',
      (tester) async {
    await tester.pumpWidget(
      mediaApp(const MediaDetailPage(item: tvItem)),
    );
    await tester.pumpAndSettle();

    expect(find.text('Severance'), findsOneWidget);
    expect(find.text('Episodes'), findsOneWidget);
    expect(find.text('Episode 1'), findsOneWidget);
    expect(find.text('Episode 2'), findsOneWidget);
    expect(find.text('Episode 3'), findsOneWidget);
    // Season one has three episodes in the fake; the second season is only in
    // the dropdown.
    expect(find.text('Episode 4'), findsNothing);
    expect(find.text('Season 1'), findsOneWidget);
  });

  testWidgets('tapping an episode plays that season and episode',
      (tester) async {
    final played = <(int, int)>[];
    await tester.pumpWidget(
      mediaApp(
        MediaDetailPage(
          item: tvItem,
          onPlay: (context, item, {season = 1, episode = 1}) {
            played.add((season, episode));
          },
        ),
      ),
    );
    await tester.pumpAndSettle();

    await tester.ensureVisible(find.text('Episode 3'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Episode 3'));
    await tester.pump();

    expect(played, [(1, 3)]);
  });

  testWidgets('the Watch button plays the expected episode', (tester) async {
    final played = <(int, int)>[];
    await tester.pumpWidget(
      mediaApp(
        MediaDetailPage(
          item: tvItem,
          onPlay: (context, item, {season = 1, episode = 1}) {
            played.add((season, episode));
          },
        ),
      ),
    );
    await tester.pumpAndSettle();

    await tester.ensureVisible(find.text('Watch Season 1'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Watch Season 1'));
    await tester.pump();

    expect(played, [(1, 1)]);
  });

  testWidgets('changing the season swaps the episode list', (tester) async {
    final played = <(int, int)>[];
    await tester.pumpWidget(
      mediaApp(
        MediaDetailPage(
          item: tvItem,
          onPlay: (context, item, {season = 1, episode = 1}) {
            played.add((season, episode));
          },
        ),
      ),
    );
    await tester.pumpAndSettle();

    await tester.ensureVisible(find.text('Season 1'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Season 1'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Season 2 · Season Two').last);
    await tester.pumpAndSettle();

    expect(find.text('Episode 2'), findsOneWidget);
    expect(find.text('Episode 3'), findsNothing);

    await tester.ensureVisible(find.text('Episode 2'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Episode 2'));
    await tester.pump();
    expect(played, [(2, 2)]);
  });

  testWidgets('a movie shows runtime, genres and a single Watch button',
      (tester) async {
    final played = <(int, int)>[];
    await tester.pumpWidget(
      mediaApp(
        MediaDetailPage(
          item: movieItem,
          onPlay: (context, item, {season = 1, episode = 1}) {
            played.add((season, episode));
          },
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('2h 46m'), findsOneWidget);
    expect(find.text('Science Fiction'), findsOneWidget);
    expect(find.text('Adventure'), findsOneWidget);
    expect(find.text('Episodes'), findsNothing);

    await tester.ensureVisible(find.text('Watch'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Watch'));
    await tester.pump();
    expect(played, [(1, 1)]);
  });

  testWidgets('a missing key shows the setup card instead of the title',
      (tester) async {
    await tester.pumpWidget(
      mediaApp(
        const MediaDetailPage(item: movieItem),
        repository: TestRepository(
          failWith: const MissingApiKeyException('No key'),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('Add your TMDB key in Settings'), findsOneWidget);
    expect(find.text('Watch'), findsNothing);
  });

  testWidgets('a failed detail load can be retried', (tester) async {
    final repository = TestRepository(failWith: Exception('offline'));
    await tester.pumpWidget(
      mediaApp(const MediaDetailPage(item: movieItem), repository: repository),
    );
    await tester.pumpAndSettle();

    expect(find.text('Could not load this title.'), findsOneWidget);
    await tester.ensureVisible(find.text('Retry'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Retry'));
    await tester.pumpAndSettle();
    expect(repository.detailCalls, 2);
  });

  testWidgets('an anime detail uses the same episode picker', (tester) async {
    await tester.pumpWidget(
      mediaApp(const MediaDetailPage(item: animeItem)),
    );
    await tester.pumpAndSettle();

    expect(find.text('Episodes'), findsOneWidget);
    expect(find.text('Episode 1'), findsOneWidget);
  });
}
