"""The GTK quick preview: text rules, the switch, hover and dismissal.

The hover and leave tests move the real pointer with ``Gdk.Device.warp`` and
let GTK deliver the crossing and motion events itself; nothing is emitted by
hand.  Skipped without GTK 3, a display, or a display that cannot warp.
"""

from __future__ import annotations

import time
import warnings

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
    origin,
    preview_text,
    rect,
    spot,
    window_origin,
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
    for _ in range(rounds):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        time.sleep(0.01)


def wait_for(predicate, seconds: float = 2.0) -> bool:
    """Pump the main loop until `predicate` holds, or the deadline passes."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


@pytest.fixture(scope="module")
def display():
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    return Gdk.Display.get_default()


def pointer():
    """The seat's pointer, or the deprecated device manager on older GTK."""
    display = Gdk.Display.get_default()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        seat = display.get_default_seat() if hasattr(display, "get_default_seat") else None
        if seat is not None:
            device = seat.get_pointer()
            if device is not None:
                return device
        return display.get_device_manager().get_client_pointer()


@pytest.fixture(scope="module")
def warping(display):
    """Skip the pointer tests only when this display cannot move the pointer."""
    device = pointer()
    screen = Gdk.Screen.get_default()
    if device is None or screen is None:
        pytest.skip("no pointer to warp")
    warp_to(device, 4, 4)
    pump(4)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        _screen, x, y = device.get_position()
    if (x, y) != (4, 4):
        pytest.skip(f"this display cannot warp the pointer (it stayed at {x}, {y})")
    warp_to(device, 6, 6)
    pump(2)
    return device


def warp_to(device, x, y) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        device.warp(Gdk.Screen.get_default(), int(x), int(y))


def tile_centre(tile, window):
    """Root coordinates of a tile's centre."""
    _ok, window_x, window_y = window_origin(window.get_window())
    x, y = origin(tile, window)
    return (
        window_x + x + tile.get_allocated_width() // 2,
        window_y + y + tile.get_allocated_height() // 2,
    )


def away_from(window):
    """A root position outside this window, so the pointer leaves it."""
    size = window.get_size()
    _ok, window_x, window_y = window_origin(window.get_window())
    return window_x + size[0] + 40, window_y + size[1] + 40


def park(device, window):
    """Move the real pointer off this window, so no hover is inherited."""
    warp_to(device, *away_from(window))
    pump(3)


def focus(window):
    """Give the window the focus, the way a window manager would.

    Xvfb runs without a window manager, so the X focus is PointerRoot: the
    window only reports itself active while the pointer is inside it, or once
    it is focused here.  A real desktop focuses the window for the user.
    """
    window.present()
    pump(2)
    window.get_window().focus(Gdk.CURRENT_TIME)
    pump(3)
    return window.is_active()


def hover(device, tile, window):
    """Move the real pointer onto a tile, from outside the window.

    Warping twice inside the tile makes sure the window sees motion even when
    the pointer happened to sit on that exact pixel already.
    """
    park(device, window)
    x, y = tile_centre(tile, window)
    warp_to(device, x, y)
    pump(2)
    warp_to(device, x + 3, y)


def empty_spot(window, tiles):
    """A root position inside the window that no tile covers."""
    _ok, window_x, window_y = window_origin(window.get_window())
    width, height = window.get_size()
    areas = []
    for tile in tiles.values():
        x, y = origin(tile, window)
        areas.append(
            (x, y, x + tile.get_allocated_width(), y + tile.get_allocated_height())
        )
    for y in range(6, height - 6, 8):
        for x in range(6, width - 6, 8):
            if not any(x0 <= x < x1 and y0 <= y < y1 for x0, y0, x1, y1 in areas):
                return window_x + x, window_y + y
    return None


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


def library(*, delay: int = 0, **flags):
    """A real window, a flow of real library tiles, and one popover."""
    from lgl import ui

    items = entries()
    view = View(items, **flags)
    window = Gtk.Window()
    # Taller than the two tile rows, so there is empty space under the grid,
    # which is where a pointer can rest without being on a tile.
    window.set_default_size(900, 760)

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


def test_the_text_rules_come_from_the_entry(warping):
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


def test_nothing_starts_when_the_switch_is_off(warping):
    window, _flow, preview, tiles, _view = library(delay=30, quick_preview=False)
    assert preview.enabled() is False

    hover(warping, tiles["game-0"], window)
    pump(12)

    assert preview.pending() is False
    assert preview.get_visible() is False
    window.destroy()


def test_hovering_a_tile_opens_the_popover(warping):
    window, _flow, preview, tiles, view = library(delay=150)

    hover(warping, tiles["game-0"], window)
    assert wait_for(preview.pending)
    assert preview.get_visible() is False  # a pointer that passes over does nothing

    assert wait_for(preview.get_visible)
    assert preview.get_modal() is False
    assert preview.get_relative_to() is tiles["game-0"]
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


def test_moving_the_pointer_off_the_tile_closes_it(warping):
    window, _flow, preview, tiles, _view = library(delay=0)
    hover(warping, tiles["game-0"], window)
    assert wait_for(preview.get_visible)
    assert preview.get_relative_to() is tiles["game-0"]

    park(warping, window)
    assert wait_for(lambda: not preview.get_visible())
    window.destroy()


def test_empty_space_inside_the_window_is_a_leave(warping):
    """Off the tile into empty space closes it; coming back opens it again."""
    window, _flow, preview, tiles, _view = library(delay=0)
    hover(warping, tiles["game-0"], window)
    assert wait_for(preview.get_visible)

    gap = empty_spot(window, tiles)
    assert gap is not None, "the grid left no empty space inside the window"
    warp_to(warping, *gap)
    assert wait_for(lambda: not preview.get_visible())

    x, y = tile_centre(tiles["game-0"], window)
    warp_to(warping, x, y)
    pump(2)
    warp_to(warping, x + 3, y)
    assert wait_for(preview.get_visible), "the same tile did not open again"
    window.destroy()


def test_escape_keeps_it_closed_while_the_pointer_stays(warping):
    """The rule from the other side: a resting pointer must not reopen it."""
    window, flow, preview, tiles, _view = library(delay=0)
    hover(warping, tiles["game-0"], window)
    assert wait_for(preview.get_visible)

    flow.emit("key-press-event", key(Gdk.KEY_Escape))
    pump(2)
    assert preview.get_visible() is False

    x, y = tile_centre(tiles["game-0"], window)
    warp_to(warping, x + 5, y + 5)
    pump(4)

    assert preview.get_visible() is False
    window.destroy()


def test_hover_opens_from_the_pointer_poll_alone(warping):
    """Block our motion handler: the sample of the pointer still finds the tile.

    This is the failing case from the review — a real X pointer whose motion
    events do not reach the toplevel.  Nothing is emitted by hand; the pointer
    is warped and the main loop is pumped.
    """
    window, _flow, preview, tiles, _view = library(delay=150)
    assert focus(window) is True
    gap = empty_spot(window, tiles)
    assert gap is not None
    # Both moves are inside the window, so its focus never changes and the
    # poll keeps running: only the sample can see the tile under the pointer.
    warp_to(warping, *gap)
    assert wait_for(preview.polling)
    window.handler_block(preview._motion_id)
    try:
        warp_to(warping, *tile_centre(tiles["game-0"], window))
        assert wait_for(preview.get_visible), "the poll alone did not open it"
        assert preview.get_relative_to() is tiles["game-0"]
        assert preview.title.get_text() == "Hollow Knight"

        # Off the tile is noticed by the same sample, not by a motion event.
        warp_to(warping, *gap)
        assert wait_for(lambda: not preview.get_visible())
    finally:
        window.handler_unblock(preview._motion_id)
    window.destroy()


def test_the_poll_stops_when_the_switch_goes_off(warping):
    window, _flow, preview, _tiles, view = library(delay=0)
    assert focus(window) is True
    assert preview.polling() is True

    view._settings["quick_preview"] = False

    assert wait_for(lambda: not preview.polling(), 1.0), "still sampling"
    window.destroy()


def test_the_poll_stops_when_the_view_is_unmapped(warping):
    window, flow, preview, _tiles, _view = library(delay=0)
    assert focus(window) is True
    assert preview.polling() is True

    window.hide()
    pump(3)
    assert flow.get_mapped() is False
    assert preview.polling() is False

    window.show_all()
    assert focus(window) is True
    assert preview.polling() is True
    window.destroy()


def test_only_one_poll_runs_at_a_time(warping):
    window, _flow, preview, _tiles, _view = library(delay=0)
    assert focus(window) is True
    preview._stop_poll()
    preview._start_poll()
    first = preview._poll
    assert first

    preview._start_poll()
    preview._start_poll()

    assert preview._poll == first
    preview._stop_poll()
    window.destroy()


def test_the_switch_hides_the_card_tooltip(warping):
    """The card tooltip would repeat the popover, so it is off while this is on."""
    window, _flow, preview, tiles, _view = library(delay=0)
    assert preview.enabled() is True
    assert tiles["game-0"].get_has_tooltip() is False
    window.destroy()

    # A second populate (a refresh) builds new tiles; they are hidden too.
    window, _flow, preview, tiles, _view = library(delay=0)
    assert tiles["game-0"].get_has_tooltip() is False
    window.destroy()

    window, _flow, preview, tiles, _view = library(delay=0, quick_preview=False)
    assert preview.enabled() is False
    assert tiles["game-0"].get_has_tooltip() is True
    window.destroy()


def test_moving_to_another_tile_moves_the_preview(warping):
    window, _flow, preview, tiles, _view = library(delay=0)
    hover(warping, tiles["game-0"], window)
    assert wait_for(preview.get_visible)

    warp_to(warping, *tile_centre(tiles["game-1"], window))
    assert wait_for(lambda: preview.title.get_text() == "Celeste")
    assert preview.get_relative_to() is tiles["game-1"]
    window.destroy()


def test_space_opens_the_selection_and_escape_closes_it(warping):
    """Real keys, with hover parked so only a key can open the popover.

    ``Gtk.test_widget_send_key`` warps the pointer into the window before it
    sends the key, so hover is given an hour: anything that opens here came
    from the key.  Skipped when this X server drops the keys (no XTEST).
    """
    window, flow, preview, tiles, _view = library(delay=60_000)
    park(warping, window)
    assert preview.get_visible() is False
    flow.select_child(tiles["game-0"])
    window.present()
    pump()
    window.get_window().focus(Gdk.CURRENT_TIME)
    pump(3)

    Gtk.test_widget_send_key(window, Gdk.KEY_space, 0)
    if not wait_for(preview.get_visible, 1.0):
        preview.dismiss()
        window.destroy()
        pytest.skip("this X server does not deliver injected key events (no XTEST)")
    assert preview.get_relative_to() is tiles["game-0"]

    Gtk.test_widget_send_key(window, Gdk.KEY_Escape, 0)
    closed = wait_for(lambda: not preview.get_visible(), 1.0)
    preview.dismiss()
    window.destroy()
    assert closed, "Escape did not close the preview"


def test_space_and_escape_through_the_key_handler(warping):
    """The same two keys, delivered without XTest: a real Gdk.EventKey."""
    window, flow, preview, tiles, _view = library(delay=0)
    park(warping, window)
    flow.select_child(tiles["game-0"])
    pump(2)

    flow.emit("key-press-event", key(Gdk.KEY_space))
    pump(2)
    assert preview.get_visible() is True
    assert preview.get_relative_to() is tiles["game-0"]

    flow.emit("key-press-event", key(Gdk.KEY_Escape))
    pump(2)
    assert preview.get_visible() is False
    window.destroy()


def test_a_selection_change_closes_it(warping):
    window, _flow, preview, tiles, _view = library(delay=0)
    hover(warping, tiles["game-0"], window)
    assert wait_for(preview.get_visible)

    # What LibraryView runs from the flow's selected-children-changed signal.
    preview.selection_changed()
    pump()

    assert preview.get_visible() is False
    window.destroy()


def test_it_only_places_the_popover_beside_the_tile(warping):
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


def test_key_events_carry_the_key_that_was_pressed():
    """The handler reads a real Gdk.EventKey, not a made-up object."""
    event = key(Gdk.KEY_Escape)
    assert event.type == Gdk.EventType.KEY_PRESS
    assert event.keyval == Gdk.KEY_Escape
