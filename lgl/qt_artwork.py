"""Artwork matching for imported catalogue records, in the Qt frontend.

The GTK build syncs posters from a Settings action and offers a per-game picker
when a name is ambiguous.  This is the Qt equivalent: one worker that fills every
record whose poster could never load, and one dialog that lets a person choose a
candidate for a single game.

Matching rules live in ``lgl.artwork`` and are shared by both frontends: a
candidate is applied automatically only when exactly one provider result has the
same cleaned title (and year, when both are known).  Everything else is left for
the picker, because a batch is not a place to guess which game a name means.
"""

from __future__ import annotations

from typing import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .artwork import (
    ArtworkSearchers,
    Candidate,
    apply_candidate,
    find_candidates,
    needs_artwork,
    rank_candidates,
    remembered_miss,
)
from .catalog_store import CatalogGame, CatalogStore
from .qt_widgets import label, run_async
from .sources import RawgSource
from .steamart import SteamArtSource


def artwork_searchers(settings: object | None) -> ArtworkSearchers:
    """RAWG when a key is stored, then the keyless Steam store search."""
    key = ""
    if settings is not None:
        try:
            key = settings.api_key("rawg")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - a missing keyring must not stop a sync
            key = ""
    providers: list[object] = [SteamArtSource()]
    if key:
        providers.insert(0, RawgSource(key))
    return ArtworkSearchers(providers)


def records_needing_artwork(
    catalog: CatalogStore, *, force: bool = False
) -> list[CatalogGame]:
    """Imported records whose poster cannot be shown, newest first."""
    records = [
        record
        for record in catalog.iter_games()
        if needs_artwork(catalog, record) and (force or not remembered_miss(record))
    ]
    return records


def sync_catalog_artwork(
    catalog: CatalogStore,
    settings: object | None = None,
    *,
    force: bool = False,
) -> dict[str, int]:
    """Fill posters for every record that needs one; return what happened.

    One request per record through :class:`ArtworkSearchers`, which stops asking a
    provider that refused outright (a spent RAWG quota) and falls back to the
    keyless Steam search.  A "no match" is remembered by the core for a week, so
    a second run does not re-ask about the same titles unless *force* is set.
    """
    summary = {
        "added": 0, "unchanged": 0, "missing": 0, "failed": 0,
        "skipped": 0, "total": 0,
    }
    records = records_needing_artwork(catalog, force=force)
    summary["total"] = len(records)
    if not records:
        return summary
    searchers = artwork_searchers(settings)
    for record in records:
        title = (record.title or record.game_id or "").strip()
        if not title:
            summary["missing"] += 1
            continue
        if searchers.exhausted:
            summary["skipped"] += 1
            continue
        try:
            chosen = searchers.choose(title, record.year, limit=20)
        except Exception:  # noqa: BLE001 - one bad record is not the run
            summary["failed"] += 1
            continue
        if chosen is None:
            summary["skipped" if searchers.exhausted else "missing"] += 1
            continue
        try:
            filled = apply_candidate(catalog, record.key, chosen)
        except Exception:  # noqa: BLE001
            summary["failed"] += 1
            continue
        summary["added" if filled else "unchanged"] += 1
    save = getattr(catalog, "save", None)
    if callable(save):
        try:
            save()
        except OSError:
            pass
    return summary


class MatchArtworkDialog(QDialog):
    """Search a provider for one record and apply the result a person picked."""

    def __init__(
        self,
        catalog: CatalogStore,
        game: CatalogGame,
        settings: object | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("sheet")
        self.setWindowTitle("Match artwork")
        self.resize(640, 460)
        self.catalog = catalog
        self.game = game
        self.settings = settings
        self._candidates: list[Candidate] = []

        column = QVBoxLayout(self)
        column.setContentsMargins(22, 20, 22, 16)
        column.setSpacing(8)
        column.addWidget(label(game.title or game.game_id, "pageTitle"))
        column.addWidget(
            label(
                "Pick the provider record that is this game. Nothing is applied "
                "until you press Use selected.",
                "detailNote",
                wrap=True,
            )
        )

        row = QHBoxLayout()
        row.setSpacing(8)
        self.query = QLineEdit(game.title or game.game_id)
        self.query.setClearButtonEnabled(True)
        row.addWidget(self.query, 1)
        self.search_btn = QPushButton("Search")
        self.search_btn.setObjectName("suggested")
        self.search_btn.clicked.connect(self.search)
        row.addWidget(self.search_btn)
        column.addLayout(row)

        self.results = QListWidget()
        column.addWidget(self.results, 1)
        self.status = label("", "detailNote", wrap=True)
        column.addWidget(self.status)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.use_btn = QPushButton("Use selected")
        self.use_btn.setObjectName("suggested")
        self.use_btn.setEnabled(False)
        self.use_btn.clicked.connect(self.apply)
        bottom.addWidget(self.use_btn)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        bottom.addWidget(close)
        column.addLayout(bottom)
        self.results.currentRowChanged.connect(
            lambda index: self.use_btn.setEnabled(index >= 0)
        )

    # actions ---------------------------------------------------------------
    def search(self) -> None:
        title = self.query.text().strip()
        if not title:
            self.status.setText("Enter a title to search for.")
            return
        self.search_btn.setEnabled(False)
        self.status.setText("Searching…")
        searchers = artwork_searchers(self.settings)
        providers = list(getattr(searchers, "providers", []))

        def work() -> list[Candidate]:
            found: list[Candidate] = []
            for provider in providers:
                found.extend(find_candidates(provider, title, limit=10))
                if found:
                    break
            return rank_candidates(found, title, self.game.year) if found else []

        def done(result: object) -> None:
            self.search_btn.setEnabled(True)
            candidates = list(result) if isinstance(result, list) else []
            self._candidates = candidates
            self.results.clear()
            for candidate in candidates:
                item = QListWidgetItem(f"{candidate.describe()}  ·  {candidate.provider}")
                item.setToolTip(candidate.page_url or candidate.title)
                self.results.addItem(item)
            if not candidates:
                self.status.setText(
                    "No provider result matched. Try the undecorated title."
                )
            else:
                self.status.setText(
                    f"{len(candidates)} candidate(s). Check the year and developer "
                    "before applying."
                )
                self.results.setCurrentRow(0)

        run_async(work, done, lambda exc: self._failed(exc))

    def _failed(self, exc: object) -> None:
        self.search_btn.setEnabled(True)
        self.status.setText(f"Search failed: {exc}")

    def apply(self) -> None:
        row = self.results.currentRow()
        if row < 0 or row >= len(self._candidates):
            return
        candidate = self._candidates[row]
        try:
            filled = apply_candidate(
                self.catalog, self.game.key, candidate, replace_cover=True
            )
        except Exception as exc:  # noqa: BLE001 - reported in the dialog
            self.status.setText(f"Could not apply that result: {exc}")
            return
        save = getattr(self.catalog, "save", None)
        if callable(save):
            try:
                save()
            except OSError:
                pass
        added = ", ".join(filled) if filled else "no empty fields"
        self.status.setText(f"Applied {candidate.title}: {added}.")
        self.use_btn.setEnabled(False)


__all__ = [
    "MatchArtworkDialog",
    "artwork_searchers",
    "records_needing_artwork",
    "sync_catalog_artwork",
]
