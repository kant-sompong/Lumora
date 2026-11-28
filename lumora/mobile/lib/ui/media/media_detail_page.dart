import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../data/media_repository.dart';
import '../../models/media_item.dart';
import '../../nav.dart';
import '../../theme/lumora_theme.dart';
import 'future_guard.dart';
import 'widgets/media_colors.dart';
import 'widgets/media_scale.dart';
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

  /// One episode request per season, memoised so rebuilding the page or
  /// scrolling never asks the provider twice.
  final Map<int, Future<List<EpisodeInfo>>> _episodeFutures =
      <int, Future<List<EpisodeInfo>>>{};

  @override
  void initState() {
    super.initState();
    _future = _fetch();
  }

  Future<MediaDetail> _fetch() =>
      guarded(context.read<MediaRepository>().detail(widget.item));

  void _retry() {
    setState(() {
      _episodeFutures.clear();
      _future = _fetch();
    });
  }

  /// The episodes of [season]; a failed request falls back to the episode
  /// count so playback is never lost to decoration.
  Future<List<EpisodeInfo>> _episodesFor(SeasonInfo season) {
    return _episodeFutures.putIfAbsent(
      season.number,
      () => guarded(
        context.read<MediaRepository>().episodes(widget.item, season),
      ),
    );
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
              padding: const EdgeInsets.all(LumoraSpace.lg),
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
    final scale = mediaUiScale(context);
    final logoUrl = detail.logoUrl?.trim() ?? '';
    final hasLogo = logoUrl.isNotEmpty;
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
              end: LumoraSpace.lg,
              bottom: LumoraSpace.md,
            ),
            // The logo below the backdrop takes the title's place when TMDB has
            // one; the plain title stays in the bar otherwise.
            title: hasLogo
                ? null
                : Text(
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
              LumoraSpace.lg,
              LumoraSpace.lg,
              LumoraSpace.lg,
              LumoraSpace.xl,
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                if (hasLogo) ...[
                  _TitleLogo(url: logoUrl, title: item.title, scale: scale),
                  const SizedBox(height: LumoraSpace.md),
                ],
                _MetaLine(item: item, runtimeMinutes: detail.runtimeMinutes),
                const SizedBox(height: LumoraSpace.md),
                if (item.genres.isNotEmpty)
                  Wrap(
                    spacing: LumoraSpace.sm,
                    runSpacing: LumoraSpace.sm,
                    children: [
                      for (final genre in item.genres) _GenreChip(label: genre),
                    ],
                  ),
                if (detail.tagline?.trim().isNotEmpty ?? false) ...[
                  const SizedBox(height: LumoraSpace.md),
                  Text(
                    detail.tagline!.trim(),
                    style: theme.textTheme.bodyMedium?.copyWith(
                      fontStyle: FontStyle.italic,
                      color: theme.colorScheme.onSurfaceVariant,
                    ),
                  ),
                ],
                if (item.overview?.trim().isNotEmpty ?? false) ...[
                  const SizedBox(height: LumoraSpace.md),
                  Text(
                    item.overview!.trim(),
                    style: theme.textTheme.bodyMedium?.copyWith(height: 1.45),
                  ),
                ],
                const SizedBox(height: LumoraSpace.xl),
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
                  const SizedBox(height: LumoraSpace.xl),
                  Text(
                    'Episodes',
                    style: theme.textTheme.titleMedium
                        ?.copyWith(fontWeight: FontWeight.w700),
                  ),
                  const SizedBox(height: LumoraSpace.sm),
                  _SeasonHeader(
                    season: season!,
                    seasons: seasons,
                    onChanged: (value) => setState(() => _season = value),
                  ),
                  const SizedBox(height: LumoraSpace.md),
                  _EpisodeList(
                    episodes: _episodesFor(season),
                    season: season,
                    onPlay: (number) => _play(episode: number),
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

/// The title's own logo, with the plain title as the loading and error
/// fallback. The logo never exceeds 72 dp at scale 1.0 and is left aligned;
/// its width is bounded by the page, and both are scaled by the interface
/// setting (never by text scaling, which the app applies globally).
class _TitleLogo extends StatelessWidget {
  const _TitleLogo({
    required this.url,
    required this.title,
    required this.scale,
  });

  final String url;
  final String title;
  final double scale;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final factor = scale.isFinite && scale > 0 ? scale : 1.0;
    final fallback = Text(
      title,
      maxLines: 2,
      overflow: TextOverflow.ellipsis,
      style: theme.textTheme.headlineSmall,
    );
    return Semantics(
      label: title,
      image: true,
      container: true,
      excludeSemantics: true,
      child: Align(
        alignment: Alignment.centerLeft,
        child: ConstrainedBox(
          constraints: BoxConstraints(maxHeight: 72 * factor),
          child: Image.network(
            url,
            fit: BoxFit.contain,
            alignment: Alignment.centerLeft,
            excludeFromSemantics: true,
            // frameBuilder still runs after the image is ready, so the frame
            // number is what separates "loading" from "loaded".
            frameBuilder: (context, child, frame, wasSynchronouslyLoaded) =>
                frame == null ? fallback : child,
            errorBuilder: (context, error, stackTrace) => fallback,
          ),
        ),
      ),
    );
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
            errorBuilder: (_, __, ___) => fallback,
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
          color: favoriteStar,
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

/// The season's own poster beside the picker, as the desktop season picker
/// shows it (`lgl/qt_season_picker.py`). No poster at all is a labelled box,
/// not a hole in the layout.
class _SeasonHeader extends StatelessWidget {
  const _SeasonHeader({
    required this.season,
    required this.seasons,
    required this.onChanged,
  });

  final SeasonInfo season;
  final List<SeasonInfo> seasons;
  final ValueChanged<int> onChanged;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _SeasonPoster(season: season),
        const SizedBox(width: LumoraSpace.md),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              DropdownButtonHideUnderline(
                child: DropdownButton<int>(
                  isExpanded: true,
                  value: season.number,
                  items: [
                    for (final s in seasons)
                      DropdownMenuItem<int>(
                        value: s.number,
                        child: Text(_MediaDetailPageState._seasonLabel(s)),
                      ),
                  ],
                  onChanged: (value) {
                    if (value == null) return;
                    onChanged(value);
                  },
                ),
              ),
              if (season.episodeCount > 0)
                Text(
                  '${season.episodeCount} episodes',
                  style: theme.textTheme.bodySmall?.copyWith(
                    color: theme.colorScheme.onSurfaceVariant,
                  ),
                ),
            ],
          ),
        ),
      ],
    );
  }
}

class _SeasonPoster extends StatelessWidget {
  const _SeasonPoster({required this.season});

  final SeasonInfo season;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final fallback = ColoredBox(
      color: theme.colorScheme.surfaceContainerHighest,
      child: Center(
        child: Text(
          'S${season.number}',
          style: theme.textTheme.titleMedium?.copyWith(
            color: theme.colorScheme.onSurfaceVariant,
          ),
        ),
      ),
    );
    final url = season.posterUrl?.trim() ?? '';
    return ClipRRect(
      borderRadius: BorderRadius.circular(LumoraRadius.md),
      child: SizedBox(
        width: 84,
        child: AspectRatio(
          aspectRatio: 2 / 3,
          child: url.isEmpty
              ? fallback
              : Image.network(
                  url,
                  fit: BoxFit.cover,
                  errorBuilder: (_, __, ___) => fallback,
                  loadingBuilder: (context, child, progress) =>
                      progress == null ? child : fallback,
                ),
        ),
      ),
    );
  }
}

/// The episode rows of one season: provider previews when they load, and the
/// plain numbered list when they do not.
class _EpisodeList extends StatelessWidget {
  const _EpisodeList({
    required this.episodes,
    required this.season,
    required this.onPlay,
  });

  final Future<List<EpisodeInfo>> episodes;
  final SeasonInfo season;
  final ValueChanged<int> onPlay;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return FutureBuilder<List<EpisodeInfo>>(
      future: episodes,
      builder: (context, snapshot) {
        if (snapshot.connectionState == ConnectionState.waiting &&
            !snapshot.hasData) {
          return Text(
            'Loading episodes…',
            style: theme.textTheme.bodySmall?.copyWith(
              color: theme.colorScheme.onSurfaceVariant,
            ),
          );
        }
        var rows = snapshot.data ?? const <EpisodeInfo>[];
        if (rows.isEmpty) {
          rows = <EpisodeInfo>[
            for (var number = 1; number <= season.episodeCount; number++)
              EpisodeInfo(number: number),
          ];
        }
        if (rows.isEmpty) {
          return Text(
            'No episodes listed for this season.',
            style: theme.textTheme.bodySmall?.copyWith(
              color: theme.colorScheme.onSurfaceVariant,
            ),
          );
        }
        return Column(
          children: [
            for (final episode in rows)
              _EpisodeTile(
                episode: episode,
                onTap: () => onPlay(episode.number),
              ),
          ],
        );
      },
    );
  }
}

class _EpisodeTile extends StatelessWidget {
  const _EpisodeTile({required this.episode, required this.onTap});

  final EpisodeInfo episode;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final name = episode.name?.trim() ?? '';
    final title = name.isEmpty
        ? 'Episode ${episode.number}'
        : '${episode.number}. $name';
    final overview = episode.overview?.trim() ?? '';
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: Material(
        color: theme.colorScheme.surfaceContainerHighest
            .withValues(alpha: 0.45),
        borderRadius: BorderRadius.circular(LumoraRadius.md),
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(LumoraRadius.md),
          child: Container(
            constraints: const BoxConstraints(minHeight: 56),
            padding: const EdgeInsets.all(8),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                _EpisodeStill(url: episode.stillUrl),
                const SizedBox(width: LumoraSpace.md),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        title,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: theme.textTheme.bodyMedium,
                      ),
                      if (overview.isNotEmpty) ...[
                        const SizedBox(height: 2),
                        Text(
                          overview,
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                          style: theme.textTheme.bodySmall?.copyWith(
                            color: theme.colorScheme.onSurfaceVariant,
                          ),
                        ),
                      ],
                    ],
                  ),
                ),
                const Padding(
                  padding: EdgeInsets.only(top: 4, left: 6),
                  child: Icon(Icons.play_arrow_rounded, size: 20),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// A 16:9 preview still, or a quiet block when the provider has no art.
class _EpisodeStill extends StatelessWidget {
  const _EpisodeStill({this.url});

  final String? url;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final fallback = Container(
      color: theme.colorScheme.surfaceContainerHighest,
      alignment: Alignment.center,
      child: Icon(
        Icons.movie_outlined,
        size: 18,
        color: theme.colorScheme.onSurfaceVariant,
      ),
    );
    final image = url?.trim() ?? '';
    return ClipRRect(
      borderRadius: BorderRadius.circular(LumoraRadius.sm),
      child: SizedBox(
        width: 104,
        child: AspectRatio(
          aspectRatio: 16 / 9,
          child: image.isEmpty
              ? fallback
              : Image.network(
                  image,
                  fit: BoxFit.cover,
                  errorBuilder: (_, __, ___) => fallback,
                  loadingBuilder: (context, child, progress) =>
                      progress == null ? child : fallback,
                ),
        ),
      ),
    );
  }
}
