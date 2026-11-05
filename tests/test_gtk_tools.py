"""The GTK tools window must load next to the GTK 3 app (no display needed to import)."""

import pytest

gi = pytest.importorskip("gi")

try:
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
except (ValueError, ImportError):  # pragma: no cover - GTK 3 typelib missing
    pytest.skip("GTK 3 is not installed", allow_module_level=True)


def test_gtk_tools_imports_after_gtk3_is_loaded():
    # ui.py loads GTK 3 first; asking for 4.0 afterwards raised ValueError and
    # made Tools... fail with a traceback.
    from lgl import gtk_tools

    assert gtk_tools.Gtk.get_major_version() == 3


def test_tools_window_builds_every_page():
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    from lgl import gtk_tools
    from lgl.settings import Settings

    window = gtk_tools.ToolsWindow(Settings())
    pages = window._stack.get_children()
    assert len(pages) >= 5
    window.destroy()
