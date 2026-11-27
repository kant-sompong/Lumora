import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:lumora_mobile/data/media_repository.dart';
import 'package:lumora_mobile/data/search_collapse.dart';
import 'package:lumora_mobile/data/settings_store.dart';
import 'package:lumora_mobile/models/media_item.dart';

import 'media_repository_test.dart'
    show fixtureJson, graphqlResponse, jsonResponse;

/// AniList's seasons, special, short and film for one series.
List<SearchCandidate> attackOnTitanAnime() =>
    aniListSearchCandidates(fixtureJson('anilist_search_attack_on_titan.json'));

/// TMDB's TV result for the same series.
List<SearchCandidate> attackOnTitanTmdb() =>
    tmdbSearchCandidates(fixtureJson('tmdb_search_attack_on_titan.json'));

void main() {
  group('search collapse', () {
    test('keeps the TMDB show and the film that has no prequel', () {
      final results = collapseSearchResults(
        tmdb: attackOnTitanTmdb(),
        anime: attackOnTitanAnime(),
      );

      expect(results.map((item) => item.id), <String>[
        'tmdb:1429',
        'anilist:120717',
      ]);
      expect(results.first.kind, MediaKind.tv);
      expect(results.first.title, 'Attack on Titan');
      expect(results.last.title, 'Attack on Titan: Chronicle');
    });

    test('without TMDB only the first entry of each series survives', () {
      final results = collapseSearchResults(
        tmdb: const <SearchCandidate>[],
        anime: attackOnTitanAnime(),
      );

      // Season 1 stands for the series: the sequel and the special, which
      // point at it as their prequel, are gone, and so is the TV short.
      expect(results.map((item) => item.id), <String>[
        'anilist:16498',
        'anilist:120717',
      ]);
    });

    test('extras are kept when nothing else matches', () {
      final extras = attackOnTitanAnime()
          .where((candidate) => isExtraFormat(candidate.format))
          .toList();

      expect(extras.map((candidate) => candidate.item.id), <String>[
        'anilist:99147',
        'anilist:20959',
      ]);
      final results = collapseSearchResults(
        tmdb: const <SearchCandidate>[],
        anime: extras,
      );
      // The special is still a sequel; the short has no better sibling left.
      expect(results.single.id, 'anilist:20959');
    });

    test('nothing is listed twice', () {
      final anime = attackOnTitanAnime();
      final tmdb = attackOnTitanTmdb();
      final results = collapseSearchResults(
        tmdb: <SearchCandidate>[...tmdb, ...tmdb],
        anime: <SearchCandidate>[...anime, ...anime],
      );

      expect(results.map((item) => item.id), <String>[
        'tmdb:1429',
        'anilist:120717',
      ]);
    });

    test('matches a show by any title and a year within one', () {
      const show = SearchCandidate(
        item: MediaItem(
          id: 'tmdb:1',
          title: 'Attack on Titan',
          kind: MediaKind.tv,
          year: '2014',
        ),
        titles: <String>['Attack on Titan', 'Shingeki no Kyojin'],
      );
      const entry = SearchCandidate(
        item: MediaItem(
          id: 'anilist:16498',
          title: 'Shingeki no Kyojin',
          kind: MediaKind.anime,
          year: '2013',
        ),
        titles: <String>['Shingeki no Kyojin', 'Attack on Titan'],
        format: 'TV',
      );

      expect(sameSeries(show, entry), isTrue);
      expect(
        sameSeries(
          entry,
          const SearchCandidate(
            item: MediaItem(
              id: 'tmdb:1',
              title: 'Attack on Titan',
              kind: MediaKind.tv,
              year: '2019',
            ),
          ),
        ),
        isFalse,
      );
    });

    test('a TMDB film never hides an anime entry', () {
      const film = SearchCandidate(
        item: MediaItem(
          id: 'tmdb:2',
          title: 'Attack on Titan',
          kind: MediaKind.movie,
          year: '2015',
        ),
      );
      final results = collapseSearchResults(
        tmdb: const <SearchCandidate>[film],
        anime: attackOnTitanAnime(),
      );

      expect(results.map((item) => item.id), <String>[
        'tmdb:2',
        'anilist:16498',
        'anilist:120717',
      ]);
    });

    test('titleKey drops case, punctuation and spaces', () {
      expect(
        titleKey('Attack on Titan: The Final Season'),
        'attackontitanthefinalseason',
      );
      expect(isExtraFormat('ova'), isTrue);
      expect(isExtraFormat('TV'), isFalse);
      expect(isExtraFormat(null), isFalse);
    });
  });

  group('TmdbAniListRepository search', () {
    test('returns one TMDB show and the film outside the series', () async {
      final requests = <http.Request>[];
      final client = MockClient((request) async {
        requests.add(request);
        if (request.url.host == 'graphql.anilist.co') {
          return graphqlResponse('anilist_search_attack_on_titan.json');
        }
        return jsonResponse('tmdb_search_attack_on_titan.json');
      });
      final repo = TmdbAniListRepository(
        AppSettings(tmdbApiKey: 'key123'),
        client: client,
      );

      final results = await repo.search('attack on titan');

      expect(results.map((item) => item.id), <String>[
        'tmdb:1429',
        'anilist:120717',
      ]);
      final animeRequest = requests.firstWhere(
        (request) => request.url.host == 'graphql.anilist.co',
      );
      // The search query asks for the relations that mark a sequel.
      expect(animeRequest.body, contains('relations { edges { relationType'));
      expect(animeRequest.body, contains('isAdult: false'));
    });

    test('without a TMDB key only AniList answers', () async {
      final requests = <http.Request>[];
      final client = MockClient((request) async {
        requests.add(request);
        return graphqlResponse('anilist_search_attack_on_titan.json');
      });
      final repo = TmdbAniListRepository(AppSettings(), client: client);

      final results = await repo.search('attack on titan');

      expect(requests, hasLength(1));
      expect(requests.single.url.host, 'graphql.anilist.co');
      expect(results.map((item) => item.id), <String>[
        'anilist:16498',
        'anilist:120717',
      ]);
    });
  });
}
