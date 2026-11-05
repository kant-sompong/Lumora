"""Linux layout regressions that only appear once the window is allocated."""

from __future__ import annotations

import time

import pytest

gi = pytest.importorskip("gi")

try:
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
except (ValueError, ImportError):  # pragma: no cover - GTK 3 typelib missing
    pytest.skip("GTK 3 is not installed", allow_module_level=True)


@pytest.fixture(scope="module")
def display():
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    yield


def pump(rounds: int = 15) -> None:
    """Let GTK finish allocating the way the real main loop would."""
    for _ in range(rounds):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        time.sleep(0.01)


def walk(widget):
    yield widget
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            yield from walk(child)


def test_library_tiles_keep_their_own_height_when_the_grid_is_nearly_empty(display):
    from lgl import ui
    from lgl.library_store import LibraryEntry

    window = Gtk.Window()
    window.set_default_size(900, 600)
    flow = Gtk.FlowBox()
    flow.set_selection_mode(Gtk.SelectionMode.SINGLE)
    flow.set_homogeneous(True)
    flow.set_min_children_per_line(2)
    flow.set_max_children_per_line(6)
    # The flow fills the viewport; the tiles must not.
    flow.set_valign(Gtk.Align.FILL)
    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroller.add(flow)
    window.add(scroller)

    class StubView:
        """The tile asks its view for a cover; the grid is not what is under test."""

        def request_cover(self, path, callback):
            return None

    view = StubView()
    tiles = [
        ui.LibraryTile(view, LibraryEntry(identifier=f"game{index}", title=f"Game {index}"))
        for index in range(2)
    ]
    for tile in tiles:
        flow.add(tile)
    window.show_all()
    pump()

    window_height = window.get_allocated_height()
    flow_height = flow.get_allocated_height()
    assert window_height > 400, "test window did not get its size"
    # The flow fills the viewport, as intended.
    assert flow_height > window_height - 40, "the flow itself must keep FILL"
    for tile in tiles:
        assert tile.get_valign() == Gtk.Align.START
        # A FILL tile gets exactly the flow's height; that was the black band.
        assert tile.get_allocated_height() < flow_height - 40, (
            "two games stretched their tiles to the full height of the grid"
        )
    window.destroy()


def test_search_pill_is_hidden_on_every_page_but_browse(display):
    from lgl import ui

    header = Gtk.HeaderBar()
    header.set_show_close_button(False)
    pill = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    pill.get_style_context().add_class("search-pill")
    pill.pack_start(Gtk.SearchEntry(), True, True, 0)
    hint = Gtk.Label(label="Ctrl K")
    pill.pack_end(hint, False, False, 0)
    header.set_custom_title(pill)

    window = Gtk.Window()
    window.set_titlebar(header)
    window.set_default_size(900, 200)
    window.show_all()
    pump()

    assert ui.apply_search_visibility(pill, "archive") is True
    pump()
    assert pill.get_visible() is True
    assert pill.get_mapped() is True
    assert pill.get_allocated_width() > 0

    for view in ("library", "downloads", "media"):
        assert ui.apply_search_visibility(pill, view) is False
        pump()
        assert pill.get_visible() is False
        assert pill.get_mapped() is False, f"the empty capsule stayed on {view}"

    assert ui.apply_search_visibility(None, "library") is False
    window.destroy()


def test_reset_all_filters_sits_alone_without_an_empty_heading(display):
    from lgl import ui

    clicks = []
    footer = ui._filter_footer(lambda: clicks.append("reset"))
    window = Gtk.Window()
    window.add(footer)
    window.show_all()
    pump()

    texts = [widget.get_text() for widget in walk(footer) if isinstance(widget, Gtk.Label)]
    assert "Availability" not in texts, "the Availability heading has no filter under it"
    buttons = [widget for widget in walk(footer) if isinstance(widget, Gtk.Button)]
    reset = [button for button in buttons if button.get_label() == "Reset all filters"]
    assert len(reset) == 1
    reset[0].emit("clicked")
    assert clicks == ["reset"]
    window.destroy()
