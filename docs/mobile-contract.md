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
  (1) Anime (item.kind == anime, or identity is verified anime with an anilistId): provider 'ani' (anipm.pk) first if enabled, then the user's order. (2) Otherwise, if autoProvider and health data exists: the fastest reachable enabled provider that supports the kind first, then the user's order. (3) Otherwise defaultProviderId, then the enabled order. Never include disabled providers or ones that cannot play the kind. If it is not anime, do nothing special.
Search: uses TMDB only (`/search/multi`); no AniList results are mixed in, since the anime shelves and a title's detail page are where AniList entries live. TMDB search keeps include_adult=true like the desktop (lgl/tmdb.py ~line 253) and sets MediaItem.adult from TMDB's own flag; the UI marks those cards "18+".

## Contract v3 - player and settings

Player (lib/ui/player/player_page.dart, C): PlayerPage({required Uri url, required String title, MediaItem? item, List<PlaybackTarget> targets = const [], int initialIndex = 0, int? season, int? episode, ProviderHealthService? health}) -- no top bar and no overlay over the page. On open: lock landscape (landscapeLeft, landscapeRight) and immersive sticky before the first frame (SystemChrome before the page loads), black cover + spinner while the page loads, one WebView that is never rebuilt or reloaded when the panel opens. On dispose: restore portraitUp and edgeToEdge. Only chrome: a LEFT-edge handle ~28x96 dp (opacity 0.6, fades to 0.28 after 3 s, stays tappable) plus a ~24 dp edge strip; both open a panel of ~280 dp with back (opens the title's details page when the caller supplied one), title, season/episode, current provider with health dot and ms, "Next provider", the provider list and Reload. Tapping outside closes the panel; system back closes the panel first, then leaves the page. Tap, never swipe. Keys: handleKey, edgeStripKey.
lib/ui/player/player_panel.dart (C): PlayerPanel({required title, subtitle, targets, selectedIndex, onSelect, onReload, onBack, health}).
lib/nav.dart (C): openPlayer(BuildContext, MediaItem, {season = 1, episode = 1}) -- builds the list with playerTargets(context, plan, settings), the one place it computes the order; that reads the app-level ProviderHealthService, so a check run in Settings decides the provider it opens. first = the provider opened. planPlayback(MediaItem, AppSettings, {IdentityLookup? lookup}) -> PlaybackPlan{item, identity}; playbackTargets(PlaybackPlan, AppSettings, {ProviderHealthService? health}); pickProvider(AppSettings, MediaItem, {ProviderHealthService? health}) is the single-target helper. Identity lookup only when the item is not anime, has no anilistId and a malClientId is set; a "Checking..." hint while it runs; unverified / notAnime / unavailable / no Client ID -> play normally, never block or hide.
Settings (lib/ui/settings/settings_page.dart, C): TMDB API key row; MyAnimeList section with the Client ID field (obscured, paste, "Test" via MalClient.testConnection, help link https://myanimelist.net/apiconfig), autoProvider switch, "Check providers" (ProviderHealthService.checkAll(force: true), dot + word + ms from describe()) and "Use fastest". General section: UI size slider 80-130 % in 5 % steps with reset to 100 % and a live preview; About. Keys: tmdbApiKeyField, malClientIdField.
lib/ui/settings/provider_health_view.dart (C): HealthDot(ProviderHealth?) + healthStatusColor(HealthStatus).
## Contract v4 (additive) — season posters, episode previews, episode calendar

Desktop source of truth: lgl/tmdb.py (`Season.poster` / `season_poster_url`, `Episode.still_url`/`overview` and `/tv/{id}/season/{n}`), lgl/qt_season_picker.py (season artwork), lgl/qt_media.py `EpisodeRow` (preview still, title, description), lgl/episode_calendar.py (search, estimation and wording rules).

lib/models/media_item.dart (A): SeasonInfo gains `String? posterUrl` (TMDB `poster_path` at w342; null when absent, always null for AniList). New `EpisodeInfo { required int number, String? name, String? overview, String? stillUrl, String? airDate, int? runtimeMinutes }`.
lib/data/media_repository.dart (A): abstract MediaRepository gains `Future<List<EpisodeInfo>> episodes(MediaItem item, SeasonInfo season)`. TMDB uses `/tv/{id}/season/{n}` (still at w300); anime uses AniList `streamingEpisodes { title thumbnail }` (number read from the title, else the first unused number) and needs no TMDB key. A failure is the caller's to absorb: MediaDetailPage falls back to the plain numbered list from `season.episodeCount`, so playback is never lost to missing art. Parsers (public, fixture-testable): `parseTmdbEpisodes(Map)`, `parseAniListEpisodes(Map)`.
lib/data/episode_calendar.dart (A): `EpisodeCalendarService({required AppSettings settings, http.Client? client, tmdbBaseUrl, anilistEndpoint})` with `Future<EpisodeCalendarResult> search(String query, {int days = 90, DateTime? now})` and `dispose()`. Models: `EpisodeAiring { series, episode?, season?, airAt?, confirmed, source, seriesId, dateOnly }` with `label`, `known`, `estimated`, `status`; `CalendarSeries { title, source, airings }`; `EpisodeCalendarResult { query, groups, errors }`. Public parsers/helpers: `parseAniListAirings`, `parseTmdbSeasonAirings`, `estimateAirDates`, `mergeCalendarSeries`, `episodeLabel`, `airingDateLabel`, `unknownAirDate`. `typedef EpisodeSearch = Future<EpisodeCalendarResult> Function(String query)`.
  Rules (same as lgl/episode_calendar.py): AniList gives exact times (confirmed, no key); TMDB gives a day only (`dateOnly`, confirmed). An undated episode is listed as "Air date unknown" (at most 2 per season) unless at least two confirmed dates share a 5-10 day median cadence with no wild gap; then at most 3 next episodes are projected and each says Estimated. Never guess past the 90-day horizon. Dedupe by (normalised series, season, episode); a TMDB series whose normalised title matches an AniList title is dropped in AniList's favour. Search is capped at 3 series per provider and is separate from MediaSearchPage's TMDB-only search.
lib/ui/calendar/calendar_page.dart (B): `CalendarPage({EpisodeSearch? search})`, opened from the Movies & TV app bar (tooltip "Episode calendar"); field + results grouped by series, with Confirmed/Estimated badges, dates and provider error notes.

## Contract v5 (additive) — playback settings on the phone

Desktop source of truth: lgl/qt_settings.py (the two provider modes, "Default audio", "Autoplay"), lgl/streaming.py (`LANGUAGES['ani']`, `LANGUAGE_LABELS`, `DEFAULT_LANGUAGE`, `clean_language`), lgl/provider_health.py (`AUTO_KEY`).

AppSettings (lib/data/settings_store.dart), additive, persisted snake_case, defaults in []:
  String defaultAudio ['sub'] key default_audio -- one of `sub`, `dub`, `vostfr` (`audioLanguages`); anything else reads back as `sub` (`cleanAudioLanguage`).
  bool autoplay [true] key media_autoplay -- the desktop's own setting name, so the two apps mean the same thing.

lib/data/providers_registry.dart (A): `audioLanguages`, `audioLanguageLabels`, `defaultAudioLanguage`, `String cleanAudioLanguage(Object?)`. The `ani` template is `https://anipm.pk/embed/ani/{anilist}/{episode}/{language}`, and `{language}` is a fifth placeholder. `buildWatchUrl(..., String language = defaultAudioLanguage)` substitutes it with `cleanAudioLanguage(language)`; providers without the segment ignore it.
lib/nav.dart (C): every target is built with `language: settings.defaultAudio`, and `PlayerPage` receives `autoplay: settings.autoplay`.
lib/ui/player/player_page.dart (C): `bool autoplay = true`. On Android it sets `setMediaPlaybackRequiresUserGesture(!autoplay)`, so autoplay off leaves the provider's player waiting for a tap instead of starting the video by itself; playback, fullscreen and the provider list are unchanged.
lib/ui/settings/settings_page.dart (C): the two provider modes are one setting -- "Choose the fastest provider automatically" (`autoProvider`) and "I pick the provider myself" (`!autoProvider`), and picking a default provider with the radio switches the automatic choice off, as on the desktop. A Playback section holds "Default audio" (Sub/Dub/VOSTFR, key `defaultAudioField`) and "Autoplay" (key `autoplaySwitch`). Switch keys: `autoProviderSwitch`, `manualProviderSwitch`.

lib/app.dart (C): one Provider<ProviderHealthService> at the root so Settings and the player share one instance; MaterialApp.builder applies uiScale to TEXT and DENSITY only (TextScaler.linear(media.textScaler.scale(1) * scale) and VisualDensity((scale - 1) * 4)). Media widgets (lib/ui/media) already scale poster width, shelf height and gaps from AppSettings.uiScale themselves, so uiScale must not be applied to layout anywhere else: no Transform.scale or MediaQuery size override in the builder. Neither side reads the other's scale.
