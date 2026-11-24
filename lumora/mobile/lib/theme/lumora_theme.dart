import 'package:flutter/material.dart';

/// Spacing scale from `lgl/theme.py` (`SPACE`): 4, 8, 16, 24, 32, 48.
///
/// Use these constants instead of magic numbers so the phone layout keeps the
/// desktop app's rhythm.
abstract final class LumoraSpace {
  static const double xs = 4;
  static const double sm = 8;
  static const double md = 16;
  static const double lg = 24;
  static const double xl = 32;
  static const double xxl = 48;
}

/// Radii from `lgl/theme.py` (`RADIUS`): cards/buttons, dialogs, pills.
abstract final class LumoraRadius {
  static const double sm = 6;
  static const double md = 8;
  static const double lg = 12;
  static const double pill = 999;
}

/// The single source of truth for Lumora's phone look.
///
/// Ported from the PC `lgl/theme.py`: same palette tokens, spacing scale,
/// radii and type sizes (`TYPE`: page 22, title 16, body 13, small 11).
///
/// The accent has three roles on the desktop and they are kept apart here for
/// the same contrast reason: [accent] is the tint, [accentFill] is the surface
/// under a white label, and the `accentText` token is the tint as ink. White on
/// the default tint is only 4.6:1, so a filled button uses the fill shade.
abstract final class LumoraTheme {
  /// Chart/selection purple (`theme.ACCENT`), also the default setting.
  static const Color accent = Color(0xFF8557EA);

  /// Filled surfaces that carry white labels (`theme.ACCENT_FILL`).
  static const Color accentFill = Color(0xFF7C3AED);
  static const Color accentFillHover = Color(0xFF6D28D9);

  /// The one warm colour kept from the desktop design (`theme.FAVORITE`).
  static const Color favorite = Color(0xFFF5C518);

  // Dark palette (`theme.DARK`).
  static const Color darkBackground = Color(0xFF0F1014);
  static const Color darkSurface = Color(0xFF16181D);
  static const Color darkRaised = Color(0xFF1D2027);
  static const Color darkHover = Color(0xFF262B34);
  static const Color darkDivider = Color(0xFF2A2D36);
  static const Color darkText = Color(0xFFE8E9EE);
  static const Color darkMuted = Color(0xFFA4A7B3);
  static const Color darkFaint = Color(0xFF858998);
  static const Color darkAccentText = Color(0xFFA78BFA);
  static const Color darkSuccess = Color(0xFF22C55E);
  static const Color darkWarning = Color(0xFFF59E0B);
  static const Color darkDanger = Color(0xFFEF4444);

  // Light palette (`theme.LIGHT`).
  static const Color lightBackground = Color(0xFFF4F5F8);
  static const Color lightSurface = Color(0xFFFFFFFF);
  static const Color lightRaised = Color(0xFFFFFFFF);
  static const Color lightHover = Color(0xFFECEEF2);
  static const Color lightDivider = Color(0xFFDCDFE6);
  static const Color lightText = Color(0xFF14161C);
  static const Color lightMuted = Color(0xFF4A4F5C);
  static const Color lightFaint = Color(0xFF6B7080);
  static const Color lightAccentText = Color(0xFF6D28D9);
  static const Color lightSuccess = Color(0xFF15803D);
  static const Color lightWarning = Color(0xFFB45309);
  static const Color lightDanger = Color(0xFFB91C1C);

  /// Radii, reachable from the theme for call sites that already hold one.
  static const double radiusSm = LumoraRadius.sm;
  static const double radiusMd = LumoraRadius.md;
  static const double radiusLg = LumoraRadius.lg;

  /// WCAG 2.1 contrast ratio between two opaque colours (1.0 – 21.0).
  static double contrastRatio(Color a, Color b) {
    final first = a.computeLuminance();
    final second = b.computeLuminance();
    final lighter = first > second ? first : second;
    final darker = first > second ? second : first;
    return (lighter + 0.05) / (darker + 0.05);
  }

  /// A contrast-safe label colour for [accent] used as a filled surface.
  ///
  /// Mirrors the PC `with_accent()` idea: keep white where it reaches AA,
  /// otherwise switch to ink.
  static Color onAccent(Color accent) {
    return contrastRatio(accent, Colors.white) >= 4.5
        ? Colors.white
        : const Color(0xFF0B0E14);
  }

  /// The fill shade for buttons, switches and selected controls.
  ///
  /// The default accent keeps the PC's exact `accent_fill`. A custom accent is
  /// darkened in small steps until white labels clear 4.5:1.
  static Color filled(Color accent) {
    if (accent.toARGB32() == LumoraTheme.accent.toARGB32()) {
      return accentFill;
    }
    var color = accent;
    for (var step = 0; step < 12; step++) {
      if (contrastRatio(color, Colors.white) >= 4.5) {
        return color;
      }
      color = Color.lerp(color, Colors.black, 0.08)!;
    }
    return color;
  }

  /// A low-emphasis accent wash for selected rows, chips and badges.
  static Color accentWash(
    Color accent,
    Color surface, {
    double opacity = 0.18,
  }) {
    return Color.alphaBlend(accent.withValues(alpha: opacity), surface);
  }

  static ThemeData dark(Color accent) => _build(
    brightness: Brightness.dark,
    accent: accent,
    background: darkBackground,
    surface: darkSurface,
    raised: darkRaised,
    hover: darkHover,
    divider: darkDivider,
    text: darkText,
    muted: darkMuted,
    accentText: darkAccentText,
    success: darkSuccess,
    warning: darkWarning,
    danger: darkDanger,
  );

  static ThemeData light(Color accent) => _build(
    brightness: Brightness.light,
    accent: accent,
    background: lightBackground,
    surface: lightSurface,
    raised: lightRaised,
    hover: lightHover,
    divider: lightDivider,
    text: lightText,
    muted: lightMuted,
    accentText: lightAccentText,
    success: lightSuccess,
    warning: lightWarning,
    danger: lightDanger,
  );

  static ThemeData _build({
    required Brightness brightness,
    required Color accent,
    required Color background,
    required Color surface,
    required Color raised,
    required Color hover,
    required Color divider,
    required Color text,
    required Color muted,
    required Color accentText,
    required Color success,
    required Color warning,
    required Color danger,
  }) {
    final fill = filled(accent);
    final onFill = onAccent(fill);

    final base = ThemeData(
      useMaterial3: true,
      brightness: brightness,
      colorScheme:
          ColorScheme.fromSeed(
            seedColor: accent,
            brightness: brightness,
          ).copyWith(
            primary: accent,
            onPrimary: onFill,
            secondary: accent,
            onSecondary: onFill,
            surface: surface,
            onSurface: text,
            surfaceContainerHighest: raised,
            outline: divider,
            outlineVariant: divider,
            error: danger,
            onError: onFill,
          ),
      scaffoldBackgroundColor: background,
      canvasColor: background,
      dividerColor: divider,
      splashFactory: InkSparkle.splashFactory,
      visualDensity: VisualDensity.compact,
    );

    // Type scale from theme.TYPE: page 22, title 16, body 13, small 11.
    final textTheme = base.textTheme
        .apply(bodyColor: text, displayColor: text)
        .copyWith(
          headlineSmall: TextStyle(
            fontSize: 22,
            fontWeight: FontWeight.w700,
            color: text,
          ),
          titleLarge: TextStyle(
            fontSize: 18,
            fontWeight: FontWeight.w700,
            color: text,
          ),
          titleMedium: TextStyle(
            fontSize: 16,
            fontWeight: FontWeight.w600,
            color: text,
          ),
          bodyMedium: TextStyle(fontSize: 13, height: 1.4, color: text),
          bodySmall: TextStyle(fontSize: 11, height: 1.3, color: muted),
          labelLarge: const TextStyle(
            fontSize: 13,
            fontWeight: FontWeight.w600,
          ),
          labelSmall: TextStyle(fontSize: 11, color: muted),
        );

    final rounded = RoundedRectangleBorder(
      borderRadius: BorderRadius.circular(LumoraRadius.md),
    );

    return base.copyWith(
      textTheme: textTheme,
      primaryColor: accent,
      appBarTheme: AppBarTheme(
        backgroundColor: background,
        foregroundColor: text,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        scrolledUnderElevation: 0,
        centerTitle: false,
        iconTheme: IconThemeData(color: text, size: 24),
        titleTextStyle: TextStyle(
          fontSize: 20,
          fontWeight: FontWeight.w700,
          color: text,
        ),
        toolbarTextStyle: textTheme.bodyMedium,
      ),
      cardTheme: CardThemeData(
        color: raised,
        surfaceTintColor: Colors.transparent,
        shadowColor: Colors.black.withValues(alpha: 0.55),
        elevation: 0,
        margin: EdgeInsets.zero,
        clipBehavior: Clip.antiAlias,
        shape: rounded,
      ),
      dialogTheme: DialogThemeData(
        backgroundColor: raised,
        surfaceTintColor: Colors.transparent,
        titleTextStyle: textTheme.titleLarge,
        contentTextStyle: textTheme.bodyMedium,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(LumoraRadius.lg),
        ),
      ),
      bottomSheetTheme: BottomSheetThemeData(
        backgroundColor: raised,
        surfaceTintColor: Colors.transparent,
        modalBackgroundColor: raised,
        shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(
            top: Radius.circular(LumoraRadius.lg),
          ),
        ),
      ),
      tabBarTheme: TabBarThemeData(
        labelColor: text,
        unselectedLabelColor: muted,
        indicatorColor: accent,
        indicatorSize: TabBarIndicatorSize.tab,
        dividerColor: divider,
        labelStyle: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600),
        unselectedLabelStyle: const TextStyle(
          fontSize: 13,
          fontWeight: FontWeight.w500,
        ),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: raised,
        isDense: true,
        hintStyle: TextStyle(color: muted, fontSize: 13),
        labelStyle: TextStyle(color: muted, fontSize: 13),
        prefixIconColor: muted,
        suffixIconColor: muted,
        contentPadding: const EdgeInsets.symmetric(
          horizontal: LumoraSpace.md,
          vertical: LumoraSpace.sm,
        ),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(LumoraRadius.md),
          borderSide: BorderSide(color: divider),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(LumoraRadius.md),
          borderSide: BorderSide(color: divider),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(LumoraRadius.md),
          borderSide: BorderSide(color: accent, width: 1.5),
        ),
        errorBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(LumoraRadius.md),
          borderSide: BorderSide(color: danger),
        ),
        focusedErrorBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(LumoraRadius.md),
          borderSide: BorderSide(color: danger, width: 1.5),
        ),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          backgroundColor: fill,
          foregroundColor: onFill,
          minimumSize: const Size(0, 44),
          padding: const EdgeInsets.symmetric(horizontal: LumoraSpace.md),
          shape: rounded,
          textStyle: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          foregroundColor: text,
          minimumSize: const Size(0, 44),
          padding: const EdgeInsets.symmetric(horizontal: LumoraSpace.md),
          side: BorderSide(color: divider),
          shape: rounded,
          textStyle: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600),
        ),
      ),
      textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(
          foregroundColor: accentText,
          minimumSize: const Size(0, 44),
          shape: rounded,
          textStyle: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600),
        ),
      ),
      iconButtonTheme: IconButtonThemeData(
        style: IconButton.styleFrom(
          foregroundColor: text,
          minimumSize: const Size(44, 44),
        ),
      ),
      switchTheme: SwitchThemeData(
        thumbColor: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) {
            return onFill;
          }
          return muted;
        }),
        trackColor: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) {
            return fill;
          }
          return hover;
        }),
        trackOutlineColor: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) {
            return Colors.transparent;
          }
          return divider;
        }),
      ),
      checkboxTheme: CheckboxThemeData(
        fillColor: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) {
            return fill;
          }
          return Colors.transparent;
        }),
        checkColor: WidgetStatePropertyAll(onFill),
        side: BorderSide(color: divider, width: 1.5),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(LumoraRadius.sm),
        ),
      ),
      sliderTheme: SliderThemeData(
        activeTrackColor: fill,
        inactiveTrackColor: hover,
        thumbColor: fill,
        overlayColor: accent.withValues(alpha: 0.14),
      ),
      progressIndicatorTheme: ProgressIndicatorThemeData(
        color: fill,
        linearTrackColor: hover,
        circularTrackColor: hover,
      ),
      dividerTheme: DividerThemeData(color: divider, thickness: 1, space: 1),
      chipTheme: ChipThemeData(
        backgroundColor: raised,
        selectedColor: accentWash(accent, raised),
        disabledColor: raised,
        side: BorderSide(color: divider),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(LumoraRadius.pill),
        ),
        labelStyle: const TextStyle(fontSize: 11),
        padding: const EdgeInsets.symmetric(
          horizontal: LumoraSpace.sm,
          vertical: 2,
        ),
      ),
      listTileTheme: ListTileThemeData(
        iconColor: muted,
        textColor: text,
        subtitleTextStyle: TextStyle(fontSize: 11, color: muted),
        contentPadding: const EdgeInsets.symmetric(horizontal: LumoraSpace.md),
        minVerticalPadding: LumoraSpace.sm,
      ),
      snackBarTheme: SnackBarThemeData(
        backgroundColor: raised,
        contentTextStyle: TextStyle(color: text, fontSize: 13),
        actionTextColor: accentText,
        behavior: SnackBarBehavior.floating,
        shape: rounded,
      ),
      tooltipTheme: TooltipThemeData(
        decoration: BoxDecoration(
          color: raised,
          borderRadius: BorderRadius.circular(LumoraRadius.sm),
          border: Border.all(color: divider),
        ),
        textStyle: TextStyle(color: text, fontSize: 11),
      ),
      scrollbarTheme: ScrollbarThemeData(
        thumbColor: WidgetStatePropertyAll(divider),
        radius: const Radius.circular(LumoraRadius.pill),
        thickness: const WidgetStatePropertyAll(4),
      ),
      popupMenuTheme: PopupMenuThemeData(
        color: raised,
        surfaceTintColor: Colors.transparent,
        shape: rounded,
        textStyle: textTheme.bodyMedium,
      ),
    );
  }
}
