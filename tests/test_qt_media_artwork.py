"""Media artwork: gem posters, detail artwork and the poster fallback."""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication

from lgl import qt_media


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def pump(app: QApplication, seconds: float = 0.5) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def page(tmp_path) -> qt_media.MediaPage:
    return qt_media.MediaPage(settings=None, cache_root=tmp_path)


def test_a_gem_card_keeps_the_cover_art_the_api_sent():
    card = qt_media.MediaPage._card_from_gem(
        {"id": 7, "title": "Quiet Gem", "score": 85,
         "poster_url": "https://img.anilist.test/7.jpg"}
    )
    assert card.poster_url == "https://img.anilist.test/7.jpg"
    # The feed has no wide art, so the detail hero uses the poster.
    assert card.backdrop_url == "https://img.anilist.test/7.jpg"
    assert card.kind == "anime"
    assert card.rating == 8.5


def test_a_gem_without_cover_art_is_still_a_card():
    card = qt_media.MediaPage._card_from_gem({"id": 8, "title": "Bare"})
    assert card.poster_url == ""
    assert card.key == "anime:8"


def test_a_home_row_fetches_its_posters_once_its_cards_arrive(app, tmp_path):
    media = page(tmp_path)
    wanted: list[str] = []
    media._load_image = lambda url, key, apply, fallback="": wanted.append(url)

    row = media._home_rows["hidden_gems"]
    row.set_cards([
        qt_media.Card(key="anime:1", title="One", kind="anime",
                      poster_url="https://img.anilist.test/1.jpg"),
    ])
    assert wanted == ["https://img.anilist.test/1.jpg"]


def test_a_detail_card_borrows_artwork_it_arrived_without(tmp_path):
    media = page(tmp_path)
    full = qt_media.Card(
        key="movie:9", title="Full", kind="movie",
        poster_url="https://img.test/p.jpg", backdrop_url="https://img.test/b.jpg",
        overview="A film.", year="2020",
    )
    media._pages_bits["movies"][1].set_cards([full])

    bare = qt_media.Card(key="movie:9", title="Full", kind="movie")
    filled = media._fullest_card(bare)
    assert filled.backdrop_url == "https://img.test/b.jpg"
    assert filled.poster_url == "https://img.test/p.jpg"
    assert filled.overview == "A film."


def test_a_backdrop_that_cannot_load_falls_back_to_the_poster(app, tmp_path, monkeypatch):
    from PySide6.QtCore import QBuffer
    from PySide6.QtGui import QImage

    buffer = QBuffer()
    buffer.open(QBuffer.WriteOnly)
    image = QImage(8, 8, QImage.Format_RGB32)
    image.fill(0xFF336699)
    image.save(buffer, "PNG")
    png = bytes(buffer.data())

    asked: list[str] = []

    def fake(url, cache_root=None, key="", timeout=0.0):
        asked.append(url)
        if "broken" in url:
            raise OSError("no such image")
        return png

    monkeypatch.setattr(qt_media, "cached_image_bytes", fake)

    media = page(tmp_path)
    card = qt_media.Card(
        key="movie:5", title="Fallback", kind="movie",
        poster_url="https://img.test/good.jpg",
        backdrop_url="https://img.test/broken.jpg",
    )
    media.open_card(card)
    pump(app, 1.0)

    assert "https://img.test/broken.jpg" in asked
    assert "https://img.test/good.jpg" in asked
    assert media.detail_hero._pixmap is not None
