/// STAND-IN MODULE — delete this file when agent A's registry lands.
///
/// [watchUrlFor] and [playbackTargets] are agent C's own convenience wrappers
/// around the registry. They are the only public way to go from a `MediaItem`
/// to ready-to-load provider URLs, and they exist only because the registry is
/// still a stand-in: once agent A's real `lib/data/providers_registry.dart` is
/// in place, providers build their own URLs and this file (plus
/// `test/nav_stubs_test.dart`) is deleted with nothing else changing.
///
/// `lib/nav.dart` does not import this file: `openPlayer` derives its own
/// provider-switch list from `buildWatchUrl` directly, so removing this module
/// does not touch the player, the settings page or the app wiring.
///
/// Nothing here reads `StreamProvider.urlTemplate` — parsing the templates is
/// the registry's job, and `buildWatchUrl` is the only URL builder in this app.
library;

import 'data/providers_registry.dart' show buildWatchUrl;
import 'data/settings_store.dart';
import 'models/media_item.dart';
import 'nav.dart' show enabledProviders, pickProvider;
import 'ui/player/player_page.dart';

/// The URL [item] opens on the user's chosen provider, or null when no enabled
/// provider can play it.
///
/// [buildWatchUrl] throws [ArgumentError] when the provider has no route for
/// the kind or the title lacks the id that route is keyed by; that is treated
/// as "no URL for this title", not as an error the UI has to show.
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
