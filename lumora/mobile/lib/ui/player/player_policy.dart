/// Navigation rules for the player WebView.
///
/// Pure functions, kept out of [PlayerPage] so the delegate's decision can be
/// tested with fake requests instead of a real WebView.
library;

/// Schemes the embed itself uses for inline content. They cannot reach another
/// site, so they are never a cross-site navigation.
const Set<String> passthroughSchemes = <String>{
  'about',
  'blob',
  'data',
  'javascript',
};

/// Our own app schemes: a provider's "open in app" link must not launch them.
const Set<String> blockedSchemes = <String>{
  'intent',
  'market',
  'file',
  'content',
  'tel',
  'mailto',
  'sms',
};

/// The domain a host belongs to, without a `www.` prefix.
String baseDomain(String host) {
  final lower = host.trim().toLowerCase().replaceAll(RegExp(r'\.$'), '');
  return lower.startsWith('www.') ? lower.substring(4) : lower;
}

/// Whether [requested] may load while the player was opened on [origin].
///
/// Only the provider's own site (and its subdomains) is allowed: an ad overlay
/// or a "continue watching" link that points at another host is refused, and so
/// is every non-web scheme. Same-origin asset and player requests keep working.
bool isAllowedNavigation(Uri requested, Uri origin) {
  final scheme = requested.scheme.toLowerCase();
  if (passthroughSchemes.contains(scheme)) {
    return true;
  }
  if (blockedSchemes.contains(scheme)) {
    return false;
  }
  if (scheme != 'http' && scheme != 'https') {
    return false;
  }
  final host = requested.host.toLowerCase();
  if (host.isEmpty) {
    return false;
  }
  final originHost = origin.host.toLowerCase();
  if (host == originHost) {
    return true;
  }
  final domain = baseDomain(originHost);
  if (domain.isEmpty) {
    return false;
  }
  return baseDomain(host) == domain || host.endsWith('.$domain');
}

/// The delegate's decision for a raw request URL, e.g. `onNavigationRequest`.
///
/// An unparseable URL is refused, not passed on: a provider that hands the
/// WebView junk must not be able to navigate anywhere with it.
bool shouldNavigate(String requestUrl, Uri origin) {
  final Uri requested;
  try {
    requested = Uri.parse(requestUrl);
  } on FormatException {
    return false;
  }
  return isAllowedNavigation(requested, origin);
}

/// A short, log-safe description of a refused request.
String blockedNavigationReason(Uri requested, Uri origin) {
  final scheme = requested.scheme.toLowerCase();
  if (scheme != 'http' && scheme != 'https') {
    return 'scheme "${scheme.isEmpty ? 'none' : scheme}"';
  }
  final host = requested.host.isEmpty ? 'no host' : requested.host;
  return 'host "$host" outside "${origin.host}"';
}
