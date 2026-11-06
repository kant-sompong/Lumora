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

from PySide6.QtCore import Qt  # noqa: E402
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
