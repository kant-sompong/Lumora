"""Shortcut overrides and the editor dialog."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402

from lgl import qt_shortcuts, shortcut_prefs  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class Settings(dict):
    def set(self, key, value):
        self[key] = value

    def save(self):
        return True


def test_an_override_wins_over_the_default(app):
    settings = Settings()
    assert shortcut_prefs.effective(settings, qt_shortcuts.DEFAULTS)["search"] == "Ctrl+F"
    assert shortcut_prefs.set_bindings(settings, {"search": "Ctrl+Shift+F"})
    assert shortcut_prefs.effective(settings, qt_shortcuts.DEFAULTS)["search"] == "Ctrl+Shift+F"


def test_two_actions_may_not_share_a_shortcut(app):
    assert shortcut_prefs.set_bindings(Settings(), {"search": "Ctrl+F", "refresh": "ctrl+f"}) is False


def test_the_dialog_refuses_duplicates_and_saves_a_good_set(app):
    settings = Settings()
    dialog = qt_shortcuts.ShortcutsDialog(settings)
    dialog.fields["refresh"].setText("Ctrl+F")
    assert dialog.save() is False
    assert shortcut_prefs.overrides(settings) == {}

    dialog.fields["refresh"].setText("Ctrl+Shift+R")
    assert dialog.save() is True
    assert shortcut_prefs.binding(settings, "refresh", "Ctrl+R") == "Ctrl+Shift+R"

    dialog.reset()
    assert shortcut_prefs.overrides(settings) == {}
    assert dialog.fields["refresh"].text() == "Ctrl+R"
