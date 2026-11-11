"""The Media home feed: seven poster rows, the three lists and the day strip.

:mod:`lgl.gtk_feed` draws the pieces and :mod:`lgl.feed_model` holds their
state; this module connects them to loaders and to the window's callbacks, so
the page itself needs no network code.  Every poster row loads on its own
worker, and the columns and the schedule each load on one of theirs, so one
slow section never holds up the rest of the page.

Cards are plain dicts ``{"title", "meta", "item"}``: the widgets read the two
text fields through :mod:`lgl.feed_model`, and ``item`` is whatever the caller
wants back when the card is clicked.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, Callable, Sequence

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from . import gtk_feed  # noqa: E402

Loader = Callable[[], object]
ImageLoader = Callable[[str, tuple[int, int], Callable[[Any], Any]], None]

#: (key, heading, note, whether the row has a View All button).  Page order.
SECTIONS = (
    ("continue", "Continue Watching", "Recently watched", False),
    ("trending", "Trending Now", "This week on TMDB", True),
    ("movies", "Movies", "Popular on TMDB", True),
    ("tv", "TV Shows", "Popular on TMDB", True),
    ("for_you", "For you", "From what you watch and save", False),
    (
        "hidden_gems",
        "Hidden gems",
        "Anime · high scores, few viewers (Anime gems)",
        True,
    ),
    ("more_like", "More like this", "Because of what you watched last", False),
)


def card_for(item: Any, meta: str = "") -> dict[str, Any]:
    """A feed card: the poster text the widgets read, plus the item behind it."""
    title = getattr(item, "title", "")
    if isinstance(item, dict):
        title = item.get("title") or title
    return {"title": str(title or ""), "meta": str(meta or ""), "item": item}


def poster_url(card: Any) -> str:
    """The cover URL behind a feed card, empty when it has none."""
    item = card.get("item") if isinstance(card, dict) else card
    return str(getattr(item, "poster_url", "") or "")


class FeedHome(Gtk.Box):
    """The media home feed, ready to pack under the hero."""

    def __init__(
        self,
        *,
        on_open: Callable[[Any], None] | None = None,
        on_view_all: Callable[[str], None] | None = None,
        on_schedule_open: Callable[[Any], None] | None = None,
        image_loader: ImageLoader | None = None,
        threaded: bool = True,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=30)
        self.get_style_context().add_class("feed-home")
        self._on_open = on_open
        self._on_view_all = on_view_all
        self._on_schedule_open = on_schedule_open
        self._image_loader = image_loader
        self._threaded = threaded
        self._covers: dict[tuple[str, tuple[int, int]], Any] = {}
        self._column_token = 0
        self._schedule_token = 0

        self.rows: dict[str, gtk_feed.PosterRow] = {}
        self.loaders: dict[str, Loader] = {}
        self.column_loaders: dict[str, Loader] = {}
        self.schedule_loader: Loader | None = None

        for key, title, note, show_all in SECTIONS:
            row = gtk_feed.PosterRow(
                title,
                note,
                cover_loader=self._cover_for,
                on_open=self._opened,
                on_view_all=(
                    (lambda k=key: self._view_all(k))
                    if show_all and on_view_all is not None
                    else None
                ),
                threaded=threaded,
            )
            self.pack_start(row, False, False, 0)
            self.rows[key] = row

        self.columns = gtk_feed.ThreeColumnList(on_open=self._opened)
        self.pack_start(self.columns, False, False, 0)

        self.schedule = gtk_feed.ScheduleStrip(on_open=on_schedule_open)
        # A clock pinned by set_now() (tests, screenshots) survives a refresh;
        # without one each load re-reads the wall clock so the strip stays current.
        self._now: datetime | None = None
        # The strip's own empty line ("Nothing airs on …") is true only after a
        # load has answered; until then it must not appear, not even through the
        # page's show_all().  It is shown by _apply_schedule once data arrives.
        self.schedule.set_no_show_all(True)
        self.schedule.set_visible(False)
        self.schedule.empty.set_visible(False)
        self.pack_start(self.schedule, False, False, 0)

        self.schedule_error = Gtk.Label(label="", xalign=0)
        self.schedule_error.set_line_wrap(True)
        self.schedule_error.get_style_context().add_class("error-text")
        self.schedule_error.get_style_context().add_class("feed-error")
        self.schedule_error.set_no_show_all(True)
        self.pack_start(self.schedule_error, False, False, 0)

    # -- data ---------------------------------------------------------------
    def row(self, key: str) -> gtk_feed.PosterRow | None:
        return self.rows.get(key)

    def apply(self, key: str, cards: Sequence[Any]) -> None:
        """Fill one row directly, for sections the window already fetched."""
        row = self.rows.get(key)
        if row is not None:
            row.set_cards(list(cards))

    def set_loaders(
        self,
        loaders: dict[str, Loader] | None = None,
        **extra: Loader,
    ) -> None:
        """Give rows their own loaders; each one runs on the row's worker."""
        merged = dict(loaders or {})
        merged.update(extra)
        for key, loader in merged.items():
            row = self.rows.get(key)
            if row is not None and callable(loader):
                self.loaders[key] = loader
                row.set_loader(loader)

    def set_column_loaders(
        self,
        loaders: dict[str, Loader] | None = None,
        **extra: Loader,
    ) -> None:
        merged = dict(loaders or {})
        merged.update(extra)
        for key, loader in merged.items():
            if callable(loader):
                self.column_loaders[key] = loader

    def set_schedule_loader(self, loader: Loader | None) -> None:
        self.schedule_loader = loader if callable(loader) else None

    # -- loading ------------------------------------------------------------
    def refresh(self) -> None:
        """Start every section that has a loader; empty sections hide."""
        for row in self.rows.values():
            if row.loader() is not None:
                row.refresh()
        self.refresh_columns()
        self.refresh_schedule()

    def refresh_columns(self) -> None:
        loaders = dict(self.column_loaders)
        if not loaders:
            return
        self._column_token += 1
        token = self._column_token
        if not self._threaded:
            self._apply_columns(token, self._gather(loaders))
            return

        def work() -> None:
            GLib.idle_add(self._apply_columns, token, self._gather(loaders))

        threading.Thread(target=work, daemon=True, name="lgl-feed-columns").start()

    def refresh_schedule(self) -> None:
        loader = self.schedule_loader
        if loader is None:
            return
        self._schedule_token += 1
        token = self._schedule_token
        self.schedule.set_visible(False)
        self.schedule_error.set_visible(False)
        if not self._threaded:
            self._apply_schedule(token, self._gather({"schedule": loader})["schedule"])
            return

        def work() -> None:
            result = self._gather({"schedule": loader})["schedule"]
            GLib.idle_add(self._apply_schedule, token, result)

        threading.Thread(target=work, daemon=True, name="lgl-feed-schedule").start()

    @staticmethod
    def _gather(loaders: dict[str, Loader]) -> dict[str, object]:
        found: dict[str, object] = {}
        for key, loader in loaders.items():
            try:
                found[key] = loader()
            except Exception as exc:  # noqa: BLE001 - becomes that column's line
                found[key] = exc
        return found

    def _apply_columns(self, token: int, found: dict[str, object]) -> bool:
        if token != self._column_token:
            return False
        for key, value in found.items():
            if isinstance(value, BaseException):
                self.columns.set_error(key, value)
            else:
                self.columns.set_items(key, list(value or ()))  # type: ignore[arg-type]
        return False

    def _apply_schedule(self, token: int, result: object) -> bool:
        if token != self._schedule_token:
            return False
        if isinstance(result, BaseException):
            self.schedule.set_visible(False)
            self.schedule_error.set_text(f"Could not load: {result}")
            self.schedule_error.set_visible(True)
            return False
        entries = list(result or ())  # type: ignore[arg-type]
        self.schedule_error.set_visible(False)
        # The load answered, so the strip may appear now: with rows when there
        # are airings, and with its own "Nothing airs on …" line when the week
        # really is empty.  The old code hid the strip for an empty answer, so
        # an empty result and a missing one looked the same.
        self.schedule.set_now(self._now or datetime.now())
        self.schedule.set_entries(entries)
        self.schedule.set_no_show_all(False)
        self.schedule.show_all()
        self.schedule.set_visible(True)
        return False

    def set_now(self, when: datetime) -> None:
        self._now = when
        self.schedule.set_now(when)

    # -- callbacks ----------------------------------------------------------
    def _opened(self, card: Any) -> None:
        if self._on_open is None:
            return
        if isinstance(card, dict):
            item = card.get("item")
        else:
            item = getattr(card, "item", card)
        if item is None:
            item = card
        self._on_open(item)

    def _view_all(self, key: str) -> None:
        if self._on_view_all is not None:
            self._on_view_all(key)

    # -- covers -------------------------------------------------------------
    def _cover_for(self, card: Any) -> Any:
        """Hand a poster its cover from cache, or ask for it and repaint later."""
        url = poster_url(card)
        if not url or self._image_loader is None:
            return None
        size = gtk_feed.POSTER_SIZE
        cached = self._covers.get((url, size))
        if cached is not None:
            return cached

        def done(pixbuf: Any, url: str = url, size: tuple[int, int] = size) -> bool:
            if pixbuf is not None:
                self._covers[(url, size)] = pixbuf
                # Paint once the row's cards exist: a loader that answers at
                # once is still called while the first card is being built.
                GLib.idle_add(self._paint_covers)
            return False

        self._image_loader(url, size, done)
        return None

    def _paint_covers(self) -> bool:
        for row in self.rows.values():
            for poster in row.posters():
                url = poster_url(getattr(poster, "card", None))
                if not url:
                    continue
                cached = self._covers.get((url, gtk_feed.POSTER_SIZE))
                if cached is not None:
                    poster.set_cover(cached)
        return False


__all__ = ["FeedHome", "SECTIONS", "card_for", "poster_url"]
