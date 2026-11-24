import 'package:material_ui/material_ui.dart';

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
  });

  final MediaShelf shelf;
  final ValueChanged<MediaItem> onItemTap;
  final bool compact;

  static const double _gap = LumoraSpace.md;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cardWidth =
        compact ? PosterCard.compactWidth : PosterCard.normalWidth;
    // Poster (3:2 portrait) plus the two text lines under it.
    final stripHeight = cardWidth * 1.5 + (compact ? 62 : 70);

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
            separatorBuilder: (_, _) => const SizedBox(width: _gap),
            itemBuilder: (context, index) {
              final item = shelf.items[index];
              return PosterCard(
                item: item,
                width: cardWidth,
                compact: compact,
                onTap: () => onItemTap(item),
              );
            },
          ),
        ),
      ],
    );
  }
}
