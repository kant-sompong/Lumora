"""Hover preview for library cards on the Windows (Qt) frontend.

The switch is the shared one in Tools → Interface (``view_prefs.quick_preview``).
Everything the popover shows comes from the entry the page already loaded — the
metadata, the cached cover, the tags in settings — so showing it never touches
the network and never opens a file.

It is a plain child widget, not a window: it takes no keyboard focus, and it
goes away on mouse leave, Escape, a selection change, a re-render and when the
window is deactivated.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from . import library_tools, view_prefs
from .qt_widgets import ElideLabel, label
from .util import human_size

#: How long the pointer has to rest on a tile before the popover opens.
HOVER_MS = 400
#: Cover box in the popover.
COVER = (96, 128)
#: How much of the description is shown: the first two non-empty lines.
DETAIL_LINES = 2
#: A single very long first line is cut here so the popover stays a popover.
LINE_CHARS = 120
POPOVER_WIDTH = 340
#: Widths tried when there is no room beside the tile; the last is the floor.
WIDTHS = (POPOVER_WIDTH, 300, 260, 220)
MIN_WIDTH = WIDTHS[-1]
#: Space left between the popover and the tile.
GAP = 8


def preview_text(entry: Any) -> str:
    """The first two non-empty lines of a description, each trimmed."""
    lines = [line.strip() for line in str(getattr(entry, "description", "") or "").splitlines()]
    kept = [line[:LINE_CHARS] for line in lines if line][:DETAIL_LINES]
    return "\n".join(kept)


def meta_text(entry: Any) -> str:
    """Year and size, the two facts a card shows as well."""
    bits = [
        str(getattr(entry, "year", "") or "").strip(),
        human_size(getattr(entry, "size", 0)) if getattr(entry, "size", 0) else "",
    ]
    return " · ".join(bit for bit in bits if bit) or "—"


def grid_area(page: Any) -> QRect:
    """The grid's own rectangle in page coordinates."""
    area = page.rect()
    view = getattr(page, "scroll", None)
    if view is None:
        return area
    viewport = view.viewport()
    corner = viewport.mapTo(page, QPoint(0, 0))
    bounds = QRect(corner, viewport.size()).intersected(area)
    if bounds.width() < 80 or bounds.height() < 80:
        return area
    return bounds


def tile_rect(page: Any, card: Any) -> QRect:
    """A tile's rectangle in page coordinates, as it is on screen now."""
    return QRect(card.mapTo(page, QPoint(0, 0)), card.size())


def candidates(tile: QRect, size: Any, area: QRect) -> list[QRect]:
    """Right, left, below, above: the order the popover tries them."""
    width, height = size.width(), size.height()
    below = tile.bottom() + GAP
    above = tile.top() - GAP - height
    flush = max(area.left(), min(tile.left(), area.right() - width))
    low = max(area.top(), min(tile.top(), area.bottom() - height))
    return [
        QRect(tile.right() + GAP, low, width, height),
        QRect(tile.left() - GAP - width, low, width, height),
        QRect(tile.left(), below, width, height),
        QRect(flush, below, width, height),
        QRect(tile.left(), above, width, height),
        QRect(flush, above, width, height),
        QRect(flush, below - GAP - height, width, height),
    ]


class QuickPreview(QFrame):
    """A small, focus-less popover built from one library entry."""

    def __init__(self, page: Any, *, delay: int = HOVER_MS) -> None:
        super().__init__(page)
        self.page = page
        self._delay = max(0, int(delay))
        self._entry: Any = None
        self._card: QWidget | None = None
        self._tiles: dict[QWidget, Any] = {}
        self._window: QWidget | None = None
        self._anchor_pending = False
        self.setObjectName("popover")
        self.setFocusPolicy(Qt.NoFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        # Clicks fall through, and moving the pointer onto the popover counts
        # as leaving the tile, which closes it again.
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFixedWidth(POPOVER_WIDTH)

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 10, 12, 10)
        row.setSpacing(10)
        self.cover = QLabel()
        self.cover.setFixedSize(*COVER)
        self.cover.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self.cover.setObjectName("coverMeta")
        row.addWidget(self.cover, 0, Qt.AlignTop)

        column = QVBoxLayout()
        column.setSpacing(2)
        self.title = ElideLabel("", mode=Qt.ElideRight)
        self.title.setObjectName("listTitle")
        column.addWidget(self.title)
        self.meta = label("", "coverMeta")
        column.addWidget(self.meta)
        self.tags_label = label("", "detailNote", wrap=True)
        column.addWidget(self.tags_label)
        self.description = label("", "detailNote", wrap=True)
        column.addWidget(self.description)
        column.addStretch(1)
        row.addLayout(column, 1)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._open)

        self._escape = QShortcut(QKeySequence(Qt.Key_Escape), page)
        self._escape.setContext(Qt.WidgetWithChildrenShortcut)
        self._escape.setEnabled(False)
        self._escape.activated.connect(self.hide)

        self._space = QShortcut(QKeySequence(Qt.Key_Space), page)
        self._space.setContext(Qt.WidgetWithChildrenShortcut)
        self._space.activated.connect(self.show_selected)
        self._space.setEnabled(False)

        app = QApplication.instance()
        if app is not None:
            app.focusChanged.connect(self._focus_changed)

        self.hide()
        self.selection_changed()

    # -- what the switch says ------------------------------------------------
    def settings(self) -> Any:
        return getattr(self.page, "settings", None)

    def enabled(self) -> bool:
        return bool(view_prefs.quick_preview(self.settings()))

    def selection(self) -> str:
        return str(getattr(self.page, "_selected", "") or "")

    # -- tracking tiles ------------------------------------------------------
    def track(self, card: QWidget, entry: Any) -> None:
        """Watch one rendered tile; called from the page's own ``_wire``."""
        self._tiles[card] = entry
        card.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:
        kind = event.type()
        if kind == QEvent.Enter and watched in self._tiles:
            self._hover(watched)
        elif kind == QEvent.Leave and watched is self._card or kind == QEvent.WindowDeactivate and watched is self._window:
            self.hide()
        return False

    def _hover(self, card: QWidget) -> None:
        if not self.enabled():
            return
        self._card = card
        self._entry = self._tiles.get(card)
        if self._entry is None:
            return
        if self._delay:
            self._timer.start(self._delay)
        else:
            self._open()

    # -- opening and closing -------------------------------------------------
    def show_selected(self) -> bool:
        """Open on the card that is already selected (the Space key)."""
        if not self.enabled():
            return False
        key = self.selection()
        entry = next(
            (item for item in getattr(self.page, "_entries", []) or [] if item.identifier == key),
            None,
        )
        if entry is None:
            return False
        self._card = (getattr(self.page, "_cards", {}) or {}).get(key)
        self._entry = entry
        self._timer.stop()
        self._open()
        return True

    def selection_changed(self) -> None:
        """The page picked a different game: the preview belongs to the old one."""
        self.hide()

    def cards_rebuilt(self) -> None:
        """The grid threw its tiles away: keep an open preview on the new one.

        Selecting a game opens the detail panel, which reflows the grid, so the
        tile the popover was anchored to is gone a moment after it opened.
        """
        if not self.isVisible():
            return
        key = str(getattr(self._entry, "identifier", "") or "")
        card = (getattr(self.page, "_cards", {}) or {}).get(key)
        if card is None:
            self.hide()
            return
        self._card = card
        self._schedule_anchor()

    def hide(self) -> None:
        self._timer.stop()
        if getattr(self, "_escape", None) is not None:
            self._escape.setEnabled(False)
        self._refresh_space()
        super().hide()

    def _open(self) -> None:
        entry = self._entry
        if entry is None or not self.enabled():
            return
        self.fill(entry)
        self._watch_window()
        self.adjustSize()
        self._place()
        self.show()
        self.raise_()
        self._escape.setEnabled(True)
        # The grid may still be reflowing (the detail panel just opened), so
        # place again on the next event-loop turn with the settled geometry.
        self._schedule_anchor()

    def fill(self, entry: Any) -> None:
        """Fill every line from the entry itself."""
        self.title.setText(entry.title or entry.identifier)
        self.meta.setText(meta_text(entry))
        tags = library_tools.tags_for(self.settings(), entry)
        self.tags_label.setText("Tags: " + ", ".join(tags) if tags else "")
        self.tags_label.setVisible(bool(tags))
        detail = preview_text(entry)
        self.description.setText(detail)
        self.description.setVisible(bool(detail))
        self._fill_cover(entry)

    def _fill_cover(self, entry: Any) -> None:
        self.cover.clear()
        pixmap = (getattr(self.page, "_pix", {}) or {}).get(entry.identifier)
        if pixmap is not None and not pixmap.isNull():
            self.cover.setPixmap(
                pixmap.scaled(*COVER, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            )
            return
        self.cover.setText((entry.title or "?")[:1].upper())

    def _place(self) -> None:
        """Beside the tile, inside the grid, never over the tile itself."""
        card = self._card
        if card is None:
            return
        page = self.page
        area = grid_area(page)
        tile = tile_rect(page, card)
        others = [
            tile_rect(page, other)
            for key, other in (getattr(page, "_cards", {}) or {}).items()
            if other is not card
        ]
        # First positions that also leave the neighbouring tiles alone, then the
        # same list ignoring them: the tile itself is the rule.
        for avoid_neighbours in (True, False):
            for width in WIDTHS:
                self.setFixedWidth(width)
                self.adjustSize()
                size = self.size()
                if size.width() > area.width() or size.height() > area.height():
                    continue
                for spot in candidates(tile, size, area):
                    if spot.intersects(tile) or not area.contains(spot):
                        continue
                    if avoid_neighbours and any(spot.intersects(o) for o in others):
                        continue
                    self.move(spot.topLeft())
                    return
        # Nothing fits without covering the tile: better no popover than one
        # that hides the game it describes.
        self.hide()

    def _reanchor(self) -> None:
        if self.isVisible():
            self._place()

    def _schedule_anchor(self) -> None:
        """Re-place once the grid has settled, with the tile's current geometry."""
        if self._anchor_pending:
            return
        self._anchor_pending = True

        def run() -> None:
            self._anchor_pending = False
            self._reanchor()

        QTimer.singleShot(0, run)

    def _watch_window(self) -> None:
        window = self.page.window()
        if window is self._window:
            return
        if self._window is not None:
            self._window.removeEventFilter(self)
        self._window = window
        if window is not None:
            window.installEventFilter(self)

    # -- keys ----------------------------------------------------------------
    def _focus_changed(self, _old=None, widget=None) -> None:
        if widget is None:
            widget = QApplication.focusWidget()
        self._typing = isinstance(
            widget, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)
        )
        self._refresh_space()

    def _refresh_space(self) -> None:
        """Space only opens the preview when it cannot mean "scroll" or a space."""
        self._space.setEnabled(
            self.enabled() and bool(self.selection()) and not getattr(self, "_typing", False)
        )


__all__ = [
    "COVER",
    "DETAIL_LINES",
    "HOVER_MS",
    "MIN_WIDTH",
    "POPOVER_WIDTH",
    "WIDTHS",
    "QuickPreview",
    "candidates",
    "grid_area",
    "meta_text",
    "preview_text",
    "tile_rect",
]
