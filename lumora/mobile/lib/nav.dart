import 'package:flutter/material.dart';
// `provider` also exports a `StreamProvider`; ours is the media model.
import 'package:provider/provider.dart' hide StreamProvider;

import 'data/providers_registry.dart';
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
/// The user's default wins when it is enabled and can play the kind; otherwise
/// the first enabled provider that can. Null means nothing can play it.
StreamProvider? pickProvider(AppSettings settings, MediaItem item) {
  final candidates = enabledProviders(settings)
      .where((StreamProvider p) => p.kinds.contains(item.kind))
      .toList();
  if (candidates.isEmpty) {
    return null;
  }
  final defaultId = settings.defaultProviderId;
  for (final provider in candidates) {
    if (provider.id == defaultId) {
      return provider;
    }
  }
  return candidates.first;
}

/// Every enabled provider that can play [item], as ready-to-load targets.
///
/// A provider is skipped when the title has no id its route needs, so a
/// film-only id cannot leave a dead entry in the switch list.
List<PlaybackTarget> playbackTargets(
  AppSettings settings,
  MediaItem item, {
  int season = 1,
  int episode = 1,
}) {
  final targets = <PlaybackTarget>[];
  for (final provider in enabledProviders(settings)) {
    if (!provider.kinds.contains(item.kind)) {
      continue;
    }
    final Uri url;
    try {
      url = buildWatchUrl(provider, item, season: season, episode: episode);
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

Uri? watchUrlFor(
  AppSettings settings,
  MediaItem item, {
  int season = 1,
  int episode = 1,
}) {
  final provider = pickProvider(settings, item);
  if (provider == null) {
    return null;
  }
  try {
    return buildWatchUrl(provider, item, season: season, episode: episode);
  } on ArgumentError {
    return null;
  }
}

/// Open [item] in the full-screen player on the user's provider.
Future<void> openPlayer(
  BuildContext c,
  MediaItem item, {
  int season = 1,
  int episode = 1,
}) async {
  final settings = c.read<AppSettings>();
  final provider = pickProvider(settings, item);
  final targets = playbackTargets(
    settings,
    item,
    season: season,
    episode: episode,
  );
  if (provider == null || targets.isEmpty) {
    final messenger = ScaffoldMessenger.maybeOf(c);
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
  var index = 0;
  for (var i = 0; i < targets.length; i++) {
    if (targets[i].providerId == provider.id) {
      index = i;
      break;
    }
  }
  await Navigator.of(c).push(
    MaterialPageRoute<void>(
      builder: (_) => PlayerPage(
        url: targets[index].url,
        title: item.title,
        targets: targets,
        initialIndex: index,
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
