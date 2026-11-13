"""The ratings row: which badges show, what they say, and the painted icons."""

from __future__ import annotations

import pytest

gi = pytest.importorskip("gi")

try:
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
except (ValueError, ImportError):  # pragma: no cover - GTK 3 not installed
    pytest.skip("GTK 3 is not installed", allow_module_level=True)

import cairo

from lgl import movies_ui, ratings


def has_display() -> bool:
    return bool(Gtk.init_check()[0])


needs_display = pytest.mark.skipif(not has_display(), reason="no display for GTK")


def row(**values):
    widget = movies_ui.RatingsRow()
    window = Gtk.OffscreenWindow()
    window.add(widget)
    window.show_all()
    widget.show_values(ratings.Ratings(**values))
    return widget, window


@needs_display
def test_all_three_scores_are_shown_in_order():
    widget, window = row(imdb=8.5, rotten=90, tmdb_percent=64)
    visible = [
        kind for kind, badge in widget._badges.items() if badge.get_visible()
    ]
    assert visible == ["imdb", "rotten", "tmdb"]
    assert widget._badges["imdb"].value.get_text() == "8.5"
    assert widget._badges["rotten"].value.get_text() == "90%"
    assert widget._badges["tmdb"].value.get_text() == "64%"
    window.destroy()


@needs_display
def test_the_gap_between_badges_is_fourteen():
    widget, window = row(imdb=8.5, rotten=90, tmdb_percent=64)
    assert widget.get_spacing() == 14
    window.destroy()


@needs_display
def test_each_badge_carries_its_tooltip():
    widget, window = row(imdb=8.5, rotten=90, tmdb_percent=64)
    assert widget._badges["imdb"].get_tooltip_text() == "IMDb"
    assert widget._badges["rotten"].get_tooltip_text() == "Rotten Tomatoes (critics)"
    assert widget._badges["tmdb"].get_tooltip_text() == "TMDB user score"
    window.destroy()


@needs_display
def test_a_badge_without_data_is_hidden():
    widget, window = row(tmdb_percent=64)
    assert widget.get_visible()
    assert not widget._badges["imdb"].get_visible()
    assert not widget._badges["rotten"].get_visible()
    assert widget._badges["tmdb"].get_visible()
    window.destroy()


@needs_display
def test_no_scores_at_all_hides_the_whole_row():
    widget, window = row()
    assert not widget.get_visible()
    window.destroy()


@needs_display
def test_the_icons_are_painted_not_downloaded():
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 16, 16)
    for kind in ("imdb", "rotten", "tmdb"):
        cr = cairo.Context(surface)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        movies_ui._rating_glyph(cr, kind)
        surface.flush()
        data = bytes(surface.get_data())
        assert any(byte for byte in data), f"{kind} drew nothing"
