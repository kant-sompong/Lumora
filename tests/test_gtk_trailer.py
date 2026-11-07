"""The GTK detail page's trailer behaviour; skips without GTK 3 or a display."""

from __future__ import annotations

import pytest

gi = pytest.importorskip("gi")

try:
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
except (ValueError, ImportError):  # pragma: no cover - GTK 3 typelib missing
    pytest.skip("GTK 3 is not installed", allow_module_level=True)

from lgl import movies_ui, trailer_data  # noqa: E402

URL = "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"
TRAILER = {"key": "dQw4w9WgXcQ", "name": "Official Trailer", "type": "Trailer", "official": True}


class Settings:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def get(self, key, default=None):
        return self.values.get(key, default)


class Dummy:
    """Just the pieces ``MoviesWindow``'s trailer methods touch."""

    def __init__(self, settings=None):
        self._detail_token = 1
        self._settings = settings or Settings()
        self._detail_state = {"trailer_button": Gtk.Button(label="Trailer")}
        self._trailer_note = Gtk.Label(label="")
        self.opened: list[str] = []
        self.play_calls: list[dict] = []

    def _open_external(self, url):
        self.opened.append(url)

    def _play_trailer(self, **kwargs):
        self.play_calls.append(kwargs)


@pytest.fixture(scope="module", autouse=True)
def display():
    if not Gtk.init_check()[0]:
        pytest.skip("no display")


def test_the_fallback_offers_a_browser_button_and_opens_nothing():
    dummy = Dummy()
    box = movies_ui.MoviesWindow._trailer_error_box(dummy, URL)

    texts = [child.get_text() for child in box.get_children() if isinstance(child, Gtk.Label)]
    assert any("no WebKitGTK" in text for text in texts)
    buttons = [child for child in box.get_children() if isinstance(child, Gtk.Button)]
    assert [button.get_label() for button in buttons] == ["Open trailer in your browser"]
    assert dummy.opened == []

    buttons[0].emit("clicked")
    assert dummy.opened == [URL]


def test_the_button_is_shown_when_a_trailer_exists_and_autoplay_is_off():
    dummy = Dummy()
    movies_ui.MoviesWindow._apply_trailer(
        dummy, 1, trailer_data.TrailerResult(trailer=TRAILER)
    )

    assert dummy._detail_state["trailer_button"].get_visible() is True
    assert dummy.play_calls == []


def test_no_trailer_keeps_the_button_hidden():
    dummy = Dummy()
    movies_ui.MoviesWindow._apply_trailer(dummy, 1, trailer_data.TrailerResult())
    assert dummy._detail_state["trailer_button"].get_visible() is False
    assert dummy._trailer_note.get_visible() is False


def test_a_failed_lookup_shows_one_dim_line():
    dummy = Dummy()
    movies_ui.MoviesWindow._apply_trailer(
        dummy, 1,
        trailer_data.TrailerResult(error="TMDB (/movie/603/videos): offline"),
    )

    assert dummy._detail_state["trailer_button"].get_visible() is False
    assert dummy._trailer_note.get_visible() is True
    assert dummy._trailer_note.get_text() == "Trailer not available"


def test_autoplay_on_starts_the_trailer_muted():
    dummy = Dummy(Settings({"trailer_autoplay": True}))
    movies_ui.MoviesWindow._apply_trailer(
        dummy, 1, trailer_data.TrailerResult(trailer=TRAILER)
    )
    assert dummy.play_calls == [{"autoplay": True, "muted": True}]
