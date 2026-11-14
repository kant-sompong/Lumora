"""The Qt design system: contrast, metrics, card states and dialog behaviour.

Every number under test comes from :mod:`lgl.design_components`, and every
colour from :mod:`lgl.design_tokens`; nothing here hard-codes a size that the
stylesheet is supposed to read from the shared table.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QFontMetricsF  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QComboBox,
    QDialog,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from lgl import design_components as dc  # noqa: E402
from lgl import qt_theme  # noqa: E402
from lgl.archive import GameSummary  # noqa: E402
from lgl.catalog_store import CatalogStore  # noqa: E402
from lgl.design_tokens import TOKENS, contrast_ratio  # noqa: E402
from lgl.library_store import LibraryEntry  # noqa: E402
from lgl.qt_browse import BrowseCard  # noqa: E402
from lgl.qt_catalog import CatalogDialog  # noqa: E402
from lgl.qt_library import LibraryCard  # noqa: E402
from lgl.qt_media import Card as MediaCard  # noqa: E402
from lgl.qt_media import PosterCard  # noqa: E402
from lgl.qt_palette import PaletteDialog  # noqa: E402
from lgl.qt_settings import SettingsDialog  # noqa: E402
from lgl.qt_widgets import FieldError, IconButton  # noqa: E402
from lgl.settings import Settings  # noqa: E402

THEMES = ("dark", "light")

#: The two pairs the immutable palette cannot bring to 4.5:1.  The accent and
#: the danger colour are tokens, so the stylesheet cannot brighten or darken
#: them here, and neither pairing reaches the threshold with any other token
#: colour.  They are pinned so a token change is noticed either way.
TOKEN_CLASHES = {
    ("dark", "white", "accent"): 4.23,
    ("light", "danger", "surface"): 3.76,
}


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def themed(app):
    """Apply a real stylesheet, the way ``qt_main`` does, and clear it after."""
    def apply(theme: str = "dark") -> str:
        qss = qt_theme.build_stylesheet(theme)
        app.setStyleSheet(qss)
        return qss
    yield apply
    app.setStyleSheet("")


def _game(key: str = "game") -> GameSummary:
    return GameSummary(
        identifier=key, title=key.upper(), year="2024", downloads=10, item_size=None
    )


def _entry(key: str = "game") -> LibraryEntry:
    return LibraryEntry(identifier=key, title=key.upper(), folder=f"/games/{key}")


def test_every_component_number_sits_on_a_token_step():
    assert dc.derived_ok(), "a component metric is off the shared scale"
    assert dc.BUTTON_HEIGHT == dc.BUTTON_CONTENT + 2 * dc.BORDER_WIDTH
    assert dc.INPUT_HEIGHT == dc.INPUT_CONTENT + 2 * dc.BORDER_WIDTH


# -- 5. contrast ----------------------------------------------------------

def test_text_on_background_clears_4_5_in_both_themes():
    for theme in THEMES:
        tokens = TOKENS[theme]
        for fg in ("text", "text2", "muted"):
            for bg in ("bg", "surface", "overlay"):
                ratio = contrast_ratio(tokens[fg], tokens[bg])
                assert ratio >= 4.5, f"{theme}: {fg} on {bg} is only {ratio:.2f}:1"


def test_only_the_documented_token_clashes_fall_short():
    measured = {}
    for theme in THEMES:
        tokens = TOKENS[theme]
        measured[(theme, "white", "accent")] = contrast_ratio(
            "#ffffff", tokens["accent"]
        )
        measured[(theme, "danger", "surface")] = contrast_ratio(
            tokens["danger"], tokens["surface"]
        )
    for key, value in TOKEN_CLASHES.items():
        assert round(measured[key], 2) == value, (
            f"{key[0]}: {key[1]} on {key[2]} moved to {measured[key]:.2f}:1; "
            "update the note in this test if the token changed on purpose"
        )
        assert value >= 3.5, f"{key} is too low to keep at all: {value}:1"


def test_the_light_theme_danger_line_is_not_used_on_overlay():
    """The danger line sits on the sheet surface, where it is at its best."""
    for theme in THEMES:
        qss = qt_theme.build_stylesheet(theme)
        assert "QLabel#fieldError" in qss


# -- 2/3. metrics ---------------------------------------------------------

def test_stylesheet_reads_the_shared_constants():
    for theme in THEMES:
        qss = qt_theme.build_stylesheet(theme)
        expected = (
            f"min-height: {dc.BUTTON_CONTENT}px",
            f"border-radius: {dc.BUTTON_RADIUS}px",
            f"min-height: {dc.BUTTON_SMALL_CONTENT}px",
            f"min-height: {dc.INPUT_CONTENT}px; max-height: {dc.INPUT_CONTENT}px",
            f"border-radius: {dc.INPUT_RADIUS}px",
            f"padding: 0 {dc.BUTTON_PADDING_X}px",
            f"border-radius: {dc.CARD_RADIUS}px",
            f"border-radius: {dc.POSTER_RADIUS}px",
            f"border: {dc.CARD_BORDER_WIDTH}px solid",
            f"border-radius: {dc.DIALOG_RADIUS}px",
            f"width: {dc.CHECK_INDICATOR}px",
            f"height: {dc.CHECK_INDICATOR}px",
        )
        for snippet in expected:
            assert snippet in qss, f"{theme}: the stylesheet lost {snippet!r}"


def test_buttons_and_inputs_measure_the_constants(app, themed):
    themed("dark")
    host = QWidget()
    column = QVBoxLayout(host)
    button = QPushButton("Save")
    small = QPushButton("Small")
    small.setObjectName("small")
    icon = IconButton("settings", "Settings")
    line = QLineEdit()
    combo = QComboBox()
    spin = QSpinBox()
    for widget in (button, small, icon, line, combo, spin):
        column.addWidget(widget)
    host.show()
    app.processEvents()

    assert button.height() == dc.BUTTON_HEIGHT
    assert small.height() == dc.BUTTON_SMALL_HEIGHT
    assert line.height() == dc.INPUT_HEIGHT
    assert combo.height() == dc.INPUT_HEIGHT
    assert spin.height() == dc.INPUT_HEIGHT
    assert icon.width() == dc.ICON_BUTTON_SIZE
    assert icon.height() == dc.ICON_BUTTON_SIZE
    assert icon.toolTip(), "an icon button needs a tooltip and an accessible name"
    host.close()


def test_error_state_marks_the_field_and_shows_one_line(app, themed):
    themed("dark")
    host = QWidget()
    column = QVBoxLayout(host)
    field = QLineEdit()
    error = FieldError()
    column.addWidget(field)
    column.addWidget(error)
    host.show()
    app.processEvents()

    assert not error.isVisible()
    error.show_for(field, "A key is required. Nothing was saved.")
    app.processEvents()
    assert field.property("error") is True
    assert error.isVisible()
    assert "required" in error.text()

    error.clear_for(field)
    app.processEvents()
    assert field.property("error") is False
    assert not error.isVisible()
    host.close()


# -- 1. cards -------------------------------------------------------------

def _cards() -> list[QWidget]:
    return [BrowseCard(_game()), LibraryCard(_entry())]


def test_a_card_hovers_selects_and_keeps_its_size(app, themed):
    themed("dark")
    for card in _cards():
        card.show()
        app.processEvents()
        size = card.size()
        card.set_hovered(False)
        assert card.property("hover") is False

        card.set_hovered(True)
        app.processEvents()
        assert card.property("hover") is True
        assert card.graphicsEffect().offset().y() == dc.CARD_LIFT
        assert card.size() == size, "the hover lift must not resize the card"

        card.set_hovered(False)
        app.processEvents()
        assert card.property("hover") is False
        assert card.graphicsEffect().offset().y() == 0
        assert card.size() == size

        card.set_selected(True)
        app.processEvents()
        assert card.property("selected") is True
        assert card.size() == size
        card.close()


def test_a_card_takes_keyboard_focus_and_the_ring_is_styled(app, themed):
    qss = themed("dark")
    for card in _cards():
        assert card.focusPolicy() & Qt.TabFocus, "a card must be reachable by Tab"
        card.setFocus()
        app.processEvents()
        assert card.hasFocus() or not card.isVisible()
        card.close()
    assert "QFrame#coverCard:focus" in qss
    assert f"border: {dc.CARD_FOCUS_RING}px solid" in qss


def test_a_card_without_art_shows_the_title_initials(app, themed):
    themed("dark")
    card = BrowseCard(_game("hollow-knight"))
    card.show()
    app.processEvents()
    assert card.cover._title, "the placeholder needs the title for its initials"
    card.close()


# -- 4. dialogs -----------------------------------------------------------

def _dialogs(tmp_path: Path) -> dict[str, QDialog]:
    store = CatalogStore(tmp_path / "catalog.json")
    return {
        "catalog": CatalogDialog(catalog=store),
        "palette": PaletteDialog(None),
        "settings": SettingsDialog(Settings(tmp_path / "s.json", keyring=False)),
    }


def test_dialogs_focus_escape_and_the_primary_button(app, themed, tmp_path):
    themed("dark")
    dialogs = _dialogs(tmp_path)
    # The settings sheet keeps its tab strip flush to the top edge, so the
    # padding belongs to the pages inside it rather than to the dialog.
    for name in ("catalog", "palette"):
        layout = dialogs[name].layout()
        margins = layout.contentsMargins()
        assert (margins.left(), margins.top()) == (dc.DIALOG_PADDING, dc.DIALOG_PADDING)
        assert margins.right() == dc.DIALOG_PADDING
        assert margins.bottom() == dc.DIALOG_PADDING
        assert layout.spacing() == dc.DIALOG_GAP
    settings_layout = dialogs["settings"].layout()
    assert settings_layout.contentsMargins().left() == 0
    for dialog in dialogs.values():
        dialog.show()
        app.processEvents()
        dialog.close()

    catalog = dialogs["catalog"]
    catalog.show()
    app.processEvents()
    assert catalog.focusWidget() is catalog.url, "focus starts in the first field"
    assert catalog.import_btn.isDefault(), "the primary button is the default"
    # Enter reaches the primary: an empty address is refused by the import itself.
    catalog.url.clear()
    QTest.keyClick(catalog.url, Qt.Key_Return)
    app.processEvents()
    assert "Enter the address" in catalog.status.text()
    QTest.keyClick(catalog, Qt.Key_Escape)
    app.processEvents()
    assert not catalog.isVisible(), "Escape closes the sheet"

    palette = dialogs["palette"]
    palette.show()
    app.processEvents()
    assert palette.focusWidget() is palette.field
    QTest.keyClick(palette, Qt.Key_Escape)
    app.processEvents()
    assert not palette.isVisible()

    settings = dialogs["settings"]
    settings.show()
    app.processEvents()
    assert settings.focusWidget() is settings._tabs["general"]
    QTest.keyClick(settings, Qt.Key_Escape)
    app.processEvents()
    assert not settings.isVisible()


def test_a_dialog_fits_a_900_px_window(app, themed, tmp_path):
    themed("dark")
    for dialog in _dialogs(tmp_path).values():
        dialog.resize(900, 600)
        dialog.show()
        app.processEvents()
        for child in dialog.findChildren(QWidget):
            if child.parent() is not dialog or not child.isVisible():
                continue
            geometry = child.geometry()
            assert geometry.right() <= dialog.width(), (
                f"{type(dialog).__name__}: {type(child).__name__} overflows"
            )
            assert geometry.bottom() <= dialog.height()
        dialog.close()


def test_every_card_pays_for_the_stylesheet_inset(app, themed):
    """The border and padding live inside the card, so the height a card
    declares has to be its layout's needs plus that frame. On Windows the
    larger font made two pixels of shortfall visible as tiles overlapping."""
    themed("dark")
    line = QFontMetricsF(app.font()).height()
    assert line > 0

    poster = PosterCard(MediaCard(key="movie:1", title="A Film"))
    poster.ensurePolished()
    poster.layout().activate()
    assert poster.height() >= poster.layout().sizeHint().height() + 2 * dc.CARD_INSET
    assert poster.height() >= poster.cover.height() + 2 * dc.CARD_INSET + line
    poster.close()

    browse = BrowseCard(_game())
    browse.resize(240, browse.height())
    browse.show()
    browse.ensurePolished()
    app.processEvents()
    assert browse.height() >= browse.layout().sizeHint().height() + 2 * dc.CARD_INSET
    assert browse.height() >= browse.cover.height() + 2 * dc.CARD_INSET + line
    browse.close()

    library = LibraryCard(_entry())
    library.resize(240, library.height())
    library.show()
    library.ensurePolished()
    library.layout().activate()
    app.processEvents()
    assert library.minimumHeight() >= (
        library.layout().sizeHint().height() + 2 * dc.CARD_INSET
    )
    library.close()


def test_a_card_fits_its_own_box_at_900_px(app, themed):
    themed("dark")
    card = BrowseCard(_game())
    card.resize(240, card.height())
    card.show()
    app.processEvents()
    left = dc.CARD_PADDING
    for child in card.findChildren(QWidget):
        if child.parent() is not card:
            continue
        geometry = child.geometry()
        assert geometry.left() >= 0
        assert geometry.right() <= card.width()
        assert geometry.width() >= 1
    assert card.cover.width() <= card.width() - 2 * left
    card.close()
