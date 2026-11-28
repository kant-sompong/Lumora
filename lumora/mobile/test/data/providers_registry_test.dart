import 'package:flutter_test/flutter_test.dart';
import 'package:lumora_mobile/data/providers_registry.dart';
import 'package:lumora_mobile/models/media_item.dart';
import 'package:lumora_mobile/models/stream_provider.dart';

MediaItem movie({int? tmdbId = 603}) => MediaItem(
  id: 'tmdb:603',
  title: 'The Quiet Orbit',
  kind: MediaKind.movie,
  tmdbId: tmdbId,
);

MediaItem series({int? tmdbId = 1399}) => MediaItem(
  id: 'tmdb:1399',
  title: 'Night Market',
  kind: MediaKind.tv,
  tmdbId: tmdbId,
);

MediaItem anime({int? anilistId = 4242, int? tmdbId = 99}) => MediaItem(
  id: 'anilist:4242',
  title: 'Some Anime',
  kind: MediaKind.anime,
  tmdbId: tmdbId,
  anilistId: anilistId,
);

StreamProvider provider(String id) {
  final found = providerById(id);
  expect(found, isNotNull, reason: 'missing provider $id');
  return found!;
}

void main() {
  group('registry', () {
    test('lists every PC provider in lgl/streaming.py order', () {
      expect(
        allProviders.map((p) => p.id).toList(),
        <String>[
          'cinesrc',
          'rivestream',
          'toflix',
          'cinelite',
          'framextv',
          'atlantic',
          'vidy',
          'chillflix',
          'vidcore',
          'velarium',
          'afterdusk',
          'moonflix',
          'ani',
        ],
      );
    });

    test('uses the PC labels', () {
      expect(provider('cinesrc').name, 'CineSrc');
      expect(provider('rivestream').name, 'Rive');
      expect(provider('framextv').name, 'frameXTV');
      expect(provider('ani').name, 'anipm.pk (anime)');
    });

    test('providerById returns null for an unknown id', () {
      expect(providerById('nope'), isNull);
      expect(providerById(''), isNull);
    });

    test('every provider has one template per kind', () {
      for (final p in allProviders) {
        expect(
          urlTemplates(p).length,
          p.kinds.length,
          reason: '${p.id} template/kind mismatch',
        );
        for (final template in urlTemplates(p)) {
          expect(template, startsWith('https://'), reason: p.id);
          expect(template.contains(' '), isFalse, reason: p.id);
        }
      }
    });

    test('only the four documented placeholders are used', () {
      final used = <String>{};
      for (final p in allProviders) {
        for (final token in RegExp(r'\{[a-z]+\}').allMatches(p.urlTemplate)) {
          used.add(token.group(0)!);
        }
      }
      expect(used, templateTokens);
    });

    test('ani is anime only and every anime route is AniList keyed', () {
      expect(provider('ani').kinds, <MediaKind>{MediaKind.anime});
      for (final p in providersForKind(MediaKind.anime)) {
        expect(
          templateFor(p, MediaKind.anime)!.contains('{anilist}'),
          isTrue,
          reason: p.id,
        );
      }
    });

    test('providersForKind keeps PC order and skips anime for films', () {
      expect(providersForKind(MediaKind.movie).map((p) => p.id), isNot(contains('ani')));
      expect(providersForKind(MediaKind.anime).map((p) => p.id), <String>[
        'toflix',
        'cinelite',
        'vidy',
        'ani',
      ]);
    });
  });

  group('buildWatchUrl', () {
    test('builds the Rive movie URL from the TMDB id', () {
      expect(
        buildWatchUrl(provider('rivestream'), movie()).toString(),
        'https://www.rivestream.app/embed?type=movie&id=603',
      );
    });

    test('carries season and episode into a series URL', () {
      expect(
        buildWatchUrl(
          provider('cinesrc'),
          series(),
          season: 2,
          episode: 5,
        ).toString(),
        'https://cinesrc.st/embed/tv/1399?s=2&e=5',
      );
      expect(
        buildWatchUrl(
          provider('rivestream'),
          series(),
          season: 2,
          episode: 5,
        ).toString(),
        'https://www.rivestream.app/embed?type=tv&id=1399'
        '&season=2&episode=5',
      );
      expect(
        buildWatchUrl(
          provider('velarium'),
          series(),
          season: 2,
          episode: 5,
        ).toString(),
        'https://velarium.cc/en/watch/tv/1399-2-5',
      );
    });

    test('builds AniList-keyed anime URLs', () {
      expect(
        buildWatchUrl(provider('toflix'), anime(), episode: 3).toString(),
        'https://toflix.co/embed/anime/4242/3',
      );
      expect(
        buildWatchUrl(provider('cinelite'), anime(), episode: 3).toString(),
        'https://embed.cinelite.xyz/anime/4242/3',
      );
      expect(
        buildWatchUrl(provider('vidy'), anime(), episode: 3).toString(),
        'https://www.vidy.st/anime/4242/3',
      );
      expect(
        buildWatchUrl(provider('ani'), anime(), episode: 12).toString(),
        'https://anipm.pk/embed/ani/4242/12/sub',
      );
    });

    test('asks the anime route for the chosen audio track', () {
      expect(
        buildWatchUrl(
          provider('ani'),
          anime(),
          episode: 12,
          language: 'dub',
        ).toString(),
        'https://anipm.pk/embed/ani/4242/12/dub',
      );
      expect(
        buildWatchUrl(
          provider('ani'),
          anime(),
          episode: 12,
          language: 'vostfr',
        ).toString(),
        'https://anipm.pk/embed/ani/4242/12/vostfr',
      );
      // An unknown track falls back to sub instead of reaching the provider.
      expect(
        buildWatchUrl(
          provider('ani'),
          anime(),
          episode: 12,
          language: 'klingon',
        ).toString(),
        'https://anipm.pk/embed/ani/4242/12/sub',
      );
      // A provider without a language segment is unaffected.
      expect(
        buildWatchUrl(
          provider('cinesrc'),
          movie(),
          language: 'dub',
        ).toString(),
        'https://cinesrc.st/embed/movie/603',
      );
    });

    test('rejects a provider that cannot play the kind', () {
      expect(
        () => buildWatchUrl(provider('ani'), movie()),
        throwsA(isA<ArgumentError>()),
      );
      expect(
        () => buildWatchUrl(provider('cinesrc'), anime()),
        throwsA(isA<ArgumentError>()),
      );
    });

    test('throws when the title lacks the id the route needs', () {
      // No AniList id: the anime route must not fall back to the TMDB id.
      expect(
        () => buildWatchUrl(provider('ani'), anime(anilistId: null)),
        throwsA(isA<ArgumentError>()),
      );
      expect(
        () => buildWatchUrl(provider('cinesrc'), movie(tmdbId: null)),
        throwsA(isA<ArgumentError>()),
      );
    });

    test('MissingMediaIdException is an ArgumentError', () {
      try {
        buildWatchUrl(provider('ani'), anime(anilistId: null));
        fail('expected a throw');
      } on ArgumentError catch (error) {
        expect(error, isA<MissingMediaIdException>());
        expect(error.toString(), contains('Some Anime'));
      }
    });

    test('rejects non-positive ids and episode numbers', () {
      expect(
        () => buildWatchUrl(provider('cinesrc'), movie(tmdbId: 0)),
        throwsA(isA<ArgumentError>()),
      );
      expect(
        () => buildWatchUrl(provider('cinesrc'), movie(tmdbId: -4)),
        throwsA(isA<ArgumentError>()),
      );
      expect(
        () => buildWatchUrl(provider('ani'), anime(), episode: 0),
        throwsA(isA<ArgumentError>()),
      );
      expect(
        () => buildWatchUrl(provider('cinesrc'), series(), season: 0),
        throwsA(isA<ArgumentError>()),
      );
    });

    test('never returns a URL with an unexpanded token or a zero id', () {
      for (final p in allProviders) {
        for (final kind in p.kinds) {
          final item = switch (kind) {
            MediaKind.movie => movie(tmdbId: 1),
            MediaKind.tv => series(tmdbId: 1),
            MediaKind.anime => anime(anilistId: 1),
          };
          final url = buildWatchUrl(p, item).toString();
          expect(url.contains('{'), isFalse, reason: p.id);
          expect(url.contains('//0'), isFalse, reason: p.id);
          expect(url.startsWith('https://'), isTrue, reason: p.id);
        }
      }
    });
  });
}
