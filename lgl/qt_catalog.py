"""Website sources and catalogue import for the Qt (Windows) front end.

The GTK build has a full "Website Sources" dialog with per-source capability
reports.  This is the Qt equivalent: one URL box that inspects the page and
imports whatever catalogue it publishes, plus a preset for the Kryoto catalogue.
Both use the same core as Linux (``sources`` + ``importer`` + ``CatalogStore``),
so an import done here is the same import the Linux build would make.
"""

from __future__ import annotations

import re
import urllib.parse
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .catalog_store import CatalogGame, CatalogStore
from .importer import ImportOptions, import_source
from .qt_widgets import label, run_async

#: The public Kryoto catalogue; also the default for the generic dialog.
KRYOTO_URL = "https://kryo.to"

WEBSITE_NOTE = (
    "Paste the address of a catalogue page and Lumora inspects it with the "
    "adapters it ships (archive.org, Kryoto, RAWG, JSON/HTML catalogues). "
    "Anything it cannot verify is reported instead of guessed. Importing only "
    "writes catalogue records; nothing is downloaded or installed."
)

KRYOTO_NOTE = (
    "Discovery uses the site's public API. A record with source links, and a "
    "verified file, are different states; only a check that returned a file "
    "response enables the download button. Importing is polite: one request at "
    "a time with a small gap."
)


class CatalogDialog(QDialog):
    """Add a website source and import its catalogue."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        title: str = "Website Sources",
        url: str = KRYOTO_URL,
        note: str = WEBSITE_NOTE,
        action: str = "Import catalogue",
    ) -> None:
        super().__init__(parent)
        self.setObjectName("sheet")
        self.setWindowTitle(title)
        self.resize(620, 300)
        self._busy = False

        column = QVBoxLayout(self)
        column.setContentsMargins(24, 22, 24, 16)
        column.setSpacing(10)

        column.addWidget(label(title, "pageTitle"))
        column.addWidget(label(note, "detailNote", wrap=True))

        row = QHBoxLayout()
        row.setSpacing(8)
        self.url = QLineEdit(url)
        self.url.setPlaceholderText("https://example.org/games")
        self.url.setClearButtonEnabled(True)
        row.addWidget(self.url, 1)
        self.import_btn = QPushButton(action)
        self.import_btn.setObjectName("suggested")
        self.import_btn.setCursor(Qt.PointingHandCursor)
        self.import_btn.clicked.connect(self.start_import)
        row.addWidget(self.import_btn)
        column.addLayout(row)

        self.status = label(self._summary(), "detailNote", wrap=True)
        column.addWidget(self.status)
        column.addStretch(1)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        bottom.addWidget(close)
        column.addLayout(bottom)

    # helpers ---------------------------------------------------------------
    def _summary(self) -> str:
        try:
            count = CatalogStore().count_games()
        except Exception:  # noqa: BLE001 - a broken store must not block import
            return "The local catalogue could not be read."
        if count:
            return f"{count:,} catalogue games stored so far."
        return "No catalogue games stored yet."

    def start_import(self) -> None:
        """Inspect the URL and import everything the adapter can enumerate."""
        if self._busy:
            return
        url = self.url.text().strip()
        if not url:
            self.status.setText("Enter the address of a catalogue page first.")
            return
        self._busy = True
        self.import_btn.setEnabled(False)
        self.status.setText(f"Inspecting {url}… this can take a while for a large catalogue.")

        def work() -> tuple[int, str]:
            store = CatalogStore()
            outcome, report = import_source(store, url, options=ImportOptions())
            name = getattr(report, "name", "") or getattr(outcome, "source_name", "")
            return store.count_games(), str(name or "")

        def done(result: object) -> None:
            self._busy = False
            self.import_btn.setEnabled(True)
            count, name = result  # type: ignore[misc]
            source = f" from {name}" if name else ""
            self.status.setText(
                f"Import finished{source}. {count:,} catalogue games are stored."
            )

        def failed(exc: object) -> None:
            self._busy = False
            self.import_btn.setEnabled(True)
            self.status.setText(f"Import failed: {exc}")

        run_async(work, done, failed)


def catalog_identifier(key: str) -> str:
    """A download-queue identifier for an imported record.

    Archive identifiers allow dots, dashes and underscores; a catalogue key is
    ``source:game``, so it is folded into that shape rather than rejected.
    """
    folded = re.sub(r"[^A-Za-z0-9._-]+", "-", str(key or "")).strip("-.")
    return (folded or "catalogue-game")[:120]


def mirror_filename(url: str) -> str:
    """The last path segment of a link, decoded, as a usable file name."""
    path = urllib.parse.urlsplit(str(url or "")).path
    name = urllib.parse.unquote(path.rsplit("/", 1)[-1])
    return name or "download"


class CatalogDownloadDialog(QDialog):
    """Queue the links imported catalogues actually carry.

    Magnets go to the torrent queue, ordinary http(s) links to the download
    manager.  A link is only a link: the dialog shows the status the importer
    recorded and never claims a file was verified when it was not.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        catalog: CatalogStore | None = None,
        manager: object | None = None,
        torrents: object | None = None,
        download_dir: Path | None = None,
        on_queued: object | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("sheet")
        self.setWindowTitle("Download from catalogue")
        self.resize(760, 540)
        self.catalog = catalog or CatalogStore()
        self.manager = manager
        self.torrents = torrents
        self.download_dir = Path(download_dir) if download_dir else Path.home()
        self._on_queued = on_queued if callable(on_queued) else None
        self._games: list[CatalogGame] = []

        column = QVBoxLayout(self)
        column.setContentsMargins(24, 22, 24, 16)
        column.setSpacing(10)
        column.addWidget(label("Download from catalogue", "pageTitle"))
        column.addWidget(
            label(
                "Imported records carry magnets, mirrors, or both. Magnets go to "
                "the torrent queue; http(s) links go to the download queue. A "
                "record with no link cannot be downloaded, whatever it is called.",
                "detailNote", wrap=True,
            )
        )

        body = QHBoxLayout()
        body.setSpacing(14)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._show_details)
        body.addWidget(self.list, 1)
        self.detail = label("", "detailNote", wrap=True)
        self.detail.setMinimumWidth(220)
        body.addWidget(self.detail, 1)
        column.addLayout(body, 1)

        self.status = label("", "detailNote", wrap=True)
        column.addWidget(self.status)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.art_btn = QPushButton("Match artwork…")
        self.art_btn.setCursor(Qt.PointingHandCursor)
        self.art_btn.setToolTip("Pick a provider record for this catalogue game")
        self.art_btn.clicked.connect(self.match_artwork)
        bottom.addWidget(self.art_btn)
        bottom.addStretch(1)
        self.queue_btn = QPushButton("Queue selected")
        self.queue_btn.setObjectName("suggested")
        self.queue_btn.setCursor(Qt.PointingHandCursor)
        self.queue_btn.clicked.connect(self.queue_selected)
        bottom.addWidget(self.queue_btn)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        bottom.addWidget(close)
        column.addLayout(bottom)

        self._fill()

    # helpers ---------------------------------------------------------------
    def _fill(self) -> None:
        self.list.clear()
        self._games = []
        for game in self.catalog.iter_games():
            magnets = self._magnets(game)
            mirrors = self._mirrors(game)
            if not magnets and not mirrors:
                continue
            self._games.append(game)
            item = QListWidgetItem(
                f"{game.title or game.game_id}  ·  {len(magnets)} magnet(s) · "
                f"{len(mirrors)} link(s)"
            )
            item.setToolTip(game.source_url or game.key)
            self.list.addItem(item)
        if not self._games:
            self.status.setText(
                "No imported record carries a download link yet. Import a "
                "catalogue that publishes magnets or mirrors first."
            )
            self.queue_btn.setEnabled(False)
            self.art_btn.setEnabled(False)
        else:
            self.status.setText(f"{len(self._games)} record(s) with links.")
            self.list.setCurrentRow(0)

    def _magnets(self, game: CatalogGame) -> list[object]:
        try:
            return list(self.catalog.magnets_for(game.key))
        except Exception:  # noqa: BLE001 - a store issue is shown, not raised
            return []

    def _mirrors(self, game: CatalogGame) -> list[object]:
        try:
            return [
                record for record in self.catalog.mirrors_for(game.key)
                if str(getattr(record, "url", "")).startswith(("http://", "https://"))
            ]
        except Exception:  # noqa: BLE001
            return []

    def _selected(self) -> CatalogGame | None:
        row = self.list.currentRow()
        if 0 <= row < len(self._games):
            return self._games[row]
        return None

    def _show_details(self, _row: int) -> None:
        game = self._selected()
        if game is None:
            return
        magnets = self._magnets(game)
        mirrors = self._mirrors(game)
        lines = [game.title or game.game_id, ""]
        if game.year:
            lines.append(f"Year: {game.year}")
        if magnets:
            lines.append("Magnets:")
            for record in magnets[:4]:
                lines.append(f"  · {getattr(record, 'display_name', '') or 'unnamed'}")
        if mirrors:
            lines.append("Links:")
            for record in mirrors[:6]:
                status = getattr(record, "status_label", None)
                label_text = status() if callable(status) else str(
                    getattr(record, "check_status", "unchecked")
                )
                lines.append(f"  · {getattr(record, 'filename', '') or mirror_filename(getattr(record, 'url', ''))}: {label_text}")
        self.detail.setText("\n".join(lines))

    # actions ---------------------------------------------------------------
    def queue_selected(self) -> None:
        game = self._selected()
        if game is None:
            return
        added = 0
        errors: list[str] = []
        for record in self._magnets(game):
            uri = str(getattr(record, "uri", "") or "")
            if not uri:
                continue
            if self.torrents is None:
                errors.append("the torrent queue is unavailable")
                continue
            try:
                self.torrents.add(uri, title=game.title)  # type: ignore[attr-defined]
                added += 1
            except Exception as exc:  # noqa: BLE001 - collected, never fatal
                errors.append(str(exc))
        if self.manager is None:
            errors.append("the download queue is unavailable")
        else:
            identifier = catalog_identifier(game.key)
            for record in self._mirrors(game):
                url = str(getattr(record, "url", "") or "")
                name = str(getattr(record, "filename", "") or "") or mirror_filename(url)
                try:
                    self.manager.add(  # type: ignore[attr-defined]
                        identifier=identifier,
                        filename=name,
                        url=url,
                        download_dir=self.download_dir,
                        total_size=getattr(record, "size", 0) or None,
                    )
                    added += 1
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{name}: {exc}")
        if added and self._on_queued is not None:
            self._on_queued(catalog_identifier(game.key), game.title or game.game_id)
        if added:
            self.status.setText(f"Queued {added} link(s) for {game.title or game.game_id}.")
        elif errors:
            self.status.setText(f"Nothing was queued: {errors[0]}")

    def match_artwork(self) -> None:
        game = self._selected()
        if game is None:
            return
        from .qt_artwork import MatchArtworkDialog

        settings = getattr(self.parent(), "_settings", None)
        MatchArtworkDialog(self.catalog, game, settings, self).exec()


__all__ = [
    "CatalogDialog",
    "CatalogDownloadDialog",
    "KRYOTO_URL",
    "KRYOTO_NOTE",
    "WEBSITE_NOTE",
    "catalog_identifier",
    "mirror_filename",
]
