import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:lumora_mobile/data/settings_store.dart';
import 'package:lumora_mobile/models/media_item.dart';
import 'package:lumora_mobile/ui/media/media_detail_page.dart';
import 'package:lumora_mobile/ui/media/media_home_page.dart';
import 'package:lumora_mobile/ui/media/media_search_page.dart';
import 'package:lumora_mobile/ui/media/widgets/adult_badge.dart';
import 'package:lumora_mobile/ui/media/widgets/poster_card.dart';

import 'media_test_support.dart';
import 'network_image_support.dart';

Widget detailWithLogo(String url, {double uiScale = 1.0}) {
  return mediaApp(
    const MediaDetailPage(item: movieItem),
    settings: AppSettings(uiScale: uiScale),
    repository: TestRepository(
      detailOverride: MediaDetail(item: movieItem, logoUrl: url),
    ),
  );
}

void main() {
  HttpOverrides? previousHttp;
  setUpAll(() {
    previousHttp = installFakeImageHttp();
  });
  tearDownAll(() {
    restoreImageHttp(previousHttp);
  });

  group('18+ pill', () {
    testWidgets('an adult poster carries the pill', (tester) async {
      final handle = tester.ensureSemantics();

      await tester.pumpWidget(
        mediaApp(
          const Scaffold(
            body: Center(
              child: PosterCard(item: adultItem, width: PosterCard.normalWidth),
            ),
          ),
        ),
      );

      expect(find.byType(AdultBadge), findsOneWidget);
      expect(find.text('18+'), findsOneWidget);
      expect(
        tester.getSemantics(find.byType(AdultBadge)).label,
        contains(AdultBadge.semanticsLabel),
      );
      handle.dispose();
    });

    testWidgets('a regular poster has no pill', (tester) async {
      await tester.pumpWidget(
        mediaApp(
          const Scaffold(
            body: Center(
              child: PosterCard(item: movieItem, width: PosterCard.normalWidth),
            ),
          ),
        ),
      );

      expect(find.byType(AdultBadge), findsNothing);
      expect(find.text('18+'), findsNothing);
    });

    testWidgets('search results badge adult titles', (tester) async {
      final repository = TestRepository(
        results: const <MediaItem>[movieItem, adultItem],
      );
      await tester.pumpWidget(
        mediaApp(const MediaSearchPage(), repository: repository),
      );

      await tester.enterText(find.byType(TextField), 'e');
      await tester.pump(MediaSearchPage.debounce);
      await tester.pumpAndSettle();

      expect(find.byType(PosterCard), findsNWidgets(2));
      expect(find.byType(AdultBadge), findsOneWidget);
    });
  });

  group('detail logo', () {
    testWidgets('the loaded logo replaces the plain title', (tester) async {
      await tester.pumpWidget(
        detailWithLogo('https://images.test/logo-ok.png'),
      );

      // While it loads, the plain title stands in.
      expect(find.text(movieItem.title), findsOneWidget);

      await tester.runAsync(() async {
        await precacheImage(
          NetworkImage('https://images.test/logo-ok.png'),
          tester.element(find.byType(MediaDetailPage)),
        );
      });
      await tester.pumpAndSettle();

      expect(find.text(movieItem.title), findsNothing);
      final image = tester.widget<Image>(find.byType(Image));
      expect(
        (image.image as NetworkImage).url,
        'https://images.test/logo-ok.png',
      );
    });

    testWidgets('the plain title shows while the logo is loading',
        (tester) async {
      await tester.pumpWidget(
        detailWithLogo('https://images.test/logo-slow.png'),
      );
      await tester.pump(const Duration(milliseconds: 50));

      expect(find.text(movieItem.title), findsOneWidget);
      expect(find.byType(Image), findsOneWidget);

      // Let the delayed response finish so no timer outlives the test.
      await tester.pump(const Duration(milliseconds: 400));
      await tester.pumpAndSettle();
    });

    testWidgets('a broken logo falls back to the plain title', (tester) async {
      await tester.pumpWidget(
        detailWithLogo('https://images.test/logo-broken.png'),
      );
      await tester.pumpAndSettle();

      expect(find.text(movieItem.title), findsOneWidget);
      expect(find.byType(Image), findsOneWidget);
    });
  });

  group('uiScale', () {
    testWidgets('the scale reaches the poster card', (tester) async {
      await tester.pumpWidget(
        mediaApp(
          const MediaHomePage(),
          settings: AppSettings(uiScale: 1.3),
        ),
      );
      await tester.pumpAndSettle();

      final card = tester.widget<PosterCard>(find.byType(PosterCard).first);
      expect(card.scale, 1.3);
    });

    for (final scale in <double>[0.8, 1.0, 1.3]) {
      testWidgets('the shelf fits at scale $scale with larger system text',
          (tester) async {
        await tester.pumpWidget(
          mediaApp(
            MediaQuery(
              data: const MediaQueryData(
                textScaler: TextScaler.linear(1.6),
              ),
              child: const MediaHomePage(),
            ),
            settings: AppSettings(uiScale: scale),
          ),
        );
        await tester.pumpAndSettle();

        expect(tester.takeException(), isNull);
      });
    }
  });
}
