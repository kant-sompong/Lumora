import 'package:flutter/material.dart';

import '../../../models/media_item.dart';
import '../../../theme/lumora_theme.dart';
import 'poster_card.dart';

/// One home shelf: its title and a horizontal strip of posters.
class ShelfRow extends StatelessWidget {
  const ShelfRow({
    super.key,
    required this.shelf,
    required this.onItemTap,
    this.compact = false,
    this.scale = 1.0,
  });

  final MediaShelf shelf;
  final ValueChanged<MediaItem> onItemTap;
  final bool compact;

  /// The interface scale for poster width and row height
  /// ([AppSettings.uiScale]). Text is left to the global text scaler.
  final double scale;

  static const double _gap = LumoraSpace.md;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final factor = scale.isFinite && scale > 0 ? scale : 1.0;
    final cardWidth =
        (compact ? PosterCard.compactWidth : PosterCard.normalWidth) * factor;
    // Poster (2:3 portrait) plus the two text lines under it. The text part
    // grows with the system font size while the 8 dp of spacing does not, so
    // a large system text scale cannot push the text past the fixed row.
    final textScaler = MediaQuery.textScalerOf(context);
    final textHeight = 8 + textScaler.scale(compact ? 54 : 62);
    final stripHeight = cardWidth * 1.5 + textHeight;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(
            LumoraSpace.lg,
            0,
            LumoraSpace.lg,
            LumoraSpace.sm,
          ),
          child: Row(
            children: [
              Expanded(
                child: Text(
                  shelf.title,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: theme.textTheme.titleMedium
                      ?.copyWith(fontWeight: FontWeight.w700),
                ),
              ),
              Text(
                '${shelf.items.length}',
                style: theme.textTheme.bodySmall
                    ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
              ),
            ],
          ),
        ),
        SizedBox(
          height: stripHeight,
          child: ListView.separated(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.symmetric(horizontal: LumoraSpace.lg),
            itemCount: shelf.items.length,
            separatorBuilder: (_, __) => const SizedBox(width: _gap),
            itemBuilder: (context, index) {
              final item = shelf.items[index];
              return PosterCard(
                item: item,
                width: cardWidth,
                compact: compact,
                scale: factor,
                onTap: () => onItemTap(item),
              );
            },
          ),
        ),
      ],
    );
  }
}
