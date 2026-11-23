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
