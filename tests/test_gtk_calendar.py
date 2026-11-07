import time

import pytest

gi = pytest.importorskip("gi")
try:
    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk
except (ValueError, ImportError):  # pragma: no cover
    pytest.skip("GTK 3 is not installed", allow_module_level=True)

from lgl import anime_calendar as cal
from lgl import release_feed as feed

NOW = 1_800_000_000.0


class Memory:
    def __init__(self):
        self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


def spin_until(condition, timeout=5.0):
    """Spin the main loop until ``condition()`` is true, or ``timeout`` passes.

    The window fills itself from a worker thread through ``GLib.idle_add``, so a
    fixed number of iterations can finish before that idle callback arrives.
    """
    context = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        while context.iteration(False):
            pass
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def labels_in(widget):
    """Every label text in ``widget``'s tree."""
    labels = []

    def walk(node):
        if isinstance(node, Gtk.Label):
            labels.append(node.get_text())
        if isinstance(node, Gtk.Container):
            for child in node.get_children():
                walk(child)

    walk(widget)
    return labels


def wait_for_text(window, text, timeout=5.0):
    """Wait for the background load to put a label containing ``text`` on screen."""
    return spin_until(
        lambda: any(text in label for label in labels_in(window)), timeout
    )


def test_calendar_imports_next_to_gtk3():
    from lgl import gtk_calendar

    assert gtk_calendar.Gtk.get_major_version() == 3


def test_calendar_shows_days_and_follows(monkeypatch):
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    from lgl import gtk_calendar

    result = feed.FeedResult(entries=[
        cal.Entry("anilist:1", "Alpha", "3", NOW + 100),
        cal.Entry("tmdb:movie:2", "Film", "", NOW + 90000, date_only=True),
    ], fetched_at=NOW)
    monkeypatch.setattr(feed, "load_feed", lambda *a, **k: result)
    monkeypatch.setattr(feed, "tmdb_getter", lambda settings=None: None)
    settings = Memory()
    window = gtk_calendar.CalendarWindow(settings)
    assert wait_for_text(window, "Alpha — 3"), "the feed never reached the window"
    labels = labels_in(window)
    assert any("Film" in text for text in labels)
    toggles = []

    def find(widget):
        if isinstance(widget, Gtk.ToggleButton):
            toggles.append(widget)
        if isinstance(widget, Gtk.Container):
            for child in widget.get_children():
                find(child)

    find(window)
    toggles[0].set_active(True)
    assert cal.follows(settings) == ["anilist:1"]
    window.destroy()
