import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:lumora_mobile/data/episode_calendar.dart';
import 'package:lumora_mobile/ui/calendar/calendar_page.dart';
import 'package:lumora_mobile/ui/media/media_home_page.dart';

import '../media/media_test_support.dart';

EpisodeCalendarResult sample(String query) => EpisodeCalendarResult(
  query: query,
  groups: <CalendarSeries>[
    CalendarSeries(
      title: 'Alpha',
      source: 'TMDB',
      airings: <EpisodeAiring>[
        EpisodeAiring(
          series: 'Alpha',
          season: 2,
          episode: 4,
          airAt: DateTime.utc(2027, 1, 15),
          confirmed: true,
          dateOnly: true,
          source: 'TMDB',
        ),
        EpisodeAiring(
          series: 'Alpha',
          season: 2,
          episode: 5,
          airAt: DateTime.utc(2027, 1, 22),
          dateOnly: true,
          source: 'TMDB',
        ),
        EpisodeAiring(series: 'Alpha', season: 2, episode: 6, source: 'TMDB'),
      ],
    ),
  ],
  errors: const <String>['TV search needs a TMDB key (Settings → Providers).'],
);

Future<void> search(WidgetTester tester, String query) async {
  await tester.enterText(find.byType(TextField), query);
  await tester.testTextInput.receiveAction(TextInputAction.search);
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('searching lists the series, its episodes and their certainty',
      (tester) async {
    await tester.pumpWidget(
      mediaApp(CalendarPage(search: (query) async => sample(query))),
    );

    expect(find.textContaining('Search for a series'), findsOneWidget);
    await search(tester, 'alpha');

    expect(find.text('Alpha'), findsOneWidget);
    expect(find.text('TMDB'), findsOneWidget);
    expect(find.text('S2E4'), findsOneWidget);
    expect(find.text('S2E5'), findsOneWidget);
    expect(find.text('S2E6'), findsOneWidget);
    expect(find.text('Confirmed'), findsOneWidget);
    expect(find.text('Estimated'), findsOneWidget);
    expect(find.text('Air date unknown'), findsOneWidget);
    expect(find.textContaining('15 Jan 2027'), findsOneWidget);
    // A provider that failed still explains itself beside the results.
    expect(find.textContaining('TMDB key'), findsOneWidget);
  });

  testWidgets('an empty answer says nothing was found', (tester) async {
    await tester.pumpWidget(
      mediaApp(
        CalendarPage(
          search: (query) async => EpisodeCalendarResult(query: query),
        ),
      ),
    );

    await search(tester, 'nothing');
    expect(find.textContaining('No upcoming episodes found'), findsOneWidget);
  });

  testWidgets('a failed search shows the error instead of a blank page',
      (tester) async {
    await tester.pumpWidget(
      mediaApp(
        CalendarPage(search: (query) async => throw Exception('offline')),
      ),
    );

    await search(tester, 'alpha');
    expect(find.textContaining('Could not search'), findsOneWidget);
  });

  testWidgets('clearing the field returns to the hint', (tester) async {
    await tester.pumpWidget(
      mediaApp(CalendarPage(search: (query) async => sample(query))),
    );
    await search(tester, 'alpha');
    expect(find.text('Alpha'), findsOneWidget);

    await tester.tap(find.byTooltip('Clear'));
    await tester.pumpAndSettle();
    expect(find.text('Alpha'), findsNothing);
    expect(find.textContaining('Search for a series'), findsOneWidget);
  });

  testWidgets('the home page opens the calendar', (tester) async {
    await tester.pumpWidget(mediaApp(const MediaHomePage()));
    await tester.pumpAndSettle();

    await tester.tap(find.byTooltip('Episode calendar'));
    await tester.pumpAndSettle();

    expect(find.text('Episode calendar'), findsOneWidget);
    expect(find.byType(CalendarPage), findsOneWidget);
  });
}
