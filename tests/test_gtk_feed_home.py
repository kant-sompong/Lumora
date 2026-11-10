"""The Media home feed: the rows, the three lists and the day strip wired up.

Slower, network-touching loaders are injected as plain callables here, so the
page is exercised without TMDB, AniList or a real window.  The module skips
itself when GTK 3 or a display is missing, like the rest of the GTK suite.
"""

from __future__ import annotations

import time
from datetime import datetime

import pytest

gi = pytest.importorskip("gi")

try:
    gi.require_version("Gtk", "3.0")
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf, Gtk
except (ValueError, ImportError):  # pragma: no cover - GTK 3 typelib missing
    pytest.skip("GTK 3 is not installed", allow_module_level=True)

from lgl import gtk_feed_home  # noqa: E402

NOW = datetime(2026, 11, 10, 15, 30)


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


def cards(count: int, meta: str = "Movie") -> list[dict]:
    return [
        {"title": f"Title {index}", "meta": meta, "item": f"item-{index}"}
        for index in range(count)
    ]


def airing(title: str, when: datetime, episode: object = 1) -> dict:
    return {"title": title, "air_at": when.timestamp(), "episode": episode}


def in_window(widget: Gtk.Widget, width: int = 1100, height: int = 900):
    window = Gtk.OffscreenWindow()
    window.set_default_size(width, height)
    window.add(widget)
    window.show_all()
    pump()
    return window


def build(**kwargs) -> gtk_feed_home.FeedHome:
    kwargs.setdefault("threaded", False)
    return gtk_feed_home.FeedHome(**kwargs)


# -- the page ---------------------------------------------------------------
def test_the_page_has_the_seven_sections_in_order(display):
    feed = build()
    keys = [key for key, _title, _note, _show in gtk_feed_home.SECTIONS]
    assert list(feed.rows) == keys
    assert keys[0] == "continue"
    assert "hidden_gems" in keys
    headings = [feed.rows[key].heading.get_text() for key in keys]
    assert headings[0] == "Continue Watching"
    assert "Trending Now" in headings
    window = in_window(feed)
    window.destroy()


def test_only_the_sections_that_can_expand_have_view_all(display):
    feed = build(on_view_all=lambda _key: None)
    shown = {key for key, row in feed.rows.items() if row.view_all.get_visible()}
    assert shown == {"trending", "movies", "tv", "hidden_gems"}


def test_an_empty_page_hides_every_row_columns_and_strip(display):
    feed = build()
    assert feed.get_visible() is False
    for row in feed.rows.values():
        assert row.get_visible() is False
    assert feed.columns.get_visible() is False
    assert feed.schedule.get_visible() is False


def test_apply_fills_one_row_and_leaves_the_rest_hidden(display):
    feed = build()
    feed.apply("trending", cards(4))
    assert feed.rows["trending"].get_visible() is True
    assert feed.rows["trending"].titles() == [f"Title {index}" for index in range(4)]
    assert feed.rows["trending"].count.get_text() == "4"
    for key in feed.rows:
        if key != "trending":
            assert feed.rows[key].get_visible() is False


def test_applying_an_empty_list_hides_a_row_again(display):
    feed = build()
    feed.apply("movies", cards(3))
    feed.apply("movies", [])
    assert feed.rows["movies"].get_visible() is False


# -- row loaders ------------------------------------------------------------
def test_a_row_loader_fills_its_row(display):
    feed = build()
    feed.set_loaders({"continue": lambda: cards(2, "Recently watched")})
    feed.refresh()
    assert feed.rows["continue"].get_visible() is True
    assert feed.rows["continue"].titles() == ["Title 0", "Title 1"]
    assert feed.rows["trending"].get_visible() is False


def test_a_failing_row_loader_shows_one_line_and_no_posters(display):
    def broken():
        raise RuntimeError("offline")

    feed = build()
    feed.set_loaders({"hidden_gems": broken})
    feed.refresh()
    row = feed.rows["hidden_gems"]
    assert row.get_visible() is True
    assert row.error.get_text() == "Could not load: offline"
    assert row.error.get_visible() is True
    assert row.posters() == []
    assert feed.rows["more_like"].get_visible() is False


# -- the three lists --------------------------------------------------------
def test_the_columns_load_and_only_populated_ones_show(display):
    feed = build()
    feed.set_column_loaders(
        {
            "new_release": lambda: cards(2, "Friday"),
            "new_added": lambda: [],
            "just_completed": lambda: cards(3, "Finale"),
        }
    )
    feed.refresh_columns()
    assert feed.columns.columns["new_release"]["box"].get_visible() is True
    assert feed.columns.columns["new_added"]["box"].get_visible() is False
    assert feed.columns.columns["just_completed"]["box"].get_visible() is True
    assert feed.columns.rows("new_release") == 2


def test_one_failing_column_leaves_its_empty_siblings_hidden(display):
    def broken():
        raise RuntimeError("timeout")

    feed = build()
    feed.set_column_loaders(
        {
            "new_release": lambda: [],
            "new_added": broken,
            "just_completed": lambda: [],
        }
    )
    feed.refresh_columns()
    assert feed.columns.error_text("new_added") == "Could not load: timeout"
    assert feed.columns.columns["new_added"]["box"].get_visible() is True
    assert feed.columns.columns["new_release"]["box"].get_visible() is False
    assert feed.columns.columns["just_completed"]["box"].get_visible() is False


def test_toggling_a_column_does_not_reveal_its_siblings(display):
    feed = build()
    feed.set_column_loaders({"new_release": lambda: cards(8, "Friday")})
    feed.refresh_columns()
    feed.columns.toggle("new_release")
    assert feed.columns.rows("new_release") == 8
    assert feed.columns.columns["new_added"]["box"].get_visible() is False
    assert feed.columns.columns["just_completed"]["box"].get_visible() is False


# -- the day strip ----------------------------------------------------------
def test_the_strip_waits_for_its_first_answer_before_appearing(display):
    feed = build()
    feed.set_schedule_loader(lambda: [airing("Frieren", NOW.replace(hour=9))])
    feed.set_now(NOW)
    # The page's own show_all() must not reveal an unloaded strip.
    window = in_window(feed)
    assert feed.schedule.get_visible() is False
    assert feed.schedule.rows() == 0

    feed.refresh_schedule()
    pump()
    assert feed.schedule.get_visible() is True
    assert feed.schedule.rows() == 1
    assert feed.schedule.empty.get_visible() is False
    window.destroy()


def test_a_loaded_empty_week_shows_the_strips_own_line(display):
    state = {"entries": []}
    feed = build()
    feed.set_schedule_loader(lambda: list(state["entries"]))
    feed.set_now(NOW)
    feed.refresh_schedule()
    # A completed answer that has nothing to air is a real state, not a
    # missing one: the strip says so instead of vanishing.
    assert feed.schedule.get_visible() is True
    assert feed.schedule.rows() == 0
    assert feed.schedule.empty.get_visible() is True
    assert feed.schedule.empty.get_text() == feed.schedule.state.empty_text()
    assert feed.schedule_error.get_visible() is False

    state["entries"] = [
        airing("Frieren", NOW.replace(hour=9)),
        airing("Dandadan", NOW.replace(hour=13, minute=30)),
    ]
    feed.refresh_schedule()
    assert feed.schedule.get_visible() is True
    assert feed.schedule.rows() == 2
    assert feed.schedule.empty.get_visible() is False
    assert feed.schedule_error.get_visible() is False


def test_a_failing_schedule_shows_one_line_and_hides_the_strip(display):
    def broken():
        raise RuntimeError("no calendar")

    feed = build()
    feed.set_schedule_loader(broken)
    feed.refresh_schedule()
    assert feed.schedule.get_visible() is False
    assert feed.schedule_error.get_visible() is True
    assert feed.schedule_error.get_text() == "Could not load: no calendar"


def test_selecting_a_day_uses_the_strips_own_selection(display):
    feed = build()
    feed.set_schedule_loader(
        lambda: [
            airing("Frieren", NOW.replace(hour=9)),
            airing("Orb", (NOW.replace(hour=9)).replace(day=12)),
        ]
    )
    feed.set_now(NOW)
    feed.refresh_schedule()
    window = in_window(feed)
    feed.schedule.select_day(2)
    pump()
    assert feed.schedule.selected_day().day == NOW.day + 2
    assert feed.schedule.rows() == 1
    window.destroy()


# -- callbacks --------------------------------------------------------------
def test_view_all_names_the_row_it_belongs_to(display):
    pressed = []
    feed = build(on_view_all=pressed.append)
    feed.rows["tv"].view_all.emit("clicked")
    feed.rows["movies"].view_all.emit("clicked")
    assert pressed == ["tv", "movies"]


def test_clicking_a_card_hands_back_the_item_behind_it(display):
    opened = []
    feed = build(on_open=opened.append)
    feed.apply("trending", cards(2))
    poster = feed.rows["trending"].posters()[1]
    poster.emit("clicked")
    assert opened == ["item-1"]


def test_a_schedule_row_hands_back_the_original_entry(display):
    opened = []
    entry = airing("Frieren", NOW.replace(hour=9))
    feed = build(on_schedule_open=opened.append)
    feed.set_schedule_loader(lambda: [entry])
    feed.set_now(NOW)
    feed.refresh_schedule()
    row = feed.schedule.rows_box.get_children()[0]
    row.emit("clicked")
    assert opened == [entry]
    assert opened[0] is entry


def test_a_schedule_row_without_a_callback_opens_nothing(display):
    feed = build()
    feed.set_schedule_loader(lambda: [airing("Frieren", NOW.replace(hour=9))])
    feed.set_now(NOW)
    feed.refresh_schedule()
    row = feed.schedule.rows_box.get_children()[0]
    row.emit("clicked")  # an optional callback: nothing to call, no error
    assert feed.schedule.get_visible() is True


def test_a_column_row_hands_back_the_whole_entry(display):
    opened = []
    entry = {"title": "Heat", "meta": "This week", "key": "anime:1"}
    feed = build(on_open=opened.append)
    feed.set_column_loaders({"new_added": lambda: [entry]})
    feed.refresh_columns()
    row = feed.columns.columns["new_added"]["rows"].get_children()[0]
    row.emit("clicked")
    assert opened == [entry]


# -- covers -----------------------------------------------------------------
def test_covers_are_loaded_and_painted_onto_the_posters(display):
    requested = []

    def loader(url, size, done):
        requested.append((url, size))
        pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 40, 60)
        pixbuf.fill(0x336699FF)
        done(pixbuf)

    feed = build(image_loader=loader)
    poster = type("Item", (), {"poster_url": "https://example.test/a.jpg",
                               "title": "Heat"})()
    feed.apply("trending", [gtk_feed_home.card_for(poster, "Movie")])
    pump()
    painted = feed.rows["trending"].posters()[0]
    assert requested and requested[0][0] == "https://example.test/a.jpg"
    assert painted.poster.get_pixbuf() is not None


def test_a_card_without_a_cover_never_asks_for_one(display):
    requested = []

    def loader(url, size, done):
        requested.append(url)

    feed = build(image_loader=loader)
    feed.apply("movies", [{"title": "Heat", "meta": "Movie"}])
    assert requested == []


# -- the window above it ----------------------------------------------------
class Store(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)

    def set(self, key, value):
        self[key] = value

    def save(self):
        return True


@pytest.fixture
def media_window(display):
    pytest.importorskip("cairo")
    movies_ui = pytest.importorskip("lgl.movies_ui")
    tmdb = pytest.importorskip("lgl.tmdb")

    class Client:
        configured = True

        def __init__(self, *_a, **_k):
            pass

        def trending(self, kind="movie", **_k):
            return [
                tmdb.MediaItem(kind=kind, tmdb_id=index, title=f"{kind} {index}")
                for index in range(3)
            ]

        def popular(self, kind="movie", **_k):
            return self.trending(kind)

    original = movies_ui.TmdbClient
    movies_ui.TmdbClient = Client
    window = Gtk.OffscreenWindow()
    media = movies_ui.MoviesWindow(window, Store({"network_timeout": 5}))
    window.add(media)
    window.show_all()
    pump()
    try:
        yield media
    finally:
        window.destroy()
        pump(0.02)
        movies_ui.TmdbClient = original


def test_the_window_opens_a_feed_entry_by_its_key(media_window):
    opened = []
    media_window._open_details = opened.append
    media_window._open_feed_item({"key": "tmdb:tv:42", "title": "Heat"})
    media_window._open_feed_item({"key": "tmdb:movie:7", "title": "Alien"})
    assert [(item.kind, item.tmdb_id) for item in opened] == [("tv", 42), ("movie", 7)]


def test_a_feed_entry_without_a_key_opens_nothing(media_window):
    opened = []
    media_window._open_details = opened.append
    media_window._open_anime_details = opened.append
    media_window._open_feed_item({"title": "Heat"})
    media_window._open_feed_item("Heat")
    assert opened == []


def test_view_all_leads_to_the_matching_place(media_window):
    nav, browse, gems = [], [], []
    media_window._activate_nav = nav.append
    media_window._open_browse = browse.append
    media_window._open_gems = lambda: gems.append(True)
    for key in ("continue", "trending", "movies", "tv", "hidden_gems"):
        media_window._show_feed_more(key)
    assert nav == ["history"]
    assert browse == ["movie", "movie", "tv"]
    assert gems == [True]


def test_the_window_installs_loaders_and_fills_its_rows(media_window):
    feed = media_window.feed
    assert set(feed.loaders) == {"continue", "for_you", "hidden_gems", "more_like"}
    assert set(feed.column_loaders) == {"new_release", "new_added", "just_completed"}
    assert feed.schedule_loader is not None


def test_the_home_fetch_fills_trending_movies_and_tv(media_window):
    tmdb = pytest.importorskip("lgl.tmdb")
    rows = {
        "movie": [tmdb.MediaItem(kind="movie", tmdb_id=1, title="Heat")],
        "tv": [tmdb.MediaItem(kind="tv", tmdb_id=2, title="Alien")],
        "popular_movie": [tmdb.MediaItem(kind="movie", tmdb_id=3, title="Arrival")],
        "popular_tv": [tmdb.MediaItem(kind="tv", tmdb_id=4, title="Sicario")],
    }
    media_window._apply_home(media_window._home_token, rows, {})
    feed = media_window.feed
    assert feed.rows["trending"].titles() == ["Heat", "Alien"]
    assert feed.rows["movies"].titles() == ["Arrival"]
    assert feed.rows["tv"].titles() == ["Sicario"]
    assert feed.rows["trending"].get_visible() is True
    assert feed.rows["movies"].get_visible() is True


def test_a_home_fetch_with_nothing_to_show_hides_the_fetched_rows(media_window):
    feed = media_window.feed
    feed.apply("trending", cards(2))
    feed.apply("movies", cards(2))
    feed.apply("tv", cards(2))
    media_window._apply_home(media_window._home_token, {}, {})
    for key in ("trending", "movies", "tv"):
        assert feed.rows[key].get_visible() is False
