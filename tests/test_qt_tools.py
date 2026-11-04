from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication

from lgl.qt_tools import ToolsDialog
from lgl.settings import Settings


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


@pytest.fixture()
def settings(tmp_path):
    return Settings(tmp_path / "settings.json", keyring=lambda *a, **k: None)


def test_dialog_lists_every_section(app, settings):
    dialog = ToolsDialog(settings)
    try:
        titles = [dialog._nav.item(i).text() for i in range(dialog._nav.count())]
        assert "Providers" in titles
        assert "Updates, backup and support" in titles
        assert dialog._stack.count() == dialog._nav.count()
    finally:
        dialog.deleteLater()


def test_toggle_row_changes_settings(app, settings):
    dialog = ToolsDialog(settings)
    try:
        # Drive the action directly: the renderer calls exactly this.
        dialog._apply("notify.episodes", True)
        assert settings.get("notify_episodes") is True
        dialog._apply("notify.episodes", False)
        assert settings.get("notify_episodes") is False
        assert "off" in dialog._status.text()
    finally:
        dialog.deleteLater()


def test_unknown_action_reports_cleanly(app, settings):
    dialog = ToolsDialog(settings)
    try:
        dialog._apply("nonsense", None)
        assert dialog._status.text()
    finally:
        dialog.deleteLater()


def test_backup_and_report_runs(app, settings, tmp_path, monkeypatch):
    from lgl import platform_support

    monkeypatch.setattr(platform_support, "data_dir", lambda *a, **k: tmp_path)
    dialog = ToolsDialog(settings)
    try:
        report = dialog._backup()
        assert Path(report.path).exists()
        dialog._backup_done(report)
        assert "Backup written" in dialog._status.text()
        path = dialog._report()
        assert path.exists()
        dialog._report_done(path)
        assert "Support report saved" in dialog._status.text()
    finally:
        dialog.deleteLater()
