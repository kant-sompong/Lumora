import '../models/media_item.dart';

/// One provider's search hit: the parsed title plus the few raw fields that
/// decide whether the hit belongs in the collapsed list.
class SearchCandidate {
  const SearchCandidate({
    required this.item,
    this.titles = const <String>[],
    this.format,
    this.hasPrequel = false,
  });

  final MediaItem item;

  /// Every title the provider knows the entry by: AniList's romaji, english
  /// and native titles, TMDB's name and original name.
  final List<String> titles;

  /// AniList's media format (`TV`, `MOVIE`, `OVA`, ...); null for TMDB.
  final String? format;

  /// True when AniList says another anime is this entry's prequel.
  final bool hasPrequel;
}

/// Formats that are extras to a series rather than a series of their own.
const Set<String> _extraFormats = <String>{
  'OVA',
  'ONA',
  'SPECIAL',
  'MUSIC',
  'TV_SHORT',
};

/// A title reduced to its letters and digits, so punctuation, spacing, case
/// and accents' neighbours do not keep two spellings apart.
String titleKey(String value) {
  return value.toLowerCase().replaceAll(RegExp(r'[^0-9a-z]+'), '');
}

/// True for a format that belongs to another entry (an OVA, special, ...).
bool isExtraFormat(String? format) {
  if (format == null) {
    return false;
  }
  return _extraFormats.contains(format.toUpperCase());
}

/// True when [a] and [b] look like the same series: a shared normalized title
/// and the same year, or a year within one.
bool sameSeries(SearchCandidate a, SearchCandidate b) {
  final yearA = int.tryParse(a.item.year ?? '');
  final yearB = int.tryParse(b.item.year ?? '');
  if (yearA == null || yearB == null || (yearA - yearB).abs() > 1) {
    return false;
  }
  final keysB = <String>{
    for (final title in b.titles)
      if (titleKey(title).isNotEmpty) titleKey(title),
  };
  if (keysB.isEmpty) {
    return false;
  }
  for (final title in a.titles) {
    final key = titleKey(title);
    if (key.isNotEmpty && keysB.contains(key)) {
      return true;
    }
  }
  return false;
}

/// Collapses both providers' hits into one result list.
///
/// TMDB comes first and keeps every hit, since its TV entry carries the
/// seasons. AniList then contributes only the first entry of each series:
/// sequels (a `PREQUEL` relation) and extras are dropped, unless dropping the
/// extras would leave no anime at all. An AniList entry that repeats a TMDB TV
/// result of the same series is dropped too. Nothing is listed twice.
List<MediaItem> collapseSearchResults({
  required List<SearchCandidate> tmdb,
  required List<SearchCandidate> anime,
}) {
  final seriesFirst = anime
      .where((candidate) => !candidate.hasPrequel)
      .toList(growable: false);
  final withoutExtras = seriesFirst
      .where((candidate) => !isExtraFormat(candidate.format))
      .toList(growable: false);
  final keptAnime = withoutExtras.isEmpty ? seriesFirst : withoutExtras;

  final tmdbShows = tmdb
      .where((candidate) => candidate.item.kind == MediaKind.tv)
      .toList(growable: false);

  final out = <MediaItem>[];
  final seen = <String>{};
  for (final candidate in tmdb) {
    if (seen.add(candidate.item.id)) {
      out.add(candidate.item);
    }
  }
  for (final candidate in keptAnime) {
    if (tmdbShows.any((show) => sameSeries(show, candidate))) {
      continue;
    }
    if (seen.add(candidate.item.id)) {
      out.add(candidate.item);
    }
  }
  return out;
}
