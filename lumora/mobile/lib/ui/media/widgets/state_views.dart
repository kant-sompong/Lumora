import 'package:material_ui/material_ui.dart';

import '../../../nav.dart';
import '../../../theme/lumora_theme.dart';
import 'poster_card.dart';

/// Grey stand-ins shown while the first shelf request is in flight.
///
/// The PC app fills its rows asynchronously behind a placeholder; this is the
/// phone version: no spinner, so the page does not jump when data lands.
class MediaHomeSkeleton extends StatelessWidget {
  const MediaHomeSkeleton({super.key, this.shelfCount = 3});

  final int shelfCount;

  @override
  Widget build(BuildContext context) {
    final base = Theme.of(context).colorScheme.surfaceContainerHighest;
    return ListView.separated(
      padding: const EdgeInsets.symmetric(vertical: LumoraTheme.gapLg),
      itemCount: shelfCount,
      separatorBuilder: (_, _) => const SizedBox(height: LumoraTheme.gapXl),
      itemBuilder: (context, index) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            height: 16,
            width: 140,
            margin: const EdgeInsets.only(left: LumoraTheme.gapLg),
            decoration: BoxDecoration(
              color: base,
              borderRadius: BorderRadius.circular(LumoraTheme.radiusSm),
            ),
          ),
          const SizedBox(height: LumoraTheme.gapMd),
          SizedBox(
            height: PosterCard.normalWidth * 1.5,
            child: ListView.separated(
              scrollDirection: Axis.horizontal,
              physics: const NeverScrollableScrollPhysics(),
              padding: const EdgeInsets.symmetric(horizontal: LumoraTheme.gapLg),
              itemCount: 4,
              separatorBuilder: (_, _) =>
                  const SizedBox(width: LumoraTheme.gapMd),
              itemBuilder: (context, index) => Container(
                width: PosterCard.normalWidth,
                decoration: BoxDecoration(
                  color: base,
                  borderRadius: BorderRadius.circular(LumoraTheme.radiusMd),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// A plain grey block for a detail page that is still loading.
class MediaDetailSkeleton extends StatelessWidget {
  const MediaDetailSkeleton({super.key});

  @override
  Widget build(BuildContext context) {
    final base = Theme.of(context).colorScheme.surfaceContainerHighest;
    Widget bar(double widthFactor, double height) => FractionallySizedBox(
          alignment: Alignment.centerLeft,
          widthFactor: widthFactor,
          child: Container(
            height: height,
            margin: const EdgeInsets.only(bottom: LumoraTheme.gapMd),
            decoration: BoxDecoration(
              color: base,
              borderRadius: BorderRadius.circular(LumoraTheme.radiusSm),
            ),
          ),
        );
    return Padding(
      padding: const EdgeInsets.all(LumoraTheme.gapLg),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            height: 140,
            decoration: BoxDecoration(
              color: base,
              borderRadius: BorderRadius.circular(LumoraTheme.radiusLg),
            ),
          ),
          const SizedBox(height: LumoraTheme.gapLg),
          bar(0.6, 22),
          bar(0.35, 14),
          bar(1, 14),
          bar(0.85, 14),
        ],
      ),
    );
  }
}

/// The failure card used by every media page; Retry runs [onRetry].
class MediaErrorCard extends StatelessWidget {
  const MediaErrorCard({
    super.key,
    required this.message,
    required this.onRetry,
    this.detail,
  });

  final String message;
  final VoidCallback onRetry;
  final String? detail;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Card(
      margin: EdgeInsets.zero,
      child: Padding(
        padding: const EdgeInsets.all(LumoraTheme.gapLg),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(Icons.error_outline, color: theme.colorScheme.error),
                const SizedBox(width: LumoraTheme.gapSm),
                Expanded(
                  child: Text(message, style: theme.textTheme.titleSmall),
                ),
              ],
            ),
            if (detail != null) ...[
              const SizedBox(height: LumoraTheme.gapSm),
              Text(
                detail!,
                style: theme.textTheme.bodySmall
                    ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
              ),
            ],
            const SizedBox(height: LumoraTheme.gapMd),
            Align(
              alignment: Alignment.centerRight,
              child: OutlinedButton(
                onPressed: onRetry,
                child: const Text('Retry'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// Shown when the TMDB key is missing: the PC app's "Add a TMDB key to unlock
/// Movies & TV" card, with the same pointer to Settings → Providers.
class MissingApiKeyCard extends StatelessWidget {
  const MissingApiKeyCard({super.key, this.onOpenSettings, this.message});

  final VoidCallback? onOpenSettings;
  final String? message;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Card(
      margin: EdgeInsets.zero,
      child: Padding(
        padding: const EdgeInsets.all(LumoraTheme.gapLg),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(Icons.key_outlined, color: theme.colorScheme.primary),
                const SizedBox(width: LumoraTheme.gapSm),
                Expanded(
                  child: Text(
                    'Add your TMDB key in Settings',
                    style: theme.textTheme.titleMedium
                        ?.copyWith(fontWeight: FontWeight.w700),
                  ),
                ),
              ],
            ),
            const SizedBox(height: LumoraTheme.gapSm),
            Text(
              message ??
                  'Movies and TV use a free TMDB API key. Add it in Settings → '
                      'Providers; anime comes from AniList and needs no key.',
              style: theme.textTheme.bodyMedium
                  ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
            ),
            const SizedBox(height: LumoraTheme.gapLg),
            SizedBox(
              width: double.infinity,
              height: 48,
              child: FilledButton.icon(
                onPressed: onOpenSettings ?? () => openSettings(context),
                icon: const Icon(Icons.settings_outlined),
                label: const Text('Open Settings'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// A quiet empty state (search with no hits, a shelf with nothing in it).
class MediaEmptyView extends StatelessWidget {
  const MediaEmptyView({
    super.key,
    required this.message,
    this.icon = Icons.search_off_rounded,
    this.hint,
  });

  final String message;
  final IconData icon;
  final String? hint;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.all(LumoraTheme.gapXl),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 40, color: theme.colorScheme.onSurfaceVariant),
          const SizedBox(height: LumoraTheme.gapMd),
          Text(
            message,
            textAlign: TextAlign.center,
            style: theme.textTheme.titleSmall,
          ),
          if (hint != null) ...[
            const SizedBox(height: LumoraTheme.gapSm),
            Text(
              hint!,
              textAlign: TextAlign.center,
              style: theme.textTheme.bodySmall
                  ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
            ),
          ],
        ],
      ),
    );
  }
}
