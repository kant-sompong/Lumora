"""Anime title logos on the Qt detail page, sharing the TMDB title lookup.

Everything here runs against a stub client: the real path is exercised - open a
card, run the lookup, load the logo - but no request leaves the process.  The
service layer (``lgl.tmdb``) is the shared, cached one the Linux page uses, and
these tests pin that the Windows page calls it and nothing else.
"""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtGui import QColor, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lgl import qt_media, tmdb  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def clean_title_cache():
    """The title lookup is cached per title; each test starts from nothing."""
    tmdb._TITLE_IMAGES.clear()
    yield
    tmdb._TITLE_IMAGES.clear()


def pump(app: QApplication, predicate=None, seconds: float = 3.0) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        if predicate is not None and predicate():
            return True
        time.sleep(0.01)
    return predicate() if predicate is not None else True


class Hit:
    """One search result, shaped like the shared TMDB item."""

    def __init__(self, title: str, kind: str, year: str, tmdb_id: int) -> None:
        self.title = title
        self.kind = kind
        self.year = year
        self.tmdb_id = tmdb_id


class StubClient:
    """A TMDB client that answers logos per language and records every call."""

    def __init__(self, *, logos: dict[str, str] | None = None,
                 backdrop: str = "", hits: list[Hit] | None = None) -> None:
        self.language = "en-US"
        self.configured = True
        self.logos = logos if logos is not None else {"en": "https://img/logo-en.png"}
        self.backdrop = backdrop
        self.hits = hits or []
        self.image_calls: list[tuple[str, int, str | None]] = []
        self.search_calls: list[str] = []

    def search(self, title: str):
        self.search_calls.append(title)
        return list(self.hits)

    def images(self, kind: str, tmdb_id: int, language: str | None = None):
        self.image_calls.append((kind, int(tmdb_id), language))
        logo = self.logos.get(language or "en") or self.logos.get("en", "")
        return tmdb.MediaImages(logo_url=logo, backdrop_url=self.backdrop)


def _pixmap(colour: int) -> QPixmap:
    pix = QPixmap(8, 4)
    pix.fill(QColor(colour, colour, colour))
    return pix


def make_page(tmp_path, client: StubClient, *, images: list | None = None):
    page = qt_media.MediaPage(settings=None, cache_root=tmp_path)
    page._tmdb = client
    # No artwork network here: every image source answers immediately.
    loaded = images if images is not None else []
    logo = _pixmap(200)

    def load_image(url, key, apply, fallback="", paint_url="", on_fail=None):
        loaded.append((url, key))
        if apply is not None:
            apply(logo)

    page._load_image = load_image
    # Episode, ratings and trailer lookups are other features; keep them quiet.
    page._load_episodes = lambda card: None
    page._load_ratings = lambda card: None
    page._load_trailer = lambda card: None
    return page, loaded


def anime_card(title: str = "Frieren", episodes: int = 28,
               year: str = "2023", media_type: str = "tv") -> qt_media.Card:
    return qt_media.Card(
        key="anime:1", title=title, year=year, kind="anime",
        mal_id=1, episodes=episodes, media_type=media_type,
    )


def hero_shows_logo(page) -> bool:
    hero = page.detail_hero
    return hero._logo_source is not None and hero.title.isHidden()


def hero_shows_title(page) -> bool:
    hero = page.detail_hero
    return hero._logo_source is None and not hero.title.isHidden()


def test_an_anime_series_asks_the_tv_side_and_shows_its_logo(app, tmp_path, monkeypatch):
    monkeypatch.setattr(qt_media.recently_viewed, "record", lambda *a, **k: None)
    monkeypatch.setattr(qt_media.MediaPage, "_logo_language", lambda self: "fr")
    client = StubClient(
        logos={"fr": "https://img/logo-fr.png", "en": "https://img/logo-en.png"},
        hits=[Hit("Frieren", "tv", "2023", 209867)],
    )
    page, loaded = make_page(tmp_path, client)
    page.open_card(anime_card())
    assert pump(app, lambda: hero_shows_logo(page)), "the logo never reached the hero"

    assert client.search_calls == ["Frieren"]
    assert client.image_calls == [("tv", 209867, "fr")]
    assert ("https://img/logo-fr.png", "logo-anime-anime:1") in loaded
    assert page.detail_hero.logo.toolTip() == "Frieren"
    assert page.detail_hero.logo.accessibleName() == "Frieren"


def test_an_anime_film_asks_the_movie_side(app, tmp_path, monkeypatch):
    monkeypatch.setattr(qt_media.recently_viewed, "record", lambda *a, **k: None)
    monkeypatch.setattr(qt_media.MediaPage, "_logo_language", lambda self: None)
    client = StubClient(hits=[Hit("A Silent Voice", "movie", "2016", 378064)])
    page, loaded = make_page(tmp_path, client)
    page.open_card(
        anime_card("A Silent Voice", episodes=1, year="2016", media_type="movie")
    )
    assert pump(app, lambda: hero_shows_logo(page))

    assert client.image_calls == [("movie", 378064, None)]
    assert page.detail_hero._logo_source is not None


def test_an_untyped_anime_asks_the_tv_side(app, tmp_path, monkeypatch):
    """MAL has not said what it is yet: the TV side is the safe question."""
    monkeypatch.setattr(qt_media.recently_viewed, "record", lambda *a, **k: None)
    monkeypatch.setattr(qt_media.MediaPage, "_logo_language", lambda self: None)
    client = StubClient(hits=[Hit("Frieren", "tv", "2023", 209867)])
    page, _loaded = make_page(tmp_path, client)
    page.open_card(anime_card("Frieren", episodes=0, media_type=""))
    assert pump(app, lambda: hero_shows_logo(page))
    assert client.image_calls == [("tv", 209867, None)]


def test_the_language_order_is_display_then_english_then_neutral(app, tmp_path, monkeypatch):
    monkeypatch.setattr(qt_media.recently_viewed, "record", lambda *a, **k: None)
    monkeypatch.setattr(qt_media.MediaPage, "_logo_language", lambda self: "ja")
    # Only English and a neutral logo exist: English wins over neutral.
    client = StubClient(
        logos={"en": "https://img/logo-en.png"},
        hits=[Hit("Frieren", "tv", "2023", 209867)],
    )
    page, loaded = make_page(tmp_path, client)
    page.open_card(anime_card())
    assert pump(app, lambda: hero_shows_logo(page))
    assert client.image_calls == [("tv", 209867, "ja")]
    assert "https://img/logo-en.png" in [url for url, _key in loaded]


def test_a_logo_less_title_keeps_the_text_title(app, tmp_path, monkeypatch):
    monkeypatch.setattr(qt_media.recently_viewed, "record", lambda *a, **k: None)
    client = StubClient(logos={}, hits=[Hit("Frieren", "tv", "2023", 209867)])
    page, loaded = make_page(tmp_path, client)
    page.open_card(anime_card())
    assert pump(app, lambda: client.image_calls)
    pump(app, None, 0.2)  # let any late callback land
    assert hero_shows_title(page)
    assert not [url for url, _key in loaded if "logo" in url]


def test_a_logo_that_fails_to_load_leaves_the_text_title(app, tmp_path, monkeypatch):
    monkeypatch.setattr(qt_media.recently_viewed, "record", lambda *a, **k: None)
    client = StubClient(hits=[Hit("Frieren", "tv", "2023", 209867)])
    page, loaded = make_page(tmp_path, client)

    def load_image(url, key, apply, fallback="", paint_url="", on_fail=None):
        loaded.append((url, key))
        if "logo" in url and apply is not None:
            apply(None)  # the bytes did not decode

    page._load_image = load_image
    page.open_card(anime_card())
    assert pump(app, lambda: any("logo" in url for url, _k in loaded))
    pump(app, None, 0.2)
    assert hero_shows_title(page)


def test_reopening_the_title_asks_tmdb_nothing_again(app, tmp_path, monkeypatch):
    monkeypatch.setattr(qt_media.recently_viewed, "record", lambda *a, **k: None)
    client = StubClient(hits=[Hit("Frieren", "tv", "2023", 209867)])
    page, loaded = make_page(tmp_path, client)
    page.open_card(anime_card())
    assert pump(app, lambda: hero_shows_logo(page))
    first = (list(client.search_calls), list(client.image_calls))

    page.open_card(anime_card())
    assert pump(app, lambda: hero_shows_logo(page))
    assert (client.search_calls, client.image_calls) == (first[0], first[1]), (
        "the second visit must come from the shared title cache"
    )


def test_a_stale_logo_is_ignored_after_navigating_away(app, tmp_path, monkeypatch):
    monkeypatch.setattr(qt_media.recently_viewed, "record", lambda *a, **k: None)
    client = StubClient(
        logos={"en": "https://img/logo-en.png"},
        hits=[Hit("Frieren", "tv", "2023", 209867)],
    )
    page, _loaded = make_page(tmp_path, client)
    pending: list = []

    def hold_image(url, key, apply, fallback="", paint_url="", on_fail=None):
        if "logo" in url and apply is not None:
            pending.append(apply)  # arrives after the reader has moved on

    page._load_image = hold_image
    page.open_card(anime_card("Frieren"))
    assert pump(app, lambda: pending)

    other = qt_media.Card(key="movie:603", title="The Matrix", year="1999",
                          kind="movie", tmdb_id=603)
    client.hits = [Hit("The Matrix", "movie", "1999", 603)]
    client.logos = {"en": "https://img/logo-matrix.png"}
    page.open_card(other)
    assert pump(app, lambda: len(pending) >= 1)

    before = page.detail_hero._logo_source
    pending[0](_pixmap(50))  # Frieren's logo arrives late
    assert page.detail_hero._logo_source is before, (
        "a logo for the previous title must not replace the current hero"
    )


def test_movie_and_tv_heroes_pass_the_display_language(app, tmp_path, monkeypatch):
    monkeypatch.setattr(qt_media.MediaPage, "_logo_language", lambda self: "de")
    client = StubClient(logos={"de": "https://img/logo-de.png"})
    page, loaded = make_page(tmp_path, client)
    card = qt_media.Card(key="movie:603", title="The Matrix", kind="movie", tmdb_id=603)
    page._detail_card = card
    page._load_detail_art(card)
    assert pump(app, lambda: client.image_calls)
    assert client.image_calls == [("movie", 603, "de")]
    assert pump(app, lambda: hero_shows_logo(page))
    assert "https://img/logo-de.png" in [url for url, _key in loaded]


def test_no_language_means_no_keyword_so_old_clients_keep_working(app, tmp_path, monkeypatch):
    monkeypatch.setattr(qt_media.MediaPage, "_logo_language", lambda self: None)

    class OldClient(StubClient):
        def images(self, kind, tmdb_id):  # no language keyword at all
            self.image_calls.append((kind, int(tmdb_id), None))
            return tmdb.MediaImages(logo_url="https://img/logo-en.png")

    client = OldClient()
    page, _loaded = make_page(tmp_path, client)
    card = qt_media.Card(key="tv:877", title="Friends", kind="tv", tmdb_id=877)
    page._detail_card = card
    page._load_detail_art(card)
    assert pump(app, lambda: client.image_calls)
    assert client.image_calls == [("tv", 877, None)]


def test_a_hero_without_art_stays_dark_for_the_white_logo(app, tmp_path, monkeypatch):
    """The light theme paints a near-white hero; a white logo would vanish."""
    monkeypatch.setattr(qt_media.recently_viewed, "record", lambda *a, **k: None)
    monkeypatch.setattr(qt_media.MediaPage, "_logo_language", lambda self: None)
    client = StubClient(hits=[Hit("Frieren", "tv", "2023", 209867)])
    page, _loaded = make_page(tmp_path, client)
    page.open_card(anime_card())
    assert pump(app, lambda: hero_shows_logo(page))
    hero = page.detail_hero
    assert hero._pixmap is None, "no wide art arrived, so the hero is bare"
    assert "color:" in hero.styleSheet(), "a bare hero needs light labels"

    art = _pixmap(120)
    hero.set_pixmap(art)
    assert hero.styleSheet() == "", "with art the theme colours come back"
    hero.set_pixmap(None)
    assert "color:" in hero.styleSheet()


def test_the_anime_side_uses_the_shared_cached_lookup_only(tmp_path, monkeypatch):
    """No second lookup, cache or provider lives in the Windows page."""
    client = StubClient(hits=[Hit("Frieren", "tv", "2023", 209867)])
    found = tmdb.tmdb_images_for_title("Frieren", "2023", "tv", client=client)
    assert found.logo_url
    assert tmdb.tmdb_images_for_title("Frieren", "2023", "tv", client=client) is found
    assert len(client.search_calls) == 1
    assert not hasattr(qt_media, "tmdb_logo_for_title")
