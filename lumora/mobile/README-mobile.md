# Lumora Mobile — Movies & TV

The **Movies & TV** tab of the Lumora desktop app, shrunk to a phone. It is the
same look, the same streaming providers and the same wording; everything else
(games, library, downloads, calendar, stats, collections, profiles, plugins) is
deliberately absent.

- **Media home** — shelves of posters.
- **Search** — results in a three-column grid.
- **Title detail** — poster, year, rating, genres, seasons.
- **Player** — one full-screen WebView on a provider from the PC app.
- **Settings** — exactly two tabs: **General** and **Providers**.

There is no browser UI. The player has a back button, an auto-hiding title
overlay and a "switch provider" button; no address bar, no tabs, no history.

## Requirements

- Flutter **3.35.x** (the version CI pins; `flutter --version` should report
  3.35.7 or newer in that line).
- Android SDK with a device or emulator on API 21+.

## Run

```bash
cd lumora/mobile
flutter pub get
flutter run
```

`flutter run` installs a debug build on the connected device/emulator. A
physical device needs USB debugging on.

## Build a release APK

```bash
cd lumora/mobile
flutter build apk --release
```

The APK lands in `build/app/outputs/flutter-apk/app-release.apk`. It is signed
with the debug key unless a release keystore is configured
(`android/key.properties` plus a `signingConfig` in `android/app/build.gradle.kts`),
so treat it as a test build.

Checks that must pass before handing a build over:

```bash
flutter analyze
flutter test
```

## Android settings the WebView needs

Already applied in `android/app/src/main/AndroidManifest.xml`; listed here so a
merge cannot silently drop them.

- `<uses-permission android:name="android.permission.INTERNET"/>` — metadata and
  the player's embed are fetched over the network. Without it the debug build
  works (the debug manifest adds it) and the **release** build shows empty
  shelves.
- `android:usesCleartextTraffic="false"` — every provider serves HTTPS; plain
  HTTP is refused rather than silently allowed.
- `android:hardwareAccelerated="true"` on the application/activity — required
  for the WebView video surface.
- The activity keeps `orientation` in `configChanges`, so rotating into
  landscape does not recreate the WebView. The player locks nothing: it asks for
  immersive mode, allows landscape, and restores the system bars when it exits.
- Popups: `webview_flutter_android` creates the WebView with
  `setSupportMultipleWindows(true)` and no `onCreateWindow` handler, so a
  `window.open` has nowhere to go. Anything that would load in the same view is
  refused by the navigation delegate, which allows only the provider's own host
  and its subdomains (`lib/ui/player/player_policy.dart`).

## Providers

`lib/data/providers_registry.dart` mirrors `lgl/streaming.py`: the same ids, the
same labels, the same embed URL templates, and the same TMDB-id / AniList-id
split. The defaults (`cinesrc` for films and series, `ani` for anime) come from
that module too.

## Contract with Agent A

Agent C's files do not define the shared pieces; they import them. Everything
below is the shape agent C compiles and tests against, so a change to any of it
has to keep this signature or the player, the settings page and the app wiring
stop building.

### `lib/models/media_item.dart`

| Symbol | Signature assumed here |
| --- | --- |
| `MediaKind` | `enum MediaKind { movie, tv, anime }` |
| `MediaItem` | `MediaItem({required String id, required String title, required MediaKind kind, String? posterUrl, String? backdropUrl, String? year, double? rating, String? overview, List<String> genres = const <String>[], int? tmdbId, int? anilistId})` |

Read by agent C: `item.id`, `item.title`, `item.kind`, `item.tmdbId`,
`item.anilistId`. `SeasonInfo`, `MediaDetail` and `MediaShelf` are shared with
the home, search and detail pages, not used by agent C's own files.

### `lib/models/stream_provider.dart`

| Symbol | Signature assumed here |
| --- | --- |
| `StreamProvider` | `const StreamProvider({required String id, required String name, required String urlTemplate, required Set<MediaKind> kinds})` |

Read by agent C: `p.id`, `p.name`, `p.kinds`, `p.urlTemplate`.

**`urlTemplate` is pipe-separated `"movie | tv | anime"`** — one field carries
up to three embed templates, split on `' | '`, in that order. A provider that
plays a single kind ships one segment, which is its movie route. `{id}`,
`{anilist}`, `{season}` and `{episode}` are the placeholders; a template
containing `{anilist}` is keyed by `MediaItem.anilistId`, and everything else by
`tmdbId`.

### `lib/data/providers_registry.dart`

| Symbol | Signature assumed here |
| --- | --- |
| `allProviders` | `const List<StreamProvider>` — every provider, in `lgl/streaming.py` order |
| `providerById` | `StreamProvider? providerById(String id)` — null for an unknown id |
| `buildWatchUrl` | `Uri buildWatchUrl(StreamProvider p, MediaItem item, {int season = 1, int episode = 1})` — throws `ArgumentError` when the provider cannot play that kind, or the title lacks the id its route needs |

`enabledProviders`, `pickProvider`, `playbackTargets` and `watchUrlFor` in
`lib/nav.dart` call only these three, so an id left in stored settings by an
older build is skipped instead of crashing.

### `lib/data/settings_store.dart`

**`AppSettings` persists through `shared_preferences`**, and `load()` / `save()`
round-trip the fields below under the snake_case keys the current stub uses
(`tmdb_api_key`, `enabled_provider_ids`, `default_provider_id`, `theme_mode`,
`accent_color`, `compact_cards`). If it moves to a file or another store, the
`AppSettings.load()` assertions in the settings tests need rewriting.

| Symbol | Signature assumed here |
| --- | --- |
| `AppSettings` | `AppSettings({String tmdbApiKey = '', List<String>? enabledProviderIds, String? defaultProviderId, ThemeMode themeMode = ThemeMode.system, Color accent = const Color(0xFF8557EA), bool compactCards = false})`, a `ChangeNotifier` |
| `AppSettings.load` | `static Future<AppSettings> load()` |
| `save` | `Future<void> save()` |
| fields | `String tmdbApiKey` (getter and setter), `String? defaultProviderId`, `ThemeMode themeMode`, `Color accent`, `bool compactCards` |
| provider access | `List<String> get enabledProviderIds`, `bool isProviderEnabled(String id)`, `void setProviderEnabled(String id, bool enabled)`, `void setEnabledProviderIds(List<String> ids)` |

`accent` is compared with `toARGB32()`, so it has to be a real colour a user
picked, not a seed derived from the theme mode.

### `lib/data/media_repository.dart`

| Symbol | Signature assumed here |
| --- | --- |
| `MediaRepository` | `abstract class` with `Future<List<MediaShelf>> home()`, `Future<List<MediaItem>> search(String query)`, `Future<MediaDetail> detail(MediaItem item)` |
| `TmdbAniListRepository` | `TmdbAniListRepository(AppSettings settings, {FakeMediaRepository? fallback})` — what `defaultRepository` builds |
| `FakeMediaRepository` | `FakeMediaRepository()` — offline canned data, used by `test/nav_test.dart` |

### `lib/theme/lumora_theme.dart`

| Symbol | Signature assumed here |
| --- | --- |
| `LumoraSpace` | `xs = 4, sm = 8, md = 16, lg = 24, xl = 32, xxl = 48`, all `double` |
| `LumoraTheme.dark` | `static ThemeData dark(Color accent)` |
| `LumoraTheme.light` | `static ThemeData light(Color accent)` |

Both theme builders take the accent from settings, so picking an accent
repaints the whole app. `LumoraRadius` is not used by agent C's files.

## Layout

```
lib/
  main.dart              loads settings, runs the app
  app.dart               MultiProvider + MaterialApp (theme from settings)
  nav.dart               provider choice, watch URLs, player and settings routes
  theme/                 LumoraTheme + spacing/radius tokens (agent A)
  models/                MediaItem, StreamProvider (agent A)
  data/                  providers_registry, media_repository, settings_store (A)
  ui/media/              home, search, detail (agent B)
  ui/player/             one WebView, its overlay and its navigation policy
  ui/settings/           the two-tab settings page
test/                    unit and widget tests for the above
```

## CI

`.github/workflows/mobile.yml` runs `flutter analyze`, `flutter test` and
`flutter build apk --release` for anything that touches `lumora/mobile/`, and
uploads the APK as a build artifact.
