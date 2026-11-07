"""The Qt detail page's Trailer button, offscreen, with a stubbed TMDB answer."""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402

from lgl import platform_support, qt_media, tmdb, trailer_data  # noqa: E402

TRAILER = {"key": "dQw4w9WgXcQ", "name": "Official Trailer", "type": "Trailer", "official": True}
EMBED = "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"


class Settings:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def card():
    return qt_media.card_from_tmdb(
        tmdb.MediaItem(
            kind="movie",
            tmdb_id=603,
            title="The Matrix",
            year="1999",
            overview="A hacker learns the truth.",
            rating=8.2,
        )
    )


@pytest.fixture()
def calls(monkeypatch):
    seen: list[tuple[str, str]] = []

    class FakeDialog:
        def __init__(self, url, title, parent=None):
            seen.append((url, title))

        def exec(self):
            return 0

    monkeypatch.setattr(qt_media, "TrailerDialog", FakeDialog)
    return seen


def settle(app, seconds=1.5):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def page_with(app, monkeypatch, result, settings=None, card=None):
    monkeypatch.setattr(trailer_data, "load_trailer", lambda *a, **k: result)
    page = qt_media.MediaPage(settings or Settings())
    page.open_card(card)
    settle(app)
    return page


def test_the_button_appears_but_nothing_plays_without_a_click(app, monkeypatch, calls, card):
    page = page_with(app, monkeypatch, trailer_data.TrailerResult(trailer=TRAILER), card=card)

    assert page.trailer_btn.isHidden() is False
    assert calls == []

    page.trailer_btn.click()

    assert len(calls) == 1
    assert calls[0][0] == EMBED + "?autoplay=1"
    assert calls[0][1] == "The Matrix"


def test_autoplay_on_starts_the_trailer_muted(app, monkeypatch, calls, card):
    page = page_with(
        app, monkeypatch, trailer_data.TrailerResult(trailer=TRAILER),
        settings=Settings({"trailer_autoplay": True}), card=card,
    )

    assert page.trailer_btn.isHidden() is False
    assert calls == [(EMBED + "?autoplay=1&mute=1", "The Matrix")]


def test_no_trailer_hides_the_button(app, monkeypatch, calls, card):
    page = page_with(app, monkeypatch, trailer_data.TrailerResult(), card=card)

    assert page.trailer_btn.isHidden() is True
    assert calls == []


def test_the_trailer_button_sits_with_watch_now_not_beside_back(app, card):
    page = qt_media.MediaPage(Settings())
    row = page.detail_hero.actions

    assert row.indexOf(page.trailer_btn) >= 0
    assert row.indexOf(page.detail_hero.play) >= 0
    assert row.indexOf(page.detail_hero.save) >= 0
    assert page.trailer_btn.parentWidget() is page.detail_hero


def test_a_failed_lookup_says_so_under_the_actions(app, monkeypatch, calls, card):
    result = trailer_data.TrailerResult(error="TMDB (/movie/603/videos): offline")
    page = page_with(app, monkeypatch, result, card=card)

    assert page.trailer_btn.isHidden() is True
    assert page.trailer_note.isHidden() is False
    assert page.trailer_note.text() == "Trailer not available"
    assert calls == []


def test_no_trailer_without_a_failure_stays_quiet(app, monkeypatch, calls, card):
    page = page_with(app, monkeypatch, trailer_data.TrailerResult(), card=card)

    assert page.trailer_btn.isHidden() is True
    assert page.trailer_note.isHidden() is True


def test_the_fallback_dialog_never_opens_a_browser_by_itself(app, monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(platform_support, "open_url", lambda url: opened.append(url))

    dialog = qt_media.TrailerDialog(EMBED, "The Matrix")

    assert opened == []
    assert dialog.windowTitle().startswith("Trailer")
