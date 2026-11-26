import 'package:flutter/material.dart';

import '../../data/provider_health.dart';
import '../../theme/lumora_theme.dart';
import 'player_page.dart';
import '../settings/provider_health_view.dart';

/// The slide-in panel on the player's left edge.
///
/// It is the player's only chrome: back, what is playing, the provider it is
/// playing from, the switch list and Reload. Nothing here is drawn over the
/// video until the user asks for it.
class PlayerPanel extends StatelessWidget {
  const PlayerPanel({
    required this.title,
    required this.subtitle,
    required this.targets,
    required this.selectedIndex,
    required this.health,
    required this.onBack,
    required this.onClose,
    required this.onNextProvider,
    required this.onSelectProvider,
    required this.onReload,
    super.key,
  });

  final String title;

  /// `Season 1 · Episode 3`, or null for a film.
  final String? subtitle;

  final List<PlaybackTarget> targets;
  final int selectedIndex;
  final ProviderHealthService? health;
  final VoidCallback onBack;
  final VoidCallback onClose;
  final VoidCallback onNextProvider;
  final ValueChanged<int> onSelectProvider;
  final VoidCallback onReload;

  PlaybackTarget? get _current =>
      selectedIndex >= 0 && selectedIndex < targets.length
      ? targets[selectedIndex]
      : null;

  ProviderHealth? _healthFor(PlaybackTarget target) =>
      health?.cached(target.providerId);

  String _describe(PlaybackTarget target) {
    final entry = _healthFor(target);
    return entry == null || health == null
        ? 'Not checked yet'
        : health!.describe(entry);
  }

  @override
  Widget build(BuildContext context) {
    final current = _current;
    return Material(
      color: const Color(0xF2101116),
      child: SafeArea(
        right: false,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: <Widget>[
            // Header: the back arrow leaves the player, the close arrow only
            // puts the panel away.
            Row(
              children: <Widget>[
                IconButton(
                  onPressed: onBack,
                  tooltip: 'Back',
                  icon: const Icon(Icons.arrow_back, color: Colors.white),
                ),
                Expanded(
                  child: Text(
                    title,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                      color: Colors.white,
                      fontSize: 15,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ),
                IconButton(
                  onPressed: onClose,
                  tooltip: 'Close menu',
                  icon: const Icon(Icons.close, color: Colors.white70),
                ),
              ],
            ),
            if (subtitle != null)
              Padding(
                padding: const EdgeInsets.fromLTRB(
                  LumoraSpace.md,
                  0,
                  LumoraSpace.md,
                  LumoraSpace.sm,
                ),
                child: Text(
                  subtitle!,
                  style: const TextStyle(color: Color(0xB3FFFFFF), fontSize: 12),
                ),
              ),
            const Divider(height: 1, color: Color(0x33FFFFFF)),
            if (current != null)
              Padding(
                padding: const EdgeInsets.fromLTRB(
                  LumoraSpace.md,
                  LumoraSpace.sm,
                  LumoraSpace.md,
                  LumoraSpace.xs,
                ),
                child: Row(
                  children: <Widget>[
                    HealthDot(health: _healthFor(current)),
                    const SizedBox(width: LumoraSpace.sm),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: <Widget>[
                          Text(
                            current.providerName,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(
                              color: Colors.white,
                              fontSize: 13,
                              fontWeight: FontWeight.w600,
                            ),
                          ),
                          Text(
                            _describe(current),
                            style: const TextStyle(
                              color: Color(0x99FFFFFF),
                              fontSize: 11,
                            ),
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
            Padding(
              padding: const EdgeInsets.symmetric(
                horizontal: LumoraSpace.md,
                vertical: LumoraSpace.xs,
              ),
              child: FilledButton.icon(
                onPressed: targets.length > 1 ? onNextProvider : null,
                icon: const Icon(Icons.skip_next, size: 18),
                label: const Text('Next provider'),
              ),
            ),
            const Padding(
              padding: EdgeInsets.fromLTRB(
                LumoraSpace.md,
                LumoraSpace.sm,
                LumoraSpace.md,
                LumoraSpace.xs,
              ),
              child: Text(
                'Providers',
                style: TextStyle(
                  color: Color(0x99FFFFFF),
                  fontSize: 11,
                  letterSpacing: 0.6,
                ),
              ),
            ),
            Expanded(
              child: ListView.builder(
                padding: const EdgeInsets.only(bottom: LumoraSpace.sm),
                itemCount: targets.length,
                itemBuilder: (BuildContext context, int index) {
                  final target = targets[index];
                  final selected = index == selectedIndex;
                  return InkWell(
                    onTap: () => onSelectProvider(index),
                    child: Padding(
                      padding: const EdgeInsets.symmetric(
                        horizontal: LumoraSpace.md,
                        vertical: LumoraSpace.sm,
                      ),
                      child: Row(
                        children: <Widget>[
                          HealthDot(health: _healthFor(target)),
                          const SizedBox(width: LumoraSpace.sm),
                          Expanded(
                            child: Text(
                              target.providerName,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: TextStyle(
                                color: selected
                                    ? Colors.white
                                    : const Color(0xCCFFFFFF),
                                fontSize: 13,
                                fontWeight: selected
                                    ? FontWeight.w700
                                    : FontWeight.w400,
                              ),
                            ),
                          ),
                          Text(
                            _describe(target),
                            style: const TextStyle(
                              color: Color(0x80FFFFFF),
                              fontSize: 10,
                            ),
                          ),
                        ],
                      ),
                    ),
                  );
                },
              ),
            ),
            const Divider(height: 1, color: Color(0x33FFFFFF)),
            TextButton.icon(
              onPressed: onReload,
              icon: const Icon(Icons.refresh, color: Colors.white),
              label: const Text(
                'Reload',
                style: TextStyle(color: Colors.white),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
