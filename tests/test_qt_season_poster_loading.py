"""Season artwork really downloads and really decodes.

The other season-picker tests inject a fake image loader, so they prove the
wiring but not the bytes.  These tests serve real PNG files from a local HTTP
server and let the page's own loader fetch, cache and decode them, so a broken
download, a 404, a dead host or a junk payload is caught here rather than in a
screenshot.  Only the URL builder is patched, and only because the picker's
helper insists on ``image.tmdb.org`` addresses; the bytes and the decoding are
the production path.
"""

from __future__ import annotations

import http.server
import os
import socket
import threading
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QBuffer, QIODevice  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lgl import qt_media, qt_season_picker  # noqa: E402
from lgl.tmdb import Season  # noqa: E402


def png(width: int, height: int, colour: str) -> bytes:
    """A real PNG, produced the way any encoder would."""
    image = QImage(width, height, QImage.Format_RGB32)
    image.fill(QColor(colour))
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    assert image.save(buffer, "PNG")
    return bytes(buffer.data())


SEASON_PNG = png(44, 64, "#3355ff")
SHOW_PNG = png(20, 30, "#ff5533")


class _Handler(http.server.BaseHTTPRequestHandler):
    routes: dict[str, tuple[int, bytes, str]] = {}
    hits: list[str] = []

    def do_GET(self) -> None:  # noqa: N802 - http.server naming
        type(self).hits.append(self.path)
        route = type(self).routes.get(self.path)
        if route is None:
            self.send_error(404, "Not Found")
            return
        status, body, content_type = route
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        return


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def server():
    handler = type("Handler", (_Handler,), {
        "routes": {
            "/season.png": (200, SEASON_PNG, "image/png"),
            "/show.png": (200, SHOW_PNG, "image/png"),
            "/missing.png": (404, b"gone", "text/plain"),
            "/broken.png": (200, b"this is not an image", "image/png"),
        },
        "hits": [],
    })
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", handler
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


@pytest.fixture
def page(app, tmp_path):
    # The page owns the real loader: fetch, cache and decode all live there.
    page = qt_media.MediaPage(cache_root=tmp_path / "cache")
    page.resize(1000, 900)
    page.show()
    app.processEvents()
    yield page
    page.close()
    page.deleteLater()
    app.processEvents()


def pump(app, predicate, timeout: float = 5.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _open(page, app, season_poster: str, show_poster: str):
    picker = page.season_combo
    picker.set_seasons(
        [Season(1, "Season 1", 10, poster=season_poster)], show_poster=show_poster
    )
    picker.open_panel()
    app.processEvents()
    row = picker.panel.rows[0]
    return picker, row


@pytest.fixture(autouse=True)
def _pass_urls_through(monkeypatch):
    """The picker's helper only trusts image.tmdb.org; keep local URLs as-is."""
    monkeypatch.setattr(
        qt_season_picker,
        "season_poster_url",
        lambda season, show_poster="", size="w185": (
            str(getattr(season, "poster", "") or "") or str(show_poster or "")
        ),
    )


def test_a_season_poster_downloads_and_decodes(app, server, page):
    base, handler = server
    _picker, row = _open(page, app, f"{base}/season.png", "")

    assert pump(app, lambda: row.thumb._pixmap is not None)
    pixmap = row.thumb._pixmap
    assert pixmap is not None and not pixmap.isNull()
    assert (pixmap.width(), pixmap.height()) == (44, 64)
    assert handler.hits == ["/season.png"]


def test_a_second_open_uses_the_cached_poster(app, server, page):
    base, handler = server
    _picker, row = _open(page, app, f"{base}/season.png", "")
    assert pump(app, lambda: row.thumb._pixmap is not None)

    picker = page.season_combo
    picker.close_panel()
    picker.open_panel()
    app.processEvents()

    assert pump(app, lambda: picker.panel.rows[0].thumb._pixmap is not None)
    assert handler.hits.count("/season.png") == 1


def test_a_404_falls_back_to_the_show_poster(app, server, page):
    base, handler = server
    _picker, row = _open(page, app, f"{base}/missing.png", f"{base}/show.png")

    assert pump(app, lambda: row.thumb._pixmap is not None)
    pixmap = row.thumb._pixmap
    assert pixmap is not None and not pixmap.isNull()
    assert (pixmap.width(), pixmap.height()) == (20, 30)  # the show's art
    assert handler.hits[0] == "/missing.png"
    assert "/show.png" in handler.hits


def test_undecodable_bytes_fall_back_to_the_show_poster(app, server, page):
    base, handler = server
    _picker, row = _open(page, app, f"{base}/broken.png", f"{base}/show.png")

    assert pump(app, lambda: row.thumb._pixmap is not None)
    pixmap = row.thumb._pixmap
    assert pixmap is not None and not pixmap.isNull()
    assert (pixmap.width(), pixmap.height()) == (20, 30)
    assert "/show.png" in handler.hits


def test_a_dead_host_falls_back_to_the_show_poster(app, server, page):
    base, handler = server
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]
    _picker, row = _open(page, app, f"http://127.0.0.1:{dead_port}/season.png",
                         f"{base}/show.png")

    assert pump(app, lambda: row.thumb._pixmap is not None)
    pixmap = row.thumb._pixmap
    assert pixmap is not None and not pixmap.isNull()
    assert (pixmap.width(), pixmap.height()) == (20, 30)
    assert handler.hits == ["/show.png"]


def test_a_season_without_artwork_uses_the_show_poster(app, server, page):
    base, handler = server
    _picker, row = _open(page, app, "", f"{base}/show.png")

    assert pump(app, lambda: row.thumb._pixmap is not None)
    pixmap = row.thumb._pixmap
    assert pixmap is not None and not pixmap.isNull()
    assert (pixmap.width(), pixmap.height()) == (20, 30)
    assert handler.hits == ["/show.png"]


def test_with_no_artwork_at_all_the_row_keeps_its_number(app, server, page):
    base, handler = server
    _picker, row = _open(page, app, "", "")

    time.sleep(0.2)
    app.processEvents()
    assert row.thumb._pixmap is None
    assert handler.hits == []
