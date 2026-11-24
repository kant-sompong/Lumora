import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:lumora_mobile/models/media_item.dart';
import 'package:lumora_mobile/ui/media/widgets/poster_card.dart';

import 'media_test_support.dart';

void main() {
  testWidgets('poster without a URL shows the placeholder', (tester) async {
    await tester.pumpWidget(
      mediaApp(
        const Scaffold(
          body: Center(
            child: PosterCard(
              item: movieItem,
              width: PosterCard.normalWidth,
            ),
          ),
        ),
      ),
    );

    expect(find.byIcon(Icons.movie_outlined), findsOneWidget);
    expect(find.text('Dune: Part Two'), findsOneWidget);
    expect(find.text('2024'), findsOneWidget);
    expect(find.text('8.2'), findsOneWidget);
  });

  testWidgets('compact cards are narrower than normal cards', (tester) async {
    await tester.pumpWidget(
      mediaApp(
        const Scaffold(
          body: Row(
            children: [
              PosterCard(item: movieItem, width: PosterCard.normalWidth),
              PosterCard(
                item: movieItem,
                compact: true,
                width: PosterCard.compactWidth,
              ),
            ],
          ),
        ),
      ),
    );

    final widths = tester
        .widgetList<SizedBox>(find.byType(SizedBox))
        .map((box) => box.width)
        .whereType<double>()
        .toList();
    expect(widths, contains(PosterCard.normalWidth));
    expect(widths, contains(PosterCard.compactWidth));
    expect(PosterCard.compactWidth, lessThan(PosterCard.normalWidth));
  });

  testWidgets('tapping a poster calls onTap', (tester) async {
    var taps = 0;
    await tester.pumpWidget(
      mediaApp(
        Scaffold(
          body: Center(
            child: PosterCard(
              item: const MediaItem(
                id: 'x',
                title: 'Example',
                kind: MediaKind.movie,
              ),
              onTap: () => taps++,
              width: PosterCard.normalWidth,
            ),
          ),
        ),
      ),
    );

    // No year and no rating: the meta line stays empty rather than printing
    // separators on their own.
    expect(find.text('·'), findsNothing);
    await tester.tap(find.text('Example'));
    expect(taps, 1);
  });
}
