import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../data/media_repository.dart';
import '../../data/settings_store.dart';
import '../../models/media_item.dart';
import '../../nav.dart';
import '../../theme/lumora_theme.dart';
import '../calendar/calendar_page.dart';
import 'future_guard.dart';
import 'media_detail_page.dart';
import 'media_search_page.dart';
import 'widgets/media_scale.dart';
import 'widgets/shelf_row.dart';
import 'widgets/state_views.dart';

/// Movies & TV home: the PC page's shelves, without its hero, calendar, list
/// or history sections.
class MediaHomePage extends StatefulWidget {
  const MediaHomePage({super.key});

  @override
  State<MediaHomePage> createState() => _MediaHomePageState();
}

class _MediaHomePageState extends State<MediaHomePage> {
  late Future<List<MediaShelf>> _future;

  @override
  void initState() {
    super.initState();
    _future = _fetch();
  }

  Future<List<MediaShelf>> _fetch() =>
      guarded(context.read<MediaRepository>().home());

  Future<void> _refresh() async {
    final future = _fetch();
    setState(() {
      _future = future;
    });
    try {
      await future;
    } catch (_) {
      // The FutureBuilder below renders the error.
    }
  }

  void _open(MediaItem item) {
    Navigator.of(context).push(
      MaterialPageRoute<void>(builder: (_) => MediaDetailPage(item: item)),
    );
  }

  @override
  Widget build(BuildContext context) {
    final compact = context.watch<AppSettings>().compactCards;
    final scale = mediaUiScale(context);
    return Scaffold(
      appBar: AppBar(
        title: const Text('Movies & TV'),
        actions: [
          IconButton(
            tooltip: 'Episode calendar',
            icon: const Icon(Icons.calendar_month_outlined),
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute<void>(builder: (_) => const CalendarPage()),
            ),
          ),
          IconButton(
            tooltip: 'Search',
            icon: const Icon(Icons.search),
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute<void>(builder: (_) => const MediaSearchPage()),
            ),
          ),
          IconButton(
            tooltip: 'Settings',
            icon: const Icon(Icons.settings_outlined),
            onPressed: () => openSettings(context),
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: _refresh,
        child: FutureBuilder<List<MediaShelf>>(
          future: _future,
          builder: (context, snapshot) {
            if (snapshot.connectionState == ConnectionState.waiting) {
              return const MediaHomeSkeleton();
            }
            if (snapshot.hasError) {
              final error = snapshot.error;
              return ListView(
                physics: const AlwaysScrollableScrollPhysics(),
                padding: const EdgeInsets.all(LumoraSpace.lg),
                children: [
                  if (error is MissingApiKeyException)
                    const MissingApiKeyCard()
                  else
                    MediaErrorCard(
                      message: 'Could not load Movies & TV.',
                      detail: '$error',
                      onRetry: _refresh,
                    ),
                ],
              );
            }
            final shelves = (snapshot.data ?? const <MediaShelf>[])
                .where((shelf) => shelf.items.isNotEmpty)
                .toList();
            if (shelves.isEmpty) {
              return ListView(
                physics: const AlwaysScrollableScrollPhysics(),
                children: const [
                  SizedBox(height: 80),
                  MediaEmptyView(
                    icon: Icons.movie_outlined,
                    message: 'Nothing to show yet',
                    hint: 'Pull down to refresh, or check your TMDB key in '
                        'Settings → Providers.',
                  ),
                ],
              );
            }
            return ListView.separated(
              physics: const AlwaysScrollableScrollPhysics(),
              padding: const EdgeInsets.symmetric(
                vertical: LumoraSpace.lg,
              ),
              itemCount: shelves.length,
              separatorBuilder: (_, __) =>
                  const SizedBox(height: LumoraSpace.xl),
              itemBuilder: (context, index) => ShelfRow(
                shelf: shelves[index],
                compact: compact,
                scale: scale,
                onItemTap: _open,
              ),
            );
          },
        ),
      ),
    );
  }
}
