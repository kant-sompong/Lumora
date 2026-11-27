import 'package:flutter/material.dart';

/// The small "18+" pill on a poster whose title TMDB flags as adult.
///
/// A dark translucent fill with a white label reads on light and dark artwork
/// alike. [scale] changes the pill's padding and radius (never its text), so it
/// follows [AppSettings.uiScale] with the poster.
class AdultBadge extends StatelessWidget {
  const AdultBadge({super.key, this.scale = 1.0});

  static const String semanticsLabel = '18 plus';
  static const String label = '18+';

  final double scale;

  @override
  Widget build(BuildContext context) {
    final factor = scale.isFinite && scale > 0 ? scale : 1.0;
    return Semantics(
      label: semanticsLabel,
      container: true,
      excludeSemantics: true,
      child: DecoratedBox(
        decoration: BoxDecoration(
          color: const Color(0xE6000000),
          borderRadius: BorderRadius.circular(4 * factor),
          border: Border.all(color: const Color(0x99FFFFFF), width: 0.5),
        ),
        child: Padding(
          padding: EdgeInsets.symmetric(
            horizontal: 5 * factor,
            vertical: 1.5 * factor,
          ),
          child: const Text(
            label,
            maxLines: 1,
            softWrap: false,
            style: TextStyle(
              color: Color(0xFFFFFFFF),
              fontSize: 11,
              fontWeight: FontWeight.w700,
              height: 1.2,
            ),
          ),
        ),
      ),
    );
  }
}
