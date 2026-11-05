"""The GTK release calendar: what airs or releases on which day.

Data comes from :mod:`lgl.release_feed` (fetched on a worker thread so the
window opens at once and never freezes); grouping, following and date
formatting come from :mod:`lgl.anime_calendar`.  GTK 3, like the rest of the
Linux app.
"""

from __future__ import annotations

import threading
from typing import Any

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from . import anime_calendar as cal  # noqa: E402
from . import release_feed  # noqa: E402

TZ = "local"


class CalendarWindow(Gtk.Window):
    def __init__(self, settings: Any, *, transient_for: Gtk.Window | None = None, days: int = 14):
        super().__init__(title="Release calendar")
        self.set_default_size(720, 640)
        if transient_for is not None:
            self.set_transient_for(transient_for)
        self.settings = settings
        self._days = days
        self._result = release_feed.FeedResult()
        self._choice = "all"
        self._busy = False

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for side in ("top", "bottom", "start", "end"):
            getattr(outer, f"set_margin_{side}")(12)

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._filter = Gtk.ComboBoxText()
        for key, label in release_feed.FILTERS:
            self._filter.append(key, label)
        self._filter.set_active_id("all")
        self._filter.connect("changed", self._on_filter)
        bar.pack_start(self._filter, False, False, 0)
        self._refresh = Gtk.Button(label="Refresh")
        self._refresh.connect("clicked", lambda *_: self.reload(force=True))
        bar.pack_end(self._refresh, False, False, 0)
        outer.pack_start(bar, False, False, 0)

        self._status = Gtk.Label(label="", xalign=0)
        self._status.set_line_wrap(True)
        self._status.get_style_context().add_class("dim-label")
        outer.pack_start(self._status, False, False, 0)

        self._list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.add(self._list)
        outer.pack_start(scroller, True, True, 0)

        close = Gtk.Button(label="Close")
        close.connect("clicked", lambda *_: self.close())
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        row.pack_end(close, False, False, 0)
        outer.pack_start(row, False, False, 0)

        self.add(outer)
        outer.show_all()
        self.reload()

    # -- data -----------------------------------------------------------
    def reload(self, force: bool = False) -> None:
        if self._busy:
            return
        self._busy = True
        self._refresh.set_sensitive(False)
        self._status.set_text("Loading release dates…")

        def work() -> None:
            try:
                result = release_feed.load_feed(
                    release_feed.default_cache_path(),
                    days=self._days,
                    force=force,
                    tmdb_get=release_feed.tmdb_getter(self.settings),
                )
            except Exception as exc:  # noqa: BLE001
                result = release_feed.FeedResult(errors=[f"Could not load the calendar: {exc}"])
            GLib.idle_add(self._loaded, result)

        threading.Thread(target=work, daemon=True).start()

    def _loaded(self, result: release_feed.FeedResult) -> bool:
        self._busy = False
        self._refresh.set_sensitive(True)
        self._result = result
        self._render()
        return False

    # -- drawing --------------------------------------------------------
    def _on_filter(self, combo: Gtk.ComboBoxText) -> None:
        self._choice = combo.get_active_id() or "all"
        self._render()

    def _render(self) -> None:
        for child in self._list.get_children():
            self._list.remove(child)
        followed = cal.follows(self.settings)
        entries = release_feed.select(self._result.entries, self._choice, followed)
        for heading, group in cal.grouped(entries, TZ):
            title = Gtk.Label(xalign=0)
            title.set_markup(f"<b>{GLib.markup_escape_text(heading)}</b>")
            title.set_margin_top(10)
            self._list.pack_start(title, False, False, 0)
            for entry in group:
                self._list.pack_start(self._row(entry, entry.key in followed), False, False, 0)
        self._list.show_all()

        notes = list(self._result.errors)
        if self._result.from_cache:
            notes.insert(0, "Showing saved dates (could not reach the servers).")
        if not entries:
            notes.append("Nothing in this view." if self._result.entries else "No release dates yet.")
        self._status.set_text(" ".join(notes))

    def _row(self, entry: cal.Entry, following: bool) -> Gtk.Widget:
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        name = entry.title + (f" — {entry.episode}" if entry.episode else "")
        label = Gtk.Label(label=name, xalign=0)
        label.set_line_wrap(True)
        label.set_max_width_chars(48)
        line.pack_start(label, True, True, 0)
        kind = {"anime": "Anime", "tv": "TV", "movie": "Movie"}[release_feed.kind_of(entry)]
        when = Gtk.Label(label=f"{kind} · {cal.format_when(entry, TZ)}", xalign=1)
        when.get_style_context().add_class("dim-label")
        line.pack_start(when, False, False, 0)
        button = Gtk.ToggleButton(label="Following" if following else "Follow")
        button.set_active(following)
        button.connect("toggled", self._on_follow, entry.key)
        line.pack_end(button, False, False, 0)
        return line

    def _on_follow(self, button: Gtk.ToggleButton, key: str) -> None:
        if button.get_active():
            if not cal.follow(self.settings, key):
                button.set_active(False)
                self._status.set_text(f"You can follow up to {cal.MAX_FOLLOWS} titles.")
                return
            button.set_label("Following")
        else:
            cal.unfollow(self.settings, key)
            button.set_label("Follow")


def show_calendar(parent: Any, settings: Any) -> CalendarWindow:
    window = CalendarWindow(settings, transient_for=parent if isinstance(parent, Gtk.Window) else None)
    window.show_all()
    window.present()
    return window


__all__ = ["CalendarWindow", "show_calendar"]
