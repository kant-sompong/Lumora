"""Website sources and catalogue import for the Qt (Windows) front end.

The GTK build has a full "Website Sources" dialog with per-source capability
reports.  This is the Qt equivalent: one URL box that inspects the page and
imports whatever catalogue it publishes, plus a preset for the Kryoto catalogue.
Both use the same core as Linux (``sources`` + ``importer`` + ``CatalogStore``),
so an import done here is the same import the Linux build would make.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .catalog_store import CatalogStore
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


__all__ = ["CatalogDialog", "KRYOTO_URL", "KRYOTO_NOTE", "WEBSITE_NOTE"]
