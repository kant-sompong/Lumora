"""The Qt Statistics window: totals, favourite genres, metadata gaps and a
12-week activity heatmap, all computed by :mod:`lgl.stats`."""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from . import stats

ACCENT = QColor(124, 92, 255)


def build_events(
    entries: Iterable[Any],
    viewed: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Turn library adds, last-plays and viewed titles into stat events."""
    events: list[dict[str, Any]] = []
    for entry in entries:
        for field in ("added_at", "last_played"):
            stamp = getattr(entry, field, None)
            if stamp:
                events.append(
                    {"ts": stamp, "kind": "game", "title": getattr(entry, "title", "")}
                )
    for item in viewed:
        stamp = item.get("viewed_at")
        if stamp:
            events.append(
                {
                    "ts": stamp,
                    "kind": str(item.get("kind") or "media"),
                    "title": str(item.get("title") or ""),
                }
            )
    return events


class Heatmap(QWidget):
    """Seven weekday rows by ``N`` week columns, darker with more activity."""

    def __init__(self, grid: Sequence[Sequence[int]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.grid = [list(row) for row in grid]
        self.setMinimumHeight(120)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        weeks = len(self.grid[0]) if self.grid else 0
        if not weeks:
            return
        painter = QPainter(self)
        gap = 3
        size = max(6, min(16, (self.width() - gap * (weeks - 1)) // weeks))
        peak = max((max(row) for row in self.grid), default=0) or 1
        for day, row in enumerate(self.grid):
            for week, count in enumerate(row):
                colour = QColor(ACCENT)
                colour.setAlpha(40 if not count else int(70 + 185 * count / peak))
                painter.fillRect(week * (size + gap), day * (size + gap), size, size, colour)


class StatisticsDialog(QDialog):
    """A read-only summary of the collection and the viewing history."""

    def __init__(
        self,
        entries: Sequence[Any],
        viewed: Sequence[dict[str, Any]],
        parent: QWidget | None = None,
        *,
        embedded: bool = False,
    ) -> None:
        super().__init__(parent)
        self.embedded = embedded
        if embedded:
            self.setWindowFlags(Qt.Widget)
        self.setWindowTitle("Statistics")
        self.resize(520, 420)
        events = build_events(entries, viewed)
        totals = stats.totals(events)
        busiest = stats.busiest_day(events)
        genres = stats.favourite_genres(
            [{"genres": list(getattr(entry, "genres", []) or [])} for entry in entries],
            limit=5,
        )
        coverage = stats.coverage(
            [
                {
                    "poster": getattr(entry, "cover_path", ""),
                    "description": getattr(entry, "description", ""),
                    "year": getattr(entry, "year", ""),
                    "genres": getattr(entry, "genres", []),
                    "rating": None,
                }
                for entry in entries
            ]
        )

        layout = QVBoxLayout(self)
        heading = QLabel("STATISTICS", self)
        heading.setObjectName("detailEyebrow")
        layout.addWidget(heading)

        summary = QLabel(
            f"{coverage['titles']} titles · {totals['events']} events · "
            f"{totals['days']} active days · longest streak {totals['streak']} days",
            self,
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)

        self.busiest = QLabel(
            f"Busiest day: {busiest[0]} ({busiest[1]} events)" if busiest else "No activity yet.",
            self,
        )
        self.busiest.setObjectName("detailNote")
        layout.addWidget(self.busiest)

        self.heatmap = Heatmap(stats.heatmap(events, 12), self)
        layout.addWidget(self.heatmap)

        self.genres = QLabel(
            "Favourite genres: "
            + (" · ".join(f"{name} {count}" for name, count in genres) if genres else "none recorded"),
            self,
        )
        self.genres.setWordWrap(True)
        self.genres.setObjectName("detailNote")
        layout.addWidget(self.genres)

        self.coverage = QLabel(
            "Metadata: "
            f"posters {coverage['poster']}/{coverage['titles']} · "
            f"descriptions {coverage['description']}/{coverage['titles']} · "
            f"years {coverage['year']}/{coverage['titles']} · "
            f"genres {coverage['genres']}/{coverage['titles']}",
            self,
        )
        self.coverage.setWordWrap(True)
        self.coverage.setObjectName("detailNote")
        layout.addWidget(self.coverage)

        if not self.embedded:
            buttons = QHBoxLayout()
            buttons.addStretch(1)
            close = QPushButton("Close", self)
            close.clicked.connect(self.reject)
            buttons.addWidget(close)
            layout.addLayout(buttons)


__all__ = ["Heatmap", "StatisticsDialog", "build_events"]
