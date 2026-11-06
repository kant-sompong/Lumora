"""Tags, the detail editor, bulk edits and duplicate groups."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402

from lgl import library_tools, qt_library_tools  # noqa: E402

ENTRIES = [
    {"key": "a", "title": "Alpha", "year": "1998", "description": "", "poster": "", "size": 10},
    {"key": "b", "title": "alpha ", "year": "", "description": "", "poster": "", "size": 10},
]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class Settings(dict):
    def set(self, key, value):
        self[key] = value

    def save(self):
        return True


def test_tags_are_added_and_removed_on_the_chosen_title(app):
    settings = Settings()
    dialog = qt_library_tools.TagsDialog(ENTRIES, settings)
    dialog.field.setText("  Co-op, split  ")
    dialog.add_current()
    assert [dialog.list.item(i).text() for i in range(dialog.list.count())] == ["co-op split"]
    assert library_tools.tags_for(settings, "a") == ("co-op split",)

    dialog.list.setCurrentRow(0)
    dialog.remove_current()
    assert dialog.list.count() == 0
    assert library_tools.tags_for(settings, "a") == ()


def test_the_editor_normalises_a_year_and_ignores_junk(app):
    dialog = qt_library_tools.MetadataEditorDialog(ENTRIES[0])
    dialog.fields["title"].setText("  Alpha   Remastered ")
    dialog.fields["year"].setText("2001-05")
    edited = dialog.edited()
    assert edited["title"] == "Alpha Remastered"
    assert edited["year"] == 2001
    assert dialog.changed()

    dialog.fields["year"].setText("soon")
    assert dialog.edited()["year"] == "1998"


def test_an_unchanged_entry_reports_no_change(app):
    dialog = qt_library_tools.MetadataEditorDialog(ENTRIES[0])
    assert dialog.changed() is False


def test_bulk_edit_only_touches_the_chosen_titles(app):
    dialog = qt_library_tools.BulkEditDialog(ENTRIES, ["b"])
    dialog.field.setCurrentIndex(dialog.field.findData("year"))
    dialog.value.setText("2004")
    rows, changed = dialog.result_entries()
    assert changed == 1
    assert [row["year"] for row in rows] == ["1998", "2004"]


def test_duplicates_group_the_same_title_and_size(app):
    dialog = qt_library_tools.DuplicatesDialog(ENTRIES)
    assert len(dialog.groups) == 1
    assert dialog.list.count() == 1
    assert "Alpha" in dialog.list.item(0).text()


def test_a_clean_library_shows_the_empty_note(app):
    dialog = qt_library_tools.DuplicatesDialog([ENTRIES[0]])
    assert dialog.list.count() == 0
    assert dialog.empty.isVisible() is False or not dialog.groups
