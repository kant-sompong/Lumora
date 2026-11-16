"""TMDB episode groups on the Windows TV page.

The group API (``TmdbClient.episode_groups``, ``choose_layout``,
``group_as_seasons``, ``group_episode_to_target``) is Agent C's shared module,
built in parallel, so every one of those calls is stubbed here.  What is tested
is this page's behaviour: the layout is offered only when ``choose_layout``
says so, the toggle switches both ways, a group episode plays the original
season and episode, a failing groups call changes nothing, and a card that has
no groups at all still opens exactly as before.
"""

from __future__ import annotations

import os
import time
import types

import pytest

pytest.importorskip("PySide6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QUrl, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from lgl import qt_media, qt_player, tmdb  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def pump(app, seconds: float = 0.05) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def wait_for(app, predicate, seconds: float = 3.0) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class StubPage(QObject):
    fullScreenRequested = Signal(object)

    def triggerAction(self, _action) -> None:
        pass


class StubView(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._page = StubPage()
        self.loaded: list[str] = []

    def page(self) -> StubPage:
        return self._page

    def setUrl(self, url) -> None:  # noqa: N802 - Qt's name
        self.loaded.append(url.toString() if isinstance(url, QUrl) else str(url))

    def stop(self) -> None:
        pass


def episode(season: int, number: int, title: str) -> tmdb.Episode:
    return tmdb.Episode(
        season=season, number=number, title=title, air_date="2020-01-01",
        runtime=42,
    )


def season(number: int, name: str, episodes=()) -> tmdb.Season:
    return tmdb.Season(
        number=number, name=name, episode_count=len(episodes),
        episodes=tuple(episodes),
    )


STANDARD = (
    season(1, "Season 1", (episode(1, 1, "Pilot"), episode(1, 2, "Cat in the Bag"))),
    season(2, "Season 2", (episode(2, 1, "Seven Thirty-Seven"),)),
)

#: A made-up four-cour layout: the rows and numbering are stand-in data.
GROUP_SEASONS = (
    season(1, "Cour 1", (episode(1, 1, "Breakfast"), episode(1, 2, "Dog")),),
    season(2, "Cour 2", (episode(2, 1, "Horse"),)),
    season(3, "Cour 3", (episode(3, 1, "Bag"),)),
    season(4, "Cour 4", (episode(4, 1, "Box Cutter"),)),
)
INFO = types.SimpleNamespace(
    id="grp-1", name="Cour", description="", type=3, group_count=4,
    episode_count=5, network="",
)
GROUP = types.SimpleNamespace(id="grp-1", name="Cour", type=3, groups=GROUP_SEASONS)


class Show:
    """A TMDB client stub: the seasons page's details, and the group calls."""

    def __init__(self, *, groups=None, layout="cour", fail_groups=False) -> None:
        self.group_calls: list[str] = []
        self.group_requests: list[str] = []
        self._groups = groups if groups is not None else [INFO]
        self._layout = layout
        self._fail_groups = fail_groups

    # details / seasons -----------------------------------------------------
    def details(self, _kind: str, _tmdb_id: int) -> object:
        return types.SimpleNamespace(
            seasons=(season(0, "Specials"), *STANDARD),
            imdb_id="", rating=None, vote_count=0, title="Show",
        )

    def season(self, _tmdb_id: int, number: int) -> tmdb.Season:
        for item in STANDARD:
            if item.number == number:
                return item
        return season(number, f"Season {number}")

    def images(self, *_args, **_kwargs) -> object:
        return types.SimpleNamespace(backdrop_url="", logo_url="", poster_url="")

    def get(self, _path: str, params=None) -> dict:
        return {}

    # groups ----------------------------------------------------------------
    def episode_groups(self, _tmdb_id: int) -> list:
        self.group_requests.append("list")
        if self._fail_groups:
            raise RuntimeError("group list unavailable")
        return list(self._groups)

    def episode_group(self, group_id: str) -> object:
        self.group_requests.append(group_id)
        self.group_calls.append(group_id)
        return GROUP


TV = qt_media.Card(key="tv:1396", title="Breaking Bad", kind="tv", tmdb_id=1396)
MOVIE = qt_media.Card(key="movie:603", title="The Matrix", kind="movie", tmdb_id=603)


@pytest.fixture()
def group_api(monkeypatch):
    """Stub the four contract functions, recording what the page asks for."""
    calls: dict[str, list] = {"choose": [], "as_seasons": [], "target": []}
    state = {"layout": INFO}

    def choose_layout(standard, groups):
        calls["choose"].append((tuple(standard), tuple(groups)))
        return state["layout"]

    def group_as_seasons(group):
        calls["as_seasons"].append(group.id)
        return list(GROUP_SEASONS)

    def group_episode_to_target(group, season_order, episode_number):
        calls["target"].append((group.id, season_order, episode_number))
        if season_order == 1 and episode_number == 1:
            return (2, 5)
        return None

    monkeypatch.setattr(tmdb, "choose_layout", choose_layout, raising=False)
    monkeypatch.setattr(tmdb, "group_as_seasons", group_as_seasons, raising=False)
    monkeypatch.setattr(tmdb, "group_episode_to_target", group_episode_to_target,
                        raising=False)
    return calls, state


def make_page(app, client: Show) -> qt_media.MediaPage:
    page = qt_media.MediaPage(
        None,
        player_factory=lambda parent: qt_player.EmbeddedPlayer(
            parent, view_factory=StubView
        ),
    )
    page._tmdb = client
    page._note_error = lambda _text: None
    return page


def open_show(app, client: Show, *, expect_layout: bool = True) -> qt_media.MediaPage:
    page = make_page(app, client)
    page.open_card(TV)
    wait_for(app, lambda: bool(page.findChildren(qt_media.EpisodeRow)))
    if expect_layout:
        wait_for(app, lambda: not page.layout_toggle.isHidden())
    pump(app, 0.1)
    return page


def season_numbers(page: qt_media.MediaPage) -> list[int]:
    return [season.number for season in page.season_combo.panel.seasons()]


def row_titles(page: qt_media.MediaPage) -> list[str]:
    return [row.title_label.text() for row in page.findChildren(qt_media.EpisodeRow)]


# -- offered, shown, switched ----------------------------------------------

def test_an_alternate_layout_is_offered_and_shown(app, group_api):
    calls, _state = group_api
    client = Show()
    page = open_show(app, client)

    assert calls["choose"], "choose_layout must decide, not the page"
    assert client.group_requests == ["list", "grp-1"]
    assert page.layout_toggle.isHidden() is False
    assert page.layout_toggle.text() == "Show TMDB's default seasons"
    assert season_numbers(page) == [1, 2, 3, 4], "the group's four cours"
    assert row_titles(page) == ["Breakfast", "Dog"], "rows come from the group"
    page.deleteLater()


def test_the_toggle_switches_between_the_two_layouts(app, group_api):
    _calls, _state = group_api
    page = open_show(app, Show())
    assert season_numbers(page) == [1, 2, 3, 4]

    page.layout_toggle.click()
    pump(app, 0.2)
    assert page.layout_toggle.text() == "Show Cour seasons"
    assert season_numbers(page) == [1, 2], "back to TMDB's own seasons"
    assert row_titles(page) == ["Pilot", "Cat in the Bag"]

    page.layout_toggle.click()
    pump(app, 0.2)
    assert page.layout_toggle.text() == "Show TMDB's default seasons"
    assert season_numbers(page) == [1, 2, 3, 4]
    page.deleteLater()


def test_the_choice_is_remembered_for_the_session(app, group_api):
    calls, _state = group_api
    page = open_show(app, Show())
    page.layout_toggle.click()
    pump(app, 0.2)
    assert season_numbers(page) == [1, 2]

    # Another card, then back: the show remembers its alternate layout.
    page.open_card(MOVIE)
    pump(app, 0.1)
    page.open_card(TV)
    wait_for(app, lambda: bool(page.findChildren(qt_media.EpisodeRow)))
    wait_for(app, lambda: not page.layout_toggle.isHidden())
    pump(app, 0.2)
    assert page.season_combo.isHidden() is False
    assert season_numbers(page) == [1, 2], "the default layout stays chosen"
    page.deleteLater()


def test_a_group_season_can_be_picked_from_the_dropdown(app, group_api):
    _calls, _state = group_api
    page = open_show(app, Show())
    assert row_titles(page) == ["Breakfast", "Dog"]

    page.season_combo.panel._choose(3)
    pump(app, 0.2)
    assert row_titles(page) == ["Bag"], "the third cour's own episodes"
    page.deleteLater()


# -- played with the original numbers --------------------------------------

def test_a_group_episode_plays_the_original_season_and_episode(app, group_api):
    calls, _state = group_api
    page = open_show(app, Show())
    played: list[tuple] = []
    page._play_tmdb = lambda card, s=None, e=None: played.append((card, s, e))

    rows = page.findChildren(qt_media.EpisodeRow)
    rows[0].clicked.emit()
    pump(app, 0.1)

    assert calls["target"] == [("grp-1", 1, 1)]
    assert played and played[0][1] == 2 and played[0][2].number == 5, (
        "the group row must play TMDB's original (2, 5)"
    )
    page.deleteLater()


def test_a_group_episode_without_an_original_shows_the_no_stream_note(app, group_api):
    _calls, _state = group_api
    page = open_show(app, Show())
    notes: list[str] = []
    page._note_error = lambda text: notes.append(text)
    page._play_tmdb = lambda *a, **k: pytest.fail("nothing should play")

    rows = page.findChildren(qt_media.EpisodeRow)
    rows[1].clicked.emit()
    pump(app, 0.1)

    assert notes and "no original season" in notes[0]
    page.deleteLater()


# -- nothing to offer ------------------------------------------------------

def test_a_show_without_a_group_gets_no_toggle_and_no_second_call(app, group_api):
    calls, state = group_api
    state["layout"] = None
    client = Show()
    page = open_show(app, client)

    assert calls["choose"], "the list is still asked for"
    assert client.group_requests == ["list"], "no group is loaded when none fits"
    assert page.layout_toggle.isVisible() is False
    assert season_numbers(page) == [1, 2]
    page.deleteLater()


def test_a_failing_groups_call_falls_back_to_the_standard_seasons(app, group_api):
    _calls, _state = group_api
    client = Show(fail_groups=True)
    page = open_show(app, client, expect_layout=False)

    assert page.layout_toggle.isHidden() is True
    assert season_numbers(page) == [1, 2]
    assert row_titles(page) == ["Pilot", "Cat in the Bag"]
    page.deleteLater()


def test_a_movie_card_still_opens_without_any_groups_call(app, group_api):
    _calls, _state = group_api
    client = Show()
    page = make_page(app, client)
    page.open_card(MOVIE)
    pump(app, 0.2)

    assert client.group_requests == []
    assert page.layout_toggle.isHidden() is True
    page.deleteLater()
