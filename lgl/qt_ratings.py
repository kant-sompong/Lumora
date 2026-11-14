"""The ratings row: IMDb, Rotten Tomatoes and TMDB, drawn from the shared core.

Each badge is a 16 px painted mark and a 15 px semibold number; the row is
hidden when there is nothing to show and never prints an error, because a
missing score is not a broken page.  The icons are drawn here rather than
downloaded, so no third-party logo is bundled.

The fetcher is injected, which is what the tests use: nothing in this module
touches the network on its own.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from . import ratings as ratings_core
from .design_tokens import TOKENS
from .qt_widgets import run_async

#: Badge geometry, from the shared contract.
ICON_SIZE = 16
ICON_GAP = 6
BADGE_GAP = 14
NUMBER_SIZE = 15
#: The contract's own icon colours.  They are not surfaces or text, so they are
#: not design tokens: IMDb yellow, the TMDB teal, and the success green for the
#: Rotten Tomatoes splat (which does come from the token palette).
IMDB_YELLOW = "#f5c518"
IMDB_INK = "#14161c"
TMDB_TEAL = "#01b4e4"


def _palette(theme: str) -> dict[str, object]:
    return TOKENS.get(theme) or TOKENS["dark"]


class RatingBadge(QWidget):
    """One badge: a painted icon, a 6 px gap and the number at 15 px semibold."""

    def __init__(self, kind: str, text: str, tip: str, theme: str = "dark",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kind = kind
        self._text = text
        self._theme = theme
        self.setToolTip(tip)
        self.setFixedHeight(22)
        self.number = QLabel(text, self)
        self.number.setObjectName("ratingNumber")
        self.number.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFixedWidth(ICON_SIZE + ICON_GAP + self._number_width(text))
        self.number.setGeometry(ICON_SIZE + ICON_GAP, 0, self.width(), 22)
        self.number.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.apply_theme(theme)

    @staticmethod
    def _number_width(text: str) -> int:
        from PySide6.QtGui import QFontMetrics

        font = QFont()
        font.setPixelSize(NUMBER_SIZE)
        font.setWeight(QFont.DemiBold)
        return max(18, QFontMetrics(font).horizontalAdvance(text) + 2)

    def apply_theme(self, theme: str) -> None:
        self._theme = theme
        self.number.setStyleSheet(
            f"background: transparent; color: {_palette(theme)['text']};"
            f" font-size: {NUMBER_SIZE}px; font-weight: 600;"
        )
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        area = QRectF(0, (self.height() - ICON_SIZE) / 2, ICON_SIZE, ICON_SIZE)
        palette = _palette(self._theme)
        if self.kind == "imdb":
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(IMDB_YELLOW))
            painter.drawRoundedRect(area, 3, 3)
            font = QFont(self.font())
            font.setPixelSize(7)
            font.setWeight(QFont.Black)
            painter.setFont(font)
            painter.setPen(QColor(IMDB_INK))
            painter.drawText(area, Qt.AlignCenter, "IMDb")
            return
        if self.kind == "rotten":
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(str(palette["success"])))
            splat = QPainterPath()
            splat.addEllipse(QRectF(area.x() + 2, area.y() + 2, area.width() - 4,
                                    area.height() - 4))
            for angle in (25, 70, 115):
                spike = QPainterPath()
                spike.addEllipse(QRectF(area.x() + 1 + angle / 12, area.y(),
                                        5, 6))
                splat = splat.united(spike)
            painter.drawPath(splat)
            return
        # TMDB
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(TMDB_TEAL))
        painter.drawEllipse(area)
        font = QFont(self.font())
        font.setPixelSize(10)
        font.setWeight(QFont.Bold)
        painter.setFont(font)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(area, Qt.AlignCenter, "T")


class RatingsRow(QWidget):
    """The three badges under the title, or nothing at all.

    ``load()`` asks :func:`lgl.ratings.fetch_ratings` on a worker thread with the
    injected fetcher, so a slow OMDb answer cannot stall the page and a missing
    one cannot raise into it.
    """

    loaded = Signal(object)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        theme: str = "dark",
        imdb_id: str = "",
        tmdb_vote: Any = None,
        tmdb_votes: Any = None,
        key: str = "",
        cache_dir: Path | str | None = None,
        fetcher: Callable[[str], Any] | None = None,
    ) -> None:
        super().__init__(parent)
        self._theme = theme
        self.imdb_id = str(imdb_id or "")
        self.tmdb_vote = tmdb_vote
        self.tmdb_votes = tmdb_votes
        self.key = str(key or "")
        self.cache_dir = cache_dir
        self.fetcher = fetcher
        self._badges: list[RatingBadge] = []
        self.box = QHBoxLayout(self)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(BADGE_GAP)
        self.box.addStretch(1)
        self.setVisible(False)

    # -- data ---------------------------------------------------------------
    def set_context(
        self,
        *,
        imdb_id: str,
        tmdb_vote: Any,
        tmdb_votes: Any,
        key: str | None = None,
        fetcher: Callable[[str], Any] | None = None,
    ) -> None:
        self.imdb_id = str(imdb_id or "")
        self.tmdb_vote = tmdb_vote
        self.tmdb_votes = tmdb_votes
        if key is not None:
            self.key = str(key or "")
        if fetcher is not None:
            self.fetcher = fetcher

    def load(self) -> None:
        imdb_id = self.imdb_id
        vote, votes, key = self.tmdb_vote, self.tmdb_votes, self.key
        cache_dir, fetcher = self.cache_dir, self.fetcher

        def work() -> object:
            return ratings_core.fetch_ratings(
                imdb_id, vote, votes, key=key, fetcher=fetcher,
                cache_dir=cache_dir,
            )

        def done(result: object) -> None:
            if isinstance(result, ratings_core.Ratings):
                self._safe_show(result)

        def failed(_exc: object) -> None:
            # Ratings are a nicety; a failure never becomes page text.
            self._safe_show(ratings_core.Ratings())

        run_async(work, done, failed)

    # -- view ---------------------------------------------------------------
    def _safe_show(self, result: ratings_core.Ratings) -> None:
        """A worker can land after the page (and this row) is gone."""
        try:
            self.show_ratings(result)
        except RuntimeError:
            return

    def show_ratings(self, ratings: ratings_core.Ratings) -> None:
        for badge in self._badges:
            badge.setParent(None)
            badge.deleteLater()
        self._badges = []
        order = (
            ("imdb", "IMDb", self._imdb_text(ratings.imdb)),
            ("rotten", "Rotten Tomatoes (critics)",
             f"{ratings.rotten}%" if ratings.rotten is not None else ""),
            ("tmdb", "TMDB user score",
             f"{ratings.tmdb_percent}%" if ratings.tmdb_percent is not None else ""),
        )
        for index, (kind, tip, text) in enumerate(order):
            if not text:
                continue
            badge = RatingBadge(kind, text, tip, self._theme, self)
            self.box.insertWidget(index, badge)
            self._badges.append(badge)
        self.setVisible(bool(self._badges))
        self.loaded.emit(ratings)

    @staticmethod
    def _imdb_text(value: float | None) -> str:
        return f"{value:.1f}" if value is not None else ""

    def apply_theme(self, theme: str) -> None:
        self._theme = theme
        for badge in self._badges:
            badge.apply_theme(theme)


__all__ = [
    "BADGE_GAP",
    "ICON_GAP",
    "ICON_SIZE",
    "IMDB_YELLOW",
    "NUMBER_SIZE",
    "RatingsRow",
    "TMDB_TEAL",
]
