"""The GTK Browse filter column: year range and the shared summary line.

These tests need GTK 3 and a display; without either they skip instead of
failing, because a headless machine cannot build or show GTK widgets.
"""

import pytest

gi = pytest.importorskip("gi")

try:
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
except (ValueError, ImportError):  # pragma: no cover - GTK 3 not installed
    pytest.skip("GTK 3 is not installed", allow_module_level=True)

from lgl import browse_filters, ui


def has_display() -> bool:
    return bool(Gtk.init_check()[0])


needs_display = pytest.mark.skipif(not has_display(), reason="no display for GTK")


def test_the_range_and_the_chips_are_one_state():
    for bucket, bounds in browse_filters.YEAR_BUCKETS.items():
        assert browse_filters.bucket_bounds(bucket) == bounds
        assert browse_filters.bucket_for(*bounds) == bucket
    assert browse_filters.bucket_for(1992, 2005) == "all"
    assert browse_filters.bucket_bounds("nonsense") == (None, None)


def test_the_year_field_reads_a_year_and_ignores_junk():
    assert ui._year_value("1992") == 1992
    assert ui._year_value("1992-05-01") == 1992
    assert ui._year_value("") is None
    assert ui._year_value("soon") is None


@needs_display
def test_the_year_row_reports_every_change():
    calls = []
    row, start, end = ui._year_range_row(lambda: calls.append(1))
    window = Gtk.Window()
    window.add(row)
    window.show_all()
    try:
        start.set_text("1992")
        end.set_text("2005")
        start.activate()
        assert calls
        assert (ui._year_value(start.get_text()), ui._year_value(end.get_text())) == (
            1992,
            2005,
        )
    finally:
        window.destroy()


@needs_display
def test_hiding_a_section_survives_show_all():
    section = Gtk.Box()
    window = Gtk.Window()
    window.add(section)
    ui.MainWindow._show_filter_widget(section, False)
    window.show_all()
    assert not section.get_visible()
    ui.MainWindow._show_filter_widget(section, True)
    window.show_all()
    assert section.get_visible()
    window.destroy()
