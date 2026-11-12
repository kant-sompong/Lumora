"""The JPEG-original fallback for TMDB artwork (WebP on sized paths)."""

import pytest

from lgl import images


def _gtk_movies_ui():
    """The GTK media module, or a skip when this environment cannot import it."""
    pytest.importorskip("gi")
    try:
        from lgl import movies_ui
    except ImportError as error:  # pragma: no cover - GTK without cairo/pycairo
        pytest.skip(f"GTK frontend cannot be imported here: {error}")
    return movies_ui




def test_sized_tmdb_poster_maps_to_the_original():
    url = "https://image.tmdb.org/t/p/w500/qJ2tW6WMUDux911r6m7haRef0WH.jpg"
    assert images.portable_image_url(url) == (
        "https://image.tmdb.org/t/p/original/qJ2tW6WMUDux911r6m7haRef0WH.jpg"
    )


def test_backdrop_and_height_variants_map_too():
    assert images.portable_image_url(
        "https://image.tmdb.org/t/p/w1280/abc.jpg"
    ).endswith("/t/p/original/abc.jpg")
    assert images.portable_image_url(
        "https://image.tmdb.org/t/p/h632/abc.jpg"
    ).endswith("/t/p/original/abc.jpg")


def test_other_hosts_and_originals_have_no_twin():
    assert images.portable_image_url("https://s4.anilist.co/file/x.jpg") == ""
    assert images.portable_image_url(
        "https://image.tmdb.org/t/p/original/abc.jpg"
    ) == ""
    assert images.portable_image_url("") == ""


def test_gtk_alternate_url_prefers_the_original(monkeypatch):
    movies_ui = _gtk_movies_ui()

    url = "https://image.tmdb.org/t/p/w500/poster.jpg"
    assert movies_ui._alternate_image_url(url).endswith(
        "/t/p/original/poster.jpg"
    )
    assert movies_ui._alternate_image_url(
        "https://example.test/a.webp"
    ).endswith("a.jpg")


def test_gem_rows_read_the_poster_url_key():
    movies_ui = _gtk_movies_ui()

    assert movies_ui._gem_poster_url(
        {"poster_url": "https://s4.anilist.co/x.jpg"}
    ) == "https://s4.anilist.co/x.jpg"
    # Older caches, newest key first.
    assert movies_ui._gem_poster_url({"cover_image": "c"}) == "c"
    assert movies_ui._gem_poster_url({"image": "i"}) == "i"
    assert movies_ui._gem_poster_url({"poster_url": "", "cover_image": "c"}) == "c"
    assert movies_ui._gem_poster_url({}) == ""


def test_gtk_retries_the_jpeg_original_when_webp_cannot_be_decoded(monkeypatch):
    """A build without a WebP loader still gets the poster."""
    movies_ui = _gtk_movies_ui()

    seen: list[str] = []

    class Response:
        def __init__(self, data: bytes) -> None:
            self._data = data

        def read(self, _size: int = -1) -> bytes:
            return self._data

        def __enter__(self):
            return self

        def __exit__(self, *_exc) -> bool:
            return False

    def fake_urlopen(request, timeout=0.0):
        seen.append(request.full_url)
        return Response(b"JPEG" if "/original/" in request.full_url else b"WEBP")

    monkeypatch.setattr(movies_ui.urllib.request, "urlopen", fake_urlopen)
    class Pixbuf:
        def get_width(self):
            return 500

        def get_height(self):
            return 750

        def scale_simple(self, width, height, _interp):
            return "pixbuf"

    monkeypatch.setattr(
        movies_ui, "_decode_image",
        lambda data: Pixbuf() if data == b"JPEG" else None,
    )
    monkeypatch.setattr(movies_ui.time, "sleep", lambda _seconds: None)

    url = "https://image.tmdb.org/t/p/w500/poster.jpg"
    assert movies_ui._fetch_pixbuf(url, (100, 150)) == "pixbuf"
    assert any("/t/p/original/poster.jpg" in target for target in seen)


def test_qt_retries_the_jpeg_original_when_webp_cannot_be_decoded(monkeypatch):
    pytest.importorskip("PySide6")
    from lgl import qt_media

    for name in ("cached_image_bytes", "pixmap_from_bytes", "run_async"):
        if not hasattr(qt_media, name):
            pytest.skip(f"qt_media has no {name}")

    requested: list[str] = []
    painted: list[tuple[str, object]] = []

    class Page:
        """Just the state ``_load_image`` touches; no QWidget needed."""

        _pix: dict = {}
        _cache_root = None

        def _paint_card(self, url, pix):
            painted.append((url, pix))

    page = Page()
    page._load_image = qt_media.MediaPage._load_image.__get__(page)

    def fake_cached(url, cache_root, key, timeout=0.0):
        requested.append(url)
        return b"JPEG" if "/original/" in url else b"WEBP"

    monkeypatch.setattr(qt_media, "cached_image_bytes", fake_cached)
    monkeypatch.setattr(
        qt_media, "pixmap_from_bytes",
        lambda data: "PIX" if data == b"JPEG" else None,
    )
    monkeypatch.setattr(
        qt_media, "run_async", lambda work, done, failed: done(work())
    )
    url = "https://image.tmdb.org/t/p/w500/poster.jpg"
    page._load_image(url, "poster-1", None)

    assert requested == [
        "https://image.tmdb.org/t/p/w500/poster.jpg",
        "https://image.tmdb.org/t/p/original/poster.jpg",
    ]
    assert page._pix["poster-1-portable"] == "PIX"
    # Painting still uses the card's own URL, so the poster widget matches.
    assert painted == [("https://image.tmdb.org/t/p/w500/poster.jpg", "PIX")]
