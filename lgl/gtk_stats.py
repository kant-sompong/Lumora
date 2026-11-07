"""The GTK 3 statistics window: totals, a 12-week heatmap and metadata coverage.

The numbers live in :mod:`lgl.stats`; this window gathers events (added and
launched games plus the viewing history), draws the grid with cairo and says
plainly when nothing has been recorded.  Nothing here measures viewing time.
GTK 3 only: ``pack_start``, ``.add()`` and ``get_style_context().add_class()``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from . import recently_viewed, stats

#: ``(field, verb)`` pairs turned into events for a library entry.  ``last_seen``
#: is deliberately absent: every scan sets it to now and would drown the real
#: dates under one giant "today" bar.
ENTRY_EVENTS = (
    ("added_at", "Added"),
    ("last_played", "Launched"),
)

#: How many weeks the heatmap covers.
HEATMAP_WEEKS = 12

#: The accent used for filled cells.
CELL_COLOUR = (0.26, 0.55, 0.96)


def build_events(
    entries: Iterable[Any], viewed: Iterable[Mapping[str, Any]] = ()
) -> list[dict[str, Any]]:
    """Added and launched games plus the viewing history."""
    events: list[dict[str, Any]] = []
    for entry in entries:
        title = str(
            getattr(entry, "title", "")
            or getattr(entry, "identifier", "")
            or ""
        ).strip()
        for field, _verb in ENTRY_EVENTS:
            stamp = getattr(entry, field, 0.0)
            if stamp:
                events.append({"ts": stamp, "kind": "game", "title": title})
    for item in viewed:
        stamp = item.get("viewed_at")
        if not stamp:
            continue
        events.append(
            {
                "ts": stamp,
                "kind": str(item.get("kind") or "media"),
                "title": str(item.get("title") or ""),
            }
        )
    return events


def heatmap_grid(
    events: Iterable[Mapping[str, Any]],
    weeks: int = HEATMAP_WEEKS,
    *,
    today: Any = None,
) -> list[list[int]]:
    """Seven weekday rows by ``weeks`` columns, zeros where nothing happened."""
    return stats.heatmap(events, weeks, today=today)


def genre_summary(entries: Iterable[Any], limit: int = 5) -> list[tuple[str, int]]:
    """Favourite genres from the library entries, as ``stats`` counts them."""
    return stats.favourite_genres(
        [{"genres": list(getattr(entry, "genres", ()) or ())} for entry in entries],
        limit=limit,
    )


def coverage_summary(entries: Iterable[Any]) -> dict[str, int]:
    """How many titles carry a poster, description, year and genres."""
    return stats.coverage(
        [
            {
                "poster": getattr(entry, "cover_path", ""),
                "description": getattr(entry, "description", ""),
                "year": getattr(entry, "year", ""),
                "genres": getattr(entry, "genres", ()),
                "rating": None,
            }
            for entry in entries
        ]
    )


def library_entries(parent: Any) -> list[Any]:
    """Everything the library index holds."""
    store = getattr(parent, "library_store", None)
    getter = getattr(store, "entries", None)
    if not callable(getter):
        return []
    try:
        return list(getter())
    except Exception:  # noqa: BLE001 - an unreadable index is empty, not fatal
        return []


def _note(text: str = "") -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0)
    label.set_line_wrap(True)
    label.set_max_width_chars(64)
    label.get_style_context().add_class("dim-label")
    return label


def _heading(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0)
    label.get_style_context().add_class("panel-title")
    return label


class StatisticsWindow(Gtk.Window):
    """A read-only summary of the collection and the recorded activity."""

    def __init__(
        self,
        parent: Any,
        entries: Iterable[Any],
        viewed: Iterable[Mapping[str, Any]] = (),
    ) -> None:
        super().__init__(title="Statistics")
        if isinstance(parent, Gtk.Window):
            self.set_transient_for(parent)
        self.entries = list(entries)
        self.events = build_events(self.entries, viewed)
        self.totals = stats.totals(self.events)
        self.busiest = stats.busiest_day(self.events)
        self.grid = heatmap_grid(self.events)
        self.genres = genre_summary(self.entries)
        self.coverage = coverage_summary(self.entries)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for side in ("top", "bottom", "start", "end"):
            getattr(outer, f"set_margin_{side}")(12)
        self.add(outer)
        outer.pack_start(_heading("STATISTICS"), False, False, 0)

        self.summary = _note(
            f"{self.coverage['titles']} titles · {self.totals['events']} events "
            f"· {self.totals['days']} active days · longest streak "
            f"{self.totals['streak']} days"
        )
        outer.pack_start(self.summary, False, False, 0)

        self.busiest_label = _note(
            f"Busiest day: {self.busiest[0]} ({self.busiest[1]} events)"
            if self.busiest
            else "No activity yet."
        )
        outer.pack_start(self.busiest_label, False, False, 0)

        outer.pack_start(_note(f"Last {HEATMAP_WEEKS} weeks"), False, False, 0)
        self.area = Gtk.DrawingArea()
        self.area.set_size_request(-1, 130)
        self.area.connect("draw", self._draw_grid)
        outer.pack_start(self.area, False, False, 0)

        self.genres_label = _note(
            "Favourite genres: "
            + (
                " · ".join(f"{name} {count}" for name, count in self.genres)
                if self.genres
                else "none recorded"
            )
        )
        outer.pack_start(self.genres_label, False, False, 0)

        self.coverage_label = _note(
            "Metadata: "
            f"posters {self.coverage['poster']}/{self.coverage['titles']} · "
            f"descriptions {self.coverage['description']}/{self.coverage['titles']} · "
            f"years {self.coverage['year']}/{self.coverage['titles']} · "
            f"genres {self.coverage['genres']}/{self.coverage['titles']}"
        )
        outer.pack_start(self.coverage_label, False, False, 0)

        self.empty = _note(
            ""
            if self.events
            else "No events recorded yet: nothing has been added, launched or "
            "watched, so every total is 0 and the heatmap is blank."
        )
        outer.pack_start(self.empty, False, False, 0)

        outer.pack_start(
            _note(
                "Nothing here measures viewing time — only the dates titles "
                "were added, launched or watched are recorded."
            ),
            False,
            False,
            0,
        )

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        close = Gtk.Button(label="Close")
        close.connect("clicked", lambda *_: self.close())
        buttons.pack_end(close, False, False, 0)
        outer.pack_start(buttons, False, False, 0)

    # -- drawing --------------------------------------------------------
    def _draw_grid(self, widget: Gtk.Widget, cr: Any) -> bool:
        """Seven weekday rows by twelve week columns, empty cells outlined."""
        rows = len(self.grid)
        columns = len(self.grid[0]) if self.grid else 0
        if not rows or not columns:
            return False
        width = float(widget.get_allocated_width())
        height = float(widget.get_allocated_height())
        if width <= 0 or height <= 0:
            return False
        gap = 3.0
        cell = min(
            (width - gap * (columns - 1)) / columns,
            (height - gap * (rows - 1)) / rows,
        )
        if cell <= 0:
            return False
        peak = max((max(row) for row in self.grid), default=0) or 1
        for row_index, row in enumerate(self.grid):
            for column_index, count in enumerate(row):
                x = column_index * (cell + gap)
                y = row_index * (cell + gap)
                if count:
                    alpha = 0.35 + 0.65 * min(1.0, count / peak)
                    cr.set_source_rgba(*CELL_COLOUR, alpha)
                    cr.rectangle(x, y, cell, cell)
                    cr.fill()
                else:
                    cr.set_source_rgba(0.5, 0.5, 0.5, 0.12)
                    cr.rectangle(x, y, cell, cell)
                    cr.fill()
                    cr.set_source_rgba(0.5, 0.5, 0.5, 0.35)
                    cr.rectangle(x + 0.5, y + 0.5, cell - 1, cell - 1)
                    cr.set_line_width(1)
                    cr.stroke()
        return False


def show_statistics(parent: Any) -> StatisticsWindow:
    """Open Statistics for the library and the shared viewing history."""
    entries = library_entries(parent)
    viewed = recently_viewed.recent(recently_viewed.default_path(), 200)
    window = StatisticsWindow(parent, entries, viewed)
    window.show_all()
    window.present()
    return window


__all__ = [
    "ENTRY_EVENTS",
    "HEATMAP_WEEKS",
    "StatisticsWindow",
    "build_events",
    "coverage_summary",
    "genre_summary",
    "heatmap_grid",
    "library_entries",
    "show_statistics",
]
