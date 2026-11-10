"""Streaming-style feed widgets for the Qt Media page.

Three reusable pieces, so every home section is a single line of wiring:

* :class:`PosterRow` — a titled carousel of posters with round edge arrows and
  a *View All* button.
* :class:`ThreeColumnList` — three vertical lists of small cover rows
  (New Release / New Added / Just Completed).
* :class:`ScheduleStrip` — a seven-day strip with the airing list for the
  selected day.

Every widget takes a *loader* through :meth:`PosterRow.set_loader` or the
``set_*`` methods, never touching the network itself, so tests inject canned
data and a slow loader can never block the window.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable, Iterable, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .qt_icons import icon
from .qt_widgets import (
    CoverArt,
    ElideLabel,
    ScrollRow,
    SectionTitle,
    clear_layout,
    label,
    run_async,
)

Loader = Callable[[], object]


class _Arrow(QPushButton):
    """Round left/right carousel arrow, painted with the shared line icons."""

    def __init__(self, name: str, tip: str = "") -> None:
        super().__init__()
        self.setObjectName("feedArrow")
        self.setFixedSize(34, 34)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tip or ("Scroll left" if "left" in name else "Scroll right"))
        self.setAccessibleName(self.toolTip())
        from .qt_widgets import color

        self.setIcon(icon(name, 15, color("muted"), 1.9))


def _field(card: Any, name: str) -> str:
    """One text field from a poster card or from a plain row mapping."""
    if isinstance(card, dict):
        return str(card.get(name) or "")
    return str(getattr(card, name, "") or "")


def _simple_poster(card: Any) -> QWidget:
    """Fallback poster for callers without their own card widget."""
    frame = QFrame()
    frame.setObjectName("coverCard")
    frame.setFixedWidth(150)
    column = QVBoxLayout(frame)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(0)
    title = _field(card, "title") or "Untitled"
    cover = CoverArt(150, 225, radius=6, crop=True, title=title)
    column.addWidget(cover)
    name = ElideLabel(title)
    name.setObjectName("posterTitle")
    name.setContentsMargins(0, 8, 0, 0)
    column.addWidget(name)
    meta = ElideLabel(_field(card, "meta") or "—")
    meta.setObjectName("posterMeta")
    column.addWidget(meta)
    return frame


class PosterRow(QWidget):
    """A titled horizontal poster strip with arrows and a View All button.

    ``loader`` (optional) runs on a worker thread when :meth:`refresh` is
    called; the row shows a quiet placeholder, then either the posters or one
    dim error line, and hides itself when the result is empty.
    """

    item_clicked = Signal(object)
    view_all_clicked = Signal()

    def __init__(
        self,
        title: str,
        note: str = "",
        *,
        poster_factory: Callable[[Any], QWidget] | None = None,
        poster_width: int = 150,
        view_all: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._factory = poster_factory or _simple_poster
        self._width = poster_width
        self._loader: Loader | None = None
        self._token = 0
        self._cards: list[Any] = []
        # One card tall and no taller: a row that grows with the page leaves a
        # band of empty space under short strips and pushes what follows down.
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(8)

        self.heading = SectionTitle(title, "", note)
        self.view_all = QPushButton("View All")
        self.view_all.setObjectName("heroAction")
        self.view_all.setCursor(Qt.PointingHandCursor)
        self.view_all.clicked.connect(self.view_all_clicked.emit)
        self.view_all.setVisible(view_all)
        self.heading.add_tail(self.view_all)
        column.addWidget(self.heading)

        self.placeholder = label("Loading…", "mediaNote")
        self.placeholder.setVisible(False)
        column.addWidget(self.placeholder)

        self.error = label("", "mediaNote", wrap=True)
        self.error.setVisible(False)
        column.addWidget(self.error)

        self._host = QWidget()
        strip_row = QHBoxLayout(self._host)
        strip_row.setContentsMargins(0, 0, 0, 0)
        strip_row.setSpacing(6)
        self.left = _Arrow("chevron-left")
        self.left.clicked.connect(
            lambda: self.strip.scroll_by(-max(1, int(self.strip.width() * 0.8)))
        )
        strip_row.addWidget(self.left, 0, Qt.AlignVCenter)
        self.strip = ScrollRow(int(round(poster_width * 1.5)) + 46, spacing=16)
        strip_row.addWidget(self.strip, 1)
        self.right = _Arrow("chevron-right")
        self.right.clicked.connect(
            lambda: self.strip.scroll_by(max(1, int(self.strip.width() * 0.8)))
        )
        strip_row.addWidget(self.right, 0, Qt.AlignVCenter)
        column.addWidget(self._host)
        self._host.setVisible(False)
        self.setVisible(False)

    # data ------------------------------------------------------------------
    def set_loader(self, loader: Loader | None) -> None:
        self._loader = loader

    def loader(self) -> Loader | None:
        return self._loader

    def set_cards(self, cards: Sequence[Any]) -> None:
        """Fill the strip now (used with canned data and by :meth:`refresh`)."""
        self._cards = list(cards)
        self.strip.clear()
        for card in self._cards:
            widget = self._factory(card)
            widget.setCursor(Qt.PointingHandCursor)
            clicked = getattr(widget, "clicked", None)
            if clicked is not None and hasattr(clicked, "connect"):
                clicked.connect(lambda _=None, c=card: self.item_clicked.emit(c))
            else:
                widget.mouseReleaseEvent = _click_handler(widget, self, card)
            self.strip.add(widget)
        self._fit_to_cards()
        self.heading.count.setText(str(len(self._cards)))
        self._host.setVisible(bool(self._cards))
        self.error.setVisible(False)
        self.placeholder.setVisible(False)
        self.setVisible(bool(self._cards))

    def _fit_to_cards(self) -> None:
        """Make the strip exactly as tall as one poster card.

        The guessed ``poster_width * 1.5 + 52`` was taller than the card it
        held, so every row carried a band of empty space under it.
        """
        heights = [widget.sizeHint().height() for widget in self.posters()
                   if widget.sizeHint().height() > 0]
        if heights:
            self.strip.setFixedHeight(max(heights))

    def set_loading(self, text: str = "Loading…") -> None:
        self.placeholder.setText(text)
        self.placeholder.setVisible(True)
        self.error.setVisible(False)
        self._host.setVisible(False)
        self.setVisible(True)

    def set_error(self, reason: object) -> None:
        """One dim line instead of the row; an empty reason hides the row."""
        text = str(reason or "").strip()
        if not text:
            self.setVisible(False)
            return
        self.error.setText(f"Could not load: {text}")
        self.error.setVisible(True)
        self.placeholder.setVisible(False)
        self._host.setVisible(False)
        self.setVisible(True)

    def refresh(self) -> None:
        """Run the injected loader off the UI thread; stale answers are dropped."""
        if self._loader is None:
            return
        self._token += 1
        token = self._token
        self.set_loading()

        def work() -> object:
            return self._loader()  # type: ignore[misc]

        def done(result: object) -> None:
            if token != self._token:
                return
            cards = [card for card in (result or [])]  # type: ignore[union-attr]
            if cards:
                self.set_cards(cards)
            else:
                self.setVisible(False)
                self._host.setVisible(False)
                self.placeholder.setVisible(False)

        def failed(exc: object) -> None:
            if token != self._token:
                return
            self.set_error(exc)

        run_async(work, done, failed)

    def posters(self) -> list[QWidget]:
        layout = self.strip.widget().layout() if self.strip.widget() else None
        if layout is None:
            return []
        return [
            layout.itemAt(index).widget()
            for index in range(layout.count())
            if layout.itemAt(index).widget() is not None
        ]

    def cards(self) -> list[Any]:
        return list(self._cards)


def _click_handler(widget: QWidget, row: PosterRow, card: Any) -> Callable[[object], None]:
    """Give a plain factory widget the row's click behaviour."""

    def handler(event: object) -> None:
        if getattr(event, "button", lambda: None)() == Qt.LeftButton:
            row.item_clicked.emit(card)
        QWidget.mouseReleaseEvent(widget, event)  # type: ignore[arg-type]

    return handler


class FeedRow(QFrame):
    """One row of a three-column list: small cover, title, one dim meta line."""

    clicked = Signal(object)

    def __init__(self, item: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("feedRow")
        self.setCursor(Qt.PointingHandCursor)
        self.item = item
        if isinstance(item, dict):
            title = str(item.get("title") or "Untitled")
            meta = str(item.get("meta") or "")
        else:
            title = str(getattr(item, "title", "") or "Untitled")
            meta = str(getattr(item, "meta", "") or "")
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 4, 6, 4)
        row.setSpacing(10)
        thumb = CoverArt(40, 58, radius=4, crop=True, title=title)
        row.addWidget(thumb, 0, Qt.AlignVCenter)
        text = QVBoxLayout()
        text.setSpacing(2)
        name = ElideLabel(title)
        name.setObjectName("feedRowTitle")
        text.addWidget(name)
        if meta:
            line = ElideLabel(meta)
            line.setObjectName("feedRowMeta")
            text.addWidget(line)
        row.addLayout(text, 1)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self.item)
        super().mouseReleaseEvent(event)


class ThreeColumnList(QWidget):
    """New Release / New Added / Just Completed, five rows each."""

    item_clicked = Signal(object)
    KEYS = ("new_release", "new_added", "just_completed")
    HEADINGS = (
        ("new_release", "New Release"),
        ("new_added", "New Added"),
        ("just_completed", "Just Completed"),
    )
    LIMIT = 5

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(18)
        self.columns: dict[str, tuple[QFrame, QLabel, QVBoxLayout, QWidget]] = {}
        for key, title in self.HEADINGS:
            box = QFrame()
            box.setObjectName("feedColumn")
            box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            column = QVBoxLayout(box)
            column.setContentsMargins(12, 10, 12, 10)
            column.setSpacing(6)
            column.addWidget(label(title, "detailEyebrow"))
            error = label("", "mediaNote", wrap=True)
            error.setVisible(False)
            column.addWidget(error)
            host = QWidget()
            rows = QVBoxLayout(host)
            rows.setContentsMargins(0, 0, 0, 0)
            rows.setSpacing(2)
            column.addWidget(host)
            column.addStretch(1)
            row.addWidget(box, 1)
            self.columns[key] = (box, error, rows, host)
        self._shown: dict[str, bool] = {key: False for key in self.KEYS}
        self.setVisible(False)

    # data ------------------------------------------------------------------
    def set_items(self, key: str, items: Iterable[Any]) -> None:
        if key not in self.columns:
            return
        box, error, rows, host = self.columns[key]
        clear_layout(rows)
        shown = list(items)[: self.LIMIT]
        for item in shown:
            feed_row = FeedRow(item)
            feed_row.clicked.connect(self.item_clicked.emit)
            rows.addWidget(feed_row)
        error.setVisible(False)
        host.setVisible(bool(shown))
        box.setVisible(bool(shown))
        self._shown[key] = bool(shown)
        self.setVisible(any(self._shown.values()))

    def set_error(self, key: str, reason: object) -> None:
        """Hide this column's rows and show one dim ``Could not load:`` line."""
        if key not in self.columns:
            return
        box, error, rows, host = self.columns[key]
        text = str(reason or "").strip()
        clear_layout(rows)
        host.setVisible(False)
        if not text:
            box.setVisible(False)
            self._shown[key] = False
        else:
            error.setText(f"Could not load: {text}")
            error.setVisible(True)
            box.setVisible(True)
            self._shown[key] = True
        self.setVisible(any(self._shown.values()))

    def set_columns(
        self,
        *,
        new_release: Iterable[Any] | None = None,
        new_added: Iterable[Any] | None = None,
        just_completed: Iterable[Any] | None = None,
    ) -> None:
        for key, items in (
            ("new_release", new_release),
            ("new_added", new_added),
            ("just_completed", just_completed),
        ):
            self.set_items(key, items or [])

    def rows(self, key: str) -> int:
        if key not in self.columns:
            return 0
        _box, _error, rows, _host = self.columns[key]
        return rows.count()

    def error_text(self, key: str) -> str:
        if key not in self.columns:
            return ""
        error = self.columns[key][1]
        return error.text()


class ScheduleStrip(QWidget):
    """A seven-day strip and the airing list for the selected day."""

    entry_clicked = Signal(object)

    def __init__(self, parent: QWidget | None = None, *, now: datetime | None = None) -> None:
        super().__init__(parent)
        self._now = now or datetime.now()
        self._week_start = self._now.replace(hour=0, minute=0, second=0, microsecond=0)
        self._selected = 0
        self._entries: list[Any] = []

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(label("Estimated Schedule", "sectionTitle"))
        self.now_label = label(self.now_text(), "mediaNote")
        head.addWidget(self.now_label)
        head.addStretch(1)
        self.prev = _Arrow("chevron-left", "Previous week")
        self.prev.clicked.connect(lambda: self.shift_week(-7))
        head.addWidget(self.prev)
        self.next = _Arrow("chevron-right", "Next week")
        self.next.clicked.connect(lambda: self.shift_week(7))
        head.addWidget(self.next)
        column.addLayout(head)

        self.days = QWidget()
        self.day_row = QHBoxLayout(self.days)
        self.day_row.setContentsMargins(0, 0, 0, 0)
        self.day_row.setSpacing(6)
        column.addWidget(self.days)
        self.day_buttons: list[QPushButton] = []

        self.list_host = QWidget()
        self.list_rows = QVBoxLayout(self.list_host)
        self.list_rows.setContentsMargins(0, 0, 0, 0)
        self.list_rows.setSpacing(4)
        column.addWidget(self.list_host)
        self.empty = label("", "mediaNote")
        self.empty.setVisible(False)
        column.addWidget(self.empty)
        self._render_days()
        self._render_rows()

    # clock -----------------------------------------------------------------
    def set_now(self, when: datetime) -> None:
        """Freeze the clock (tests and screenshots use a fixed day)."""
        self._now = when
        self._week_start = when.replace(hour=0, minute=0, second=0, microsecond=0)
        self._selected = 0
        self._render_days()
        self._render_rows()

    def now_text(self) -> str:
        return f"Now: {self._now.strftime('%Y-%m-%d %H:%M')}"

    def shift_week(self, days: int) -> None:
        self._week_start += timedelta(days=days)
        self._selected = 0
        self.now_label.setText(self.now_text())
        self._render_days()
        self._render_rows()

    def select_day(self, index: int) -> None:
        self._selected = max(0, min(6, int(index)))
        self.now_label.setText(self.now_text())
        self._render_days()
        self._render_rows()

    def selected_day(self) -> datetime:
        return self._week_start + timedelta(days=self._selected)

    def week_start(self) -> datetime:
        return self._week_start

    # data ------------------------------------------------------------------
    def set_entries(self, entries: Iterable[Any]) -> None:
        """Air dates drive the list; each entry needs ``air_at``, title, episode."""
        self._entries = sorted(
            (entry for entry in entries if _air_at(entry) is not None),
            key=lambda entry: _air_at(entry) or 0.0,
        )
        self._render_rows()

    def entries_for_day(self, moment: datetime | None = None) -> list[Any]:
        day = (moment or self.selected_day()).date()
        return [
            entry for entry in self._entries
            if datetime.fromtimestamp(_air_at(entry) or 0.0).date() == day
        ]

    def _render_days(self) -> None:
        clear_layout(self.day_row)
        self.day_buttons = []
        today = self._now.date()
        for index in range(7):
            day = (self._week_start + timedelta(days=index)).date()
            button = QPushButton(day.strftime("%a %d"))
            button.setObjectName("dayChip")
            button.setCheckable(True)
            button.setChecked(index == self._selected)
            button.setProperty("today", day == today)
            button.setCursor(Qt.PointingHandCursor)
            button.setToolTip(day.strftime("%A %d %B"))
            button.clicked.connect(lambda _=False, i=index: self.select_day(i))
            self.day_row.addWidget(button, 1)
            self.day_buttons.append(button)

    def _render_rows(self) -> None:
        clear_layout(self.list_rows)
        entries = self.entries_for_day()
        for entry in entries:
            self.list_rows.addWidget(_schedule_row(entry, self.entry_clicked.emit))
        self.list_host.setVisible(bool(entries))
        if not entries:
            self.empty.setText(
                f"Nothing airs on {self.selected_day().strftime('%A %d %B')}."
            )
        self.empty.setVisible(not entries)

    def rows(self) -> int:
        return self.list_rows.count()

    def now_label_text(self) -> str:
        return self.now_label.text()


def _air_at(entry: Any) -> float | None:
    if isinstance(entry, dict):
        value = entry.get("air_at")
    else:
        value = getattr(entry, "air_at", None)
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _schedule_row(entry: Any, emit: Callable[[object], None]) -> QWidget:
    if isinstance(entry, dict):
        title = str(entry.get("title") or "Untitled")
        episode = entry.get("episode") or ""
        air_at = entry.get("air_at")
    else:
        title = str(getattr(entry, "title", "") or "Untitled")
        episode = getattr(entry, "episode", "") or ""
        air_at = getattr(entry, "air_at", None)
    row = QFrame()
    row.setObjectName("scheduleRow")
    line = QHBoxLayout(row)
    line.setContentsMargins(10, 6, 10, 6)
    line.setSpacing(12)
    try:
        stamp = datetime.fromtimestamp(float(air_at))
        time_text = stamp.strftime("%H:%M")
    except (TypeError, ValueError, OSError):
        time_text = "Day"
    clock = QLabel(time_text)
    clock.setObjectName("scheduleTime")
    clock.setFixedWidth(52)
    line.addWidget(clock, 0, Qt.AlignVCenter)
    name = ElideLabel(title)
    name.setObjectName("scheduleTitle")
    line.addWidget(name, 1)
    if episode not in ("", None):
        play = QPushButton(f"Episode {episode}")
        play.setObjectName("scheduleEpisode")
        play.setCursor(Qt.PointingHandCursor)
        play.clicked.connect(lambda _=False, item=entry: emit(item))
        line.addWidget(play, 0, Qt.AlignVCenter)
    return row


__all__ = [
    "FeedRow",
    "PosterRow",
    "ScheduleStrip",
    "ThreeColumnList",
]
