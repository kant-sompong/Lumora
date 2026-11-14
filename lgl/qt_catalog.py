"""Website sources and catalogue import for the Qt (Windows) front end.

The GTK build has a full "Website Sources" dialog with per-source capability
reports.  This is the Qt equivalent: one URL box that inspects the page and
imports whatever catalogue it publishes, plus a preset for the Kryoto catalogue.
Both use the same core as Linux (``sources`` + ``importer`` + ``CatalogStore``),
so an import done here is the same import the Linux build would make.
"""

from __future__ import annotations

import re
import threading
import time
import urllib.parse
from collections.abc import Callable, Sequence
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from . import kryoto
from . import platform_support
from .catalog_store import CatalogGame, CatalogStore
from .importer import CatalogImporter, ImportOptions, ImportOutcome, import_source
from .qt_catalog_links import (
    CatalogGamePane,
    http_links,
    queue_mirror,
)
from .qt_widgets import label, run_async, scroll_page, style_dialog

#: The public Kryoto catalogue; also the default for the generic dialog.
KRYOTO_URL = "https://kryo.to"

WEBSITE_NOTE = (
    "Paste the address of a catalogue page and Lumora inspects it with the "
    "adapters it ships (archive.org, Kryoto, RAWG, JSON/HTML catalogues). "
    "Anything it cannot verify is reported instead of guessed. Importing only "
    "writes catalogue records; nothing is downloaded or installed."
)

KRYOTO_NOTE = (
    "Discovery uses the site's public API. The import then reads each record's "
    "details and download links, so it takes longer than the plain listing. A "
    "record with source links, and a verified file, are different states; only a "
    "check that returned a file response calls a link verified. Importing keeps "
    "a small, bounded number of requests in flight."
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
        catalog: CatalogStore | None = None,
        on_imported: Callable[[int], None] | None = None,
        enrich: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("sheet")
        self.setWindowTitle(title)
        self.resize(620, 300)
        self._busy = False
        # Import into the store the rest of the window reads.  A fresh
        # ``CatalogStore()`` has no path, so it saves nothing and every record
        # it collected was lost the moment the import finished.
        self.catalog = (
            catalog if catalog is not None
            else CatalogStore(platform_support.data_dir() / "catalog.json")
        )
        self._on_imported = on_imported if callable(on_imported) else None
        #: A catalogue whose download links live on each record (Kryoto) needs
        #: the enrichment pass, or every row is imported without a single link.
        self._enrich = bool(enrich)

        column = QVBoxLayout(self)

        column.addWidget(label(title, "pageTitle"))
        column.addWidget(label(note, "detailNote", wrap=True))

        row = QHBoxLayout()
        row.setSpacing(8)
        self.url = QLineEdit(url)
        self.url.setPlaceholderText("https://example.org/games")
        self.url.setClearButtonEnabled(True)
        row.addWidget(self.url, 1)
        style_dialog(self, first=self.url)
        self.import_btn = QPushButton(action)
        self.import_btn.setObjectName("suggested")
        self.import_btn.setCursor(Qt.PointingHandCursor)
        self.import_btn.setDefault(True)
        self.import_btn.setAutoDefault(True)
        self.import_btn.clicked.connect(self.start_import)
        row.addWidget(self.import_btn)
        column.addLayout(row)

        self.status = label(self._summary(), "detailNote", wrap=True)
        column.addWidget(self.status)
        # A catalogue can take minutes: the importer publishes a page/record
        # count as it goes, and this bar shows it instead of one frozen line.
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        self.progress.setAccessibleName("Catalogue import progress")
        self.progress.setVisible(False)
        column.addWidget(self.progress)
        self._progress_lock = threading.Lock()
        self._progress_snapshot: dict[str, object] = {}
        self._poll = QTimer(self)
        self._poll.setInterval(200)
        self._poll.timeout.connect(self._show_progress)
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
            count = self.catalog.count_games()
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
        with self._progress_lock:
            self._progress_snapshot = {}
        self.progress.setRange(0, 0)
        self.progress.setVisible(True)
        self.status.setText(f"Inspecting {url}… this can take a while for a large catalogue.")
        self._poll.start()

        def work() -> tuple[int, str, str]:
            store = self.catalog
            options = (
                ImportOptions(enrich=True, workers=4) if self._enrich
                else ImportOptions()
            )
            outcome, report = import_source(
                store, url, options=options, progress=self._record_progress
            )
            name = getattr(report, "name", "") or getattr(outcome, "source_name", "")
            problem = ""
            try:
                if not store.save():
                    problem = "the catalogue has no file to save to"
            except OSError as exc:
                problem = str(exc)
            return store.count_games(), str(name or ""), problem

        def done(result: object) -> None:
            self._finish_progress()
            self._busy = False
            self.import_btn.setEnabled(True)
            count, name, problem = result  # type: ignore[misc]
            source = f" from {name}" if name else ""
            if problem:
                self.status.setText(
                    f"Import finished{source}, but the catalogue could not be "
                    f"saved: {problem}"
                )
                return
            self.status.setText(
                f"Import finished{source}. {count:,} catalogue games are stored."
            )
            if count and self._on_imported is not None:
                self._on_imported(count)

        def failed(exc: object) -> None:
            self._finish_progress()
            self._busy = False
            self.import_btn.setEnabled(True)
            self.status.setText(f"Import failed: {exc}")

        run_async(work, done, failed)

    # progress --------------------------------------------------------------
    def _record_progress(self, job: object) -> None:
        """Keep the newest counters; the importer calls this from its thread."""
        snapshot = {
            "phase": str(getattr(job, "phase", "") or ""),
            "pages": int(getattr(job, "pages_processed", 0) or 0),
            "imported": int(getattr(job, "games_imported", 0) or 0),
            "discovered": int(getattr(job, "games_discovered", 0) or 0),
            # The enrichment pass is the long one: it needs a count of its own,
            # or the window sits on "Fetching details…" for minutes.
            "enrich_done": int(getattr(job, "enrich_done", 0) or 0),
            "enrich_total": int(getattr(job, "enrich_total", 0) or 0),
            "enriched": int(getattr(job, "games_enriched", 0) or 0),
            "links": int(getattr(job, "mirrors_found", 0) or 0),
            "failures": int(getattr(job, "enrich_failures", 0) or 0),
            "status": str(getattr(job, "status", "") or ""),
        }
        with self._progress_lock:
            self._progress_snapshot = snapshot

    def _show_progress(self) -> None:
        """Paint the counters on the UI thread, at most five times a second."""
        if not self._busy:
            return
        with self._progress_lock:
            snapshot = dict(self._progress_snapshot)
        if not snapshot:
            return
        if snapshot.get("phase") == "enrichment":
            self._show_enrichment(snapshot)
            return
        pages = int(snapshot.get("pages", 0) or 0)
        imported = int(snapshot.get("imported", 0) or 0)
        discovered = int(snapshot.get("discovered", 0) or 0)
        self.progress.setRange(0, 0)
        bits = ["Reading the catalogue…", f"page {pages:,}"] if pages else ["Reading the catalogue…"]
        if imported:
            bits.append(f"{imported:,} games imported")
        elif discovered:
            bits.append(f"{discovered:,} found")
        self.status.setText(" · ".join(bits))

    def _show_enrichment(self, snapshot: dict[str, object]) -> None:
        """The second pass: a real count, and a bar that moves with it."""
        done = int(snapshot.get("enrich_done", 0) or 0)
        total = int(snapshot.get("enrich_total", 0) or 0)
        enriched = int(snapshot.get("enriched", 0) or 0)
        links = int(snapshot.get("links", 0) or 0)
        failures = int(snapshot.get("failures", 0) or 0)
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(min(done, total))
            head = f"Fetching details {done:,} / {total:,} ({int(done * 100 / total)}%)"
        else:
            self.progress.setRange(0, 0)
            head = "Fetching details…"
        bits = [head]
        if enriched:
            bits.append(f"{enriched:,} records read")
        if links:
            bits.append(f"{links:,} download link(s)")
        if failures:
            bits.append(f"{failures:,} failed")
        self.status.setText(" · ".join(bits))

    def _finish_progress(self) -> None:
        self._poll.stop()
        self.progress.setVisible(False)


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
        style_dialog(self, first=None)
        column.addWidget(label("Download from catalogue", "pageTitle"))
        column.addWidget(
            label(
                "Imported records carry magnets, mirrors, or both. Magnets go to "
                "the torrent queue; http(s) links go to the download queue, but "
                "only after “Check link” has seen the server answer with a file — "
                "a page is opened in the browser, never downloaded.",
                "detailNote", wrap=True,
            )
        )

        body = QHBoxLayout()
        body.setSpacing(14)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._show_details)
        body.addWidget(self.list, 1)
        side = QVBoxLayout()
        side.setSpacing(8)
        self.detail = label("", "detailNote", wrap=True)
        self.detail.setMinimumWidth(220)
        side.addWidget(self.detail)
        #: One row per recorded http(s) link, so a link can be opened in the
        #: browser or queued on its own instead of only in bulk.
        self.links = QListWidget()
        self.links.setAccessibleName("Download links")
        self.links.setToolTip("Double-click a link to open it in the browser")
        self.links.itemDoubleClicked.connect(lambda _item: self.open_link())
        self.links.currentRowChanged.connect(lambda _row: self._update_link_buttons())
        side.addWidget(self.links, 1)
        link_row = QHBoxLayout()
        link_row.setSpacing(8)
        self.open_link_btn = QPushButton("Open in browser")
        self.open_link_btn.setCursor(Qt.PointingHandCursor)
        self.open_link_btn.clicked.connect(self.open_link)
        link_row.addWidget(self.open_link_btn)
        self.copy_link_btn = QPushButton("Copy link")
        self.copy_link_btn.setCursor(Qt.PointingHandCursor)
        self.copy_link_btn.clicked.connect(self.copy_link)
        link_row.addWidget(self.copy_link_btn)
        self.check_link_btn = QPushButton("Check link")
        self.check_link_btn.setCursor(Qt.PointingHandCursor)
        self.check_link_btn.setToolTip(
            "Ask the server about this link first; a page is never downloaded"
        )
        self.check_link_btn.clicked.connect(self.check_link)
        link_row.addWidget(self.check_link_btn)
        self.download_link_btn = QPushButton("Download link")
        self.download_link_btn.setObjectName("suggested")
        self.download_link_btn.setCursor(Qt.PointingHandCursor)
        self.download_link_btn.setToolTip(
            "Check this link if needed, then queue it only when the server "
            "answering with a file"
        )
        self.download_link_btn.clicked.connect(self.download_link)
        link_row.addWidget(self.download_link_btn)
        link_row.addStretch(1)
        side.addLayout(link_row)
        body.addLayout(side, 1)
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
        self.queue_btn = QPushButton("Download all links")
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
            self._fill_links([])
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
            lines.append(f"{len(mirrors)} download link(s):")
        self.detail.setText("\n".join(lines))
        self._fill_links(mirrors)

    def _mirror_status(self, record: object) -> str:
        status = getattr(record, "status_label", None)
        if callable(status):
            return str(status())
        return str(getattr(record, "check_status", "unchecked") or "unchecked")

    def _fill_links(self, mirrors: list[object]) -> None:
        """List this record's links with their addresses, so they can be visited."""
        self.links.clear()
        for record in mirrors:
            url = str(getattr(record, "url", "") or "")
            name = str(getattr(record, "filename", "") or "") or mirror_filename(url)
            item = QListWidgetItem(f"{name}  ·  {self._mirror_status(record)}\n{url}")
            item.setData(Qt.UserRole, url)
            item.setToolTip(url)
            self.links.addItem(item)
        if not mirrors:
            empty = QListWidgetItem("This record carries no download link.")
            empty.setFlags(Qt.NoItemFlags)
            self.links.addItem(empty)
        else:
            self.links.setCurrentRow(0)
        self._update_link_buttons()

    def _update_link_buttons(self) -> None:
        has_link = bool(self._selected_link())
        for button in (
            self.open_link_btn, self.copy_link_btn, self.check_link_btn,
            self.download_link_btn,
        ):
            button.setEnabled(has_link)

    def _selected_link(self) -> str:
        item = self.links.currentItem()
        if item is None:
            return ""
        return str(item.data(Qt.UserRole) or "")

    # actions ---------------------------------------------------------------
    def open_link(self) -> None:
        """Open the selected link in the user's browser."""
        url = self._selected_link()
        if not url:
            return
        if not platform_support.open_url(url):
            self.status.setText(f"The browser could not be opened for {url}")

    def copy_link(self) -> None:
        url = self._selected_link()
        if not url:
            return
        QApplication.clipboard().setText(url)
        self.status.setText("Link copied.")

    def _picked(self) -> tuple[CatalogGame, object, int] | tuple[None, None, int]:
        """The selected game and the link row the user highlighted."""
        game = self._selected()
        row = self.links.currentRow()
        mirrors = self._mirrors(game) if game is not None else []
        if game is None or not (0 <= row < len(mirrors)):
            return None, None, row
        return game, mirrors[row], row

    def check_link(self, *, then_queue: bool = False) -> None:
        """Probe the selected link and keep what the server actually answered.

        This is the Linux rule: a link's shape proves nothing, so the answer is
        recorded on the record and, when *then_queue* is set, only a response
        that returned a file is handed to the download queue.
        """
        game, record, row = self._picked()
        if game is None or record is None:
            return
        url = str(getattr(record, "url", "") or "")
        if not url:
            return
        allow_external = not kryoto.host_allowed(url, kryoto.KRYOTO_HOSTS)
        host = str(getattr(record, "host", "") or "") or mirror_filename(url)
        self.status.setText(f"Checking {host}…")

        def work() -> object:
            return kryoto.check_link(url, allow_external=allow_external)

        def done(result: object) -> None:
            if not isinstance(result, kryoto.LinkCheck):
                self.status.setText("The link check returned nothing usable.")
                return
            record.last_checked = time.time()
            record.check_status = result.status
            record.url_kind = result.kind or getattr(record, "url_kind", "")
            record.content_type = result.content_type
            record.notes = tuple(result.notes)
            if result.filename and not getattr(record, "filename", ""):
                record.filename = result.filename
            if result.size and not getattr(record, "size", 0):
                record.size = result.size
            try:
                self.catalog.save()
            except OSError as exc:
                self.status.setText(f"Checked, but the catalogue could not be saved: {exc}")
            self._show_details(row)
            self.links.setCurrentRow(row)
            if then_queue:
                if kryoto.is_downloadable_link(record.check_status, record.url_kind):
                    self._queue_and_report(game, record)
                else:
                    self.status.setText(
                        f"Not queued: the server answered as “{record.status_label()}”. "
                        "Open it in the browser instead."
                    )
                return
            self.status.setText(f"Check: {record.status_label()}.")

        def failed(exc: object) -> None:
            self.status.setText(f"The link check failed: {exc}")

        run_async(work, done, failed)

    def _queue_and_report(self, game: CatalogGame, record: object) -> None:
        error = self._queue_mirror(game, record)
        if error:
            self.status.setText(f"Nothing was queued: {error}")
            return
        name = str(getattr(record, "filename", "") or "") or mirror_filename(
            str(getattr(record, "url", "") or "")
        )
        self.status.setText(f"Queued {name}.")
        if self._on_queued is not None:
            self._on_queued(catalog_identifier(game.key), game.title or game.game_id)

    def download_link(self) -> None:
        """Queue the picked link, checking it first unless it is already proven."""
        game, record, _row = self._picked()
        if game is None or record is None:
            return
        if kryoto.is_downloadable_link(
            getattr(record, "check_status", ""), getattr(record, "url_kind", "")
        ):
            self._queue_and_report(game, record)
            return
        self.check_link(then_queue=True)

    def _queue_mirror(self, game: CatalogGame, record: object) -> str:
        """Queue one http(s) link through the shared catalogue helper."""
        return queue_mirror(
            self.manager, self.download_dir, catalog_identifier(game.key), record
        )

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
        unchecked = 0
        for record in self._mirrors(game):
            if not kryoto.is_downloadable_link(
                getattr(record, "check_status", ""), getattr(record, "url_kind", "")
            ):
                # A page URL is not a download: it has to be proven first.
                unchecked += 1
                continue
            error = self._queue_mirror(game, record)
            if error:
                errors.append(error)
            else:
                added += 1
        if added and self._on_queued is not None:
            self._on_queued(catalog_identifier(game.key), game.title or game.game_id)
        if added:
            text = f"Queued {added} link(s) for {game.title or game.game_id}."
            if unchecked:
                text += f" {unchecked} still need “Check link” first."
            self.status.setText(text)
        elif errors:
            self.status.setText(f"Nothing was queued: {errors[0]}")
        elif unchecked:
            self.status.setText(
                f"{unchecked} link(s) are recorded but none is proven yet: "
                "check one (or open it in the browser) first."
            )

    def match_artwork(self) -> None:
        game = self._selected()
        if game is None:
            return
        from .qt_artwork import MatchArtworkDialog

        settings = getattr(self.parent(), "_settings", None)
        MatchArtworkDialog(self.catalog, game, settings, self).exec()


def _stamp(value: object) -> str:
    """A time the catalogue remembers as a short, local date and time."""
    try:
        moment = float(value or 0)
    except (TypeError, ValueError):
        return ""
    if moment <= 0:
        return ""
    return time.strftime("%d %b %Y, %H:%M", time.localtime(moment))


KRYOTO_HINT = (
    "Discovery uses the site's public API only. A recorded game, a record with "
    "source links, and a verified file are different states; only a check that "
    "returned a file response enables the download button."
)

#: The filter and sort vocabularies, exactly as the Linux dialog offers them.
KRYOTO_FILTERS = (
    ("all", "All records"),
    ("with", "With links"),
    ("without", "No links"),
    ("verified", "Verified files"),
    ("unchecked", "Unchecked links"),
)
KRYOTO_SORTS = (
    ("title", "Title"), ("year", "Year"), ("provider", "Provider"),
    ("links", "Links"), ("status", "Status"),
)


class KryotoCatalogDialog(QDialog):
    """Browse, refresh and check the Kryoto catalogue, like the Linux dialog.

    The dialog never equates "listed" with "downloadable": a record can be in
    the catalogue with no links, with unchecked links, or with a link a server
    check actually confirmed as a file.  Catalog work runs in a worker thread
    and is checkpointed by the shared importer, so closing cancels at the next
    page boundary and Resume continues from the saved cursor.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        catalog: CatalogStore | None = None,
        manager: object | None = None,
        torrents: object | None = None,
        download_dir: Path | None = None,
        on_imported: Callable[[int], None] | None = None,
        on_queued: Callable[[str, str], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("sheet")
        self.setWindowTitle("Kryoto Catalog")
        self.resize(1120, 760)
        self.store = (
            catalog if catalog is not None
            else CatalogStore(platform_support.data_dir() / "catalog.json")
        )
        self.manager = manager
        self.torrents = torrents
        self.download_dir = Path(download_dir) if download_dir else Path.home()
        self._on_imported = on_imported if callable(on_imported) else None
        self._on_queued = on_queued if callable(on_queued) else None
        self._cancel = threading.Event()
        self._busy = False
        self._games: list[CatalogGame] = []
        self._shown: list[CatalogGame] = []
        self._selected_key = ""
        self._progress_lock = threading.Lock()
        self._snapshot: dict[str, object] = {}

        column = QVBoxLayout(self)
        style_dialog(self, first=None)
        column.addWidget(label("Kryoto catalog", "pageTitle"))
        column.addWidget(label(KRYOTO_HINT, "detailNote", wrap=True))

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.refresh_btn = QPushButton("Refresh Kryoto Catalog")
        self.refresh_btn.setObjectName("suggested")
        self.refresh_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_btn.setToolTip(
            "Walk every catalog page with offset pagination, then fetch details "
            "and source links for records that have not been read yet"
        )
        self.refresh_btn.clicked.connect(lambda _=False: self.start_refresh(False))
        actions.addWidget(self.refresh_btn)
        self.resume_btn = QPushButton("Resume")
        self.resume_btn.setCursor(Qt.PointingHandCursor)
        self.resume_btn.setToolTip("Continue the last interrupted Kryoto scan")
        self.resume_btn.clicked.connect(lambda _=False: self.start_refresh(True))
        actions.addWidget(self.resume_btn)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setCursor(Qt.PointingHandCursor)
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel_refresh)
        actions.addWidget(self.cancel_btn)
        self.check_btn = QPushButton("Check Kryoto links")
        self.check_btn.setCursor(Qt.PointingHandCursor)
        self.check_btn.setToolTip(
            "Probe Kryoto-hosted links only. External mirrors are left for the "
            "per-link Check action; nothing is downloaded."
        )
        self.check_btn.clicked.connect(self.check_kryoto_links)
        actions.addWidget(self.check_btn)
        actions.addStretch(1)
        self.sources_btn = QPushButton("Import another source…")
        self.sources_btn.setCursor(Qt.PointingHandCursor)
        self.sources_btn.clicked.connect(self.open_other_sources)
        actions.addWidget(self.sources_btn)
        column.addLayout(actions)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        self.progress.setAccessibleName("Kryoto catalog progress")
        self.progress.setVisible(False)
        column.addWidget(self.progress)
        self.status = label("Ready.", "detailNote", wrap=True)
        column.addWidget(self.status)

        stats = QFrame()
        stats.setObjectName("catalogStats")
        grid = QGridLayout(stats)
        grid.setContentsMargins(12, 8, 12, 8)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)
        self.stats: dict[str, object] = {}
        for index, name in enumerate(
            (
                "Discovered", "Metadata", "With links", "No links",
                "Links listed", "Checked", "Verified files", "Failed requests",
                "Last refresh", "Coverage",
            )
        ):
            caption = label(name, "fact-name")
            value = label("—", "kvVal", wrap=True)
            grid.addWidget(caption, (index % 2) * 5, (index // 2) * 2)
            grid.addWidget(value, (index % 2) * 5, (index // 2) * 2 + 1)
            self.stats[name] = value
        column.addWidget(stats)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search the imported catalogue")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _text: self.refresh_view())
        toolbar.addWidget(self.search, 1)
        self.filter_combo = QComboBox()
        for key, text in KRYOTO_FILTERS:
            self.filter_combo.addItem(text, key)
        self.filter_combo.currentIndexChanged.connect(lambda _i: self.refresh_view())
        toolbar.addWidget(self.filter_combo)
        self.sort_combo = QComboBox()
        for key, text in KRYOTO_SORTS:
            self.sort_combo.addItem(f"Sort: {text}", key)
        self.sort_combo.currentIndexChanged.connect(lambda _i: self.refresh_view())
        toolbar.addWidget(self.sort_combo)
        column.addLayout(toolbar)

        split = QSplitter(Qt.Horizontal)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._show_selected)
        split.addWidget(self.list)
        pane_host = QWidget()
        pane_column = QVBoxLayout(pane_host)
        pane_column.setContentsMargins(0, 0, 0, 0)
        pane_column.setSpacing(0)
        scroll, _body, inner = scroll_page()
        pane_column.addWidget(scroll)
        self.pane = CatalogGamePane(
            self.store, self.manager, self.torrents, self.download_dir,
            on_message=self._message, on_queued=self._queued, parent=pane_host,
        )
        self.pane.changed.connect(self.refresh_view)
        inner.addWidget(self.pane)
        inner.addStretch(1)
        split.addWidget(pane_host)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 4)
        column.addWidget(split, 1)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        bottom.addWidget(close)
        column.addLayout(bottom)

        self._poll = QTimer(self)
        self._poll.setInterval(200)
        self._poll.timeout.connect(self._paint_progress)
        self.refresh_view()

    # state -----------------------------------------------------------------
    def _message(self, text: str, error: bool = False) -> None:
        self.status.setText(text)
        if error:
            self.status.setToolTip(text)

    def _queued(self, identifier: str, title: str) -> None:
        if self._on_queued is not None:
            self._on_queued(identifier, title)

    def _select_record(self) -> CatalogGame | None:
        row = self.list.currentRow()
        if 0 <= row < len(self._shown):
            return self._shown[row]
        return None

    def _show_selected(self, _row: int) -> None:
        record = self._select_record()
        if record is None:
            return
        self._selected_key = record.key
        self.pane.show_record(record, self._source_name(record.source_id))

    def _source_name(self, source_id: str) -> str:
        try:
            source = self.store.get_source(source_id)
        except Exception:  # noqa: BLE001
            source = None
        if source is not None and getattr(source, "name", ""):
            return str(source.name)
        return source_id or "unknown"

    # view ------------------------------------------------------------------
    def refresh_view(self) -> None:
        """Re-read the store and repaint stats, list and detail pane."""
        self._paint_stats()
        try:
            self._games = list(self.store.iter_games("kryoto")) or list(self.store.iter_games())
        except Exception:  # noqa: BLE001 - a store issue is reported, not raised
            self._games = []
        self._shown = self._filtered(self._games)
        self._fill_list()
        self.resume_btn.setEnabled(not self._busy and self._has_resumable())

    def _paint_stats(self) -> None:
        try:
            summary = self.store.source_summary("kryoto")
        except Exception:  # noqa: BLE001
            summary = {}
        values = {
            "Discovered": summary.get("games_discovered"),
            "Metadata": summary.get("metadata_retrieved"),
            "With links": summary.get("games_with_links"),
            "No links": summary.get("games_without_links"),
            "Links listed": summary.get("links_listed"),
            "Checked": summary.get("links_checked"),
            "Verified files": summary.get("links_verified"),
            "Failed requests": summary.get("failed_requests"),
            "Last refresh": _stamp(summary.get("last_refresh")),
            "Coverage": "complete" if summary.get("pagination_complete") else "partial",
        }
        for name, value in values.items():
            widget = self.stats.get(name)
            if widget is not None:
                widget.setText("—" if value in (None, "") else f"{value:,}" if isinstance(value, int) else str(value))

    def _has_resumable(self) -> bool:
        job = self.store.latest_job("kryoto")
        return bool(job and getattr(job, "resumable", False))

    def _filtered(self, games: Sequence[CatalogGame]) -> list[CatalogGame]:
        needle = self.search.text().strip().casefold()
        mode = str(self.filter_combo.currentData() or "all")
        rows: list[CatalogGame] = []
        for game in games:
            links = http_links(self.store, game.key)
            if needle and needle not in (game.title or "").casefold()                     and needle not in (game.developer or "").casefold():
                continue
            if mode == "with" and not links:
                continue
            if mode == "without" and links:
                continue
            if mode == "verified" and not any(
                str(getattr(link, "check_status", "")) == "verified" for link in links
            ):
                continue
            if mode == "unchecked" and not any(
                str(getattr(link, "check_status", "unchecked")) == "unchecked" for link in links
            ):
                continue
            rows.append(game)
        key = str(self.sort_combo.currentData() or "title")
        if key == "year":
            rows.sort(key=lambda game: (str(game.year or ""), game.title.casefold()))
        elif key == "provider":
            rows.sort(key=lambda game: (game.source_id, game.title.casefold()))
        elif key == "links":
            rows.sort(key=lambda game: (-len(http_links(self.store, game.key)),
                                        game.title.casefold()))
        elif key == "status":
            rows.sort(key=lambda game: (
                -sum(1 for link in http_links(self.store, game.key)
                     if str(getattr(link, "check_status", "")) == "verified"),
                game.title.casefold(),
            ))
        else:
            rows.sort(key=lambda game: (game.title or game.game_id).casefold())
        return rows

    def _fill_list(self) -> None:
        self.list.clear()
        for game in self._shown:
            links = http_links(self.store, game.key)
            item = QListWidgetItem(
                f"{game.title or game.game_id}  ·  {len(links)} link(s)"
            )
            item.setToolTip(game.source_url or game.key)
            self.list.addItem(item)
        if not self._shown:
            self._message(
                "No imported records match this view. Refresh the catalog, or "
                "clear the filters."
            )
            return
        index = next(
            (i for i, game in enumerate(self._shown) if game.key == self._selected_key), 0,
        )
        self.list.setCurrentRow(index)

    # refresh ---------------------------------------------------------------
    def start_refresh(self, resume: bool = False) -> None:
        """Import into the worker thread, never on the UI thread."""
        if self._busy:
            return
        job = None
        if resume:
            job = self.store.latest_job("kryoto")
            if job is None or not getattr(job, "resumable", False):
                self._message("There is no interrupted Kryoto scan to resume.")
                return
        from .sources import KryotoSource

        self._cancel.clear()
        self._busy = True
        self.refresh_btn.setEnabled(False)
        self.resume_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.check_btn.setEnabled(False)
        self.sources_btn.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self._message("Resuming the Kryoto scan…" if resume else "Refreshing the Kryoto catalog…")
        options = ImportOptions(page_size=25, enrich=True, workers=4)
        store = self.store
        cancel = self._cancel

        def progress(snapshot: object) -> None:
            with self._progress_lock:
                self._snapshot = {
                    "phase": str(getattr(snapshot, "phase", "") or ""),
                    "pages": int(getattr(snapshot, "pages_processed", 0) or 0),
                    "discovered": int(getattr(snapshot, "games_discovered", 0) or 0),
                    "enrich_done": int(getattr(snapshot, "enrich_done", 0) or 0),
                    "enrich_total": int(getattr(snapshot, "enrich_total", 0) or 0),
                    "links": int(getattr(snapshot, "mirrors_found", 0) or 0),
                }

        def work() -> object:
            importer = CatalogImporter(store, KryotoSource(), options=options)
            report = None if resume else importer.inspect()
            outcome = importer.run(
                job=job if resume else None, cancel=cancel, progress=progress,
                enrich=True,
            )
            try:
                store.save()
            except OSError:
                pass
            return outcome, report

        def done(result: object) -> None:
            self._finish(True)
            outcome = result[0] if isinstance(result, tuple) else result
            if isinstance(outcome, ImportOutcome):
                self._message(outcome.summary)
            self.refresh_view()
            if self._on_imported is not None:
                try:
                    self._on_imported(self.store.count_games())
                except Exception:  # noqa: BLE001 - a callback cannot break the dialog
                    pass

        def failed(exc: object) -> None:
            self._finish(True)
            self._message(f"The refresh failed: {exc}")
            self.refresh_view()

        self._poll.start()
        run_async(work, done, failed)

    def _finish(self, done: bool) -> None:
        self._busy = False
        self._poll.stop()
        self.progress.setVisible(False)
        self.refresh_btn.setEnabled(True)
        self.resume_btn.setEnabled(self._has_resumable())
        self.cancel_btn.setEnabled(False)
        self.check_btn.setEnabled(True)
        self.sources_btn.setEnabled(True)
        if done:
            with self._progress_lock:
                self._snapshot = {}

    def _paint_progress(self) -> None:
        if not self._busy:
            return
        with self._progress_lock:
            snapshot = dict(self._snapshot)
        if not snapshot:
            return
        if snapshot.get("phase") == "enrichment" and snapshot.get("enrich_total"):
            done = int(snapshot["enrich_done"])
            total = int(snapshot["enrich_total"])
            self.progress.setRange(0, total)
            self.progress.setValue(min(done, total))
            self._message(
                f"Details and source links: {done:,}/{total:,} ({int(done * 100 / total)}%) · "
                f"{int(snapshot.get('links', 0)):,} link(s) recorded"
            )
        else:
            self.progress.setRange(0, 0)
            self._message(
                f"Catalog page {int(snapshot.get('pages', 0)):,} · "
                f"{int(snapshot.get('discovered', 0)):,} game(s) discovered"
            )

    def cancel_refresh(self) -> None:
        if not self._busy:
            return
        self._cancel.set()
        self._message("Cancelling at the next page boundary; the checkpoint is kept.")

    # actions ---------------------------------------------------------------
    def check_kryoto_links(self) -> None:
        """Probe every recorded Kryoto-hosted link, as the Linux button does."""
        if self._busy:
            return
        targets = []
        for game in self.store.iter_games():
            for link in http_links(self.store, game.key):
                if kryoto.host_allowed(str(getattr(link, "url", "")), kryoto.KRYOTO_HOSTS):
                    targets.append(link)
        if not targets:
            self._message("No Kryoto-hosted links are recorded yet. Refresh the catalog first.")
            return
        self._busy = True
        self.check_btn.setEnabled(False)
        self.refresh_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress.setVisible(True)
        self.progress.setRange(0, len(targets))
        self._cancel.clear()
        store = self.store
        cancel = self._cancel

        def work() -> int:
            checked = 0
            for index, link in enumerate(targets, start=1):
                if cancel.is_set():
                    break
                url = str(getattr(link, "url", "") or "")
                try:
                    result = kryoto.check_link(url, allow_external=False)
                except Exception:  # noqa: BLE001 - one bad link is not the end
                    continue
                link.last_checked = time.time()
                link.check_status = result.status
                link.url_kind = result.kind or getattr(link, "url_kind", "")
                link.content_type = result.content_type
                link.notes = tuple(result.notes)
                if result.filename and not getattr(link, "filename", ""):
                    link.filename = result.filename
                if result.size and not getattr(link, "size", 0):
                    link.size = result.size
                checked += 1
                with self._progress_lock:
                    self._snapshot = {"checking": index, "total": len(targets)}
            try:
                store.save()
            except OSError:
                pass
            return checked

        def done(count: object) -> None:
            self._finish(True)
            self._message(f"Checked {count} Kryoto-hosted link(s).")
            self.refresh_view()

        def failed(exc: object) -> None:
            self._finish(True)
            self._message(f"The link check failed: {exc}")
            self.refresh_view()

        self._poll.stop()
        self._progress_lock_paint = True
        run_async(work, done, failed)
        self._check_poll = QTimer(self)
        self._check_poll.setInterval(200)

        def paint() -> None:
            with self._progress_lock:
                snapshot = dict(self._snapshot)
            if "checking" in snapshot:
                self.progress.setRange(0, int(snapshot.get("total", 0)))
                self.progress.setValue(int(snapshot["checking"]))
                self._message(
                    f"Checking {int(snapshot['checking']):,}/{int(snapshot.get('total', 0)):,}"
                )
            if not self._busy:
                self._check_poll.stop()

        self._check_poll.timeout.connect(paint)
        self._check_poll.start()

    def open_other_sources(self) -> None:
        dialog = CatalogDialog(
            self, catalog=self.store, on_imported=self._other_imported, enrich=False,
        )
        dialog.exec()
        self.refresh_view()

    def _other_imported(self, count: int) -> None:
        self.refresh_view()
        if self._on_imported is not None:
            try:
                self._on_imported(count)
            except Exception:  # noqa: BLE001
                pass

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._cancel.set()
        super().closeEvent(event)


__all__ = [
    "CatalogDialog",
    "CatalogDownloadDialog",
    "KRYOTO_FILTERS",
    "KRYOTO_HINT",
    "KRYOTO_SORTS",
    "KryotoCatalogDialog",
    "KRYOTO_URL",
    "KRYOTO_NOTE",
    "WEBSITE_NOTE",
    "catalog_identifier",
    "mirror_filename",
]
