import 'package:material_ui/material_ui.dart';
import 'package:provider/provider.dart';

import '../../data/media_repository.dart';
import '../../models/media_item.dart';
import '../../nav.dart';
import '../../theme/lumora_theme.dart';
import 'future_guard.dart';
import 'widgets/state_views.dart';

/// How a detail page starts playback. Defaults to [openPlayer]; tests pass a
/// recorder so the assertion does not depend on the player page.
typedef PlayHandler = void Function(
  BuildContext context,
  MediaItem item, {
  int season,
  int episode,
});

/// One title: backdrop, year/rating/runtime, genres, overview, Watch, and the
/// season/episode picker for series — the PC detail page minus ratings,
/// trailers, favourites and collections.
class MediaDetailPage extends StatefulWidget {
  const MediaDetailPage({super.key, required this.item, this.onPlay});

  final MediaItem item;

  /// Overrides nav.openPlayer; optional so the contract signature is intact.
  final PlayHandler? onPlay;

  @override
  State<MediaDetailPage> createState() => _MediaDetailPageState();
}

class _MediaDetailPageState extends State<MediaDetailPage> {
  late Future<MediaDetail> _future;
  int _season = 1;

  @override
  void initState() {
    super.initState();
    _future = _fetch();
  }

  Future<MediaDetail> _fetch() =>
      guarded(context.read<MediaRepository>().detail(widget.item));

  void _retry() {
    setState(() {
      _future = _fetch();
    });
  }

  void _play({int episode = 1}) {
    final handler = widget.onPlay;
    if (handler != null) {
      handler(context, widget.item, season: _season, episode: episode);
    } else {
      openPlayer(context, widget.item, season: _season, episode: episode);
    }
  }

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<MediaDetail>(
      future: _future,
      builder: (context, snapshot) {
        if (snapshot.connectionState == ConnectionState.waiting) {
          return Scaffold(
            appBar: AppBar(title: Text(widget.item.title)),
            body: const MediaDetailSkeleton(),
          );
        }
        if (snapshot.hasError) {
          final missing = snapshot.error is MissingApiKeyException;
          return Scaffold(
            appBar: AppBar(title: Text(widget.item.title)),
            body: ListView(
              padding: const EdgeInsets.all(LumoraTheme.gapLg),
              children: [
                if (missing)
                  const MissingApiKeyCard()
                else
                  MediaErrorCard(
                    message: 'Could not load this title.',
                    detail: '${snapshot.error}',
                    onRetry: _retry,
                  ),
              ],
            ),
          );
        }
        return Scaffold(body: _content(context, snapshot.data!));
      },
    );
  }

  Widget _content(BuildContext context, MediaDetail detail) {
    final theme = Theme.of(context);
    final item = detail.item;
    final isSeries = item.kind != MediaKind.movie;
    final seasons = detail.seasons;
    final season = seasons.isEmpty
        ? null
        : seasons.firstWhere(
            (s) => s.number == _season,
            orElse: () => seasons.first,
          );

    return CustomScrollView(
      slivers: [
        SliverAppBar(
          pinned: true,
          expandedHeight: 220,
          backgroundColor: theme.colorScheme.surface,
          flexibleSpace: FlexibleSpaceBar(
            collapseMode: CollapseMode.parallax,
            titlePadding: const EdgeInsetsDirectional.only(
              start: 56,
              end: LumoraTheme.gapLg,
              bottom: LumoraTheme.gapMd,
            ),
            title: Text(
              item.title,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
            background: _Backdrop(url: item.backdropUrl),
          ),
        ),
        SliverToBoxAdapter(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(
              LumoraTheme.gapLg,
              LumoraTheme.gapLg,
              LumoraTheme.gapLg,
              LumoraTheme.gapXl,
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                _MetaLine(item: item, runtimeMinutes: detail.runtimeMinutes),
                const SizedBox(height: LumoraTheme.gapMd),
                if (item.genres.isNotEmpty)
                  Wrap(
                    spacing: LumoraTheme.gapSm,
                    runSpacing: LumoraTheme.gapSm,
                    children: [
                      for (final genre in item.genres) _GenreChip(label: genre),
                    ],
                  ),
                if (detail.tagline?.trim().isNotEmpty ?? false) ...[
                  const SizedBox(height: LumoraTheme.gapMd),
                  Text(
                    detail.tagline!.trim(),
                    style: theme.textTheme.bodyMedium?.copyWith(
                      fontStyle: FontStyle.italic,
                      color: theme.colorScheme.onSurfaceVariant,
                    ),
                  ),
                ],
                if (item.overview?.trim().isNotEmpty ?? false) ...[
                  const SizedBox(height: LumoraTheme.gapMd),
                  Text(
                    item.overview!.trim(),
                    style: theme.textTheme.bodyMedium?.copyWith(height: 1.45),
                  ),
                ],
                const SizedBox(height: LumoraTheme.gapXl),
                SizedBox(
                  width: double.infinity,
                  height: 48,
                  child: FilledButton.icon(
                    onPressed: () => _play(),
                    icon: const Icon(Icons.play_arrow_rounded, size: 26),
                    label: Text(isSeries ? 'Watch Season $_season' : 'Watch'),
                  ),
                ),
                if (isSeries && seasons.isNotEmpty) ...[
                  const SizedBox(height: LumoraTheme.gapXl),
                  Text(
                    'Episodes',
                    style: theme.textTheme.titleMedium
                        ?.copyWith(fontWeight: FontWeight.w700),
                  ),
                  const SizedBox(height: LumoraTheme.gapSm),
                  DropdownButtonHideUnderline(
                    child: DropdownButton<int>(
                      isExpanded: true,
                      value: season!.number,
                      items: [
                        for (final s in seasons)
                          DropdownMenuItem<int>(
                            value: s.number,
                            child: Text(_seasonLabel(s)),
                          ),
                      ],
                      onChanged: (value) {
                        if (value == null) return;
                        setState(() => _season = value);
                      },
                    ),
                  ),
                  const SizedBox(height: LumoraTheme.gapSm),
                  if (season.episodeCount <= 0)
                    Text(
                      'No episodes listed for this season.',
                      style: theme.textTheme.bodySmall?.copyWith(
                        color: theme.colorScheme.onSurfaceVariant,
                      ),
                    )
                  else
                    for (var number = 1;
                        number <= season.episodeCount;
                        number++)
                      _EpisodeTile(
                        number: number,
                        onTap: () => _play(episode: number),
                      ),
                ],
              ],
            ),
          ),
        ),
      ],
    );
  }

  static String _seasonLabel(SeasonInfo season) {
    final name = season.name?.trim() ?? '';
    return name.isEmpty ? 'Season ${season.number}' : 'Season ${season.number} · $name';
  }
}

class _Backdrop extends StatelessWidget {
  const _Backdrop({required this.url});

  final String? url;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final fallback = ColoredBox(
      color: theme.colorScheme.surfaceContainerHighest,
      child: const SizedBox.expand(),
    );
    final image = url?.trim() ?? '';
    return Stack(
      fit: StackFit.expand,
      children: [
        if (image.isEmpty)
          fallback
        else
          Image.network(
            image,
            fit: BoxFit.cover,
            errorBuilder: (_, _, _) => fallback,
            loadingBuilder: (context, child, progress) =>
                progress == null ? child : fallback,
          ),
        const DecoratedBox(
          decoration: BoxDecoration(
            gradient: LinearGradient(
              begin: Alignment.topCenter,
              end: Alignment.bottomCenter,
              colors: [Color(0x22000000), Color(0xCC0F1014)],
            ),
          ),
        ),
      ],
    );
  }
}

class _MetaLine extends StatelessWidget {
  const _MetaLine({required this.item, this.runtimeMinutes});

  final MediaItem item;
  final int? runtimeMinutes;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final style = theme.textTheme.bodyMedium
        ?.copyWith(color: theme.colorScheme.onSurfaceVariant);
    final pieces = <Widget>[];

    void add(String label) {
      if (pieces.isNotEmpty) {
        pieces.add(Padding(
          padding: const EdgeInsets.symmetric(horizontal: 6),
          child: Text('·', style: style),
        ));
      }
      pieces.add(Text(label, style: style));
    }

    final year = item.year?.trim() ?? '';
    if (year.isNotEmpty) add(year);
    final runtime = runtimeMinutes;
    if (runtime != null && runtime > 0) {
      final hours = runtime ~/ 60;
      final minutes = runtime % 60;
      add(hours > 0 ? '${hours}h ${minutes}m' : '${minutes}m');
    }
    final rating = item.rating;
    if (rating != null) {
      pieces.add(Padding(
        padding: EdgeInsets.only(left: pieces.isEmpty ? 0 : 6),
        child: const Icon(
          Icons.star_rounded,
          size: 16,
          color: LumoraTheme.favorite,
        ),
      ));
      pieces.add(Text(rating.toStringAsFixed(1), style: style));
    }

    return Row(children: pieces);
  }
}

class _GenreChip extends StatelessWidget {
  const _GenreChip({required this.label});

  final String label;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
      decoration: BoxDecoration(
        color: theme.colorScheme.secondaryContainer,
        borderRadius: BorderRadius.circular(999),
      ),
      child: Text(
        label,
        style: theme.textTheme.labelMedium?.copyWith(
          color: theme.colorScheme.onSecondaryContainer,
        ),
      ),
    );
  }
}

class _EpisodeTile extends StatelessWidget {
  const _EpisodeTile({required this.number, required this.onTap});

  final int number;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: Material(
        color: theme.colorScheme.surfaceContainerHighest
            .withValues(alpha: 0.45),
        borderRadius: BorderRadius.circular(LumoraTheme.radiusMd),
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(LumoraTheme.radiusMd),
          child: Container(
            constraints: const BoxConstraints(minHeight: 48),
            padding: const EdgeInsets.symmetric(horizontal: LumoraTheme.gapMd),
            child: Row(
              children: [
                SizedBox(
                  width: 28,
                  child: Text(
                    '$number',
                    style: theme.textTheme.bodyMedium?.copyWith(
                      color: theme.colorScheme.onSurfaceVariant,
                    ),
                  ),
                ),
                Expanded(
                  child: Text(
                    'Episode $number',
                    style: theme.textTheme.bodyMedium,
                  ),
                ),
                const Icon(Icons.play_arrow_rounded, size: 20),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
