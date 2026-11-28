/// The small HTML page that frames an anime embed.
///
/// anipm.pk's developer docs say an embed must always be framed: opened as a
/// page it redirects to the title page instead of playing ("Always frame it").
/// The player therefore loads this wrapper, with the embed in a full-screen
/// `<iframe>`, instead of loading the embed URL as the top-level document.
///
/// Only the anime provider is framed: every other provider plays correctly
/// when loaded directly, and its navigation policy stays exactly as it was.
/// The wrapper carries no `sandbox` attribute - the provider documents that a
/// sandboxed frame breaks its player.
library;

/// Real-looking HTTPS origin for the wrapper document.
///
/// It matches the app's brand and is never fetched: it only gives the document
/// an origin. It is deliberately *not* the provider's origin, because a
/// same-origin wrapper would change how the embed sees its parent.
const String embedFrameBaseUrl = 'https://player.lumora.app/';

/// What the framed player is allowed to do, as the provider documents it.
const String embedFrameAllow =
    'autoplay; fullscreen; picture-in-picture; encrypted-media; '
    'screen-wake-lock';

/// Providers whose embeds must be framed rather than loaded as a page.
const Set<String> framedProviderIds = <String>{'ani'};

/// Whether [providerId]'s embeds have to be framed.
bool needsEmbedFrame(String providerId) =>
    framedProviderIds.contains(providerId.trim().toLowerCase());

/// Escapes [value] so it cannot break out of a double-quoted attribute.
String escapeHtmlAttribute(String value) => value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');

/// The wrapper document: a full-screen iframe of one escaped embed URL.
String buildEmbedFrameHtml(Uri embed) {
  final src = escapeHtmlAttribute(embed.toString());
  return '<!doctype html><html><head>'
      '<meta name="viewport" content="width=device-width,initial-scale=1">'
      '<style>html,body{margin:0;height:100%;background:#000}'
      'iframe{position:fixed;inset:0;width:100%;height:100%;border:0}'
      '</style></head><body>'
      '<iframe src="$src" allow="$embedFrameAllow" '
      'allowfullscreen></iframe>'
      '</body></html>';
}

/// Whether [url] is the wrapper document itself.
///
/// The wrapper's own initial load must survive the player's "only the
/// provider's host" rule; every other top-level navigation is still held to
/// it, so a page that tries to leave the provider is refused as before.
bool isEmbedFrameDocumentUrl(String url) {
  final parsed = Uri.tryParse(url);
  if (parsed == null || !parsed.hasScheme || parsed.host.isEmpty) {
    return false;
  }
  final base = Uri.parse(embedFrameBaseUrl);
  return parsed.origin == base.origin &&
      (parsed.path.isEmpty || parsed.path == '/');
}
