"""The Linux navigation: rail, the trimmed menu, the die and the palette.

The window itself is never built here: every check reads the module-level map
that the window is built from, so the test runs without touching the network or
any user data.  The one test that needs a display builds only the die button.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

gi = pytest.importorskip("gi")

try:
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
except (ValueError, ImportError):  # pragma: no cover - GTK 3 typelib missing
    pytest.skip("GTK 3 is not installed", allow_module_level=True)

from lgl import nav_layout, palette, ui  # noqa: E402

needs_display = pytest.mark.skipif(
    not Gtk.init_check()[0], reason="no display"
)


# -- rail -------------------------------------------------------------------
def test_the_rail_is_the_shared_map_in_order():
    entries = ui.rail_entries()
    assert [label for _stack, _icon, label, _tip in entries] == [
        label for _key, label in nav_layout.RAIL
    ]
    assert [stack for stack, _icon, _label, _tip in entries] == [
        "archive", "library", "media", "downloads"
    ]


def test_the_rail_has_no_discover_destination():
    entries = ui.rail_entries()
    assert "Discover" not in [label for *_rest, label, _tip in entries]
    assert "discover" not in [stack for stack, *_rest in entries]


def test_every_rail_entry_names_a_stack_page_and_an_icon():
    for stack, icon, label, tooltip in ui.rail_entries():
        assert stack in _stack_pages(), stack
        assert icon, label
        assert tooltip, label


def _stack_pages() -> set[str]:
    return {"archive", "library", "media", "downloads", "game"}


def test_the_bottom_of_the_rail_holds_settings_and_about():
    assert [label for _key, label in nav_layout.RAIL_BOTTOM] == ["Settings", "About"]
    for key, _label in nav_layout.RAIL_BOTTOM:
        assert key in ui._RAIL_BOTTOM_ICONS


# -- menu -------------------------------------------------------------------
def test_the_menu_keeps_exactly_the_trimmed_entries():
    owner = object.__new__(ui.MainWindow)
    actions = ui.MainWindow._menu_actions(owner)
    assert set(actions) | {"maintenance"} == set(nav_layout.menu_keys())
    for key, callback in actions.items():
        assert callable(callback), key


def test_the_menu_labels_are_the_shared_ones():
    assert nav_layout.menu_labels() == (
        "Command palette…", "Profiles…", "Keyboard shortcuts…", "Tools…",
        "Website Sources…", "Kryoto Catalog…", "Show Download Queue",
        "Maintenance", "Settings…", "About Lumora",
    )


def test_discover_is_not_a_menu_entry_any_more():
    assert "Discover…" not in nav_layout.menu_labels()


# -- search placeholders ----------------------------------------------------
def test_each_page_names_its_own_search():
    assert ui.search_placeholder("archive") == "Search games"
    assert ui.search_placeholder("library") == "Search library"
    assert ui.search_placeholder("media") == "Search movies, TV, anime"


def test_an_unknown_page_falls_back_to_the_collection_search():
    assert ui.search_placeholder("game") == "Search games"
    assert ui.search_placeholder(None) == "Search games"


# -- palette landings -------------------------------------------------------
def test_every_palette_tab_has_somewhere_to_land():
    owner = SimpleNamespace()
    openers = ui.MainWindow._tab_openers(owner)
    for _key, _title, _group, page, tab in palette.TAB_TARGETS:
        assert (page, tab) in openers, f"{page}/{tab} has no landing"


def test_a_tab_command_switches_the_page_then_opens_the_tab():
    seen: list[tuple[str, str]] = []
    owner = SimpleNamespace()
    owner._apply_view = lambda name: seen.append(("view", name))
    owner._tab_openers = lambda _owner: {
        ("library", "favourites"): lambda: seen.append(("tab", "favourites")),
    }

    ui.MainWindow.show_place(owner, "library", "favourites")

    assert seen == [("view", "library"), ("tab", "favourites")]


def test_a_page_command_opens_only_the_page():
    seen: list[str] = []
    owner = SimpleNamespace()
    owner._apply_view = lambda name: seen.append(name)

    ui.MainWindow.show_place(owner, "browse")

    assert seen == ["archive"]


def test_an_unknown_place_is_ignored():
    seen: list[str] = []
    owner = SimpleNamespace()
    owner._apply_view = lambda name: seen.append(name)

    ui.MainWindow.show_place(owner, "discover")

    assert seen == []


# -- the die -----------------------------------------------------------------
@needs_display
def test_the_die_is_drawn_rather_than_picked_from_an_icon_theme():
    import cairo

    window = Gtk.OffscreenWindow()
    area = Gtk.DrawingArea()
    area.set_size_request(18, 18)
    window.add(area)
    window.show_all()

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 18, 18)
    context = cairo.Context(surface)
    assert ui._draw_dice(area, context) is False

    # The die strokes a box and fills five pips, so something is painted.
    surface.flush()
    data = surface.get_data()
    assert any(byte for byte in bytes(data))
    window.destroy()


@needs_display
def test_the_die_button_says_what_it_does():
    owner = object.__new__(ui.MainWindow)
    button = ui.MainWindow._build_dice_button(owner)
    assert button.get_tooltip_text()
    assert button.get_accessible().get_name() == "Surprise me"
    assert button.get_children()
