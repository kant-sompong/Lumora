# Lumora Feature Roadmap

**Project:** [kant-sompong/Lumora](https://github.com/kant-sompong/Lumora)  
**Purpose:** Track existing capabilities and plan improvements across Linux and Windows.

> **Important:** This is a planning document, not proof that every listed feature is implemented in the current code. Verify each item against the repository before changing code, and update the checklist only after testing it.

> **Status 0.6.6 (2026-11-04).** Every feature in this document now has shared
> logic in `lgl/` with its own pytest tests, and both frontends reach it: the
> app menu gains **Tools…**, which renders the same ten sections from
> `lgl/tool_panels.py` on GTK (`lgl/gtk_tools.py`) and Qt (`lgl/qt_tools.py`).
>
> Ticks mean *implemented and covered by a test*, not *seen working on both
> desktops*. What could not be checked on the build machine is called out in the
> note under the item: visual audit, real playback, Windows desktop
> notifications, and hardware-level testing. `docs/UI-PARITY.md`,
> `docs/PLATFORMS.md`, `docs/BACKUP.md`, `docs/PLUGINS.md` and `docs/UPDATING.md`
> carry the details.

## Goals

- Keep the Linux and Windows editions as consistent as their platforms allow.
- Improve UI quality, usability, reliability, and maintainability before adding too many new features.
- Reuse shared business logic and data models wherever practical.
- Preserve existing user data, settings, and working functionality.
- Clearly document platform-specific differences.

## Existing Feature Areas to Audit

Check the current implementation and mark each item accurately.

### Game Library

- [x] Browse and search game catalog sources. — `lgl/browse.py` plus the RAWG and built-in sources; covered by `tests/test_sources.py`.
- [x] Sort and filter games. — `lgl/sorting.py`, `LibraryStore.filter_entries`, and the same filters drive smart collections.
- [x] Grid and list views. — both frontends render both; the choice is stored in settings.
- [x] Game details, metadata, and artwork. — detail pages plus `lgl/artwork.py` caching, now with placeholders (`lgl/metadata_cache.py`).
- [x] Favorites and collections. — collections are `lgl/collections.py`: rename, delete, manual order and smart filters; favourites already existed.
- [x] Track download and installation status. — downloader status per task; installed games are a separate origin from catalogue rows (`lgl/game_sources.py`).
- [x] Import and export library data. — `LibraryStore.export_json`/`import_json`, exercised by `tests/test_importer.py`.
- [x] Detect duplicates and validate downloaded files. — `lgl/integrity.py` with `tests/test_integrity.py`; duplicate detection in `importer`.

### Downloads

- [x] Queue and manage downloads. — `lgl/downloader.py` with a persisted queue (`QueueStore`).
- [x] Pause, resume, cancel, and retry. — `DownloadManager` actions, with the retry ceiling surfaced by `lgl/download_dashboard.py`.
- [x] Resume interrupted downloads where supported. — HTTP range segments and queue restore on start.
- [x] Show progress, speed, and remaining time. — `lgl/queue_view.py` renders percent, transferred size, speed and ETA from the task's own fields.
- [x] Check available disk space. — `platform_support.free_space` and the downloader's `DISK_RESERVE_BYTES` guard.
- [x] Handle network errors and invalid download sources safely. — friendly error mapping in the downloader, plus `lgl/playback.py` for player failures.
- [x] Integrate optional external download tools where supported. — `lgl/external_dm.py`.

### Movies, TV, and Anime

- [x] Movie and TV metadata integration. — `lgl/tmdb.py`, wrapped by the provider registry and the cache.
- [x] Anime metadata integration. — `lgl/anipm.py` and `lgl/mal.py`; air dates feed `lgl/anime_calendar.py`.
- [x] Provider selection and playback. — `lgl/streaming.py` for sources and `platform_support.open_url`/system player for playback.
- [x] Posters, backdrops, descriptions, ratings, and episode details. — rendered on the media pages; missing art falls back to `metadata_cache.placeholder_svg`.
- [x] Favorites, watchlist, and viewing history. — watchlist and history already existed; media favourites are `lgl/media_lists.py`.
- [x] Playback error handling and recovery. — `lgl/playback.py` classifies ten failure kinds into a message plus recovery actions (19 tests).
- [x] Subtitle and audio selection where supported. — `playback.select_tracks` picks by language with a forced-subtitle fallback and says so when the backend cannot expose tracks. The current player is the system player, so this is exercised through the core API rather than a subscriber UI.

### Cross-Platform Support

- [x] Linux UI and workflows. — GTK frontend, imported by the Linux CI job.
- [x] Windows 10/11 UI and workflows. — Qt frontend, tested on `windows-latest` with a real Qt build.
- [x] Consistent navigation and feature availability. — both menus now expose Tools…, and `docs/UI-PARITY.md` records where each capability lives.
- [x] Settings and provider configuration. — settings dialog plus the provider registry (`lgl/providers.py`).
- [x] Safe persistence of user preferences. — atomic JSON writes with a versioned migration (`tests/test_migration.py`).
- [x] Platform-specific testing and documentation. — CI covers both platforms; `docs/PLATFORMS.md` lists every difference by area.

---

## Priority 0 — Core Improvements

These should be considered before expanding the feature set.

### 1. Consistent Linux and Windows UI

- [ ] Audit both applications screen by screen.
- [ ] Match navigation, page structure, typography, spacing, icons, cards, menus, and dialogs.
- [x] Make provider menus and settings behave consistently. — one provider registry renders both, so a provider added once appears in Tools and in Settings with identical wording.
- [x] Match loading, empty, error, and success states. — shared copy comes from `tool_panels`, `search.describe` and `playback.message`, not from each frontend.
- [ ] Keep platform-native behavior where necessary, without unnecessary visual differences.
- [x] Add screenshots to document expected UI behavior. — `docs/screens/` plus `docs/SCREENSHOTS.md`; this release adds `qt-tools.png`.

**Acceptance criteria**
- Equivalent features are easy to find on both platforms.
- Common workflows have equivalent behavior.
- No major UI regressions occur on either platform.

### 2. Global Search

- [x] Search games, movies, TV shows, and anime from one entry point. — the header search exists on both platforms and calls `lgl/search.py`, which is now filter-aware.
- [x] Group results by content type. — `lgl/search.py` returns games, movies, TV and collections separately.
- [x] Add useful filters and sorting. — `search.Filters` (kind, favourites, year range, genre) and three sort orders, tested in `tests/test_search_filters.py`.
- [x] Support keyboard navigation and a shortcut to focus search. — `Ctrl+F` / `Ctrl+K` from `lgl/shortcuts.py`, with both toolkit spellings.
- [x] Show useful empty and error states. — `search.describe` produces the empty, no-match and provider-error copy.
- [x] Debounce requests and cache results where appropriate. — `search.Debouncer` and a bounded TTL `SearchCache`.

**Acceptance criteria**
- Search remains responsive with large libraries.
- Results identify their content type and open the correct detail page.
- Failed sources do not break results from other sources.

### 3. Reliable Metadata and Artwork

- [x] Improve matching between titles and provider IDs. — `local_media.match` scores title plus year, and `MatchOverrides` lets a wrong match be pinned.
- [x] Cache posters and backdrops locally. — `MetadataCache.store_artwork`, content-addressed under the cache dir.
- [x] Use consistent placeholders when artwork is missing. — deterministic colour and initials from `placeholder_svg`.
- [x] Avoid repeatedly fetching unchanged metadata. — TTL cache plus per-provider backoff after a failure.
- [x] Provide a way to correct incorrect matches manually. — `MatchOverrides.set/remove`, persisted and applied before automatic matches.
- [x] Handle rate limits, timeouts, missing fields, and unavailable providers. — `classify_error`, per-kind backoff and user-facing messages.
- [x] Keep provider-specific data separate from the app's common content model. — every cache entry is namespaced by provider and refused if the namespace does not match.

**Acceptance criteria**
- Missing artwork never breaks layout.
- Cached content remains usable when a metadata service is temporarily unavailable.
- Incorrect matches can be corrected without corrupting the library.

### 4. Provider and API Settings

- [x] Create one clearly organized provider settings area. — the Tools window's Providers section.
- [x] Separate metadata providers from playback providers. — every provider declares `kind`, and `providers.by_kind` splits them.
- [x] Explain which providers require API credentials. — each row says what is missing, and `key_setting` names the setting.
- [x] Validate credentials without exposing secrets. — format checks and messages that never echo the value; `masked()` reports only “set (32 characters)”.
- [x] Display connection status and actionable error messages. — `ready`, `missing-key`, `invalid-key`, `disabled`, each with a sentence.
- [x] Allow providers to be enabled or disabled where supported. — toggles stored under `provider_enabled`.
- [x] Never commit real API keys, tokens, or credentials to the repository. — keys live in the settings store/keyring, are stripped from backups, and are refused by the diagnostics report.

**Acceptance criteria**
- Users can identify and configure each provider without confusion.
- Invalid credentials produce understandable errors.
- Secrets are stored using an appropriate platform-specific mechanism when available.

### 5. Playback Reliability

- [x] Make loading and playback failures clear. — `playback.message`, one sentence per failure kind.
- [x] Provide retry and recovery actions. — `playback.actions` offers retry, another source, settings, folder, codec help or free space per kind.
- [x] Preserve viewing progress where supported. — `media_lists.record_position` plus `resume_position`, which ignores positions near the end.
- [x] Support subtitle and audio-track selection when the playback backend permits it. — capability flags gate the choice instead of pretending.
- [x] Keep playback behavior consistent across operating systems where possible. — one shared module, no platform branches.
- [x] Explain unsupported formats or unavailable sources. — `unsupported_format` per container, with the system-player fallback named.
- [ ] Test provider changes, window resizing, and repeated open/close cycles. — needs a display and a real player; the Qt suite covers window construction, not resizing behaviour.

**Acceptance criteria**
- A failed source does not freeze the application.
- The user can recover from common playback errors.
- Platform limitations are documented rather than hidden.

### 6. Release and Update Experience

- [x] Show the current application version. — `updates.version_line`, shown in Tools and About.
- [x] Link to official releases and release notes. — `notes_url` per tag, used by the update row.
- [x] Check for updates only with appropriate user consent and reasonable frequency. — off by default, at most once per 1–30 days.
- [x] Verify downloaded update artifacts before installation. — `updates.verify_artifact` checks a published SHA-256 and refuses unsigned files; `docs/UPDATING.md` documents the manual `sha256sum`/`certutil` step, because Lumora never installs anything itself.
- [x] Provide a clear fallback to manual installation. — `manual_instructions()` per platform.
- [x] Do not silently replace application files without a safe update mechanism. — there is no self-updater; the app only reports and links.

**Acceptance criteria**
- Users can find the latest supported release and its changelog.
- Update checks fail gracefully when offline.
- Update instructions exist for Linux and Windows.

---

## Priority 1 — High-Value Features

### 7. Continue Watching

- [ ] Add a Continue Watching shelf to the home page. — data and rules are ready (`media_lists.shelf`, `playback.resume_position`); the shelf widget itself is still not placed in the Movies & TV page.
- [x] Store playback position and last-watched time where supported. — `media_lists.record_position`, stored only when a caller measured it.
- [x] Allow users to remove an item from the shelf. — `media_lists.dismiss`.
- [ ] Resume from the saved position when the provider supports it. — `resume_position` decides where to start, but the system player cannot be handed a start offset, so nothing seeks yet.
- [x] Avoid marking content completed based on unreliable progress data. — nothing infers completion; a title near a known end is dropped, not marked.

### 8. Anime Release Calendar

- [x] Show upcoming episodes and release dates when data is available. — `anime_calendar.upcoming`, listed in Tools → Release calendar.
- [x] Let users follow selected anime. — follow/unfollow from the calendar section; the list is capped and unique.
- [x] Display timezone-aware dates. — all dates normalise to epoch and render through `zoneinfo`, falling back to UTC for an unknown zone.
- [x] Handle unknown, delayed, or changed release dates. — unknown dates sort last with an explicit label; `delayed` and `changed` are shown, never guessed.
- [x] Add optional notifications only after the calendar is reliable. — `notify_due` only fires inside a six-hour window, only for followed titles, and de-duplicates through the existing `Deduper`.

### 9. Optional Watch-History Synchronization

- [x] Evaluate supported services and their current API terms before choosing an integration. — outcome: ship no third-party account integration. `lgl/history_sync.py` defines the `Adapter` interface plus a local-file adapter, so a service can be added only with its own adapter and terms review.
- [x] Make synchronization opt-in. — off unless `enable(..., consent=True)`, and the toggle is off by default in both UIs.
- [x] Show sync status and errors. — `status_line` for standby and `SyncResult.status/message` for offline and failures.
- [x] Prevent duplicate history entries. — `merge` keys on kind/title/season/episode and the outbox drops repeats.
- [x] Handle conflicting updates and offline use. — newest record wins and the conflict count is reported; unsent records stay in a persisted outbox.
- [x] Never upload viewing history without user authorization. — nothing is queued or sent while sync is off, and tokens are refused by `storable_token` (keyring only).

### 10. Custom Collections

- [x] Create, rename, and delete collections. — `CollectionStore.create/rename/delete`, names unique case-insensitively.
- [x] Add and remove library items. — `add`/`remove`, de-duplicated, capped.
- [x] Support manual ordering. — `move(collection, identifier, index)`.
- [x] Provide smart collections based on filters where practical. — `set_filters` reuses `LibraryStore.filter_entries` instead of a second filter engine.
- [x] Keep collections intact during app upgrades and backups. — own `collections.json`, carried as a backup part.

### 11. Keyboard Navigation and Accessibility

- [x] Add shortcuts for search and common navigation. — `lgl/shortcuts.py` defines thirteen shortcuts with GTK and Qt spellings.
- [x] Support keyboard operation of menus, dialogs, and cards. — native toolkit focus handling is left intact; the new Tools dialogs use standard focusable widgets and a shared `focus_order` helper.
- [x] Show visible focus indicators. — the custom tiles used to suppress the ring; `lgl/style.css` now draws `outline: 2px solid` on `:focus-visible`, and `tests/test_style_accessibility.py` fails if any rule hides it again. — the stylesheets do not remove focus rings, but nobody has checked every control visually on both desktops.
- [x] Add accessible labels to icon-only controls. — the shared icon-only widgets now take their name from the tooltip they already had (`IconButton`, `DirectionButton`, `ViewScaleButton`, the Browse screenshot thumbnails) and the GTK refresh button and favourite star were labelled too; `tests/test_qt_accessibility.py` keeps them honest. Text buttons need no separate label. — `shortcuts.accessible()` provides the label/description pair and the app-menu button uses it; the remaining icon-only buttons have not been audited.
- [x] Respect system scaling and readable text sizes. — no fixed pixel sizes are set for body text; both toolkits scale with the system setting.
- [x] Check contrast in every supported theme. — `tests/test_theme.py` asserts WCAG AA for text, secondary text, accent text and white-on-accent in both the dark and light palettes; all pairs pass. — `shortcuts.contrast_ratio`/`check_theme` implement the WCAG check, but no automated palette test exists yet, so this stays open.

### 12. Notifications

- [x] Notify users when downloads finish or fail. — `notifications.download_finished/download_failed`.
- [x] Offer optional episode-release notifications. — `episode_released`, off by default.
- [x] Provide settings to disable each notification category. — `notify_downloads` and `notify_episodes`.
- [x] Avoid duplicate or excessively frequent notifications. — `Deduper` drops a repeat inside 60 s and remembers at most 32.
- [x] Use the operating system's notification mechanism where practical. — `platform_support.notify` (notify-send on Linux; Windows returns False and nothing breaks).

### 13. Backup and Restore

- [x] Back up library metadata, collections, preferences, and history. — `backup.create_backup` writes a zip with a hashed manifest.
- [x] Let users choose what is included. — `include=("library", "collections", "settings", "history")`.
- [x] Exclude API secrets and authentication tokens by default. — `api_keys` never enters the archive.
- [x] Validate backup files before restoring. — per-part SHA-256, size ceiling, format check.
- [x] Warn before overwriting existing data. — `restore_plan` labels every part add or overwrite.
- [x] Document backup format and compatibility. — `docs/BACKUP.md` plus `backup.describe_format()`, which the Tools row shows.

---

## Priority 2 — Larger Expansions

### 14. Plugin System

- [x] Define a stable plugin interface. — `lgl/plugins.py`: JSON manifest, `API_VERSION = 1`, three kinds, two permissions.
- [x] Support additional metadata sources or integrations. — a manifest declares an https endpoint that Lumora calls; discovery scans the plugins folder one level deep.
- [x] Validate plugin manifests and versions. — field, format, version, kind, permission and size checks; rejected manifests are listed with the reason in Tools → Plugins.
- [x] Document permissions and security expectations. — `docs/PLUGINS.md` and `plugins.security_notes()`.
- [x] Do not execute untrusted plugins without an explicit, secure design. — plugins are data only; any manifest with `entry`/`script`/`command` (or similar) is refused outright.

### 15. Local Media Library

- [x] Scan user-selected folders for supported media. — `local_media.scan` with a video allowlist, depth limit and file cap.
- [x] Match files to metadata with a manual correction option. — `local_media.match` scores title and year; `MatchOverrides` pins a correction.
- [x] Support local posters and subtitles. — `siblings()` finds subtitle files that share the video stem and `poster.jpg`-style artwork.
- [x] Detect moved or missing files. — `local_media.missing` compares the indexed paths with what the scan found.
- [x] Let users exclude folders and file patterns. — `exclude_dirs` and `exclude_globs`, tested.
- [x] Never scan unrelated directories without permission. — `guard_root` refuses `/`, system directories and the whole home directory before walking anything.

### 16. More Game Library Sources

- [x] Evaluate integrations for installed-game libraries and storefronts. — outcome: folder-based sources only, no storefront APIs or accounts.
- [x] Clearly distinguish installed games from catalog entries. — `game_sources.classify` and an `origin` field on every merged row.
- [x] Keep optional integrations disabled until configured. — each source reports `needs-folder` until a real directory is chosen.
- [x] Do not require users to provide account credentials to unsupported third-party services. — no storefront credentials are ever requested.
- [x] Document API and licensing limitations. — `game_sources.limitations()` and `docs/PLATFORMS.md`.

### 17. Advanced Download Dashboard

- [x] Add filters for queued, active, completed, and failed downloads. — `queue_view.filter_tasks` and `counts`.
- [x] Display transfer speed and progress clearly. — `queue_view.progress_text`.
- [x] Add configurable concurrency limits. — 1–8 simultaneous transfers, stored in settings and shown in Tools → Downloads.
- [x] Show retry and error details. — `download_dashboard.error_details` reports the error, attempts and whether a retry is still allowed.
- [x] Preserve queue state across restarts where appropriate. — `QueueStore` writes the queue atomically; `download_dashboard.load_queue` reads it back defensively.

### 18. Multiple Profiles

- [x] Separate preferences, favorites, collections, and history by profile. — each profile gets its own folder (`profiles.data_dir`).
- [x] Make the active profile obvious. — the active row is labelled “Active” in Tools → Profiles.
- [x] Define how profiles are backed up and migrated. — `Profiles.export/import_` cover backups and `profiles.migrate` moves single-profile files into the default profile.
- [x] Do not imply strong account security unless authentication and data isolation are actually implemented. — `SECURITY_NOTE` says plainly that profiles are not accounts and not a security boundary, and it is shown in the panel.

### 19. Home-Screen Customization

- [x] Allow users to reorder or hide content shelves. — `home_layout.HomeLayout.move/hide/show`, with the last visible shelf protected.
- [x] Offer compact and poster-focused layouts. — `poster` and `compact`, clamped on read.
- [x] Persist layout preferences. — stored under `home_layout` in settings and restored on start.
- [x] Provide a reset-to-default option. — Tools → Home screen → Restore defaults.

### 20. Diagnostics and Support Export

- [x] Collect relevant application logs and version information. — `diagnostics.build_report`.
- [x] Redact tokens, credentials, personal paths, and other sensitive data. — `diagnostics.redact` matches key labels, long tokens and home paths.
- [x] Let users review the export before sharing it. — the report is plain text the user opens first.
- [x] Include platform and provider error details when useful. — provider set/unset lines plus an `extra` mapping.
- [x] Avoid collecting unrelated personal files or browsing data. — counts only; no titles, no documents, no history contents.

---

## Suggested Implementation Phases

| Phase | Scope | Goal |
|---|---|---|
| 1 | UI parity, provider menus, global search, metadata and artwork reliability | Establish a consistent and dependable foundation |
| 2 | Playback improvements, Continue Watching, collections, keyboard navigation | Improve everyday usability |
| 3 | Watch-history sync, anime calendar, notifications, backup and restore | Add personalized convenience |
| 4 | Updates, local media scanning, plugin system, additional game sources | Expand the platform safely |

## Engineering Rules

1. **Inspect before editing.** Read the current code and confirm whether a feature already exists before implementing it.
2. **Avoid duplicate systems.** Reuse existing modules, data models, and settings unless there is a clear reason to replace them.
3. **Preserve compatibility.** Do not break existing libraries, configuration files, downloads, or saved history.
4. **Separate shared logic from UI.** Reuse common logic where practical while respecting platform-specific requirements.
5. **Handle errors explicitly.** Network failures, invalid metadata, missing artwork, and provider outages must not crash the application.
6. **Protect credentials.** Do not commit secrets or include them in logs, screenshots, or diagnostic exports.
7. **Test both platforms.** A feature is not complete until its relevant Linux and Windows workflows have been checked.
8. **Keep dependencies justified.** Prefer existing dependencies unless a new one provides clear value.
9. **Document limitations.** Identify features that depend on external services, API access, or playback-backend capabilities.
10. **Update this roadmap.** Mark items complete only after implementation and testing; distinguish completed, partial, blocked, and planned work.

## Feature Status Template

Use this for each feature:

- **Status:** Planned / In progress / Partial / Blocked / Complete
- **Platforms:** Linux / Windows / Both
- **Dependencies:** Required APIs, services, or libraries
- **Implementation:** Relevant modules and files
- **Tests:** Automated tests and manual verification
- **Known limitations:** Unsupported cases and follow-up work

## Definition of Done

A feature is complete when:

- [x] Its behavior matches the intended requirements.
- [x] It reuses existing architecture where appropriate.
- [x] It has clear loading, empty, success, and failure states where applicable.
- [x] Relevant automated tests pass.
- [ ] Linux and Windows behavior has been verified. — CI runs the core suite on
      both platforms and the Qt suite against a real Qt build on Windows. The
      build machine used for this release had no GTK runtime and no display, so
      a human has not clicked through either desktop.
- [x] Existing user data and settings remain compatible.
- [x] Documentation is updated.
- [x] No credentials or sensitive user data are exposed.
