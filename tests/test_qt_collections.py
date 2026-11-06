"""The collections editor: naming, membership and removal."""

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402

from lgl import qt_collections  # noqa: E402
from lgl.collections import CollectionStore  # noqa: E402

ENTRIES = [
    SimpleNamespace(identifier="a", title="Alpha"),
    SimpleNamespace(identifier="b", title="Beta"),
]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_a_new_collection_starts_empty_and_lists_its_titles(app):
    dialog = qt_collections.CollectionsDialog(CollectionStore(), ENTRIES)
    assert dialog.list.count() == 0
    assert dialog.create("Co-op")
    assert dialog.list.count() == 1
    assert dialog.add("a") == 1
    assert dialog.members.count() == 1
    assert dialog.members.item(0).text() == "Alpha"


def test_renaming_refuses_a_name_already_in_use(app):
    store = CollectionStore()
    dialog = qt_collections.CollectionsDialog(store, ENTRIES)
    dialog.create("First")
    dialog.create("Second")
    assert dialog.rename("First") is False
    assert dialog.rename("Third") is True
    assert [row.name for row in store.all()] == ["First", "Third"]


def test_removing_a_title_and_deleting_a_collection(app):
    store = CollectionStore()
    dialog = qt_collections.CollectionsDialog(store, ENTRIES)
    dialog.create("Keep")
    dialog.add("a")
    dialog.add("b")
    assert dialog.remove("a") == 1
    assert dialog.members.count() == 1
    assert dialog.delete() is True
    assert dialog.list.count() == 0
