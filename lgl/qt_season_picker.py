"""The season picker: a quiet header that opens a panel of season rows.

The closed state is just ``Season N`` and a chevron; nothing is boxed, because
the page already has enough outlines.  The open state is a popup 300 px wide,
14 px radius, at most 420 px tall and scrolling inside, with one 76 px row per
season: portrait thumbnail, the season name and its episode count, a check mark
on the selected row, keyboard navigation and a 120 ms fade.

Every colour, radius and spacing comes from :mod:`lgl.design_tokens`, so the
Linux build can draw the same panel from the same numbers.
"""

from __future__ import annotations

from typing import Callable, Iterable, Sequence

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPoint,
    QPropertyAnimation,
    QRectF,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .design_tokens import RADIUS, SPACING, TEXT_SIZES, TOKENS
from .tmdb import Season, season_poster_url

#: Panel geometry, straight from the contract.
PANEL_WIDTH = 300
PANEL_MAX_HEIGHT = 420
ROW_HEIGHT = 76
ROW_PADDING = 12
THUMB_WIDTH = 44
THUMB_HEIGHT = 64
THUMB_RADIUS = RADIUS[0]
FADE_MS = 120
HEADER_TEXT = TEXT_SIZES["header"]

ImageLoader = Callable[..., None]


def _palette(theme: str) -> dict[str, object]:
    return TOKENS.get(theme) or TOKENS["dark"]


def _alpha(colour: str, opacity: float) -> QColor:
    value = QColor(colour)
    value.setAlphaF(max(0.0, min(1.0, opacity)))
    return value


def _spaced(step: int) -> int:
    """The nearest step on the shared spacing scale."""
    return min(SPACING, key=lambda value: abs(value - step))


class Chevron(QWidget):
    """A small down/up chevron, drawn rather than shipped as an icon."""

    def __init__(self, theme: str = "dark", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._open = False
        self._theme = theme
        self.setFixedSize(14, 14)

    def set_open(self, open_: bool) -> None:
        if open_ != self._open:
            self._open = open_
            self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        colour = QColor(str(_palette(self._theme)["text2"]))
        painter.setPen(Qt.NoPen)
        painter.setBrush(colour)
        middle = self.width() / 2
        if self._open:
            points = [QPoint(int(middle - 5), 8), QPoint(int(middle + 5), 8),
                      QPoint(int(middle), 3)]
        else:
            points = [QPoint(int(middle - 5), 3), QPoint(int(middle + 5), 3),
                      QPoint(int(middle), 8)]
        painter.drawPolygon(points)


class SeasonThumb(QWidget):
    """44x64 portrait art with a 6 px radius, or the season number."""

    def __init__(self, theme: str = "dark", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._pixmap: QPixmap | None = None
        self._number = 0
        self.setFixedSize(THUMB_WIDTH, THUMB_HEIGHT)

    def set_number(self, number: int) -> None:
        self._number = number
        self.update()

    def set_pixmap(self, pixmap: QPixmap | None) -> None:
        self._pixmap = pixmap
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        area = QRectF(0, 0, self.width(), self.height())
        palette = _palette(self._theme)
        if self._pixmap is not None and not self._pixmap.isNull():
            painter.setClipPath(_rounded(area, THUMB_RADIUS))
            scaled = self._pixmap.scaled(
                self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
            )
            painter.drawPixmap(
                int((self.width() - scaled.width()) / 2),
                int((self.height() - scaled.height()) / 2),
                scaled,
            )
            painter.setClipping(False)
            return
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(str(palette["border"])))
        painter.drawRoundedRect(area, THUMB_RADIUS, THUMB_RADIUS)
        font = QFont(self.font())
        font.setPixelSize(TEXT_SIZES["title"])
        font.setWeight(QFont.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(str(palette["muted"])))
        painter.drawText(area, Qt.AlignCenter, str(self._number))


def _rounded(area: QRectF, radius: float):
    from PySide6.QtGui import QPainterPath

    path = QPainterPath()
    path.addRoundedRect(area, radius, radius)
    return path


class SeasonRow(QWidget):
    """One 76 px row: thumbnail, two text lines, and a check when selected."""

    clicked = Signal(int)

    def __init__(self, number: int, name: str, episode_count: int,
                 theme: str = "dark", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.number = number
        self._theme = theme
        self._hover = False
        self._selected = False
        self.setFixedHeight(ROW_HEIGHT)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self.setFocusPolicy(Qt.NoFocus)

        row = QHBoxLayout(self)
        row.setContentsMargins(ROW_PADDING, 0, ROW_PADDING, 0)
        row.setSpacing(_spaced(12))
        self.thumb = SeasonThumb(theme, self)
        self.thumb.set_number(number)
        row.addWidget(self.thumb, 0, Qt.AlignVCenter)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)
        self.name_label = QLabel(self._season_name(number, name), self)
        self.name_label.setObjectName("seasonRowName")
        self.name_label.setStyleSheet("background: transparent;")
        self.count_label = QLabel(f"{episode_count} episodes", self)
        self.count_label.setObjectName("seasonRowCount")
        self.count_label.setStyleSheet("background: transparent;")
        text.addStretch(1)
        text.addWidget(self.name_label)
        text.addWidget(self.count_label)
        text.addStretch(1)
        row.addLayout(text, 1)

        self.check = QLabel("✓", self)
        self.check.setObjectName("seasonRowCheck")
        self.check.setStyleSheet("background: transparent;")
        self.check.setFixedWidth(18)
        self.check.setAlignment(Qt.AlignCenter)
        self.check.setVisible(False)
        row.addWidget(self.check, 0, Qt.AlignVCenter)
        self.apply_theme(theme)

    @staticmethod
    def _season_name(number: int, name: str) -> str:
        label = " ".join(str(name or "").split())
        if number == 0:
            return label or "Specials"
        return label or f"Season {number}"

    def apply_theme(self, theme: str) -> None:
        self._theme = theme
        palette = _palette(theme)
        self.name_label.setStyleSheet(
            f"background: transparent; color: {palette['text']};"
            f" font-size: {TEXT_SIZES['label']}px; font-weight: 600;"
        )
        self.count_label.setStyleSheet(
            f"background: transparent; color: {palette['muted']};"
            f" font-size: {TEXT_SIZES['caption'] + 1}px;"
        )
        self.check.setStyleSheet(
            f"background: transparent; color: {palette['accent']};"
            f" font-size: {TEXT_SIZES['label']}px; font-weight: 700;"
        )
        self.update()

    def set_pixmap(self, pixmap: QPixmap | None) -> None:
        self.thumb.set_pixmap(pixmap)

    def set_selected(self, selected: bool) -> None:
        if selected != self._selected:
            self._selected = selected
            self.check.setVisible(selected)
            self.update()

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self.clicked.emit(self.number)
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        palette = _palette(self._theme)
        area = QRectF(self.rect())
        if self._selected:
            painter.setPen(Qt.NoPen)
            painter.setBrush(_alpha(str(palette["accent"]), 0.18))
            painter.drawRoundedRect(area, RADIUS[0], RADIUS[0])
        elif self._hover:
            painter.setPen(Qt.NoPen)
            painter.setBrush(_alpha(str(palette["text"]), 0.06))
            painter.drawRoundedRect(area, RADIUS[0], RADIUS[0])
        super().paintEvent(_event)


class SeasonPanel(QFrame):
    """The open panel: 300 px wide, overlay, radius 14, scrolling inside."""

    chosen = Signal(int)

    def __init__(self, theme: str = "dark", parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.Popup)
        self._theme = theme
        self.setObjectName("seasonPanel")
        self.setFixedWidth(PANEL_WIDTH)
        self.setMaximumHeight(PANEL_MAX_HEIGHT)
        self.rows: list[SeasonRow] = []
        self._current = 0
        self._seasons: list[Season] = []
        self._posters: dict[int, str] = {}
        self._loader: ImageLoader | None = None
        self._show_poster = ""
        self._pixmaps: dict[int, QPixmap] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.surface = QFrame(self)
        self.surface.setObjectName("seasonPanelSurface")
        surface_column = QVBoxLayout(self.surface)
        surface_column.setContentsMargins(0, 0, 0, 0)
        surface_column.setSpacing(0)
        outer.addWidget(self.surface)
        self.scroll = QScrollArea(self.surface)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body = QWidget()
        self.column = QVBoxLayout(self.body)
        self.column.setContentsMargins(0, 0, 0, 0)
        self.column.setSpacing(0)
        self.column.addStretch(1)
        self.scroll.setWidget(self.body)
        surface_column.addWidget(self.scroll)

        shadow = QGraphicsDropShadowEffect(self.surface)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 110))
        self.surface.setGraphicsEffect(shadow)
        self._fade = QGraphicsOpacityEffect(self)
        self._fade.setOpacity(0.0)
        self._animation = QPropertyAnimation(self._fade, b"opacity", self)
        self._animation.setDuration(FADE_MS)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)
        self._closing = False
        self._animation.finished.connect(self._fade_finished)

    # -- data ---------------------------------------------------------------
    def set_loader(self, loader: ImageLoader | None) -> None:
        self._loader = loader

    def set_seasons(self, seasons: Sequence[Season], show_poster: str = "",
                    selected: int | None = None) -> None:
        self._seasons = sorted(seasons, key=lambda season: season.number)
        self._show_poster = show_poster
        for row in self.rows:
            row.setParent(None)
            row.deleteLater()
        self.rows = []
        for season in self._seasons:
            self._posters[season.number] = season_poster_url(
                season, show_poster, size="w185"
            )
            row = SeasonRow(
                season.number, season.name, season.episode_count, self._theme, self.body
            )
            row.clicked.connect(self._choose)
            self.column.insertWidget(self.column.count() - 1, row)
            self.rows.append(row)
        if selected is None:
            # The page has always opened on season 1, with Specials available
            # above it in the list rather than selected by default.
            regular = [s.number for s in self._seasons if s.number > 0]
            selected = regular[0] if regular else (self._seasons[0].number if self._seasons else 0)
        self.set_current(selected, emit=False)

    def seasons(self) -> list[Season]:
        return list(self._seasons)

    def apply_theme(self, theme: str) -> None:
        self._theme = theme
        self.setStyleSheet(self._qss())
        for row in self.rows:
            row.apply_theme(theme)
        self.update()

    def _qss(self) -> str:
        palette = _palette(self._theme)
        return (
            "QFrame#seasonPanel { background: transparent; border: none; }"
            " QFrame#seasonPanelSurface {"
            f" background: {palette['overlay']};"
            f" border: 1px solid {palette['border']};"
            f" border-radius: {RADIUS[2]}px;"
            "}"
            " QScrollArea { background: transparent; border: none; }"
            " QWidget#qt_scrollarea_viewport { background: transparent; }"
            " QScrollBar:vertical { background: transparent; width: 8px; margin: 4px; }"
            f" QScrollBar::handle:vertical {{ background: {palette['border']};"
            " border-radius: 4px; min-height: 24px; }"
            " QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical"
            " { height: 0; }"
        )

    # -- selection ----------------------------------------------------------
    def current(self) -> int:
        return self._current

    def set_current(self, number: int, *, emit: bool = False) -> None:
        found: int | None = None
        for index, row in enumerate(self.rows):
            row.set_selected(row.number == number)
            if row.number == number:
                found = index
        if found is None and self.rows:
            found = 0
            self.rows[0].set_selected(True)
            number = self.rows[0].number
        self._current = number
        if found is not None:
            self.scroll.ensureWidgetVisible(self.rows[found], 0, 8)
        if emit:
            self.chosen.emit(number)

    def move_current(self, step: int) -> None:
        if not self.rows:
            return
        index = next(
            (position for position, row in enumerate(self.rows) if row.number == self._current),
            0,
        )
        index = max(0, min(len(self.rows) - 1, index + step))
        self.set_current(self.rows[index].number, emit=False)

    def _choose(self, number: int) -> None:
        self.set_current(number, emit=False)
        self.chosen.emit(number)

    # -- thumbnails ---------------------------------------------------------
    def load_posters(self) -> None:
        for row in self.rows:
            number = row.number
            if number in self._pixmaps:
                row.set_pixmap(self._pixmaps[number])
                continue
            url = self._posters.get(number, "")
            if not url:
                continue
            fallback = self._show_poster if self._show_poster.startswith("http") else ""
            if fallback:
                url = url or fallback
            if self._loader is None:
                continue
            self._loader(
                url,
                f"season-poster-{number}",
                lambda pix, n=number: self._poster_loaded(n, pix),
                fallback,
            )

    def _poster_loaded(self, number: int, pixmap: QPixmap | None) -> None:
        if pixmap is None:
            return
        self._pixmaps[number] = pixmap
        for row in self.rows:
            if row.number == number:
                row.set_pixmap(pixmap)

    # -- keyboard / fade ----------------------------------------------------
    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.key() in (Qt.Key_Down, Qt.Key_Up):
            self.move_current(1 if event.key() == Qt.Key_Down else -1)
            event.accept()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._choose(self._current)
            event.accept()
            return
        if event.key() == Qt.Key_Escape:
            self.close()
            event.accept()
            return
        super().keyPressEvent(event)

    def fade_in(self) -> None:
        self._fade.setOpacity(0.0)
        self.setGraphicsEffect(self._fade)
        self._animation.stop()
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.start()
        self.show()
        self.raise_()
        self.setFocus()

    def fade_out(self) -> None:
        self._closing = True
        self._animation.stop()
        self._animation.setStartValue(self._fade.opacity())
        self._animation.setEndValue(0.0)
        self._animation.start()

    def _fade_finished(self) -> None:
        if self._closing:
            self._closing = False
            self.hide()


class SeasonPicker(QWidget):
    """The closed header (``Season N`` + chevron) and its popup panel.

    ``currentIndexChanged`` keeps the signal the media page already connected, so
    the episode list does not need to know this is no longer a combo box.
    """

    currentIndexChanged = Signal(int)

    def __init__(
        self,
        theme: str = "dark",
        parent: QWidget | None = None,
        *,
        image_loader: ImageLoader | None = None,
    ) -> None:
        super().__init__(parent)
        self._theme = theme
        self._seasons: list[Season] = []
        self._current = 0
        #: False until the page or the user actually picks a season, so the
        #: first paint can default to season 1 rather than Specials.
        self._picked = False
        self._show_poster = ""
        self._loader = image_loader
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(_spaced(8))
        self.header = QLabel("Season", self)
        self.header.setObjectName("seasonHeader")
        row.addWidget(self.header)
        self.chevron = Chevron(theme, self)
        row.addWidget(self.chevron)

        self.panel = SeasonPanel(theme, self)
        self.panel.set_loader(image_loader)
        self.panel.chosen.connect(self._chosen)
        self.apply_theme(theme)
        self.setVisible(False)

    # -- combo-box-shaped API ----------------------------------------------
    def count(self) -> int:
        return len(self._seasons)

    def season_numbers(self) -> list[int]:
        return [season.number for season in self._seasons]

    def currentData(self) -> int | None:
        return self._current if self._seasons else None

    def set_seasons(self, seasons: Iterable[Season], show_poster: str = "") -> None:
        self._seasons = sorted(
            (season for season in seasons if season.number >= 0),
            key=lambda season: season.number,
        )
        self._show_poster = show_poster or ""
        known = {season.number for season in self._seasons}
        selected = self._current if self._picked and self._current in known else None
        self.panel.set_seasons(self._seasons, self._show_poster, selected)
        self._current = self.panel.current()
        self.setVisible(bool(self._seasons))
        self._update_header()

    def set_current_number(self, number: int) -> None:
        self._picked = True
        self.panel.set_current(int(number), emit=False)
        self._current = self.panel.current()
        self._update_header()

    # -- painting -----------------------------------------------------------
    def apply_theme(self, theme: str) -> None:
        self._theme = theme
        palette = _palette(theme)
        self.header.setStyleSheet(
            f"color: {palette['text']}; font-size: {HEADER_TEXT}px;"
            " font-weight: 600; background: transparent;"
        )
        self.chevron._theme = theme
        self.chevron.update()
        self.panel.apply_theme(theme)
        self.update()

    def _update_header(self) -> None:
        number = self._current
        self.header.setText("Specials" if number == 0 else f"Season {number}")

    # -- interaction --------------------------------------------------------
    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self.toggle()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter, Qt.Key_Down):
            self.open_panel()
            event.accept()
            return
        super().keyPressEvent(event)

    def toggle(self) -> None:
        if self.panel.isVisible():
            self.close_panel()
        else:
            self.open_panel()

    def open_panel(self) -> None:
        if not self._seasons:
            return
        self.panel.set_seasons(self._seasons, self._show_poster, self._current)
        self.panel.adjustSize()
        height = min(PANEL_MAX_HEIGHT, max(ROW_HEIGHT, ROW_HEIGHT * len(self._seasons)))
        self.panel.setFixedHeight(min(PANEL_MAX_HEIGHT, height))
        position = self.mapToGlobal(QPoint(0, self.height() + 4))
        self.panel.move(position)
        self.chevron.set_open(True)
        self.panel.load_posters()
        self.panel.fade_in()
        self.panel.setFocus()

    def close_panel(self) -> None:
        self.chevron.set_open(False)
        self.panel.fade_out()

    def _chosen(self, number: int) -> None:
        self._picked = True
        self._current = number
        self._update_header()
        self.chevron.set_open(False)
        self.panel.fade_out()
        self.currentIndexChanged.emit(number)

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.panel.hide()
        super().hideEvent(event)

    def event(self, event) -> bool:
        panel = getattr(self, "panel", None)
        if (
            panel is not None
            and event.type() == QEvent.WindowDeactivate
            and panel.isVisible()
        ):
            self.close_panel()
        return super().event(event)


__all__ = [
    "PANEL_MAX_HEIGHT",
    "PANEL_WIDTH",
    "ROW_HEIGHT",
    "SeasonPanel",
    "SeasonPicker",
    "SeasonRow",
    "SeasonThumb",
]
