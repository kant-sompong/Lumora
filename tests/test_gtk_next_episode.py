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


def stub(details, playing):
    return types.SimpleNamespace(_current_details=details, _playing_episode=playing)


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
