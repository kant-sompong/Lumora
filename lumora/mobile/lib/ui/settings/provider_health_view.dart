import 'package:flutter/material.dart';

import '../../data/provider_health.dart';

/// The colour of a health dot, by status.
Color healthStatusColor(HealthStatus? status) {
  switch (status) {
    case HealthStatus.fast:
      return const Color(0xFF34D399);
    case HealthStatus.ok:
      return const Color(0xFF60A5FA);
    case HealthStatus.slow:
      return const Color(0xFFFBBF24);
    case HealthStatus.unreachable:
    case HealthStatus.timedOut:
      return const Color(0xFFF87171);
    case null:
      return const Color(0xFF9CA3AF);
  }
}

/// A small coloured dot for one provider's measured health.
///
/// A provider that has not been checked yet gets a hollow grey dot rather than
/// a red one, so "unknown" never reads as "broken".
class HealthDot extends StatelessWidget {
  const HealthDot({this.health, this.size = 9, super.key});

  final ProviderHealth? health;
  final double size;

  @override
  Widget build(BuildContext context) {
    final colour = healthStatusColor(health?.status);
    final filled = health != null;
    return Semantics(
      label: filled ? 'Provider health' : 'Provider not checked',
      child: Container(
        width: size,
        height: size,
        decoration: BoxDecoration(
          color: filled ? colour : Colors.transparent,
          shape: BoxShape.circle,
          border: filled ? null : Border.all(color: colour, width: 1.5),
        ),
      ),
    );
  }
}
