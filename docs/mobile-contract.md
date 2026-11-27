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

## Contract v3 (additive)

Desktop source of truth: lgl/mal.py, lgl/anime_identity.py, lgl/qt_anime_routing.py, lgl/provider_health.py, lgl/tmdb.py (adult flag, /images logos, _best_logo), lgl/qt_settings.py (auto provider), lgl/qt_media.py ("Use fastest"). Port faithfully; do not invent rules.

AppSettings (lib/data/settings_store.dart), additive, persisted snake_case, defaults in []:
  String malClientId [''] key mal_client_id (trimmed; never logged or shown in clear)
  bool autoProvider [true] key auto_provider
  double uiScale [1.0] key ui_scale, clamped 0.8-1.3
MediaItem: add bool adult [false]; add copyWith({kind, anilistId, adult, ...}). MediaDetail: add String? logoUrl.

lib/data/mal_client.dart: MalClient({required String clientId, http.Client? client}); Future<String> testConnection(); Future<List<MalAnime>> search(String q, {int limit = 24}); Future<MalAnime> details(int malId). Exceptions: MalException (retryable flag), MissingMalClientIdException. The Client ID goes ONLY in the X-MAL-CLIENT-ID header, never in a URL, a log or an error message.
lib/data/anime_identity.dart: enum IdentityStatus { anime, notAnime, unverified }; AnimeIdentity { status, malId?, anilistId?, reason }; AnimeIdentityService(AppSettings, {MalClient?, http.Client?}) { Future<AnimeIdentity> identifyTmdb(MediaItem item); }
  Desktop rules: MAL is the only proof of "anime". Match by EXACT title (primary or alternative) + year + media type; no fuzzy scoring; two matches = ambiguous. Unmatched = UNVERIFIED (not "not anime", never hidden). Resolve the AniList id separately: AniList Media(idMal:) link first, else a crosswalk candidate validated against idMal; never pass a MAL id where an AniList id is needed. MAL throttled to ~1 request/s; 403 or no answer = lookup unavailable. Cache in shared_preferences: verified 7 days, others 1 day. User-Agent "Lumora (personal mobile client)".
lib/data/provider_health.dart: enum HealthStatus { fast, ok, slow, unreachable, timedOut }; ProviderHealth { providerId, status, ms?, httpStatus?, checkedAt }; ProviderHealthService(AppSettings, {http.Client?}) { Future<List<ProviderHealth>> checkAll({bool force = false}); ProviderHealth? cached(String id); String? fastest(Iterable<String> ids); String describe(ProviderHealth h); }
  Desktop rules: one HEAD request per attempt to the provider's BASE host only (never a player or video), 3 attempts, median time to first byte, 5 s timeout, at most 4 at once, cache 10 minutes. fast <300 ms, ok 300-999, slow >=1000; an HTTP error is a response, so still reachable.
lib/data/provider_choice.dart: List<String> orderedProviders({required MediaItem item, required AppSettings settings, ProviderHealthService? health, AnimeIdentity? identity}) -> provider ids in order to try; first = default.
  (1) Anime (item.kind == anime, or identity is verified anime with an anilistId): provider 'ani' (ani.pm) first if enabled, then the user's order. (2) Otherwise, if autoProvider and health data exists: the fastest reachable enabled provider that supports the kind first, then the user's order. (3) Otherwise defaultProviderId, then the enabled order. Never include disabled providers or ones that cannot play the kind. If it is not anime, do nothing special.
Search: TMDB search keeps include_adult=true like the desktop (lgl/tmdb.py ~line 253) and sets MediaItem.adult from TMDB's own flag; the UI marks those cards "18+".
