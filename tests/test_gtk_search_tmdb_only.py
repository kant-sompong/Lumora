"""GTK Movies & TV: search is TMDB only; anime plays on ani.pm.

These skip themselves when GTK 3 or a display is missing (the Linux CI job
installs Xvfb and runs them); they were not run locally, where this checkout has
no ``gi`` and no ``xvfb-run``.
"""

from __future__ import annotations

import time

import pytest

gi = pytest.importorskip("gi")

try:
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
except (ValueError, ImportError):  # pragma: no cover - GTK 3 typelib missing
    pytest.skip("GTK 3 is not installed", allow_module_level=True)

movies_ui = pytest.importorskip("lgl.movies_ui")

from lgl import tmdb  # noqa: E402

MOVIE = tmdb.MediaItem(kind="movie", tmdb_id=603, title="The Matrix", year="1999")
SHOW = tmdb.MediaItem(kind="tv", tmdb_id=1429, title="Attack on Titan", year="2013")
EPISODE = tmdb.Episode(season=2, number=5, title="Hero")

#: TMDB's own answer for an anime: Japanese, and genre id 16.
ANIME = {
    "id": 1429,
    "original_language": "ja",
    "genres": [{"id": 16, "name": "Animation"}],
}
NOT_ANIME = {
    "id": 1429,
    "original_language": "en",
    "genres": [{"id": 16, "name": "Animation"}],
}


class Store(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)

    def set(self, key, value):
        self[key] = value

    def save(self):
        return True


class Bomb:
    """An ani.pm client that raises the moment anything asks it a question."""

    def __getattr__(self, name):
        raise AssertionError("the search page must not touch ani.pm")


def has_display() -> bool:
    return bool(Gtk.init_check()[0])


needs_display = pytest.mark.skipif(not has_display(), reason="no display for GTK")


def pump(seconds: float = 0.6) -> None:
    end = time.time() + seconds
    while time.time() < end:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        time.sleep(0.005)


def wait_until(condition, seconds: float = 20.0) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        if condition():
            return True
        time.sleep(0.01)
    return bool(condition())


class FakeClient:
    configured = True

    def __init__(self, items=(), document=None) -> None:
        self.items = list(items)
        self.document = dict(document or {})
        self.searches: list[str] = []
        self.gets: list[str] = []

    def search(self, query, **_kwargs):
        self.searches.append(query)
        return list(self.items)

    def get(self, path, params=None):
        self.gets.append(path)
        return dict(self.document)

    # The window's home loader asks for these on show; it gets nothing.
    def trending(self, kind="movie", **_kwargs):
        return []

    def popular(self, kind="movie", **_kwargs):
        return []


@pytest.fixture
def window(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(movies_ui, "TmdbClient", lambda *_a, **_k: client)
    monkeypatch.setattr(movies_ui.anipm, "AniPmClient", Bomb)
    offscreen = Gtk.OffscreenWindow()
    media = movies_ui.MoviesWindow(offscreen, Store({"anime_streaming_provider": "ani"}))
    offscreen.add(media)
    offscreen.show_all()
    pump(0.3)
    try:
        yield media
    finally:
        offscreen.destroy()
        pump(0.05)


# -- the anime rule ---------------------------------------------------------


@pytest.mark.parametrize(
    "document,expected",
    [
        (ANIME, True),
        (NOT_ANIME, False),
        ({"original_language": "ja", "genre_ids": [16, 35]}, True),
        ({"original_language": "ja", "genre_ids": [35]}, False),
        ({"genres": [{"id": 16}]}, False),
        ({}, False),
        (None, False),
    ],
)
def test_the_anime_rule_is_language_and_genre(document, expected):
    assert movies_ui.is_anime(document) is expected


# -- the search page --------------------------------------------------------


@needs_display
def test_search_queries_tmdb_and_never_anipm(window):
    window._client.items = [MOVIE, SHOW]
    window._run_search("attack on titan", window._search_token)
    assert wait_until(lambda: len(window._search_flow.get_children()) == 2)

    assert window._client.searches == ["attack on titan"]
    note = window._search_status.get_text()
    assert note == "2 results"
    assert "from ani.pm" not in note and "from TMDB" not in note


@needs_display
def test_a_stale_search_token_is_dropped(window):
    window._run_search("old", window._search_token)
    window._search_token += 1
    window._apply_search(window._search_token - 1, "old", [MOVIE])
    assert window._search_flow.get_children() == []
    assert window._search_status.get_text() != "1 result"


# -- playback ---------------------------------------------------------------


@pytest.fixture
def playing(window):
    played: list[tuple] = []
    messages: list[tuple] = []
    window._record_playback = lambda *_a, **_k: None
    window._play_attempts = lambda attempts, title: played.append((attempts, title))
    window._open_player_message = lambda title, message: messages.append((title, message))
    return window, played, messages


def _target(anilist_id: int, episode: int = 1, confident: bool = True, reason=""):
    return movies_ui.anime_map.PlayTarget(
        anilist_id=anilist_id, episode=episode, confident=confident, reason=reason
    )


@needs_display
def test_a_confident_target_plays_that_entry_and_episode(playing, monkeypatch):
    window, played, _messages = playing
    window._client.document = ANIME
    seen: dict = {}

    def resolve(tmdb_kind, tmdb_id, season=1, episode=1, *, title="", **_kwargs):
        seen.update(kind=tmdb_kind, id=tmdb_id, season=season, episode=episode, title=title)
        return _target(16498, 5)

    monkeypatch.setattr(movies_ui.anime_map, "tmdb_to_anilist", resolve)
    window._play_tmdb_title(SHOW, 2, EPISODE)
    assert wait_until(lambda: bool(played))

    assert seen == {
        "kind": "tv", "id": 1429, "season": 2, "episode": 5, "title": "Attack on Titan",
    }
    assert played[0][0] == [("ani", "https://ani.pm/embed/ani/16498/5/sub")]


@needs_display
def test_an_unsure_target_asks_which_part_and_plays_the_pick(playing, monkeypatch):
    window, played, _messages = playing
    window._client.document = ANIME
    ambiguous = movies_ui.anime_map.PlayTarget(
        0, 15, False, "two entries",
        (
            _target(99147, 3, reason="starts at TMDB episode 1"),
            _target(104578, 3, reason="starts at TMDB episode 13"),
        ),
    )
    monkeypatch.setattr(
        movies_ui.anime_map, "tmdb_to_anilist", lambda *_a, **_k: ambiguous
    )
    asked: list[list[str]] = []

    class Recorder:
        def __init__(self, _parent, title, candidates) -> None:
            asked.append([text for text, _target_ in candidates])
            self._target = list(candidates)[-1][1]

        def run(self) -> None:
            return None

        def destroy(self) -> None:
            return None

        def chosen(self):
            return self._target

    monkeypatch.setattr(movies_ui, "WhichPartDialog", Recorder)
    window._play_tmdb_title(SHOW, 3, tmdb.Episode(season=3, number=15, title="Smoke"))
    assert wait_until(lambda: bool(played))

    assert asked == [["Part 1/2", "Part 2/2"]]
    assert played[0][0] == [("ani", "https://ani.pm/embed/ani/104578/3/sub")]


@needs_display
def test_none_falls_back_to_the_anilist_title_search(playing, monkeypatch):
    window, played, _messages = playing
    window._client.document = ANIME
    monkeypatch.setattr(movies_ui.anime_map, "tmdb_to_anilist", lambda *_a, **_k: None)
    monkeypatch.setattr(movies_ui.streaming, "anilist_search", lambda *_a, **_k: 20954)
    window._play_tmdb_title(MOVIE)
    assert wait_until(lambda: bool(played))
    assert played[0][0] == [("ani", "https://ani.pm/embed/ani/20954/1/sub")]


@needs_display
def test_nothing_matched_reports_it_and_plays_nothing(playing, monkeypatch):
    window, played, messages = playing
    window._client.document = ANIME
    monkeypatch.setattr(movies_ui.anime_map, "tmdb_to_anilist", lambda *_a, **_k: None)
    monkeypatch.setattr(movies_ui.streaming, "anilist_search", lambda *_a, **_k: None)
    window._play_tmdb_title(MOVIE)
    assert wait_until(lambda: bool(messages))
    assert "No AniList entry" in messages[0][1]
    assert played == []


@needs_display
def test_a_non_anime_title_keeps_the_provider_chain(playing, monkeypatch):
    window, played, _messages = playing
    window._client.document = NOT_ANIME
    monkeypatch.setattr(
        movies_ui.anime_map, "tmdb_to_anilist",
        lambda *_a, **_k: pytest.fail("a non-anime title must not use the mapping"),
    )
    provider: list[str] = []
    window._provider_attempts = lambda build, preferred=None: (
        provider.append("built") or [("vidsrc", "https://vidsrc.example/603")]
    )
    window._play_tmdb_title(MOVIE)
    assert wait_until(lambda: bool(played))
    assert provider == ["built"]


# -- the sheet --------------------------------------------------------------


@needs_display
def test_which_part_dialog_follows_the_sheet_rules():
    parts = [("Attack on Titan", _target(99147)), ("Part 2", _target(104578))]
    dialog = movies_ui.WhichPartDialog(None, "Attack on Titan", parts)
    try:
        assert dialog.get_border_width() == 24
        assert dialog.get_content_area().get_spacing() == 16
        assert dialog.get_default_response() == 100
        # The first part has its own response id, so Enter plays it.
        first = dialog.get_widget_for_response(100)
        assert first is not None and first.get_label() == "Attack on Titan"
        assert dialog.labels() == ["Attack on Titan", "Part 2"]
        assert dialog.chosen() is None

        class Escape:
            keyval = 65307  # Gdk.KEY_Escape

        assert dialog._on_key(dialog, Escape()) is True
        assert dialog.chosen() is None
    finally:
        dialog.destroy()


@needs_display
def test_which_part_dialog_returns_the_picked_part():
    parts = [("Attack on Titan", _target(99147)), ("Part 2", _target(104578))]
    dialog = movies_ui.WhichPartDialog(None, "Attack on Titan", parts)
    try:
        dialog.response(101)
        assert dialog.chosen() is parts[1][1]
    finally:
        dialog.destroy()
