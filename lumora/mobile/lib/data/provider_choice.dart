import '../models/media_item.dart';
import 'anime_identity.dart';
import 'provider_health.dart';
import 'providers_registry.dart';
import 'settings_store.dart';

/// The provider ids to try for [item], best first; the first one is the
/// default.
///
/// * **Anime** - [MediaItem.kind] is [MediaKind.anime], or [identity] verified
///   it as anime with an AniList id: `ani` (ani.pm) first when it is enabled,
///   then the user's order.
/// * **Automatic** - otherwise, when `autoProvider` is on and health data
///   exists: the fastest reachable enabled provider that supports the kind
///   first, then the user's order.
/// * **Saved** - otherwise `defaultProviderId`, then the enabled order.
///
/// Disabled providers and providers that cannot play the kind are never
/// included. A title that is not anime gets no special treatment.
List<String> orderedProviders({
  required MediaItem item,
  required AppSettings settings,
  ProviderHealthService? health,
  AnimeIdentity? identity,
}) {
  final anime =
      item.kind == MediaKind.anime ||
      (identity != null &&
          identity.status == IdentityStatus.anime &&
          identity.anilistId != null);

  // A verified anime is played as anime, so anime-capable providers are the
  // only ones that can play it even when the row came from TMDB's TV list.
  final kind = anime ? MediaKind.anime : item.kind;
  final enabled = <String>[
    for (final id in settings.enabledProviderIds)
      if (providerById(id)?.supports(kind) ?? false) id,
  ];

  final out = <String>[];
  void add(String? id) {
    if (id != null && enabled.contains(id) && !out.contains(id)) {
      out.add(id);
    }
  }

  if (anime) {
    add('ani');
    for (final id in enabled) {
      add(id);
    }
    return List<String>.unmodifiable(out);
  }

  if (settings.autoProvider && health != null) {
    final hasData = enabled.any((id) => health.cached(id) != null);
    if (hasData) {
      add(health.fastest(enabled));
    }
  }

  add(settings.defaultProviderId);
  for (final id in enabled) {
    add(id);
  }
  return List<String>.unmodifiable(out);
}
