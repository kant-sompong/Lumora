"""Hover preview for library tiles on the Linux (GTK 3) frontend.

The switch is the shared one in Tools → Interface (``view_prefs.quick_preview``).
Everything the popover shows comes from the entry the library already loaded —
the metadata, the cached cover, the tags in settings — so showing it never
touches the network and never opens a file.

It is a non-modal ``Gtk.Popover``: it takes no focus, and it goes away on a
pointer leave, Escape, a selection change, a grid rebuild and when the window
is deactivated.  Placement is decided from the grid's own geometry, so the
popover never covers the tile it describes.

GTK 3 only: ``set_relative_to``/``set_position`` on the popover,
``pack_start`` and ``get_style_context().add_class()``.  Nothing here uses a
Gtk 4.0 only API.
"""

from __future__ import annotations

from typing import Any

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango

from . import library_tools, view_prefs
from .util import human_size

#: How long the pointer has to rest on a tile before the popover opens.
HOVER_MS = 400
#: How often the pointer is sampled, for when motion events do not arrive.
POLL_MS = 100
#: Cover box in the popover.
COVER = (96, 128)
#: How much of the description is shown: the first two non-empty lines.
DETAIL_LINES = 2
#: A single very long first line is cut here so the popover stays a popover.
LINE_CHARS = 120
POPOVER_WIDTH = 320
#: The narrowest the popover is allowed to be before it gives up.
MIN_WIDTH = 220
#: Space left between the popover and the tile it belongs to.
GAP = 8
#: The popover's own border and shadow, which the child's size does not cover.
CHROME = 24
#: The order the four positions are tried in.
POSITIONS = (
    Gtk.PositionType.RIGHT,
    Gtk.PositionType.LEFT,
    Gtk.PositionType.BOTTOM,
    Gtk.PositionType.TOP,
)


def preview_text(entry: Any) -> str:
    """The first two non-empty lines of a description, each trimmed.

    The same rule as the Qt popover, so both frontends cut a blurb the same way.
    """
    raw = str(getattr(entry, "description", "") or "")
    lines = [line.strip() for line in raw.splitlines()]
    kept = [line[:LINE_CHARS] for line in lines if line][:DETAIL_LINES]
    return "\n".join(kept)


def meta_text(entry: Any) -> str:
    """Year and size, the two facts a card shows as well."""
    bits = [
        str(getattr(entry, "year", "") or "").strip(),
        human_size(getattr(entry, "size", 0)) if getattr(entry, "size", 0) else "",
    ]
    return " · ".join(bit for bit in bits if bit) or "—"


def rect(x: int, y: int, width: int, height: int) -> Gdk.Rectangle:
    """A rectangle with its fields set; PyGObject ignores constructor arguments."""
    rectangle = Gdk.Rectangle()
    rectangle.x, rectangle.y = int(x), int(y)
    rectangle.width, rectangle.height = int(width), int(height)
    return rectangle


def spot(position: Gtk.PositionType, tile: Gdk.Rectangle,
         size: tuple[int, int]) -> Gdk.Rectangle:
    """Where a popover of ``size`` sits for one position, beside the tile."""
    width, height = size
    if position == Gtk.PositionType.RIGHT:
        return rect(tile.x + tile.width + GAP, tile.y, width, height)
    if position == Gtk.PositionType.LEFT:
        return rect(tile.x - GAP - width, tile.y, width, height)
    if position == Gtk.PositionType.BOTTOM:
        return rect(tile.x, tile.y + tile.height + GAP, width, height)
    return rect(tile.x, tile.y - GAP - height, width, height)


def window_origin(window: Gdk.Window) -> tuple[int, int, int]:
    """A Gdk window's root position, as ``(ok, x, y)``, whatever the build."""
    moved = window.get_origin()
    if len(moved) == 3:
        return int(moved[0]), int(moved[1]), int(moved[2])
    return 1, int(moved[0]), int(moved[1])


def origin(widget: Gtk.Widget, target: Gtk.Widget) -> tuple[int, int]:
    """A widget's top-left corner in another widget's coordinates.

    PyGObject returns ``(x, y)`` from ``translate_coordinates`` in some builds
    and ``(ok, x, y)`` in others, so both shapes are accepted.
    """
    moved = widget.translate_coordinates(target, 0, 0)
    if len(moved) == 3:
        return int(moved[1]), int(moved[2])
    return int(moved[0]), int(moved[1])


def fits(rect: Gdk.Rectangle, area: Gdk.Rectangle) -> bool:
    """True when the popover rectangle stays inside the grid area."""
    return (
        rect.x >= area.x
        and rect.y >= area.y
        and rect.x + rect.width <= area.x + area.width
        and rect.y + rect.height <= area.y + area.height
    )


def choose(tile: Gdk.Rectangle, size: tuple[int, int],
           area: Gdk.Rectangle) -> Gtk.PositionType | None:
    """Right, left, bottom, then top: the first side with room for the popover.

    Every candidate sits beside the tile, so a position that fits can never
    overlap it.  ``None`` means there is no room on any side.
    """
    for position in POSITIONS:
        if fits(spot(position, tile, size), area):
            return position
    return None


def cover_pixbuf(pixbuf: GdkPixbuf.Pixbuf | None,
                 size: tuple[int, int]) -> GdkPixbuf.Pixbuf | None:
    """Scale a cover to fill ``size`` and crop the overflow, keeping the ratio."""
    if pixbuf is None or pixbuf.get_width() <= 0 or pixbuf.get_height() <= 0:
        return None
    width, height = size
    scale = max(width / pixbuf.get_width(), height / pixbuf.get_height())
    scaled = pixbuf.scale_simple(
        max(width, round(pixbuf.get_width() * scale)),
        max(height, round(pixbuf.get_height() * scale)),
        GdkPixbuf.InterpType.BILINEAR,
    )
    if scaled is None:  # pragma: no cover - scale_simple only fails on bad input
        return None
    left = max(0, (scaled.get_width() - width) // 2)
    top = max(0, (scaled.get_height() - height) // 2)
    return scaled.new_subpixbuf(left, top, width, height)


class QuickPreview(Gtk.Popover):
    """A small, focus-less popover built from one library entry."""

    def __init__(self, view: Any, *, delay: int = HOVER_MS) -> None:
        super().__init__()
        self._view = view
        self._delay = max(0, int(delay))
        self._entry: Any = None
        self._tile: Gtk.Widget | None = None
        self._tiles: dict[Gtk.Widget, Any] = {}
        self._source = 0
        self._window: Gtk.Window | None = None
        self._flow: Gtk.Widget | None = None
        self._watched: list[Gtk.Window] = []
        self._hovered: Gtk.Widget | None = None
        self._tips: bool | None = None
        self._poll = 0
        self._motion_id = 0
        self._last_at: tuple[int, int] | None = None
        self.set_modal(False)
        self.set_position(Gtk.PositionType.RIGHT)
        self.get_style_context().add_class("quick-preview")

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        body.set_size_request(POPOVER_WIDTH, -1)
        body.set_margin_start(10)
        body.set_margin_end(12)
        body.set_margin_top(10)
        body.set_margin_bottom(10)

        self.cover = Gtk.Image()
        self.cover.set_size_request(*COVER)
        self.cover.set_valign(Gtk.Align.START)
        body.pack_start(self.cover, False, False, 0)

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.title = Gtk.Label(xalign=0)
        self.title.set_ellipsize(Pango.EllipsizeMode.END)
        self.title.set_max_width_chars(34)
        self.title.get_style_context().add_class("tile-title")
        column.pack_start(self.title, False, False, 0)

        self.meta = Gtk.Label(xalign=0)
        self.meta.set_ellipsize(Pango.EllipsizeMode.END)
        self.meta.set_max_width_chars(34)
        self.meta.get_style_context().add_class("tile-meta")
        column.pack_start(self.meta, False, False, 0)

        self.tags = Gtk.Label(xalign=0)
        self.tags.set_line_wrap(True)
        self.tags.set_max_width_chars(38)
        self.tags.get_style_context().add_class("tile-meta")
        column.pack_start(self.tags, False, False, 0)

        self.description = Gtk.Label(xalign=0)
        self.description.set_line_wrap(True)
        self.description.set_max_width_chars(38)
        self.description.get_style_context().add_class("tile-meta")
        column.pack_start(self.description, False, False, 0)
        body.pack_start(column, True, True, 0)

        self.add(body)
        self._body = body
        body.show_all()

        self.connect("key-press-event", self._on_key)
        self.connect("show", self._on_shown)

    # -- what the switch says ------------------------------------------------
    def settings(self) -> Any:
        return getattr(self._view, "_settings", None)

    def enabled(self) -> bool:
        return bool(view_prefs.quick_preview(self.settings()))

    def pending(self) -> bool:
        """True while the hover timer is running."""
        return bool(self._source)

    # -- tracking tiles ------------------------------------------------------
    def attach(self, flow: Gtk.Widget) -> None:
        """Follow the pointer over the grid and catch Space while it has focus."""
        self._flow = flow
        flow.connect("key-press-event", self._on_key)
        flow.connect("map", self._watch_pointer)
        flow.connect("unmap", self._on_unmap)
        self._watch_pointer()

    def _watch_pointer(self, *_args: Any) -> None:
        """Follow the pointer on the window that owns the tiles.

        A ``Gtk.FlowBoxChild`` has no ``GdkWindow`` of its own, so it never sees
        crossing events however many masks it asks for — the window under the
        pointer is the toplevel's.  Motion there is the fast path; the pointer
        is also sampled, because some windows drop those events entirely.
        """
        self._start_poll()
        flow = self._flow
        if flow is None:
            return
        toplevel = flow.get_toplevel()
        if not isinstance(toplevel, Gtk.Window) or toplevel in self._watched:
            return
        toplevel.add_events(
            Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        self._motion_id = toplevel.connect("motion-notify-event", self._on_motion)
        toplevel.connect("leave-notify-event", self._on_window_leave)
        self._watched.append(toplevel)
        self._window = toplevel
        try:
            toplevel.connect("notify::is-active", self._on_active)
        except TypeError:  # pragma: no cover - older GTK without the property
            toplevel.connect("focus-out-event", self._on_focus_out)

    # -- polling for the pointer ---------------------------------------------
    def polling(self) -> bool:
        """True while the pointer sampling source is running."""
        return bool(self._poll)

    def _wanted(self) -> bool:
        """Poll only for a mapped, enabled view in the active window."""
        flow = self._flow
        if flow is None or not self.enabled():
            return False
        if not flow.get_mapped():
            return False
        toplevel = self._toplevel()
        return toplevel is not None and toplevel.is_active()

    def _start_poll(self) -> None:
        """Sample the pointer position until something says to stop.

        Motion events are only a fast path: some windows never see them with a
        real X pointer, so the tile under the pointer is read from the seat too.
        """
        if self._poll or not self._wanted():
            return
        self._poll = GLib.timeout_add(POLL_MS, self._on_poll)

    def _stop_poll(self) -> None:
        self._last_at = None
        if self._poll:
            GLib.source_remove(self._poll)
            self._poll = 0

    def _on_poll(self) -> bool:
        self._poll = 0
        if not self._wanted():
            return False
        self._poll = GLib.timeout_add(POLL_MS, self._on_poll)
        position = self._pointer()
        if position is None:
            return False
        # Finding the tile costs a walk over every card, so only do it when
        # the pointer has actually moved: an idle app weighs nothing.
        if position == self._last_at:
            return False
        self._last_at = position
        self._update(self._tile_at(*position))
        return False

    def _pointer(self) -> tuple[int, int] | None:
        """The pointer's root position, or None when there is no pointer."""
        display = Gdk.Display.get_default()
        if display is None:
            return None
        device = None
        if hasattr(display, "get_default_seat"):
            seat = display.get_default_seat()
            if seat is not None:
                device = seat.get_pointer()
        if device is None:  # pragma: no cover - older GTK without seats
            manager = display.get_device_manager()
            device = manager.get_client_pointer() if manager is not None else None
        if device is None:  # pragma: no cover - no pointer device at all
            return None
        moved = device.get_position()
        if len(moved) == 3:
            return int(moved[1]), int(moved[2])
        return int(moved[0]), int(moved[1])

    def _on_unmap(self, *_args: Any) -> None:
        """The grid is gone: nothing to look at until it is mapped again."""
        self._stop_poll()
        self._hovered = None
        if self.get_visible():
            self.dismiss(remember=False)

    def track(self, tile: Gtk.Widget) -> None:
        """Watch one tile; called from the view for every rendered card."""
        if tile in self._tiles:
            return
        self._tiles[tile] = getattr(tile, "entry", None)
        tile.connect("key-press-event", self._on_key)
        self._tooltips([tile])
        # A populate is also how the switch comes back on, so look again.
        self._start_poll()

    def _tooltips(self, tiles: list[Gtk.Widget] | None = None) -> None:
        """Hide the cards' own tooltips while the popover says the same thing.

        The view still sets the tooltip text; only the showing of it is turned
        off, so switching quick preview back off restores it untouched.  Every
        tile is set when it is tracked, and the switch is re-checked on pointer
        motion, which covers it being flipped while the grid stays as it is.
        """
        wanted = not self.enabled()
        self._tips = wanted
        for tile in list(self._tiles) if tiles is None else tiles:
            if tile.get_has_tooltip() != wanted:
                tile.set_has_tooltip(wanted)

    def _toplevel(self) -> Gtk.Window | None:
        widget = self._flow if self._flow is not None else self._view
        if not isinstance(widget, Gtk.Widget):
            return None
        toplevel = widget.get_toplevel()
        return toplevel if isinstance(toplevel, Gtk.Window) else None

    def _tile_at(self, x_root: float, y_root: float) -> Gtk.Widget | None:
        """The tracked tile under a root-coordinate pointer position."""
        toplevel = self._toplevel()
        if toplevel is None or toplevel.get_window() is None:
            return None
        _ok, window_x, window_y = window_origin(toplevel.get_window())
        for tile in list(self._tiles):
            if not tile.get_mapped():
                continue
            x, y = origin(tile, toplevel)
            width, height = tile.get_allocated_width(), tile.get_allocated_height()
            if (
                window_x + x <= x_root < window_x + x + width
                and window_y + y <= y_root < window_y + y + height
            ):
                return tile
        return None

    def _update(self, tile: Gtk.Widget | None) -> None:
        """The one place that reacts to which tile the pointer is over.

        Motion events and the pointer poll both come through here, so they can
        never disagree about the hovered tile.
        """
        if self._tips != (not self.enabled()):
            self._tooltips()
        if not self.enabled():
            if self.get_visible():
                self.dismiss(remember=False)
            return
        self._start_poll()
        if tile is self._hovered:
            return
        self._hovered = tile
        if tile is None:
            # Empty space inside the window is a leave: the tile under the
            # pointer must not stay remembered, or coming back to it would
            # never open the preview again.
            if self.get_visible():
                self.dismiss(remember=False)
            return
        self._hover(tile)

    def _on_motion(self, _widget: Gtk.Widget, event: Gdk.EventMotion) -> bool:
        """Fast path: the window did deliver motion, so there is no poll lag."""
        self._update(self._tile_at(event.x_root, event.y_root))
        return False

    def _on_window_leave(self, _widget: Gtk.Widget,
                         _event: Gdk.EventCrossing) -> bool:
        """The pointer left the window the tiles live in."""
        self._update(None)
        return False

    def _hover(self, tile: Gtk.Widget) -> None:
        entry = self._tiles.get(tile)
        if entry is None or not tile.get_mapped():
            return
        self._entry = entry
        self._tile = tile
        self._restart()

    # -- opening and closing -------------------------------------------------
    def open_selected(self) -> bool:
        """Open on the tile that is already selected (the Space key)."""
        if not self.enabled():
            return False
        selector = getattr(self._view, "_selected_entry", None)
        entry = selector() if callable(selector) else None
        if entry is None:
            return False
        tile = (getattr(self._view, "_tiles", {}) or {}).get(entry.identifier)
        if tile is None:
            return False
        self._cancel()
        return self._show(entry, tile)

    def selection_changed(self) -> None:
        """The library picked a different game: the preview belongs to the old one."""
        self.dismiss()

    def grid_rebuilt(self) -> None:
        """The grid threw its tiles away; the preview was anchored to one of them."""
        self.dismiss()

    def dismiss(self, *, remember: bool = True) -> None:
        """Close the popover and forget the tile it was pointing at.

        The tile the popover belonged to stays remembered, so the next motion
        event on that same tile does not open the preview again: after Escape
        or a selection change it takes a move to a different tile (or leaving
        and coming back).  ``remember=False`` is for the pointer simply moving
        off the tile, where coming back must open it again.
        """
        self._cancel()
        self._hovered = (self._hovered or self._tile) if remember else None
        self._entry = None
        self._tile = None
        if self.get_visible():
            # popdown() alone keeps the widget visible until the hide
            # transition ends; hide() makes the state immediate.
            self.popdown()
            self.hide()

    def _restart(self) -> None:
        self._cancel()
        if self._delay:
            self._source = GLib.timeout_add(self._delay, self._on_timer)
        else:
            self._show(self._entry, self._tile)

    def _cancel(self) -> None:
        if self._source:
            GLib.source_remove(self._source)
            self._source = 0

    def _on_timer(self) -> bool:
        self._source = 0
        self._show(self._entry, self._tile)
        return False

    def _show(self, entry: Any, tile: Gtk.Widget | None) -> bool:
        if entry is None or tile is None or not self.enabled():
            return False
        if not tile.get_mapped():
            return False
        self._entry = entry
        self._tile = tile
        self.fill(entry)
        self.set_relative_to(tile)
        if not self._place(tile):
            # No side has room: no popover is better than one on the tile.
            self._entry = None
            self._tile = None
            return False
        self.popup()
        return True

    # -- content -------------------------------------------------------------
    def fill(self, entry: Any) -> None:
        """Fill every line from the entry itself."""
        self.title.set_text(entry.title or entry.identifier)
        self.meta.set_text(meta_text(entry))
        tags = library_tools.tags_for(self.settings(), entry)
        self.tags.set_text("Tags: " + ", ".join(tags) if tags else "")
        self.tags.set_visible(bool(tags))
        detail = preview_text(entry)
        self.description.set_text(detail)
        self.description.set_visible(bool(detail))
        self._fill_cover(entry)

    def _fill_cover(self, entry: Any) -> None:
        self.cover.set_from_icon_name(
            "applications-games-symbolic", Gtk.IconSize.DIALOG
        )
        path = str(getattr(entry, "cover_path", "") or "")
        request = getattr(self._view, "request_cover", None)
        if not path or not callable(request):
            return
        request(path, lambda pixbuf: self._cover_ready(entry, pixbuf))

    def _cover_ready(self, entry: Any, pixbuf: Any) -> None:
        if entry is not self._entry or not self.get_visible():
            return
        scaled = cover_pixbuf(pixbuf, COVER)
        if scaled is not None:
            self.cover.set_from_pixbuf(scaled)

    # -- placement -----------------------------------------------------------
    def _size(self) -> tuple[int, int]:
        """The body's natural size plus the popover's own chrome."""
        _minimum, natural = self._body.get_preferred_size()
        width = natural.width if natural.width > 0 else POPOVER_WIDTH
        height = natural.height if natural.height > 0 else COVER[1]
        return max(width, MIN_WIDTH) + CHROME, height + CHROME

    def _anchor(self, tile: Gtk.Widget) -> tuple[Gdk.Rectangle, Gdk.Rectangle]:
        """The tile and the room around it, both in toplevel coordinates."""
        toplevel = tile.get_toplevel()
        x, y = origin(tile, toplevel)
        tile_rect = rect(
            x, y, tile.get_allocated_width(), tile.get_allocated_height()
        )
        area = rect(
            0, 0,
            max(1, toplevel.get_allocated_width()),
            max(1, toplevel.get_allocated_height()),
        )
        grid = getattr(self._view, "_grid_scroll", None)
        if isinstance(grid, Gtk.Widget) and grid.get_realized():
            gx, gy = origin(grid, toplevel)
            width, height = grid.get_allocated_width(), grid.get_allocated_height()
            if width > 80 and height > 80:
                area = rect(gx, gy, width, height)
        return tile_rect, area

    def _place(self, tile: Gtk.Widget) -> bool:
        """Pick a side of the tile with room; the popover never covers it."""
        tile_rect, area = self._anchor(tile)
        position = choose(tile_rect, self._size(), area)
        if position is None:
            return False
        self.set_position(position)
        return True

    # -- keys and the window -------------------------------------------------
    def typing(self) -> bool:
        """True when a text field has focus, so Space is a space, not a preview."""
        toplevel = self._view.get_toplevel() if isinstance(self._view, Gtk.Widget) else None
        focus = toplevel.get_focus() if isinstance(toplevel, Gtk.Window) else None
        return isinstance(focus, (Gtk.Entry, Gtk.TextView))

    def _on_key(self, _widget: Gtk.Widget, event: Gdk.EventKey) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            if not self.get_visible():
                return False
            self.dismiss()
            return True
        if event.keyval in (Gdk.KEY_space, Gdk.KEY_KP_Space):
            if self.typing():
                return False
            if self.get_visible():
                self.dismiss()
                return True
            return self.open_selected()
        return False

    def _on_shown(self, _widget: Gtk.Widget) -> None:
        """Watch the toplevel so the preview goes away with the window's focus."""
        toplevel = self.get_toplevel()
        if not isinstance(toplevel, Gtk.Window) or toplevel is self._window:
            return
        self._window = toplevel
        try:
            toplevel.connect("notify::is-active", self._on_active)
        except TypeError:  # pragma: no cover - older GTK without the property
            toplevel.connect("focus-out-event", self._on_focus_out)

    def _on_active(self, window: Gtk.Window, _param: Any = None) -> None:
        """Do no work in the background: sample the pointer only when focused."""
        if window.is_active():
            self._start_poll()
            return
        self._stop_poll()
        # Losing the window is like leaving it, not like pressing Escape.
        self._hovered = None
        if self.get_visible():
            self.dismiss(remember=False)

    def _on_focus_out(self, _window: Gtk.Window,
                      _event: Gdk.EventFocus) -> bool:  # pragma: no cover - fallback
        if self.get_visible() and not self.typing():
            self.dismiss()
        return False


__all__ = [
    "COVER",
    "DETAIL_LINES",
    "GAP",
    "HOVER_MS",
    "LINE_CHARS",
    "MIN_WIDTH",
    "POPOVER_WIDTH",
    "POSITIONS",
    "QuickPreview",
    "choose",
    "cover_pixbuf",
    "fits",
    "meta_text",
    "origin",
    "preview_text",
    "rect",
    "spot",
]
