"""Logos, wide art and streaming episode rows in the GTK media views.

Every test runs the real widgets on a stub window: artwork is handed in by a
fake image loader, so nothing here touches the network.
"""

import types

import pytest

gi = pytest.importorskip("gi")

try:
    gi.require_version("Gtk", "3.0")
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf, Gtk
except (ValueError, ImportError):  # pragma: no cover - GTK 3 not installed
    pytest.skip("GTK 3 is not installed", allow_module_level=True)

from lgl import movies_ui
from lgl.tmdb import MediaImages


def has_display() -> bool:
    return bool(Gtk.init_check()[0])


needs_display = pytest.mark.skipif(not has_display(), reason="no display for GTK")


class Settings(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)

    def set(self, key, value):
        dict.__setitem__(self, key, value)

    def save(self):
        return True


def pixbuf(width: int = 300, height: int = 100):
    return GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, width, height)


class Loader:
    """Stands in for the image cache: records asks and answers at once."""

    def __init__(self, deliver: bool = True) -> None:
        self.asks: list[tuple[str, tuple[int, int]]] = []
        self._deliver = deliver

    def load(self, url, size, callback) -> None:
        self.asks.append((url, size))
        if self._deliver:
            callback(pixbuf())

    def urls(self) -> list[str]:
        return [url for url, _size in self.asks]


class Client:
    def __init__(self, backdrop: str = "", logo: str = "") -> None:
        self.found = MediaImages(backdrop_url=backdrop, logo_url=logo)
        self.asked: list[tuple[str, int]] = []

    def images(self, kind, tmdb_id):
        self.asked.append((kind, tmdb_id))
        return self.found


class InlineThread:
    """Runs the worker's body in place, so the test needs no waiting."""

    def __init__(self, target=None, daemon=None, name=None) -> None:
        self._target = target

    def start(self) -> None:
        if self._target is not None:
            self._target()


@pytest.fixture(autouse=True)
def inline(monkeypatch):
    monkeypatch.setattr(movies_ui.threading, "Thread", InlineThread)
    monkeypatch.setattr(movies_ui.GLib, "idle_add", lambda fn, *args: fn(*args))
    yield


ART_METHODS = (
    "_load_detail_art",
    "_load_poster_fallback",
    "_apply_art_backdrop",
    "_fetch_art_images",
    "_apply_art_images",
    "_apply_episode_still",
    "_apply_hero_logo",
    "_fetch_anime_art",
    "_apply_anime_backdrop",
    "_load_home_hero_art",
    "_apply_home_hero_images",
    "_apply_hero_image",
)


def window(loader=None, client=None):
    """A stub window with the real art helpers bound to it."""
    stub = types.SimpleNamespace(
        _images=loader or Loader(),
        _client=client or Client(),
        _settings=Settings(),
        _art_token=0,
        _hero_image_token=1,
        _season_token=1,
    )
    for name in ART_METHODS:
        setattr(stub, name, types.MethodType(getattr(movies_ui.MoviesWindow, name), stub))
    stub._hero_backdrop = movies_ui.Backdrop(430)
    stub._play_episode = lambda *_a: None
    return stub


def item(**kwargs):
    base = dict(
        kind="movie",
        tmdb_id=603,
        title="The Matrix",
        year="1999",
        poster_url="https://image.tmdb.org/t/p/w500/poster.jpg",
        backdrop_url="",
    )
    base.update(kwargs)
    return types.SimpleNamespace(**base)


def labels(widget):
    found = []

    def walk(node):
        if isinstance(node, Gtk.Label):
            found.append(node.get_text())
        children = getattr(node, "get_children", None)
        if children is not None:
            for child in children():
                walk(child)

    walk(widget)
    return found


# -- episode facts ---------------------------------------------------------


def test_the_row_reads_runtime_then_a_short_date():
    assert movies_ui._episode_meta_text(66, "2012-06-24") == "66 min · Jun 24, 2012"


def test_a_row_without_a_runtime_keeps_its_date():
    assert movies_ui._episode_meta_text(0, "2012-06-24") == "Jun 24, 2012"


def test_a_row_without_facts_says_nothing():
    assert movies_ui._episode_meta_text(None, "") == ""


def test_the_tmdb_attribution_is_the_official_sentence():
    assert movies_ui.TMDB_ATTRIBUTION == (
        "This product uses the TMDB API but is not endorsed or certified by TMDB."
    )


# -- wide art --------------------------------------------------------------


@needs_display
def test_a_card_without_a_backdrop_still_gets_one():
    loader = Loader()
    client = Client(backdrop="https://image.tmdb.org/t/p/original/wide.jpg")
    backdrop = movies_ui.Backdrop(420)
    window(loader, client)._load_detail_art(item(), backdrop, None, None)
    assert client.asked == [("movie", 603)]
    assert "https://image.tmdb.org/t/p/original/wide.jpg" in loader.urls()
    assert backdrop._fallback is False


@needs_display
def test_the_poster_is_only_a_marked_fallback():
    loader = Loader()
    backdrop = movies_ui.Backdrop(420)
    window(loader, Client())._load_detail_art(item(), backdrop, None, None)
    assert loader.urls() == ["https://image.tmdb.org/t/p/w500/poster.jpg"]
    assert backdrop._fallback is True


@needs_display
def test_an_anime_title_asks_tmdb_by_name_not_its_poster(monkeypatch):
    asked = []

    def lookup(title, year="", kind="tv", *, client=None):
        asked.append((title, year))
        return "https://image.tmdb.org/t/p/original/anime.jpg"

    monkeypatch.setattr(movies_ui.tmdb, "tmdb_backdrop_for_title", lookup)
    loader = Loader()
    backdrop = movies_ui.Backdrop(420)
    anime = item(kind="anime", tmdb_id=0, title="Frieren", year="2023")
    window(loader)._load_detail_art(anime, backdrop, None, None, kind="anime")
    assert asked == [("Frieren", "2023")]
    assert "https://image.tmdb.org/t/p/original/anime.jpg" in loader.urls()
    assert backdrop._fallback is False


# -- logos -----------------------------------------------------------------


@needs_display
def test_a_logo_takes_the_title_place_and_keeps_its_name():
    loader = Loader()
    hero = Gtk.Overlay()
    hero.set_size_request(900, 420)
    logo = movies_ui.HeroLogo(hero)
    title = Gtk.Label(label="The Matrix")
    client = Client(logo="https://image.tmdb.org/t/p/original/logo.png")
    window(loader, client)._load_detail_art(
        item(), movies_ui.Backdrop(420), logo, title
    )
    assert logo.get_visible()
    assert not title.get_visible()
    assert logo.get_tooltip_text() == "The Matrix"


@needs_display
def test_without_a_logo_the_text_title_stays():
    loader = Loader()
    hero = Gtk.Overlay()
    logo = movies_ui.HeroLogo(hero)
    title = Gtk.Label(label="The Matrix")
    window(loader, Client())._load_detail_art(
        item(), movies_ui.Backdrop(420), logo, title
    )
    assert not logo.get_visible()
    assert title.get_visible()


@needs_display
def test_a_logo_arriving_for_an_old_page_is_ignored():
    loader = Loader()
    hero = Gtk.Overlay()
    logo = movies_ui.HeroLogo(hero)
    title = Gtk.Label(label="The Matrix")
    title.show()
    stub = window(loader, Client())
    stub._apply_art_images(
        MediaImages(logo_url="https://image.tmdb.org/t/p/original/logo.png"),
        movies_ui.Backdrop(420),
        logo,
        title,
        99,  # an older page's token
    )
    assert not logo.get_visible()
    assert title.get_visible()


@needs_display
def test_the_logo_is_scaled_down_to_the_hero():
    hero = Gtk.Overlay()
    hero.set_size_request(900, 420)
    hero.get_allocated_width = lambda: 900
    logo = movies_ui.HeroLogo(hero)
    title = Gtk.Label(label="The Matrix")
    logo.load_source(pixbuf(1000, 300), title)
    shown = logo.get_pixbuf()
    assert shown is not None
    assert shown.get_height() <= movies_ui.HERO_LOGO_HEIGHT + 12
    assert shown.get_width() <= 900 * movies_ui.HERO_LOGO_SHARE + 12


# -- episode rows ----------------------------------------------------------


def season(*episodes):
    return types.SimpleNamespace(number=1, episodes=list(episodes))


def episode(number, **kwargs):
    base = dict(
        number=number,
        title=f"Episode {number}",
        air_date="2012-06-24",
        overview="A long night on the wall.",
        still_url="https://image.tmdb.org/t/p/w780/still.jpg",
        rating=6.6,
        runtime=66,
    )
    base.update(kwargs)
    return types.SimpleNamespace(**base)


@needs_display
def test_a_row_shows_the_pill_the_facts_and_the_still():
    box = Gtk.ListBox()
    stub = window(Loader())
    movies_ui.MoviesWindow._apply_season(stub, 1, box, season(episode(1)))
    shown = labels(box)
    assert "01" in shown
    assert "★ 6.6" in shown
    assert "66 min · Jun 24, 2012" in shown
    assert "Episode 1" in shown
    assert "Play" in shown
    assert "https://image.tmdb.org/t/p/w780/still.jpg" in stub._images.urls()


@needs_display
def test_an_unrated_episode_hides_its_pill():
    box = Gtk.ListBox()
    stub = window(Loader())
    movies_ui.MoviesWindow._apply_season(stub, 1, box, season(episode(2, rating=None)))
    shown = labels(box)
    assert not any(text.startswith("★") for text in shown)


@needs_display
def test_a_row_keeps_its_synopsis_to_three_lines():
    box = Gtk.ListBox()
    stub = window(Loader())
    movies_ui.MoviesWindow._apply_season(
        stub, 1, box, season(episode(3, overview="word " * 200))
    )
    overviews = [label for label in labels(box) if label.startswith("word")]
    assert len(overviews) == 1
    assert overviews[0].startswith("word")


def widgets(node, kind):
    found = []

    def walk(item):
        if isinstance(item, kind):
            found.append(item)
        children = getattr(item, "get_children", None)
        if children is not None:
            for child in children():
                walk(child)

    walk(node)
    return found


@needs_display
def test_a_row_fits_a_nine_hundred_pixel_window():
    box = Gtk.ListBox()
    win = Gtk.Window()
    win.set_default_size(900, 420)
    win.add(box)
    win.show_all()
    stub = window(Loader())
    movies_ui.MoviesWindow._apply_season(stub, 1, box, season(episode(1)))
    win.show_all()
    for _ in range(40):
        while Gtk.events_pending():
            Gtk.main_iteration()
    row = box.get_children()[0]
    assert row.get_allocated_width() <= 900
    still = [
        image for image in widgets(row, Gtk.Image)
        if not isinstance(image, movies_ui.HeroLogo)
    ][0]
    assert (still.get_allocated_width(), still.get_allocated_height()) == (200, 112)
    assert still.get_pixbuf() is not None
    win.destroy()


@needs_display
def test_an_episode_without_a_still_asks_for_no_image():
    box = Gtk.ListBox()
    stub = window(Loader())
    movies_ui.MoviesWindow._apply_season(stub, 1, box, season(episode(4, still_url="")))
    assert stub._images.urls() == []


# -- hero fallback ---------------------------------------------------------


@needs_display
def test_the_home_hero_prefers_wide_art_over_the_poster():
    loader = Loader()
    client = Client(backdrop="https://image.tmdb.org/t/p/original/hero.jpg")
    stub = window(loader, client)
    stub._load_home_hero_art(item(), stub._hero_image_token)
    # The wide art is asked for last, so it is what stays on screen.
    assert loader.urls()[-1] == "https://image.tmdb.org/t/p/original/hero.jpg"
    assert stub._hero_backdrop._fallback is False


@needs_display
def test_the_home_hero_marks_a_poster_that_stands_in():
    loader = Loader()
    stub = window(loader, Client())
    stub._load_home_hero_art(item(), stub._hero_image_token)
    assert "https://image.tmdb.org/t/p/w500/poster.jpg" in loader.urls()
    assert stub._hero_backdrop._fallback is True
