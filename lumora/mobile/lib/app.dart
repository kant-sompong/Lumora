import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'data/media_repository.dart';
import 'data/provider_health.dart';
import 'data/settings_store.dart';
import 'theme/lumora_theme.dart';
import 'ui/media/media_home_page.dart';

/// The repository the app runs on: real metadata, or canned data offline.
///
/// Swapping the two lines below is the whole switch.
MediaRepository defaultRepository(AppSettings settings) =>
    TmdbAniListRepository(settings);
// MediaRepository defaultRepository(AppSettings settings) => FakeMediaRepository();

/// Root widget: settings, the shared provider-health cache and the repository
/// above a themed [MaterialApp].
class LumoraApp extends StatelessWidget {
  const LumoraApp({required this.settings, this.repository, super.key});

  final AppSettings settings;
  final MediaRepository? repository;

  @override
  Widget build(BuildContext context) {
    return MultiProvider(
      providers: [
        ChangeNotifierProvider<AppSettings>.value(value: settings),
        Provider<MediaRepository>(
          create: (_) => repository ?? defaultRepository(settings),
        ),
        // One checker for the whole app, so a health check run in Settings is
        // what the player's panel and the automatic choice see.
        Provider<ProviderHealthService>(
          create: (_) => ProviderHealthService(settings),
        ),
      ],
      child: Consumer<AppSettings>(
        builder: (BuildContext context, AppSettings current, Widget? _) {
          // UI size scales text and touch targets together, live.
          final scale = current.uiScale.clamp(
            AppSettings.minUiScale,
            AppSettings.maxUiScale,
          );
          final density = (scale - 1) * 4;
          return MaterialApp(
            title: 'Lumora',
            debugShowCheckedModeBanner: false,
            themeMode: current.themeMode,
            theme: LumoraTheme.light(current.accent).copyWith(
              visualDensity: VisualDensity(
                horizontal: density,
                vertical: density,
              ),
            ),
            darkTheme: LumoraTheme.dark(current.accent).copyWith(
              visualDensity: VisualDensity(
                horizontal: density,
                vertical: density,
              ),
            ),
            builder: (BuildContext context, Widget? child) {
              final media = MediaQuery.of(context);
              return MediaQuery(
                data: media.copyWith(
                  textScaler: TextScaler.linear(
                    media.textScaler.scale(1) * scale,
                  ),
                ),
                child: child ?? const SizedBox.shrink(),
              );
            },
            home: const MediaHomePage(),
          );
        },
      ),
    );
  }
}
