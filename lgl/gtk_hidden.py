"""The GTK 3 page for anime hidden gems: two thresholds, a list and Refresh.

The ranking is :mod:`lgl.hidden_gems`; this page only draws it.  It loads when
the tab is first opened (or when Refresh is pressed), on a worker thread that
comes back through ``GLib.idle_add``, like the release calendar.  Tests pass
``threaded=False`` to stay synchronous.

Only AniList anime is covered — games and TMDB titles have no score/popularity
pair here — and the page says so.

GTK 3 only: ``pack_start``, ``Gtk.SpinButton`` and
``get_style_context().add_class()``.
"""

from __future__ import annotations

import threading
from typing import Any

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from . import hidden_gems  # noqa: E402

#: Where the two thresholds live in the app settings.
MIN_SCORE_KEY = "hidden_gems_min_score"
MAX_POPULARITY_KEY = "hidden_gems_max_popularity"

HINT = "Select a title to see its score and popularity."
BLURB = (
    "AniList anime only: games and TMDB titles have no score and popularity "
    "pair here, so they are not covered."
)


class HiddenGemsPage(Gtk.Box):
    """Score and popularity thresholds over AniList's anime, best rated first."""

    def __init__(
        self,
        settings: Any = None,
        *,
        post: hidden_gems.Post | None = None,
        cache_path: Any = None,
        threaded: bool = True,
        min_score: int = hidden_gems.MIN_SCORE,
        max_popularity: int = 0,
        excluded: Any = (),
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.settings = settings
        self._post = post
        self._cache_path = cache_path
        self._threaded = threaded
        self.excluded = [str(value) for value in excluded if str(value).strip()]
        self.result: hidden_gems.GemsResult | None = None
        self.loaded = False
        self._busy = False

        for side in ("top", "bottom", "start", "end"):
            getattr(self, f"set_margin_{side}")(8)

        heading = Gtk.Label(label="Hidden gems (anime)", xalign=0)
        heading.get_style_context().add_class("panel-title")
        self.pack_start(heading, False, False, 0)

        blurb = Gtk.Label(label=BLURB, xalign=0)
        blurb.set_line_wrap(True)
        blurb.set_max_width_chars(70)
        blurb.get_style_context().add_class("dim-label")
        self.pack_start(blurb, False, False, 0)

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        controls.pack_start(Gtk.Label(label="Score at least"), False, False, 0)
        self.score = Gtk.SpinButton.new_with_range(0, 100, 1)
        self.score.set_digits(0)
        self.score.set_width_chars(4)
        self.score.set_value(self._saved_score(min_score))
        controls.pack_start(self.score, False, False, 0)
        controls.pack_start(Gtk.Label(label="Popularity below"), False, False, 0)
        self.popularity = Gtk.SpinButton.new_with_range(0, hidden_gems.MAX_POPULARITY, 100)
        self.popularity.set_digits(0)
        self.popularity.set_width_chars(8)
        self.popularity.set_value(self._saved_popularity(max_popularity))
        self.popularity.set_tooltip_text(
            "0 uses the median popularity of the fetched titles."
        )
        controls.pack_start(self.popularity, False, False, 0)
        self.refresh_btn = Gtk.Button(label="Refresh")
        self.refresh_btn.connect("clicked", lambda *_: self.refresh())
        controls.pack_start(self.refresh_btn, False, False, 0)
        self.pack_start(controls, False, False, 0)

        self.cutoff_note = Gtk.Label(
            label="Popularity 0 means the median of the fetched titles.", xalign=0
        )
        self.cutoff_note.set_line_wrap(True)
        self.cutoff_note.get_style_context().add_class("dim-label")
        self.pack_start(self.cutoff_note, False, False, 0)

        self.error = Gtk.Label(label="", xalign=0)
        self.error.set_line_wrap(True)
        self.error.set_no_show_all(True)
        self.pack_start(self.error, False, False, 0)

        self.status = Gtk.Label(
            label="Open this tab or press Refresh to load anime from AniList.", xalign=0
        )
        self.status.set_line_wrap(True)
        self.status.get_style_context().add_class("dim-label")
        self.pack_start(self.status, False, False, 0)

        self.list = Gtk.ListBox()
        self.list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.list.connect("row-selected", self._on_selected)
        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroller.add(self.list)
        self.pack_start(self.scroller, True, True, 0)

        self.detail = Gtk.Label(label=HINT, xalign=0)
        self.detail.set_line_wrap(True)
        self.detail.get_style_context().add_class("dim-label")
        self.pack_start(self.detail, False, False, 0)

    # -- saved thresholds ------------------------------------------------
    def _saved_score(self, fallback: int) -> int:
        saved = self.settings.get(MIN_SCORE_KEY) if self.settings is not None else None
        return hidden_gems.clean_score(fallback if saved is None else saved)

    def _saved_popularity(self, fallback: int) -> int:
        saved = (
            self.settings.get(MAX_POPULARITY_KEY) if self.settings is not None else None
        )
        return hidden_gems.clean_popularity(fallback if saved is None else saved)

    def _remember(self, score: int, popularity: int) -> None:
        """Save both controls as they are; 0 stays 0 rather than the median."""
        if self.settings is None:
            return
        self.settings.update(**{MIN_SCORE_KEY: score, MAX_POPULARITY_KEY: popularity})

    # -- thresholds -----------------------------------------------------
    def thresholds(self) -> tuple[int, int]:
        """The two numbers in the controls, cleaned to what the module expects."""
        return (
            hidden_gems.clean_score(self.score.get_value()),
            hidden_gems.clean_popularity(self.popularity.get_value()),
        )

    # -- loading --------------------------------------------------------
    def ensure_loaded(self) -> None:
        """Load once, the first time the tab is opened."""
        if not self.loaded and not self._busy:
            self.refresh()

    def refresh(self) -> None:
        """Fetch or filter the catalogue for the current thresholds."""
        if self._busy:
            return
        self._busy = True
        self.refresh_btn.set_sensitive(False)
        self.status.set_text("Loading anime from AniList…")
        self.error.set_text("")
        self.error.hide()
        score, popularity = self.thresholds()
        self._remember(score, popularity)
        excluded = list(self.excluded)

        def load() -> None:
            result = hidden_gems.load_gems(
                post=self._post,
                cache_path=self._cache_path,
                min_score=score,
                max_popularity=popularity,
                excluded=excluded,
            )
            if self._threaded:
                GLib.idle_add(self.show_result, result)
            else:
                self.show_result(result)

        if self._threaded:
            threading.Thread(target=load, daemon=True).start()
        else:
            load()

    def show_result(self, result: hidden_gems.GemsResult) -> None:
        """Draw ``result`` without loading anything (tests and screenshots)."""
        self._busy = False
        self.result = result
        # Only a load that worked counts as loaded.
        self.loaded = not result.error_text
        self.refresh_btn.set_sensitive(True)
        self.status.set_text(result.note())
        self.error.set_text(result.error_text)
        failed = bool(result.error_text)
        # A failure leaves one readable line, not empty boxes.
        self.scroller.set_visible(not failed)
        self.detail.set_visible(not failed)
        if failed:
            self.error.show()
        else:
            self.error.hide()
        if result.cutoff:
            self.cutoff_note.set_text(
                f"Popularity below {result.cutoff:,} "
                f"({'from the control' if result.max_popularity else 'the median of the fetched titles'})."
            )

        for row in list(self.list.get_children()):
            self.list.remove(row)
        for entry in result.entries:
            row = Gtk.ListBoxRow()
            label = Gtk.Label(
                label=hidden_gems.entry_line(entry, result.scanned), xalign=0
            )
            label.set_line_wrap(True)
            label.set_margin_start(6)
            label.set_margin_end(6)
            label.set_margin_top(4)
            label.set_margin_bottom(4)
            row.add(label)
            row.gem_entry = entry  # type: ignore[attr-defined]
            row.set_tooltip_text(str(entry.get("title") or ""))
            self.list.add(row)
        self.list.show_all()
        self.detail.set_text(HINT)

    # -- behaviour ------------------------------------------------------
    def _on_selected(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        entry = getattr(row, "gem_entry", None) if row is not None else None
        scanned = self.result.scanned if self.result is not None else 0
        self.detail.set_text(hidden_gems.entry_details(entry, scanned) or HINT)


__all__ = ["BLURB", "HINT", "HiddenGemsPage"]
