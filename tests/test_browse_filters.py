"""The Browse filters both frontends share: content_filters over GameSummary."""

from lgl import browse_filters
from lgl.archive import GameSummary


def game(identifier, year="", subjects=()):
    return GameSummary(identifier, identifier, year, 0, None, subjects=list(subjects))


GAMES = [
    game("a", "1993", ("Action", "Shooter")),
    game("b", "1995", ("Puzzle",)),
    game("c", "1993", ("Puzzle", "Adventure")),
    game("d", "", ()),
]


def test_the_range_and_genres_are_casefolded_for_matching():
    filters = browse_filters.filters_for(
        min_year=1992,
        max_year=2005,
        genres=("Puzzle", "puzzle"),
        exclude_genres=("Action RPG",),
    )
    assert filters.genres == ("puzzle",)
    assert filters.exclude_genres == ("action rpg",)
    assert (filters.min_year, filters.max_year) == (1992, 2005)


def test_only_year_and_genres_are_ever_set():
    # GameSummary has no runtime, language or rating, so those stay empty rather
    # than being offered as controls that could never match.
    filters = browse_filters.filters_for(genres=("Puzzle",))
    assert filters.min_runtime is None and filters.max_runtime is None
    assert filters.languages == ()
    assert filters.min_rating is None


def test_a_backwards_range_is_swapped():
    filters = browse_filters.filters_for(min_year=2005, max_year=1992)
    assert (filters.min_year, filters.max_year) == (1992, 2005)


def test_games_match_the_shared_rules():
    filters = browse_filters.filters_for(
        min_year=1992, max_year=2005, genres=("Puzzle",)
    )
    assert [g.identifier for g in browse_filters.apply(GAMES, filters)] == ["b", "c"]
    assert browse_filters.matches(GAMES[0], filters) is False


def test_an_excluded_genre_wins_over_a_matching_include():
    filters = browse_filters.filters_for(
        genres=("Puzzle",), exclude_genres=("Adventure",)
    )
    assert [g.identifier for g in browse_filters.apply(GAMES, filters)] == ["b"]


def test_a_year_range_drops_titles_with_no_year():
    filters = browse_filters.filters_for(min_year=1900)
    assert "d" not in [g.identifier for g in browse_filters.apply(GAMES, filters)]


def test_the_summary_reads_as_a_sentence():
    filters = browse_filters.filters_for(
        min_year=1992,
        max_year=2005,
        genres=("Puzzle",),
        exclude_genres=("Action",),
    )
    assert browse_filters.summary(
        filters, display={"puzzle": "Puzzle", "action": "Action"}
    ) == "Showing 1992-2005, Puzzle, not Action"


def test_the_summary_is_empty_when_nothing_is_filtered():
    assert browse_filters.summary() == ""
    assert browse_filters.summary(browse_filters.EMPTY) == ""


def test_decade_chips_are_ranges_of_the_same_state():
    assert browse_filters.bucket_bounds("1990s") == (1990, 1999)
    assert browse_filters.bucket_bounds("pre1990") == (None, 1989)
    assert browse_filters.bucket_bounds("nonsense") == (None, None)
    assert browse_filters.bucket_for(1990, 1999) == "1990s"
    assert browse_filters.bucket_for(2020, None) == "2020s"
    assert browse_filters.bucket_for(1992, 2005) == "all"


def test_available_reports_only_the_fields_the_titles_carry():
    assert browse_filters.available(GAMES) == {"year", "genres"}
    assert browse_filters.available([game("x", "", ())]) == set()
    assert browse_filters.available([]) == set()


def test_year_numbers_accept_junk_but_not_nonsense():
    assert browse_filters.year_number("1995") == 1995
    assert browse_filters.year_number(" 1995-06-01 ") == 1995
    assert browse_filters.year_number("") is None
    assert browse_filters.year_number("soon") is None
    assert browse_filters.year_number("1000") is None
