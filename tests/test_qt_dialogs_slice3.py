"""Slice 3: every sheet gets the shared treatment; the three small fixes hold.

The dialogs are built from their real constructors with throwaway stores, so a
dialog that stops setting its layout margins, its starting focus, its Escape
handling or its Enter target fails here rather than on a desktop.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QImage, QPixmap  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QWidget,
)

from lgl import design_components as dc  # noqa: E402
from lgl import qt_main, qt_theme  # noqa: E402
from lgl.archive import ArchiveFile  # noqa: E402
from lgl.catalog_store import CatalogGame, CatalogStore  # noqa: E402
from lgl.collections import CollectionStore  # noqa: E402
from lgl.design_tokens import contrast_ratio  # noqa: E402
from lgl.library_store import LibraryEntry  # noqa: E402
from lgl.profiles import Profiles  # noqa: E402
from lgl.settings import Settings  # noqa: E402


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    qt_main.apply_theme(application, "dark")
    return application


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(tmp_path / "config.json", keyring=False)


def _entries(tmp_path: Path) -> list[LibraryEntry]:
    entries = []
    for index, title in enumerate(("Hollow Knight", "Celeste", "Hades")):
        folder = tmp_path / f"game{index}"
        folder.mkdir(exist_ok=True)
        (folder / "game.7z").write_bytes(b"7z")
        entries.append(LibraryEntry(
            identifier=f"game{index}", title=title, folder=str(folder),
            year=str(2017 + index), size=1_000_000 * (index + 1),
        ))
    return entries


def _items() -> list[dict[str, object]]:
    return [
        {"key": "game0", "title": "Hollow Knight", "year": "2017",
         "genres": ["Metroidvania"], "tags": ["metroidvania"]},
        {"key": "game1", "title": "Celeste", "year": "2018",
         "genres": ["Platformer"], "tags": ["platformer"]},
        {"key": "game2", "title": "Hades", "year": "2020",
         "genres": ["Roguelike"], "tags": ["roguelike"]},
    ]


def _events() -> list[dict[str, object]]:
    return [{"kind": "play", "key": "game0", "title": "Hollow Knight",
             "at": 1_700_000_000.0}]


def _pixmap(tmp_path: Path) -> QPixmap:
    return QPixmap.fromImage(_image(tmp_path))


def _image(tmp_path: Path) -> QImage:
    image = QImage(32, 32, QImage.Format_ARGB32)
    image.fill(0xFF223344)
    return image


def _png(tmp_path: Path) -> Path:
    path = tmp_path / "shot.png"
    image = QImage(32, 32, QImage.Format_ARGB32)
    image.fill(0xFF223344)
    image.save(str(path))
    return path


def _catalog_game(description: str = "") -> CatalogGame:
    return CatalogGame(
        source_id="kryoto", game_id="game0", title="Hollow Knight", year="2017",
        description=description,
    )


def _catalog(tmp_path: Path) -> CatalogStore:
    store = CatalogStore(tmp_path / "catalog.json")
    store.upsert_game(_catalog_game())
    return store


#: One builder per restyled dialog, keyed by the class the test reports.
def _builders() -> dict[str, object]:
    from lgl.qt_artwork import MatchArtworkDialog
    from lgl.qt_browse import ScreenshotViewer
    from lgl.qt_calendar import CalendarDialog
    from lgl.qt_collections import CollectionsDialog
    from lgl.qt_discover import (
        ActivityTimelineDialog,
        CompleteTheSetDialog,
        DiscoverDialog,
        FavouritesDialog,
    )
    from lgl.qt_downloads import FilePickDialog
    from lgl.qt_library import DuplicatesDialog, EditDetailsDialog
    from lgl.qt_library_tools import (
        BulkEditDialog,
        MetadataEditorDialog,
        TagsDialog,
    )
    from lgl.qt_library_tools import DuplicatesDialog as ToolsDuplicatesDialog
    from lgl.qt_media import TrailerDialog
    from lgl.qt_profiles import ProfilesDialog
    from lgl.qt_recent import RecentlyViewedDialog
    from lgl.qt_settings import AboutDialog
    from lgl.qt_shelves import ShelvesDialog
    from lgl.qt_shortcuts import ShortcutsDialog
    from lgl.qt_stats import StatisticsDialog
    from lgl.qt_tools import ToolsDialog

    return {
        "MatchArtworkDialog": lambda t: MatchArtworkDialog(_catalog(t), _catalog_game()),
        "ScreenshotViewer": lambda t: ScreenshotViewer([_pixmap(t)], 0),
        "CalendarDialog": lambda t: CalendarDialog(None, autoload=False),
        "CollectionsDialog": lambda t: CollectionsDialog(
            CollectionStore(t / "collections.json"), _entries(t)),
        "DiscoverDialog": lambda t: DiscoverDialog(_items()),
        "FavouritesDialog": lambda t: FavouritesDialog(_items()),
        "ActivityTimelineDialog": lambda t: ActivityTimelineDialog(_events()),
        "CompleteTheSetDialog": lambda t: CompleteTheSetDialog(_items(), ["game0"]),
        "FilePickDialog": lambda t: FilePickDialog(
            "Download", [ArchiveFile(name="setup.7z", size=1024, format="7z")]),
        "EditDetailsDialog": lambda t: EditDetailsDialog(_entries(t)[0]),
        "DuplicatesDialog": lambda t: DuplicatesDialog([_entries(t)[:2]]),
        "ToolsDuplicatesDialog": lambda t: ToolsDuplicatesDialog(_entries(t)),
        "TagsDialog": lambda t: TagsDialog(_entries(t), None),
        "MetadataEditorDialog": lambda t: MetadataEditorDialog(_entries(t)[0]),
        "BulkEditDialog": lambda t: BulkEditDialog(_entries(t), ["game0"]),
        "TrailerDialog": lambda t: TrailerDialog("file://" + str(_png(t)), "Trailer"),
        "ProfilesDialog": lambda t: ProfilesDialog(Profiles(t / "profiles.json")),
        "RecentlyViewedDialog": lambda t: RecentlyViewedDialog(t / "recent.json"),
        "AboutDialog": lambda t: AboutDialog(),
        "ShelvesDialog": lambda t: ShelvesDialog(Settings(t / "shelves.json", keyring=False)),
        "ShortcutsDialog": lambda t: ShortcutsDialog(
            Settings(t / "keys.json", keyring=False)),
        "StatisticsDialog": lambda t: StatisticsDialog(_entries(t), _events()),
        "ToolsDialog": lambda t: ToolsDialog(Settings(t / "tools.json", keyring=False)),
    }


BUILDERS = _builders()


def _pump(app: QApplication, seconds: float = 0.15) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def _primary(dialog: QWidget) -> QPushButton | None:
    button = dialog.findChild(QPushButton, "suggested")
    if button is not None:
        return button
    for box in dialog.findChildren(QDialogButtonBox):
        for role in (QDialogButtonBox.Ok, QDialogButtonBox.Save,
                     QDialogButtonBox.Apply, QDialogButtonBox.Yes):
            candidate = box.button(role)
            if isinstance(candidate, QPushButton) and candidate.isVisible():
                return candidate
    return None


@pytest.mark.parametrize("name", sorted(BUILDERS))
def test_restyled_dialog_opens_as_a_sheet(name, app, tmp_path):
    dialog = BUILDERS[name](tmp_path)
    assert dialog.objectName() == "sheet", name
    dialog.show()
    _pump(app)

    layout = dialog.layout()
    padding = 0 if name in {"ScreenshotViewer", "TrailerDialog"} else dc.DIALOG_PADDING
    margins = layout.contentsMargins()
    assert (margins.left(), margins.right()) == (padding, padding), name
    assert margins.top() == padding, name
    assert layout.spacing() == dc.DIALOG_GAP, name

    focus = dialog.focusWidget()
    assert focus is not None and dialog.isAncestorOf(focus), name

    primary = _primary(dialog)
    if primary is not None:
        assert primary.isDefault(), name

    QTest.keyClick(dialog, Qt.Key_Escape)
    _pump(app)
    assert not dialog.isVisible(), name
    dialog.close()


# -- item 2: the literal ampersand ------------------------------------------
def test_settings_buttons_keep_their_ampersand(app, settings):
    from lgl.qt_settings import SettingsDialog

    dialog = SettingsDialog(settings)
    dialog.show()
    _pump(app)
    try:
        texts = [button.text() for button in dialog.findChildren(QPushButton)]
        assert "Save && test" in texts
        # A single ampersand is a mnemonic and eats the next character; every
        # button text in the sheet must be literal, so every & comes doubled.
        for text in texts:
            assert text.count("&") % 2 == 0, text
        # Nothing is rendered with Qt's mnemonic underscore: unescaped, the
        # button still reads exactly "Save & test".
        assert "Save && test".replace("&&", "&") == "Save & test"
        assert not any(text.endswith("_test") for text in texts)
        # The same rule for every label the page shows: a single & would eat
        # the character after it ("Media _playback", "Movies _TV").
        for caption in dialog.findChildren(QLabel):
            assert caption.text().count("&") % 2 == 0, caption.text()
    finally:
        dialog.close()


def test_shortcut_rows_show_a_literal_ampersand(app, settings):
    from lgl.qt_shortcuts import BINDINGS, ShortcutsDialog
    from lgl.qt_widgets import literal

    assert literal("Movies & TV") == "Movies && TV"
    assert dict((key, label) for key, label, _ in BINDINGS)["nav.media"] == "Movies & TV"
    dialog = ShortcutsDialog(settings)
    dialog.show()
    _pump(app)
    try:
        labels = [label.text() for label in dialog.findChildren(QLabel)]
        assert "Movies && TV" in labels
        for text in labels:
            assert text.count("&") % 2 == 0, text
    finally:
        dialog.close()


# -- item 3: the catalogue pane is a card -----------------------------------
def test_catalog_pane_is_a_surface_card_with_a_title(app, tmp_path):
    from lgl.qt_catalog_links import CatalogGamePane

    pane = CatalogGamePane(CatalogStore(tmp_path / "pane.json"))
    pane.show()
    _pump(app)
    try:
        assert pane.objectName() == "catalogPane"
        sheet = qt_theme.build_stylesheet("dark")
        assert "QWidget#catalogPane" in sheet
        assert f"border-radius: {dc.CARD_RADIUS}px" in sheet
        assert f"padding: {dc.CARD_PADDING}px" in sheet

        pane.show_record(_catalog_game("A bug with a nail."), "kryoto")
        _pump(app)
        assert pane.title.text() == "Hollow Knight"
        assert pane.description.text().strip()
        assert pane.description.text() == "A bug with a nail."
        about = [label.text() for label in pane.findChildren(QLabel)]
        assert "ABOUT" in about
    finally:
        pane.close()


# -- item 4: the library tab strip paints the page --------------------------
def test_library_tab_strip_sits_on_the_page_background(app, tmp_path):
    from lgl.library_store import LibraryStore
    from lgl.qt_library import LibraryPage

    assert "QWidget#pageBody" in qt_theme.build_stylesheet("dark")

    store = LibraryStore(tmp_path / "library.json", download_root=tmp_path / "games")
    for entry in _entries(tmp_path):
        store.upsert(entry)
    page = LibraryPage(store, Settings(tmp_path / "page.json", keyring=False))
    page.set_entries(store.entries())

    window = QMainWindow()
    body = QWidget()
    body.setObjectName("pageBody")
    row = QHBoxLayout(body)
    row.setContentsMargins(0, 0, 0, 0)
    row.addWidget(page, 1)
    window.setCentralWidget(body)
    window.resize(1100, 760)
    window.show()
    _pump(app, 0.3)
    try:
        image = window.grab().toImage()
        strip = page._tab_row.mapTo(window, page._tab_row.rect().topLeft())
        sample = image.pixelColor(strip.x() + page._tab_row.width() - 20, strip.y() + 8)
        background = qt_theme.palette("dark")["bg"].lstrip("#")
        expected = QImage(1, 1, QImage.Format_ARGB32)
        expected.fill(0)
        assert sample.alpha() == 255, "the tab strip leaves an unpainted hole"
        assert sample.name().lower() == f"#{background}".lower()
    finally:
        window.close()


# -- item 5: the new text pairs are readable --------------------------------
def test_new_text_pairs_meet_contrast(app):
    for theme in ("dark", "light"):
        colours = qt_theme.palette(theme)
        for foreground in ("text", "muted", "text2"):
            ratio = contrast_ratio(colours[foreground], colours["raised"])
            assert ratio >= 4.5, f"{theme}: {foreground} on raised is {ratio:.2f}"
