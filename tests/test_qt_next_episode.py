"""The Qt player's Next episode button.

The seasons a details page loads are what the target is read from: the details
call itself only returns season summaries, so a cache miss is the normal case
and these tests check both places.  Everything runs offscreen with a stub view,
so no Qt WebEngine and no network are needed.
"""

from __future__ import annotations

import os
import time

import pytest

pytest.importorskip("PySide6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMainWindow, QWidget  # noqa: E402

from lgl import qt_media, qt_player  # noqa: E402
from lgl.tmdb import Episode, Season  # noqa: E402

SHOW = qt_media.Card(
    key="tv:1399", title="W.W.D.D.", year="2011", kind="tv", tmdb_id=1399
)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def pump(app, seconds: float = 0.05) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def season(number: int, *episodes: tuple[int, str]) -> Season:
    return Season(
        number=number,
        episode_count=len(episodes),
        episodes=tuple(
            Episode(season=number, number=count, title=title)
            for count, title in episodes
        ),
    )


class StubView(QWidget):
    """A stand-in for QWebEngineView: records loads instead of networking."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.loaded: list[str] = []

    def setUrl(self, url) -> None:  # noqa: N802 - Qt's name
        self.loaded.append(url.toString())

    def page(self):
        return None


def stub_player(parent: QWidget) -> qt_player.EmbeddedPlayer:
    return qt_player.EmbeddedPlayer(parent, view_factory=StubView)


class Harness:
    """The real Media page with the stub player, in a plain window."""

    def __init__(self, app: QApplication) -> None:
        self.window = QMainWindow()
        self.page = qt_media.MediaPage(player_factory=stub_player)
        self.window.setCentralWidget(self.page)
        self.window.resize(1000, 700)
        self.window.show()
        pump(app)

    def load(self, *seasons: Season) -> None:
        """What the details page and a season tab leave behind."""
        self.page._detail_seasons = tuple(seasons)
        self.page._season_cache.clear()
        for item in seasons:
            if item.episodes:
                self.page._season_cache[item.number] = item


def test_the_target_is_the_following_episode(app):
    harness = Harness(app)
    harness.load(season(1, (1, "Pilot"), (2, "Next one")))
    harness.page._playing_episode = (1, 1)

    found = harness.page._next_episode()

    assert found is not None
    assert found[0] == 1
    assert found[1].number == 2


def test_a_summary_without_episodes_still_finds_the_next_one(app):
    """MediaDetails.seasons has no episodes; the cached season must be used."""
    harness = Harness(app)
    harness.load(Season(number=1, episode_count=2), season(1, (1, "Pilot"), (2, "Two")))
    harness.page._playing_episode = (1, 1)

    found = harness.page._next_episode()

    assert found is not None and found[1].number == 2


def test_playing_an_episode_offers_the_next_one(app):
    harness = Harness(app)
    harness.load(season(1, (1, "Pilot"), (2, "Next one")))
    first, second = harness.page._season_cache[1].episodes

    harness.page._play_tmdb(SHOW, 1, first)
    pump(app)

    assert harness.page.player.next_episode.isVisible()
    assert "S01E02" in harness.page.player.next_episode.toolTip()

    harness.page.player.next_episode.click()
    pump(app)

    assert harness.page._playing_episode == (1, 2)
    assert not harness.page.player.next_episode.isVisible(), "the last episode offered another"


def test_a_movie_never_offers_a_next_episode(app):
    harness = Harness(app)
    movie = qt_media.Card(
        key="movie:603", title="The Matrix", year="1999", kind="movie", tmdb_id=603
    )

    harness.page._play_tmdb(movie)
    pump(app)

    assert not harness.page.player.next_episode.isVisible()


def test_the_last_episode_of_the_show_offers_nothing(app):
    harness = Harness(app)
    harness.load(season(1, (1, "Only")))
    only = harness.page._season_cache[1].episodes[0]

    harness.page._play_tmdb(SHOW, 1, only)
    pump(app)

    assert not harness.page.player.next_episode.isVisible()


def test_a_season_finale_fetches_the_next_season(app):
    """The next episode may be in a season whose episodes were never loaded."""
    harness = Harness(app)
    harness.load(
        Season(number=1, episode_count=1),
        season(1, (1, "Finale")),
        Season(number=2, episode_count=1),
    )

    class Client:
        def __init__(self) -> None:
            self.asked: list[int] = []

        def season(self, _tmdb_id: int, number: int) -> Season:
            self.asked.append(number)
            return season(2, (1, "Later"))

    client = Client()
    harness.page._tmdb = client
    finale = harness.page._season_cache[1].episodes[0]

    harness.page._play_tmdb(SHOW, 1, finale)
    pump(app, 0.2)

    assert client.asked == [2]
    assert harness.page.player.next_episode.isVisible()
    assert "S02E01" in harness.page.player.next_episode.toolTip()


def test_the_button_hides_with_the_chrome_in_fullscreen(app):
    harness = Harness(app)
    harness.load(season(1, (1, "Pilot"), (2, "Next one")))
    harness.page._play_tmdb(SHOW, 1, harness.page._season_cache[1].episodes[0])
    pump(app)
    assert harness.page.player.next_episode.isVisible()

    harness.page.player.set_fullscreen(True)
    pump(app)

    assert not harness.page.player.next_episode.isVisible()
