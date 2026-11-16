"""TMDB -> AniList routing on the Windows play path.

Agent C's shared module (``lgl/anime_map.py`` + the ``tmdb`` group API) is
built in parallel, so every contract function this code calls is stubbed here.
The tests pin the routing contract, not C's implementation: confident,
not confident, no answer, non-anime, another provider, and the verified-anime
provider default.  No network: the TMDB client is a stub and the player is a
stub view.
"""

from __future__ import annotations

import os
import time

import pytest

pytest.importorskip("PySide6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QUrl, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QWidget  # noqa: E402

from lgl import anime_map, anipm, qt_media, qt_player, streaming  # noqa: E402
from lgl.settings import Settings  # noqa: E402


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


class Tmdb:
    """The public client surface the play path touches, and nothing else."""

    def __init__(self, document: dict | None = None) -> None:
        self.document = document if document is not None else {
            "original_language": "ja",
            "genres": [{"id": 16}, {"id": 18}],
        }
        self.paths: list[str] = []

    def get(self, path: str, params=None) -> dict:
        self.paths.append(path)
        return self.document


class Verdict:
    def __init__(self, *, is_anime: bool = True, verified: bool = True) -> None:
        self.is_anime = is_anime
        self.verified = verified
        self.mal_id = None
        self.anilist_ids = ()
        self.reason = ""


class Target:
    def __init__(self, anilist_id: int = 0, episode: int = 1, *,
                 confident: bool = True, candidates=()) -> None:
        self.anilist_id = anilist_id
        self.episode = episode
        self.confident = confident
        self.candidates = tuple(candidates)


class Page:
    """A MediaPage wired for routing only: no history, no network, no player."""

    def __init__(self, app, *, settings=None, provider="ani", tmdb=None) -> None:
        self.app = app
        self.settings = settings
        self.widget = qt_media.MediaPage(
            self.settings,
            player_factory=lambda parent: qt_player.EmbeddedPlayer(
                parent, view_factory=StubView
            ),
        )
        self.widget._tmdb = tmdb or Tmdb()
        self.widget._provider = lambda: provider
        self.widget._language = lambda: "sub"
        self.widget._record_history = lambda card: None
        self.urls: list[str] = []
        self.widget.play_embedded = self._play_embedded
        self.widget._refresh_next_episode = lambda: None
        self.plain: list[tuple] = []
        self._plain = self.widget._play_tmdb_plain

        def plain(card, provider_key, season=None, episode=None):
            self.plain.append((card, provider_key, season, episode))

        self.widget._play_tmdb_plain = plain

    def _play_embedded(self, url: str, title: str = "") -> None:
        self.urls.append(url)

    def close(self) -> None:
        self.widget.deleteLater()


TV = qt_media.Card(key="tv:1396", title="Breaking Bad", kind="tv", tmdb_id=1396)


def episode(number: int = 3) -> object:
    return qt_media.tmdb.Episode(season=2, number=number, title="Bit by a Dead Bee")


@pytest.fixture()
def stubbed(monkeypatch):
    """Every contract call recorded, and the verdict/target the test sets."""
    calls: dict[str, list] = {"is_anime": [], "mapped": [], "searched": []}
    state = {"verdict": Verdict(), "target": Target(anilist_id=42, episode=5)}

    def is_anime(kind, tmdb_id, *, original_language="", genre_ids=()):
        calls["is_anime"].append((kind, tmdb_id, original_language, tuple(genre_ids)))
        return state["verdict"]

    def mapped(kind, tmdb_id, season=1, ep=1, *, title=""):
        calls["mapped"].append((kind, tmdb_id, season, ep, title))
        return state["target"]

    monkeypatch.setattr(anime_map, "is_anime", is_anime, raising=False)
    monkeypatch.setattr(anime_map, "tmdb_to_anilist", mapped, raising=False)
    monkeypatch.setattr(anipm, "AniPmClient", lambda *a, **k: _NoTitles())
    return calls, state


class _NoTitles:
    def title(self, _anilist_id):
        class Named:
            title = ""

        return Named()


# -- confident --------------------------------------------------------------

def test_a_confident_mapping_plays_the_anilist_episode(app, stubbed):
    calls, _state = stubbed
    page = Page(app)
    page.widget._play_tmdb(TV, 2, episode(3))
    assert wait_for(app, lambda: page.urls)

    assert page.urls == [streaming.ani_url(42, 5, language="sub")]
    assert calls["mapped"] == [("tv", 1396, 2, 3, "Breaking Bad")]
    assert calls["is_anime"] == [("tv", 1396, "ja", (16, 18))]
    assert page.plain == [], "a confident anime must not fall back to the provider"
    assert page.widget._playing_episode == (2, 3), (
        "next-episode bookkeeping must stay in TMDB numbers"
    )
    page.close()


def test_the_details_document_is_fetched_once_per_card(app, stubbed):
    _calls, _state = stubbed
    page = Page(app)
    page.widget._play_tmdb(TV, 2, episode(3))
    assert wait_for(app, lambda: page.urls)
    page.widget._play_tmdb(TV, 2, episode(4))
    assert wait_for(app, lambda: len(page.urls) == 2)
    assert page.widget._tmdb.paths == ["/tv/1396"]
    page.close()


# -- not confident ----------------------------------------------------------

def test_an_unsure_mapping_asks_which_part_and_plays_the_pick(app, stubbed, monkeypatch):
    _calls, state = stubbed
    state["target"] = Target(
        confident=False,
        candidates=(Target(anilist_id=11, episode=1), Target(anilist_id=22, episode=1)),
    )
    opened: list[dict] = []

    class FakeDialog:
        def __init__(self, show_title, candidates, *, parent=None) -> None:
            self.show_title = show_title
            self.candidates = list(candidates)
            self.picked = None
            self.titles: list[str] = []
            opened.append({"title": show_title, "count": len(self.candidates)})

        def set_titles(self, titles) -> None:
            self.titles = list(titles)

        def isVisible(self) -> bool:  # noqa: N802 - Qt's name
            return True

        def exec(self):
            self.picked = self.candidates[1]
            return qt_media.QDialog.Accepted

        def choice(self):
            return self.picked

    monkeypatch.setattr(qt_media, "WhichPartDialog", FakeDialog, raising=False)
    page = Page(app)
    page.widget._play_tmdb(TV, 2, episode(3))
    assert wait_for(app, lambda: page.urls)

    assert opened == [{"title": "Breaking Bad", "count": 2}]
    assert page.urls == [streaming.ani_url(22, 1, language="sub")], (
        "the pick decides which AniList entry plays"
    )
    page.close()


def test_cancelling_which_part_plays_nothing(app, stubbed, monkeypatch):
    _calls, state = stubbed
    state["target"] = Target(confident=False, candidates=(Target(1, 1), Target(2, 1)))

    class Cancelled:
        def __init__(self, *a, **k) -> None:
            self.picked = None

        def set_titles(self, _titles) -> None:
            pass

        def isVisible(self) -> bool:  # noqa: N802
            return True

        def exec(self):
            return qt_media.QDialog.Rejected

        def choice(self):
            return None

    monkeypatch.setattr(qt_media, "WhichPartDialog", Cancelled, raising=False)
    page = Page(app)
    page.widget._play_tmdb(TV, 2, episode(3))
    pump(app, 0.4)

    assert page.urls == [] and page.plain == []
    page.close()


# -- no mapping -------------------------------------------------------------

def test_no_mapping_uses_the_anilist_title_search(app, stubbed, monkeypatch):
    _calls, state = stubbed
    state["target"] = None
    searches: list[str] = []

    def search(title):
        searches.append(title)
        return 777

    monkeypatch.setattr(streaming, "anilist_search", search, raising=False)
    page = Page(app)
    page.widget._play_tmdb(TV, 2, episode(3))
    assert wait_for(app, lambda: page.urls)

    assert searches == ["Breaking Bad"]
    assert page.urls == [streaming.ani_url(777, 3, language="sub")]
    assert page.plain == []
    page.close()


def test_no_mapping_and_no_search_hit_keeps_todays_path(app, stubbed, monkeypatch):
    _calls, state = stubbed
    state["target"] = None
    monkeypatch.setattr(streaming, "anilist_search", lambda _t: None, raising=False)
    page = Page(app)
    page.widget._play_tmdb(TV, 2, episode(3))
    assert wait_for(app, lambda: page.plain)

    assert page.plain and page.plain[0][0] is TV
    assert page.urls == []
    page.close()


# -- unaffected paths -------------------------------------------------------

def test_a_non_anime_title_plays_exactly_as_before(app, stubbed):
    calls, state = stubbed
    state["verdict"] = Verdict(is_anime=False, verified=False)
    page = Page(app)
    page.widget._play_tmdb(TV, 2, episode(3))
    assert wait_for(app, lambda: page.plain)

    assert calls["mapped"] == [], "a non-anime title never touches the mapping"
    assert page.urls == []
    page.close()


def test_another_provider_is_not_routed_at_all(app, stubbed):
    calls, _state = stubbed
    page = Page(app, provider="vidy")
    page.widget._play_tmdb(TV, 2, episode(3))
    pump(app, 0.3)

    assert calls["is_anime"] == []
    assert page.widget._tmdb.paths == [], "no details request for other providers"
    assert page.plain and page.urls == []
    page.close()


# -- verified default -------------------------------------------------------

def test_a_verified_anime_makes_anipm_the_anime_default(app, stubbed, tmp_path):
    _calls, state = stubbed
    state["verdict"] = Verdict(is_anime=True, verified=True)
    settings = Settings(tmp_path / "settings.json", keyring=False)
    page = Page(app, settings=settings)
    page.widget._play_tmdb(TV, 2, episode(3))
    assert wait_for(app, lambda: page.urls)

    assert settings.get("anime_streaming_provider") == streaming.ANI_PROVIDER
    page.close()


def test_a_saved_pick_is_not_overridden(app, stubbed, tmp_path):
    _calls, state = stubbed
    state["verdict"] = Verdict(is_anime=True, verified=True)
    settings = Settings(tmp_path / "settings.json", keyring=False)
    settings.set("anime_streaming_provider", "toflix")
    page = Page(app, settings=settings)
    page.widget._play_tmdb(TV, 2, episode(3))
    assert wait_for(app, lambda: page.urls)

    assert settings.get("anime_streaming_provider") == "toflix"
    page.close()


# -- the dialog itself ------------------------------------------------------

def test_the_which_part_sheet_uses_the_shared_frame(app):
    from lgl import design_components
    from lgl.qt_which_part import WhichPartDialog, candidate_label

    dialog = WhichPartDialog("Breaking Bad", [Target(1, 1), Target(2, 1)])
    layout = dialog.layout()
    assert dialog.objectName() == "sheet"
    assert layout.contentsMargins().top() == design_components.DIALOG_PADDING
    assert layout.spacing() == design_components.DIALOG_GAP
    labels = [button.text() for button in dialog.buttons()]
    assert labels == ["Part 1", "Part 2"], "no title yet means the numbered fallback"

    dialog.set_titles(["Season one", ""])
    assert [button.text() for button in dialog.buttons()] == [
        candidate_label(0, "Season one"), "Part 2",
    ]

    dialog.buttons()[0].click()
    pump(app)
    assert dialog.result() == QDialog.Accepted
    assert dialog.choice().anilist_id == 1


def test_escape_closes_the_which_part_sheet(app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from lgl.qt_which_part import WhichPartDialog

    dialog = WhichPartDialog("Show", [Target(1, 1), Target(2, 1)])
    dialog.show()
    pump(app)
    QTest.keyClick(dialog, Qt.Key_Escape)
    pump(app)
    assert dialog.result() == QDialog.Rejected
    assert dialog.choice() is None

# -- real mapping records, real conversion ---------------------------------

#: The Fribb list's shape, with the Attack on Titan records as the list has
#: them: one entry per TMDB season, and season 3 split at episode 13.
AOT_TMDB = 1429
AOT_RECORDS = (
    {"anilist_id": 16498, "type": "TV", "themoviedb_id": {"tv": AOT_TMDB},
     "season": {"tmdb": 1}, "episode_offset": {"tmdb": 0}},
    {"anilist_id": 99147, "type": "TV", "themoviedb_id": {"tv": AOT_TMDB},
     "season": {"tmdb": 3}, "episode_offset": {"tmdb": 0}},
    {"anilist_id": 104578, "type": "TV", "themoviedb_id": {"tv": AOT_TMDB},
     "season": {"tmdb": 3}, "episode_offset": {"tmdb": 12}},
    {"anilist_id": 131681, "type": "TV", "themoviedb_id": {"tv": AOT_TMDB},
     "season": {"tmdb": 4}, "episode_offset": {"tmdb": 0}},
)

AOT = qt_media.Card(
    key=f"tv:{AOT_TMDB}", title="Attack on Titan", kind="tv", tmdb_id=AOT_TMDB
)


@pytest.fixture()
def real_mapping(monkeypatch):
    """The real module with the mapping records injected, and no sheet allowed."""
    monkeypatch.setattr(
        anime_map, "load_mapping",
        lambda **kwargs: anime_map.MappingList(records=AOT_RECORDS),
        raising=False,
    )

    class Never:
        def __init__(self, *args, **kwargs) -> None:
            raise AssertionError("the Which-part sheet must not open here")

    monkeypatch.setattr(qt_media, "WhichPartDialog", Never, raising=False)
    return None


@pytest.mark.parametrize(
    "season, number, anilist_id, anilist_episode",
    [
        (3, 1, 99147, 1),    # season 3's first entry
        (3, 13, 104578, 1),  # season 3 splits here; offset 12 restarts at 1
        (3, 22, 104578, 10),
        (1, 5, 16498, 5),
    ],
)
def test_a_real_mapping_plays_without_asking(app, real_mapping, season, number,
                                             anilist_id, anilist_episode):
    page = Page(app)
    page.widget._play_tmdb(AOT, season, episode(number))
    assert wait_for(app, lambda: page.urls)

    assert page.urls == [
        streaming.ani_url(anilist_id, anilist_episode, language="sub")
    ], f"S{season}E{number} should play AniList {anilist_id} episode {anilist_episode}"
    assert page.plain == []
    page.close()


def test_watch_now_without_a_season_uses_season_one_episode_one(app, real_mapping):
    """The hero's Play has no season chosen: S1E1, still no sheet."""
    page = Page(app)
    page.widget._play_card(AOT)
    assert wait_for(app, lambda: page.urls)

    assert page.urls == [streaming.ani_url(16498, 1, language="sub")]
    page.close()
