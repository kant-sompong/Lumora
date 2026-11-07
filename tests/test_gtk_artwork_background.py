"""The GTK artwork backdrop: the preference off means nothing is painted."""

from __future__ import annotations

import pytest

gi = pytest.importorskip("gi")

try:
    gi.require_version("Gtk", "3.0")
    from gi.repository import GdkPixbuf, Gtk
except (ValueError, ImportError):  # pragma: no cover - GTK 3 typelib missing
    pytest.skip("GTK 3 is not installed", allow_module_level=True)

from lgl import movies_ui, view_prefs  # noqa: E402


class Store(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)

    def set(self, key, value):
        self[key] = value


def test_the_setting_off_paints_nothing():
    window = Gtk.OffscreenWindow()
    backdrop = movies_ui.ArtworkBackdrop(window, Store({view_prefs.ARTWORK: False}))
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 8, 8)
    pixbuf.fill(0xFFFFFF)

    backdrop.show_pixbuf("cover-white.png", pixbuf)

    assert backdrop.pixbuf is None
    assert backdrop.alpha == 0.0


def test_clearing_keeps_the_page_plain():
    window = Gtk.OffscreenWindow()
    backdrop = movies_ui.ArtworkBackdrop(window, Store({view_prefs.ARTWORK: True}))
    backdrop.pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 8, 8)
    backdrop.alpha = 1.0

    backdrop.clear()

    assert backdrop.pixbuf is None
    assert backdrop.alpha == 0.0
