"""The Qt detail hero and episode rows: logos, real wide art, streaming rows.

No test touches the network: images come from a canned PNG, and TMDB is a stub
client.  Everything runs offscreen.
"""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QBuffer  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lgl import qt_media, tmdb  # noqa: E402

POSTER = "https://image.tmdb.org/t/p/w500/poster.jpg"
BACKDROP = "https://image.tmdb.org/t/p/w1280/backdrop.jpg"
LOGO = "https://image.tmdb.org/t/p/w500/logo.png"
STILL = "https://image.tmdb.org/t/p/w780/still.jpg"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def pump(app: QApplication, seconds: float = 1.0) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def png(width: int, height: int, colour: int = 0xFF336699) -> bytes:
    buffer = QBuffer()
    buffer.open(QBuffer.WriteOnly)
    image = QImage(width, height, QImage.Format_RGB32)
    image.fill(colour)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


#: Different sizes let a test say which picture reached the hero.
PICTURES = {
    POSTER: png(100, 150),
    BACKDROP: png(320, 180),
    LOGO: png(400, 120),
    STILL: png(320, 180, 0xFF884422),
}


class Client:
    """A TMDB client that answers from memory."""

    def __init__(self, images: tmdb.MediaImages | None = None,
                 results: list | None = None,
                 season: object | None = None) -> None:
        self._images = images or tmdb.MediaImages()
        self._results = list(results or ())
        self._season = season
        self.asked: list[tuple[str, int]] = []

    @property
    def configured(self) -> bool:
        return True

    def images(self, kind: str, tmdb_id: int) -> tmdb.MediaImages:
        self.asked.append((kind, tmdb_id))
        return self._images

    def search(self, _query: str, *, page: int = 1) -> list:
        return list(self._results)

    def season(self, _tmdb_id: int, _number: int) -> object:
        return self._season


@pytest.fixture(autouse=True)
def clean_state(monkeypatch, tmp_path):
    tmdb._TITLE_BACKDROPS.clear()
    asked: list[str] = []

    def fake(url, cache_root=None, key="", timeout=0.0):
        asked.append(url)
        data = PICTURES.get(url)
        if data is None:
            raise OSError(f"no canned image for {url}")
        return data

    monkeypatch.setattr(qt_media, "cached_image_bytes", fake)
    yield asked
    tmdb._TITLE_BACKDROPS.clear()


def make_page(app: QApplication, tmp_path, client: Client | None = None) -> qt_media.MediaPage:
    media = qt_media.MediaPage(settings=None, cache_root=tmp_path)
    media._tmdb = client
    return media


def movie_card(**kwargs) -> qt_media.Card:
    values = dict(
        key="movie:1", title="Dune", kind="movie", tmdb_id=1,
        poster_url=POSTER, backdrop_url="",
    )
    values.update(kwargs)
    return qt_media.Card(**values)


def anime_card(**kwargs) -> qt_media.Card:
    values = dict(
        key="anime:1", title="Frieren", year="2023", kind="anime",
        poster_url=POSTER, backdrop_url="",
    )
    values.update(kwargs)
    return qt_media.Card(**values)


# -- backdrop ---------------------------------------------------------------


def test_a_card_without_a_list_backdrop_gets_the_images_backdrop(app, tmp_path):
    media = make_page(app, tmp_path, Client(tmdb.MediaImages(backdrop_url=BACKDROP)))

    media.open_card(movie_card())
    pump(app)

    assert media.detail_hero.backdrop_kind == "backdrop"
    assert media.detail_hero._pixmap is not None
    assert media.detail_hero._pixmap.size().width() == 320
    assert "backdrop-images-movie:1" in media._pix


def test_the_images_backdrop_beats_the_list_backdrop(app, tmp_path):
    other = "https://image.tmdb.org/t/p/w1280/list.jpg"
    PICTURES.setdefault(other, png(640, 360, 0xFF112233))
    media = make_page(app, tmp_path, Client(tmdb.MediaImages(backdrop_url=BACKDROP)))

    media.open_card(movie_card(backdrop_url=other))
    pump(app)

    assert media.detail_hero.backdrop_kind == "backdrop"
    assert media.detail_hero._pixmap.size().width() == 320
    assert media._detail_card is not None


def test_an_anime_card_does_not_use_its_poster_when_tmdb_has_art(app, tmp_path):
    client = Client(
        tmdb.MediaImages(backdrop_url=BACKDROP),
        results=[tmdb.MediaItem(kind="tv", tmdb_id=9, title="Frieren", year="2023")],
    )
    media = make_page(app, tmp_path, client)

    media.open_card(anime_card())
    pump(app)

    assert media.detail_hero.backdrop_kind == "backdrop"
    assert media.detail_hero._pixmap.size().width() == 320
    assert client.asked == [("tv", 9)]


def test_the_poster_is_a_marked_fallback(app, tmp_path):
    """No wide art anywhere: the poster is shown, blurred, and said to be one."""
    media = make_page(app, tmp_path, Client())

    media.open_card(movie_card())
    pump(app)

    assert media.detail_hero.backdrop_kind == "fallback"
    assert media.detail_hero._pixmap is not None
    # Blurred and widened, never the poster at its own size.
    assert media.detail_hero._pixmap.size().width() == 1280


# -- logo -------------------------------------------------------------------


def test_the_logo_replaces_the_text_title(app, tmp_path):
    media = make_page(app, tmp_path, Client(tmdb.MediaImages(logo_url=LOGO)))

    media.open_card(movie_card())
    pump(app)

    hero = media.detail_hero
    assert not hero.logo.isHidden()
    assert hero.title.isHidden()
    assert hero.logo.pixmap().height() <= 110
    assert hero.logo.accessibleName() == "Dune"
    assert hero.logo.toolTip() == "Dune"


def test_without_a_logo_the_text_title_stays(app, tmp_path):
    media = make_page(app, tmp_path, Client(tmdb.MediaImages(backdrop_url=BACKDROP)))

    media.open_card(movie_card())
    pump(app)

    hero = media.detail_hero
    assert hero.logo.isHidden()
    assert not hero.title.isHidden()
    assert hero.title.text() == "Dune"


def test_a_logo_that_never_loads_leaves_the_title(app, tmp_path):
    media = make_page(
        app, tmp_path,
        Client(tmdb.MediaImages(logo_url="https://image.tmdb.org/t/p/w500/gone.png")),
    )

    media.open_card(movie_card())
    pump(app)

    assert media.detail_hero.logo.isHidden()
    assert not media.detail_hero.title.isHidden()


def test_a_logo_from_the_previous_title_is_ignored(app, tmp_path):
    client = Client(tmdb.MediaImages(logo_url=LOGO))
    media = make_page(app, tmp_path, client)
    first = movie_card()

    media.open_card(first)
    pump(app)
    assert not media.detail_hero.logo.isHidden()

    client._images = tmdb.MediaImages()
    media.open_card(movie_card(key="movie:2", title="Dune: Part Two", tmdb_id=2))
    pump(app)

    assert media.detail_hero.logo.isHidden()
    assert media.detail_hero.title.text() == "Dune: Part Two"


# -- episode rows -----------------------------------------------------------

EPISODE = tmdb.Episode(
    season=1, number=3, title="The One", air_date="2022-09-01",
    overview="A long description " * 40, still_url=STILL, rating=6.6, runtime=66,
)


def season(*episodes: tmdb.Episode) -> tmdb.Season:
    return tmdb.Season(number=1, episode_count=len(episodes), episodes=episodes)


def test_a_row_shows_the_pill_and_the_facts(app):
    row = qt_media.EpisodeRow(
        3, "The One", "2022-09-01", still_url=STILL, rating=6.6, runtime=66,
        overview="A long description " * 40,
    )

    assert row.rating_pill.text() == "★ 6.6"
    assert not row.rating_pill.isHidden()
    assert row.meta.text() == "66 min · Sep 1, 2022"
    assert row.overview.text().endswith("…")
    assert len(row.overview.text()) < len(row.overview.full_text())
    assert row.still.width() == 200 and row.still.height() == 112


def test_an_unrated_episode_has_no_pill_and_no_runtime(app):
    row = qt_media.EpisodeRow(4, "Bare", "")

    assert row.rating_pill.isHidden()
    assert row.meta.text() == ""
    assert row.meta.isHidden()


def test_the_season_list_builds_streaming_rows(app, tmp_path):
    media = make_page(app, tmp_path, Client(season=season(EPISODE)))
    media._detail_card = movie_card(key="tv:1", kind="tv")

    media._show_season(media._detail_card, 1)
    pump(app)

    assert media.episode_column.count() == 1
    row = media.episode_column.itemAt(0).widget()
    assert isinstance(row, qt_media.EpisodeRow)
    assert row.rating_pill.text() == "★ 6.6"
    assert row.still._pixmap is not None
    assert "still-1-1-3" in media._pix


def test_the_row_fits_a_narrow_window(app, tmp_path):
    media = make_page(app, tmp_path, Client(season=season(EPISODE)))
    media.resize(900, 700)
    media._detail_card = movie_card(key="tv:1", kind="tv")
    media._show_season(media._detail_card, 1)
    media.show()
    pump(app, 0.4)

    row = media.episode_column.itemAt(0).widget()
    assert row.width() <= media.width()
    assert row.still.width() == 200
    assert row.overview.width() > 0
    media.close()


def test_the_attribution_is_on_the_detail_page(app, tmp_path):
    media = make_page(app, tmp_path, Client())

    assert "not endorsed or certified by TMDB" in media.attribution.text()
