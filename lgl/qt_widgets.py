"""Small Qt widgets shared by the Library, Archive and Downloads views."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QPoint, QRect, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QLayout, QLayoutItem, QWidget


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

#: Theme-dependent values widgets need before the stylesheet can reach them.
THEME_STATE = {"muted": "#9aa3af"}


def run_async(
    fn: Callable[[], object],
    on_done: Callable[[object], None],
    on_error: Callable[[object], None] | None = None,
) -> None:
    """Run ``fn`` on a worker thread; callbacks run on the UI thread.

    Network and disk work never happens on the UI thread (same rule as the
    Tkinter frontend).
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


def search_icon(color: QColor, size: int = 16) -> QIcon:
    """Magnifier drawn with QPainter, so it needs no font or image file."""
    pix = QPixmap(size * 2, size * 2)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(color.red(), color.green(), color.blue(), 150), 2.6)
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    painter.drawEllipse(5, 5, 15, 15)
    painter.drawLine(18, 18, 27, 27)
    painter.end()
    pix.setDevicePixelRatio(2)
    return QIcon(pix)


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


class CoverArt(QWidget):
    """Rounded cover image; a quiet tinted block when there is no art."""

    def __init__(self, width: int, height: int, radius: int = 6) -> None:
        super().__init__()
        self._pixmap: QPixmap | None = None
        self._radius = radius
        self.setFixedSize(width, height)

    def set_pixmap(self, pix: QPixmap) -> None:
        self._pixmap = None if pix.isNull() else pix
        self.update()

    def clear(self) -> None:
        self._pixmap = None
        self.update()

    def set_image(self, path: str) -> None:
        pix = QPixmap(path) if path and Path(path).is_file() else QPixmap()
        self._pixmap = None if pix.isNull() else pix
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        clip = QPainterPath()
        clip.addRoundedRect(self.rect(), self._radius, self._radius)
        painter.setClipPath(clip)
        fg = QColor(self.palette().color(self.foregroundRole()))
        painter.fillRect(self.rect(), QColor(fg.red(), fg.green(), fg.blue(), 12))
        if self._pixmap:
            scaled = self._pixmap.scaled(
                self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            painter.drawPixmap(
                (self.width() - scaled.width()) // 2,
                (self.height() - scaled.height()) // 2,
                scaled,
            )
