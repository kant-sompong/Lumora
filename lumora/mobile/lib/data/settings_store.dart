import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'providers_registry.dart';

/// User settings persisted with `shared_preferences`.
///
/// The defaults follow the PC app: no provider switched on until the user picks
/// one, the system theme, the PC accent (`lgl/theme.py` `ACCENT`) and
/// full-size poster cards. `load()` seeds every provider, in PC order, on a
/// first run.
class AppSettings extends ChangeNotifier {
  AppSettings({
    String tmdbApiKey = '',
    String malClientId = '',
    List<String>? enabledProviderIds,
    String? defaultProviderId,
    ThemeMode themeMode = ThemeMode.system,
    Color accent = const Color(0xFF8557EA),
    bool compactCards = false,
    bool autoProvider = true,
    double uiScale = 1.0,
  }) : _tmdbApiKey = tmdbApiKey,
       _malClientId = malClientId.trim(),
       _enabledProviderIds = _sanitize(enabledProviderIds ?? const <String>[]),
       _defaultProviderId = defaultProviderId,
       _themeMode = themeMode,
       _accent = accent,
       _compactCards = compactCards,
       _autoProvider = autoProvider,
       _uiScale = clampUiScale(uiScale);

  /// Keys are the snake_case names agent C's contract fixes.
  static const String keyTmdbApiKey = 'tmdb_api_key';
  static const String keyMalClientId = 'mal_client_id';
  static const String keyEnabledProviderIds = 'enabled_provider_ids';
  static const String keyDefaultProviderId = 'default_provider_id';
  static const String keyThemeMode = 'theme_mode';
  static const String keyAccentColor = 'accent_color';
  static const String keyCompactCards = 'compact_cards';
  static const String keyAutoProvider = 'auto_provider';
  static const String keyUiScale = 'ui_scale';

  /// The range [uiScale] is kept in (contract v3).
  static const double minUiScale = 0.8;
  static const double maxUiScale = 1.3;

  /// Every provider id, in PC order.
  static List<String> get defaultProviderIds =>
      allProviders.map((provider) => provider.id).toList(growable: false);

  String _tmdbApiKey;
  String _malClientId;
  List<String> _enabledProviderIds;
  String? _defaultProviderId;
  ThemeMode _themeMode;
  Color _accent;
  bool _compactCards;
  bool _autoProvider;
  double _uiScale;

  String get tmdbApiKey => _tmdbApiKey;

  set tmdbApiKey(String value) {
    final trimmed = value.trim();
    if (_tmdbApiKey == trimmed) {
      return;
    }
    _tmdbApiKey = trimmed;
    notifyListeners();
  }

  /// The MyAnimeList Client ID, trimmed. Never logged and never shown in the
  /// clear; [MalClient] sends it only in the `X-MAL-CLIENT-ID` header.
  String get malClientId => _malClientId;

  set malClientId(String value) {
    final trimmed = value.trim();
    if (_malClientId == trimmed) {
      return;
    }
    _malClientId = trimmed;
    notifyListeners();
  }

  /// Whether a reached provider may be chosen automatically (PC setting
  /// `provider_auto_select`, default on).
  bool get autoProvider => _autoProvider;

  set autoProvider(bool value) {
    if (_autoProvider == value) {
      return;
    }
    _autoProvider = value;
    notifyListeners();
  }

  /// Interface scale factor, clamped to [minUiScale]-[maxUiScale].
  double get uiScale => _uiScale;

  set uiScale(double value) {
    final next = clampUiScale(value);
    if (_uiScale == next) {
      return;
    }
    _uiScale = next;
    notifyListeners();
  }

  /// Clamps a stored or requested scale to the contract's 0.8-1.3 range.
  static double clampUiScale(double value) {
    if (value.isNaN) {
      return 1.0;
    }
    if (value < minUiScale) {
      return minUiScale;
    }
    if (value > maxUiScale) {
      return maxUiScale;
    }
    return value;
  }

  /// Enabled providers in the user's order.
  List<String> get enabledProviderIds =>
      List<String>.unmodifiable(_enabledProviderIds);

  set enabledProviderIds(List<String> value) {
    final next = _sanitize(value);
    if (_sameIds(next, _enabledProviderIds)) {
      return;
    }
    _enabledProviderIds = next;
    if (_defaultProviderId != null && !next.contains(_defaultProviderId)) {
      _defaultProviderId = null;
    }
    notifyListeners();
  }

  String? get defaultProviderId => _defaultProviderId;

  set defaultProviderId(String? value) {
    if (value != null && providerById(value) == null) {
      return;
    }
    if (_defaultProviderId == value) {
      return;
    }
    _defaultProviderId = value;
    notifyListeners();
  }

  ThemeMode get themeMode => _themeMode;

  set themeMode(ThemeMode value) {
    if (_themeMode == value) {
      return;
    }
    _themeMode = value;
    notifyListeners();
  }

  Color get accent => _accent;

  set accent(Color value) {
    if (_accent.toARGB32() == value.toARGB32()) {
      return;
    }
    _accent = value;
    notifyListeners();
  }

  bool get compactCards => _compactCards;

  set compactCards(bool value) {
    if (_compactCards == value) {
      return;
    }
    _compactCards = value;
    notifyListeners();
  }

  /// Whether [id] is switched on. Unknown ids are never enabled.
  bool isProviderEnabled(String id) => _enabledProviderIds.contains(id);

  /// Turns [id] on (appended to the order) or off.
  ///
  /// An unknown id is ignored, so a stale id from an older build cannot be
  /// added back.
  void setProviderEnabled(String id, bool enabled) {
    if (providerById(id) == null) {
      return;
    }
    if (_enabledProviderIds.contains(id) == enabled) {
      return;
    }
    final next = List<String>.from(_enabledProviderIds);
    if (enabled) {
      next.add(id);
    } else {
      next.remove(id);
    }
    enabledProviderIds = next;
  }

  /// Replaces the enabled list and its order, dropping unknown and duplicate
  /// ids.
  void setEnabledProviderIds(List<String> ids) {
    enabledProviderIds = ids;
  }

  /// Reads settings, replacing junk/missing values with defaults.
  ///
  /// A first run (no stored provider list) enables every provider in PC order.
  static Future<AppSettings> load() async {
    final prefs = await SharedPreferences.getInstance();

    // Read through get() and type-check, so a value stored with the wrong type
    // falls back to its default instead of throwing a cast error.
    final rawIds = prefs.get(keyEnabledProviderIds);
    final storedIds = rawIds is List
        ? rawIds.whereType<String>().toList(growable: false)
        : null;
    final enabled = storedIds ?? defaultProviderIds;

    final rawDefault = prefs.get(keyDefaultProviderId);
    final rawTheme = prefs.get(keyThemeMode);
    final rawAccent = prefs.get(keyAccentColor);
    final rawKey = prefs.get(keyTmdbApiKey);
    final rawCompact = prefs.get(keyCompactCards);
    final rawMal = prefs.get(keyMalClientId);
    final rawAuto = prefs.get(keyAutoProvider);
    final rawScale = prefs.get(keyUiScale);

    final themeText = rawTheme is String ? rawTheme : null;
    final shortKey = rawKey is String ? rawKey : '';

    return AppSettings(
      tmdbApiKey: shortKey,
      malClientId: rawMal is String ? rawMal : '',
      enabledProviderIds: enabled,
      defaultProviderId: rawDefault is String ? rawDefault : null,
      themeMode: parseThemeMode(themeText),
      accent: rawAccent is int ? Color(rawAccent) : const Color(0xFF8557EA),
      compactCards: rawCompact is bool ? rawCompact : false,
      autoProvider: rawAuto is bool ? rawAuto : true,
      uiScale: rawScale is num ? rawScale.toDouble() : 1.0,
    );
  }

  /// Persists every setting. Call after changing a field.
  Future<void> save() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(keyTmdbApiKey, _tmdbApiKey);
    await prefs.setString(keyMalClientId, _malClientId);
    await prefs.setStringList(keyEnabledProviderIds, _enabledProviderIds);
    await prefs.setString(keyDefaultProviderId, _defaultProviderId ?? '');
    await prefs.setString(keyThemeMode, themeModeName(_themeMode));
    await prefs.setInt(keyAccentColor, _accent.toARGB32());
    await prefs.setBool(keyCompactCards, _compactCards);
    await prefs.setBool(keyAutoProvider, _autoProvider);
    await prefs.setDouble(keyUiScale, _uiScale);
  }

  /// `system` for anything unrecognised, as `lgl/theme.py` does.
  static ThemeMode parseThemeMode(String? raw) {
    switch (raw) {
      case 'light':
        return ThemeMode.light;
      case 'dark':
        return ThemeMode.dark;
      case 'system':
      default:
        return ThemeMode.system;
    }
  }

  static String themeModeName(ThemeMode mode) {
    switch (mode) {
      case ThemeMode.light:
        return 'light';
      case ThemeMode.dark:
        return 'dark';
      case ThemeMode.system:
        return 'system';
    }
  }

  /// Keeps the ids this build knows, in order, without duplicates.
  static List<String> _sanitize(List<String> ids) {
    final known = <String>{for (final provider in allProviders) provider.id};
    final seen = <String>{};
    final out = <String>[];
    for (final id in ids) {
      if (known.contains(id) && seen.add(id)) {
        out.add(id);
      }
    }
    return out;
  }

  static bool _sameIds(List<String> a, List<String> b) {
    if (a.length != b.length) {
      return false;
    }
    for (var i = 0; i < a.length; i++) {
      if (a[i] != b[i]) {
        return false;
      }
    }
    return true;
  }
}
