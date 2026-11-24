# Lumora mobile — shared contract (v2)

The only spec for the mobile app's shared modules. Agent ownership: **A** owns
`lib/models`, `lib/data`, `lib/theme`; **C** owns `lib/nav.dart`,
`lib/ui/player`, `lib/ui/settings`; **B** owns `lib/ui/media`. Do not rely on
memory of the chat: this file is the authority.

--- CONTRACT (v2) ---
lib/theme/lumora_theme.dart (A): class LumoraTheme { static ThemeData dark(Color accent); static ThemeData light(Color accent); }  class LumoraSpace { static const double xs, sm, md, lg, xl, xxl; }  class LumoraRadius { static const double sm, md, lg; }
lib/models/media_item.dart (A): enum MediaKind { movie, tv, anime }; MediaItem { id, title, kind required; posterUrl, backdropUrl, year, rating, overview nullable; genres non-null default const []; tmdbId, anilistId nullable }; SeasonInfo(number, name?, episodeCount=0); MediaDetail(item, seasons=[], runtimeMinutes?, tagline?); MediaShelf(title, items=[])
lib/models/stream_provider.dart (A): StreamProvider(id, name, urlTemplate, kinds: Set<MediaKind>)
lib/data/providers_registry.dart (A): const List<StreamProvider> allProviders; StreamProvider? providerById(String id); Uri buildWatchUrl(StreamProvider p, MediaItem item, {int season = 1, int episode = 1}) -- throws ArgumentError if the item lacks the id the route needs
lib/data/media_repository.dart (A): abstract MediaRepository { home(); search(String); detail(MediaItem) }; FakeMediaRepository(); TmdbAniListRepository(AppSettings) throws MissingApiKeyException / NetworkException
lib/data/settings_store.dart (A): AppSettings extends ChangeNotifier { tmdbApiKey, enabledProviderIds (ordered), defaultProviderId, themeMode, accent, compactCards; isProviderEnabled(String); setProviderEnabled(String, bool); setEnabledProviderIds(List<String>); static Future<AppSettings> load(); Future<void> save(); }
lib/nav.dart (C): openPlayer(BuildContext, MediaItem, {season=1, episode=1}); openSettings(BuildContext)
lib/ui/media/ (B): MediaHomePage(), MediaSearchPage(), MediaDetailPage({required MediaItem item})
lib/ui/settings/ (C): SettingsPage()   lib/ui/player/ (C): PlayerPage({required Uri url, required String title})
--- END ---
