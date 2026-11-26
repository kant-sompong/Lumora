import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lumora_mobile/theme/lumora_theme.dart';

void main() {
  test('spacing and radius tokens match lgl/theme.py', () {
    expect(<double>[
      LumoraSpace.xs,
      LumoraSpace.sm,
      LumoraSpace.md,
      LumoraSpace.lg,
      LumoraSpace.xl,
      LumoraSpace.xxl,
    ], <double>[4, 8, 16, 24, 32, 48]);
    expect(<double>[
      LumoraRadius.sm,
      LumoraRadius.md,
      LumoraRadius.lg,
    ], <double>[6, 8, 12]);
  });

  test('the theme keeps the PC palette', () {
    expect(LumoraTheme.accent.toARGB32(), 0xFF8557EA);
    expect(LumoraTheme.accentFill.toARGB32(), 0xFF7C3AED);
    expect(LumoraTheme.favorite.toARGB32(), 0xFFF5C518);
    expect(LumoraTheme.darkBackground.toARGB32(), 0xFF0F1014);
    expect(LumoraTheme.lightBackground.toARGB32(), 0xFFF4F5F8);
    expect(LumoraTheme.radiusSm, LumoraRadius.sm);
    expect(LumoraTheme.radiusMd, LumoraRadius.md);
    expect(LumoraTheme.radiusLg, LumoraRadius.lg);
  });

  test('dark() builds a dark theme with the PC background', () {
    final theme = LumoraTheme.dark(LumoraTheme.accent);
    expect(theme.brightness, Brightness.dark);
    expect(theme.scaffoldBackgroundColor.toARGB32(), 0xFF0F1014);
    expect(theme.colorScheme.primary.toARGB32(), 0xFF8557EA);
    expect(theme.colorScheme.surface.toARGB32(), 0xFF16181D);
    expect(theme.dividerColor.toARGB32(), 0xFF2A2D36);
  });

  test('light() builds a light theme with the PC background', () {
    final theme = LumoraTheme.light(LumoraTheme.accent);
    expect(theme.brightness, Brightness.light);
    expect(theme.scaffoldBackgroundColor.toARGB32(), 0xFFF4F5F8);
    expect(theme.colorScheme.primary.toARGB32(), 0xFF8557EA);
    expect(theme.colorScheme.surface.toARGB32(), 0xFFFFFFFF);
  });

  test('a picked accent repaints the theme', () {
    const teal = Color(0xFF0F9D8F);
    final dark = LumoraTheme.dark(teal);
    final light = LumoraTheme.light(teal);
    expect(dark.colorScheme.primary.toARGB32(), teal.toARGB32());
    expect(light.colorScheme.primary.toARGB32(), teal.toARGB32());
    expect(dark.colorScheme.primary.toARGB32(), isNot(0xFF8557EA));
  });

  test('type sizes follow theme.TYPE', () {
    final theme = LumoraTheme.dark(LumoraTheme.accent);
    expect(theme.textTheme.headlineSmall?.fontSize, 22);
    expect(theme.textTheme.titleMedium?.fontSize, 16);
    expect(theme.textTheme.bodyMedium?.fontSize, 13);
    expect(theme.textTheme.bodySmall?.fontSize, 11);
  });

  test('filled accents keep white labels readable', () {
    for (final choice in <Color>[
      LumoraTheme.accent,
      const Color(0xFF0F9D8F),
      const Color(0xFFFACC15),
    ]) {
      final fill = LumoraTheme.filled(choice);
      final label = LumoraTheme.onAccent(fill);
      expect(
        LumoraTheme.contrastRatio(fill, label),
        greaterThanOrEqualTo(4.5),
        reason: '$choice',
      );
    }
  });

  test('the default accent keeps the PC fill shade', () {
    expect(LumoraTheme.filled(LumoraTheme.accent).toARGB32(), 0xFF7C3AED);
  });
}
