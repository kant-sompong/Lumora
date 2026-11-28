"""The Qt release calendar: what airs or releases on which day.

Same data and wording as the GTK window (:mod:`lgl.gtk_calendar`): entries from
:mod:`lgl.release_feed`, grouping and formatting from :mod:`lgl.anime_calendar`.
Fetching runs through :func:`run_async` so the dialog never freezes.
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import anime_calendar as cal
from . import episode_calendar
from . import release_feed
from .qt_widgets import run_async, style_dialog

TZ = "local"


class CalendarDialog(QDialog):
    def __init__(self, settings: Any, parent: QWidget | None = None, *, days: int = 14,
                 autoload: bool = True, embedded: bool = False,
                 search_loader: Callable[[str], Any] | None = None):
        super().__init__(parent)
        self.embedded = embedded
        if embedded:
            self.setWindowFlags(Qt.Widget)
        self.setWindowTitle("Release calendar")
        self.resize(720, 640)
        self.settings = settings
        self._days = days
        self._result = release_feed.FeedResult()
        self._busy = False
        self._search_loader = search_loader
        self._search_result: episode_calendar.CalendarResult | None = None
        self._searching = False

        layout = QVBoxLayout(self)

        style_dialog(self)
        bar = QHBoxLayout()
        self._filter = QComboBox()
        for key, label in release_feed.FILTERS:
            self._filter.addItem(label, key)
        self._filter.currentIndexChanged.connect(lambda _i: self._render())
        bar.addWidget(self._filter)
        bar.addStretch(1)
        self._refresh = QPushButton("Refresh")
        self._refresh.clicked.connect(lambda: self.reload(force=True))
        bar.addWidget(self._refresh)
        layout.addLayout(bar)

        find = QHBoxLayout()
        self._query = QLineEdit()
        self._query.setPlaceholderText("Search a series for its next episodes…")
        self._query.setClearButtonEnabled(True)
        self._query.returnPressed.connect(lambda: self.search())
        find.addWidget(self._query, 1)
        self._find = QPushButton("Search")
        self._find.clicked.connect(lambda: self.search())
        find.addWidget(self._find)
        layout.addLayout(find)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setObjectName("detail-note")
        layout.addWidget(self._status)

        self._body = QWidget()
        self._rows = QVBoxLayout(self._body)
        self._rows.setContentsMargins(0, 0, 8, 0)
        scroller = QScrollArea()
        scroller.setWidgetResizable(True)
        scroller.setWidget(self._body)
        layout.addWidget(scroller, 1)

        if not self.embedded:
            close = QPushButton("Close")
            close.clicked.connect(self.accept)
            foot = QHBoxLayout()
            foot.addStretch(1)
            foot.addWidget(close)
            layout.addLayout(foot)
        if autoload:
            self.reload()

    # -- data -----------------------------------------------------------
    def reload(self, force: bool = False) -> None:
        if self._busy:
            return
        self._busy = True
        self._refresh.setEnabled(False)
        self._status.setText("Loading release dates…")
        getter = release_feed.tmdb_getter(self.settings)
        run_async(
            lambda: release_feed.load_feed(
                release_feed.default_cache_path(), days=self._days, force=force, tmdb_get=getter
            ),
            self._loaded,
            lambda error: self._loaded(release_feed.FeedResult(errors=[f"Could not load the calendar: {error}"])),
        )

    def _loaded(self, result: object) -> None:
        self._busy = False
        self._refresh.setEnabled(True)
        if isinstance(result, release_feed.FeedResult):
            self._result = result
        self._render()
        release_feed.notify_due(self.settings, self._result.entries, notify=_announce)

    def set_result(self, result: release_feed.FeedResult) -> None:
        """Show ``result`` without fetching (used by tests and screenshots)."""
        self._loaded(result)

    def search(self, query: str | None = None) -> None:
        """Look up the next episodes of the series matching ``query``.

        A blank query leaves the search and shows the release feed again.
        """
        text = (self._query.text() if query is None else str(query)).strip()
        if not text:
            self._search_result = None
            self._render()
            return
        if self._searching:
            return
        self._searching = True
        self._find.setEnabled(False)
        self._status.setText(f"Searching for {text}…")
        loader = self._search_loader or self._default_search
        run_async(
            lambda: loader(text),
            self._searched,
            lambda error: self._searched(episode_calendar.CalendarResult(
                query=text, errors=[f"Could not search: {error}"]
            )),
        )

    def _default_search(self, query: str) -> episode_calendar.CalendarResult:
        return episode_calendar.search(
            query, tmdb_get=release_feed.tmdb_getter(self.settings)
        )

    def _searched(self, result: object) -> None:
        self._searching = False
        self._find.setEnabled(True)
        if isinstance(result, episode_calendar.CalendarResult):
            self._search_result = result
        else:
            self._search_result = episode_calendar.CalendarResult(
                errors=["Could not search the episode calendar."]
            )
        self._render()

    # -- drawing --------------------------------------------------------
    def _clear_rows(self) -> None:
        while self._rows.count():
            item = self._rows.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _render(self) -> None:
        self._clear_rows()
        if self._search_result is not None:
            self._render_search(self._search_result)
            return
        followed = cal.follows(self.settings)
        choice = self._filter.currentData() or "all"
        entries = release_feed.select(self._result.entries, choice, followed)
        for heading, group in cal.grouped(entries, TZ):
            title = QLabel(heading)
            title.setObjectName("filter-head")
            title.setStyleSheet("font-weight: 700; margin-top: 10px;")
            self._rows.addWidget(title)
            for entry in group:
                self._rows.addWidget(self._row(entry, entry.key in followed))
        self._rows.addStretch(1)

        notes = list(self._result.errors)
        if self._result.from_cache:
            notes.insert(0, "Showing saved dates (could not reach the servers).")
        if not entries:
            notes.append("Nothing in this view." if self._result.entries else "No release dates yet.")
        self._status.setText(" ".join(notes))

    def _render_search(self, result: episode_calendar.CalendarResult) -> None:
        for group in result.groups:
            title = QLabel(f"{group.title} · {group.source}")
            title.setObjectName("filter-head")
            title.setStyleSheet("font-weight: 700; margin-top: 10px;")
            self._rows.addWidget(title)
            for airing in group.airings:
                self._rows.addWidget(self._airing_row(airing))
        self._rows.addStretch(1)

        notes = list(result.errors)
        if not result.groups:
            notes.append(f"No upcoming episodes found for “{result.query}”.")
        self._status.setText(" ".join(notes))

    def _airing_row(self, airing: episode_calendar.Airing) -> QWidget:
        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 2, 0, 2)
        name = QLabel(airing.label or airing.series)
        name.setWordWrap(True)
        line.addWidget(name, 1)
        when = QLabel(episode_calendar.format_date(airing, TZ))
        when.setObjectName("detail-note")
        line.addWidget(when)
        status = episode_calendar.status_label(airing)
        if status:
            badge = QLabel(status)
            badge.setObjectName("detail-note")
            line.addWidget(badge)
        return row

    def _row(self, entry: cal.Entry, following: bool) -> QWidget:
        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 2, 0, 2)
        name = QLabel(entry.title + (f" — {entry.episode}" if entry.episode else ""))
        name.setWordWrap(True)
        line.addWidget(name, 1)
        kind = {"anime": "Anime", "tv": "TV", "movie": "Movie"}[release_feed.kind_of(entry)]
        when = QLabel(f"{kind} · {cal.format_when(entry, TZ)}")
        when.setObjectName("detail-note")
        line.addWidget(when)
        button = QPushButton("Following" if following else "Follow")
        button.setCheckable(True)
        button.setChecked(following)
        button.toggled.connect(lambda on, key=entry.key, b=button: self._on_follow(b, key, on))
        line.addWidget(button)
        return row

    def _on_follow(self, button: QPushButton, key: str, on: bool) -> None:
        if on:
            if not cal.follow(self.settings, key):
                button.setChecked(False)
                self._status.setText(f"You can follow up to {cal.MAX_FOLLOWS} titles.")
                return
            button.setText("Following")
        else:
            cal.unfollow(self.settings, key)
            button.setText("Follow")


def _announce(settings: Any, title: str) -> bool:
    from . import notifications

    return notifications.episode_released(settings, title)


def show_calendar(settings: Any, parent: QWidget | None = None) -> CalendarDialog:
    dialog = CalendarDialog(settings, parent)
    dialog.exec()
    return dialog


__all__ = ["CalendarDialog", "show_calendar"]
