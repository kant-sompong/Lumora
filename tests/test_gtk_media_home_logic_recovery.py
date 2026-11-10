"""The Media home's logic: AniList keys, TMDB-less sections, errors and pages.

Every loader here is given fixture data by the test, so nothing reaches TMDB,
AniList or MyAnimeList.  The module skips itself when GTK 3 or a display is
missing, like the rest of the GTK suite.
"""

from __future__ import annotations

import threading
import time

import pytest

gi = pytest.importorskip("gi")

try:
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
except (ValueError, ImportError):  # pragma: no cover - GTK 3 typelib missing
    pytest.skip("GTK 3 is not installed", allow_module_level=True)

from lgl import anime_calendar, anipm, hidden_gems, mal, movies_ui, tmdb  # noqa: E402


@pytest.fixture(scope="module")
def display():
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    yield


def pump(seconds: float = 0.05) -> None:
    end = time.time() + seconds
    while time.time() < end:
        while Gtk.events_pending():
            Gtk.main_iteration()
        time.sleep(0.005)


def wait_for(predicate, seconds: float = 3.0) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        pump(0.02)
        if predicate():
            return True
    return False


class Store(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)

    def set(self, key, value):
        self[key] = value

    def save(self):
        return True


class StubTmdb:
    """A TMDB client that never leaves the process (the window builds one)."""

    configured = True

    def __init__(self, *_a, **_k):
        pass

    def trending(self, kind="movie", **_k):
        return []

    def popular(self, kind="movie", **_k):
        return []


class StubMal:
    """A MAL client whose one call answers with a value or the given error."""

    def __init__(self, *, error=None, details=None):
        self.error = error
        self.details_value = details
        self.calls: list[int] = []

    def details(self, anime_id):
        self.calls.append(anime_id)
        if self.error is not None:
            raise self.error
        return self.details_value


@pytest.fixture
def worker_errors():
    """Collect uncaught worker-thread exceptions for the test to assert on."""
    errors: list[object] = []
    original = threading.excepthook
    threading.excepthook = errors.append
    try:
        yield errors
    finally:
        threading.excepthook = original


@pytest.fixture
def window(display, monkeypatch):
    pytest.importorskip("cairo")
    monkeypatch.setattr(movies_ui, "TmdbClient", StubTmdb)
    monkeypatch.setattr(movies_ui.MoviesWindow, "_load_home", lambda self: None)
    frame = Gtk.OffscreenWindow()
    media = movies_ui.MoviesWindow(frame, Store({"network_timeout": 5}))
    frame.add(media)
    frame.show_all()
    pump()
    media._mal_client = StubMal()
    feed = media.feed
    # A test names its own loaders; the installed ones would need the network.
    for row in feed.rows.values():
        row.set_loader(None)
    feed.schedule_loader = None
    feed.column_loaders.clear()
    try:
        yield media
    finally:
        frame.destroy()
        pump(0.02)


def gem(key: str, title: str, score: int = 88, **extra) -> dict:
    entry = {
        "key": key,
        "title": title,
        "score": score,
        "popularity": 1200,
        "rank": 3,
        "episodes": 12,
        "format": "TV",
        "status": "FINISHED",
        "source": "AniList",
    }
    entry.update(extra)
    return entry


# -- 1. AniList ids in keys -------------------------------------------------
def test_anilist_keys_resolve_to_their_positive_id():
    assert movies_ui._anilist_id_from_key("anilist:161645") == 161645
    assert movies_ui._anilist_id_from_key("anime:161645") == 161645
    assert movies_ui._anilist_id_from_key(" anipm:7 ") == 7


def test_a_malformed_key_never_becomes_an_id():
    for bad in ("", "anilist", "anilist:", "anilist:abc", "anilist:0", "anilist:-5",
                "tmdb:movie:9", "161645", None, 161645):
        assert movies_ui._anilist_id_from_key(bad) == 0, bad


def test_hidden_gems_cards_carry_the_anilist_id_from_their_key(window, monkeypatch):
    entries = [
        gem("anilist:161645", "Frieren"),
        gem("anilist:abc", "Broken key"),
        gem("tmdb:movie:9", "Not AniList"),
        {"title": "No key at all"},
    ]
    monkeypatch.setattr(
        hidden_gems, "load_gems", lambda **_k: hidden_gems.GemsResult(entries=entries)
    )
    cards = window.feed.loaders["hidden_gems"]()
    assert len(cards) == 1
    item = cards[0]["item"]
    assert isinstance(item, mal.AnimeItem)
    assert isinstance(item, movies_ui.AniListItem)
    assert item.id == 161645
    assert item.anilist_id == 161645
    assert item.title == "Frieren"


def test_a_hidden_gem_card_opens_anilist_details_without_crashing(
    window, worker_errors
):
    opened = []
    window._open_anime_details = opened.append
    gem_item = movies_ui.AniListItem(id=161645, title="Frieren", rating=8.8)
    window._open_feed_item(gem_item)
    assert len(opened) == 1
    assert isinstance(opened[0], anipm.AniTitle)
    assert opened[0].anilist_id == 161645
    assert opened[0].title == "Frieren"
    assert worker_errors == []


# -- 2. AniList sections without TMDB ---------------------------------------
MISSING_KEY = tmdb.TmdbError("TMDB needs an API key.", kind="missing-key")


def no_tmdb() -> dict:
    return {"movie": MISSING_KEY, "tv": MISSING_KEY}


def test_a_missing_tmdb_key_refreshes_the_independent_sections(
    window, monkeypatch
):
    refreshed = []
    monkeypatch.setattr(window.feed, "refresh", lambda: refreshed.append(True))
    assert window._apply_home(window._home_token, {}, no_tmdb()) is False
    assert refreshed == [True]
    assert window._home_failed is True


def test_a_missing_tmdb_key_keeps_its_notice_and_hides_tmdb_rows(window):
    window._apply_home(window._home_token, {}, no_tmdb())
    for key in ("trending", "movies", "tv"):
        assert window.feed.rows[key].get_visible() is False
        assert window.feed.rows[key].cards() == []
    assert window._home_notice_box.get_visible() is True


def test_the_anilist_rows_fill_themselves_without_tmdb(window, worker_errors):
    cards = [
        {"title": "Gem", "meta": "Anime", "item": "gem"},
        {"title": "Other", "meta": "Anime", "item": "other"},
    ]
    window.feed.set_loaders({"hidden_gems": lambda: list(cards)})
    window._apply_home(window._home_token, {}, no_tmdb())
    assert wait_for(lambda: len(window.feed.rows["hidden_gems"].cards()) == 2)
    assert window.feed.rows["hidden_gems"].get_visible() is True
    # A second attempt replaces the cards instead of appending them.
    window._apply_home(window._home_token, {}, no_tmdb())
    pump(0.3)
    assert len(window.feed.rows["hidden_gems"].cards()) == 2
    assert worker_errors == []


# -- 3. Anime details errors ------------------------------------------------
def test_a_missing_mal_key_explains_itself_on_the_page(window, worker_errors):
    window._mal_client = StubMal(
        error=mal.MalError("MyAnimeList needs a Client ID.", kind="missing-key")
    )
    window._open_anime_details(mal.AnimeItem(id=42, title="Frieren"))
    assert wait_for(window._anime_detail_status.get_visible)
    text = window._anime_detail_status.get_text()
    assert "MyAnimeList is not configured" in text
    assert "Settings" in text
    assert worker_errors == []
    assert window._stack.get_visible_child_name() == "details"


def test_a_value_error_is_reported_instead_of_killing_the_worker(
    window, worker_errors
):
    window._mal_client = StubMal(
        error=ValueError("An anime id must be a positive integer.")
    )
    window._open_anime_details(mal.AnimeItem(id=7, title="Bad id"))
    assert wait_for(window._anime_detail_status.get_visible)
    assert "Could not open this anime" in window._anime_detail_status.get_text()
    assert worker_errors == []


def test_an_idless_item_is_reported_without_any_request(window, worker_errors):
    window._open_anime_details(mal.AnimeItem(id=0, title="No id"))
    assert window._anime_detail_status.get_visible() is True
    assert "no usable anime id" in window._anime_detail_status.get_text()
    assert window._mal_client.calls == []
    assert worker_errors == []


def test_loading_details_ends_with_the_status_line_hidden(window, worker_errors):
    details = mal.AnimeDetails(
        id=42, title="Frieren: Beyond Journey's End", synopsis="A quiet journey."
    )
    window._mal_client = StubMal(details=details)
    window._open_anime_details(mal.AnimeItem(id=42, title="Frieren"))
    assert wait_for(
        lambda: window._anime_detail_state["overview"].get_text() == "A quiet journey."
    )
    assert window._anime_detail_status.get_visible() is False
    assert window._mal_client.calls == [42]
    assert worker_errors == []


# -- 4. Schedule entries ----------------------------------------------------
def test_a_schedule_anilist_entry_opens_the_anime_details(window):
    opened = []
    window._open_anime_details = opened.append
    entry = anime_calendar.Entry(
        key="anilist:161645", title="Frieren", episode="6", air_at=1.0
    )
    window._open_feed_item(entry)
    assert len(opened) == 1
    assert isinstance(opened[0], anipm.AniTitle)
    assert opened[0].anilist_id == 161645
    assert opened[0].title == "Frieren"


def test_a_schedule_tmdb_entry_still_opens_tmdb_details(window):
    opened = []
    window._open_details = opened.append
    window._open_feed_item(
        anime_calendar.Entry(key="tmdb:movie:7", title="Heat", air_at=1.0)
    )
    window._open_feed_item(
        anime_calendar.Entry(key="tmdb:tv:42", title="Alien", episode="3", air_at=1.0)
    )
    assert [(item.kind, item.tmdb_id) for item in opened] == [("movie", 7), ("tv", 42)]


def test_existing_movie_anime_and_column_clicks_are_unchanged(window):
    tmdb_opened, anime_opened = [], []
    window._open_details = tmdb_opened.append
    window._open_anime_details = anime_opened.append
    window._open_feed_item(tmdb.MediaItem(kind="movie", tmdb_id=3, title="Heat"))
    item = mal.AnimeItem(id=11, title="Anime")
    window._open_feed_item(item)
    window._open_feed_item({"key": "tmdb:tv:42", "title": "Show"})
    window._open_feed_item({"key": "anime:11", "title": "Anime"})
    assert [opened.key for opened in tmdb_opened] == ["movie:3", "tv:42"]
    assert anime_opened[0] is item
    assert isinstance(anime_opened[1], anipm.AniTitle)
    assert anime_opened[1].anilist_id == 11


# -- 5. Hidden Gems View All ------------------------------------------------
def test_hidden_gems_view_all_opens_the_gems_page(window):
    loaded = []
    window._load_gems = lambda: loaded.append(True)
    window._show_feed_more("hidden_gems")
    assert window._stack.get_visible_child_name() == "gems"
    assert loaded == [True]


def test_the_gems_page_draws_the_anilist_titles(window):
    result = hidden_gems.GemsResult(
        entries=[gem("anilist:161645", "Frieren"), gem("anilist:2", "Monster")],
        scanned=40,
        min_score=78,
        median=5000,
    )
    window._apply_gems(window._gems_token, result)
    tiles = window._gems_flow.get_children()
    assert len(tiles) == 2
    assert "2 of 40 titles score 78+" in window._gems_status.get_text()
    assert window._gems_loaded is True
    assert window._gems_flow.get_visible() is True


def test_a_gem_tile_opens_anilist_details(window):
    opened = []
    window._open_anime_details = opened.append
    result = hidden_gems.GemsResult(entries=[gem("anilist:161645", "Frieren")], scanned=1)
    window._apply_gems(window._gems_token, result)
    tile = window._gems_flow.get_children()[0]
    tile.get_child().emit("clicked")
    assert len(opened) == 1
    assert opened[0].anilist_id == 161645


def test_a_failed_gems_load_says_so_without_malformed_titles(window):
    result = hidden_gems.GemsResult(error="AniList: offline")
    window._apply_gems(window._gems_token, result)
    assert window._gems_status.get_text() == "Could not load: AniList: offline"
    assert window._gems_flow.get_children() == []
    assert window._gems_flow.get_visible() is False
    assert window._gems_loaded is False


def test_a_gem_entry_without_a_usable_key_is_skipped():
    assert movies_ui.MoviesWindow._gem_title({"key": "anilist:abc", "title": "x"}) is None
    assert movies_ui.MoviesWindow._gem_title({"key": "anilist:9", "title": "x"}).anilist_id == 9


# -- 6. Three-column Show all ----------------------------------------------
def test_the_anime_columns_keep_every_row_for_show_all(window, monkeypatch):
    rows = [
        {
            "id": 100 + index,
            "title": f"Anime {index}",
            "format": "TV",
            "status": "FINISHED",
            "episodes": 12,
            "end_date": "",
        }
        for index in range(8)
    ]
    monkeypatch.setattr(
        hidden_gems,
        "load_anime_lists",
        lambda **_k: {"new_added": rows, "just_completed": [], "errors": []},
    )
    window._install_feed_loaders()
    loaded = window.feed.column_loaders["new_added"]()
    assert len(loaded) == 8
    assert [row["key"] for row in loaded][0] == "anime:100"


def test_the_column_collapses_to_five_and_expands_with_show_all(window, monkeypatch):
    rows = [
        {
            "id": 100 + index,
            "title": f"Anime {index}",
            "format": "TV",
            "status": "FINISHED",
            "episodes": 12,
            "end_date": "",
        }
        for index in range(8)
    ]
    monkeypatch.setattr(
        hidden_gems,
        "load_anime_lists",
        lambda **_k: {"new_added": rows, "just_completed": [], "errors": []},
    )
    window._install_feed_loaders()
    window.feed.columns.set_items(
        "new_added", window.feed.column_loaders["new_added"]()
    )
    toggle = window.feed.columns.columns["new_added"]["toggle"]
    assert window.feed.columns.rows("new_added") == 5
    assert toggle.get_visible() is True
    window.feed.columns.toggle("new_added")
    assert window.feed.columns.rows("new_added") == 8


# -- 7. The schedule callback, wired in production --------------------------
def test_the_window_wires_the_schedule_callback_into_feed_home(window):
    """The home page must pass on_schedule_open, not just accept it."""
    # Bound methods compare equal by receiver and function; ``is`` would not.
    assert window.feed._on_schedule_open == window._open_feed_item
    assert window.feed.schedule._on_open == window._open_feed_item


def test_clicking_a_real_schedule_row_opens_its_title(window):
    """FeedHome -> ScheduleStrip -> MoviesWindow, with a genuine Entry."""
    import datetime

    opened = []
    window._open_anime_details = opened.append
    entry = anime_calendar.Entry(
        key="anilist:161645",
        title="Frieren",
        episode="6",
        air_at=datetime.datetime.now().timestamp(),
    )
    feed = window.feed
    feed._threaded = False
    feed.set_schedule_loader(lambda: [entry])
    feed.refresh_schedule()
    rows = feed.schedule.rows_box.get_children()
    assert len(rows) == 1
    rows[0].emit("clicked")
    assert len(opened) == 1
    assert isinstance(opened[0], anipm.AniTitle)
    assert opened[0].anilist_id == 161645
    assert opened[0].title == "Frieren"
