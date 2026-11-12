"""Next-episode ordering: same season first, then the next season."""

from __future__ import annotations

from lgl.tmdb import Episode, Season, following_episode


def season(number: int, *episodes: tuple[int, str]) -> Season:
    return Season(
        number=number,
        episodes=tuple(
            Episode(season=number, number=count, title=title)
            for count, title in episodes
        ),
    )


SHOW = (
    season(1, (1, "Pilot"), (2, "Second")),
    season(2, (1, "Return"), (2, "Middle"), (3, "Finale")),
)


def test_the_next_episode_is_the_following_one_in_the_same_season():
    found = following_episode(SHOW, 1, 1)

    assert found is not None
    assert found[0] == 1
    assert found[1].number == 2


def test_the_last_episode_of_a_season_moves_to_the_next_season():
    found = following_episode(SHOW, 1, 2)

    assert found is not None
    assert found[0] == 2
    assert found[1].number == 1
    assert found[1].title == "Return"


def test_the_series_finale_has_nothing_after_it():
    assert following_episode(SHOW, 2, 3) is None


def test_an_unknown_episode_offers_nothing():
    assert following_episode(SHOW, 9, 1) is None
    assert following_episode((), 1, 1) is None


def test_a_season_without_episodes_is_skipped():
    show = (season(1, (1, "Pilot")), season(2), season(3, (1, "Third")))

    found = following_episode(show, 1, 1)

    assert found is not None
    assert found[0] == 3
    assert found[1].number == 1
