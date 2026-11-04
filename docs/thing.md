# Lumora Feature Roadmap

**Project:** [kant-sompong/Lumora](https://github.com/kant-sompong/Lumora)  
**Purpose:** Track existing capabilities and plan improvements across Linux and Windows.

> **Important:** This is a planning document, not proof that every listed feature is implemented in the current code. Verify each item against the repository before changing code, and update the checklist only after testing it.

## Goals

- Keep the Linux and Windows editions as consistent as their platforms allow.
- Improve UI quality, usability, reliability, and maintainability before adding too many new features.
- Reuse shared business logic and data models wherever practical.
- Preserve existing user data, settings, and working functionality.
- Clearly document platform-specific differences.

## Existing Feature Areas to Audit

Check the current implementation and mark each item accurately.

### Game Library

- [ ] Browse and search game catalog sources.
- [ ] Sort and filter games.
- [ ] Grid and list views.
- [ ] Game details, metadata, and artwork.
- [ ] Favorites and collections.
- [ ] Track download and installation status.
- [ ] Import and export library data.
- [ ] Detect duplicates and validate downloaded files.

### Downloads

- [ ] Queue and manage downloads.
- [ ] Pause, resume, cancel, and retry.
- [ ] Resume interrupted downloads where supported.
- [ ] Show progress, speed, and remaining time.
- [ ] Check available disk space.
- [ ] Handle network errors and invalid download sources safely.
- [ ] Integrate optional external download tools where supported.

### Movies, TV, and Anime

- [ ] Movie and TV metadata integration.
- [ ] Anime metadata integration.
- [ ] Provider selection and playback.
- [ ] Posters, backdrops, descriptions, ratings, and episode details.
- [ ] Favorites, watchlist, and viewing history.
- [ ] Playback error handling and recovery.
- [ ] Subtitle and audio selection where supported.

### Cross-Platform Support

- [ ] Linux UI and workflows.
- [ ] Windows 10/11 UI and workflows.
- [ ] Consistent navigation and feature availability.
- [ ] Settings and provider configuration.
- [ ] Safe persistence of user preferences.
- [ ] Platform-specific testing and documentation.

---

## Priority 0 — Core Improvements

These should be considered before expanding the feature set.

### 1. Consistent Linux and Windows UI

- [ ] Audit both applications screen by screen.
- [ ] Match navigation, page structure, typography, spacing, icons, cards, menus, and dialogs.
- [ ] Make provider menus and settings behave consistently.
- [ ] Match loading, empty, error, and success states.
- [ ] Keep platform-native behavior where necessary, without unnecessary visual differences.
- [ ] Add screenshots to document expected UI behavior.

**Acceptance criteria**
- Equivalent features are easy to find on both platforms.
- Common workflows have equivalent behavior.
- No major UI regressions occur on either platform.

### 2. Global Search

- [ ] Search games, movies, TV shows, and anime from one entry point.
- [ ] Group results by content type.
- [ ] Add useful filters and sorting.
- [ ] Support keyboard navigation and a shortcut to focus search.
- [ ] Show useful empty and error states.
- [ ] Debounce requests and cache results where appropriate.

**Acceptance criteria**
- Search remains responsive with large libraries.
- Results identify their content type and open the correct detail page.
- Failed sources do not break results from other sources.

### 3. Reliable Metadata and Artwork

- [ ] Improve matching between titles and provider IDs.
- [ ] Cache posters and backdrops locally.
- [ ] Use consistent placeholders when artwork is missing.
- [ ] Avoid repeatedly fetching unchanged metadata.
- [ ] Provide a way to correct incorrect matches manually.
- [ ] Handle rate limits, timeouts, missing fields, and unavailable providers.
- [ ] Keep provider-specific data separate from the app's common content model.

**Acceptance criteria**
- Missing artwork never breaks layout.
- Cached content remains usable when a metadata service is temporarily unavailable.
- Incorrect matches can be corrected without corrupting the library.

### 4. Provider and API Settings

- [ ] Create one clearly organized provider settings area.
- [ ] Separate metadata providers from playback providers.
- [ ] Explain which providers require API credentials.
- [ ] Validate credentials without exposing secrets.
- [ ] Display connection status and actionable error messages.
- [ ] Allow providers to be enabled or disabled where supported.
- [ ] Never commit real API keys, tokens, or credentials to the repository.

**Acceptance criteria**
- Users can identify and configure each provider without confusion.
- Invalid credentials produce understandable errors.
- Secrets are stored using an appropriate platform-specific mechanism when available.

### 5. Playback Reliability

- [ ] Make loading and playback failures clear.
- [ ] Provide retry and recovery actions.
- [ ] Preserve viewing progress where supported.
- [ ] Support subtitle and audio-track selection when the playback backend permits it.
- [ ] Keep playback behavior consistent across operating systems where possible.
- [ ] Explain unsupported formats or unavailable sources.
- [ ] Test provider changes, window resizing, and repeated open/close cycles.

**Acceptance criteria**
- A failed source does not freeze the application.
- The user can recover from common playback errors.
- Platform limitations are documented rather than hidden.

### 6. Release and Update Experience

- [ ] Show the current application version.
- [ ] Link to official releases and release notes.
- [ ] Check for updates only with appropriate user consent and reasonable frequency.
- [ ] Verify downloaded update artifacts before installation.
- [ ] Provide a clear fallback to manual installation.
- [ ] Do not silently replace application files without a safe update mechanism.

**Acceptance criteria**
- Users can find the latest supported release and its changelog.
- Update checks fail gracefully when offline.
- Update instructions exist for Linux and Windows.

---

## Priority 1 — High-Value Features

### 7. Continue Watching

- [ ] Add a Continue Watching shelf to the home page.
- [ ] Store playback position and last-watched time where supported.
- [ ] Allow users to remove an item from the shelf.
- [ ] Resume from the saved position when the provider supports it.
- [ ] Avoid marking content completed based on unreliable progress data.

### 8. Anime Release Calendar

- [ ] Show upcoming episodes and release dates when data is available.
- [ ] Let users follow selected anime.
- [ ] Display timezone-aware dates.
- [ ] Handle unknown, delayed, or changed release dates.
- [ ] Add optional notifications only after the calendar is reliable.

### 9. Optional Watch-History Synchronization

- [ ] Evaluate supported services and their current API terms before choosing an integration.
- [ ] Make synchronization opt-in.
- [ ] Show sync status and errors.
- [ ] Prevent duplicate history entries.
- [ ] Handle conflicting updates and offline use.
- [ ] Never upload viewing history without user authorization.

### 10. Custom Collections

- [ ] Create, rename, and delete collections.
- [ ] Add and remove library items.
- [ ] Support manual ordering.
- [ ] Provide smart collections based on filters where practical.
- [ ] Keep collections intact during app upgrades and backups.

### 11. Keyboard Navigation and Accessibility

- [ ] Add shortcuts for search and common navigation.
- [ ] Support keyboard operation of menus, dialogs, and cards.
- [ ] Show visible focus indicators.
- [ ] Add accessible labels to icon-only controls.
- [ ] Respect system scaling and readable text sizes.
- [ ] Check contrast in every supported theme.

### 12. Notifications

- [ ] Notify users when downloads finish or fail.
- [ ] Offer optional episode-release notifications.
- [ ] Provide settings to disable each notification category.
- [ ] Avoid duplicate or excessively frequent notifications.
- [ ] Use the operating system's notification mechanism where practical.

### 13. Backup and Restore

- [ ] Back up library metadata, collections, preferences, and history.
- [ ] Let users choose what is included.
- [ ] Exclude API secrets and authentication tokens by default.
- [ ] Validate backup files before restoring.
- [ ] Warn before overwriting existing data.
- [ ] Document backup format and compatibility.

---

## Priority 2 — Larger Expansions

### 14. Plugin System

- [ ] Define a stable plugin interface.
- [ ] Support additional metadata sources or integrations.
- [ ] Validate plugin manifests and versions.
- [ ] Document permissions and security expectations.
- [ ] Do not execute untrusted plugins without an explicit, secure design.

### 15. Local Media Library

- [ ] Scan user-selected folders for supported media.
- [ ] Match files to metadata with a manual correction option.
- [ ] Support local posters and subtitles.
- [ ] Detect moved or missing files.
- [ ] Let users exclude folders and file patterns.
- [ ] Never scan unrelated directories without permission.

### 16. More Game Library Sources

- [ ] Evaluate integrations for installed-game libraries and storefronts.
- [ ] Clearly distinguish installed games from catalog entries.
- [ ] Keep optional integrations disabled until configured.
- [ ] Do not require users to provide account credentials to unsupported third-party services.
- [ ] Document API and licensing limitations.

### 17. Advanced Download Dashboard

- [ ] Add filters for queued, active, completed, and failed downloads.
- [ ] Display transfer speed and progress clearly.
- [ ] Add configurable concurrency limits.
- [ ] Show retry and error details.
- [ ] Preserve queue state across restarts where appropriate.

### 18. Multiple Profiles

- [ ] Separate preferences, favorites, collections, and history by profile.
- [ ] Make the active profile obvious.
- [ ] Define how profiles are backed up and migrated.
- [ ] Do not imply strong account security unless authentication and data isolation are actually implemented.

### 19. Home-Screen Customization

- [ ] Allow users to reorder or hide content shelves.
- [ ] Offer compact and poster-focused layouts.
- [ ] Persist layout preferences.
- [ ] Provide a reset-to-default option.

### 20. Diagnostics and Support Export

- [ ] Collect relevant application logs and version information.
- [ ] Redact tokens, credentials, personal paths, and other sensitive data.
- [ ] Let users review the export before sharing it.
- [ ] Include platform and provider error details when useful.
- [ ] Avoid collecting unrelated personal files or browsing data.

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

- [ ] Its behavior matches the intended requirements.
- [ ] It reuses existing architecture where appropriate.
- [ ] It has clear loading, empty, success, and failure states where applicable.
- [ ] Relevant automated tests pass.
- [ ] Linux and Windows behavior has been verified.
- [ ] Existing user data and settings remain compatible.
- [ ] Documentation is updated.
- [ ] No credentials or sensitive user data are exposed.
