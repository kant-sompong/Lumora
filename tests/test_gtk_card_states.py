"""The game and movie cards must show hover, selection and focus, and open.

A card is a widget, not a picture, so the three states a keyboard user relies on
have to be visible in the stylesheet and reachable from the keyboard.  These
tests read the values GTK actually resolves, the same way a user's screen does,
and send the keys a focused card must answer.
"""

from __future__ import annotations

import pytest

gi = pytest.importorskip("gi")

try:
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gdk, Gtk
except (ValueError, ImportError):  # pragma: no cover - GTK 3 not installed
    pytest.skip("GTK 3 is not installed", allow_module_level=True)

from lgl import design_components, theme, theme_gtk, ui  # noqa: E402
from lgl.library_store import LibraryEntry  # noqa: E402


def has_display() -> bool:
    return bool(Gtk.init_check()[0])


needs_display = pytest.mark.skipif(not has_display(), reason="no display for GTK")


@pytest.fixture(scope="module", autouse=True)
def themed():
    """The real sheets, exactly as the app installs them."""
    if not has_display():
        pytest.skip("no display for GTK")
    theme_gtk.install()
    ui.load_css()
    yield


def pump() -> None:
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)


def palette() -> dict[str, str]:
    """The token values the app has actually resolved right now."""
    return theme.PALETTES[theme_gtk.resolved()]


def as_hex(colour) -> str:
    return "#%02x%02x%02x" % (
        round(colour.red * 255),
        round(colour.green * 255),
        round(colour.blue * 255),
    )


class StubView:
    """The tile asks its view for a cover; artwork is not what is under test."""

    def request_cover(self, path, callback):
        return None


def game_grid(count: int = 2) -> tuple[Gtk.Window, Gtk.FlowBox, list]:
    flow = Gtk.FlowBox()
    flow.set_selection_mode(Gtk.SelectionMode.SINGLE)
    tiles = [
        ui.LibraryTile(StubView(), LibraryEntry(identifier=f"game{index}",
                                                 title=f"Game {index}"))
        for index in range(count)
    ]
    for tile in tiles:
        flow.add(tile)
    window = Gtk.Window()
    window.add(flow)
    window.show_all()
    pump()
    flow.unselect_all()
    pump()
    return window, flow, tiles


@needs_display
def test_a_game_card_changes_on_hover_selected_and_focus():
    window, flow, tiles = game_grid()
    try:
        tile = tiles[0]
        card = tile.get_child()
        context = card.get_style_context()
        tile.unset_state_flags(Gtk.StateFlags.PRELIGHT)
        pump()
        rest = as_hex(context.get_property("border-top-color", context.get_state()))

        tile.set_state_flags(Gtk.StateFlags.PRELIGHT, False)
        pump()
        hovered = as_hex(context.get_property("border-top-color", context.get_state()))
        assert hovered != rest, "hovering a game card draws the same border as rest"

        flow.select_child(tile)
        tile.unset_state_flags(Gtk.StateFlags.PRELIGHT)
        pump()
        selected = flow.get_selected_children()
        assert selected and selected[0] is tile, "the flow box did not select the card"
        flags = context.get_state()
        assert context.get_property("border-top-width", flags) == (
            design_components.CARD_SELECTED_BORDER
        )
        assert as_hex(context.get_property("border-top-color", flags)) == (
            palette()["accent"]
        )

        tile.set_state_flags(Gtk.StateFlags.FOCUSED, False)
        pump()
        flags = tile.get_style_context().get_state()
        assert flags & Gtk.StateFlags.FOCUSED
        assert tile.get_style_context().get_property("outline-width", flags) == (
            design_components.CARD_FOCUS_RING
        )
        assert as_hex(
            tile.get_style_context().get_property("outline-color", flags)
        ) == palette()["accent"]
    finally:
        window.destroy()


@needs_display
@pytest.mark.parametrize("key", [Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space])
def test_enter_and_space_select_the_focused_game_card(key):
    window, flow, tiles = game_grid()
    try:
        tile = tiles[1]
        flow.unselect_all()
        pump()
        event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        event.keyval = key
        event.window = window.get_window()
        handled = tile.emit("key-press-event", event)
        pump()
        assert handled, f"{Gdk.keyval_name(key)} is not handled by the card"
        selected = flow.get_selected_children()
        assert selected and selected[0] is tile, (
            f"{Gdk.keyval_name(key)} did not open the focused game card"
        )
    finally:
        window.destroy()


def movie_card(opened: list) -> Gtk.Button:
    from lgl import movies_ui, tmdb

    item = tmdb.MediaItem(kind="movie", tmdb_id=1, title="Heat", year="1995")
    card = movies_ui.MediaCard(item, movies_ui.Images(), opened.append)
    window = Gtk.Window()
    window.add(card)
    window.show_all()
    pump()
    card.set_state_flags(Gtk.StateFlags.FOCUSED, False)
    pump()
    return card


@needs_display
def test_a_movie_card_changes_on_hover_and_focus():
    card = movie_card([])
    try:
        context = card.get_style_context()
        rest = as_hex(context.get_property("border-top-color", context.get_state()))
        card.set_state_flags(Gtk.StateFlags.PRELIGHT, False)
        pump()
        hovered = as_hex(context.get_property("border-top-color", context.get_state()))
        assert hovered != rest, "hovering a movie card draws the same border as rest"

        card.unset_state_flags(Gtk.StateFlags.PRELIGHT)
        flags = context.get_state()
        assert flags & Gtk.StateFlags.FOCUSED
        assert context.get_property("outline-width", flags) == (
            design_components.CARD_FOCUS_RING
        )
        assert as_hex(context.get_property("outline-color", flags)) == (
            palette()["accent"]
        )
    finally:
        card.get_toplevel().destroy()


@needs_display
@pytest.mark.parametrize("key", [Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space])
def test_enter_and_space_open_the_focused_movie_card(key):
    opened: list = []
    card = movie_card(opened)
    try:
        assert card.get_can_focus(), "a movie card must be reachable with the keyboard"
        event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        event.keyval = key
        event.window = card.get_window()
        handled = card.emit("key-press-event", event)
        pump()
        assert handled, f"{Gdk.keyval_name(key)} is not handled by the card"
        assert opened, f"{Gdk.keyval_name(key)} did not open the focused movie card"
    finally:
        card.get_toplevel().destroy()
