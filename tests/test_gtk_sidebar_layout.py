"""The Linux sidebar keeps its 72 px shape and every label readable.

The rail is built by the real window class on a stub owner: the widget code and
the stylesheet are what is under test, not the archive the window would load.
"""

from __future__ import annotations

import time
import types
from pathlib import Path

import pytest

gi = pytest.importorskip("gi")

try:
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, Gtk
except (ValueError, ImportError):  # pragma: no cover - GTK 3 typelib missing
    pytest.skip("GTK 3 is not installed", allow_module_level=True)

from lgl import nav_layout, ui


def has_display() -> bool:
    return bool(Gtk.init_check()[0])


needs_display = pytest.mark.skipif(not has_display(), reason="no display for GTK")

WIDTHS = (1000, 1280, 1920)


@pytest.fixture(scope="module")
def display():
    yield


class Owner:
    """Just enough window for the rail's own methods."""

    def __init__(self) -> None:
        self._rail_buttons: dict[str, Gtk.Button] = {}
        self._current = "browse"
        self._media_button = None
        self._badge = None

    def active_view(self) -> str:
        return self._current

    def _apply_view(self, name: str) -> None:
        self._current = name

    def set_downloads_visible(self, visible: bool) -> None:
        return None


def rail_owner() -> types.SimpleNamespace:
    owner = types.SimpleNamespace(
        _rail_buttons={},
        _current="browse",
        _media_button=None,
        _badge=None,
    )
    owner.active_view = lambda: owner._current
    owner._apply_view = lambda name: None
    owner.set_downloads_visible = lambda visible: None
    for name in ("_build_rail", "_rail_button", "_sync_rail"):
        setattr(owner, name, types.MethodType(getattr(ui.MainWindow, name), owner))
    return owner


@pytest.fixture(scope="module", autouse=True)
def stylesheet(display):
    """The real sheet: the rail's 72 px shape comes from tokens and rules."""
    css = Path(__file__).resolve().parent.parent / "lgl" / "style.css"
    provider = Gtk.CssProvider()
    provider.load_from_path(str(css))
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    yield provider


def pump(rounds: int = 30) -> None:
    for _ in range(rounds):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        time.sleep(0.005)


@needs_display
@pytest.mark.parametrize("width", WIDTHS)
def test_the_sidebar_labels_fit_at_every_window_width(width):
    owner = rail_owner()
    rail = owner._build_rail()

    # The rail sits beside the page and never stretches, exactly as in the app.
    frame = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
    frame.pack_start(rail, False, False, 0)
    frame.pack_start(Gtk.Box(), True, True, 0)

    window = Gtk.Window()
    window.set_default_size(width, 900)
    window.add(frame)
    window.show_all()
    pump()

    assert rail.get_allocated_width() == 72

    buttons = [child for child in rail.get_children() if isinstance(child, Gtk.Button)]
    assert len(buttons) == len(nav_layout.RAIL) + len(nav_layout.RAIL_BOTTOM)

    for button in buttons:
        assert button.get_allocated_height() >= 56
        labels = [
            widget
            for widget in walk(button)
            if isinstance(widget, Gtk.Label) and widget.get_style_context().has_class("rail-label")
        ]
        assert labels, "a rail button lost its label"
        for label in labels:
            assert label.get_allocated_width() <= button.get_allocated_width()
            assert not label.get_layout().is_ellipsized()
            assert label.get_layout().get_pixel_size()[0] <= button.get_allocated_width()

    window.destroy()


def walk(widget):
    yield widget
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            yield from walk(child)
