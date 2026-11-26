import 'package:flutter/material.dart';
// `provider` also exports a `StreamProvider`; ours is the media model.
import 'package:provider/provider.dart' hide StreamProvider;

// The only registry symbols this file is allowed to touch. Providers build
// their own URLs; nothing here parses `StreamProvider.urlTemplate`.
import 'data/anime_identity.dart';
import 'data/provider_choice.dart';
import 'data/provider_health.dart';
import 'data/providers_registry.dart' show buildWatchUrl, providerById;
import 'data/settings_store.dart';
import 'models/media_item.dart';
import 'models/stream_provider.dart';
import 'ui/player/player_page.dart';
import 'ui/settings/settings_page.dart';

/// The providers the user has switched on, in the order they chose.
List<StreamProvider> enabledProviders(AppSettings settings) {
  final enabled = <StreamProvider>[];
  for (final id in settings.enabledProviderIds) {
    final provider = providerById(id);
    if (provider != null) {
      enabled.add(provider);
    }
  }
  return enabled;
}

/// The provider [openPlayer] should try first for [item].
///
/// Null means nothing enabled can play it.
StreamProvider? pickProvider(AppSettings settings, MediaItem item) {
  final ids = orderedProviders(item: item, settings: settings);
  return ids.isEmpty ? null : providerById(ids.first);
}

/// Whether [item] is worth an identity lookup before playback.
///
/// Only titles that are not already anime, have no AniList id, and a saved
/// MyAnimeList Client ID qualify. Everything else plays immediately.
bool needsIdentityLookup(MediaItem item, AppSettings settings) =>
    item.kind != MediaKind.anime &&
    item.anilistId == null &&
    settings.malClientId.trim().isNotEmpty;

/// How a lookup answers for one title; replaceable in tests.
typedef IdentityLookup =
    Future<AnimeIdentity> Function(MediaItem item, AppSettings settings);

/// What the player should play, after at most one identity lookup.
class PlaybackPlan {
  const PlaybackPlan({required this.item, this.identity});

  /// [item], or the same title retyped as anime once MyAnimeList proved it.
  final MediaItem item;

  /// The verified identity, when that is what made [item] anime.
  final AnimeIdentity? identity;
}

/// Resolves [item] to something playable.
///
/// A verified anime match with an AniList id is retyped so ani.pm can play it.
/// Everything else — unverified, not anime, a refused or missing lookup — is
/// returned untouched: a title is never hidden or blocked because of this.
Future<PlaybackPlan> planPlayback(
  MediaItem item,
  AppSettings settings, {
  IdentityLookup? lookup,
}) async {
  if (!needsIdentityLookup(item, settings)) {
    return PlaybackPlan(item: item);
  }
  final resolve =
      lookup ?? (MediaItem it, AppSettings s) => AnimeIdentityService(s).identifyTmdb(it);
  AnimeIdentity identity;
  try {
    identity = await resolve(item, settings);
  } catch (_) {
    return PlaybackPlan(item: item);
  }
  final anilistId = identity.anilistId;
  if (identity.status == IdentityStatus.anime && anilistId != null) {
    return PlaybackPlan(
      item: item.copyWith(kind: MediaKind.anime, anilistId: anilistId),
      identity: identity,
    );
  }
  return PlaybackPlan(item: item, identity: identity);
}

/// The player's provider list for [plan], best first.
///
/// Ordered by [orderedProviders]; a provider that cannot build a URL for this
/// title is skipped, so a film-only id cannot leave a dead entry in the list.
List<PlaybackTarget> playbackTargets(
  PlaybackPlan plan,
  AppSettings settings, {
  ProviderHealthService? health,
  int season = 1,
  int episode = 1,
}) {
  final ordered = orderedProviders(
    item: plan.item,
    settings: settings,
    health: health,
    identity: plan.identity,
  );
  final targets = <PlaybackTarget>[];
  for (final id in ordered) {
    final provider = providerById(id);
    if (provider == null) {
      continue;
    }
    final Uri url;
    try {
      url = buildWatchUrl(
        provider,
        plan.item,
        season: season,
        episode: episode,
      );
    } on ArgumentError {
      continue;
    }
    targets.add(
      PlaybackTarget(
        providerId: provider.id,
        providerName: provider.name,
        url: url,
      ),
    );
  }
  return targets;
}

/// The app-level health service, when the widget tree provides one.
///
/// Tests and previews build their own trees without it; the player then simply
/// has no health dots.
ProviderHealthService? _healthService(BuildContext c) {
  try {
    return c.read<ProviderHealthService>();
  } on ProviderNotFoundException {
    return null;
  }
}

/// Open [item] in the full-screen player.
Future<void> openPlayer(
  BuildContext c,
  MediaItem item, {
  int season = 1,
  int episode = 1,
}) async {
  final settings = c.read<AppSettings>();
  final health = _healthService(c);
  final messenger = ScaffoldMessenger.maybeOf(c);

  var plan = PlaybackPlan(item: item);
  if (needsIdentityLookup(item, settings)) {
    // A short, cancellable-looking note while one lookup runs; playback never
    // waits for anything else.
    messenger?.hideCurrentSnackBar();
    messenger?.showSnackBar(
      const SnackBar(
        content: Text('Checking…'),
        duration: Duration(seconds: 20),
      ),
    );
    plan = await planPlayback(item, settings);
    messenger?.hideCurrentSnackBar();
    if (!c.mounted) {
      return;
    }
  }

  final targets = playbackTargets(
    plan,
    settings,
    health: health,
    season: season,
    episode: episode,
  );
  if (targets.isEmpty) {
    messenger?.hideCurrentSnackBar();
    messenger?.showSnackBar(
      SnackBar(
        content: const Text('Enable a provider in Settings'),
        action: SnackBarAction(
          label: 'Settings',
          onPressed: () => openSettings(c),
        ),
      ),
    );
    return;
  }

  final isMovie = plan.item.kind == MediaKind.movie;
  await Navigator.of(c).push(
    MaterialPageRoute<void>(
      builder: (_) => PlayerPage(
        url: targets.first.url,
        title: plan.item.title,
        targets: targets,
        initialIndex: 0,
        season: isMovie ? null : season,
        episode: isMovie ? null : episode,
        health: health,
      ),
    ),
  );
}

/// Push the two-tab settings page.
void openSettings(BuildContext c) {
  Navigator.of(c).push(
    MaterialPageRoute<void>(builder: (_) => const SettingsPage()),
  );
}
