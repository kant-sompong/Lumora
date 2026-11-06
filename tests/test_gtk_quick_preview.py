"""The GTK quick preview: text rules, the switch, opening and dismissal.

Skipped without GTK 3 or a display, like the rest of the GTK suite.
"""

from __future__ import annotations

import time

import pytest

gi = pytest.importorskip("gi")

try:
    gi.require_version("Gdk", "3.0")
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gdk, Gtk
except (ValueError, ImportError):  # pragma: no cover - GTK 3 typelib missing
    pytest.skip("GTK 3 is not installed", allow_module_level=True)

from lgl.gtk_quick_preview import (
    POSITIONS,
    QuickPreview,
    choose,
    meta_text,
    preview_text,
    rect,
    spot,
)
from lgl.library_store import LibraryEntry


class Settings(dict):
    """Settings surface: dict storage plus the flags the tiles read."""

    library_games_only = True
    library_show_missing = True
    library_hide_adult = False
    library_group_copies = False

    def get(self, key, default=None):
        return dict.get(self, key, default)

    def set(self, key, value):
        self[key] = value

    def save(self):
        return True


class View(Gtk.Box):
    """The part of LibraryView the popover reads, inside a real window."""

    def __init__(self, entries, **flags):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._settings = Settings(**flags)
        self._entries = list(entries)
        self._tiles = {}
        self.cover_requests = []

    def request_cover(self, path, callback):
        self.cover_requests.append(path)

    def _selected_entry(self):
        return self._entries[0] if self._entries else None


def pump(rounds: int = 6) -> None:
    """Let GTK finish allocating and run the timeout sources."""
    for _ in range(rounds):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        time.sleep(0.01)


@pytest.fixture(scope="module")
def display():
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    yield


def entry(identifier, title, **kwargs):
    return LibraryEntry(
        identifier=identifier, title=title, folder=f"/games/{identifier}", **kwargs
    )


def entries():
    return [
        entry(
            "game-0",
            "Hollow Knight",
            year="2002",
            size=1536,
            cover_path="/covers/hollow-knight.png",
            description=(
                "A hand-drawn metroidvania through a ruined insect kingdom.\n"
                "Second line of the blurb with the details.\n"
                "Third line must not appear in the popover."
            ),
        ),
        entry("game-1", "Celeste", year="2000", size=2048),
        entry("game-2", "Braid", year="1998", size=1024),
        entry("game-3", "Inside", year="2004", size=4096),
    ]


def library(display, *, delay: int = 0, **flags):
    """A real window, a flow of real library tiles, and one popover."""
    from lgl import ui

    items = entries()
    view = View(items, **flags)
    window = Gtk.Window()
    window.set_default_size(900, 620)

    flow = Gtk.FlowBox()
    flow.set_selection_mode(Gtk.SelectionMode.SINGLE)
    flow.set_homogeneous(True)
    flow.set_min_children_per_line(2)
    flow.set_max_children_per_line(2)
    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroller.add(flow)
    view.pack_start(scroller, True, True, 0)
    window.add(view)

    preview = QuickPreview(view, delay=delay)
    preview.attach(flow)
    tiles = {}
    for item in items:
        tile = ui.LibraryTile(view, item)
        view._tiles[item.identifier] = tile
        preview.track(tile)
        flow.add(tile)
        tiles[item.identifier] = tile
    window.show_all()
    pump()
    return window, flow, preview, tiles, view


def crossing(kind=Gdk.EventType.ENTER_NOTIFY):
    return Gdk.Event.new(kind)


def key(keyval):
    event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
    event.keyval = keyval
    return event


def overlap(first, second):
    """Rectangles touch or cross, shadow margins included."""
    return not (
        first.x + first.width <= second.x
        or second.x + second.width <= first.x
        or first.y + first.height <= second.y
        or second.y + second.height <= first.y
    )


def test_the_text_rules_come_from_the_entry(display):
    long_line = "x" * 200
    item = entry("braid", "Braid", description=f"  first line  \n\n{long_line}\nthird line")

    text = preview_text(item)
    lines = text.splitlines()

    assert lines[0] == "first line"
    assert len(lines) == 2
    assert lines[1] == long_line[:120]
    assert "third" not in text

    assert meta_text(entry("celeste", "Celeste", year="2000", size=2048)) == "2000 · 2.0 KiB"
    assert meta_text(entry("plain", "Plain")) == "—"


def test_nothing_starts_when_the_switch_is_off(display):
    window, _flow, preview, tiles, _view = library(
        display, delay=30, quick_preview=False
    )
    assert preview.enabled() is False

    tiles["game-0"].emit("enter-notify-event", crossing())
    pump(8)

    assert preview.pending() is False
    assert preview.get_visible() is False
    window.destroy()


def test_hovering_a_tile_opens_the_popover(display):
    # 80 ms stands in for the 400 ms a real pointer has to rest.
    window, _flow, preview, tiles, view = library(display, delay=80)
    tile = tiles["game-0"]

    tile.emit("enter-notify-event", crossing())
    pump(2)
    assert preview.pending() is True
    assert preview.get_visible() is False

    pump(10)

    assert preview.get_visible() is True
    assert preview.get_modal() is False
    assert preview.get_relative_to() is tile
    assert preview.get_position() in POSITIONS
    assert preview.title.get_text() == "Hollow Knight"
    assert preview.meta.get_text() == "2002 · 1.5 KiB"
    detail = preview.description.get_text()
    assert detail.splitlines()[0].startswith("A hand-drawn")
    assert "Third line" not in detail
    # The tile asked for its cover once; opening the preview asks again, by
    # path, through the view's own cache. Nothing is read from disk here.
    assert view.cover_requests.count("/covers/hollow-knight.png") == 2
    window.destroy()


def test_space_opens_the_selected_tile_unless_a_field_has_focus(display):
    window, flow, preview, tiles, view = library(display)

    assert tiles["game-1"].emit("key-press-event", key(Gdk.KEY_space)) is True
    pump()
    assert preview.get_visible() is True
    preview.dismiss()
    pump()

    field = Gtk.Entry()
    view.pack_start(field, False, False, 0)
    field.show()
    field.grab_focus()
    pump()
    if window.get_focus() is not field:
        window.destroy()
        pytest.skip("this display did not give the entry focus")

    assert preview.typing() is True
    assert flow.emit("key-press-event", key(Gdk.KEY_space)) is False
    pump()
    assert preview.get_visible() is False
    window.destroy()


def test_escape_closes_it(display):
    window, _flow, preview, _tiles, _view = library(display)
    preview.open_selected()
    pump()
    assert preview.get_visible() is True

    assert preview.emit("key-press-event", key(Gdk.KEY_Escape)) is True
    pump()

    assert preview.get_visible() is False
    window.destroy()


def test_a_selection_change_closes_it(display):
    window, _flow, preview, _tiles, _view = library(display)
    preview.open_selected()
    pump()
    assert preview.get_visible() is True

    # What LibraryView runs from the flow's selected-children-changed signal.
    preview.selection_changed()
    pump()

    assert preview.get_visible() is False
    window.destroy()


def test_it_only_places_the_popover_beside_the_tile(display):
    size = (320, 190)
    roomy = rect(0, 0, 900, 600)
    middle = rect(300, 200, 180, 260)

    assert choose(middle, size, roomy) == Gtk.PositionType.RIGHT
    assert choose(rect(700, 200, 180, 260), size, roomy) == Gtk.PositionType.LEFT

    # Narrow enough that neither side has room, the tile at the top.
    tight = rect(0, 0, 700, 600)
    assert choose(rect(200, 0, 180, 100), size, tight) == Gtk.PositionType.BOTTOM

    # Neither side and no room below: above is what is left.
    low = rect(200, 200, 180, 260)
    assert choose(low, size, rect(0, 0, 700, 600)) == Gtk.PositionType.TOP

    # Every candidate sits clear of the tile itself.
    for position in POSITIONS:
        assert not overlap(spot(position, middle, size), middle)

    assert choose(middle, size, rect(0, 0, 100, 100)) is None
