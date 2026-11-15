"""Search is TMDB only; anime plays on ani.pm through the mapping list.

Everything runs offscreen with stub HTTP: the fake client answers both the
search and the single details read the anime rule needs, and ``anime_map`` is
stubbed so no mapping list is downloaded.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QPushButton, QWidget  # noqa: E402

from lgl import design_components as dc  # noqa: E402
from lgl import qt_media, qt_player  # noqa: E402
from lgl.settings import Settings  # noqa: E402
from lgl.tmdb import Episode, MediaItem  # noqa: E402

SHOW = qt_media.Card(
    key="tv:1429", title="Attack on Titan", year="2013", kind="tv", tmdb_id=1429
)
MOVIE = qt_media.Card(
    key="movie:603", title="The Matrix", year="1999", kind="movie", tmdb_id=603
)

#: TMDB's own answer for an anime: Japanese, and genre id 16.
ANIME = {
    "id": 1429,
    "original_language": "ja",
    "genres": [{"id": 16, "name": "Animation"}, {"id": 10759, "name": "Action"}],
}
NOT_ANIME = {
    "id": 1429,
    "original_language": "en",
    "genres": [{"id": 16, "name": "Animation"}],
}


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def pump(app, seconds: float = 0.6) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def wait_until(app, condition, seconds: float = 25.0) -> bool:
    """Pump until ``condition`` holds; the shared worker pool can be busy."""
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    app.processEvents()
    return bool(condition())


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


class FakeClient:
    """The two TMDB reads the media page uses here, without a network."""

    configured = True

    def __init__(self, items=(), document: dict | None = None) -> None:
        self.items = list(items)
        self.document = dict(document or {})
        self.searches: list[str] = []
        self.gets: list[str] = []

    def search(self, query: str, *, page: int = 1):
        self.searches.append(query)
        return list(self.items)

    def get(self, path: str, params=None):
        self.gets.append(path)
        return dict(self.document)


class Bomb:
    """Anything that must never be called during a search."""

    def __init__(self, *args, **kwargs) -> None:
        raise AssertionError("the search page must not touch ani.pm")

    def __getattr__(self, name):
        raise AssertionError("the search page must not touch ani.pm")


def page(app, tmp_path: Path, *, settings: Settings | None = None) -> qt_media.MediaPage:
    store = settings or Settings(tmp_path / "config.json", keyring=False)
    widget = qt_media.MediaPage(settings=store, cache_root=tmp_path, player_factory=stub_player)
    widget.resize(1200, 800)
    widget.show()
    pump(app, 0.1)
    return widget


# -- the search page --------------------------------------------------------


def test_search_queries_tmdb_and_nothing_else(app, tmp_path, monkeypatch):
    calls = {"anilist": 0, "mal": 0}

    def count_anilist(*_args, **_kwargs):
        calls["anilist"] += 1
        return 42

    class MalBomb:
        configured = True

        def search(self, *_args, **_kwargs):
            calls["mal"] += 1
            raise AssertionError("the search page must not query MyAnimeList")

    monkeypatch.setattr(qt_media.anipm, "AniPmClient", Bomb)
    monkeypatch.setattr(qt_media.streaming, "anilist_search", count_anilist)

    widget = page(app, tmp_path)
    widget._tmdb = FakeClient([
        MediaItem(kind="tv", tmdb_id=1429, title="Attack on Titan", year="2013"),
        MediaItem(kind="movie", tmdb_id=378064, title="A Silent Voice", year="2016"),
    ])
    widget._mal = MalBomb()
    widget.search.setText("attack on titan")
    widget._search_timer.stop()  # the debounce would ask a second time
    widget._run_search()
    assert wait_until(app, lambda: bool(widget._search_grid.cards()))

    assert widget._tmdb.searches == ["attack on titan"]
    cards = [poster.card for poster in widget._search_grid.cards()]
    assert [card.key for card in cards] == ["tv:1429", "movie:378064"]
    assert all(card.kind in ("movie", "tv") for card in cards)
    assert calls == {"anilist": 0, "mal": 0}
    note = widget.search_note.text()
    assert "2 results" in note
    assert "ani.pm" not in note and "from " not in note
    widget.deleteLater()


def test_search_results_are_one_card_per_tmdb_series(app, tmp_path, monkeypatch):
    monkeypatch.setattr(qt_media.anipm, "AniPmClient", Bomb)
    widget = page(app, tmp_path)
    widget._tmdb = FakeClient([
        MediaItem(kind="tv", tmdb_id=1429, title="Attack on Titan", year="2013"),
    ])
    widget._run_search()  # empty box: nothing is asked
    assert widget._tmdb.searches == []
    widget.search.setText("attack on titan")
    widget._search_timer.stop()
    widget._run_search()
    assert wait_until(app, lambda: len(widget._search_grid.cards()) == 1)
    assert widget._search_empty.isHidden()
    widget.deleteLater()


# -- the anime rule ---------------------------------------------------------


@pytest.mark.parametrize(
    "document,expected",
    [
        (ANIME, True),
        (NOT_ANIME, False),
        ({"original_language": "ja", "genre_ids": [16, 35]}, True),
        ({"original_language": "ja", "genre_ids": [35]}, False),
        ({"original_language": "ja"}, False),
        ({"genres": [{"id": 16, "name": "Animation"}]}, False),
        ({}, False),
        (None, False),
        ("ja 16", False),
    ],
)
def test_the_anime_rule_is_language_and_genre(document, expected):
    assert qt_media.is_anime(document) is expected


def test_the_anime_rule_reads_tmdb_genre_objects_and_ids():
    assert qt_media._genre_ids(ANIME) == {16, 10759}
    assert qt_media._genre_ids({"genre_ids": [16, "35", True]}) == {16}


# -- playback ---------------------------------------------------------------


def _resolve(monkeypatch, target):
    seen: dict[str, object] = {}

    def resolve(tmdb_kind, tmdb_id, season=1, episode=1, *, title="", **_kwargs):
        seen.update(
            kind=tmdb_kind, tmdb_id=tmdb_id, season=season,
            episode=episode, title=title,
        )
        return target

    monkeypatch.setattr(qt_media.anime_map, "tmdb_to_anilist", resolve)
    return seen


def test_a_confident_target_plays_that_entry_and_episode(app, tmp_path, monkeypatch):
    seen = _resolve(
        monkeypatch,
        qt_media.anime_map.PlayTarget(anilist_id=16498, episode=5, confident=True),
    )
    monkeypatch.setattr(qt_media.anipm, "AniPmClient", Bomb)
    widget = page(app, tmp_path)
    widget._tmdb = FakeClient(document=ANIME)

    widget._play_tmdb(SHOW, 2, Episode(season=2, number=5, title="Hero"))
    assert wait_until(app, lambda: bool(widget.player.view.loaded))

    assert seen == {
        "kind": "tv", "tmdb_id": 1429, "season": 2, "episode": 5,
        "title": "Attack on Titan",
    }
    assert widget.player.view.loaded[-1] == "https://ani.pm/embed/ani/16498/5/sub"
    assert widget.stack.currentWidget() is widget.player
    widget.deleteLater()


def test_an_unsure_target_asks_which_part_and_plays_the_pick(app, tmp_path, monkeypatch):
    parts = (
        qt_media.anime_map.PlayTarget(anilist_id=99147, episode=3, confident=True,
                                      reason="starts at TMDB episode 1"),
        qt_media.anime_map.PlayTarget(anilist_id=104578, episode=3, confident=True,
                                      reason="starts at TMDB episode 13"),
    )
    _resolve(
        monkeypatch,
        qt_media.anime_map.PlayTarget(0, 15, False, "two entries", parts),
    )
    monkeypatch.setattr(qt_media.anipm, "AniPmClient", Bomb)

    asked: list[list[str]] = []

    class Recorder:
        def __init__(self, labels, title="", parent=None) -> None:
            asked.append([str(text) for text, _target in labels])
            self._target = labels[-1][1]

        def exec(self) -> int:
            return QDialog.Accepted

        def chosen(self):
            return self._target

    monkeypatch.setattr(qt_media, "WhichPartDialog", Recorder)
    widget = page(app, tmp_path)
    widget._tmdb = FakeClient(document=ANIME)

    widget._play_tmdb(SHOW, 3, Episode(season=3, number=15, title="The Other Side"))
    assert wait_until(app, lambda: bool(widget.player.view.loaded))

    assert asked == [["Part 1/2", "Part 2/2"]]
    assert widget.player.view.loaded[-1] == "https://ani.pm/embed/ani/104578/3/sub"
    widget.deleteLater()


def test_no_mapping_falls_back_to_the_anilist_title_search(app, tmp_path, monkeypatch):
    seen = _resolve(monkeypatch, None)
    looked: list[str] = []

    def anilist_search(title, **_kwargs):
        looked.append(title)
        return 20954

    monkeypatch.setattr(qt_media.streaming, "anilist_search", anilist_search)
    monkeypatch.setattr(qt_media.anipm, "AniPmClient", Bomb)
    widget = page(app, tmp_path)
    widget._tmdb = FakeClient(document=ANIME)

    widget._play_tmdb(MOVIE)
    assert wait_until(app, lambda: bool(widget.player.view.loaded))

    assert seen["kind"] == "movie" and seen["season"] == 1 and seen["episode"] == 1
    assert looked == ["The Matrix"]
    assert widget.player.view.loaded[-1] == "https://ani.pm/embed/ani/20954/1/sub"
    widget.deleteLater()


def test_nothing_matched_reports_it_and_plays_nothing(app, tmp_path, monkeypatch):
    _resolve(monkeypatch, None)
    monkeypatch.setattr(qt_media.streaming, "anilist_search", lambda *_a, **_k: None)
    monkeypatch.setattr(qt_media.anipm, "AniPmClient", Bomb)
    widget = page(app, tmp_path)
    widget._tmdb = FakeClient(document=ANIME)
    notes: list[str] = []
    widget._note_error = notes.append

    widget._play_tmdb(MOVIE)
    assert wait_until(app, lambda: bool(notes))

    assert notes and "No AniList entry" in notes[0]
    assert widget.player.view.loaded == []
    widget.deleteLater()


def test_a_non_anime_title_keeps_the_chosen_provider(app, tmp_path, monkeypatch):
    _resolve(monkeypatch, qt_media.anime_map.PlayTarget(1, 1, True))
    monkeypatch.setattr(qt_media.anipm, "AniPmClient", Bomb)
    widget = page(app, tmp_path)
    widget._tmdb = FakeClient(document=NOT_ANIME)

    widget._play_tmdb(MOVIE)
    assert wait_until(app, lambda: bool(widget.player.view.loaded))

    url = widget.player.view.loaded[-1]
    assert "ani.pm" not in url
    assert "603" in url
    widget.deleteLater()


def test_a_build_without_a_tmdb_client_plays_the_provider_route(app):
    widget = qt_media.MediaPage(None, cache_root=None, player_factory=stub_player)
    try:
        widget._play_tmdb(MOVIE)
        pump(app, 0.1)
        assert "603" in widget.player.view.loaded[-1]
    finally:
        widget.deleteLater()


# -- the sheet --------------------------------------------------------------


def _target(anilist_id: int, reason: str = "") -> object:
    return qt_media.anime_map.PlayTarget(anilist_id=anilist_id, episode=1,
                                         confident=True, reason=reason)


def test_which_part_dialog_follows_the_sheet_rules(app):
    parts = [("Attack on Titan", _target(99147)), ("Part 2", _target(104578))]
    dialog = qt_media.WhichPartDialog(parts, "Attack on Titan")
    dialog.show()
    pump(app, 0.1)
    try:
        assert dialog.objectName() == "sheet"
        layout = dialog.layout()
        margins = layout.contentsMargins()
        assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == (24, 24, 24, 24)
        assert dc.DIALOG_PADDING == 24 and dc.DIALOG_GAP == 16
        assert layout.spacing() == 16

        buttons = dialog.buttons()
        assert [button.text() for button in buttons] == ["Attack on Titan", "Part 2"]
        assert buttons[0].isDefault() and not buttons[1].isDefault()
        assert dialog.focusWidget() is not None
        assert dialog.isAncestorOf(dialog.focusWidget())

        QTest.keyClick(dialog, Qt.Key_Escape)
        pump(app, 0.1)
        assert not dialog.isVisible()
        assert dialog.result() == QDialog.Rejected
        assert dialog.chosen() is None
    finally:
        dialog.close()


def test_which_part_dialog_returns_the_picked_part(app):
    parts = [("Attack on Titan", _target(99147)), ("Part 2", _target(104578))]
    dialog = qt_media.WhichPartDialog(parts, "Attack on Titan")
    dialog.show()
    pump(app, 0.1)
    try:
        QTest.mouseClick(dialog.buttons()[1], Qt.LeftButton)
        pump(app, 0.1)
        assert dialog.chosen() is parts[1][1]
        assert dialog.result() == QDialog.Accepted
    finally:
        dialog.close()


# -- saved items ------------------------------------------------------------


def test_old_saved_items_still_open(app, tmp_path):
    from lgl import media_history

    store = Settings(tmp_path / "config.json", keyring=False)
    store.set(qt_media.WATCHLIST_KEY, [SHOW.key])
    media_history.record(
        store, key=MOVIE.key, title=MOVIE.title, poster_url="", year=MOVIE.year,
    )
    widget = page(app, tmp_path, settings=store)
    widget._tmdb_items = [SHOW, MOVIE]
    widget._render_list()
    widget._render_history()

    assert [poster.card.key for poster in widget._list_grid.cards()] == [SHOW.key]
    assert [poster.card.key for poster in widget._history_grid.cards()] == [MOVIE.key]

    widget.open_card(widget._list_grid.cards()[0].card)
    pump(app, 0.1)
    assert widget._detail_card is not None and widget._detail_card.key == SHOW.key
    assert widget.stack.currentWidget() is widget._detail_page

    widget.open_card(widget._history_grid.cards()[0].card)
    pump(app, 0.1)
    assert widget._detail_card is not None and widget._detail_card.key == MOVIE.key
    assert widget.stack.currentWidget() is widget._detail_page
    widget.close()
    widget.deleteLater()


def test_the_search_page_shows_one_tmdb_count_line_only(app):
    """No \"N from ani.pm · N from TMDB\" line is built anywhere."""
    widget = qt_media.MediaPage(None, cache_root=None)
    try:
        assert "TMDB" in widget.search_note.text()
        assert "ani.pm" not in widget.search_note.text()
        labels = [child.text() for child in widget.findChildren(QPushButton)]
        assert not any("AniList" in text for text in labels)
    finally:
        widget.deleteLater()
