"""GTK shelf chips follow the per-section size; skipped without GTK 3."""

from __future__ import annotations

import pytest

gi = pytest.importorskip("gi")

try:
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
except (ValueError, ImportError):  # pragma: no cover - GTK 3 typelib missing
    pytest.skip("GTK 3 is not installed", allow_module_level=True)

from lgl import home_layout  # noqa: E402


class Store(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)

    def set(self, key, value):
        self[key] = value

    def save(self):
        return True


def test_a_shelf_chip_changes_height_with_the_section_size():
    settings = Store()
    layout = home_layout.HomeLayout(settings)
    layout.set_size("continue", "compact")

    short = Gtk.Button(label="Keroro")
    short.set_size_request(-1, home_layout.size_height(layout.size("continue")))
    assert short.get_size_request()[1] == home_layout.size_height("compact")

    layout.set_size("continue", "large")
    tall = Gtk.Button(label="Keroro")
    tall.set_size_request(-1, home_layout.size_height(layout.size("continue")))
    assert tall.get_size_request()[1] > short.get_size_request()[1]


def test_a_compact_section_fits_fewer_titles():
    assert home_layout.shelf_titles("compact") < home_layout.shelf_titles("normal")
