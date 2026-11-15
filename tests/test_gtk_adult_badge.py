"""The GTK 18+ badge on TMDB cards: shown for adult rows, absent otherwise."""

from __future__ import annotations

import pytest

gi = pytest.importorskip("gi")

try:
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
except (ValueError, ImportError):  # pragma: no cover - GTK 3 not installed
    pytest.skip("GTK 3 is not installed", allow_module_level=True)

from lgl import movies_ui, theme_gtk, ui  # noqa: E402
from lgl.tmdb import MediaItem  # noqa: E402


def has_display() -> bool:
    return bool(Gtk.init_check()[0])


needs_display = pytest.mark.skipif(not has_display(), reason="no display for GTK")


@pytest.fixture(scope="module", autouse=True)
def themed():
    if not has_display():
        pytest.skip("no display for GTK")
    theme_gtk.install()
    ui.load_css()
    yield


def adult_labels(widget) -> list:
    """Every 18+ label below ``widget``, however deep the card nests it."""
    found = []
    if isinstance(widget, Gtk.Label) and widget.get_text() == "18+":
        found.append(widget)
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            found.extend(adult_labels(child))
    return found


def item(**kwargs) -> MediaItem:
    base = dict(kind="movie", tmdb_id=900, title="Overflow", year="2020",
                adult=True)
    base.update(kwargs)
    return MediaItem(**base)


@needs_display
def test_a_carousel_card_badges_an_adult_title():
    card = movies_ui.MediaCard(item(), movies_ui.Images(), lambda _item: None)

    labels = adult_labels(card)
    assert len(labels) == 1
    assert labels[0].get_tooltip_text() == "Marked adult (18+) by TMDB"


@needs_display
def test_a_carousel_card_badges_nothing_else():
    card = movies_ui.MediaCard(item(adult=False), movies_ui.Images(),
                               lambda _item: None)

    assert adult_labels(card) == []


@needs_display
def test_a_guide_tile_badges_an_adult_title():
    tile = movies_ui.PosterTile(item(), movies_ui.Images(), lambda _item: None)

    assert len(adult_labels(tile)) == 1


@needs_display
def test_a_guide_tile_badges_nothing_else():
    tile = movies_ui.PosterTile(item(adult=False), movies_ui.Images(),
                                lambda _item: None)

    assert adult_labels(tile) == []


@needs_display
def test_the_badge_uses_the_existing_adult_style():
    card = movies_ui.MediaCard(item(), movies_ui.Images(), lambda _item: None)
    label = adult_labels(card)[0]

    assert label.get_style_context().has_class("movie-adult-badge")
