"""The GTK 3 season comparison: two pickers, three lists, and why they are empty.

The data and the wording live in :mod:`lgl.season_data`; this window only draws
it.  Loading runs on a worker thread and comes back through ``GLib.idle_add``,
like the release calendar; tests pass ``threaded=False`` to stay synchronous.

GTK 3 only, like the rest of the Linux frontend: ``pack_start``,
``Gtk.ComboBoxText`` and ``get_style_context().add_class()``.
"""

from __future__ import annotations

import threading
from typing import Any

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from . import season_data  # noqa: E402

#: ``(result key, heading)`` for the three lists, top to bottom.
SECTIONS = (
    ("only_first", "Only in A"),
    ("only_second", "Only in B"),
    ("shared", "In both (continuing)"),
)

HINT = "Select a title to see its score and episodes."


class SeasonCompareWindow(Gtk.Window):
    """Compare what one anime season has against the next one."""

    def __init__(
        self,
        *,
        parent: Any = None,
        post: Any = None,
        cache_path: Any = None,
        threaded: bool = True,
        autoload: bool = False,
    ) -> None:
        super().__init__(title="Compare seasons")
        self.set_default_size(820, 780)
        if isinstance(parent, Gtk.Window):
            self.set_transient_for(parent)
        self._post = post
        self._cache_path = cache_path
        self._threaded = threaded
        self._busy = False
        self._result = season_data.CompareResult()

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for side in ("top", "bottom", "start", "end"):
            getattr(outer, f"set_margin_{side}")(12)

        heading = Gtk.Label(label="SEASON COMPARISON", xalign=0)
        heading.get_style_context().add_class("section-label")
        outer.pack_start(heading, False, False, 0)

        note = Gtk.Label(
            label=(
                "Two seasons from AniList, compared by series id: what only one "
                "season has and what carried over."
            ),
            xalign=0,
        )
        note.set_line_wrap(True)
        note.get_style_context().add_class("dim-label")
        outer.pack_start(note, False, False, 0)

        season, year = season_data.latest_season()
        before, before_year = season_data.previous_season(season, year)
        self.season_a, self.year_a = self._pickers(outer, "A", season, year)
        self.season_b, self.year_b = self._pickers(outer, "B", before, before_year)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.compare_btn = Gtk.Button(label="Compare")
        self.compare_btn.connect("clicked", lambda *_: self.compare())
        buttons.pack_start(self.compare_btn, False, False, 0)
        self.status = Gtk.Label(label="", xalign=0)
        self.status.set_line_wrap(True)
        self.status.get_style_context().add_class("dim-label")
        buttons.pack_start(self.status, True, True, 0)
        outer.pack_start(buttons, False, False, 0)

        self.summary = Gtk.Label(label=season_data.season_compare.summary({}), xalign=0)
        self.summary.set_line_wrap(True)
        outer.pack_start(self.summary, False, False, 0)

        # A failure has to be readable, so it keeps the normal text colour
        # instead of the dim helper style used for hints.
        self.error = Gtk.Label(label="", xalign=0)
        self.error.set_line_wrap(True)
        self.error.set_no_show_all(True)
        outer.pack_start(self.error, False, False, 0)

        self.lists: dict[str, Gtk.ListBox] = {}
        self.section_heads: dict[str, Gtk.Label] = {}
        self.scrollers: dict[str, Gtk.ScrolledWindow] = {}
        for key, title in SECTIONS:
            label = Gtk.Label(label=title, xalign=0)
            label.get_style_context().add_class("section-label")
            outer.pack_start(label, False, False, 0)
            listing = Gtk.ListBox()
            listing.set_selection_mode(Gtk.SelectionMode.SINGLE)
            listing.connect("row-selected", self._on_selected)
            scroller = Gtk.ScrolledWindow()
            scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroller.set_size_request(-1, 120)
            scroller.add(listing)
            outer.pack_start(scroller, True, True, 0)
            self.lists[key] = listing
            self.section_heads[key] = label
            self.scrollers[key] = scroller

        self.detail = Gtk.Label(label=HINT, xalign=0)
        self.detail.set_line_wrap(True)
        self.detail.get_style_context().add_class("dim-label")
        outer.pack_start(self.detail, False, False, 0)

        foot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        close = Gtk.Button(label="Close")
        close.connect("clicked", lambda *_: self.close())
        foot.pack_end(close, False, False, 0)
        outer.pack_start(foot, False, False, 0)

        self.add(outer)
        outer.show_all()
        self.error.hide()
        if autoload:
            self.compare()

    # -- pickers -----------------------------------------------------------
    def _pickers(self, outer: Gtk.Box, side: str, season: str, year: int,
                 ) -> tuple[Gtk.ComboBoxText, Gtk.ComboBoxText]:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        tag = Gtk.Label(label=side, xalign=0)
        tag.get_style_context().add_class("section-label")
        row.pack_start(tag, False, False, 0)

        seasons = Gtk.ComboBoxText()
        for name in season_data.SEASONS:
            seasons.append(name, season_data.SEASON_NAMES[name])
        seasons.set_active_id(season)
        seasons.set_tooltip_text("Season")
        row.pack_start(seasons, False, False, 0)

        years = Gtk.ComboBoxText()
        current = season_data.latest_season()[1]
        for number in range(current + 1, current - 12, -1):
            years.append(str(number), str(number))
        years.set_active_id(str(year))
        years.set_tooltip_text("Year")
        row.pack_start(years, False, False, 0)

        row.pack_start(Gtk.Box(), True, True, 0)
        outer.pack_start(row, False, False, 0)
        return seasons, years

    # -- data --------------------------------------------------------------
    def values(self) -> tuple[Any, Any, Any, Any]:
        return (
            self.season_a.get_active_id(),
            self.year_a.get_active_id(),
            self.season_b.get_active_id(),
            self.year_b.get_active_id(),
        )

    def compare(self) -> None:
        """Load both seasons and draw the comparison."""
        if self._busy:
            return
        self._busy = True
        self.compare_btn.set_sensitive(False)
        self.status.set_text("Loading both seasons…")
        self.error.set_text("")
        self.error.hide()
        picked = self.values()

        def load() -> None:
            try:
                result = season_data.compare(
                    *picked, post=self._post, cache_path=self._cache_path
                )
            except Exception as exc:  # noqa: BLE001 - shown, never raised
                result = season_data.failed_compare(season_data.reason_of(exc))
            if self._threaded:
                GLib.idle_add(self.set_result, result)
            else:
                self.set_result(result)

        if self._threaded:
            threading.Thread(target=load, daemon=True).start()
        else:
            load()

    def set_result(self, result: season_data.CompareResult) -> bool:
        """Draw ``result`` without loading anything (tests and screenshots)."""
        self._busy = False
        self.compare_btn.set_sensitive(True)
        self._result = result

        self.summary.set_text(result.summary)
        error = result.error_text
        self.error.set_text(error)
        if error:
            self.error.show()
        else:
            self.error.hide()

        for key, _title in SECTIONS:
            listing = self.lists[key]
            for child in listing.get_children():
                listing.remove(child)
            # A failure shows the error line and the controls, not three
            # empty boxes under their headings.
            self.section_heads[key].set_visible(not error)
            self.scrollers[key].set_visible(not error)
            for entry in getattr(result, key):
                row = Gtk.ListBoxRow()
                row.entry = dict(entry)
                label = Gtk.Label(label=season_data.entry_line(entry), xalign=0)
                label.set_line_wrap(True)
                label.set_tooltip_text(season_data.entry_details(entry))
                row.add(label)
                listing.add(row)
            listing.show_all()

        notes: list[str] = []
        if result.saved_titles:
            notes.append("Showing saved titles from the last successful load.")
        if not (result.only_first or result.only_second or result.shared):
            notes.append("Nothing to compare yet.")
        self.status.set_text(" ".join(notes))
        self.detail.set_text(HINT)
        return False

    # -- selection ---------------------------------------------------------
    def _on_selected(self, listing: Gtk.ListBox, row: Any) -> None:
        entry = getattr(row, "entry", None) if row is not None else None
        self.detail.set_text(season_data.entry_details(entry) if entry else HINT)


def show_season_compare(parent: Any = None) -> SeasonCompareWindow:
    """Open the comparison from the app menu."""
    window = SeasonCompareWindow(parent=parent, autoload=True)
    window.show_all()
    window.present()
    return window


__all__ = ["HINT", "SECTIONS", "SeasonCompareWindow", "show_season_compare"]
