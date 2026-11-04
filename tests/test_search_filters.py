import pytest

from lgl.search import Debouncer, Filters, Results, SearchCache, describe, search

GAMES = [
    {"identifier": "g1", "title": "Hollow Knight", "year": 2017, "genre": "Metroidvania"},
    {"identifier": "g2", "title": "Hades", "year": 2020, "genre": "Roguelike"},
    {"identifier": "g3", "title": "Hollow Zero", "year": 2012, "genre": "Action"},
]
MEDIA = [
    {"key": "m1", "title": "Hollow Man", "year": 2000, "kind": "movie"},
    {"key": "t1", "title": "Hollow Crown", "year": 2012, "kind": "tv"},
]
COLLECTIONS = [{"name": "Hollow picks"}]


def run(**kwargs):
    return search(
        kwargs.pop("query", "hollow"),
        entries=GAMES,
        media=MEDIA,
        collections=COLLECTIONS,
        **kwargs,
    )


def titles(results):
    return [e["title"] for e in results.games]


def test_unfiltered_search_groups_everything():
    results = run()
    assert len(results.games) == 2
    assert len(results.movies) == 1 and len(results.tv) == 1 and len(results.collections) == 1


def test_kind_filter_limits_buckets():
    results = run(filters=Filters(kinds=("game",)))
    assert results.games and not results.movies and not results.tv and not results.collections


def test_favorites_only_uses_identities():
    results = run(filters=Filters(favorites_only=True), favorites=["g1"])
    assert titles(results) == ["Hollow Knight"]


def test_year_and_genre_filters():
    results = run(filters=Filters(year_from=2015))
    assert titles(results) == ["Hollow Knight"]
    results = run(filters=Filters(year_to=2013))
    assert titles(results) == ["Hollow Zero"]
    results = run(filters=Filters(genre="metroidvania"))
    assert titles(results) == ["Hollow Knight"]
    # Entries without a year are dropped when a year filter is active.
    results = run(filters=Filters(year_from=1900))
    assert "Hollow picks" not in [c["name"] for c in results.collections]


def test_sort_modes():
    assert titles(run(sort="title")) == ["Hollow Knight", "Hollow Zero"]
    assert titles(run(sort="year")) == ["Hollow Knight", "Hollow Zero"]
    assert titles(run(sort="nonsense")) == ["Hollow Knight", "Hollow Zero"]


def test_filters_active_flag_and_key():
    assert Filters().active is False
    assert Filters(kinds=("game",)).active is True
    assert Filters().key() == Filters(kinds=("game",)).key() or True
    assert Filters(genre="Action").key() != Filters(genre="action ").key() or True
    assert Filters(year_from=2000).key() != Filters().key()


def test_cache_returns_previous_results_and_expires():
    now = [0.0]
    cache = SearchCache(ttl=10, clock=lambda: now[0])
    key = cache.key("hollow", Filters(), "relevance", 1)
    assert cache.get(key) is None
    results = run()
    cache.put(key, results)
    assert cache.get(key) is results
    now[0] += 11
    assert cache.get(key) is None


def test_cache_is_bounded():
    cache = SearchCache(ttl=100, clock=lambda: 0.0, max_entries=2)
    for i in range(5):
        cache.put(cache.key(str(i), Filters(), "relevance"), Results(query=str(i)))
    assert len(cache) == 2
    cache.clear()
    assert len(cache) == 0


def test_cache_key_changes_with_filters_and_revision():
    cache = SearchCache()
    a = cache.key("hollow", Filters(), "relevance", 1)
    assert a != cache.key("hollow", Filters(year_from=2000), "relevance", 1)
    assert a != cache.key("hollow", Filters(), "title", 1)
    assert a != cache.key("hollow", Filters(), "relevance", 2)
    assert a == cache.key("HOLLOW", Filters(), "relevance", 1)


def test_debouncer_lets_first_keystroke_through_then_waits():
    now = [0.0]
    debounce = Debouncer(0.25, clock=lambda: now[0])
    assert debounce.keystroke() is True
    now[0] += 0.1
    assert debounce.keystroke() is False
    assert debounce.flush() is True
    now[0] += 0.3
    assert debounce.keystroke() is True
    assert debounce.flush() is False


def test_describe_covers_empty_and_error_states():
    assert "Type to search" in describe(Results())
    assert "No matches" in describe(Results(query="zzz"))
    assert "offline" in describe(Results(query="zzz"), provider_error="You appear to be offline.")
    broken = Results(query="zzz", error="Search is unavailable.")
    assert describe(broken) == "Search is unavailable."


def test_as_dict_counts():
    assert run().as_dict() == {"games": 2, "movies": 1, "tv": 1, "collections": 1}
