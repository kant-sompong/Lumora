"""The Library's right-click game menu, its tab order and the palette Tags row."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton  # noqa: E402

from lgl import collections, nav_layout, palette as palette_lib, profiles  # noqa: E402
from lgl import qt_collections, qt_library, qt_library_tools, qt_palette  # noqa: E402
from lgl.library_store import LibraryEntry  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class Store:
    """The few store methods the menu touches."""

    def __init__(self, entries):
        self._entries = list(entries)

    def entries(self):
        return list(self._entries)

    def toggle_favorite(self, key):
        for item in self._entries:
            if item.identifier == key:
                item.favorite = not item.favorite
        return True

    def find_duplicates(self, **_kwargs):
        return []

    def save(self):
        return True


def game(key="g1", favorite=False):
    return LibraryEntry(identifier=key, title=key.upper(), folder=f"/games/{key}",
                        favorite=favorite)


def page(entries):
    store = Store(entries)
    widget = qt_library.LibraryPage(store, None, None, None)
    widget.set_entries(store.entries())
    return widget, store


def menu_texts(widget, identifier="g1"):
    return [action.text() for action in widget.game_menu(identifier).actions()]


class Opened:
    """A dialog stand-in: records construction, never starts an event loop."""

    calls: list = []

    def __init__(self, *args, **kwargs):
        Opened.calls.append((self.__class__.__name__, args, kwargs))

    def exec(self):
        return 0


@pytest.fixture
def dialogs(monkeypatch):
    Opened.calls = []
    for module, name in (
        (qt_library, "EditDetailsDialog"),
        (qt_library, "DuplicatesDialog"),
        (qt_library_tools, "TagsDialog"),
        (qt_collections, "CollectionsDialog"),
    ):
        monkeypatch.setattr(module, name, type(name, (Opened,), {}))
    # The collection action opens a real store on disk; keep it in memory.
    monkeypatch.setattr(collections, "CollectionStore", lambda *_a, **_k: object())
    monkeypatch.setattr(profiles, "active_file", lambda _name: "/nonexistent/collections.json")
    # The duplicate scan is threaded; run it inline so the test is deterministic.
    monkeypatch.setattr(qt_library, "run_async", lambda work, done, failed=None: done(work()))
    return Opened


def test_the_game_menu_offers_the_six_actions(app):
    widget, _ = page([game()])

    assert menu_texts(widget) == [
        "Edit details…", "Edit tags…", "Add to collection…",
        "Add to favourites", "Find duplicates of this game…", "Select",
    ]
    widget.close()


def test_the_favourite_row_says_what_it_will_do(app):
    widget, _ = page([game(favorite=True)])

    assert "Remove from favourites" in menu_texts(widget)
    assert "Add to favourites" not in menu_texts(widget)
    widget.close()


def test_select_opens_the_game(app):
    widget, _ = page([game()])

    action = next(a for a in widget.game_menu("g1").actions() if a.text() == "Select")
    action.trigger()

    assert widget._selected == "g1"
    widget.close()


def test_edit_details_opens_the_details_dialog(app, dialogs):
    widget, _ = page([game()])

    action = next(a for a in widget.game_menu("g1").actions() if a.text() == "Edit details…")
    action.trigger()

    assert "EditDetailsDialog" in [name for name, _args, _kw in dialogs.calls]
    widget.close()


def test_edit_tags_opens_the_tag_dialog(app, dialogs):
    widget, _ = page([game()])

    action = next(a for a in widget.game_menu("g1").actions() if a.text() == "Edit tags…")
    action.trigger()

    opened = {name: args for name, args, _kw in dialogs.calls}
    assert "TagsDialog" in opened
    assert opened["TagsDialog"][0][0].identifier == "g1"
    widget.close()


def test_add_to_collection_opens_the_collections_dialog(app, dialogs):
    widget, _ = page([game()])

    action = next(
        a for a in widget.game_menu("g1").actions() if a.text() == "Add to collection…"
    )
    action.trigger()

    assert "CollectionsDialog" in [name for name, _args, _kw in dialogs.calls]
    widget.close()


def test_find_duplicates_opens_the_duplicates_dialog(app, dialogs):
    widget, _ = page([game()])

    action = next(
        a for a in widget.game_menu("g1").actions()
        if a.text() == "Find duplicates of this game…"
    )
    action.trigger()

    assert "DuplicatesDialog" in [name for name, _args, _kw in dialogs.calls]
    widget.close()


def test_favourite_flips_and_the_menu_follows(app):
    widget, store = page([game()])

    action = next(
        a for a in widget.game_menu("g1").actions() if a.text() == "Add to favourites"
    )
    action.trigger()

    assert store.entries()[0].favorite is True
    assert "Remove from favourites" in menu_texts(widget)
    widget.close()


def test_an_open_tab_is_rebuilt_when_the_library_changes(app):
    widget, _ = page([game("g1", favorite=True)])
    widget.show_tab("favourites")
    first = widget._tab_hosts["favourites"].layout().itemAt(0).widget()

    widget.set_entries([game("g1", favorite=True), game("g2", favorite=True)])
    second = widget._tab_hosts["favourites"].layout().itemAt(0).widget()

    assert second is not None
    assert second is not first
    widget.close()


def test_the_library_has_no_tags_tab(app):
    assert "tags" not in nav_layout.tab_keys("library")
    assert [label for _key, label in nav_layout.tabs_for("library")] == [
        "Games", "Favourites", "Collections", "Activity", "Statistics",
        "Recently viewed", "Complete the set",
    ]


def test_the_toolbar_has_no_edit_details_button(app):
    widget, _ = page([game()])

    bar = widget._bar
    texts = [
        bar.itemAt(index).widget().text()
        for index in range(bar.count())
        if bar.itemAt(index).widget() is not None
        and hasattr(bar.itemAt(index).widget(), "text")
    ]
    assert "Edit details…" not in texts
    widget.close()


def test_the_more_menu_has_no_find_duplicates(app):
    widget, _ = page([game()])

    widget._more_menu()
    texts = [button.text() for button in widget._more_popover.findChildren(QPushButton)]

    assert "Find duplicates…" not in texts
    assert "Export library…" in texts
    widget.close()


def test_the_library_has_no_search_box_of_its_own(app):
    widget, _ = page([game()])

    # One box filters this page: the one in the window header.
    assert not widget.findChildren(QLineEdit)
    widget.close()


def test_the_page_query_filters_the_games_tab(app):
    widget, _ = page([game("alpha"), game("beta")])

    widget.set_query("alp")

    assert [entry.identifier for entry in widget._shown] == ["alpha"]

    widget.set_query("")

    assert sorted(entry.identifier for entry in widget._shown) == ["alpha", "beta"]
    widget.close()


def test_the_palette_tags_command_opens_the_tag_dialog(app):
    command = next(item for item in palette_lib.commands() if item.key == "tags.edit")

    assert palette_lib.destination(command) == ("action", "tags.edit", "")
    opened = []
    owner = SimpleNamespace(show_tags=lambda: opened.append(True))
    qt_palette.PaletteDialog(owner).run(command)

    assert opened == [True]
