"""Small Qt widgets shared by the Browse, Library, Downloads and Media views.

Everything here is presentation only: the data classes come from the shared
core (``archive``, ``library_store``, ``downloader``, ``tmdb``…), exactly like
the GTK frontend.  Widgets that are easier to make pixel-accurate by hand
(rail buttons, filter rows, cover art) paint themselves.
"""

from __future__ import annotations

import hashlib
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Iterable, Sequence

from PySide6.QtCore import QObject, QPoint, QRect, QRectF, QSize, Qt, QThreadPool, QTimer, QRunnable, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .qt_icons import icon, icon_pixmap, paint_icon
from .qt_theme import alpha

#: Theme values custom-painted widgets need before QSS can reach them.
THEME_STATE: dict[str, object] = {
    "muted": theme.DARK["muted"],
    "resolved": "dark",
    "palette": dict(theme.DARK),
}


def set_theme(resolved: str) -> None:
    """Remember the resolved palette so painted widgets can colour themselves."""
    palette = dict(theme.palette(resolved))
    THEME_STATE["resolved"] = resolved
    THEME_STATE["palette"] = palette
    THEME_STATE["muted"] = palette["muted"]


def color(token: str) -> QColor:
    palette = THEME_STATE["palette"]
    assert isinstance(palette, dict)
    return QColor(palette.get(token, palette["text"]))


def alpha_color(token: str, opacity: float) -> QColor:
    base = color(token)
    return QColor(base.red(), base.green(), base.blue(), int(max(0.0, min(1.0, opacity)) * 255))


# --------------------------------------------------------------------------
# threading
# --------------------------------------------------------------------------


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(object)


class _Job(QRunnable):
    def __init__(self, fn: Callable[[], object]) -> None:
        super().__init__()
        self.fn = fn
        self.signals = _Signals()

    def run(self) -> None:  # runs on a pool thread
        try:
            result = self.fn()
        except Exception as exc:  # noqa: BLE001 - reported to the UI thread
            self.signals.failed.emit(exc)
        else:
            self.signals.done.emit(result)


_JOBS: set[_Job] = set()


def run_async(
    fn: Callable[[], object],
    on_done: Callable[[object], None],
    on_error: Callable[[object], None] | None = None,
) -> None:
    """Run ``fn`` on a worker thread; callbacks run on the UI thread.

    Network and disk work never happens on the UI thread (same rule as the
    GTK frontend).
    """
    job = _Job(fn)
    _JOBS.add(job)

    def finish(callback: Callable[[object], None] | None, value: object) -> None:
        _JOBS.discard(job)
        if callback is not None:
            callback(value)

    job.signals.done.connect(lambda v: finish(on_done, v))
    job.signals.failed.connect(lambda e: finish(on_error, e))
    QThreadPool.globalInstance().start(job)


# --------------------------------------------------------------------------
# image helpers
# --------------------------------------------------------------------------


def search_icon(colour: QColor, size: int = 16) -> QIcon:
    return icon("search", size, colour, 1.6)


def cached_image_bytes(url: str, cache_root: Path | None, key: str,
                       timeout: float = 20.0) -> bytes:
    """Return image bytes for ``url``, using ``cache_root`` when given.

    Cached files are keyed by a hash of the URL so two posters never collide.
    Network failures raise; callers decide whether that matters.
    """
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
    if cache_root is not None:
        folder = Path(cache_root) / "qt-images"
        cached = folder / f"{digest}.img"
        try:
            if cached.is_file() and cached.stat().st_size > 512:
                return cached.read_bytes()
        except OSError:
            pass
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Lumora/0.3 (+https://github.com/kant-sompong/Lumora)",
            "Accept": "image/avif,image/webp,image/png,image/jpeg,*/*;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            data = response.read(8 * 1024 * 1024)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        raise
    if cache_root is not None and len(data) > 512:
        try:
            folder.mkdir(parents=True, exist_ok=True)
            temp = folder / f"{digest}.part"
            temp.write_bytes(data)
            temp.replace(folder / f"{digest}.img")
        except OSError:
            pass
    return data


def pixmap_from_bytes(data: object) -> QPixmap | None:
    if not isinstance(data, (bytes, bytearray)):
        return None
    pix = QPixmap()
    return pix if pix.loadFromData(bytes(data)) else None


# --------------------------------------------------------------------------
# labels
# --------------------------------------------------------------------------


class ElideLabel(QLabel):
    """A one-line label that elides its text and shows the rest in a tooltip."""

    def __init__(self, text: str = "", *, mode: Qt.TextElideMode = Qt.ElideRight) -> None:
        super().__init__(text)
        self._mode = mode
        self._full = text
        self.setToolTip(text)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt API
        self._full = text
        self.setToolTip(text)
        super().setText(text)
        self._apply()

    def full_text(self) -> str:
        return self._full

    def paintEvent(self, event) -> None:  # noqa: N802
        self._apply()
        super().paintEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply()

    def _apply(self) -> None:
        width = max(0, self.contentsRect().width())
        metrics = QFontMetrics(self.font())
        shown = metrics.elidedText(self._full, self._mode, width) if width else self._full
        if shown != super().text():
            QLabel.setText(self, shown)


def label(text: str, name: str | None = None, *, wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


# --------------------------------------------------------------------------
# buttons
# --------------------------------------------------------------------------


class IconButton(QAbstractButton):
    """Square header button with a painted line icon.

    The icon is painted here rather than set as a ``QIcon`` so the exact same
    lines and colours appear on every platform and in both palettes.
    """

    def __init__(self, name: str, tip: str = "", size: int = 18,
                 box: int = 36, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("iconButton")
        self._name = name
        self._size = size
        self._hover = False
        self.setCheckable(False)
        self.setFixedSize(box, box)
        self.setCursor(Qt.PointingHandCursor)
        if tip:
            self.setToolTip(tip)
            # The icon is painted, so nothing names this button for a screen
            # reader unless we do it here.
            self.setAccessibleName(tip)
        self.setAttribute(Qt.WA_Hover, True)

    def set_icon(self, name: str) -> None:
        self._name = name
        self.update()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if self._hover and self.isEnabled():
            painter.setPen(Qt.NoPen)
            painter.setBrush(alpha_color("text", 0.07))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
        elif self.isChecked():
            painter.setPen(Qt.NoPen)
            painter.setBrush(alpha_color("text", 0.10))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
        tint = color("muted") if self.isEnabled() else alpha_color("text", 0.28)
        size = float(self._size)
        paint_icon(
            painter, self._name,
            QRectF((self.width() - size) / 2, (self.height() - size) / 2, size, size),
            tint, 1.7,
        )


class RailButton(QWidget):
    """One destination in the far-left rail: icon, label, optional badge."""

    clicked = Signal()

    def __init__(self, key: str, icon_name: str, text: str, tip: str = "") -> None:
        super().__init__()
        self.key = key
        self._icon = icon_name
        self._text = text
        self._badge = 0
        self._active = False
        self._hover = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(64, 58)
        self.setToolTip(tip or text)
        self.setAttribute(Qt.WA_Hover, True)

    def set_active(self, active: bool) -> None:
        if active != self._active:
            self._active = active
            self.update()

    def set_badge(self, count: int) -> None:
        if count != self._badge:
            self._badge = count
            self.update()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(4, 2, -4, -2)
        if self._active:
            painter.setBrush(alpha_color("text", 0.075))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(rect, 8, 8)
        elif self._hover:
            painter.setBrush(alpha_color("text", 0.045))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(rect, 8, 8)

        tint = color("accent_text") if self._active else color("muted")
        icon_size = 21.0
        paint_icon(
            painter, self._icon,
            QRectF((self.width() - icon_size) / 2, 9, icon_size, icon_size),
            tint, 1.65,
        )

        painter.setPen(tint if self._active else color("muted"))
        font = QFont(self.font())
        font.setPointSizeF(8.6)
        font.setWeight(QFont.Medium if self._active else QFont.Normal)
        painter.setFont(font)
        painter.drawText(
            QRectF(0, 33, self.width(), 16),
            Qt.AlignHCenter | Qt.AlignTop,
            self._text,
        )

        if self._badge:
            text = "99+" if self._badge > 99 else str(self._badge)
            badge_font = QFont(self.font())
            badge_font.setPointSizeF(7.4)
            badge_font.setWeight(QFont.DemiBold)
            painter.setFont(badge_font)
            width = max(15.0, QFontMetrics(badge_font).horizontalAdvance(text) + 8.0)
            badge = QRectF(self.width() - width - 7, 5, width, 15)
            painter.setBrush(QColor(color("accent_fill")))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(badge, 7.5, 7.5)
            painter.setPen(QColor(theme.DARK["on_accent"]) if THEME_STATE["resolved"] == "dark"
                           else QColor(theme.LIGHT["on_accent"]))
            painter.drawText(badge, Qt.AlignCenter, text)


class CheckRow(QWidget):
    """A filter row: rounded checkbox, label, optional count on the right."""

    toggled = Signal(bool)

    def __init__(self, text: str, count: int | None = None, *,
                 checked: bool = False, tooltip: str = "") -> None:
        super().__init__()
        self._text = text
        self._count = count
        self._checked = checked
        self._hover = False
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self.setFixedHeight(24)
        if tooltip:
            self.setToolTip(tooltip)

    def isChecked(self) -> bool:  # noqa: N802
        return self._checked

    def setChecked(self, value: bool) -> None:  # noqa: N802
        if value != self._checked:
            self._checked = value
            self.update()

    def set_text(self, text: str) -> None:
        self._text = text
        self.update()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self._checked = not self._checked
            self.update()
            self.toggled.emit(self._checked)
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if self._hover:
            painter.setBrush(alpha_color("text", 0.05))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(0, 1, 0, -1), 6, 6)

        box = QRectF(6, (self.height() - 15) / 2, 15, 15)
        if self._checked:
            painter.setBrush(QColor(THEME_STATE["palette"]["accent_fill"]))  # type: ignore[index]
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(box, 4, 4)
            paint_icon(painter, "check", box.adjusted(2.4, 2.4, -2.4, -2.4),
                       QColor(THEME_STATE["palette"]["on_accent"]), 2.2)  # type: ignore[index]
        else:
            painter.setBrush(Qt.NoBrush)
            pen = QPen(alpha_color("text", 0.30), 1.2)
            painter.setPen(pen)
            painter.drawRoundedRect(box, 4, 4)

        painter.setPen(color("text"))
        painter.drawText(QRectF(30, 0, self.width() - 70, self.height()),
                         Qt.AlignLeft | Qt.AlignVCenter, self._text)
        if self._count is not None:
            painter.setPen(color("faint"))
            painter.drawText(QRectF(self.width() - 56, 0, 50, self.height()),
                             Qt.AlignRight | Qt.AlignVCenter, f"{self._count:,}")


class SortPill(QPushButton):
    """``Installed size ⌄`` — the compact sort dropdown used in page headers."""

    def __init__(self, text: str, tip: str = "") -> None:
        super().__init__(text)
        self.setObjectName("sortPill")
        self.setCursor(Qt.PointingHandCursor)
        if tip:
            self.setToolTip(tip)
        self.setMinimumHeight(34)
        self._chevron = True

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if not self._chevron:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        paint_icon(
            painter, "chevron-down",
            QRectF(self.width() - 26, (self.height() - 14) / 2, 14, 14),
            color("muted"), 1.8,
        )


class DirectionButton(QPushButton):
    """Up/down arrow paired with a sort pill."""

    def __init__(self, tip: str = "Sort direction") -> None:
        super().__init__()
        self.setObjectName("sortDirection")
        self.setFixedSize(38, 34)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tip)
        self.setAccessibleName(tip)
        self._down = False

    def set_down(self, down: bool) -> None:
        self._down = down
        self.update()

    def is_down(self) -> bool:
        return self._down

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF((self.width() - 15) / 2, (self.height() - 15) / 2, 15, 15)
        painter.save()
        if not self._down:
            painter.translate(rect.center())
            painter.rotate(180)
            painter.translate(-rect.center())
        paint_icon(painter, "chevron-down", rect, color("muted"), 1.8)
        painter.restore()


class ViewScaleButton(IconButton):
    """Grid/list switch: shows the view you would switch *to*."""

    def __init__(self, tip: str = "Grid / list") -> None:
        super().__init__("list", tip, 17, 38)
        self.setCheckable(True)
        self.setFixedSize(38, 34)

    def set_list(self, is_list: bool) -> None:
        self.setChecked(is_list)
        self.set_icon("grid" if is_list else "list")
        switching_to = "Show covers" if is_list else "Show as a list"
        self.setToolTip(switching_to)
        self.setAccessibleName(switching_to)


#: Kept under the old name for imports written against earlier builds.
ViewToggle = ViewScaleButton


# --------------------------------------------------------------------------
# layout
# --------------------------------------------------------------------------


class FlowLayout(QLayout):
    """Left-to-right layout that wraps, like GTK's FlowBox."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 16) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self.setSpacing(spacing)

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802 - Qt API
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._lay_out(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._lay_out(rect, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _lay_out(self, rect: QRect, *, apply: bool) -> int:
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, row_h = area.x(), area.y(), 0
        gap = self.spacing()
        for item in self._items:
            hint = item.sizeHint()
            if x + hint.width() > area.right() + 1 and row_h > 0:
                x = area.x()
                y += row_h + gap
                row_h = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + gap
            row_h = max(row_h, hint.height())
        return y + row_h - rect.y() + m.bottom()


class CoverGrid(QWidget):
    """Responsive grid of same-size cards (4 across on a wide window)."""

    def __init__(self, spacing: int = 16, min_column: int = 230) -> None:
        super().__init__()
        self._spacing = spacing
        self._min_column = min_column
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(spacing)
        self._grid.setVerticalSpacing(20)
        self._cards: list[QWidget] = []
        self._columns = 0

    def clear(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget() if item else None
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self._cards.clear()
        self._columns = 0

    def set_cards(self, cards: Sequence[QWidget]) -> None:
        self.clear()
        self._cards = list(cards)
        for card in self._cards:
            card.setParent(self)
        self._reflow(force=True)

    def add_card(self, card: QWidget) -> None:
        self._cards.append(card)
        card.setParent(self)

    def columns(self) -> int:
        return self._columns

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._reflow()

    def _reflow(self, force: bool = False) -> None:
        width = max(self.width(), self._min_column)
        columns = max(1, (width + self._spacing) // (self._min_column + self._spacing))
        columns = int(columns)
        if columns == self._columns and not force:
            return
        self._columns = columns
        for index, card in enumerate(self._cards):
            self._grid.addWidget(card, index // columns, index % columns)
        for column in range(columns):
            self._grid.setColumnStretch(column, 1)


def clear_layout(layout: QLayout) -> None:
    """Remove every widget from ``layout`` now.

    ``deleteLater`` alone leaves the old widgets painted until the event loop
    gets round to deleting them, which shows up as ghost chips and tiles.
    """
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget() if item else None
        if widget is not None:
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        elif item is not None and item.layout() is not None:
            clear_layout(item.layout())


def horizontal(widgets: Iterable[QWidget], spacing: int = 8,
               margins: tuple[int, int, int, int] = (0, 0, 0, 0)) -> QWidget:
    host = QWidget()
    row = QHBoxLayout(host)
    row.setContentsMargins(*margins)
    row.setSpacing(spacing)
    for widget in widgets:
        row.addWidget(widget)
    return host


# --------------------------------------------------------------------------
# artwork
# --------------------------------------------------------------------------


def monogram_color(text: str) -> QColor:
    """Stable quiet tint for a title, so placeholders differ but stay calm."""
    seed = sum(ord(char) for char in (text or "?"))
    base = QColor(THEME_STATE["palette"]["raised"])  # type: ignore[index]
    shift = (seed % 5) * 6
    return QColor(
        min(255, base.red() + shift + 6),
        min(255, base.green() + shift + 4),
        min(255, base.blue() + shift + 10),
    )


def monogram_initials(text: str) -> str:
    words = [word for word in (text or "").replace("_", " ").split() if word[:1].isalnum()]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][:1] + words[1][:1]).upper()


class CoverArt(QWidget):
    """Rounded cover image; a quiet tinted monogram when there is no art."""

    def __init__(self, width: int | None = None, height: int | None = None,
                 radius: int = 6, *, crop: bool = True, title: str = "") -> None:
        super().__init__()
        self._pixmap: QPixmap | None = None
        self._radius = radius
        self._crop = crop
        self._title = title
        if width and height:
            self.setFixedSize(width, height)
        else:
            self.setMinimumSize(60, 60)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_title(self, text: str) -> None:
        if text != self._title:
            self._title = text
            self.update()

    def set_pixmap(self, pix: QPixmap | None) -> None:
        self._pixmap = None if pix is None or pix.isNull() else pix
        self.update()

    def clear(self) -> None:
        self._pixmap = None
        self.update()

    def set_image(self, path: str) -> None:
        pix = QPixmap(path) if path and Path(path).is_file() else QPixmap()
        self.set_pixmap(None if pix.isNull() else pix)

    def has_art(self) -> bool:
        return self._pixmap is not None

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()), self._radius, self._radius)
        painter.setClipPath(clip)

        if self._pixmap is None:
            painter.fillRect(self.rect(), monogram_color(self._title))
            font = QFont(self.font())
            font.setWeight(QFont.DemiBold)
            font.setPointSizeF(max(11.0, min(self.width(), self.height()) * 0.30))
            painter.setFont(font)
            painter.setPen(alpha_color("text", 0.5))
            painter.drawText(self.rect(), Qt.AlignCenter, monogram_initials(self._title))
            return

        source = self._pixmap
        target = QRectF(self.rect())
        scaled = source.scaled(
            self.size(),
            Qt.KeepAspectRatioByExpanding if self._crop else Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        painter.drawPixmap(
            int((self.width() - scaled.width()) / 2),
            int((self.height() - scaled.height()) / 2),
            scaled,
        )
        painter.setClipping(False)
        painter.setClipping(True)
        painter.setClipPath(clip)
        if not self._crop:
            painter.fillRect(target, alpha_color("text", 0.02))


# --------------------------------------------------------------------------
# page scaffolding
# --------------------------------------------------------------------------


def scroll_page() -> tuple[QScrollArea, QWidget, QVBoxLayout]:
    """A vertical page: scroll area, content widget and its layout."""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setFrameShape(QFrame.NoFrame)
    body = QWidget()
    body.setObjectName("pageBody")
    column = QVBoxLayout(body)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(0)
    scroll.setWidget(body)
    return scroll, body, column


def divider(height: int = 1) -> QFrame:
    line = QFrame()
    line.setObjectName("detailDivider")
    line.setFixedHeight(height)
    return line


def space(height: int) -> QWidget:
    widget = QWidget()
    widget.setFixedHeight(height)
    return widget


# --------------------------------------------------------------------------
# toast
# --------------------------------------------------------------------------


class Toast(QFrame):
    """A short message pinned to the bottom centre of its parent window.

    Mirrors the GTK app's toast: it fades out on its own, never takes focus
    and a newer message simply replaces the one on screen.
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("toast")
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 10, 16, 10)
        self._label = QLabel()
        self._label.setWordWrap(True)
        row.addWidget(self._label)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        self.hide()

    def show_message(self, text: str, error: bool = False, ms: int = 4500) -> None:
        self._label.setText(text)
        self.setProperty("error", "true" if error else "false")
        self.style().unpolish(self)
        self.style().polish(self)
        self.setMaximumWidth(max(240, min(560, self.parentWidget().width() - 48)))
        self.adjustSize()
        self.reposition()
        self.show()
        self.raise_()
        self._timer.start(ms)

    def reposition(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        self.move((parent.width() - self.width()) // 2, parent.height() - self.height() - 28)


class EmptyState(QWidget):
    """Centred icon + title + hint, used by empty pages and drawers."""

    def __init__(self, icon_name: str, title: str, hint: str = "") -> None:
        super().__init__()
        column = QVBoxLayout(self)
        column.setContentsMargins(24, 24, 24, 24)
        column.setSpacing(8)
        column.addStretch(1)
        self._icon = _EmptyIcon(icon_name)
        column.addWidget(self._icon, 0, Qt.AlignHCenter)
        self.title = label(title, "emptyTitle")
        self.title.setAlignment(Qt.AlignCenter)
        column.addWidget(self.title)
        self.hint = label(hint, "emptySub", wrap=True)
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setVisible(bool(hint))
        column.addWidget(self.hint)
        column.addStretch(1)

    def set_text(self, title: str, hint: str = "") -> None:
        self.title.setText(title)
        self.hint.setText(hint)
        self.hint.setVisible(bool(hint))


class _EmptyIcon(QWidget):
    def __init__(self, name: str) -> None:
        super().__init__()
        self._name = name
        self.setFixedSize(56, 56)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        paint_icon(painter, self._name, QRectF(10, 10, 36, 36), alpha_color("text", 0.28), 1.6)


class ScrollRow(QScrollArea):
    """Horizontal strip with arrow buttons, like the media carousels."""

    def __init__(self, height: int, spacing: int = 16) -> None:
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFixedHeight(height)
        self._host = QWidget()
        self._row = QHBoxLayout(self._host)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(spacing)
        self._row.addStretch(1)
        self.setWidget(self._host)

    def add(self, widget: QWidget) -> None:
        self._row.insertWidget(self._row.count() - 1, widget)

    def clear(self) -> None:
        while self._row.count() > 1:
            item = self._row.takeAt(0)
            widget = item.widget() if item else None
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    def scroll_by(self, delta: int) -> None:
        bar = self.horizontalScrollBar()
        bar.setValue(bar.value() + delta)


class HeroBanner(QWidget):
    """Backdrop image with a bottom-left scrim; children are laid out on top."""

    def __init__(self, height: int = 420) -> None:
        super().__init__()
        self._pixmap: QPixmap | None = None
        self.setMinimumHeight(height)
        self.setMaximumHeight(height)

    def set_pixmap(self, pix: QPixmap | None) -> None:
        self._pixmap = None if pix is None or pix.isNull() else pix
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        if self._pixmap is not None:
            scaled = self._pixmap.scaled(
                self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
            )
            painter.drawPixmap(
                int((self.width() - scaled.width()) / 2),
                int((self.height() - scaled.height()) / 2),
                scaled,
            )
        else:
            painter.fillRect(self.rect(), color("surface"))

        gradient = QLinearGradient(0, 0, 0, self.height())
        base = color("bg")
        gradient.setColorAt(0.0, QColor(base.red(), base.green(), base.blue(), 40))
        gradient.setColorAt(0.55, QColor(base.red(), base.green(), base.blue(), 150))
        gradient.setColorAt(1.0, QColor(base.red(), base.green(), base.blue(), 250))
        painter.fillRect(self.rect(), gradient)

        side = QLinearGradient(0, 0, self.width() * 0.75, 0)
        side.setColorAt(0.0, QColor(base.red(), base.green(), base.blue(), 215))
        side.setColorAt(0.75, QColor(base.red(), base.green(), base.blue(), 0))
        painter.fillRect(self.rect(), side)


class Popover(QFrame):
    """A small floating panel (filter sheet, kebab menu) anchored to a widget."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("popover")
        self.setWindowFlags(Qt.Popup)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._column = QVBoxLayout(self)
        self._column.setContentsMargins(10, 10, 10, 10)
        self._column.setSpacing(2)

    def column(self) -> QVBoxLayout:
        return self._column

    def add_widget(self, widget: QWidget) -> None:
        self._column.addWidget(widget)

    def show_at(self, anchor: QWidget, *, align: str = "right") -> None:
        self.adjustSize()
        size = self.sizeHint()
        point = anchor.mapToGlobal(QPoint(0, anchor.height() + 6))
        if align == "right":
            point.setX(anchor.mapToGlobal(QPoint(anchor.width(), 0)).x() - size.width())
        self.move(point)
        self.show()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)


def add_popover_rows(popover: Popover, rows: Sequence[QWidget]) -> None:
    for row in rows:
        popover.add_widget(row)


class SectionTitle(QWidget):
    """``Trending Now   40   This week on TMDB`` with an optional right link."""

    def __init__(self, title: str, count: str = "", note: str = "") -> None:
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        self.title = label(title, "sectionTitle")
        row.addWidget(self.title)
        self.count = label(count, "sectionCount")
        row.addWidget(self.count)
        self.note = label(note, "sectionNote")
        row.addWidget(self.note)
        row.addStretch(1)
        self.tail = QWidget()
        self._tail_row = QHBoxLayout(self.tail)
        self._tail_row.setContentsMargins(0, 0, 0, 0)
        self._tail_row.setSpacing(8)
        row.addWidget(self.tail)

    def add_tail(self, widget: QWidget) -> None:
        self._tail_row.addWidget(widget)


__all__ = [
    "CheckRow",
    "CoverArt",
    "CoverGrid",
    "DirectionButton",
    "ElideLabel",
    "EmptyState",
    "FlowLayout",
    "HeroBanner",
    "IconButton",
    "Popover",
    "RailButton",
    "ScrollRow",
    "SectionTitle",
    "SortPill",
    "THEME_STATE",
    "Toast",
    "ViewToggle",
    "add_popover_rows",
    "alpha_color",
    "cached_image_bytes",
    "clear_layout",
    "color",
    "divider",
    "horizontal",
    "label",
    "monogram_initials",
    "pixmap_from_bytes",
    "run_async",
    "scroll_page",
    "search_icon",
    "set_theme",
    "space",
]
