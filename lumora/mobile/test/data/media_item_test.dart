import 'package:flutter_test/flutter_test.dart';
import 'package:lumora_mobile/models/media_item.dart';

void main() {
  test('copyWith replaces kind, ids and the adult flag', () {
    const item = MediaItem(
      id: 'tmdb:1',
      title: 'A Film',
      kind: MediaKind.movie,
      tmdbId: 1,
      year: '2024',
    );

    final copy = item.copyWith(
      kind: MediaKind.anime,
      anilistId: 5,
      adult: true,
    );

    expect(copy.kind, MediaKind.anime);
    expect(copy.anilistId, 5);
    expect(copy.adult, isTrue);
    expect(copy.id, 'tmdb:1');
    expect(copy.title, 'A Film');
    expect(copy.tmdbId, 1);
    expect(copy.year, '2024');
    expect(item.adult, isFalse);
  });

  test('copyWith keeps nullables unless it is told to clear them', () {
    const item = MediaItem(
      id: 'tmdb:1',
      title: 'A Film',
      kind: MediaKind.movie,
      posterUrl: 'https://example.test/p.jpg',
      rating: 7.5,
    );

    final kept = item.copyWith(adult: true);
    expect(kept.posterUrl, 'https://example.test/p.jpg');
    expect(kept.rating, 7.5);

    final cleared = item.copyWith(posterUrl: null, rating: null);
    expect(cleared.posterUrl, isNull);
    expect(cleared.rating, isNull);
  });

  test('MediaDetail carries a logo url', () {
    const detail = MediaDetail(
      item: MediaItem(id: 'tmdb:1', title: 'A Film', kind: MediaKind.movie),
      logoUrl: 'https://image.tmdb.org/t/p/w500/logo.png',
    );

    expect(detail.logoUrl, 'https://image.tmdb.org/t/p/w500/logo.png');
  });
}
