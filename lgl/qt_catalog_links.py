"""The catalogue game page and its per-link actions, for the Qt frontend.

This is the Qt twin of the Linux detail panel's catalogue view: facts from the
stored record, the links the importer actually recorded, and one row per link
with Copy / Open / Check / Download.  Everything here reads the shared core
(``catalog_store``, ``kryoto``, ``external_dm``); nothing is fetched unless the
user presses Check or Download, and a web page is never queued.

Both the Browse game page and the Kryoto catalogue dialog build the same
``CatalogGamePane``, so the two can never disagree about what a link is.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import external_dm, kryoto, platform_support
from .catalog_store import CatalogGame, CatalogStore, MagnetRecord, MirrorRecord
from .qt_widgets import clear_layout, label, run_async
from .util import human_size

#: Shown under the send-to buttons, exactly as the Linux dialog does.
NOT_AFFILIATED = external_dm.NOT_AFFILIATED


def mirror_name(record: object) -> str:
    """The provider's own label for a link, falling back to its address."""
    for field_name in ("label", "filename", "host", "url"):
        value = str(getattr(record, field_name, "") or "")
        if value:
            return value
    return "link"


def mirror_status(record: object) -> str:
    status = getattr(record, "status_label", None)
    if callable(status):
        return str(status())
    return str(getattr(record, "check_status", "") or "unchecked")


def link_is_file(record: object) -> bool:
    """Only a probe that saw a file response qualifies, exactly as Linux."""
    return kryoto.is_downloadable_link(
        getattr(record, "check_status", ""), getattr(record, "url_kind", "")
    )


def needs_reclassification(record: object) -> bool:
    """A stored ``verified`` that never came with a file classification.

    ``verified`` is only meaningful next to ``url_kind == "file"``; the pair
    is what a probe writes.  A record holding the word alone cannot be
    downloaded and is not quietly re-probed from the Download button either -
    the user is asked to run Check, which records both fields again.
    """
    return (
        str(getattr(record, "check_status", "")) == "verified"
        and not link_is_file(record)
    )


def http_links(store: CatalogStore, game_key: str) -> list[MirrorRecord]:
    """The recorded http(s) links of one game, in the order the importer stored."""
    try:
        records = list(store.mirrors_for(game_key))
    except Exception:  # noqa: BLE001 - a store issue is shown, not raised
        return []
    return [
        record for record in records
        if str(getattr(record, "url", "")).startswith(("http://", "https://"))
    ]


def magnets_of(store: CatalogStore, game_key: str) -> list[MagnetRecord]:
    try:
        return list(store.magnets_for(game_key))
    except Exception:  # noqa: BLE001
        return []


def queue_mirror(
    manager: object | None,
    download_dir: Path | str | None,
    identifier: str,
    record: object,
) -> str:
    """Queue one http(s) link; returns an error message, or "" on success."""
    if manager is None:
        return "the download queue is unavailable"
    url = str(getattr(record, "url", "") or "")
    if not url:
        return "that link has no address"
    name = str(getattr(record, "filename", "") or "") or url.rsplit("/", 1)[-1] or "download"
    folder = Path(download_dir) if download_dir else Path.home()
    try:
        manager.add(  # type: ignore[attr-defined]
            identifier=identifier,
            filename=name,
            url=url,
            download_dir=folder,
            total_size=getattr(record, "size", 0) or None,
        )
    except Exception as exc:  # noqa: BLE001 - collected, never fatal
        return f"{name}: {exc}"
    return ""


def check_mirror(
    store: CatalogStore,
    record: object,
    on_done: Callable[[object, bool], None],
) -> None:
    """Probe one link in the background and persist what the server answered.

    Kryoto's own hosts are checked; an external mirror is probed too, because a
    per-link check is the user asking about that one address.
    """
    url = str(getattr(record, "url", "") or "")
    if not url:
        on_done(None, True)
        return
    allow_external = not kryoto.host_allowed(url, kryoto.KRYOTO_HOSTS)

    def work() -> object:
        return kryoto.check_link(url, allow_external=allow_external)

    def done(result: object) -> None:
        if not isinstance(result, kryoto.LinkCheck):
            on_done(None, True)
            return
        record.last_checked = time.time()  # type: ignore[attr-defined]
        record.check_status = result.status  # type: ignore[attr-defined]
        record.url_kind = result.kind or getattr(record, "url_kind", "")  # type: ignore[attr-defined]
        record.content_type = result.content_type  # type: ignore[attr-defined]
        record.notes = tuple(result.notes)  # type: ignore[attr-defined]
        if result.filename and not getattr(record, "filename", ""):
            record.filename = result.filename  # type: ignore[attr-defined]
        if result.size and not getattr(record, "size", 0):
            record.size = result.size  # type: ignore[attr-defined]
        try:
            saved = bool(store.save())
        except OSError:
            saved = False
        on_done(result, saved)

    run_async(work, done, lambda _exc: on_done(None, True))


def confirm_download(
    parent: QWidget | None, record: object, download_dir: Path | str | None
) -> bool:
    """Ask before queueing a catalogue file, naming the file and the folder."""
    name = str(getattr(record, "filename", "") or "") or mirror_name(record)
    folder = str(download_dir or Path.home())
    answer = QMessageBox.question(
        parent,
        "Download this catalog file?",
        f"{name}\n\nIt will be saved to {folder}.",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    return answer == QMessageBox.Yes


def download_mirror(
    parent: QWidget | None,
    store: CatalogStore,
    manager: object | None,
    download_dir: Path | str | None,
    identifier: str,
    record: object,
    on_message: Callable[[str, bool], None],
    on_queued: Callable[[], None] | None = None,
) -> None:
    """The Download button on one catalogue link, with the Linux rules.

    A link that has not been proven is checked first; only a file response is
    offered to the download manager, and the user confirms it by name.
    """

    def proceed() -> None:
        if link_is_file(record):
            if not confirm_download(parent, record, download_dir):
                return
            error = queue_mirror(manager, download_dir, identifier, record)
            if error:
                on_message(f"Nothing was queued: {error}", True)
                return
            on_message(f"Queued {mirror_name(record)}.", False)
            if on_queued is not None:
                on_queued()
            return
        on_message(
            "That link is a web page, not a file. Use Open in Browser instead.",
            True,
        )

    if link_is_file(record):
        proceed()
        return

    if needs_reclassification(record):
        on_message(
            "That link is marked verified but was never classified as a file. "
            "Press Check to probe it again.",
            True,
        )
        return

    on_message(f"Checking {getattr(record, 'host', '') or 'the link'} before downloading…", False)

    def checked(result: object, saved: bool) -> None:
        if result is None:
            on_message("That link could not be checked. Nothing was queued.", True)
            return
        if link_is_file(record):
            proceed()
            return
        if getattr(record, "check_status", "") in (
            "external", "blocked", "failed", "unsupported", "missing",
        ):
            on_message(
                "That link could not be checked. Nothing was queued.", True
            )
            return
        on_message(
            "That link is a web page, not a file. Use Open in Browser instead.",
            True,
        )

    check_mirror(store, record, checked)


# --------------------------------------------------------------------------
# widgets
# --------------------------------------------------------------------------


class ElidedUrlLabel(QLabel):
    """A link address that keeps all of its text but elides in the middle.

    The source URLs are long and the detail panel is 420 px wide, so wrapping
    one word is not an option: the whole panel would either overflow or grow a
    horizontal scrollbar.  The full address stays in ``text()``, the tooltip and
    the row's Copy button.
    """

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("catalogLinkUrl")
        self.setTextFormat(Qt.PlainText)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(0)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        metrics = self.fontMetrics()
        shown = metrics.elidedText(self.text(), Qt.ElideMiddle, max(0, self.width()))
        painter.drawText(self.rect(), int(self.alignment() | Qt.AlignVCenter), shown)


class CatalogLinkRow(QFrame):
    """One recorded source link, with the four actions Linux offers."""

    checked = Signal(object)
    download_requested = Signal(object)

    def __init__(self, record: object, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.record = record
        self.setObjectName("catalogLinkRow")
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 4, 0, 4)
        column.setSpacing(2)

        name = label(mirror_name(record), "fact-name")
        name.setWordWrap(True)
        column.addWidget(name)

        bits = [
            str(getattr(record, "host", "") or ""),
            str(getattr(record, "url_kind", "") or "").replace("-", " "),
            str(getattr(record, "content_type", "") or ""),
            human_size(getattr(record, "size", 0) or 0) if getattr(record, "size", 0) else "",
            mirror_status(record),
        ]
        detail = label(" · ".join(bit for bit in bits if bit), "detailNote", wrap=True)
        column.addWidget(detail)
        self.detail = detail

        url = str(getattr(record, "url", "") or "")
        self.url_label = ElidedUrlLabel(url)
        self.url_label.setToolTip(url)
        column.addWidget(self.url_label)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        for text, slot, tip in (
            ("Copy", self._copy, "Copy this source URL exactly"),
            ("Open in Browser", self._open, "Open this link in the default browser"),
            ("Check", self._check, "Ask the server about this link. Nothing is downloaded."),
            ("Download", self._download,
             "Checks this link first and queues it only if the server answers with a file"),
        ):
            button = QPushButton(text)
            button.setObjectName("suggested" if text == "Download" and link_is_file(record) else "quiet")
            button.setCursor(Qt.PointingHandCursor)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            actions.addWidget(button)
        actions.addStretch(1)
        column.addLayout(actions)

    def refresh(self) -> None:
        """Repaint the status line after a check changed the record."""
        bits = [
            str(getattr(self.record, "host", "") or ""),
            str(getattr(self.record, "url_kind", "") or "").replace("-", " "),
            str(getattr(self.record, "content_type", "") or ""),
            human_size(getattr(self.record, "size", 0) or 0) if getattr(self.record, "size", 0) else "",
            mirror_status(self.record),
        ]
        self.detail.setText(" · ".join(bit for bit in bits if bit))

    def _copy(self) -> None:
        url = str(getattr(self.record, "url", "") or "")
        if url:
            QApplication.clipboard().setText(url)

    def _open(self) -> None:
        url = str(getattr(self.record, "url", "") or "")
        if url:
            platform_support.open_url(url)

    def _check(self) -> None:
        self.checked.emit(self.record)

    def _download(self) -> None:
        self.download_requested.emit(self.record)


class CatalogLinksSection(QWidget):
    """The SOURCE LINKS block: one row per recorded link."""

    checked = Signal(object)
    download_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._column = QVBoxLayout(self)
        self._column.setContentsMargins(0, 0, 0, 0)
        self._column.setSpacing(2)
        self.rows: list[CatalogLinkRow] = []

    def set_links(self, records: Sequence[object]) -> None:
        clear_layout(self._column)
        self.rows = []
        for record in records:
            row = CatalogLinkRow(record, self)
            row.checked.connect(self.checked.emit)
            row.download_requested.connect(self.download_requested.emit)
            self._column.addWidget(row)
            self.rows.append(row)
        if not records:
            self._column.addWidget(
                label(
                    "No source links were reported for this game. It is listed in "
                    "the catalog but not downloadable from it.",
                    "detailNote", wrap=True,
                )
            )


class CatalogMagnetsSection(QWidget):
    """The MAGNETS block, using the existing Qt torrent queue."""

    def __init__(self, torrents: object | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.torrents = torrents
        self._column = QVBoxLayout(self)
        self._column.setContentsMargins(0, 0, 0, 0)
        self._column.setSpacing(2)
        self.rows: list[QWidget] = []

    def set_magnets(self, records: Sequence[MagnetRecord], title: str = "") -> None:
        clear_layout(self._column)
        self.rows = []
        if not records:
            self._column.addWidget(
                label(
                    "This source did not expose a magnet link for this game.",
                    "detailNote", wrap=True,
                )
            )
            return
        for record in records:
            row = QWidget(self)
            # The panel is 420 px wide: the hash and the buttons cannot share a
            # line without forcing the whole panel wider than its slot.
            block = QVBoxLayout(row)
            block.setContentsMargins(0, 2, 0, 2)
            block.setSpacing(2)
            origin = "built from infohash" if record.origin == "constructed" else "from source"
            name = label(
                f"{(record.infohash or '')[:16]}… · {origin}", "fact-name", wrap=True,
            )
            name.setToolTip(record.uri)
            block.addWidget(name)
            line = QHBoxLayout()
            line.setSpacing(6)
            for text, slot in (
                ("Download with torrent", lambda _=False, r=record: self._start(r, title)),
                ("Copy", lambda _=False, r=record: QApplication.clipboard().setText(r.uri)),
                ("Open", lambda _=False, r=record: platform_support.open_magnet(r.uri)),
            ):
                button = QPushButton(text)
                button.setObjectName("quiet")
                button.setCursor(Qt.PointingHandCursor)
                button.clicked.connect(slot)
                line.addWidget(button)
            line.addStretch(1)
            block.addLayout(line)
            self._column.addWidget(row)
            self.rows.append(row)

    def _start(self, record: MagnetRecord, title: str) -> None:
        if self.torrents is None:
            return
        try:
            self.torrents.add(record.uri, title=title or record.display_name)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - the window reports queue problems
            return


class CatalogGamePane(QWidget):
    """The Linux catalogue game page, as a Qt widget.

    Facts, description, the provider's own informational fields, the source
    page buttons, the recorded links and magnets, and the two send-to buttons.
    """

    changed = Signal()

    def __init__(
        self,
        store: CatalogStore | None = None,
        manager: object | None = None,
        torrents: object | None = None,
        download_dir: Path | str | None = None,
        *,
        on_message: Callable[[str, bool], None] | None = None,
        on_queued: Callable[[str, str], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.store = store or CatalogStore()
        self.manager = manager
        self.torrents = torrents
        self.download_dir = download_dir
        self._on_message = on_message
        self._on_queued = on_queued
        self.record: CatalogGame | None = None

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)

        self.title = label("", "detailTitle", wrap=True)
        column.addWidget(self.title)
        self.subtitle = label("", "detailSub", wrap=True)
        column.addWidget(self.subtitle)

        self._facts: dict[str, QLabel] = {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)
        for index, name in enumerate(
            (
                "Year", "Developer", "Publisher", "Genres", "Platforms", "Version",
                "Size", "Source", "Provider", "Identifier",
            )
        ):
            caption = label(name, "kvKey")
            caption.setFixedWidth(82)
            value = label("—", "kvVal", wrap=True)
            grid.addWidget(caption, index, 0, Qt.AlignTop)
            grid.addWidget(value, index, 1)
            self._facts[name] = value
        column.addLayout(grid)

        column.addWidget(label("ABOUT", "kvKey"))
        self.description = label("", "detailNote", wrap=True)
        column.addWidget(self.description)
        self.provider_note = label("", "detailNote", wrap=True)
        column.addWidget(self.provider_note)
        self.provider_note.hide()

        page_row = QHBoxLayout()
        page_row.setSpacing(8)
        self.source_btn = QPushButton("Open source page")
        self.source_btn.setObjectName("quiet")
        self.source_btn.setCursor(Qt.PointingHandCursor)
        self.source_btn.clicked.connect(self._open_source)
        page_row.addWidget(self.source_btn)
        self.copy_source_btn = QPushButton("Copy source URL")
        self.copy_source_btn.setObjectName("quiet")
        self.copy_source_btn.setCursor(Qt.PointingHandCursor)
        self.copy_source_btn.clicked.connect(self._copy_source)
        page_row.addWidget(self.copy_source_btn)
        page_row.addStretch(1)
        column.addLayout(page_row)

        column.addWidget(label("SOURCE LINKS", "kvKey"))
        self.links = CatalogLinksSection(self)
        self.links.checked.connect(self._check_link)
        self.links.download_requested.connect(self._download_link)
        column.addWidget(self.links)

        send_row = QHBoxLayout()
        send_row.setSpacing(8)
        self.jd_btn = QPushButton("Send to JDownloader")
        self.ab_btn = QPushButton("Send to AB Download Manager")
        for button, name in ((self.jd_btn, "jdownloader"), (self.ab_btn, "ab_download_manager")):
            button.setObjectName("quiet")
            button.setCursor(Qt.PointingHandCursor)
            known = external_dm.MANAGERS[name]
            button.setToolTip(f"{known['name']} — {known['how']}")
            button.clicked.connect(lambda _=False, key=name: self._send_external(key))
            send_row.addWidget(button)
        send_row.addStretch(1)
        column.addLayout(send_row)
        column.addWidget(label(NOT_AFFILIATED, "detailNote", wrap=True))

        column.addWidget(label("MAGNETS", "kvKey"))
        self.magnets = CatalogMagnetsSection(torrents, self)
        column.addWidget(self.magnets)
        column.addStretch(1)

    # showing ---------------------------------------------------------------
    def show_record(self, record: CatalogGame, source_name: str = "") -> None:
        self.record = record
        extra = record.extra if isinstance(record.extra, dict) else {}
        fields = extra.get("source_fields")
        fields = fields if isinstance(fields, dict) else {}
        self.title.setText(record.title or record.game_id)
        subtitle = [value for value in (record.year, record.developer, source_name) if value]
        self.subtitle.setText(
            " · ".join(subtitle)
            or "No further metadata was reported by the source."
        )
        sizes = []
        if fields.get("size"):
            sizes.append(f"install {fields['size']}")
        if fields.get("download_size"):
            sizes.append(f"download {fields['download_size']}")
        if not sizes:
            estimated = None
            try:
                estimated = self.store.estimated_size(record.key)
            except Exception:  # noqa: BLE001
                estimated = None
            if estimated:
                sizes.append(human_size(estimated))
        values = {
            "Year": record.year or getattr(record, "release_date", ""),
            "Developer": record.developer,
            "Publisher": record.publisher,
            "Genres": ", ".join(record.genres or ()),
            "Platforms": ", ".join(record.platforms or ()),
            "Version": str(extra.get("version") or fields.get("version") or ""),
            "Size": " · ".join(sizes),
            "Source": source_name or record.source_id,
            "Provider": source_name or record.source_id,
            "Identifier": record.game_id,
        }
        for name, value in values.items():
            self._facts[name].setText(str(value or "—"))
        self.description.setText(record.description or "This game has no description.")
        if fields:
            listed = " · ".join(
                f"{name}: {_value(value)}" for name, value in list(fields.items())[:12]
            )
            self.provider_note.setText(
                "Reported by the source (informational — not used as links): " + listed
            )
            self.provider_note.show()
        else:
            self.provider_note.hide()
        self.source_btn.setVisible(bool(record.source_url))
        self.copy_source_btn.setVisible(bool(record.source_url))
        self.refresh()

    def refresh(self) -> None:
        """Re-read the stored links and magnets for the current record."""
        if self.record is None:  # pragma: no cover - nothing to show yet
            return
        records = http_links(self.store, self.record.key)
        self.links.set_links(records)
        self.magnets.set_magnets(magnets_of(self.store, self.record.key),
                                 self.record.title or self.record.game_id)
        enabled = bool(records)
        self.jd_btn.setEnabled(enabled)
        self.ab_btn.setEnabled(enabled)

    # actions ---------------------------------------------------------------
    def _message(self, text: str, error: bool = False) -> None:
        if self._on_message is not None:
            self._on_message(text, error)

    def _open_source(self) -> None:
        if self.record is not None and self.record.source_url:
            if not platform_support.open_url(self.record.source_url):
                self._message("No browser answered for this link.", True)

    def _copy_source(self) -> None:
        if self.record is not None and self.record.source_url:
            QApplication.clipboard().setText(self.record.source_url)

    def _check_link(self, record: object) -> None:
        self._message("Checking the link…")

        def done(result: object, saved: bool) -> None:
            if result is None:
                self._message("That link could not be checked.", True)
                return
            if not saved:
                self._message("Checked, but the catalogue could not be saved.", True)
            else:
                self._message(f"Check: {mirror_status(record)}.")
            self.refresh()
            self.changed.emit()

        check_mirror(self.store, record, done)

    def _download_link(self, record: object) -> None:
        if self.record is None:
            return
        identifier = f"catalog-{self.record.key}".replace(":", "-")

        def done() -> None:
            if self._on_queued is not None:
                self._on_queued(identifier, self.record.title if self.record else "")

        download_mirror(
            self.window(), self.store, self.manager, self.download_dir,
            identifier, record, self._message, done,
        )

    def _send_external(self, name: str) -> None:
        if self.record is None:
            return
        urls = [str(getattr(record, "url", "") or "") for record in
                http_links(self.store, self.record.key)]
        urls = [url for url in urls if url]
        if not urls:
            self._message("This game has no links to send.", True)
            return
        from .qt_downloads import send_to_external

        folder = str(self.download_dir or "")

        def done(count: object) -> None:
            self._message(f"Sent {count} link(s) to the download manager.")

        def failed(exc: object) -> None:
            self._message(f"Could not send the links: {exc}", True)

        run_async(lambda: send_to_external(name, urls, folder), done, failed)


def _value(value: object) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value)
    return str(value)


__all__ = [
    "CatalogGamePane",
    "CatalogLinkRow",
    "CatalogLinksSection",
    "CatalogMagnetsSection",
    "NOT_AFFILIATED",
    "check_mirror",
    "needs_reclassification",
    "confirm_download",
    "download_mirror",
    "http_links",
    "link_is_file",
    "magnets_of",
    "mirror_name",
    "mirror_status",
    "queue_mirror",
]
