import 'package:material_ui/material_ui.dart';

import '../../../models/media_item.dart';
import '../../../theme/lumora_theme.dart';
import 'media_colors.dart';

/// A poster tile: artwork, then title and the year/rating line.
///
/// The PC app draws these at 158 px, scaled by the home layout; a phone shelf
/// uses [normalWidth] and [compactWidth] (the PC's "Compact layout" switch).
class PosterCard extends StatelessWidget {
  const PosterCard({
    super.key,
    required this.item,
    this.onTap,
    this.compact = false,
    this.width,
  });

  final MediaItem item;
  final VoidCallback? onTap;
  final bool compact;

  /// When null the card fills its parent (used by the search grid).
  final double? width;

  static const double normalWidth = 120;
  static const double compactWidth = 96;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final titleStyle = (compact
            ? theme.textTheme.bodySmall
            : theme.textTheme.titleSmall)
        ?.copyWith(fontWeight: FontWeight.w600);

    final card = InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(LumoraRadius.md),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          ClipRRect(
            borderRadius: BorderRadius.circular(LumoraRadius.md),
            child: AspectRatio(
              aspectRatio: 2 / 3,
              child: _Poster(item: item),
            ),
          ),
          const SizedBox(height: 6),
          Text(
            item.title,
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
            style: titleStyle,
          ),
          const SizedBox(height: 2),
          _MetaLine(item: item),
        ],
      ),
    );

    final cardWidth = width;
    if (cardWidth == null) return card;
    return SizedBox(width: cardWidth, child: card);
  }
}

class _Poster extends StatelessWidget {
  const _Poster({required this.item});

  final MediaItem item;

  @override
  Widget build(BuildContext context) {
    const placeholder = _PosterPlaceholder();
    final url = item.posterUrl?.trim() ?? '';
    if (url.isEmpty) return placeholder;
    return Image.network(
      url,
      fit: BoxFit.cover,
      errorBuilder: (_, _, _) => placeholder,
      loadingBuilder: (context, child, progress) =>
          progress == null ? child : placeholder,
    );
  }
}

class _PosterPlaceholder extends StatelessWidget {
  const _PosterPlaceholder();

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return ColoredBox(
      color: theme.colorScheme.surfaceContainerHighest,
      child: Center(
        child: Icon(
          Icons.movie_outlined,
          color: theme.colorScheme.onSurfaceVariant.withValues(alpha: 0.6),
          size: 32,
        ),
      ),
    );
  }
}

class _MetaLine extends StatelessWidget {
  const _MetaLine({required this.item});

  final MediaItem item;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final muted = theme.textTheme.bodySmall?.copyWith(
      color: theme.colorScheme.onSurfaceVariant,
    );
    final year = item.year?.trim() ?? '';
    final rating = item.rating;
    return Row(
      children: [
        Expanded(
          child: Text(
            year,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: muted,
          ),
        ),
        if (rating != null) ...[
          const SizedBox(width: 4),
          const Icon(Icons.star_rounded, size: 12, color: favoriteStar),
          const SizedBox(width: 2),
          Text(
            rating.toStringAsFixed(1),
            maxLines: 1,
            softWrap: false,
            style: muted,
          ),
        ],
      ],
    );
  }
}
