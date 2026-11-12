"""The GTK player's Next episode target.

``_next_episode`` only reads the details the window already holds, so a stub
window is enough and no display is needed.
"""

from __future__ import annotations

import types

import pytest

gi = pytest.importorskip("gi")

try:
    gi.require_version("Gtk", "3.0")
except (ValueError, ImportError):  # pragma: no cover - GTK 3 not installed
    pytest.skip("GTK 3 is not installed", allow_module_level=True)

from lgl import movies_ui  # noqa: E402
from lgl.tmdb import Episode, MediaDetails, Season  # noqa: E402


def show(*seasons: Season) -> MediaDetails:
    return MediaDetails(
        kind="tv", tmdb_id=1, title="W.W.D.D.", seasons=seasons
    )


def season(number: int, *episodes: tuple[int, str]) -> Season:
    return Season(
        number=number,
        episodes=tuple(
            Episode(season=number, number=count, title=title)
            for count, title in episodes
        ),
    )


def stub(details, playing, *, cache=None):
    return types.SimpleNamespace(
        _current_details=details,
        _playing_episode=playing,
        _season_cache=cache or {},
    )


def test_the_button_targets_the_following_episode():
    window = stub(show(season(4, (1, "W.W.D.D."), (2, "Next one"))), (4, 1))

    found = movies_ui.MoviesWindow._next_episode(window)

    assert found is not None
    assert found[0] == 4
    assert found[1].number == 2


def test_the_last_episode_of_a_season_moves_to_the_next_season():
    details = show(season(4, (1, "Only")), season(5, (1, "Later")))
    window = stub(details, (4, 1))

    found = movies_ui.MoviesWindow._next_episode(window)

    assert found is not None
    assert found[0] == 5


def test_a_movie_playing_has_no_next_episode():
    assert movies_ui.MoviesWindow._next_episode(
        stub(show(season(1, (1, "Pilot"))), None)
    ) is None


def test_a_missing_details_page_has_no_next_episode():
    assert movies_ui.MoviesWindow._next_episode(stub(None, (1, 1))) is None


def test_the_last_episode_of_the_show_has_no_next_episode():
    window = stub(show(season(1, (1, "Only"))), (1, 1))

    assert movies_ui.MoviesWindow._next_episode(window) is None


def test_a_details_summary_uses_the_season_tab_that_was_loaded():
    """This is what the page really holds: details has counts, not episodes."""
    summary = show(Season(number=4, name="Season 4", episode_count=2))
    window = stub(
        summary,
        (4, 1),
        cache={4: season(4, (1, "W.W.D.D."), (2, "Next one"))},
    )

    found = movies_ui.MoviesWindow._next_episode(window)

    assert found is not None
    assert found[1].number == 2


def test_a_summary_without_a_loaded_season_has_nothing_to_offer():
    window = stub(show(Season(number=4, episode_count=2)), (4, 1))

    assert movies_ui.MoviesWindow._next_episode(window) is None


def test_a_season_finale_names_the_next_season_to_fetch():
    details = show(
        Season(number=1, episode_count=2),
        Season(number=2, episode_count=4),
    )
    window = stub(details, (1, 2), cache={1: season(1, (1, "Pilot"), (2, "Finale"))})

    assert movies_ui.MoviesWindow._next_season_number(window) == 2


def test_an_episode_with_more_to_come_fetches_no_season():
    details = show(Season(number=1, episode_count=2), Season(number=2, episode_count=1))
    window = stub(details, (1, 1), cache={1: season(1, (1, "Pilot"), (2, "Two"))})

    assert movies_ui.MoviesWindow._next_season_number(window) is None
