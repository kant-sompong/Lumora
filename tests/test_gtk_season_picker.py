"""The season picker: order, thumbnails, selection and keyboard.

The widgets are real; the image loader and TMDB seasons are stubs, so nothing
here needs a network or a poster file.
"""

from __future__ import annotations


import pytest

gi = pytest.importorskip("gi")

try:
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import Gdk, GdkPixbuf, Gtk
except (ValueError, ImportError):  # pragma: no cover - GTK 3 not installed
    pytest.skip("GTK 3 is not installed", allow_module_level=True)

from lgl import movies_ui
from lgl.tmdb import Season


def has_display() -> bool:
    return bool(Gtk.init_check()[0])


needs_display = pytest.mark.skipif(not has_display(), reason="no display for GTK")


class Loader:
    def __init__(self) -> None:
        self.asks: list[str] = []

    def load(self, url, size, callback) -> None:
        self.asks.append(url)
        pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 88, 128)
        callback(pixbuf)


def season(number, name="", poster="", episodes=10):
    return Season(
        number=number,
        name=name,
        episode_count=episodes,
        poster=poster,
    )


def picker(seasons, loader=None, show_poster=""):
    chosen = []
    widget = movies_ui.SeasonPicker(chosen.append)
    widget.load(seasons, loader or Loader(), show_poster)
    window = Gtk.OffscreenWindow()
    window.add(widget)
    window.show_all()
    pump()
    return widget, chosen, window


def pump(rounds=20) -> None:
    for _ in range(rounds):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)


@needs_display
def test_specials_come_first_then_seasons_in_order():
    widget, _chosen, window = picker([
        season(2, "Season 2"),
        season(0, "Specials"),
        season(1, "Season 1"),
    ])
    order = [row.season_number for row in widget._list.get_children()]
    assert order == [0, 1, 2]
    window.destroy()


@needs_display
def test_the_first_season_is_selected_and_the_page_told():
    widget, chosen, window = picker([season(1, "Season 1"), season(2, "Season 2")])
    assert [item.number for item in chosen] == [1]
    assert widget._label.get_text() == "Season 1"
    first = widget._rows[1]
    assert first.check.get_visible()
    assert not widget._rows[2].check.get_visible()
    assert first.get_style_context().has_class("season-row-on")
    window.destroy()


@needs_display
def test_a_row_shows_its_name_and_episode_count():
    widget, _chosen, window = picker([season(1, "Season 1", episodes=8)])
    labels = [
        label.get_text()
        for label in widget._rows[1].get_child().get_children()
        if isinstance(label, Gtk.Box)
        for label in label.get_children()
        if isinstance(label, Gtk.Label)
    ]
    assert "Season 1" in labels
    assert "8 episodes" in labels
    window.destroy()


@needs_display
def test_a_season_without_a_poster_borrows_the_show_art():
    loader = Loader()
    widget, _chosen, window = picker(
        [season(1, "Season 1", poster="/season.jpg")],
        loader,
        "https://image.tmdb.org/t/p/w500/show.jpg",
    )
    assert loader.asks == ["https://image.tmdb.org/t/p/w185/season.jpg"]
    window.destroy()


@needs_display
def test_with_no_art_at_all_the_row_shows_the_number():
    loader = Loader()
    widget, _chosen, window = picker([season(0, "Specials", poster="")], loader)
    assert loader.asks == []
    row = widget._rows[0]
    placeholders = [
        child
        for child in row.get_child().get_children()
        if isinstance(child, Gtk.Label)
    ]
    assert placeholders[0].get_text() == "0"
    window.destroy()


@needs_display
def test_choosing_a_season_moves_the_check_and_notifies():
    widget, chosen, window = picker([season(1, "Season 1"), season(2, "Season 2")])
    widget.choose(2)
    assert chosen[-1].number == 2
    assert widget._rows[2].check.get_visible()
    assert not widget._rows[1].check.get_visible()
    assert widget._label.get_text() == "Season 2"
    window.destroy()


@needs_display
def test_down_and_return_move_and_choose_from_the_keyboard():
    widget, chosen, window = picker([season(1, "Season 1"), season(2, "Season 2")])
    widget._list.select_row(widget._rows[1])
    down = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
    down.keyval = Gdk.KEY_Down
    assert widget._on_key_press(widget._list, down) is True
    assert widget._list.get_selected_row() is widget._rows[2]
    enter = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
    enter.keyval = Gdk.KEY_Return
    assert widget._on_key_press(widget._list, enter) is True
    assert chosen[-1].number == 2
    window.destroy()


@needs_display
def test_escape_is_handled_by_the_panel():
    widget, _chosen, window = picker([season(1, "Season 1")])
    escape = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
    escape.keyval = Gdk.KEY_Escape
    assert widget._on_key_press(widget._list, escape) is True
    window.destroy()


def test_a_season_page_with_no_seasons_keeps_its_header():
    widget = movies_ui.SeasonPicker(lambda _season: None)
    widget.load([], Loader())
    assert widget._label.get_text() == "Season"
