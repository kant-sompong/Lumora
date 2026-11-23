import 'dart:async';

import 'package:material_ui/material_ui.dart';
import 'package:provider/provider.dart';

import '../../data/media_repository.dart';
import '../../data/settings_store.dart';
import '../../models/media_item.dart';
import '../../theme/lumora_theme.dart';
import 'future_guard.dart';
import 'media_detail_page.dart';
import 'widgets/poster_card.dart';
import 'widgets/state_views.dart';

/// Search across TMDB and AniList, three posters across.
///
/// The PC page searches as you type; on a phone the same thing happens after a
/// 400 ms pause so a slow keyboard does not fire a request per letter.
class MediaSearchPage extends StatefulWidget {
  const MediaSearchPage({super.key});

  static const Duration debounce = Duration(milliseconds: 400);

  @override
  State<MediaSearchPage> createState() => _MediaSearchPageState();
}

class _MediaSearchPageState extends State<MediaSearchPage> {
  final TextEditingController _controller = TextEditingController();
  Timer? _debounce;
  Future<List<MediaItem>>? _future;
  String _query = '';
  MediaKind? _kind;

  @override
  void dispose() {
    _debounce?.cancel();
    _controller.dispose();
    super.dispose();
  }

  void _onChanged(String text) {
    _debounce?.cancel();
    final query = text.trim();
    if (query.isEmpty) {
      setState(() {
        _query = '';
        _future = null;
      });
      return;
    }
    _debounce = Timer(MediaSearchPage.debounce, () => _search(query));
  }

  void _search(String query) {
    if (!mounted) return;
    setState(() {
      _query = query;
      _future = guarded(context.read<MediaRepository>().search(query));
    });
  }

  void _open(MediaItem item) {
    Navigator.of(context).push(
      MaterialPageRoute<void>(builder: (_) => MediaDetailPage(item: item)),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        titleSpacing: 0,
        // A bare TextField in an AppBar title is laid out flush with the top
        // edge on a phone; centring it in a 48 dp box keeps the field (and its
        // tap target) where the eye expects it.
        title: Padding(
          padding: const EdgeInsets.only(right: LumoraTheme.gapLg),
          child: SizedBox(
            height: 48,
            child: Center(
              child: TextField(
                controller: _controller,
                autofocus: true,
                textInputAction: TextInputAction.search,
                onChanged: _onChanged,
                style: Theme.of(context).textTheme.bodyLarge,
                decoration: const InputDecoration(
                  hintText: 'Search movies, TV and anime',
                  border: InputBorder.none,
                  isDense: true,
                  contentPadding: EdgeInsets.zero,
                ),
              ),
            ),
          ),
        ),
      ),
      body: Column(
        children: [
          _KindFilters(
            selected: _kind,
            onChanged: (kind) => setState(() => _kind = kind),
          ),
          Expanded(child: _results()),
        ],
      ),
    );
  }

  Widget _results() {
    final future = _future;
    if (future == null) {
      return const MediaEmptyView(
        message: 'Search movies, TV and anime',
        hint: 'Results come straight from TMDB and AniList — nothing is '
            'suggested or invented.',
      );
    }
    final compact = context.watch<AppSettings>().compactCards;
    return FutureBuilder<List<MediaItem>>(
      future: future,
      builder: (context, snapshot) {
        if (snapshot.connectionState == ConnectionState.waiting) {
          return const Padding(
            padding: EdgeInsets.all(LumoraTheme.gapXl),
            child: MediaHomeSkeleton(shelfCount: 1),
          );
        }
        if (snapshot.hasError) {
          final error = snapshot.error;
          return ListView(
            padding: const EdgeInsets.all(LumoraTheme.gapLg),
            children: [
              if (error is MissingApiKeyException)
                const MissingApiKeyCard()
              else
                MediaErrorCard(
                  message: 'Search failed.',
                  detail: '$error',
                  onRetry: () => _search(_query),
                ),
            ],
          );
        }
        final all = snapshot.data ?? const <MediaItem>[];
        final results = _kind == null
            ? all
            : all.where((item) => item.kind == _kind).toList();
        if (results.isEmpty) {
          return MediaEmptyView(
            icon: Icons.search_off_rounded,
            message: 'No matches for “$_query”',
            hint: _kind == null
                ? 'Try a shorter title.'
                : 'Try another filter, or All.',
          );
        }
        return GridView.builder(
          padding: const EdgeInsets.all(LumoraTheme.gapLg),
          gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
            crossAxisCount: 3,
            mainAxisSpacing: LumoraTheme.gapLg,
            crossAxisSpacing: LumoraTheme.gapMd,
            childAspectRatio: 0.46,
          ),
          itemCount: results.length,
          itemBuilder: (context, index) {
            final item = results[index];
            return PosterCard(
              item: item,
              compact: compact,
              onTap: () => _open(item),
            );
          },
        );
      },
    );
  }
}

/// All / Movies / TV / Anime, the same split as the PC page's section links.
class _KindFilters extends StatelessWidget {
  const _KindFilters({required this.selected, required this.onChanged});

  final MediaKind? selected;
  final ValueChanged<MediaKind?> onChanged;

  @override
  Widget build(BuildContext context) {
    final options = <(String, MediaKind?)>[
      ('All', null),
      ('Movies', MediaKind.movie),
      ('TV', MediaKind.tv),
      ('Anime', MediaKind.anime),
    ];
    return SizedBox(
      height: 48,
      child: ListView.separated(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: LumoraTheme.gapLg),
        itemCount: options.length,
        separatorBuilder: (_, _) => const SizedBox(width: LumoraTheme.gapSm),
        itemBuilder: (context, index) {
          final (label, kind) = options[index];
          return Center(
            child: ChoiceChip(
              label: Text(label),
              selected: selected == kind,
              onSelected: (_) => onChanged(kind),
            ),
          );
        },
      ),
    );
  }
}
