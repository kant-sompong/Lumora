"""GTK home shelves and card sizes.

These skip themselves when GTK 3 or a display is missing, so the suite stays
green on a headless machine; CI's Linux job installs Xvfb and runs them.
"""

from __future__ import annotations

import pytest

gi = pytest.importorskip("gi")

try:
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
except (ValueError, ImportError):  # pragma: no cover - GTK 3 typelib missing
    pytest.skip("GTK 3 is not installed", allow_module_level=True)

from lgl import gtk_shelves, home_layout, shelves, ui  # noqa: E402


class Store(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)

    def set(self, key, value):
        self[key] = value

    def save(self):
        return True


@pytest.fixture(scope="module")
def display():
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    yield


def test_the_three_cover_sizes_grow_in_order(display):
    ui.set_compact_layout(False)
    assert ui.card_cover_size("large")[0] > ui.card_cover_size("poster")[0]
    assert ui.card_cover_size("poster")[0] > ui.card_cover_size("compact")[0]
    assert home_layout.LAYOUTS == (home_layout.POSTER, home_layout.LARGE, home_layout.COMPACT)
    assert home_layout.scale("large") > home_layout.scale("poster") > home_layout.scale("compact")


def test_compact_mode_keeps_its_own_cover_size(display):
    ui.set_compact_layout(True)
    try:
        assert ui.card_cover_size("large") == (118, 177)
    finally:
        ui.set_compact_layout(False)


def test_the_shelves_editor_adds_and_removes(display):
    settings = Store()
    dialog = gtk_shelves.ShelvesDialog(settings)
    try:
        assert dialog.add_shelf("Co-op night", "rpg") is True
        assert [row["name"] for row in shelves.shelves(settings)] == ["Co-op night"]
        assert dialog.list.get_row_at_index(0) is not None
        dialog.list.select_row(dialog.list.get_row_at_index(0))
        assert dialog.remove_selected() is True
        assert shelves.shelves(settings) == []
    finally:
        dialog.destroy()


def test_a_shelf_without_a_name_is_refused(display):
    settings = Store()
    dialog = gtk_shelves.ShelvesDialog(settings)
    try:
        assert dialog.add_shelf("", "rpg") is False
        assert shelves.shelves(settings) == []
    finally:
        dialog.destroy()
