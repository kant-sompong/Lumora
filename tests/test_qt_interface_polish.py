"""Tests for the Qt interface polish: palette, compact, accent, transitions.

Everything here builds the real window (offscreen), shows it, and grabs the
rendered pixels to ``artifacts/`` so a change that never reaches a widget fails
here rather than in a screenshot review.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtWidgets")
pytest.importorskip("PySide6.QtTest")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QColor, QKeySequence  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lgl import palette as palette_lib  # noqa: E402
from lgl import qt_main, qt_settings, qt_widgets, view_prefs  # noqa: E402
from lgl.library_store import LibraryEntry  # noqa: E402
from lgl.qt_settings import SettingsDialog  # noqa: E402
from lgl.settings import Settings  # noqa: E402

ARTIFACTS = Path(__file__).resolve().parents[1] / "artifacts"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def pump(app: QApplication, seconds: float = 0.2) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


@pytest.fixture
def settings(tmp_path):
    return Settings(tmp_path / "config.json", keyring=False)


@pytest.fixture
def window(app, settings, tmp_path):
    qt_main.apply_theme(app, "dark")
    window = qt_main.Window(None, None, None, tmp_path, settings)
    window.resize(1280, 820)
    window.show()
    pump(app)
    yield window
    window.close()
    window.deleteLater()
    pump(app, 0.05)


def grab(widget, name: str) -> Path:
    """Grab the rendered widget to ``artifacts/<name>`` and prove it has pixels."""
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    path = ARTIFACTS / name
    assert widget.isVisible(), f"{name}: the widget was never shown"
    assert widget.grab().save(str(path)), f"could not save {path}"
    assert path.stat().st_size > 2000, f"{path} is empty"
    return path


def rule(sheet: str, selector: str) -> str:
    """The body of the QSS rule for ``selector``, or an empty string."""
    lines = sheet.splitlines()
    for index, line in enumerate(lines):
        text = line.strip()
        if not text.startswith(selector + " {"):
            continue
        tail = text[len(selector) + 2:].strip()
        if "}" in tail:
            return tail.strip("} ")
        body: list[str] = []
        for follow in lines[index + 1:]:
            if "}" in follow:
                body.append(follow.split("}")[0])
                break
            body.append(follow)
        return "\n".join(body).strip()
    return ""


# --------------------------------------------------------------------------
# command palette
# --------------------------------------------------------------------------


def test_ctrl_k_opens_a_shown_palette_window(app, window):
    dialog = window.show_palette()
    pump(app)
    assert dialog.isVisible()
    assert len(dialog._results) == palette_lib.MAX_RESULTS
    assert grab(dialog, "qt-palette.png").is_file()
    assert dialog in window.findChildren(type(dialog))
    dialog.close()


def test_typing_filters_and_arrows_move_the_selection(app, window):
    dialog = window.show_palette()
    pump(app)
    QTest.keyClicks(dialog.field, "libr")
    app.processEvents()
    assert dialog.current_command() is not None
    assert dialog.current_command().key == "page.library"
    assert len(dialog._results) < palette_lib.MAX_RESULTS

    # Clear the query so there is more than one row to move between.
    dialog.field.clear()
    app.processEvents()
    assert len(dialog._results) == palette_lib.MAX_RESULTS
    first = dialog.current_command()
    assert first is not None

    QTest.keyClick(dialog.field, Qt.Key_Down)
    app.processEvents()
    second = dialog.current_command()
    assert second is not None and second.key != first.key

    QTest.keyClick(dialog.field, Qt.Key_Up)
    app.processEvents()
    assert dialog.current_command().key == first.key


def test_enter_runs_the_highlighted_command(app, window):
    dialog = window.show_palette()
    pump(app)
    dialog.field.setText("movies")
    app.processEvents()
    assert dialog.current_command().key == "page.media"
    QTest.keyClick(dialog.field, Qt.Key_Enter)
    pump(app, 0.05)
    assert not dialog.isVisible()
    assert window._current == "media"


def test_escape_closes_without_running_anything(app, window):
    window.show_view("browse")
    pump(app, 0.05)
    dialog = window.show_palette()
    pump(app)
    dialog.field.setText("settings")
    QTest.keyClick(dialog.field, Qt.Key_Escape)
    pump(app, 0.05)
    assert not dialog.isVisible()
    assert window._current == "browse"


def test_palette_dispatches_a_settings_page(app, window):
    opened: list[str] = []
    window.show_settings = lambda tab="general": opened.append(tab)  # type: ignore[assignment]
    dialog = window.show_palette()
    pump(app)
    dialog.field.setText("locations")
    assert dialog.current_command().key == "settings.locations"
    dialog.run_current()
    assert opened == ["locations"]


def test_the_menu_has_a_palette_entry(app, window):
    menu = window._build_menu()
    action = next(
        item for item in menu.actions() if item.text().startswith("Command palette")
    )
    assert action.shortcut() == QKeySequence("Ctrl+K")
    action.trigger()
    pump(app, 0.05)
    assert window._palette is not None and window._palette.isVisible()
    window._palette.close()
    menu.deleteLater()


# --------------------------------------------------------------------------
# compact mode
# --------------------------------------------------------------------------


def _entry(tmp_path: Path) -> LibraryEntry:
    folder = tmp_path / "alpha"
    folder.mkdir(exist_ok=True)
    return LibraryEntry(
        identifier="alpha", title="Alpha", year="1999", size=4096,
        folder=str(folder), status="ready",
    )


def test_compact_mode_shrinks_and_puts_everything_back(app, window, settings, tmp_path):
    window.library.set_entries([_entry(tmp_path)])
    window.show_view("library")
    window.library._view = "list"
    window.library._render()
    pump(app)

    roomy_header = window._header.height()
    roomy_gap = window.library.grid.horizontalSpacing()
    roomy_margins = window.library._bar.contentsMargins()
    roomy_row = window.library.list_column.itemAt(0).widget().sizeHint().height()
    assert roomy_header == qt_main.HEADER_HEIGHT

    view_prefs.set_compact(settings, True)
    window.apply_interface_prefs()
    pump(app)

    assert window._header.height() == qt_main.COMPACT_HEADER_HEIGHT
    assert window.library.grid.horizontalSpacing() < roomy_gap
    assert window.library._bar.contentsMargins().left() < roomy_margins.left()
    compact_row = window.library.list_column.itemAt(0).widget().sizeHint().height()
    assert compact_row < roomy_row
    assert "compact density" in QApplication.instance().styleSheet()
    assert grab(window, "qt-compact.png").is_file()

    view_prefs.set_compact(settings, False)
    window.apply_interface_prefs()
    pump(app)

    assert window._header.height() == roomy_header
    assert window.library.grid.horizontalSpacing() == roomy_gap
    assert window.library._bar.contentsMargins() == roomy_margins
    assert window.library.list_column.itemAt(0).widget().sizeHint().height() == roomy_row
    assert "compact density" not in QApplication.instance().styleSheet()


# --------------------------------------------------------------------------
# accent colour
# --------------------------------------------------------------------------


class _PickViolet:
    """Stand-in for QColorDialog: always answers violet."""

    @staticmethod
    def getColor(*_args, **_kwargs):
        return QColor("#8b5cf6")


def test_accent_is_applied_persisted_and_reset(app, window, settings, monkeypatch):
    monkeypatch.setattr(qt_settings, "QColorDialog", _PickViolet)
    dialog = SettingsDialog(settings, window)
    dialog.show_tab("general")
    dialog.show()
    pump(app)
    assert view_prefs.accent(settings) == view_prefs.DEFAULT_ACCENT

    dialog._pick_accent()
    pump(app)

    assert view_prefs.accent(settings) == "#8b5cf6"
    assert "#8b5cf6" in QApplication.instance().styleSheet()
    assert qt_widgets.THEME_STATE["palette"]["accent"] == "#8b5cf6"  # type: ignore[index]
    # A restart reads the same value back from the settings file.
    assert Settings(settings.path, keyring=False).get(view_prefs.ACCENT) == "#8b5cf6"
    assert grab(dialog, "qt-accent.png").is_file()

    dialog._reset_accent()
    pump(app)
    assert view_prefs.accent(settings) == view_prefs.DEFAULT_ACCENT
    assert view_prefs.DEFAULT_ACCENT in QApplication.instance().styleSheet()
    dialog.close()


# --------------------------------------------------------------------------
# page transitions
# --------------------------------------------------------------------------


def test_transitions_fade_then_switch_instantly(app, window, settings):
    view_prefs.set_transitions(settings, "subtle")
    window.apply_interface_prefs()
    assert window.transition_duration() == qt_main.TRANSITION_MS

    window.show_view("browse")
    page = window.stack.currentWidget()
    effect = page.graphicsEffect()
    assert effect is not None, "the page was swapped without a fade"
    assert window._transition_anim is not None
    assert window._transition_anim.duration() == qt_main.TRANSITION_MS
    assert grab(window, "qt-transition.png").is_file()

    pump(app, 0.6)
    assert page.graphicsEffect() is None, "the fade effect was never removed"

    view_prefs.set_transitions(settings, "off")
    window.apply_interface_prefs()
    assert window.transition_duration() == 0
    window.show_view("library")
    assert window.stack.currentWidget() is window.library
    assert window.library.graphicsEffect() is None


# --------------------------------------------------------------------------
# visual defects found while reviewing the polish branch
# --------------------------------------------------------------------------


def runs_of(columns: list[int], join: int = 3) -> list[tuple[int, int]]:
    """Group ink columns into runs, tolerating ``join`` px inside a glyph run."""
    runs: list[list[int]] = []
    for x in columns:
        if runs and x - runs[-1][1] <= join:
            runs[-1][1] = x
        else:
            runs.append([x, x])
    return [(start, end) for start, end in runs]


def icon_to_text_gap(window):
    """Measure the icon and the placeholder in the rendered window.

    Returns ``(icon_run, text_run, gap)`` in field coordinates: the number of
    pixels between the magnifier's right edge and the first text pixel.
    """
    pill = window.library._search_pill
    window.library.search.clear()
    application = QApplication.instance()
    application.processEvents()
    assert pill.width() > 100, f"the search pill was never laid out ({pill.width()}px)"
    image = window.grab().toImage()
    origin = pill.mapTo(window, QPoint(0, 0))
    left, top = origin.x(), origin.y()
    width, height = pill.width(), pill.height()
    background = image.pixelColor(left + width - 14, top + height // 2).lightness()
    columns: list[int] = []
    for x in range(left + 2, left + width - 2):
        for y in range(top + 5, top + height - 5):
            if abs(image.pixelColor(x, y).lightness() - background) > 25:
                columns.append(x - left)
                break
    runs = runs_of(columns)
    assert len(runs) >= 2, f"no icon and text found in the search pill: runs={runs}"
    icon, text = runs[0], runs[1]
    return icon, text, text[0] - icon[1] - 1


def test_library_search_icon_and_placeholder_sit_together(app, window, settings):
    """Room to the right of the magnifier is 8px, not a 40px hole."""
    window.show_view("library")
    pump(app)
    icon, text, roomy_gap = icon_to_text_gap(window)
    view_prefs.set_compact(settings, True)
    window.apply_interface_prefs()
    pump(app)
    icon, text, compact_gap = icon_to_text_gap(window)
    assert roomy_gap <= 8, f"roomy gap is {roomy_gap}px (icon {icon}, text {text})"
    assert compact_gap <= 8, f"compact gap is {compact_gap}px (icon {icon}, text {text})"


def test_compact_resizes_grid_cards_already_drawn(app, window, settings, tmp_path):
    """Toggling compact in grid view must resize the cards, not just the gaps."""
    window.library.set_entries([_entry(tmp_path)])
    window.show_view("library")
    window.library._view = "grid"
    window.library._render()
    pump(app)
    card = next(iter(window.library._cards.values()))
    roomy = (card.width(), card.height(), card.maximumWidth())

    view_prefs.set_compact(settings, True)
    window.apply_interface_prefs()
    pump(app)
    card = next(iter(window.library._cards.values()))
    compact = (card.width(), card.height(), card.maximumWidth())

    assert compact[2] < roomy[2], f"card width did not shrink: {roomy} -> {compact}"
    assert compact[0] < roomy[0], f"card is not narrower: {roomy} -> {compact}"


def test_compact_library_search_keeps_the_icon_and_the_pill(app, window, settings):
    """The library search field is an icon inside a pill, in compact mode too."""
    view_prefs.set_compact(settings, True)
    window.apply_interface_prefs()
    pump(app)
    sheet = QApplication.instance().styleSheet()
    compact_block = sheet.split("compact density", 1)[1]
    assert "border-radius" in rule(compact_block, "QFrame#librarySearchPill")
    assert "transparent" in rule(sheet, "QLineEdit#librarySearch")
    assert window.library._search_pill.height() == 32


def _many_entries(tmp_path, count: int = 8) -> list[LibraryEntry]:
    made = []
    for index in range(count):
        folder = tmp_path / f"game-{index}"
        folder.mkdir(exist_ok=True)
        made.append(LibraryEntry(
            identifier=f"game-{index}", title=f"Game {index}", year="1999",
            size=1024 + index, folder=str(folder), status="ready",
        ))
    return made


def visible_columns(window) -> int:
    """How many distinct card columns the library grid actually placed."""
    return len({card.x() for card in window.library._cards.values()})


def test_compact_grid_fits_more_columns_at_the_same_width(app, window, settings, tmp_path):
    """Compact mode must pack more tiles per row, not just smaller ones."""
    window.library.set_entries(_many_entries(tmp_path))
    window.show_view("library")
    window.library._view = "grid"
    window.library._render()
    pump(app)
    roomy = visible_columns(window)

    view_prefs.set_compact(settings, True)
    window.apply_interface_prefs()
    pump(app)
    compact = visible_columns(window)

    assert compact >= 6, f"only {compact} columns at 1280px (roomy had {roomy})"
    assert compact > roomy, f"compact {compact} is not denser than roomy {roomy}"


def test_switches_and_radios_are_painted_shapes_in_the_accent(app, window, settings):
    """The toggles are a track and a knob, and they take the chosen accent."""
    from lgl.qt_settings import RadioDot, Switch

    view_prefs.set_accent(settings, "#8b5cf6")
    window.apply_interface_prefs()
    pump(app)
    palette = qt_widgets.THEME_STATE["palette"]
    accent_fill = QColor(palette["accent_fill"])

    switch = Switch(True)
    switch.show()
    pump(app, 0.05)
    image = switch.grab().toImage()
    corner = image.pixelColor(1, 1)
    track = image.pixelColor(6, 10)
    knob = image.pixelColor(28, 10)
    assert track.alpha() > 0 and track != corner, "the switch is painted as a filled square"
    assert knob != track, "the switch has no knob"
    assert (track.red(), track.green(), track.blue()) == (
        accent_fill.red(), accent_fill.green(), accent_fill.blue()
    ), f"the track is not the accent: {track.name()} vs {accent_fill.name()}"

    dot = RadioDot(True)
    dot.show()
    pump(app, 0.05)
    centre = dot.grab().toImage().pixelColor(10, 10)
    assert (centre.red(), centre.green(), centre.blue()) == (
        accent_fill.red(), accent_fill.green(), accent_fill.blue()
    ), f"the radio dot is not the accent: {centre.name()}"

    # The spin box buttons used to clip, and then hide, their arrows entirely.
    spin = qt_settings.SpinBox()
    spin.setRange(0, 99)
    spin.setValue(30)
    spin.setFixedSize(84, 30)
    spin.show()
    pump(app, 0.05)
    shot = spin.grab().toImage()
    button = shot.pixelColor(shot.width() - 3, shot.height() // 2).lightness()
    for top in (0, shot.height() // 2):
        arrow_pixels = sum(
            1
            for x in range(shot.width() - 22, shot.width() - 2)
            for y in range(top + 2, top + shot.height() // 2 - 2)
            if abs(shot.pixelColor(x, y).lightness() - button) > 20
        )
        assert arrow_pixels > 10, (
            f"no chevron in the spin box button at y={top}: {arrow_pixels} px"
        )


def test_custom_accent_reaches_tabs_and_list_selection(app, window, settings):
    """A custom accent must not leave the tabs or the palette rows on the old one."""
    view_prefs.set_accent(settings, "#8b5cf6")
    window.apply_interface_prefs()
    pump(app)
    sheet = QApplication.instance().styleSheet()
    violet = "139, 92, 246"
    assert violet in rule(sheet, "QPushButton#sheetTab:checked")
    assert violet in rule(sheet, "QListWidget::item:selected")
