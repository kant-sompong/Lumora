"""GTK 3 feed widgets: poster rows, a three-column list and a schedule strip.

These mirror the Qt widgets in :mod:`lgl.qt_feed` call for call, and share the
decisions that are not drawing with :mod:`lgl.feed_model`:

* :class:`PosterRow` — a titled strip of posters with left and right arrows,
  ``set_cards``/``set_loading``/``set_error`` and an optional loader that runs
  on a worker thread and comes back through ``GLib.idle_add`` (tests pass
  ``threaded=False`` to stay synchronous).
* :class:`ThreeColumnList` — New Release / New Added / Just Completed, five
  rows each, with "Show all" / "Show fewer" on a column that has more.
* :class:`ScheduleStrip` — a seven-day strip whose chips pick the day, and the
  airings for that day in local 24-hour time.

Nothing here loads a cover itself: a row takes a ``cover_loader(card)``
callable, so the page decides where images come from and tests need no files.

GTK 3 only: ``pack_start``/``pack_end``/``.add``, never GTK 4 widgets or CSS
helpers.  The new ``feed-*`` style classes have no rules in ``lgl/style.css``
yet; slice 2 adds them, and the widgets still lay out sensibly without them.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, Callable, Iterable, Sequence

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, GdkPixbuf, Gtk, Pango  # noqa: E402

from . import feed_model  # noqa: E402

#: Poster width and height; the height keeps the 2:3 poster ratio.
POSTER_SIZE = (150, 225)
SPACING = 14

Loader = Callable[[], object]
CoverLoader = Callable[[Any], Any]
OpenHandler = Callable[[Any], None]


def _label(text: str = "", *classes: str, xalign: float = 0.0) -> Gtk.Label:
    widget = Gtk.Label(label=text, xalign=xalign)
    for name in classes:
        widget.get_style_context().add_class(name)
    return widget


def _show_all(widget: Gtk.Widget) -> None:
    """``show_all`` on a widget marked no-show-all does nothing at all.

    The feed rows keep that mark so an empty section stays hidden when a page
    calls ``show_all()`` on itself; their own tree is shown by lifting the mark
    for the call and putting it straight back.
    """
    flagged = widget.get_no_show_all()
    if flagged:
        widget.set_no_show_all(False)
    widget.show_all()
    if flagged:
        widget.set_no_show_all(True)


def _clear(box: Gtk.Box) -> None:
    for child in box.get_children():
        box.remove(child)


def _fit_pixbuf(pixbuf: Any, width: int, height: int) -> Any:
    """Scale a cover to fill *width* x *height*, cropping the overflow."""
    if pixbuf is None:
        return None
    source_w, source_h = pixbuf.get_width(), pixbuf.get_height()
    if source_w <= 0 or source_h <= 0:
        return None
    scale = max(width / source_w, height / source_h)
    crop_w = min(source_w, int(round(width / scale)))
    crop_h = min(source_h, int(round(height / scale)))
    x = max(0, (source_w - crop_w) // 2)
    y = max(0, (source_h - crop_h) // 2)
    cropped = pixbuf.new_subpixbuf(x, y, crop_w, crop_h)
    return cropped.scale_simple(width, height, GdkPixbuf.InterpType.BILINEAR)


class PosterCard(Gtk.Button):
    """One poster: cover, title, and one dim meta line."""

    def __init__(self, card: Any, *, cover_loader: CoverLoader | None = None,
                 width: int = POSTER_SIZE[0], height: int = POSTER_SIZE[1]) -> None:
        super().__init__()
        self.card = card
        self.set_relief(Gtk.ReliefStyle.NONE)
        self.set_halign(Gtk.Align.START)
        self.set_valign(Gtk.Align.START)
        self.get_style_context().add_class("feed-card")
        title, meta = feed_model.poster_text(card)
        self.set_tooltip_text(title)
        try:
            self.get_accessible().set_name(title)
        except (AttributeError, TypeError):  # pragma: no cover - old GTK
            pass

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.poster = Gtk.Image()
        self.poster.set_size_request(width, height)
        self.poster.get_style_context().add_class("poster-frame")
        self.poster.get_style_context().add_class("feed-poster")
        box.pack_start(self.poster, False, False, 0)

        self.title = _label(title, "game-title", "feed-card-title")
        self.title.set_ellipsize(Pango.EllipsizeMode.END)
        self.title.set_max_width_chars(22)
        self.title.set_width_chars(1)
        box.pack_start(self.title, False, False, 0)

        self.meta = _label(meta, "dim", "small", "feed-card-meta")
        self.meta.set_ellipsize(Pango.EllipsizeMode.END)
        self.meta.set_max_width_chars(24)
        self.meta.set_width_chars(1)
        box.pack_start(self.meta, False, False, 0)

        self.add(box)
        if cover_loader is not None:
            self.set_cover(cover_loader(card))

    def set_cover(self, pixbuf: Any) -> None:
        """Show *pixbuf*, cropped to the poster ratio; ``None`` keeps the frame."""
        if pixbuf is None:
            return
        width, height = self.poster.get_size_request()
        fitted = _fit_pixbuf(pixbuf, max(1, width), max(1, height))
        if fitted is not None:
            self.poster.set_from_pixbuf(fitted)


class PosterRow(Gtk.Box):
    """A titled poster strip with arrows, a count and an optional View All."""

    def __init__(
        self,
        title: str,
        note: str = "",
        *,
        cover_loader: CoverLoader | None = None,
        on_open: OpenHandler | None = None,
        on_view_all: Callable[[], None] | None = None,
        card_width: int = POSTER_SIZE[0],
        threaded: bool = True,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=9)
        self.get_style_context().add_class("feed-row")
        self.state = feed_model.PosterRowState(
            card_width=card_width, spacing=SPACING
        )
        self._cover_loader = cover_loader
        self._on_open = on_open
        self._loader: Loader | None = None
        self._threaded = threaded
        self._token = 0

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        self.heading = _label(title, "section-label", "feed-title", xalign=0.0)
        head.pack_start(self.heading, False, False, 0)
        self.count = _label("", "count-label", "feed-count")
        self.count.set_no_show_all(True)
        head.pack_start(self.count, False, False, 0)
        if note:
            self.note = _label(note, "dim", "small", "feed-note")
            head.pack_start(self.note, False, False, 0)
        else:
            self.note = None
        head.pack_start(Gtk.Box(), True, True, 0)
        self.view_all = Gtk.Button(label="View All")
        self.view_all.set_relief(Gtk.ReliefStyle.NONE)
        self.view_all.get_style_context().add_class("link-button")
        self.view_all.set_no_show_all(on_view_all is None)
        self.view_all.set_visible(on_view_all is not None)
        if on_view_all is not None:
            self.view_all.connect("clicked", lambda *_a: on_view_all())
        head.pack_end(self.view_all, False, False, 0)
        self.pack_start(head, False, False, 0)

        self.placeholder = _label("", "dim", "small", "feed-placeholder")
        self.placeholder.set_no_show_all(True)
        self.pack_start(self.placeholder, False, False, 0)

        self.error = _label("", "error-text", "feed-error", xalign=0.0)
        self.error.set_line_wrap(True)
        self.error.set_no_show_all(True)
        self.pack_start(self.error, False, False, 0)

        self.body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.body.get_style_context().add_class("feed-strip")
        self.left = self._arrow("go-previous-symbolic", "Scroll left", -1)
        self.right = self._arrow("go-next-symbolic", "Scroll right", 1)
        self.scroller = Gtk.ScrolledWindow()
        # EXTERNAL: the strip scrolls by its arrows, so no scrollbar is drawn
        # under it, but the adjustment still tracks the overflow.
        self.scroller.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.NEVER)
        self.scroller.set_kinetic_scrolling(True)
        self.scroller.set_overlay_scrolling(False)
        self.scroller.get_style_context().add_class("feed-scroll")
        self.scroller.set_size_request(-1, POSTER_SIZE[1] + 62)
        self.cards_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=SPACING)
        self.cards_box.set_margin_start(2)
        self.cards_box.set_margin_end(2)
        self.scroller.add(self.cards_box)
        self.body.pack_start(self.left, False, False, 0)
        self.body.pack_start(self.scroller, True, True, 0)
        self.body.pack_end(self.right, False, False, 0)
        self.pack_start(self.body, False, False, 0)
        # no_show_all on the row keeps an empty section hidden inside a page
        # that is show_all()ed; its own show_all() still runs.
        self.set_no_show_all(True)
        self.set_visible(False)

    # -- construction helpers ----------------------------------------------
    def _arrow(self, icon: str, tooltip: str, direction: int) -> Gtk.Button:
        button = Gtk.Button.new_from_icon_name(icon, Gtk.IconSize.BUTTON)
        button.set_valign(Gtk.Align.CENTER)
        button.set_tooltip_text(tooltip)
        try:
            button.get_accessible().set_name(tooltip)
        except (AttributeError, TypeError):  # pragma: no cover - old GTK
            pass
        button.get_style_context().add_class("chip")
        button.get_style_context().add_class("feed-arrow")
        button.connect("clicked", lambda *_a: self.scroll(direction))
        return button

    # -- data ---------------------------------------------------------------
    def set_loader(self, loader: Loader | None) -> None:
        self._loader = loader

    def loader(self) -> Loader | None:
        return self._loader

    def set_cover_loader(self, cover_loader: CoverLoader | None) -> None:
        self._cover_loader = cover_loader
        for widget in self.posters():
            if isinstance(widget, PosterCard) and cover_loader is not None:
                widget.set_cover(cover_loader(widget.card))

    def set_on_open(self, on_open: OpenHandler | None) -> None:
        self._on_open = on_open

    def set_cards(self, cards: Sequence[Any]) -> None:
        """Fill the strip now; an empty list hides the whole row."""
        self.state.set_cards(cards)
        _clear(self.cards_box)
        for card in self.state.cards:
            tile = PosterCard(card, cover_loader=self._cover_loader)
            tile.connect("clicked", lambda _b, c=card: self._open(c))
            self.cards_box.pack_start(tile, False, False, 0)
            tile.show_all()
        self.count.set_text(str(self.state.count) if self.state.count else "")
        _show_all(self)
        self.count.set_visible(bool(self.state.count))
        self.placeholder.set_visible(False)
        self.error.set_visible(False)
        self.body.set_visible(self.state.showing_cards)
        self.set_visible(self.state.visible)

    def set_loading(self, text: str = feed_model.LOADING_TEXT) -> None:
        self.state.set_loading(text)
        self.placeholder.set_text(self.state.placeholder)
        _show_all(self)
        self.placeholder.set_visible(True)
        self.error.set_visible(False)
        self.body.set_visible(False)
        self.set_visible(True)

    def set_error(self, reason: object) -> None:
        """One dim line instead of the row; an empty reason hides the row."""
        self.state.set_error(reason)
        if not self.state.error:
            self.error.set_visible(False)
            self.placeholder.set_visible(False)
            self.body.set_visible(False)
            self.set_visible(False)
            return
        self.error.set_text(self.state.error)
        _show_all(self)
        self.error.set_visible(True)
        self.placeholder.set_visible(False)
        self.body.set_visible(False)
        self.set_visible(True)

    def refresh(self) -> None:
        """Run the injected loader; a stale answer never replaces a newer one."""
        if self._loader is None:
            return
        self._token += 1
        token = self._token
        self.set_loading()
        loader = self._loader

        def work() -> None:
            try:
                result = loader()
            except Exception as exc:  # noqa: BLE001 - shown, never raised
                self._deliver(token, None, exc)
                return
            self._deliver(token, result, None)

        if self._threaded:
            threading.Thread(target=work, daemon=True).start()
        else:
            work()

    def _deliver(self, token: int, result: object, error: object) -> None:
        if self._threaded:
            GLib.idle_add(self._apply, token, result, error)
        else:
            self._apply(token, result, error)

    def _apply(self, token: int, result: object, error: object) -> bool:
        if token != self._token:
            return False
        if error is not None:
            self.set_error(error)
            return False
        cards = list(result or [])  # type: ignore[arg-type]
        if cards:
            self.set_cards(cards)
        else:
            self.state.set_cards([])
            self.body.set_visible(False)
            self.placeholder.set_visible(False)
            self.set_visible(False)
        return False

    # -- questions ----------------------------------------------------------
    def posters(self) -> list[Gtk.Widget]:
        return list(self.cards_box.get_children())

    def cards(self) -> list[Any]:
        return list(self.state.cards)

    def titles(self) -> list[str]:
        return self.state.titles()

    # -- scrolling ----------------------------------------------------------
    def scroll(self, direction: int) -> None:
        """Move the strip by about one visible page, like the Qt arrows do."""
        adjustment = self.scroller.get_hadjustment()
        if adjustment is None:
            return
        step = self._page_step()
        upper = adjustment.get_upper()
        page = adjustment.get_page_size()
        limit = max(0.0, upper - page)
        target = adjustment.get_value() + (step if direction > 0 else -step)
        adjustment.set_value(max(0.0, min(limit, target)))

    def _page_step(self) -> float:
        width = self.scroller.get_allocated_width()
        if width <= 1:
            width = 900
        fits = max(1, self.state.fit_count(width))
        return float(fits * (self.state.card_width + self.state.spacing))

    def _open(self, card: Any) -> None:
        if self._on_open is not None:
            self._on_open(card)


#: The cover a three-column row draws, and the size requested for it.
COLUMN_THUMB = (34, 50)
COLUMN_THUMB_REQUEST = (68, 100)


class ThreeColumnList(Gtk.Box):
    """New Release / New Added / Just Completed, five rows each."""

    def __init__(
        self,
        *,
        on_open: OpenHandler | None = None,
        image_loader: Callable[..., Any] | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        self.get_style_context().add_class("feed-columns")
        self.state = feed_model.ThreeColumnState()
        self._on_open = on_open
        self._image_loader = image_loader
        self._covers: dict[str, Any] = {}
        self._thumbs: dict[str, list[tuple[Any, str]]] = {}
        self.columns: dict[str, dict[str, Any]] = {}

        for key, title in feed_model.COLUMN_HEADINGS:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            box.get_style_context().add_class("feed-column")
            box.pack_start(_label(title, "section-label", "feed-column-title"), False, False, 0)
            error = _label("", "error-text", "feed-column-error", xalign=0.0)
            error.set_line_wrap(True)
            error.set_no_show_all(True)
            box.pack_start(error, False, False, 0)
            rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            box.pack_start(rows, False, False, 0)
            toggle = Gtk.Button(label=feed_model.SHOW_ALL)
            toggle.set_relief(Gtk.ReliefStyle.NONE)
            toggle.set_halign(Gtk.Align.START)
            toggle.get_style_context().add_class("link-button")
            toggle.get_style_context().add_class("feed-column-more")
            toggle.set_no_show_all(True)
            toggle.connect("clicked", lambda _b, k=key: self.toggle(k))
            box.pack_start(toggle, False, False, 0)
            self.pack_start(box, True, True, 0)
            self.columns[key] = {
                "box": box, "error": error, "rows": rows, "toggle": toggle,
            }
        self.set_no_show_all(True)
        self.set_visible(False)

    # -- data ---------------------------------------------------------------
    def set_items(self, key: str, items: Iterable[Any]) -> None:
        self.state.set_items(key, items)
        self._render(key)

    def set_error(self, key: str, reason: object) -> None:
        """Hide this column's rows and show one dim ``Could not load:`` line."""
        self.state.set_error(key, reason)
        self._render(key)

    def set_columns(
        self,
        *,
        new_release: Iterable[Any] | None = None,
        new_added: Iterable[Any] | None = None,
        just_completed: Iterable[Any] | None = None,
    ) -> None:
        self.state.set_columns(
            new_release=new_release,
            new_added=new_added,
            just_completed=just_completed,
        )
        for key in feed_model.COLUMN_KEYS:
            self._render(key)

    def _render(self, key: str) -> None:
        parts = self.columns.get(key)
        if parts is None:
            return
        box = parts["box"]
        rows = parts["rows"]
        _clear(rows)
        self._thumbs[key] = []
        for item in self.state.rows(key):
            rows.pack_start(self._feed_row(item, key), False, False, 0)
        has_rows = self.state.row_count(key) > 0
        error = self.state.error_text(key)
        visible = self.state.visible(key)
        parts["error"].set_text(error)
        parts["toggle"].set_label(self.state.toggle_label(key))
        # Show this column's own tree, never the whole list: show_all() on the
        # list would reveal empty sibling columns and whatever they last hid.
        if visible:
            _show_all(box)
        # then the real visibility, or a column that only has an error line
        # would come back with its empty row box showing.
        parts["error"].set_visible(bool(error))
        rows.set_visible(has_rows)
        parts["toggle"].set_visible(self.state.can_expand(key))
        box.set_visible(visible)
        self.set_visible(self.state.any_visible())

    def _feed_row(self, item: Any, key: str) -> Gtk.Button:
        button = Gtk.Button()
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.set_halign(Gtk.Align.FILL)
        button.get_style_context().add_class("feed-column-row")
        title, meta = feed_model.poster_text(item)
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        thumb = Gtk.Image()
        thumb.set_size_request(34, 50)
        thumb.get_style_context().add_class("poster-frame")
        line.pack_start(thumb, False, False, 0)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        name = _label(title, "game-title", "feed-column-row-title")
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(20)
        name.set_width_chars(1)
        text.pack_start(name, False, False, 0)
        meta_label = _label(meta, "dim", "small", "feed-column-row-meta")
        meta_label.set_ellipsize(Pango.EllipsizeMode.END)
        meta_label.set_max_width_chars(22)
        meta_label.set_width_chars(1)
        text.pack_start(meta_label, False, False, 0)
        line.pack_start(text, True, True, 0)
        button.add(line)
        button.set_tooltip_text(title)
        button.connect("clicked", lambda _b, entry=item: self._open(entry))
        self._thumb_for(thumb, item, key)
        return button

    @staticmethod
    def _poster_url(item: Any) -> str:
        if isinstance(item, dict):
            return str(item.get("poster_url") or "")
        return str(getattr(item, "poster_url", "") or "")

    def _thumb_for(self, thumb: Gtk.Image, item: Any, key: str) -> None:
        """A row shows its cover when it has one, through the page's loader.

        The cover may arrive as a pixbuf (``cover``) or as a URL
        (``poster_url``); a URL is fetched once and repaints every row that
        shares it.
        """
        pixbuf = None
        if isinstance(item, dict):
            pixbuf = item.get("cover")
        else:
            pixbuf = getattr(item, "cover", None)
        if pixbuf is not None:
            fitted = _fit_pixbuf(pixbuf, *COLUMN_THUMB)
            if fitted is not None:
                thumb.set_from_pixbuf(fitted)
            return
        url = self._poster_url(item)
        if not url:
            return
        self._thumbs.setdefault(key, []).append((thumb, url))
        if url in self._covers:
            self._paint_thumb(thumb, url)
            return
        if self._image_loader is None:
            return

        def done(result: Any, url: str = url) -> bool:
            if result is not None:
                self._covers[url] = result
                GLib.idle_add(self._paint_covers)
            return False

        self._image_loader(url, COLUMN_THUMB_REQUEST, done)

    def _paint_thumb(self, thumb: Gtk.Image, url: str) -> None:
        fitted = _fit_pixbuf(self._covers[url], *COLUMN_THUMB)
        if fitted is not None:
            thumb.set_from_pixbuf(fitted)

    def _paint_covers(self) -> bool:
        for thumbs in list(self._thumbs.values()):
            for thumb, url in list(thumbs):
                if url in self._covers:
                    self._paint_thumb(thumb, url)
        return False

    def toggle(self, key: str) -> bool:
        expanded = self.state.toggle(key)
        self._render(key)
        return expanded

    # -- questions ----------------------------------------------------------
    def rows(self, key: str) -> int:
        return self.state.row_count(key)

    def error_text(self, key: str) -> str:
        return self.state.error_text(key)

    def _open(self, item: Any) -> None:
        if self._on_open is not None:
            self._on_open(item)


def _entry_kind(entry: Any) -> str:
    """``Anime``, ``TV`` or ``Movie`` for a schedule row, or ``""``."""
    from . import release_feed

    key = entry.get("key") if isinstance(entry, dict) else getattr(entry, "key", "")
    labels = {"anime": "Anime", "tv": "TV", "movie": "Movie"}
    kind = release_feed.kind_of_key(key)
    if kind in labels:
        return labels[kind]
    raw = entry.get("kind") if isinstance(entry, dict) else getattr(entry, "kind", "")
    return labels.get(str(raw or "").lower(), "")


class ScheduleStrip(Gtk.Box):
    """A seven-day strip and the airing list for the selected day."""

    def __init__(self, now: datetime | None = None, *,
                 on_open: OpenHandler | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.get_style_context().add_class("feed-schedule")
        self.state = feed_model.ScheduleState(now=now or datetime.now())
        self._on_open = on_open
        self._syncing = False

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        head.pack_start(
            _label("Estimated Schedule", "section-label", "feed-title"), False, False, 0
        )
        self.now_label = _label("", "dim", "small", "feed-now")
        head.pack_start(self.now_label, False, False, 0)
        head.pack_start(Gtk.Box(), True, True, 0)
        self.prev_btn = self._chip_button("go-previous-symbolic", "Previous week")
        self.next_btn = self._chip_button("go-next-symbolic", "Next week")
        self.prev_btn.connect("clicked", lambda *_a: self.shift_week(-7))
        head.pack_end(self.next_btn, False, False, 0)
        head.pack_end(self.prev_btn, False, False, 0)
        self.next_btn.connect("clicked", lambda *_a: self.shift_week(7))
        self.pack_start(head, False, False, 0)

        self.days = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.days.set_homogeneous(True)
        self.days.get_style_context().add_class("feed-days")
        self.pack_start(self.days, False, False, 0)
        self.day_buttons: list[Gtk.ToggleButton] = []

        self.rows_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.pack_start(self.rows_box, False, False, 0)
        self.empty = _label("", "dim", "small", "feed-empty")
        self.empty.set_no_show_all(True)
        self.pack_start(self.empty, False, False, 0)

        self._render_days()
        self._render_rows()

    def _chip_button(self, icon: str, tooltip: str) -> Gtk.Button:
        button = Gtk.Button.new_from_icon_name(icon, Gtk.IconSize.BUTTON)
        button.set_tooltip_text(tooltip)
        button.set_valign(Gtk.Align.CENTER)
        button.get_style_context().add_class("chip")
        button.get_style_context().add_class("feed-arrow")
        try:
            button.get_accessible().set_name(tooltip)
        except (AttributeError, TypeError):  # pragma: no cover - old GTK
            pass
        return button

    # -- clock --------------------------------------------------------------
    def set_now(self, when: datetime) -> None:
        """Freeze the clock (tests and screenshots use a fixed day)."""
        self.state.set_now(when)
        self._refresh()

    def now_text(self) -> str:
        return self.state.now_text()

    def shift_week(self, days: int) -> None:
        self.state.shift_week(days)
        self._refresh()

    def select_day(self, index: int) -> None:
        self.state.select_day(index)
        self._refresh()

    def selected_day(self) -> datetime:
        return self.state.selected_day()

    def week_start(self) -> datetime:
        return self.state.week_start

    # -- data ---------------------------------------------------------------
    def set_entries(self, entries: Iterable[Any]) -> None:
        """Air dates drive the list; each entry needs ``air_at``, title, episode."""
        self.state.set_entries(entries)
        self._render_rows()

    def entries_for_day(self, moment: datetime | None = None) -> list[Any]:
        return self.state.entries_for_day(moment)

    def rows(self) -> int:
        return len(self.state.rows())

    def _refresh(self) -> None:
        self.now_label.set_text(self.state.now_text())
        self._render_days()
        self._render_rows()

    # -- drawing ------------------------------------------------------------
    def _render_days(self) -> None:
        _clear(self.days)
        self.day_buttons = []
        dates = self.state.day_dates()
        for index, day in enumerate(dates):
            button = Gtk.ToggleButton(label=day.strftime("%a %d"))
            button.get_style_context().add_class("chip")
            button.get_style_context().add_class("feed-day")
            button.set_tooltip_text(day.strftime("%A %d %B"))
            button.set_active(index == self.state.selected)
            if index == self.state.selected:
                button.get_style_context().add_class("chip-on")
            if self.state.is_today(index):
                button.get_style_context().add_class("feed-today")
            button.connect("toggled", self._day_toggled, index)
            self.days.pack_start(button, True, True, 0)
            self.day_buttons.append(button)
        self.days.show_all()

    def _day_toggled(self, button: Gtk.ToggleButton, index: int) -> None:
        if self._syncing:
            return
        if not button.get_active():
            # A day chip stays down: the strip always has one selected day.
            self._syncing = True
            button.set_active(index == self.state.selected)
            self._syncing = False
            return
        self.select_day(index)

    def _render_rows(self) -> None:
        _clear(self.rows_box)
        entries = self.state.rows()
        for entry in entries:
            self.rows_box.pack_start(self._entry_row(entry), False, False, 0)
        self.rows_box.set_visible(bool(entries))
        self.empty.set_text(self.state.empty_text())
        self.empty.set_visible(not entries)
        if entries:
            self.rows_box.show_all()
        # show_all() on the box keeps the selected chip painted as chosen.
        for index, button in enumerate(self.day_buttons):
            self._syncing = True
            button.set_active(index == self.state.selected)
            if index == self.state.selected:
                button.get_style_context().add_class("chip-on")
            else:
                button.get_style_context().remove_class("chip-on")
            self._syncing = False
        self.now_label.set_text(self.state.now_text())

    def _entry_row(self, entry: Any) -> Gtk.Button:
        button = Gtk.Button()
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.set_halign(Gtk.Align.FILL)
        button.get_style_context().add_class("feed-schedule-row")
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        stamp = None
        raw = feed_model.air_at(entry)
        if raw is not None:
            stamp = datetime.fromtimestamp(raw)
        clock = _label(
            feed_model.minutes_text(stamp) if stamp else "—",
            "mono", "small", "feed-schedule-time",
        )
        clock.set_size_request(52, -1)
        line.pack_start(clock, False, False, 0)
        title, meta = feed_model.poster_text(entry)
        name = _label(title, "game-title", "feed-schedule-title")
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_width_chars(1)
        line.pack_start(name, True, True, 0)
        episode = entry.get("episode") if isinstance(entry, dict) else getattr(
            entry, "episode", None
        )
        kind = _entry_kind(entry)
        if episode not in ("", None):
            text = f"Episode {episode}"
            if kind:
                text = f"{kind} · {text}"
            line.pack_end(
                _label(text, "dim", "small", "feed-schedule-episode"),
                False, False, 0,
            )
        else:
            text = meta
            if kind:
                text = f"{kind} · {meta}" if meta else kind
            line.pack_end(
                _label(text, "dim", "small", "feed-schedule-meta"), False, False, 0
            )
        button.add(line)
        button.connect("clicked", lambda _b, item=entry: self._open(item))
        return button

    def _open(self, entry: Any) -> None:
        if self._on_open is not None:
            self._on_open(entry)


__all__ = [
    "COLUMN_THUMB",
    "CoverLoader",
    "POSTER_SIZE",
    "PosterCard",
    "PosterRow",
    "ScheduleStrip",
    "ThreeColumnList",
]
