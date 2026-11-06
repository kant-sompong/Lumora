"""The profile switcher: create, switch, rename and the last-profile guard."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402

from lgl import qt_profiles  # noqa: E402
from lgl.profiles import Profiles  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_creating_switching_and_renaming(tmp_path):
    store = Profiles(tmp_path / "profiles.json")
    dialog = qt_profiles.ProfilesDialog(store)
    assert dialog.list.count() == 1
    assert dialog.create("Second")
    assert dialog.list.count() == 2
    for row in range(dialog.list.count()):
        if dialog.list.item(row).data(256) == "second":
            dialog.list.setCurrentRow(row)
    assert dialog.switch() is True
    assert store.active_id == "second"
    assert dialog.rename("Work") is True
    assert store.active().name == "Work"


def test_the_last_profile_cannot_be_deleted(tmp_path):
    store = Profiles(tmp_path / "profiles.json")
    dialog = qt_profiles.ProfilesDialog(store)
    assert dialog.delete() is False
    assert dialog.list.count() == 1
