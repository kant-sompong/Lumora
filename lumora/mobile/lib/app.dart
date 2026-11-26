import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'data/media_repository.dart';
import 'data/settings_store.dart';
import 'theme/lumora_theme.dart';
import 'ui/media/media_home_page.dart';

/// The repository the app runs on: real metadata, or canned data offline.
///
/// Swapping the two lines below is the whole switch.
MediaRepository defaultRepository(AppSettings settings) =>
    TmdbAniListRepository(settings);
// MediaRepository defaultRepository(AppSettings settings) => FakeMediaRepository();

/// Root widget: settings and the repository above a themed [MaterialApp].
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
      ],
      child: Consumer<AppSettings>(
        builder: (BuildContext context, AppSettings current, Widget? _) {
          return MaterialApp(
            title: 'Lumora',
            debugShowCheckedModeBanner: false,
            themeMode: current.themeMode,
            theme: LumoraTheme.light(current.accent),
            darkTheme: LumoraTheme.dark(current.accent),
            home: const MediaHomePage(),
          );
        },
      ),
    );
  }
}
