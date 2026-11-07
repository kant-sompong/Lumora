"""Movies & TV: a cinematic TMDB browser with frameXTV playback.

The window keeps its own near-black look regardless of the app theme: a compact
navigation bar, a large hero, horizontal carousels, poster grids and the same
embedded WebKit player the app has always used.  The structure is deliberately
honest -- carousels only show what TMDB returned, and Continue Watching only
shows titles the user actually pressed Play on (see :mod:`lgl.media_history`).

Everything that touches the network runs on a worker thread and hands its
result back through ``GLib.idle_add``.
"""

from __future__ import annotations

import os as _os
_os.environ.setdefault("WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS", "1")

import threading
import time
import urllib.parse
import urllib.request
from itertools import zip_longest
from typing import Any

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import (  # noqa: E402
    Gdk,
    GdkPixbuf,
    GLib,
    Gtk,
    Pango,
    PangoCairo,
)

from . import artwork_background, platform_support, recently_viewed  # noqa: E402
from . import (  # noqa: E402
    anipm,
    framextv,
    mal,
    media_history,
    streaming,
    tmdb,
    trailer_data,
    trailers,
    view_prefs,
)
from .mal import (  # noqa: E402
    AnimeDetails,
    AnimeItem,
    MalClient,
    MalError,
    load_client_id,
)
from .tmdb import (  # noqa: E402
    MediaDetails,
    MediaItem,
    TmdbClient,
    TmdbError,
    load_api_key,
)

POSTER_SIZE = (150, 225)
CARD_SIZE = (164, 246)
BACKDROP_SIZE = (1400, 560)
DETAIL_BACKDROP_SIZE = (1400, 460)
WATCHLIST_KEY = "movie_watchlist"

_NAV_ITEMS = (
    ("home", "Home"),
    ("movies", "Movies"),
    ("tv", "TV Shows"),
    ("anime", "Anime"),
    ("search", "Search"),
    ("mylist", "My List"),
    ("history", "History"),
)

#: Applied to this window only, above the screen-wide token provider.  The
#: movie window is deliberately dark in both app themes, so it cannot simply
#: inherit light-theme tokens; the literal colours live here, in Python, and
#: style.css stays token-only.
_CINEMATIC_CSS = """
.movie-window.background,
.movie-window {
  background-color: #0a0b0d;
  color: #e8eaed;
}

headerbar.movie-nav {
  background-color: #0a0b0d;
  background-image: none;
  border-bottom: 1px solid rgba(255, 255, 255, 0.07);
  box-shadow: none;
  min-height: 48px;
  padding: 4px 12px;
  color: #f5f6f7;
}
headerbar.movie-nav:backdrop {
  background-color: #0a0b0d;
  color: #f5f6f7;
}
headerbar.movie-nav button.titlebutton {
  color: #e8eaed;
  background-color: transparent;
  background-image: none;
  border: none;
  box-shadow: none;
}
headerbar.movie-nav button.titlebutton:hover {
  background-color: rgba(255, 255, 255, 0.10);
}
.movie-brand { color: #f5f6f7; font-weight: 700; font-size: 0.98em; }
.movie-brand-mark { color: #f5c518; font-size: 0.85em; }

button.movie-nav-item {
  background-color: transparent;
  background-image: none;
  border: none;
  border-image: none;
  box-shadow: none;
  text-shadow: none;
  color: #9aa3af;
  padding: 5px 10px;
  min-height: 0;
  min-width: 0;
  border-radius: 7px;
  font-weight: 600;
  font-size: 0.9em;
}
button.movie-nav-item:hover {
  color: #ffffff;
  background-color: rgba(255, 255, 255, 0.07);
}
button.movie-nav-item.active {
  color: #0a0b0d;
  background-color: #f5f6f7;
}
button.movie-nav-item.active:hover {
  background-color: #ffffff;
  color: #0a0b0d;
}
button.movie-icon-button {
  background-color: transparent;
  background-image: none;
  border: none;
  box-shadow: none;
  color: #c7ccd3;
  padding: 4px;
  min-width: 0;
  min-height: 0;
  border-radius: 7px;
}
button.movie-icon-button:hover {
  color: #ffffff;
  background-color: rgba(255, 255, 255, 0.09);
}

.movie-search {
  background-color: #15181d;
  background-image: none;
  border: 1px solid rgba(255, 255, 255, 0.09);
  border-radius: 999px;
  color: #e8eaed;
  padding: 1px 10px;
  min-height: 30px;
  box-shadow: none;
}
.movie-search entry {
  background-color: transparent;
  background-image: none;
  border: none;
  box-shadow: none;
  color: #e8eaed;
}
.movie-search entry placeholder { color: #6b7280; }
.movie-search image { color: #6b7280; }

.movie-scroll,
.movie-scroll viewport,
.movie-window scrolledwindow {
  background-color: transparent;
}
.movie-window scrollbar {
  background-color: transparent;
  border: none;
}
.movie-window scrollbar slider {
  background-color: rgba(255, 255, 255, 0.16);
  border: none;
  border-radius: 8px;
  min-width: 6px;
  min-height: 6px;
}
.movie-window scrollbar slider:hover {
  background-color: rgba(255, 255, 255, 0.28);
}

.movie-hero {
  background-color: #111419;
  background-image: linear-gradient(120deg, #171b22, #0b0d10 55%, #16130f);
}
.movie-hero-scrim {
  background-image: linear-gradient(
    to bottom,
    rgba(10, 11, 13, 0.00) 0%,
    rgba(10, 11, 13, 0.22) 38%,
    rgba(10, 11, 13, 0.86) 76%,
    #0a0b0d 100%
  );
}
.movie-hero-badge {
  background-color: rgba(255, 255, 255, 0.13);
  color: #ffffff;
  border-radius: 4px;
  padding: 2px 8px;
  font-size: 0.68em;
  font-weight: 800;
}
.movie-hero-title {
  color: #ffffff;
  font-size: 2.5em;
  font-weight: 800;
}
.movie-page-title {
  color: #ffffff;
  font-size: 1.65em;
  font-weight: 800;
}

.movie-rating-star {
  color: #f5c518;
  font-weight: 700;
}
.movie-meta {
  color: #a8b0ba;
  font-size: 0.9em;
}
.movie-overview {
  color: #c3c9d1;
  font-size: 0.98em;
}

button.movie-btn-primary {
  background-color: #f5f6f7;
  background-image: none;
  border: none;
  border-image: none;
  box-shadow: none;
  text-shadow: none;
  color: #0a0b0d;
  border-radius: 8px;
  padding: 9px 20px;
  min-height: 0;
  font-weight: 700;
}
button.movie-btn-primary:hover {
  background-color: #ffffff;
  color: #0a0b0d;
}
button.movie-btn-primary:active {
  background-color: #d9dde2;
}
button.movie-btn-dark {
  background-color: rgba(255, 255, 255, 0.10);
  background-image: none;
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-image: none;
  box-shadow: none;
  text-shadow: none;
  color: #f5f6f7;
  border-radius: 8px;
  padding: 8px 16px;
  min-height: 0;
  font-weight: 600;
}
button.movie-btn-dark:hover {
  background-color: rgba(255, 255, 255, 0.17);
  color: #ffffff;
}
button.movie-btn-dark:checked {
  background-color: rgba(245, 197, 24, 0.16);
  border-color: rgba(245, 197, 24, 0.55);
  color: #f5c518;
}
button.movie-btn-small {
  padding: 4px 12px;
  border-radius: 6px;
  font-size: 0.85em;
}

button.movie-link {
  background-color: transparent;
  background-image: none;
  border: none;
  box-shadow: none;
  color: #b9c0ca;
  padding: 2px 6px;
  min-height: 0;
  font-weight: 600;
  font-size: 0.86em;
}
button.movie-link:hover { color: #ffffff; }

.movie-section-title {
  color: #f5f6f7;
  font-size: 1.15em;
  font-weight: 700;
}
.movie-section-count {
  background-color: rgba(255, 255, 255, 0.08);
  color: #c7ccd3;
  border-radius: 999px;
  padding: 1px 8px;
  font-size: 0.72em;
  font-weight: 700;
}
.movie-section-sub {
  color: #8b949e;
  font-size: 0.82em;
}

button.movie-card {
  background-color: transparent;
  background-image: none;
  border: none;
  border-image: none;
  box-shadow: none;
  text-shadow: none;
  padding: 0;
  min-height: 0;
  min-width: 0;
  border-radius: 10px;
}
button.movie-card:hover .movie-card-title { color: #ffffff; }
.movie-card-title {
  color: #e8eaed;
  font-size: 0.9em;
  font-weight: 600;
}
.movie-card-meta {
  color: #8b949e;
  font-size: 0.78em;
}
.movie-card-poster {
  background-color: #15181d;
  border-radius: 10px;
}
.movie-card-rating {
  background-color: rgba(10, 11, 13, 0.82);
  color: #f5c518;
  border-radius: 6px;
  padding: 1px 6px;
  font-size: 0.72em;
  font-weight: 700;
}
.movie-poster-frame {
  background-color: #15181d;
  border-radius: 10px;
}

button.movie-arrow {
  background-color: rgba(10, 11, 13, 0.72);
  background-image: none;
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-image: none;
  box-shadow: none;
  color: #ffffff;
  border-radius: 999px;
  padding: 0;
  min-width: 32px;
  min-height: 32px;
}
button.movie-arrow:hover {
  background-color: rgba(255, 255, 255, 0.16);
  color: #ffffff;
}
button.movie-arrow:disabled {
  color: rgba(255, 255, 255, 0.25);
  background-color: rgba(10, 11, 13, 0.45);
}

.movie-empty {
  background-color: #111419;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 14px;
  padding: 28px;
  color: #8b949e;
}
.movie-empty-title {
  color: #f5f6f7;
  font-size: 1.05em;
  font-weight: 700;
}
.movie-empty-icon { color: #5f6772; }
.movie-status {
  color: #8b949e;
  font-size: 0.88em;
}

.movie-episode {
  color: #e8eaed;
  font-size: 0.94em;
}
.movie-adult-badge {
  background-color: rgba(200, 38, 38, 0.92);
  color: #ffffff;
  border-radius: 5px;
  padding: 1px 6px;
  font-size: 0.7em;
  font-weight: 800;
}
.movie-episode-row {
  background-color: #111419;
  border-radius: 8px;
  padding: 6px 8px;
}
.movie-episode-row:hover { background-color: #171b21; }
.movie-episode-list { background-color: transparent; }

.movie-history-row {
  background-color: #111419;
  background-image: none;
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-image: none;
  border-radius: 10px;
  box-shadow: none;
  padding: 8px;
}
.movie-history-row:hover {
  background-color: #171b21;
  border-color: rgba(255, 255, 255, 0.12);
}
.movie-history-title {
  color: #f5f6f7;
  font-weight: 600;
  font-size: 0.95em;
}
.movie-detail-body { background-color: #0a0b0d; }
.movie-player-frame {
  background-color: #000000;
  border-radius: 10px;
}
.movie-player-frame webkitwebview { background-color: #000000; }

.movie-window separator {
  background-color: rgba(255, 255, 255, 0.07);
}
"""

_CSS_PROVIDER = None
_CSS_SCREEN_INSTALLED = False


def _cinematic_provider():
    """One parsed provider shared by every movie window.

    Widget style contexts do not hand providers down to their children, so the
    provider is installed on the screen.  Every selector in it is movie-scoped,
    which keeps the rest of the app on its own tokens.
    """
    global _CSS_PROVIDER, _CSS_SCREEN_INSTALLED
    if _CSS_PROVIDER is None:
        provider = Gtk.CssProvider()
        try:
            provider.load_from_data(_CINEMATIC_CSS.encode("utf-8"))
        except GLib.Error:
            return None
        _CSS_PROVIDER = provider
    if not _CSS_SCREEN_INSTALLED:
        screen = Gdk.Screen.get_default()
        if screen is not None:
            Gtk.StyleContext.add_provider_for_screen(
                screen, _CSS_PROVIDER, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1
            )
            _CSS_SCREEN_INSTALLED = True
    return _CSS_PROVIDER


def _webkit():
    """WebKit2 when this build has it; ``None`` means "open in a browser"."""
    for version in ("4.1", "4.0"):
        try:
            gi.require_version("WebKit2", version)
            from gi.repository import WebKit2  # noqa: PLC0415

            return WebKit2
        except (ValueError, ImportError):
            continue
    return None


#: Artwork formats, most portable first.  A format this build has no loader
#: for must never be requested: image.tmdb.org honours ``Accept`` and answers
#: WebP, which then cannot be decoded, leaving every poster blank.
_IMAGE_PREFERENCE = ("image/jpeg", "image/png", "image/gif", "image/webp")
#: The pair every gdk-pixbuf build can decode, used when a server insists on
#: a format we cannot read.
_PORTABLE_IMAGE_ACCEPT = "image/jpeg,image/png"


def _probe_status(url: str, timeout: float = 8.0) -> int | None:
    """HTTP status of a provider page, or ``None`` when it is unknown.

    A reachable page that answers 4xx/5xx (a rate limit, a block, a removed
    embed) is a provider that cannot play, so the caller can fall through to
    the next one instead of showing an error page as if it were a player.
    A network failure returns ``None``: we cannot tell, so WebKit still gets
    the chance to load it.
    """
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": tmdb.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(getattr(response, "status", 200) or 200)
    except urllib.error.HTTPError as error:
        return int(getattr(error, "code", 0) or 0)
    except (OSError, ValueError):
        return None


def _image_accept_header(portable_only: bool = False) -> str:
    """An ``Accept`` header listing only formats this build can decode."""
    try:
        supported = {
            mime
            for fmt in GdkPixbuf.Pixbuf.get_formats()
            for mime in fmt.get_mime_types()
        }
    except Exception:  # noqa: BLE001 - fall back to the portable pair
        supported = set()
    formats = [mime for mime in _IMAGE_PREFERENCE if mime in supported]
    if not formats:
        formats = ["image/jpeg", "image/png"]
    if not portable_only:
        formats.append("image/*;q=0.5")
    return ",".join(formats)


def _decode_image(data: bytes):
    """Bytes to pixbuf, or ``None`` when no installed loader understands them."""
    try:
        loader = GdkPixbuf.PixbufLoader()
        loader.write(data)
        loader.close()
        return loader.get_pixbuf()
    except (GLib.Error, ValueError):
        return None


def _alternate_image_url(url: str) -> str:
    """The JPEG twin of a WebP image URL, when there is one.

    MyAnimeList hands out ``.webp`` poster paths for part of its catalogue,
    and a gdk-pixbuf build without a WebP loader cannot decode them.  Its CDN
    still serves the same artwork at the ``.jpg`` path, which every build can
    read, so a failed WebP download is retried against that twin.
    """
    parts = urllib.parse.urlsplit(url)
    if not parts.path.lower().endswith(".webp"):
        return ""
    return urllib.parse.urlunsplit(
        parts._replace(path=parts.path[:-5] + ".jpg")
    )


def _fetch_pixbuf(url: str, size: tuple[int, int]):
    """Download and scale one image.

    A failed download is retried once, and so are bytes no loader can decode:
    a CDN that answers WebP can be asked again for JPEG or PNG only, and a
    WebP-only URL falls back to its JPEG twin.
    """
    parsed = urllib.parse.urlsplit(url)
    # Accept any https image URL; the host allow-list was too strict and
    # caused legitimate MAL/TMDB posters to be silently dropped.
    if parsed.scheme != "https" or not parsed.hostname:
        return None
    targets = [url]
    alternate = _alternate_image_url(url)
    if alternate:
        targets.append(alternate)
    pixbuf = None
    for target in targets:
        for accept in (_image_accept_header(), _PORTABLE_IMAGE_ACCEPT):
            request = urllib.request.Request(
                target, headers={"User-Agent": tmdb.USER_AGENT, "Accept": accept}
            )
            try:
                with urllib.request.urlopen(request, timeout=20) as response:
                    data = response.read(8 * 1024 * 1024)
            except (OSError, ValueError):
                time.sleep(0.4)
                continue
            pixbuf = _decode_image(data)
            if pixbuf is not None:
                break
        if pixbuf is not None:
            break
    if pixbuf is None:
        return None
    width, height = size
    scale = min(
        width / max(1, pixbuf.get_width()), height / max(1, pixbuf.get_height())
    )
    return pixbuf.scale_simple(
        max(1, int(pixbuf.get_width() * scale)),
        max(1, int(pixbuf.get_height() * scale)),
        GdkPixbuf.InterpType.BILINEAR,
    )


def _pixbuf_pixels(pixbuf) -> bytes:
    """RGB bytes with the row padding removed, which is what the blur expects."""
    width, height = pixbuf.get_width(), pixbuf.get_height()
    channels = pixbuf.get_n_channels()
    stride = pixbuf.get_rowstride()
    data = pixbuf.get_pixels()
    out = bytearray()
    for y in range(height):
        row = y * stride
        for x in range(width):
            index = row + x * channels
            out += data[index : index + 3]
    return bytes(out)


class ArtworkBackdrop:
    """Heavily blurred cover art painted behind a page's content.

    A draw handler paints the cached picture first, so the page's own widgets
    sit on top of it.  Decoding and blurring happen on a worker thread; the
    picture fades in unless transitions are switched off in the preferences.
    """

    OPACITY = 0.55

    def __init__(self, widget: Gtk.Widget, settings: Any) -> None:
        self.widget = widget
        self.settings = settings
        self.pixbuf = None
        self.alpha = 0.0
        self.source = ""
        self._token = 0
        widget.connect("draw", self._draw)

    def clear(self) -> None:
        self._token += 1
        self.pixbuf = None
        self.alpha = 0.0
        self.source = ""
        self.widget.queue_draw()

    def show_file(self, path: Any) -> None:
        """Blur a local cover file (the library's cover art)."""
        name = str(path or "").strip()
        if not name or not view_prefs.artwork_backgrounds(self.settings):
            self.clear()
            return
        self._token += 1
        token = self._token

        def work() -> None:
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    name,
                    artwork_background.SAMPLE_WIDTH,
                    artwork_background.SAMPLE_HEIGHT,
                    False,
                )
            except GLib.Error:
                return
            GLib.idle_add(self._decoded, token, name, pixbuf)

        threading.Thread(target=work, daemon=True, name="lgl-artwork").start()

    def _decoded(self, token: int, name: str, pixbuf) -> bool:
        if token != self._token:
            return False
        self.show_pixbuf(name, pixbuf)
        return False

    def show_pixbuf(self, source: Any, pixbuf) -> None:
        """Blur an already-decoded picture (a title page's backdrop)."""
        name = str(source or "").strip()
        if pixbuf is None or not name:
            self.clear()
            return
        if not view_prefs.artwork_backgrounds(self.settings):
            self.clear()
            return
        if (
            pixbuf.get_width() > artwork_background.SAMPLE_WIDTH
            or pixbuf.get_height() > artwork_background.SAMPLE_HEIGHT
        ):
            scaled = pixbuf.scale_simple(
                artwork_background.SAMPLE_WIDTH,
                artwork_background.SAMPLE_HEIGHT,
                GdkPixbuf.InterpType.BILINEAR,
            )
        else:
            scaled = pixbuf
        if scaled is None:
            self.clear()
            return
        pixels = _pixbuf_pixels(scaled)
        self._build(name, pixels, scaled.get_width(), scaled.get_height())

    def _build(self, name: str, pixels: bytes, width: int, height: int) -> None:
        self.source = name
        self._token += 1
        token = self._token
        out_width, out_height = artwork_background.BACKGROUND_SIZE
        cache_dir = platform_support.cache_dir()

        def work() -> None:
            path = artwork_background.make_background(
                name,
                out_width,
                out_height,
                load=lambda _s: (pixels, width, height),
                cache_dir=cache_dir,
            )
            if path is not None:
                GLib.idle_add(self._ready, token, str(path))

        threading.Thread(target=work, daemon=True, name="lgl-artwork").start()

    def _ready(self, token: int, path: str) -> bool:
        if token != self._token:
            return False
        try:
            self.pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
        except GLib.Error:
            return False
        if view_prefs.transitions(self.settings) == "off":
            self.alpha = 1.0
            self.widget.queue_draw()
        else:
            self.alpha = 0.0
            GLib.timeout_add(16, self._tick, token)
        self.widget.queue_draw()
        return False

    def _tick(self, token: int) -> bool:
        if token != self._token:
            return False
        self.alpha = min(1.0, self.alpha + 0.09)
        self.widget.queue_draw()
        return self.alpha < 1.0

    def _draw(self, widget: Gtk.Widget, cr) -> bool:
        if self.pixbuf is None or self.alpha <= 0:
            return False
        width, height = widget.get_allocated_width(), widget.get_allocated_height()
        picture_width, picture_height = self.pixbuf.get_width(), self.pixbuf.get_height()
        if width <= 1 or height <= 1 or picture_width <= 0 or picture_height <= 0:
            return False
        scale = max(width / picture_width, height / picture_height)
        cr.save()
        cr.translate(
            (width - picture_width * scale) / 2,
            (height - picture_height * scale) / 2,
        )
        cr.scale(scale, scale)
        Gdk.cairo_set_source_pixbuf(cr, self.pixbuf, 0, 0)
        cr.paint_with_alpha(self.OPACITY * self.alpha)
        cr.restore()
        return False


class Images:
    """Image cache; the UI thread only ever receives a finished pixbuf."""

    def __init__(self) -> None:
        self._cache: dict[tuple[str, int, int], object] = {}
        self._lock = threading.Lock()

    def load(self, url: str, size: tuple[int, int], callback) -> None:
        if not url:
            callback(None)
            return
        key = (url, size[0], size[1])
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            callback(cached)
            return

        def work() -> None:
            pixbuf = _fetch_pixbuf(url, size)
            if pixbuf is not None:
                with self._lock:
                    self._cache[key] = pixbuf
            GLib.idle_add(callback, pixbuf)

        threading.Thread(target=work, daemon=True, name="lgl-poster").start()


def _rounded_path(cr, x: float, y: float, width: float, height: float, radius: float) -> None:
    radius = max(0.0, min(radius, width / 2, height / 2))
    cr.new_sub_path()
    cr.arc(x + width - radius, y + radius, radius, -1.5707963, 0)
    cr.arc(x + width - radius, y + height - radius, radius, 0, 1.5707963)
    cr.arc(x + radius, y + height - radius, radius, 1.5707963, 3.1415927)
    cr.arc(x + radius, y + radius, radius, 3.1415927, 4.7123890)
    cr.close_path()


def _paint_cover(cr, pixbuf, width: float, height: float, radius: float) -> None:
    """Draw *pixbuf* so it covers the rectangle, cropping the overflow."""
    if pixbuf is None or width <= 0 or height <= 0:
        return
    pw = max(1, pixbuf.get_width())
    ph = max(1, pixbuf.get_height())
    scale = max(width / pw, height / ph)
    draw_width = pw * scale
    draw_height = ph * scale
    cr.save()
    _rounded_path(cr, 0, 0, width, height, radius)
    cr.clip()
    cr.translate((width - draw_width) / 2, (height - draw_height) / 2)
    cr.scale(scale, scale)
    Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
    cr.paint()
    cr.restore()


class Poster(Gtk.DrawingArea):
    """A 2:3 image with genuinely rounded corners (GTK3 cannot clip images)."""

    def __init__(self, width: int = POSTER_SIZE[0], height: int = POSTER_SIZE[1],
                 radius: int = 10, placeholder: str = "No poster") -> None:
        super().__init__()
        self._radius = radius
        self._pixbuf = None
        self._placeholder = placeholder
        self.set_size_request(width, height)
        self.get_style_context().add_class("movie-card-poster")
        self.connect("draw", self._on_draw)

    def set_pixbuf(self, pixbuf) -> None:
        self._pixbuf = pixbuf
        self.queue_draw()

    def _on_draw(self, widget, cr) -> bool:
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        if self._pixbuf is not None:
            _rounded_path(cr, 0, 0, width, height, self._radius)
            cr.set_source_rgb(0.082, 0.094, 0.114)
            cr.fill()
            _paint_cover(cr, self._pixbuf, width, height, self._radius)
            return False

        # A missing poster is drawn as a deliberate placeholder rather than a
        # blank slab: a soft vertical wash, a faint frame glyph, and a label.
        gradient = cairo.LinearGradient(0, 0, 0, max(1, height))
        gradient.add_color_stop_rgb(0.00, 0.125, 0.141, 0.169)
        gradient.add_color_stop_rgb(1.00, 0.071, 0.082, 0.102)
        _rounded_path(cr, 0, 0, width, height, self._radius)
        cr.set_source(gradient)
        cr.fill()
        _rounded_path(cr, 0.5, 0.5, max(0, width - 1), max(0, height - 1), self._radius)
        cr.set_line_width(1.0)
        cr.set_source_rgba(0.42, 0.45, 0.50, 0.22)
        cr.stroke()

        if width < 60 or height < 80:
            return False
        icon_width = min(width * 0.34, 46.0)
        icon_height = icon_width * 1.18
        icon_x = (width - icon_width) / 2
        icon_y = (height - icon_height) / 2 - 14
        _rounded_path(cr, icon_x, icon_y, icon_width, icon_height, 5)
        cr.set_line_width(1.5)
        cr.set_source_rgba(0.50, 0.54, 0.60, 0.55)
        cr.stroke()
        cr.new_sub_path()
        cr.arc(icon_x + icon_width * 0.30, icon_y + icon_height * 0.30, 2.4, 0, 6.2831853)
        cr.stroke()
        cr.move_to(icon_x + 4, icon_y + icon_height - 6)
        cr.line_to(icon_x + icon_width * 0.42, icon_y + icon_height * 0.52)
        cr.line_to(icon_x + icon_width * 0.64, icon_y + icon_height - 12)
        cr.line_to(icon_x + icon_width * 0.80, icon_y + icon_height * 0.64)
        cr.line_to(icon_x + icon_width - 4, icon_y + icon_height - 6)
        cr.stroke()

        layout = self.create_pango_layout(self._placeholder)
        layout.set_alignment(Pango.Alignment.CENTER)
        text_width, text_height = layout.get_pixel_size()
        cr.move_to(
            max(4, (width - text_width) / 2),
            min(height - text_height - 6, icon_y + icon_height + 10),
        )
        cr.set_source_rgba(0.52, 0.56, 0.62, 0.90)
        PangoCairo.show_layout(cr, layout)
        return False


class Backdrop(Gtk.DrawingArea):
    """Wide artwork, cropped to fill the hero area."""

    def __init__(self, height: int = 430, radius: int = 0) -> None:
        super().__init__()
        self._radius = radius
        self._pixbuf = None
        self.set_size_request(-1, height)
        self.set_hexpand(True)
        self.get_style_context().add_class("movie-hero")
        self.connect("draw", self._on_draw)

    def set_pixbuf(self, pixbuf) -> None:
        self._pixbuf = pixbuf
        self.queue_draw()

    def _on_draw(self, widget, cr) -> bool:
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        if self._pixbuf is None:
            gradient = cairo.LinearGradient(0, 0, width, height)
            gradient.add_color_stop_rgb(0.00, 0.071, 0.082, 0.102)
            gradient.add_color_stop_rgb(0.55, 0.043, 0.051, 0.063)
            gradient.add_color_stop_rgb(1.00, 0.090, 0.075, 0.055)
            cr.set_source(gradient)
            cr.rectangle(0, 0, width, height)
            cr.fill()
            return False
        _paint_cover(cr, self._pixbuf, width, height, self._radius)
        return False


_KIND_NOUN = {"movie": "Movie", "tv": "TV", "anime": "Anime"}


def _kind_noun(kind: str) -> str:
    return _KIND_NOUN.get(str(kind).lower(), "Title")


def _rating_text(rating) -> str:
    try:
        return f"★ {float(rating):.1f}"
    except (TypeError, ValueError):
        return ""


def _tmdb_page_url(item) -> str:
    kind = "tv" if getattr(item, "kind", "") == "tv" else "movie"
    return f"https://www.themoviedb.org/{kind}/{int(getattr(item, 'tmdb_id', 0))}"


def _interleave(first, second) -> list:
    """Mix two result lists so one kind cannot crowd out the other."""
    mixed = []
    seen = set()
    for pair in zip_longest(first, second):
        for item in pair:
            if item is None:
                continue
            key = getattr(item, "key", None) or id(item)
            if key in seen:
                continue
            seen.add(key)
            mixed.append(item)
    return mixed


def _configure_button(label: str = "Configure providers") -> Gtk.Button:
    button = Gtk.Button(label=label)
    button.get_style_context().add_class("movie-btn-dark")
    return button


class MediaCard(Gtk.Button):
    """A poster card used by carousels and lists."""

    def __init__(
        self,
        item: MediaItem,
        images: Images,
        on_open,
        *,
        size: tuple[int, int] = CARD_SIZE,
        meta_text: str | None = None,
        show_rating: bool = True,
    ) -> None:
        super().__init__()
        self.set_relief(Gtk.ReliefStyle.NONE)
        self.set_halign(Gtk.Align.START)
        self.set_valign(Gtk.Align.START)
        self.get_style_context().add_class("movie-card")
        self.set_tooltip_text(item.title)
        try:
            self.get_accessible().set_name(item.title)
        except (AttributeError, TypeError):
            pass
        self.connect("clicked", lambda *_a: on_open(item))

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        poster = Poster(size[0], size[1], radius=9)
        holder = Gtk.Overlay()
        holder.add(poster)
        if show_rating and item.rating is not None:
            badge = Gtk.Label(label=_rating_text(item.rating))
            badge.set_halign(Gtk.Align.END)
            badge.set_valign(Gtk.Align.START)
            badge.set_margin_top(7)
            badge.set_margin_end(7)
            badge.get_style_context().add_class("movie-card-rating")
            badge.set_tooltip_text(f"Rated {item.rating:g} out of 10")
            holder.add_overlay(badge)
        box.pack_start(holder, False, False, 0)

        title = Gtk.Label(label=item.title, xalign=0)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_max_width_chars(20)
        title.set_width_chars(1)
        title.get_style_context().add_class("movie-card-title")
        box.pack_start(title, False, False, 0)

        label = meta_text
        if label is None:
            label = item.year or _kind_noun(item.kind)
        meta = Gtk.Label(label=label, xalign=0)
        meta.set_ellipsize(Pango.EllipsizeMode.END)
        meta.set_max_width_chars(22)
        meta.set_width_chars(1)
        meta.get_style_context().add_class("movie-card-meta")
        box.pack_start(meta, False, False, 0)

        self.add(box)
        self._poster = poster
        images.load(item.poster_url, size, self._set_pixbuf)

    def _set_pixbuf(self, pixbuf) -> bool:
        self._poster.set_pixbuf(pixbuf)
        return False


class PosterTile(Gtk.FlowBoxChild):
    """One poster in a grid: artwork, title, and one muted line."""

    def __init__(self, item: MediaItem, images: Images, on_open) -> None:
        super().__init__()
        self.get_style_context().add_class("movie-tile")
        button = Gtk.Button()
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.set_halign(Gtk.Align.FILL)
        button.get_style_context().add_class("movie-card")
        button.set_tooltip_text(item.title)
        try:
            button.get_accessible().set_name(item.title)
        except (AttributeError, TypeError):
            pass
        button.connect("clicked", lambda *_a: on_open(item))

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        box.set_size_request(POSTER_SIZE[0], -1)
        poster = Poster(*POSTER_SIZE, radius=10)
        # The poster keeps its 2:3 shape; a long title ellipsizes instead of
        # stretching the artwork (which made missing posters look like slabs).
        poster.set_halign(Gtk.Align.START)
        box.pack_start(poster, False, False, 0)

        title = Gtk.Label(label=item.title, xalign=0)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_max_width_chars(22)
        title.set_width_chars(1)
        title.set_tooltip_text(item.title)
        title.get_style_context().add_class("movie-card-title")
        box.pack_start(title, False, False, 0)

        bits = [item.year, _kind_noun(item.kind)]
        meta = Gtk.Label(label=" · ".join(bit for bit in bits if bit), xalign=0)
        meta.set_ellipsize(Pango.EllipsizeMode.END)
        meta.set_width_chars(1)
        meta.get_style_context().add_class("movie-card-meta")
        box.pack_start(meta, False, False, 0)

        button.add(box)
        self.add(button)
        images.load(item.poster_url, POSTER_SIZE, poster.set_pixbuf)


class AniPmTile(Gtk.FlowBoxChild):
    """One ani.pm catalogue result: poster, title, meta and an 18+ badge.

    Metadata is exactly what the ani.pm catalogue API returned - year, format
    and score when present, genres in the tooltip, and a visible 18+ badge
    when the title is flagged ``adult``.  Nothing is filled in by guesswork.
    """

    def __init__(self, item: "anipm.AniTitle", images: Images, on_open) -> None:
        super().__init__()
        self.get_style_context().add_class("movie-tile")
        button = Gtk.Button()
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.set_halign(Gtk.Align.FILL)
        button.get_style_context().add_class("movie-card")
        tooltip = item.title
        if item.genres:
            tooltip += "\n" + " · ".join(item.genres)
        button.set_tooltip_text(tooltip)
        try:
            button.get_accessible().set_name(item.title)
        except (AttributeError, TypeError):
            pass
        button.connect("clicked", lambda *_a: on_open(item))

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        box.set_size_request(POSTER_SIZE[0], -1)
        poster = Poster(*POSTER_SIZE, radius=10)
        poster.set_halign(Gtk.Align.START)
        holder = Gtk.Overlay()
        holder.add(poster)
        if item.adult:
            badge = Gtk.Label(label="18+")
            badge.set_halign(Gtk.Align.END)
            badge.set_valign(Gtk.Align.START)
            badge.set_margin_top(7)
            badge.set_margin_end(7)
            badge.get_style_context().add_class("movie-adult-badge")
            badge.set_tooltip_text("Marked adult (18+) by ani.pm")
            holder.add_overlay(badge)
        box.pack_start(holder, False, False, 0)

        title = Gtk.Label(label=item.title, xalign=0)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_max_width_chars(22)
        title.set_width_chars(1)
        title.set_tooltip_text(item.title)
        title.get_style_context().add_class("movie-card-title")
        box.pack_start(title, False, False, 0)

        bits = [item.year, item.format, _rating_text(item.score)]
        meta = Gtk.Label(label=" · ".join(bit for bit in bits if bit), xalign=0)
        meta.set_ellipsize(Pango.EllipsizeMode.END)
        meta.set_width_chars(1)
        meta.get_style_context().add_class("movie-card-meta")
        box.pack_start(meta, False, False, 0)

        button.add(box)
        self.add(button)
        images.load(item.poster_url, POSTER_SIZE, poster.set_pixbuf)


class MoviesWindow(Gtk.Box):
    """The cinematic Movies & TV surface, embedded in the main window."""

    def __init__(self, parent, settings) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.get_style_context().add_class("movie-window")
        self._parent = parent
        self._settings = settings
        self._images = Images()
        self._client = TmdbClient(load_api_key(settings=settings))
        self._mal_client = MalClient(load_client_id(settings=settings))
        self._ani_client = anipm.AniPmClient()
        self._current_ani_series = None
        self._current_ani_episodes: list = []
        self._anime_token = 0
        self._anime_loaded = False
        self._anime_detail_token = 0
        self._kind = "movie"
        self._browse_loaded_kind: str | None = None
        self._search_token = 0
        self._home_token = 0
        self._detail_token = 0
        self._season_token = 0
        self._mylist_token = 0
        self._browse_token = 0
        self._hero_image_token = 0
        self._current_id = 0
        self._current_item: MediaItem | None = None
        self._current_details: MediaDetails | None = None
        self._back_target = "home"
        self._details_origin = "home"
        self._home_failed = False
        self._suppress_search = False
        self._nav_buttons: dict[str, Gtk.Button] = {}
        self._detail_state: dict[str, Any] = {}
        self._initial_loaded = False
        self._webview = None
        self._playback_attempts: list[tuple[str, str]] = []
        self._playback_index = 0
        self._playback_title = ""
        self._playback_reason = ""
        self._player_fullscreen = False

        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._stack.set_transition_duration(160)

        self._details_host = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._details_scroll = Gtk.ScrolledWindow()
        self._details_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._details_scroll.get_style_context().add_class("movie-scroll")
        self._details_scroll.add(self._details_host)
        self._player = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._player.set_margin_start(24)
        self._player.set_margin_end(24)
        self._player.set_margin_top(16)
        self._player.set_margin_bottom(16)

        self._build_header()

        self._stack.add_named(self._build_home_page(), "home")
        self._stack.add_named(self._build_browse_page(), "browse")
        self._stack.add_named(self._build_search_page(), "search")
        self._stack.add_named(self._build_anime_page(), "anime")
        self._stack.add_named(self._build_mylist_page(), "mylist")
        self._stack.add_named(self._build_history_page(), "history")
        self._stack.add_named(self._details_scroll, "details")
        self._stack.add_named(self._player, "player")

        self.pack_start(self._header, False, False, 0)
        self.pack_start(self._stack, True, True, 0)

        self._stack.connect("notify::visible-child-name", self._on_page_changed)

        provider = _cinematic_provider()
        if provider is not None:
            priority = Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1
            self.get_style_context().add_provider(provider, priority)

        self._stack.set_visible_child_name("home")
        self._set_active_nav("home")
        self._update_back()
        self.show_all()
        self._render_continue()
        self._render_history()
        self._render_mylist_empty()
        self.connect("destroy", lambda *_a: self.stop_playback())
        self.connect("map", lambda *_a: self._on_map())

    # -- shell --------------------------------------------------------------

    def _build_header(self) -> None:
        header = Gtk.HeaderBar()
        # This bar lives inside the app's own window, so it must not offer a
        # second set of window controls (the stray close button) or pretend
        # to be a titlebar.
        header.set_show_close_button(False)
        header.get_style_context().add_class("movie-nav")
        header.set_title("Movies & TV")
        provider = _cinematic_provider()
        if provider is not None:
            header.get_style_context().add_provider(
                provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1
            )

        self._back = Gtk.Button.new_from_icon_name(
            "go-previous-symbolic", Gtk.IconSize.BUTTON
        )
        self._back.set_tooltip_text("Back")
        try:
            self._back.get_accessible().set_name("Back")
        except (AttributeError, TypeError):
            pass
        self._back.get_style_context().add_class("movie-icon-button")
        self._back.connect("clicked", lambda *_a: self._navigate_back())
        self._back.set_no_show_all(True)
        self._back.hide()
        header.pack_start(self._back)

        brand = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        brand.set_valign(Gtk.Align.CENTER)
        mark = Gtk.Label(label="●")
        mark.get_style_context().add_class("movie-brand-mark")
        brand.pack_start(mark, False, False, 0)
        name = Gtk.Label(label="Movies & TV")
        name.get_style_context().add_class("movie-brand")
        brand.pack_start(name, False, False, 0)
        header.pack_start(brand)

        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        nav.set_halign(Gtk.Align.CENTER)
        nav.set_valign(Gtk.Align.CENTER)
        for key, label in _NAV_ITEMS:
            button = Gtk.Button(label=label)
            button.set_relief(Gtk.ReliefStyle.NONE)
            button.get_style_context().add_class("movie-nav-item")
            button.connect("clicked", lambda _b, page=key: self._activate_nav(page))
            nav.pack_start(button, False, False, 0)
            self._nav_buttons[key] = button
        header.set_custom_title(nav)

        self._search = Gtk.SearchEntry()
        self._search.set_placeholder_text("Search movies, TV and anime")
        self._search.set_width_chars(18)
        self._search.get_style_context().add_class("movie-search")
        self._search.connect("search-changed", self._on_search_changed)
        self._search.connect("activate", lambda *_a: self._activate_nav("search"))

        self._gear = Gtk.Button.new_from_icon_name(
            "preferences-system-symbolic", Gtk.IconSize.BUTTON
        )
        self._gear.set_tooltip_text("Provider settings")
        try:
            self._gear.get_accessible().set_name("Provider settings")
        except (AttributeError, TypeError):
            pass
        # Settings gear — opens the Media settings tab.
        self._gear.get_style_context().add_class("movie-icon-button")
        self._gear.set_tooltip_text("Media settings (providers, audio, backdrop)")
        self._gear.connect("clicked", lambda *_a: self._open_media_settings())
        header.pack_end(self._gear)
        header.pack_end(self._search)

        self._header = header
        try:
            header.set_show_close_button(False)
        except (AttributeError, TypeError):
            pass
        try:
            header.set_decoration_layout("")
        except (AttributeError, TypeError):
            pass

    def _on_map(self) -> None:
        if not self._initial_loaded:
            self._initial_loaded = True
            self._load_home()

    def refresh_providers(self) -> None:
        self._client = TmdbClient(load_api_key(settings=self._settings))
        self._mal_client = MalClient(load_client_id(settings=self._settings))
        self._home_failed = False
        self._anime_loaded = False
        self._browse_loaded_kind = None
        try:
            self._load_home()
        except Exception:  # noqa: BLE001
            pass

    def stop_playback(self) -> None:
        if getattr(self, "_player_fullscreen", False):
            self._set_player_fullscreen(False)
        view = getattr(self, "_webview", None)
        if view is not None:
            try:
                view.stop_loading()
            except Exception:  # noqa: BLE001
                pass
            try:
                view.load_uri("about:blank")
            except Exception:  # noqa: BLE001
                pass
            self._webview = None
        self._player_frame = None
        for child in list(self._player.get_children()):
            try:
                self._player.remove(child)
            except Exception:  # noqa: BLE001
                pass
            try:
                child.destroy()
            except Exception:  # noqa: BLE001
                pass

    def _activate_nav(self, page: str) -> None:
        if page == "movies":
            self._open_browse("movie")
            return
        if page == "tv":
            self._open_browse("tv")
            return
        if page == "search":
            self._stack.set_visible_child_name("search")
            self._set_active_nav("search")
            self._update_back()
            if not self._search.get_text().strip():
                self._search_status.set_text("Type a title above to search TMDB.")
                self._search_empty.show()
            self._search.grab_focus()
            return
        if page == "mylist":
            self._stack.set_visible_child_name("mylist")
            self._set_active_nav("mylist")
            self._update_back()
            self._load_mylist()
            return
        if page == "history":
            self._stack.set_visible_child_name("history")
            self._set_active_nav("history")
            self._update_back()
            self._render_history()
            return
        if page == "anime":
            self._stack.set_visible_child_name("anime")
            self._set_active_nav("anime")
            self._update_back()
            if not self._anime_loaded:
                self._load_anime()
            return
        # Home
        self._stack.set_visible_child_name("home")
        self._set_active_nav("home")
        self._update_back()
        self._render_continue()
        if self._home_failed:
            self._load_home()

    def _set_active_nav(self, key: str) -> None:
        for name, button in self._nav_buttons.items():
            context = button.get_style_context()
            if name == key:
                context.add_class("active")
            else:
                context.remove_class("active")

    def _nav_key_for_page(self, name: str) -> str:
        if name in ("details", "player"):
            name = self._details_origin or "home"
        if name == "browse":
            return "movies" if self._kind == "movie" else "tv"
        return name

    def _update_back(self) -> None:
        name = self._stack.get_visible_child_name() or "home"
        show = name in ("details", "player")
        if show:
            self._back.show()
        else:
            self._back.hide()

    def _on_page_changed(self, stack, _param) -> None:
        name = stack.get_visible_child_name()
        if name != "player":
            self.stop_playback()
        self._update_back()

    def _streaming_provider(self) -> str:
        try:
            return streaming.clean_provider(self._settings.get("streaming_provider", streaming.DEFAULT_PROVIDER))
        except Exception:  # noqa: BLE001
            return streaming.DEFAULT_PROVIDER

    def _anime_provider(self) -> str:
        try:
            return streaming.clean_anime_provider(
                self._settings.get(
                    "anime_streaming_provider",
                    self._streaming_provider(),
                )
            )
        except Exception:  # noqa: BLE001
            return streaming.DEFAULT_ANIME_PROVIDER

    def _set_streaming_provider(self, provider: str) -> None:
        provider = streaming.clean_provider(provider)
        try:
            self._settings.set("streaming_provider", provider)
        except (OSError, ValueError, AttributeError):
            pass
        # Rebuild nothing; URL builder reads provider at play time.

    def _set_anime_provider(self, provider: str) -> None:
        provider = streaming.clean_anime_provider(provider)
        try:
            self._settings.set("anime_streaming_provider", provider)
        except (OSError, ValueError, AttributeError):
            pass

    def _open_media_settings(self) -> None:
        parent = self._parent
        if parent is None or not hasattr(parent, "show_settings"):
            return
        try:
            parent.show_settings(page="media")
        except TypeError:
            try:
                parent.show_settings()
            except Exception:  # noqa: BLE001
                pass

    def _open_provider_settings(self) -> None:
        parent = self._parent
        if parent is None or not hasattr(parent, "show_settings"):
            return
        try:
            parent.show_settings(page="providers")
        except TypeError:
            # Older main windows only accept the shortcuts flag.
            try:
                parent.show_settings()
            except Exception:  # noqa: BLE001 - settings are optional here
                pass
        # The user may have just added/changed a provider; pick it up.
        try:
            self.refresh_providers()
        except Exception:  # noqa: BLE001 - best-effort
            pass
        if not self._anime_loaded:
            try:
                self._load_anime()
            except Exception:  # noqa: BLE001 - best-effort
                pass

    def _navigate_back(self) -> None:
        target = self._back_target if self._back_target else "home"
        if self._stack.get_visible_child_name() == "player":
            target = self._back_target if self._back_target != "player" else "details"
        self._stack.set_visible_child_name(target)
        self._set_active_nav(self._nav_key_for_page(target))
        self._update_back()
        if target == "home":
            self._render_continue()

    # -- shared widgets -----------------------------------------------------

    def _scroller(self) -> Gtk.ScrolledWindow:
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.get_style_context().add_class("movie-scroll")
        return scroller

    def _page_title(self, text: str) -> Gtk.Label:
        label = Gtk.Label(label=text, xalign=0)
        label.get_style_context().add_class("movie-page-title")
        return label

    def _icon_button(self, icon: str, tooltip: str, callback) -> Gtk.Button:
        button = Gtk.Button.new_from_icon_name(icon, Gtk.IconSize.BUTTON)
        button.set_valign(Gtk.Align.CENTER)
        button.set_tooltip_text(tooltip)
        try:
            button.get_accessible().set_name(tooltip)
        except (AttributeError, TypeError):
            pass
        button.get_style_context().add_class("movie-arrow")
        button.connect("clicked", lambda *_a: callback())
        return button

    def _primary_button(self, label: str, icon: str, callback) -> Gtk.Button:
        button = Gtk.Button()
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.get_style_context().add_class("movie-btn-primary")
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        image = Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.BUTTON)
        box.pack_start(image, False, False, 0)
        box.pack_start(Gtk.Label(label=label), False, False, 0)
        button.add(box)
        button.connect("clicked", lambda *_a: callback())
        return button

    def _dark_button(self, label: str, callback) -> Gtk.Button:
        button = Gtk.Button(label=label)
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.get_style_context().add_class("movie-btn-dark")
        button.connect("clicked", lambda *_a: callback())
        return button

    def _empty_state(
        self,
        title: str,
        body: str,
        action_label: str | None = None,
        action=None,
        icon: str = "video-x-generic-symbolic",
    ) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9)
        box.get_style_context().add_class("movie-empty")
        box.set_halign(Gtk.Align.FILL)
        image = Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.DIALOG)
        image.set_halign(Gtk.Align.START)
        image.get_style_context().add_class("movie-empty-icon")
        box.pack_start(image, False, False, 0)
        heading = Gtk.Label(label=title, xalign=0)
        heading.get_style_context().add_class("movie-empty-title")
        box.pack_start(heading, False, False, 0)
        note = Gtk.Label(label=body, xalign=0)
        note.set_line_wrap(True)
        note.get_style_context().add_class("movie-status")
        box.pack_start(note, False, False, 0)
        if action_label and action is not None:
            button = self._dark_button(action_label, action)
            button.set_halign(Gtk.Align.START)
            button.set_margin_top(4)
            box.pack_start(button, False, False, 0)
        return box

    def _fill_meta(self, box: Gtk.Box, bits, rating=None) -> None:
        for child in box.get_children():
            box.remove(child)
        written = False

        def add(text: str, css: str) -> None:
            nonlocal written
            if written:
                dot = Gtk.Label(label="·")
                dot.get_style_context().add_class("movie-meta")
                box.pack_start(dot, False, False, 0)
            label = Gtk.Label(label=text)
            label.get_style_context().add_class(css)
            box.pack_start(label, False, False, 0)
            written = True

        for text in bits:
            if text:
                add(str(text), "movie-meta")
        if rating is not None:
            add("★", "movie-rating-star")
            add(f"{rating:g}", "movie-meta")
        box.show_all()

    def _copy_link(self, item, feedback: Gtk.Label) -> None:
        url = _tmdb_page_url(item)
        try:
            clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            clipboard.set_text(url, -1)
            clipboard.store()
        except Exception:  # noqa: BLE001 - a missing clipboard must not break UI
            pass
        feedback.set_text("TMDB link copied to the clipboard")
        feedback.show()
        GLib.timeout_add(2400, self._hide_feedback, feedback)

    def _hide_feedback(self, label: Gtk.Label) -> bool:
        try:
            label.set_text("")
            label.hide()
        except Exception:  # noqa: BLE001 - the card may have been replaced
            pass
        return False

    # -- home ---------------------------------------------------------------

    def _build_home_page(self) -> Gtk.Widget:
        scroller = self._scroller()
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        scroller.add(inner)

        self._home_hero = Gtk.Overlay()
        self._home_hero.set_size_request(-1, 430)
        self._hero_backdrop = Backdrop(430)
        self._home_hero.add(self._hero_backdrop)
        scrim = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        scrim.set_hexpand(True)
        scrim.set_vexpand(True)
        scrim.get_style_context().add_class("movie-hero-scrim")
        self._home_hero.add_overlay(scrim)
        self._hero_content: Gtk.Widget | None = None
        inner.pack_start(self._home_hero, False, False, 0)

        sections = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=30)
        sections.set_margin_start(32)
        sections.set_margin_end(32)
        sections.set_margin_top(26)
        sections.set_margin_bottom(40)
        inner.pack_start(sections, False, False, 0)

        self._home_notice_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._home_notice_box.set_no_show_all(True)
        sections.pack_start(self._home_notice_box, False, False, 0)

        self._continue_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self._continue_section.set_no_show_all(True)
        sections.pack_start(self._continue_section, False, False, 0)

        (
            self._trending_section,
            self._trending_cards,
            self._trending_scroller,
            self._trending_count,
            self._home_spinner,
        ) = self._carousel_section(
            "Trending Now",
            "This week on TMDB",
            "View All",
            lambda: self._open_browse("movie"),
        )
        sections.pack_start(self._trending_section, False, False, 0)
        self._render_home_hero(None)
        return scroller

    def _carousel_section(
        self, title: str, subtitle: str, link_label: str, link_callback
    ):
        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        heading = Gtk.Label(label=title, xalign=0)
        heading.get_style_context().add_class("movie-section-title")
        head.pack_start(heading, False, False, 0)
        count = Gtk.Label(label="")
        count.set_no_show_all(True)
        count.get_style_context().add_class("movie-section-count")
        head.pack_start(count, False, False, 0)
        sub = Gtk.Label(label=subtitle, xalign=0)
        sub.get_style_context().add_class("movie-section-sub")
        head.pack_start(sub, False, False, 0)
        spacer = Gtk.Box()
        head.pack_start(spacer, True, True, 0)
        spinner = Gtk.Spinner()
        spinner.set_no_show_all(True)
        head.pack_end(spinner, False, False, 0)
        link = Gtk.Button(label=link_label)
        link.set_relief(Gtk.ReliefStyle.NONE)
        link.get_style_context().add_class("movie-link")
        link.connect("clicked", lambda *_a: link_callback())
        head.pack_end(link, False, False, 0)
        section.pack_start(head, False, False, 0)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        left = self._icon_button(
            "go-previous-symbolic", "Scroll left", lambda: self._scroll_carousel(scroller, -1)
        )
        left.set_valign(Gtk.Align.CENTER)
        right = self._icon_button(
            "go-next-symbolic", "Scroll right", lambda: self._scroll_carousel(scroller, 1)
        )
        right.set_valign(Gtk.Align.CENTER)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        scroller.set_kinetic_scrolling(True)
        scroller.set_overlay_scrolling(False)
        scroller.get_style_context().add_class("movie-scroll")
        scroller.set_size_request(-1, CARD_SIZE[1] + 72)
        cards = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        cards.set_margin_start(2)
        cards.set_margin_end(2)
        scroller.add(cards)
        row.pack_start(left, False, False, 0)
        row.pack_start(scroller, True, True, 0)
        row.pack_start(right, False, False, 0)
        section.pack_start(row, False, False, 0)
        return section, cards, scroller, count, spinner

    def _scroll_carousel(self, scroller: Gtk.ScrolledWindow, direction: int) -> None:
        adjustment = scroller.get_hadjustment()
        if adjustment is None:
            return
        page = adjustment.get_page_size()
        step = max(240.0, page * 0.8)
        upper = max(adjustment.get_lower(), adjustment.get_upper() - page)
        value = min(max(adjustment.get_value() + direction * step, adjustment.get_lower()), upper)
        adjustment.set_value(value)

    def _render_home_hero(self, item: MediaItem | None, notice: str | None = None) -> None:
        if self._hero_content is not None:
            self._home_hero.remove(self._hero_content)
            self._hero_content = None
        self._hero_image_token += 1
        token = self._hero_image_token
        self._hero_backdrop.set_pixbuf(None)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=11)
        content.set_halign(Gtk.Align.FILL)
        content.set_valign(Gtk.Align.END)
        content.set_margin_start(46)
        content.set_margin_end(46)
        content.set_margin_bottom(34)

        badge = Gtk.Label(label="MOVIES & TV" if item is None else (
            "TV" if item.kind == "tv" else "MOVIE"
        ))
        badge.set_halign(Gtk.Align.START)
        badge.get_style_context().add_class("movie-hero-badge")
        content.pack_start(badge, False, False, 0)

        if item is not None:
            rating_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            if item.rating is not None:
                star = Gtk.Label(label="★")
                star.get_style_context().add_class("movie-rating-star")
                value = Gtk.Label(label=f"{item.rating:g}")
                value.get_style_context().add_class("movie-meta")
                rating_row.pack_start(star, False, False, 0)
                rating_row.pack_start(value, False, False, 0)
            content.pack_start(rating_row, False, False, 0)

            title = Gtk.Label(label=item.title, xalign=0)
            title.set_line_wrap(True)
            title.set_lines(2)
            title.set_ellipsize(Pango.EllipsizeMode.END)
            title.set_max_width_chars(38)
            title.get_style_context().add_class("movie-hero-title")
            content.pack_start(title, False, False, 0)

            meta = Gtk.Label(
                label=" · ".join(
                    bit for bit in (item.year, _kind_noun(item.kind)) if bit
                ),
                xalign=0,
            )
            meta.get_style_context().add_class("movie-meta")
            content.pack_start(meta, False, False, 0)

            overview = Gtk.Label(label=item.overview or "No synopsis yet.")
            overview.set_xalign(0)
            overview.set_line_wrap(True)
            overview.set_lines(2)
            overview.set_ellipsize(Pango.EllipsizeMode.END)
            overview.set_max_width_chars(64)
            overview.get_style_context().add_class("movie-overview")
            content.pack_start(overview, False, False, 0)
        else:
            title = Gtk.Label(label="Movies & TV", xalign=0)
            title.get_style_context().add_class("movie-hero-title")
            content.pack_start(title, False, False, 0)
            overview = Gtk.Label(label=notice or "Loading this week's titles…")
            overview.set_xalign(0)
            overview.set_line_wrap(True)
            overview.set_max_width_chars(64)
            overview.get_style_context().add_class("movie-overview")
            content.pack_start(overview, False, False, 0)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        if item is not None:
            actions.pack_start(
                self._primary_button(
                    "Watch Now",
                    "media-playback-start-symbolic",
                    lambda: self._hero_watch(item),
                ),
                False,
                False,
                0,
            )
            actions.pack_start(
                self._dark_button("Details", lambda: self._open_details(item)),
                False,
                False,
                0,
            )

        self._hero_feedback = Gtk.Label(label="")
        self._hero_feedback.get_style_context().add_class("movie-status")
        self._hero_feedback.set_no_show_all(True)
        actions.pack_start(self._hero_feedback, False, False, 0)

        if item is not None:
            actions.pack_start(
                self._dark_button("Share", lambda: self._copy_link(item, self._hero_feedback)),
                False,
                False,
                0,
            )
        content.pack_start(actions, False, False, 0)

        self._home_hero.add_overlay(content)
        self._hero_content = content
        content.show_all()
        self._hero_feedback.hide()

        if item is not None:
            url = item.backdrop_url or item.poster_url
            self._images.load(url, BACKDROP_SIZE, lambda pixbuf: self._apply_hero_image(pixbuf, token))

    def _apply_hero_image(self, pixbuf, token: int) -> bool:
        if token != self._hero_image_token:
            return False
        try:
            self._hero_backdrop.set_pixbuf(pixbuf)
        except Exception:  # noqa: BLE001 - the window may be closing
            pass
        return False

    def _hero_watch(self, item: MediaItem) -> None:
        if item.kind == "tv":
            self._open_details(item)
        else:
            self._play(item)

    def _render_continue(self) -> None:
        section = self._continue_section
        for child in section.get_children():
            section.remove(child)
        entries = media_history.recent(self._settings, 14)
        if not entries:
            section.hide()
            return
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        heading = Gtk.Label(label="Continue Watching", xalign=0)
        heading.get_style_context().add_class("movie-section-title")
        head.pack_start(heading, False, False, 0)
        sub = Gtk.Label(label="Recently watched", xalign=0)
        sub.get_style_context().add_class("movie-section-sub")
        head.pack_start(sub, False, False, 0)
        spacer = Gtk.Box()
        head.pack_start(spacer, True, True, 0)
        link = Gtk.Button(label="History")
        link.set_relief(Gtk.ReliefStyle.NONE)
        link.get_style_context().add_class("movie-link")
        link.connect("clicked", lambda *_a: self._activate_nav("history"))
        head.pack_end(link, False, False, 0)
        section.pack_start(head, False, False, 0)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        scroller.get_style_context().add_class("movie-scroll")
        scroller.set_size_request(-1, CARD_SIZE[1] + 72)
        cards = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        cards.set_margin_start(2)
        cards.set_margin_end(2)
        for entry in entries:
            item = MediaItem(
                kind=entry["kind"],
                tmdb_id=entry["tmdb_id"],
                title=entry["title"],
                year=entry["year"],
                poster_url=entry["poster_url"],
            )
            cards.pack_start(
                MediaCard(
                    item,
                    self._images,
                    self._open_details,
                    meta_text="Recently watched",
                    show_rating=False,
                ),
                False,
                False,
                0,
            )
        scroller.add(cards)
        row.pack_start(self._icon_button("go-previous-symbolic", "Scroll left",
                                         lambda: self._scroll_carousel(scroller, -1)),
                       False, False, 0)
        row.pack_start(scroller, True, True, 0)
        row.pack_start(self._icon_button("go-next-symbolic", "Scroll right",
                                         lambda: self._scroll_carousel(scroller, 1)),
                       False, False, 0)
        section.pack_start(row, False, False, 0)
        section.set_no_show_all(False)
        section.show_all()

    def _load_home(self) -> None:
        self._home_token += 1
        token = self._home_token
        self._spin(self._home_spinner, True)

        def work() -> None:
            results: dict[str, list[MediaItem]] = {}
            errors: dict[str, TmdbError] = {}
            for kind in ("movie", "tv"):
                try:
                    results[kind] = self._client.trending(kind)
                except TmdbError as error:
                    errors[kind] = error
            GLib.idle_add(self._apply_home, token, results, errors)

        threading.Thread(target=work, daemon=True, name="lgl-home").start()

    def _apply_home(self, token: int, results, errors) -> bool:
        if token != self._home_token:
            return False
        self._spin(self._home_spinner, False)
        self._render_continue()
        items = _interleave(results.get("movie", []), results.get("tv", []))
        missing = any(getattr(error, "kind", "") == "missing-key" for error in errors.values())
        if items:
            self._home_failed = False
            self._home_notice_box.hide()
            self._render_home_hero(items[0])
            self._fill_trending(items)
            self._trending_section.show_all()
            return False
        self._home_failed = True
        self._trending_section.hide()
        if missing:
            message = "Add a TMDB API key to browse movies and TV."
            self._set_home_notice(
                "TMDB is not connected",
                message + " Use Settings → Providers, or set TMDB_API_KEY.",
                "Configure providers",
                self._open_provider_settings,
            )
        elif errors:
            detail = str(next(iter(errors.values())))
            self._set_home_notice(
                "Trending could not be loaded",
                detail,
                "Try again",
                self._load_home,
            )
        else:
            self._set_home_notice(
                "Nothing is trending yet",
                "TMDB returned no titles this week. Try a search instead.",
                "Try again",
                self._load_home,
            )
        self._render_home_hero(None, notice="Connect TMDB to see what is trending.")
        return False

    def _set_home_notice(self, title, body, action_label=None, action=None) -> None:
        for child in self._home_notice_box.get_children():
            self._home_notice_box.remove(child)
        self._home_notice_box.add(
            self._empty_state(title, body, action_label, action, icon="network-error-symbolic")
        )
        self._home_notice_box.set_no_show_all(False)
        self._home_notice_box.show_all()

    def _fill_trending(self, items) -> None:
        cards = self._trending_cards
        for child in cards.get_children():
            cards.remove(child)
        for item in items[:20]:
            cards.pack_start(
                MediaCard(
                    item,
                    self._images,
                    self._open_details,
                    meta_text=_kind_noun(item.kind),
                ),
                False,
                False,
                0,
            )
        cards.show_all()
        self._trending_count.set_text(str(len(items)))
        self._trending_count.show()

    @staticmethod
    def _spin(spinner: Gtk.Spinner, busy: bool) -> None:
        if busy:
            spinner.show()
            spinner.start()
        else:
            spinner.stop()
            spinner.hide()

    # -- browse (Movies / TV) ----------------------------------------------

    def _flowbox(self) -> Gtk.FlowBox:
        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_valign(Gtk.Align.START)
        flow.set_column_spacing(16)
        flow.set_row_spacing(18)
        flow.set_homogeneous(True)
        flow.set_min_children_per_line(2)
        flow.set_max_children_per_line(9)
        flow.set_margin_top(10)
        flow.set_margin_bottom(24)
        return flow

    def _build_browse_page(self) -> Gtk.Widget:
        scroller = self._scroller()
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        inner.set_margin_start(32)
        inner.set_margin_end(32)
        inner.set_margin_top(24)
        inner.set_margin_bottom(20)
        scroller.add(inner)

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self._browse_title = self._page_title("Movies")
        head.pack_start(self._browse_title, False, False, 0)
        self._browse_count = Gtk.Label(label="")
        self._browse_count.set_no_show_all(True)
        self._browse_count.get_style_context().add_class("movie-section-count")
        head.pack_start(self._browse_count, False, False, 0)
        spacer = Gtk.Box()
        head.pack_start(spacer, True, True, 0)
        self._browse_spinner = Gtk.Spinner()
        self._browse_spinner.set_no_show_all(True)
        head.pack_end(self._browse_spinner, False, False, 0)
        self._browse_configure = _configure_button()
        self._browse_configure.set_no_show_all(True)
        self._browse_configure.connect("clicked", lambda *_a: self._open_provider_settings())
        head.pack_end(self._browse_configure, False, False, 0)
        inner.pack_start(head, False, False, 0)

        self._browse_status = Gtk.Label(label="", xalign=0)
        self._browse_status.set_line_wrap(True)
        self._browse_status.get_style_context().add_class("movie-status")
        inner.pack_start(self._browse_status, False, False, 6)

        self._browse_flow = self._flowbox()
        flow_scroller = Gtk.ScrolledWindow()
        flow_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        flow_scroller.set_overlay_scrolling(False)
        flow_scroller.get_style_context().add_class("movie-scroll")
        flow_scroller.add(self._browse_flow)
        inner.pack_start(flow_scroller, True, True, 0)
        return scroller

    def _open_browse(self, kind: str) -> None:
        kind = "tv" if kind == "tv" else "movie"
        self._suppress_search = True
        self._search.set_text("")
        self._suppress_search = False
        self._search_token += 1
        self._kind = kind
        self._browse_title.set_text("TV Shows" if kind == "tv" else "Movies")
        self._browse_status.set_text("")
        self._stack.set_visible_child_name("browse")
        self._set_active_nav("tv" if kind == "tv" else "movies")
        self._update_back()
        if self._browse_loaded_kind != kind:
            self._load_browse(kind)

    def _load_browse(self, kind: str) -> None:
        self._browse_token += 1
        token = self._browse_token
        self._spin(self._browse_spinner, True)
        self._browse_configure.hide()
        self._clear_flow(self._browse_flow)
        self._browse_count.set_text("")
        self._browse_count.hide()
        self._browse_status.set_text("Loading…")

        def work() -> None:
            try:
                items = self._client.trending(kind)
            except TmdbError as error:
                GLib.idle_add(self._browse_error, token, error)
                return
            GLib.idle_add(self._apply_browse, token, kind, items)

        threading.Thread(target=work, daemon=True, name="lgl-tmdb-browse").start()

    def _apply_browse(self, token: int, kind: str, items) -> bool:
        if token != self._browse_token or kind != self._kind:
            return False
        self._spin(self._browse_spinner, False)
        self._browse_loaded_kind = kind
        if not items:
            self._browse_status.set_text(
                "Nothing came back. Check the connection, or search instead."
            )
            return False
        self._browse_count.set_text(str(len(items)))
        self._browse_count.show()
        self._browse_status.set_text("Trending this week")
        self._fill_flow(self._browse_flow, items)
        return False

    def _browse_error(self, token: int, error: TmdbError) -> bool:
        if token != self._browse_token:
            return False
        self._spin(self._browse_spinner, False)
        self._browse_status.set_text(str(error))
        if getattr(error, "kind", "") == "missing-key":
            self._browse_configure.show()
        return False

    def _fill_flow(self, flow: Gtk.FlowBox, items) -> None:
        self._clear_flow(flow)
        for item in items:
            flow.add(PosterTile(item, self._images, self._open_details))
        flow.show_all()

    @staticmethod
    def _clear_flow(flow: Gtk.FlowBox) -> None:
        for child in flow.get_children():
            flow.remove(child)

    # -- search -------------------------------------------------------------

    def _build_search_page(self) -> Gtk.Widget:
        scroller = self._scroller()
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        inner.set_margin_start(32)
        inner.set_margin_end(32)
        inner.set_margin_top(24)
        inner.set_margin_bottom(20)
        scroller.add(inner)

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        head.pack_start(self._page_title("Search"), False, False, 0)
        spacer = Gtk.Box()
        head.pack_start(spacer, True, True, 0)
        self._search_spinner = Gtk.Spinner()
        self._search_spinner.set_no_show_all(True)
        head.pack_end(self._search_spinner, False, False, 0)
        self._search_configure = _configure_button()
        self._search_configure.set_no_show_all(True)
        self._search_configure.connect("clicked", lambda *_a: self._open_provider_settings())
        head.pack_end(self._search_configure, False, False, 0)
        inner.pack_start(head, False, False, 0)

        self._search_status = Gtk.Label(
            label="Type a title above to search TMDB and ani.pm.", xalign=0
        )
        self._search_status.set_line_wrap(True)
        self._search_status.get_style_context().add_class("movie-status")
        inner.pack_start(self._search_status, False, False, 6)

        self._search_empty = self._empty_state(
            "Search movies, TV and anime",
            "Results come straight from TMDB and ani.pm's catalogue - "
            "anime that only exists on ani.pm shows up here too. "
            "Nothing is suggested or invented.",
            icon="edit-find-symbolic",
        )
        inner.pack_start(self._search_empty, False, False, 10)

        self._search_flow = self._flowbox()
        flow_scroller = Gtk.ScrolledWindow()
        flow_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        flow_scroller.set_overlay_scrolling(False)
        flow_scroller.get_style_context().add_class("movie-scroll")
        flow_scroller.add(self._search_flow)
        inner.pack_start(flow_scroller, True, True, 0)
        return scroller

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        if self._suppress_search:
            return
        text = entry.get_text().strip()
        self._search_token += 1
        token = self._search_token
        if not text:
            self._spin(self._search_spinner, False)
            self._clear_flow(self._search_flow)
            self._search_status.set_text("Type a title above to search TMDB and ani.pm.")
            self._search_empty.show()
            return

        self._stack.set_visible_child_name("search")
        self._set_active_nav("search")
        self._update_back()
        self._search_empty.hide()
        self._search_configure.hide()
        self._search_status.set_text(f"Searching for “{text[:60]}”…")
        self._spin(self._search_spinner, True)

        def later() -> bool:
            if token != self._search_token:
                return False
            self._run_search(text, token)
            return False

        GLib.timeout_add(350, later)

    def _run_search(self, text: str, token: int) -> None:
        """Search TMDB and ani.pm in parallel, then show both in one grid.

        ani.pm is queried alongside TMDB so anime that exists only in the
        ani.pm catalogue still appears.  Either source may fail without
        hiding the other's results; stale tokens drop late arrivals.
        """
        results: dict[str, object] = {}
        lock = threading.Lock()
        remaining = 2

        def finish() -> None:
            nonlocal remaining
            with lock:
                remaining -= 1
                done = remaining == 0
            if done:
                GLib.idle_add(
                    self._apply_search, token, text,
                    results.get("tmdb") or [], results.get("ani") or [],
                    results.get("tmdb_error"), results.get("ani_error"),
                )

        def tmdb_work() -> None:
            try:
                results["tmdb"] = self._client.search(text)
            except TmdbError as error:
                results["tmdb_error"] = error
            finish()

        def ani_work() -> None:
            try:
                results["ani"] = self._ani_client.search(text, limit=24)
            except anipm.AniPmError as error:
                results["ani_error"] = error
            finish()

        threading.Thread(target=tmdb_work, daemon=True, name="lgl-tmdb-search").start()
        threading.Thread(target=ani_work, daemon=True, name="lgl-anipm-search").start()

    def _apply_search(self, token: int, text: str, items, ani_items,
                      tmdb_error=None, ani_error=None) -> bool:
        if token != self._search_token:
            return False
        self._spin(self._search_spinner, False)
        self._clear_flow(self._search_flow)
        # ani.pm results first: this integration exists because anime that
        # only lives there was invisible before.
        for item in (ani_items or []):
            self._search_flow.add(
                AniPmTile(item, self._images, self._open_anime_details)
            )
        for item in (items or []):
            self._search_flow.add(PosterTile(item, self._images, self._open_details))
        total = len(items or []) + len(ani_items or [])
        if not total:
            if tmdb_error is None and ani_error is None:
                self._search_status.set_text(f"No titles matched “{text[:60]}”.")
            else:
                self._search_status.set_text(
                    self._search_error_text(tmdb_error, ani_error)
                )
            self._search_empty.show()
            if getattr(tmdb_error, "kind", "") == "missing-key":
                self._search_configure.show()
            return False
        bits = []
        if ani_items:
            bits.append(f"{len(ani_items)} from ani.pm")
        if items:
            bits.append(f"{len(items)} from TMDB")
        note = f"{total} results · " + " · ".join(bits)
        degraded = self._search_error_text(tmdb_error, ani_error)
        if degraded:
            note += f" · {degraded}"
        self._search_status.set_text(note)
        self._search_flow.show_all()
        return False

    @staticmethod
    def _search_error_text(tmdb_error, ani_error) -> str:
        """A short, per-source explanation for whichever search failed."""
        parts = []
        if tmdb_error is not None:
            parts.append(f"TMDB: {tmdb_error}")
        if ani_error is not None:
            parts.append(f"ani.pm: {ani_error}")
        return " · ".join(parts)

    # -- anime --------------------------------------------------------------

    def _build_anime_page(self) -> Gtk.Widget:
        scroller = self._scroller()
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        inner.set_margin_start(32)
        inner.set_margin_end(32)
        inner.set_margin_top(24)
        inner.set_margin_bottom(24)
        scroller.add(inner)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        heading = self._page_title("Anime")
        header.pack_start(heading, False, False, 0)
        self._anime_spinner = Gtk.Spinner()
        header.pack_end(self._anime_spinner, False, False, 0)
        refresh = self._icon_button(
            "view-refresh-symbolic", "Reload popular anime", self._reload_anime
        )
        header.pack_end(refresh, False, False, 0)
        inner.pack_start(header, False, False, 0)

        sub = Gtk.Label(
            label="Popular anime from MyAnimeList. Metadata comes from MAL; "
            "playback uses the anime provider selected in Settings. The main "
            "search above also queries ani.pm's catalogue, where titles "
            "missing from MAL/TMDB live.",
            xalign=0,
        )
        sub.set_line_wrap(True)
        sub.get_style_context().add_class("movie-status")
        inner.pack_start(sub, False, False, 4)

        self._anime_status = Gtk.Label(label="", xalign=0)
        self._anime_status.set_line_wrap(True)
        self._anime_status.get_style_context().add_class("movie-status")
        inner.pack_start(self._anime_status, False, False, 4)

        self._anime_empty = self._empty_state(
            "Anime needs a MyAnimeList provider",
            "Configure it in Settings → Providers. Until then this page stays "
            "empty rather than showing made-up titles.",
            "Open provider settings",
            self._open_provider_settings,
            icon="video-x-generic-symbolic",
        )
        inner.pack_start(self._anime_empty, False, False, 12)

        self._anime_flow = self._flowbox()
        self._anime_flow.set_no_show_all(True)
        inner.pack_start(self._anime_flow, True, True, 0)
        return scroller

    def _reload_anime(self) -> None:
        self._anime_loaded = False
        self._load_anime()

    def _load_anime(self) -> None:
        self._anime_token += 1
        token = self._anime_token
        self._spin(self._anime_spinner, True)
        self._anime_status.set_text("Loading anime from MyAnimeList…")

        def work() -> None:
            try:
                items = self._mal_client.ranking("bypopularity", limit=24)
            except MalError as error:
                GLib.idle_add(self._apply_anime_error, token, error)
                return
            # For anime without a MAL poster, try to get one from TMDB.
            enriched = []
            for item in (items or []):
                if item.poster_url:
                    enriched.append(item)
                    continue
                # Search TMDB for a poster fallback.
                tmdb_poster = ""
                try:
                    results = self._client.search(item.title)
                    if results and results[0].poster_url:
                        tmdb_poster = results[0].poster_url
                except Exception:  # noqa: BLE001
                    pass
                if tmdb_poster:
                    enriched.append(mal.AnimeItem(
                        id=item.id, title=item.title, year=item.year,
                        synopsis=item.synopsis, poster_url=tmdb_poster,
                        rating=item.rating, episodes=item.episodes,
                        status=item.status, media_type=item.media_type,
                    ))
                else:
                    enriched.append(item)
            GLib.idle_add(self._apply_anime, token, enriched)

        threading.Thread(target=work, daemon=True, name="lgl-mal-ranking").start()

    def _apply_anime(self, token: int, items) -> bool:
        if token != self._anime_token:
            return False
        self._anime_loaded = True
        self._spin(self._anime_spinner, False)
        items = [item for item in (items or []) if getattr(item, "id", 0)]
        if items:
            self._anime_status.set_text(
                f"Popular now · {len(items)} titles from MyAnimeList"
            )
            self.show_anime_items(items)
            return False
        self._anime_status.set_text("MyAnimeList returned no titles right now.")
        self._clear_flow(self._anime_flow)
        self._anime_flow.hide()
        self._anime_empty.hide()
        return False

    def _apply_anime_error(self, token: int, error: MalError) -> bool:
        if token != self._anime_token:
            return False
        self._anime_loaded = False
        self._spin(self._anime_spinner, False)
        if getattr(error, "kind", "") == "missing-key":
            self._anime_status.set_text(
                "No MyAnimeList Client ID is configured."
            )
            self._anime_empty.show_all()
            return False
        self._anime_status.set_text(f"Could not load anime: {error}")
        self._clear_flow(self._anime_flow)
        self._anime_flow.hide()
        self._anime_empty.hide()
        return False

    def show_anime_items(self, items) -> None:
        """Render anime items from MyAnimeList (public hook)."""
        for child in self._anime_flow.get_children():
            self._anime_flow.remove(child)
        if not items:
            self._anime_flow.hide()
            self._anime_empty.show_all()
            return
        for item in items:
            self._anime_flow.add(
                PosterTile(item, self._images, self._open_anime_details)
            )
        self._anime_empty.hide()
        self._anime_flow.set_no_show_all(False)
        self._anime_flow.show_all()

    # -- anime details ------------------------------------------------------

    def _open_anime_details(self, item) -> None:
        current = self._stack.get_visible_child_name() or "home"
        if current not in ("details", "player"):
            self._back_target = current
            self._details_origin = current
        self._current_item = item
        self._current_details = None
        self._anime_detail_token += 1
        token = self._anime_detail_token

        for child in self._details_host.get_children():
            self._details_host.remove(child)
        self._details_host.add(self._build_anime_details_page(item))
        self._stack.set_visible_child_name("details")
        self._set_active_nav(self._nav_key_for_page(self._back_target))
        self._update_back()
        self._details_scroll.get_vadjustment().set_value(0)
        self._load_anime_details(item, token)

    def _build_anime_details_page(self, item) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        page.set_valign(Gtk.Align.START)
        page.get_style_context().add_class("movie-detail-body")

        overlay = Gtk.Overlay()
        overlay.set_size_request(-1, 420)
        backdrop = Backdrop(420)
        overlay.add(backdrop)
        scrim = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        scrim.set_hexpand(True)
        scrim.set_vexpand(True)
        scrim.get_style_context().add_class("movie-hero-scrim")
        overlay.add_overlay(scrim)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        content.set_valign(Gtk.Align.END)
        content.set_margin_start(46)
        content.set_margin_end(46)
        content.set_margin_bottom(30)

        badge_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        badge = Gtk.Label(label="ANIME")
        badge.set_halign(Gtk.Align.START)
        badge.get_style_context().add_class("movie-hero-badge")
        badge_row.pack_start(badge, False, False, 0)
        if bool(getattr(item, "adult", False)):
            adult_badge = Gtk.Label(label="18+")
            adult_badge.set_halign(Gtk.Align.START)
            adult_badge.get_style_context().add_class("movie-adult-badge")
            adult_badge.set_tooltip_text("Marked adult (18+) by ani.pm")
            badge_row.pack_start(adult_badge, False, False, 0)
        content.pack_start(badge_row, False, False, 0)

        title = Gtk.Label(label=getattr(item, "title", ""), xalign=0)
        title.set_line_wrap(True)
        title.set_lines(2)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_max_width_chars(40)
        title.get_style_context().add_class("movie-hero-title")
        content.pack_start(title, False, False, 0)

        meta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bits = [getattr(item, "year", "")]
        if getattr(item, "episodes", 0):
            bits.append(f"{item.episodes} episodes")
        self._fill_meta(meta, [bit for bit in bits if bit], getattr(item, "rating", None))
        content.pack_start(meta, False, False, 0)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        find = self._primary_button(
            "Watch",
            "media-playback-start-symbolic",
            lambda: self._watch_anime(item),
        )
        actions.pack_start(find, False, False, 0)

        provider = self._anime_provider()
        languages = streaming.languages_for(provider)
        language = streaming.clean_language(
            provider, self._settings.get("anime_language", "")
            or ("dub" if self._settings.get("anime_dub", False) else "sub")
        )

        episode_label = Gtk.Label(label="Episode")
        episode_label.get_style_context().add_class("movie-meta")
        actions.pack_start(episode_label, False, False, 0)
        episode_count = self._anime_episode_count(item)
        episode_spin = Gtk.SpinButton.new_with_range(
            1, max(1, episode_count or 1000), 1
        )
        episode_spin.set_value(1)
        episode_spin.set_tooltip_text(
            f"Episode to play (1–{max(1, episode_count or 1000)})"
        )
        actions.pack_start(episode_spin, False, False, 0)

        language_combo = Gtk.ComboBoxText()
        for key in languages:
            language_combo.append(key, streaming.LANGUAGE_LABELS.get(key, key))
        language_combo.set_active_id(language)
        language_combo.set_tooltip_text(
            f"Audio/subtitle track for {streaming.LABELS.get(provider, provider)}"
        )

        def on_language_changed(combo) -> None:
            choice = combo.get_active_id() or streaming.DEFAULT_LANGUAGE
            self._settings.set("anime_language", choice)
            self._settings.set("anime_dub", choice == "dub")

        language_combo.connect("changed", on_language_changed)
        actions.pack_start(language_combo, False, False, 0)

        back = self._dark_button("Back", self._navigate_back)
        actions.pack_start(back, False, False, 0)
        content.pack_start(actions, False, False, 0)
        self._anime_detail_state = {
            "episode": episode_spin,
            "language": language_combo,
        }
        overlay.add_overlay(content)
        page.pack_start(overlay, False, False, 0)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        body.set_margin_start(46)
        body.set_margin_end(46)
        body.set_margin_top(24)
        body.set_margin_bottom(40)

        note = Gtk.Label(xalign=0)
        if isinstance(item, anipm.AniTitle):
            # ani.pm catalogue titles play by their own AniList id; the other
            # anime providers join the fallback chain with the same id.
            note.set_label(
                "Anime metadata from the ani.pm catalogue. Watch plays the "
                "ani.pm player in the app using this title's AniList id "
                "(never the MAL id or page slug); the episode list below "
                "shows exactly which tracks ani.pm offers."
            )
        else:
            note.set_label(
                "This is anime metadata from MyAnimeList. Watch opens the "
                f"{streaming.LABELS.get(provider, provider)} player in the "
                "app: ani.pm plays straight from the MAL id (no key needed); "
                "the other providers need a TMDB match by title. "
                f"Tracks: {', '.join(streaming.LANGUAGE_LABELS.get(item, item) for item in languages)}."
            )
        note.set_line_wrap(True)
        note.set_max_width_chars(100)
        note.get_style_context().add_class("movie-status")
        body.pack_start(note, False, False, 0)

        overview = Gtk.Label(label=getattr(item, "synopsis", "") or "")
        overview.set_xalign(0)
        overview.set_line_wrap(True)
        overview.set_selectable(True)
        overview.set_max_width_chars(100)
        overview.get_style_context().add_class("movie-overview")
        body.pack_start(overview, False, False, 0)
        page.pack_start(body, False, False, 0)

        state = self._anime_detail_state
        state.update({"title": title, "meta": meta, "overview": overview})
        if isinstance(item, anipm.AniTitle):
            self._build_ani_episode_section(body, item)
        self._load_detail_image(getattr(item, "poster_url", ""), backdrop, self._anime_detail_token)
        state["backdrop"] = backdrop
        page.show_all()
        return page

    def _load_anime_details(self, item, token: int) -> None:
        if isinstance(item, anipm.AniTitle):
            # ani.pm catalogue title: series metadata, availability and the
            # episode list all come from ani.pm, keyed by the AniList id.
            def work_ani() -> None:
                series = None
                episodes: list = []
                try:
                    series = self._ani_client.series(item.anilist_id)
                    episodes = list(series.episodes)
                except anipm.AniPmError:
                    series = None
                if not episodes:
                    try:
                        episodes = self._ani_client.episodes(item.anilist_id)
                    except anipm.AniPmError:
                        episodes = []
                GLib.idle_add(self._apply_anime_details_ani, token, series, episodes)

            threading.Thread(
                target=work_ani, daemon=True, name="lgl-anipm-details"
            ).start()
            return

        def work() -> None:
            try:
                details = self._mal_client.details(item.id)
            except MalError:
                return
            GLib.idle_add(self._apply_anime_details, token, details)

        threading.Thread(target=work, daemon=True, name="lgl-mal-details").start()

    def _build_ani_episode_section(self, body: Gtk.Box, item) -> None:
        """The episode list for an ani.pm title, filled once data arrives."""
        self._current_ani_series = None
        self._current_ani_episodes = []
        head = Gtk.Label(label="Episodes", xalign=0)
        head.get_style_context().add_class("movie-section-title")
        body.pack_start(head, False, False, 0)
        note = view_prefs.spoiler_note(self._settings)
        if note:
            hint = Gtk.Label(label=note, xalign=0)
            hint.set_line_wrap(True)
            hint.get_style_context().add_class("movie-meta")
            body.pack_start(hint, False, False, 0)
        status = Gtk.Label(label="Loading episodes from ani.pm…", xalign=0)
        status.set_line_wrap(True)
        status.get_style_context().add_class("movie-status")
        body.pack_start(status, False, False, 0)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        body.pack_start(box, False, False, 0)
        state = self._anime_detail_state
        state["ani_episode_box"] = box
        state["ani_episode_status"] = status

    def _apply_anime_details_ani(self, token: int, series, episodes) -> bool:
        if token != self._anime_detail_token:
            return False
        state = getattr(self, "_anime_detail_state", {})
        if series is not None:
            self._current_ani_series = series
            title = state.get("title")
            if title is not None and series.title:
                title.set_text(series.title)
            meta = state.get("meta")
            if meta is not None:
                bits = [series.year, series.format]
                if episodes:
                    bits.append(f"{len(episodes)} episodes")
                if series.genres:
                    bits.append(" · ".join(series.genres))
                self._fill_meta(meta, [bit for bit in bits if bit], series.score)
            overview = state.get("overview")
            if overview is not None and series.synopsis:
                overview.set_text(series.synopsis)
            backdrop = state.get("backdrop")
            if backdrop is not None and series.poster_url:
                self._load_detail_image(series.poster_url, backdrop, token)
        status = state.get("ani_episode_status")
        if status is not None:
            if episodes:
                tracks = series.available.languages if series is not None else ()
                text = f"{len(episodes)} episodes from ani.pm"
                if tracks:
                    text += " · tracks: " + ", ".join(
                        anipm.LANGUAGE_LABELS.get(name, name) for name in tracks
                    )
                status.set_text(text)
            elif series is None:
                status.set_text(
                    "Could not load the episode list from ani.pm. "
                    "Watch still tries the documented ani.pm route."
                )
            else:
                status.set_text("ani.pm lists no episodes for this title yet.")
        available = series.available if series is not None else None
        self._render_ani_episodes(
            getattr(self, "_current_item", None), episodes, available
        )
        return False

    def _render_ani_episodes(self, item, episodes, available) -> None:
        """One row per episode; unavailable tracks are disabled, not hidden."""
        state = getattr(self, "_anime_detail_state", {})
        box = state.get("ani_episode_box")
        if box is None:
            return
        for child in box.get_children():
            box.remove(child)
        episodes = list(episodes or [])
        self._current_ani_episodes = episodes
        if not episodes or item is None:
            box.show_all()
            return
        allow = available or anipm.Availability()
        for ep in episodes:
            playable = ep.playable_languages(allow)
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.get_style_context().add_class("movie-episode-row")
            name_text = f"Episode {ep.number}"
            shown = view_prefs.episode_title(self._settings, ep.title)
            if shown:
                name_text += f" · {shown}"
            name = Gtk.Label(label=name_text, xalign=0)
            name.set_ellipsize(Pango.EllipsizeMode.END)
            name.set_max_width_chars(48)
            row.pack_start(name, True, True, 0)
            for language in anipm.LANGUAGES:
                label = anipm.LANGUAGE_LABELS.get(language, language)
                button = Gtk.Button(label=label)
                button.set_relief(Gtk.ReliefStyle.NONE)
                button.get_style_context().add_class("movie-btn-small")
                if language in playable:
                    button.set_tooltip_text(
                        f"Play episode {ep.number} ({label}) via ani.pm"
                    )
                    button.connect(
                        "clicked", self._on_ani_episode_play, item, ep.number,
                        language,
                    )
                else:
                    button.set_sensitive(False)
                    button.set_tooltip_text(
                        f"ani.pm does not offer {label} for this episode"
                    )
                row.pack_start(button, False, False, 0)
            box.pack_start(row, False, False, 0)
        box.show_all()

    def _on_ani_episode_play(self, _button, item, episode, language) -> None:
        self._play_anime_pm(item, episode, language)

    def _play_anime_pm(self, item, episode, language) -> None:
        """Play an ani.pm catalogue title by its **AniList** id.

        The API's own embed URL for this episode and track wins whenever the
        catalogue returned one (it was already validated against the pinned
        ani.pm origin at parse time); otherwise the documented
        ``/embed/ani/<anilist>/<episode>/<lang>`` route is constructed from the
        ids.  The AniList-routed providers stay in the chain as fallbacks.
        """
        try:
            number = max(1, int(episode))
        except (TypeError, ValueError):
            return
        language = anipm.language_key(language) or anipm.LANGUAGES[0]
        api_embed = ""
        for ep in list(getattr(self, "_current_ani_episodes", []) or []):
            if ep.number == number:
                try:
                    api_embed = ep.embed_url_for(language)
                except streaming.EmbedUrlError:
                    api_embed = ""
                break
        attempts: list[tuple[str, str]] = []
        try:
            url = anipm.playback_url(
                item.anilist_id, number, language, api_embed
            )
            attempts.append(
                (streaming.ANI_PROVIDER,
                 streaming.validate_embed_url(streaming.ANI_PROVIDER, url))
            )
        except (streaming.EmbedUrlError, ValueError):
            pass
        for provider in streaming.ANILIST_ANIME_PROVIDERS:
            try:
                url = streaming.anime_url(provider, item.anilist_id, number)
                attempts.append(
                    (provider, streaming.validate_embed_url(provider, url))
                )
            except (streaming.EmbedUrlError, ValueError):
                continue
        label = anipm.LANGUAGE_LABELS.get(language, language)
        self._start_anime_attempts(
            item, f"{item.title} · Episode {number} ({label})", number, attempts
        )

    def _apply_anime_details(self, token: int, details: AnimeDetails) -> bool:
        if token != self._anime_detail_token:
            return False
        state = getattr(self, "_anime_detail_state", {})
        title = state.get("title")
        if title is not None and details.title:
            title.set_text(details.title)
        meta = state.get("meta")
        if meta is not None:
            bits = [details.year]
            if details.episodes:
                bits.append(f"{details.episodes} episodes")
            if details.genres:
                bits.append(" · ".join(details.genres))
            self._fill_meta(meta, [bit for bit in bits if bit], details.rating)
        overview = state.get("overview")
        if overview is not None and details.synopsis:
            overview.set_text(details.synopsis)
        # Update backdrop from details poster (MAL details include main_picture)
        backdrop = state.get("backdrop")
        if backdrop is not None and details.poster_url:
            self._load_detail_image(details.poster_url, backdrop, token)
        return False

    @staticmethod
    def _anime_episode_count(item) -> int:
        try:
            count = int(getattr(item, "episodes", 0) or 0)
        except (TypeError, ValueError):
            return 0
        return max(0, count)

    def _anime_playback_choice(self) -> tuple[int, str]:
        """Episode and track chosen on the anime details page."""
        state = getattr(self, "_anime_detail_state", {})
        spin = state.get("episode")
        combo = state.get("language")
        try:
            episode = max(1, int(spin.get_value())) if spin is not None else 1
        except (TypeError, ValueError):
            episode = 1
        provider = self._anime_provider()
        chosen = combo.get_active_id() if combo is not None else None
        if not chosen:
            chosen = self._settings.get("anime_language", "")
        if not chosen:
            chosen = "dub" if self._settings.get("anime_dub", False) else "sub"
        return episode, streaming.clean_language(provider, chosen)

    def _watch_anime(self, item, episode=None, language=None) -> None:
        """Play an anime in the in-app player via the selected provider."""
        title = " ".join(str(getattr(item, "title", "") or "").split())
        if not title:
            return
        if isinstance(item, anipm.AniTitle):
            # An ani.pm catalogue title plays by its own AniList id through
            # the ani.pm route; its MAL id (if any) is never substituted here.
            chosen_episode, chosen_language = self._anime_playback_choice()
            number = chosen_episode if episode is None else max(1, int(episode))
            self._play_anime_pm(
                item, number, anipm.language_key(language or chosen_language)
                or anipm.LANGUAGES[0]
            )
            return
        provider = self._anime_provider()
        chosen_episode, chosen_language = self._anime_playback_choice()
        episode = chosen_episode if episode is None else max(1, int(episode))
        language = chosen_language if language is None else language
        try:
            language = streaming.ensure_language(provider, language)
        except streaming.EmbedUrlError as error:
            self._anime_status.set_text(str(error))
            return
        dub = language == "dub"

        if provider == streaming.ANI_PROVIDER or streaming.has_anime_route(provider):
            if not streaming.has_anime_route(provider):
                # ani.pm plays from the MAL id this page already holds.
                attempts = self._anime_attempts(item, episode, language, None)
                self._start_anime_attempts(item, title, episode, attempts)
                return
            # ToFlix and CineLite route anime by AniList id, which this page
            # does not hold.  Look it up by title rather than reusing the MAL
            # or TMDB id as if it were an AniList one.
            token = self._anime_detail_token + 1
            self._anime_detail_token = token
            self._anime_status.set_text(f"Looking up “{title[:60]}” on AniList…")

            def work() -> None:
                found = streaming.anilist_search(title)
                GLib.idle_add(
                    self._apply_anime_attempts,
                    token,
                    item,
                    title,
                    episode,
                    language,
                    found,
                )

            threading.Thread(target=work, daemon=True, name="lgl-anilist").start()
            return

        token = self._search_token + 1
        self._search_token = token

        def work() -> None:
            try:
                results = self._client.search(title)
            except TmdbError as error:
                GLib.idle_add(self._anime_watch_error, token, error)
                return
            GLib.idle_add(
                self._anime_watch_apply_tmdb, token, title, results, provider, dub
            )

        threading.Thread(target=work, daemon=True, name="lgl-anime-watch").start()

    def _anime_attempts(
        self, item, episode, language, anilist_id
    ) -> list[tuple[str, str]]:
        """Anime providers that can play this title, the preferred one first.

        ani.pm plays from the MAL id this page already holds; ToFlix and
        CineLite only join once an AniList id was resolved.  The two
        identifiers are never substituted for one another.
        """
        attempts: list[tuple[str, str]] = []
        for provider in streaming.fallback_anime_providers(self._anime_provider()):
            try:
                if provider == streaming.ANI_PROVIDER:
                    url = streaming.ani_mal_url(
                        getattr(item, "id", 0), episode, language=language
                    )
                elif streaming.has_anime_route(provider):
                    if anilist_id is None:
                        continue
                    url = streaming.anime_url(provider, anilist_id, episode)
                else:
                    continue
                attempts.append(
                    (provider, streaming.validate_embed_url(provider, url))
                )
            except (streaming.EmbedUrlError, ValueError):
                continue
        return attempts

    def _apply_anime_attempts(
        self, token, item, title, episode, language, anilist_id
    ) -> bool:
        if token != self._anime_detail_token:
            return False
        if anilist_id is None:
            label = streaming.LABELS.get(self._anime_provider(), "That provider")
            self._anime_status.set_text(
                f"No AniList match for “{title[:60]}”, which {label} needs. "
                "Trying another provider."
            )
        self._start_anime_attempts(
            item, title, episode,
            self._anime_attempts(item, episode, language, anilist_id),
        )
        return False

    def _start_anime_attempts(self, item, title, episode, attempts) -> None:
        if not attempts:
            self._anime_status.set_text(
                f"No provider could play “{title[:60]}” (episode {episode})."
            )
            return
        label = streaming.LABELS.get(attempts[0][0], attempts[0][0])
        self._anime_status.set_text(
            f"Playing “{title[:60]}” via {label} (episode {episode})."
        )
        self._record_playback(
            getattr(item, "key", ""),
            getattr(item, "title", title),
            getattr(item, "poster_url", ""),
            getattr(item, "year", ""),
        )
        self._play_attempts(attempts, getattr(item, "title", title))

    def _anime_watch_apply_tmdb(
        self, token: int, title: str, results, provider=None, dub=None
    ) -> bool:
        if token != self._search_token:
            return False
        provider = provider or self._anime_provider()
        if dub is None:
            dub = bool(self._settings.get("anime_dub", False))
        if not results:
            self._anime_status.set_text(
                f"No TMDB match for “{title[:60]}”. Try searching manually."
            )
            self._search_tmdb_for(title)
            return False
        match = results[0]
        if match.kind == "movie":
            self._record_playback(match.key, match.title, match.poster_url, match.year)
            attempts = self._provider_attempts(
                lambda key: streaming.movie_url(
                    key, match.tmdb_id, title=match.title, dub=dub
                ),
                preferred=provider,
            )
            self._play_attempts(attempts, match.title)
        else:
            self._open_details(match)
        return False

    def _anime_watch_error(self, token: int, error: TmdbError) -> bool:
        if token != self._search_token:
            return False
        if getattr(error, "kind", "") == "missing-key":
            self._anime_status.set_text(
                "Add a TMDB API key to watch anime (Settings → Providers)."
            )
            self._open_provider_settings()
        else:
            self._anime_status.set_text(f"Could not search TMDB: {error}")
        return False

    def _search_tmdb_for(self, title: str) -> None:
        text = " ".join(str(title or "").split())
        if not text:
            return
        self._stack.set_visible_child_name("search")
        self._set_active_nav("search")
        self._update_back()
        self._suppress_search = True
        self._search.set_text(text)
        self._suppress_search = False
        self._on_search_changed(self._search)


    # -- My List ------------------------------------------------------------

    def _watchlist(self) -> list[str]:
        value = self._settings.get(WATCHLIST_KEY, [])
        return [str(entry) for entry in value] if isinstance(value, list) else []

    def _watchlist_entries(self) -> list[tuple[str, int]]:
        entries: list[tuple[str, int]] = []
        for raw in self._watchlist():
            kind, _, number = raw.partition(":")
            kind = kind.strip()
            if kind not in ("movie", "tv"):
                continue
            if not number.isdigit() or int(number) <= 0:
                continue
            entries.append((kind, int(number)))
        return entries

    def _build_mylist_page(self) -> Gtk.Widget:
        scroller = self._scroller()
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        inner.set_margin_start(32)
        inner.set_margin_end(32)
        inner.set_margin_top(24)
        inner.set_margin_bottom(20)
        scroller.add(inner)

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        head.pack_start(self._page_title("My List"), False, False, 0)
        self._mylist_count = Gtk.Label(label="")
        self._mylist_count.set_no_show_all(True)
        self._mylist_count.get_style_context().add_class("movie-section-count")
        head.pack_start(self._mylist_count, False, False, 0)
        spacer = Gtk.Box()
        head.pack_start(spacer, True, True, 0)
        self._mylist_spinner = Gtk.Spinner()
        self._mylist_spinner.set_no_show_all(True)
        head.pack_end(self._mylist_spinner, False, False, 0)
        inner.pack_start(head, False, False, 0)

        self._mylist_status = Gtk.Label(label="", xalign=0)
        self._mylist_status.set_line_wrap(True)
        self._mylist_status.get_style_context().add_class("movie-status")
        inner.pack_start(self._mylist_status, False, False, 6)

        self._mylist_empty_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        inner.pack_start(self._mylist_empty_box, False, False, 10)

        self._mylist_flow = self._flowbox()
        self._mylist_flow.set_no_show_all(True)
        flow_scroller = Gtk.ScrolledWindow()
        flow_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        flow_scroller.set_overlay_scrolling(False)
        flow_scroller.get_style_context().add_class("movie-scroll")
        flow_scroller.add(self._mylist_flow)
        inner.pack_start(flow_scroller, True, True, 0)
        return scroller

    def _set_mylist_empty(self, title, body, action_label=None, action=None) -> None:
        for child in self._mylist_empty_box.get_children():
            self._mylist_empty_box.remove(child)
        self._mylist_empty_box.add(
            self._empty_state(
                title, body, action_label, action, icon="starred-symbolic"
            )
        )
        self._mylist_empty_box.show_all()
        self._mylist_flow.hide()

    def _render_mylist_empty(self) -> None:
        self._mylist_count.set_text("")
        self._mylist_count.hide()
        self._set_mylist_empty(
            "My List is empty",
            "Open a title's details and press My List to keep it here.",
        )

    def _load_mylist(self) -> None:
        self._mylist_token += 1
        token = self._mylist_token
        entries = self._watchlist_entries()
        if not entries:
            self._spin(self._mylist_spinner, False)
            self._mylist_status.set_text("")
            self._render_mylist_empty()
            return
        self._mylist_empty_box.hide()
        self._mylist_flow.hide()
        self._mylist_status.set_text("Loading your list…")
        self._mylist_count.set_text("")
        self._spin(self._mylist_spinner, True)

        def work() -> None:
            items: list[MediaItem] = []
            errors: list[TmdbError] = []
            for kind, tmdb_id in entries[:40]:
                try:
                    details = self._client.details(kind, tmdb_id)
                except TmdbError as error:
                    errors.append(error)
                    continue
                items.append(
                    MediaItem(
                        kind=details.kind,
                        tmdb_id=details.tmdb_id,
                        title=details.title,
                        year=details.year,
                        poster_url=details.poster_url,
                        backdrop_url=details.backdrop_url,
                        overview=details.overview,
                        rating=details.rating,
                    )
                )
            GLib.idle_add(self._apply_mylist, token, items, errors)

        threading.Thread(target=work, daemon=True, name="lgl-tmdb-mylist").start()

    def _apply_mylist(self, token: int, items, errors) -> bool:
        if token != self._mylist_token:
            return False
        self._spin(self._mylist_spinner, False)
        if items:
            self._mylist_empty_box.hide()
            self._mylist_flow.set_no_show_all(False)
            self._fill_flow(self._mylist_flow, items)
            self._mylist_flow.show()
            self._mylist_count.set_text(str(len(items)))
            self._mylist_count.show()
            self._mylist_status.set_text(
                "Saved titles" if not errors else "Some entries could not be loaded."
            )
            return False
        missing = any(getattr(error, "kind", "") == "missing-key" for error in errors)
        if missing:
            self._set_mylist_empty(
                "My List cannot load",
                "A TMDB API key is needed to fetch the titles in your list.",
                "Configure providers",
                self._open_provider_settings,
            )
        elif errors:
            self._set_mylist_empty(
                "My List could not load",
                str(errors[0]),
                "Try again",
                self._load_mylist,
            )
        else:
            self._render_mylist_empty()
        self._mylist_status.set_text("")
        return False

    # -- history ------------------------------------------------------------

    def _build_history_page(self) -> Gtk.Widget:
        scroller = self._scroller()
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        inner.set_margin_start(32)
        inner.set_margin_end(32)
        inner.set_margin_top(24)
        inner.set_margin_bottom(32)
        scroller.add(inner)

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        head.pack_start(self._page_title("History"), False, False, 0)
        self._history_count = Gtk.Label(label="")
        self._history_count.set_no_show_all(True)
        self._history_count.get_style_context().add_class("movie-section-count")
        head.pack_start(self._history_count, False, False, 0)
        spacer = Gtk.Box()
        head.pack_start(spacer, True, True, 0)
        self._history_clear = Gtk.Button(label="Clear history")
        self._history_clear.set_relief(Gtk.ReliefStyle.NONE)
        self._history_clear.set_no_show_all(True)
        self._history_clear.get_style_context().add_class("movie-btn-dark")
        self._history_clear.get_style_context().add_class("movie-btn-small")
        self._history_clear.connect("clicked", lambda *_a: self._confirm_clear_history())
        head.pack_end(self._history_clear, False, False, 0)
        inner.pack_start(head, False, False, 0)

        note = Gtk.Label(
            label="Titles appear here only after you press Play.",
            xalign=0,
        )
        note.get_style_context().add_class("movie-status")
        inner.pack_start(note, False, False, 0)

        self._history_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        inner.pack_start(self._history_list, False, False, 6)

        self._history_empty = self._empty_state(
            "Nothing watched yet",
            "Continue Watching and History fill up when you play a title.",
            icon="document-open-recent-symbolic",
        )
        self._history_empty.set_no_show_all(True)
        inner.pack_start(self._history_empty, False, False, 8)
        return scroller

    def _render_history(self) -> None:
        for child in self._history_list.get_children():
            self._history_list.remove(child)
        entries = media_history.load(self._settings)
        self._history_count.set_text(str(len(entries)))
        if not entries:
            self._history_empty.set_no_show_all(False)
            self._history_empty.show_all()
            self._history_clear.hide()
            self._history_count.hide()
            return
        self._history_empty.hide()
        self._history_clear.set_no_show_all(False)
        self._history_clear.show()
        self._history_count.show()
        for entry in entries:
            item = MediaItem(
                kind=entry["kind"],
                tmdb_id=entry["tmdb_id"],
                title=entry["title"],
                year=entry["year"],
                poster_url=entry["poster_url"],
            )
            row = Gtk.Button()
            row.set_relief(Gtk.ReliefStyle.NONE)
            row.get_style_context().add_class("movie-history-row")
            row.set_tooltip_text(item.title)
            row.connect("clicked", lambda _b, chosen=item: self._open_details(chosen))

            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            poster = Poster(48, 72, radius=6)
            line.pack_start(poster, False, False, 0)
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            text.set_valign(Gtk.Align.CENTER)
            title = Gtk.Label(label=item.title, xalign=0)
            title.set_ellipsize(Pango.EllipsizeMode.END)
            title.get_style_context().add_class("movie-history-title")
            text.pack_start(title, False, False, 0)
            bits = [item.year, _kind_noun(item.kind)]
            when = ""
            if entry["watched_at"]:
                when = time.strftime("%d %b %Y, %H:%M", time.localtime(entry["watched_at"]))
            meta = Gtk.Label(
                label=" · ".join(bit for bit in bits if bit)
                + (f" · watched {when}" if when else ""),
                xalign=0,
            )
            meta.get_style_context().add_class("movie-card-meta")
            text.pack_start(meta, False, False, 0)
            line.pack_start(text, True, True, 0)
            chevron = Gtk.Image.new_from_icon_name("go-next-symbolic", Gtk.IconSize.BUTTON)
            chevron.get_style_context().add_class("movie-card-meta")
            line.pack_end(chevron, False, False, 4)
            row.add(line)
            self._history_list.pack_start(row, False, False, 0)
            self._images.load(item.poster_url, (48, 72), poster.set_pixbuf)
        self._history_list.show_all()

    def _confirm_clear_history(self) -> None:
        dialog = Gtk.MessageDialog(
            transient_for=(self.get_toplevel() if isinstance(self.get_toplevel(), Gtk.Window) else None),
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.NONE,
            text="Clear watching history?",
        )
        dialog.format_secondary_text(
            "This removes every entry from History and Continue Watching. "
            "My List is not affected."
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        clear = dialog.add_button("Clear history", Gtk.ResponseType.OK)
        clear.get_style_context().add_class("destructive-action")
        response = dialog.run()
        dialog.destroy()
        if response != Gtk.ResponseType.OK:
            return
        media_history.clear(self._settings)
        self._render_history()
        self._render_continue()

    # -- details ------------------------------------------------------------

    def _open_details(self, item: MediaItem) -> None:
        current = self._stack.get_visible_child_name() or "home"
        if current not in ("details", "player"):
            self._back_target = current
            self._details_origin = current
        self._current_item = item
        self._current_id = item.tmdb_id
        kind = str(getattr(item, "kind", "") or "")
        if kind in recently_viewed.KINDS:
            recently_viewed.record(
                recently_viewed.default_path(),
                kind=kind,
                key=f"{kind}:{item.tmdb_id}",
                title=str(getattr(item, "title", "") or ""),
                subtitle=str(getattr(item, "year", "") or ""),
            )
        self._current_details = None
        self._detail_token += 1
        self._season_token += 1
        token = self._detail_token

        for child in self._details_host.get_children():
            self._details_host.remove(child)
        self._details_host.add(self._build_details_page(item))
        self._stack.set_visible_child_name("details")
        self._set_active_nav(self._nav_key_for_page(self._back_target))
        self._update_back()
        self._details_scroll.get_vadjustment().set_value(0)
        self._detail_trailer = None
        self._load_details(item, token)
        self._load_trailer(item, token)

    def _build_details_page(self, item: MediaItem) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        page.set_valign(Gtk.Align.START)
        page.get_style_context().add_class("movie-detail-body")
        self._artwork = ArtworkBackdrop(page, self._settings)

        overlay = Gtk.Overlay()
        overlay.set_size_request(-1, 420)
        backdrop = Backdrop(420)
        overlay.add(backdrop)
        scrim = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        scrim.set_hexpand(True)
        scrim.set_vexpand(True)
        scrim.get_style_context().add_class("movie-hero-scrim")
        overlay.add_overlay(scrim)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        content.set_valign(Gtk.Align.END)
        content.set_margin_start(46)
        content.set_margin_end(46)
        content.set_margin_bottom(30)

        badge = Gtk.Label(label="TV" if item.kind == "tv" else "MOVIE")
        badge.set_halign(Gtk.Align.START)
        badge.get_style_context().add_class("movie-hero-badge")
        content.pack_start(badge, False, False, 0)

        title = Gtk.Label(label=item.title, xalign=0)
        title.set_line_wrap(True)
        title.set_lines(2)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_max_width_chars(40)
        title.get_style_context().add_class("movie-hero-title")
        content.pack_start(title, False, False, 0)

        meta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._fill_meta(
            meta,
            [bit for bit in (item.year, _kind_noun(item.kind)) if bit],
            item.rating,
        )
        content.pack_start(meta, False, False, 0)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        if item.kind == "tv":
            play = self._primary_button(
                "Choose an episode",
                "media-playback-start-symbolic",
                self._scroll_to_seasons,
            )
        else:
            play = self._primary_button(
                "Watch Now",
                "media-playback-start-symbolic",
                lambda: self._play(item),
            )
        actions.pack_start(play, False, False, 0)

        # Hidden until TMDB says this title has a trailer we can embed.
        trailer_button = Gtk.Button(label="Trailer")
        trailer_button.set_relief(Gtk.ReliefStyle.NONE)
        trailer_button.get_style_context().add_class("movie-btn-dark")
        trailer_button.set_no_show_all(True)
        trailer_button.set_tooltip_text("Play the trailer")
        trailer_button.connect("clicked", lambda *_a: self._play_trailer())
        actions.pack_start(trailer_button, False, False, 0)

        watch = Gtk.ToggleButton(label="My List")
        watch.set_relief(Gtk.ReliefStyle.NONE)
        watch.get_style_context().add_class("movie-btn-dark")
        watch.set_active(self._in_watchlist(item.key))
        watch.set_tooltip_text("Add to or remove from My List")
        watch.connect("clicked", lambda button: self._toggle_watchlist(item, button))
        actions.pack_start(watch, False, False, 0)

        self._detail_feedback = Gtk.Label(label="")
        self._detail_feedback.get_style_context().add_class("movie-status")
        self._detail_feedback.set_no_show_all(True)
        actions.pack_start(self._detail_feedback, False, False, 0)

        share = self._dark_button(
            "Share", lambda: self._copy_link(item, self._detail_feedback)
        )
        actions.pack_start(share, False, False, 0)
        content.pack_start(actions, False, False, 0)

        # Only a lookup that failed says so here; a title that simply has
        # no trailer keeps the page quiet.
        self._trailer_note = Gtk.Label(label="")
        self._trailer_note.set_xalign(0)
        self._trailer_note.get_style_context().add_class("movie-meta")
        self._trailer_note.set_no_show_all(True)
        content.pack_start(self._trailer_note, False, False, 0)
        overlay.add_overlay(content)

        page.pack_start(overlay, False, False, 0)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        body.set_margin_start(46)
        body.set_margin_end(46)
        body.set_margin_top(24)
        body.set_margin_bottom(40)
        overview = Gtk.Label(label=item.overview or "")
        overview.set_xalign(0)
        overview.set_line_wrap(True)
        overview.set_selectable(True)
        overview.set_max_width_chars(100)
        overview.get_style_context().add_class("movie-overview")
        body.pack_start(overview, False, False, 0)

        seasons_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        body.pack_start(seasons_box, False, False, 0)
        page.pack_start(body, False, False, 0)

        self._detail_state = {
            "backdrop": backdrop,
            "title": title,
            "meta": meta,
            "overview": overview,
            "seasons": seasons_box,
            "play": play,
            "trailer_button": trailer_button,
        }
        url = item.backdrop_url or item.poster_url
        self._load_detail_image(url, backdrop)
        self._detail_feedback.hide()
        page.show_all()
        self._detail_feedback.hide()
        self._trailer_note.hide()
        return page

    def _load_detail_image(self, url: str, backdrop: Backdrop, token: int | None = None) -> None:
        if token is None:
            token = self._detail_token

        def apply(pixbuf) -> bool:
            if token != self._detail_token and token != self._anime_detail_token:
                return False
            try:
                backdrop.set_pixbuf(pixbuf)
            except Exception:  # noqa: BLE001 - the page may have been replaced
                pass
            if pixbuf is not None:
                self._artwork.show_pixbuf(url, pixbuf)
            return False

        self._images.load(url, DETAIL_BACKDROP_SIZE, apply)

    def _load_details(self, item: MediaItem, token: int) -> None:
        def work() -> None:
            try:
                details = self._client.details(item.kind, item.tmdb_id)
            except TmdbError as error:
                GLib.idle_add(self._details_error, token, error)
                return
            GLib.idle_add(self._apply_details, token, details)

        threading.Thread(target=work, daemon=True, name="lgl-tmdb-details").start()

    def _load_trailer(self, item: MediaItem, token: int) -> None:
        """Ask TMDB for this title's trailer without blocking the page."""

        def work() -> None:
            result = trailer_data.load_trailer(
                item.kind, item.tmdb_id, settings=self._settings
            )
            GLib.idle_add(self._apply_trailer, token, result)

        threading.Thread(target=work, daemon=True, name="lgl-trailer").start()

    def _apply_trailer(self, token: int, result: trailer_data.TrailerResult) -> bool:
        if token != self._detail_token:
            return False
        self._detail_trailer = result.trailer
        note = getattr(self, "_trailer_note", None)
        if note is not None:
            note.set_text("Trailer not available" if result.error else "")
            note.set_visible(bool(result.error))
        button = (self._detail_state or {}).get("trailer_button")
        if button is None:
            return False
        button.set_visible(result.has_trailer)
        if result.has_trailer:
            button.set_tooltip_text(str((result.trailer or {}).get("name") or "Play the trailer"))
            if trailers.autoplay_allowed(self._settings):
                self._play_trailer(autoplay=True, muted=True)
            else:
                button.grab_focus()
        return False

    def _play_trailer(self, *, autoplay: bool = False, muted: bool = False) -> None:
        """Open the trailer on the player page; Back returns to the details."""
        trailer = getattr(self, "_detail_trailer", None)
        if not trailer:
            return
        url = trailer_data.embed_url(trailer.get("key"), autoplay=autoplay, muted=muted)
        if not url:
            return
        current = self._stack.get_visible_child_name()
        if current not in (None, "player"):
            self._back_target = current
        for child in self._player.get_children():
            self._player.remove(child)
        details = self._current_details or self._current_item
        title = str(getattr(details, "title", "") or "")
        heading = Gtk.Label(label=f"Trailer · {title}" if title else "Trailer", xalign=0)
        heading.set_ellipsize(Pango.EllipsizeMode.END)
        heading.get_style_context().add_class("movie-page-title")
        self._player.pack_start(heading, False, False, 0)

        frame = Gtk.AspectFrame(xalign=0.5, yalign=0.5, ratio=16 / 9, obey_child=False)
        frame.set_shadow_type(Gtk.ShadowType.NONE)
        frame.set_size_request(560, 315)
        frame.get_style_context().add_class("movie-player-frame")
        webkit = _webkit()
        if webkit is not None:
            view = webkit.WebView()
            self._webview = view
            self._apply_trailer_settings(view)
            self._connect_player_fullscreen(view)
            view.load_uri(url)
            frame.add(view)
            note = Gtk.Label(
                label=(
                    f"{trailer.get('type') or 'Trailer'} provided by YouTube; "
                    "playback needs a network connection."
                ),
                xalign=0,
            )
            note.set_line_wrap(True)
            note.get_style_context().add_class("movie-meta")
            self._player.pack_start(note, False, False, 0)
        else:
            frame.add(self._trailer_error_box(url))
        self._player.pack_start(frame, True, True, 0)
        self._player.show_all()
        self._stack.set_visible_child_name("player")
        self._update_back()

    def _apply_trailer_settings(self, view) -> None:
        """Media permissions a YouTube embed needs; nothing broader."""
        settings = view.get_settings()
        for name, value in (
            ("enable_media", True),
            ("enable_media_stream", True),
            ("enable_mediasource", True),
            ("enable_webaudio", True),
            ("enable_webgl", True),
        ):
            try:
                settings.set_property(name, value)
            except TypeError:
                continue

    def _trailer_error_box(self, url: str) -> Gtk.Widget:
        """No WebKitGTK: offer the browser, but never open it unasked."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_halign(Gtk.Align.CENTER)
        box.set_valign(Gtk.Align.CENTER)
        box.set_margin_start(24)
        box.set_margin_end(24)
        message = Gtk.Label(
            label="This build has no WebKitGTK, so the trailer cannot be embedded."
        )
        message.set_line_wrap(True)
        message.set_max_width_chars(70)
        message.set_justify(Gtk.Justification.CENTER)
        box.pack_start(message, False, False, 0)
        open_button = Gtk.Button(label="Open trailer in your browser")
        open_button.get_style_context().add_class("movie-btn-primary")
        open_button.connect("clicked", lambda *_a: self._open_external(url))
        box.pack_start(open_button, False, False, 0)
        box.show_all()
        return box

    def _details_error(self, token: int, error: TmdbError) -> bool:
        if token != self._detail_token:
            return False
        state = self._detail_state
        meta = state.get("meta")
        if meta is not None:
            self._fill_meta(meta, [str(error)])
        return False

    def _apply_details(self, token: int, details: MediaDetails) -> bool:
        if token != self._detail_token:
            return False
        state = self._detail_state
        self._current_details = details
        title = state.get("title")
        if title is not None:
            title.set_text(details.title)
        meta = state.get("meta")
        if meta is not None:
            bits = [details.year]
            if details.genres:
                bits.append(" · ".join(details.genres))
            bits.append(details.runtime_label)
            if details.kind == "tv" and details.seasons:
                bits.append(f"{len(details.seasons)} season" + ("" if len(details.seasons) == 1 else "s"))
            self._fill_meta(meta, [bit for bit in bits if bit], details.rating)
        overview = state.get("overview")
        if overview is not None and details.overview:
            overview.set_text(details.overview)
        backdrop = state.get("backdrop")
        if backdrop is not None and details.backdrop_url:
            self._load_detail_image(details.backdrop_url, backdrop)
        if details.kind == "tv" and details.seasons:
            self._render_seasons(details)
        return False

    def _render_seasons(self, details: MediaDetails) -> None:
        box = self._detail_state.get("seasons")
        if box is None:
            return
        for child in box.get_children():
            box.remove(child)
        heading = Gtk.Label(label="Episodes", xalign=0)
        heading.get_style_context().add_class("movie-section-title")
        box.pack_start(heading, False, False, 0)
        # The note sits with the list it explains: hidden titles must not read
        # as missing data, and the way out is one line away.
        note = view_prefs.spoiler_note(self._settings)
        if note:
            hint = Gtk.Label(label=note, xalign=0)
            hint.set_line_wrap(True)
            hint.get_style_context().add_class("movie-meta")
            box.pack_start(hint, False, False, 0)

        tabs_scroller = Gtk.ScrolledWindow()
        tabs_scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        tabs_scroller.set_overlay_scrolling(False)
        tabs_scroller.set_size_request(-1, 44)
        tabs_scroller.get_style_context().add_class("movie-scroll")
        tabs = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        tabs_scroller.add(tabs)
        buttons: list[Gtk.ToggleButton] = []

        episodes = Gtk.ListBox()
        episodes.set_selection_mode(Gtk.SelectionMode.NONE)
        episodes.get_style_context().add_class("movie-episode-list")

        def select(number: int, chosen: Gtk.ToggleButton) -> None:
            for button in buttons:
                button.set_active(button is chosen)
            self._load_season(details.tmdb_id, number, episodes)

        for season in details.seasons:
            label = season.name or f"Season {season.number}"
            button = Gtk.ToggleButton(label=label)
            button.set_relief(Gtk.ReliefStyle.NONE)
            button.get_style_context().add_class("movie-btn-dark")
            button.get_style_context().add_class("movie-btn-small")
            button.set_tooltip_text(f"{season.episode_count} episodes")
            button.connect(
                "clicked",
                lambda current, number=season.number: select(number, current),
            )
            tabs.pack_start(button, False, False, 0)
            buttons.append(button)

        box.pack_start(tabs_scroller, False, False, 0)
        box.pack_start(episodes, False, False, 0)
        box.show_all()
        if buttons:
            buttons[0].set_active(True)
            select(details.seasons[0].number, buttons[0])

    def _load_season(self, tmdb_id: int, number: int, box: Gtk.ListBox) -> None:
        self._season_token += 1
        token = self._season_token
        for child in box.get_children():
            box.remove(child)
        placeholder = Gtk.Label(label="Loading episodes…", xalign=0)
        placeholder.get_style_context().add_class("movie-meta")
        row = Gtk.ListBoxRow()
        row.add(placeholder)
        box.add(row)
        box.show_all()

        def work() -> None:
            try:
                season = self._client.season(tmdb_id, number)
            except TmdbError as error:
                GLib.idle_add(self._season_error, token, box, error)
                return
            GLib.idle_add(self._apply_season, token, box, season)

        threading.Thread(target=work, daemon=True, name="lgl-tmdb-season").start()

    def _season_error(self, token: int, box: Gtk.ListBox, error: TmdbError) -> bool:
        if token != self._season_token:
            return False
        for child in box.get_children():
            box.remove(child)
        row = Gtk.ListBoxRow()
        label = Gtk.Label(label=str(error), xalign=0)
        label.set_line_wrap(True)
        label.get_style_context().add_class("movie-meta")
        row.add(label)
        box.add(row)
        box.show_all()
        return False

    def _apply_season(self, token: int, box: Gtk.ListBox, season) -> bool:
        if token != self._season_token:
            return False
        for child in box.get_children():
            box.remove(child)
        if not season.episodes:
            row = Gtk.ListBoxRow()
            row.add(Gtk.Label(label="This season lists no episodes.", xalign=0))
            box.add(row)
            box.show_all()
            return False
        for episode in season.episodes:
            row = Gtk.ListBoxRow()
            row.get_style_context().add_class("movie-episode-row")
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            line.set_margin_top(5)
            line.set_margin_bottom(5)
            line.set_margin_start(6)
            line.set_margin_end(6)

            number = Gtk.Label(label=f"{episode.number:02d}", xalign=0)
            number.get_style_context().add_class("movie-meta")
            number.set_width_chars(3)
            line.pack_start(number, False, False, 0)

            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            label = Gtk.Label(
                label=view_prefs.episode_title(self._settings, episode.title),
                xalign=0,
            )
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.get_style_context().add_class("movie-episode")
            text.pack_start(label, False, False, 0)
            if episode.air_date:
                air = Gtk.Label(label=episode.air_date, xalign=0)
                air.get_style_context().add_class("movie-card-meta")
                text.pack_start(air, False, False, 0)
            line.pack_start(text, True, True, 0)

            play = Gtk.Button(label="Play")
            play.set_relief(Gtk.ReliefStyle.NONE)
            play.get_style_context().add_class("movie-btn-dark")
            play.get_style_context().add_class("movie-btn-small")
            play.connect(
                "clicked",
                lambda _b, s=season.number, e=episode: self._play_episode(s, e),
            )
            line.pack_end(play, False, False, 0)
            row.add(line)
            box.add(row)
        box.show_all()
        return False

    def _scroll_to_seasons(self) -> None:
        def scroll() -> bool:
            adjustment = self._details_scroll.get_vadjustment()
            adjustment.set_value(max(adjustment.get_lower(), adjustment.get_upper() - adjustment.get_page_size()))
            return False

        GLib.idle_add(scroll)

    # -- playback -----------------------------------------------------------

    def _record_playback(self, key: str, title: str, poster_url: str, year: str) -> None:
        try:
            media_history.record(
                self._settings,
                key=key,
                title=title,
                poster_url=poster_url,
                year=year,
            )
        except Exception:  # noqa: BLE001 - history must never block playback
            return
        self._render_continue()

    def _play(self, item: MediaItem) -> None:
        if item.kind == "tv":
            self._open_details(item)
            return
        self._record_playback(item.key, item.title, item.poster_url, item.year)
        dub = bool(self._settings.get("anime_dub", False))
        attempts = self._provider_attempts(
            lambda provider: streaming.movie_url(
                provider, item.tmdb_id, title=item.title, dub=dub
            )
        )
        self._play_attempts(attempts, item.title)

    def _play_episode(self, season: int, episode) -> None:
        show = self._current_details or self._current_item
        if show is None:
            return
        self._record_playback(show.key, show.title, show.poster_url, show.year)
        title = f"{episode.title} · S{season:02d}E{episode.number:02d}"
        dub = bool(self._settings.get("anime_dub", False))
        attempts = self._provider_attempts(
            lambda provider: streaming.episode_url(
                provider, show.tmdb_id, season, episode.number,
                title=title, dub=dub,
            )
        )
        self._play_attempts(attempts, title)

    def _provider_attempts(self, build, preferred: str | None = None) -> list[tuple[str, str]]:
        """Every provider that can build a URL, the preferred one first.

        This is the fallback chain: when the first provider is blocked or has
        no copy of the title, the player walks to the next entry.
        """
        attempts: list[tuple[str, str]] = []
        preferred = preferred or self._streaming_provider()
        for provider in streaming.fallback_providers(preferred):
            try:
                url = build(provider)
                url = streaming.validate_embed_url(provider, url)
            except (streaming.EmbedUrlError, ValueError):
                continue
            attempts.append((provider, url))
        return attempts

    def _play_attempts(self, attempts: list[tuple[str, str]], title: str) -> None:
        """Play the first URL and keep the rest as fallbacks."""
        if not attempts:
            self._open_player_message(
                title,
                "No streaming provider could build a playback URL for this "
                "title. Check Settings → Media.",
            )
            return
        provider, url = attempts[0]
        self._open_player(url, title, provider=provider, attempts=attempts)
        self._verify_current_attempt()

    def _verify_current_attempt(self) -> None:
        """Skip the current provider if it answers an HTTP error.

        WebKit renders a 429/404 page as a successful load, so a blocked
        provider would otherwise sit there looking like a player.  The check
        runs in the background against the URL already loaded, and only when
        there is another provider to switch to.
        """
        attempts = getattr(self, "_playback_attempts", [])
        index = getattr(self, "_playback_index", 0)
        if len(attempts) < 2 or index >= len(attempts):
            return
        provider, url = attempts[index]

        def work() -> None:
            status = _probe_status(url)
            if status is not None and status >= 400:
                GLib.idle_add(self._skip_unavailable, index, provider, status)

        threading.Thread(target=work, daemon=True, name="lgl-provider-check").start()

    def _skip_unavailable(self, index: int, provider: str, status: int) -> bool:
        """Advance past a provider that answered an HTTP error."""
        if index != getattr(self, "_playback_index", 0):
            return False
        label = streaming.LABELS.get(provider, provider)
        self._playback_reason = f"{label} answered HTTP {status}, so "
        if not self._advance_playback():
            self._playback_reason = ""
        return False

    def _advance_playback(self) -> bool:
        """Move to the next provider in the chain; False when none is left."""
        attempts = getattr(self, "_playback_attempts", [])
        index = getattr(self, "_playback_index", 0) + 1
        if index >= len(attempts):
            return False
        provider, url = attempts[index]
        self._playback_index = index
        self._open_player(
            url,
            getattr(self, "_playback_title", ""),
            provider=provider,
            attempts=attempts,
        )
        return True

    def _try_another_provider(self) -> None:
        """Manual fallback for a provider that loads but has no stream."""
        if self._advance_playback():
            return
        self._open_player_message(
            getattr(self, "_playback_title", ""),
            "Every configured provider has been tried for this title.",
        )

    def _open_player(
        self,
        url: str,
        title: str,
        *,
        provider: str | None = None,
        attempts: list[tuple[str, str]] | None = None,
    ) -> None:
        provider = provider or self._streaming_provider()
        try:
            spec = streaming.provider_spec(provider)
            url = streaming.validate_embed_url(provider, url)
        except streaming.EmbedUrlError as error:
            self._open_player_message(title, str(error))
            return

        if attempts is None:
            attempts = [(provider, url)]
        self._playback_attempts = [(item[0], item[1]) for item in attempts]
        self._playback_title = title
        known = [item[1] for item in self._playback_attempts]
        self._playback_index = known.index(url) if url in known else 0

        current = self._stack.get_visible_child_name()
        if current not in (None, "player"):
            self._back_target = current
        for child in self._player.get_children():
            self._player.remove(child)
        heading = Gtk.Label(label=title, xalign=0)
        heading.set_ellipsize(Pango.EllipsizeMode.END)
        heading.get_style_context().add_class("movie-page-title")
        self._player.pack_start(heading, False, False, 0)

        if len(self._playback_attempts) > 1:
            status = Gtk.Label(
                label=(
                    f"{spec.label} · provider {self._playback_index + 1} "
                    f"of {len(self._playback_attempts)}"
                ),
                xalign=0,
            )
            status.get_style_context().add_class("movie-meta")
            self._player.pack_start(status, False, False, 0)

        frame = Gtk.AspectFrame(xalign=0.5, yalign=0.5, ratio=16 / 9, obey_child=False)
        frame.set_shadow_type(Gtk.ShadowType.NONE)
        frame.set_size_request(560, 315)
        frame.get_style_context().add_class("movie-player-frame")
        webkit = _webkit()
        if webkit is not None:
            view = webkit.WebView()
            self._webview = view
            self._apply_player_settings(view, spec)
            self._connect_player_fullscreen(view)

            overlay = Gtk.Overlay()
            overlay.add(view)
            loading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            loading.set_halign(Gtk.Align.CENTER)
            loading.set_valign(Gtk.Align.CENTER)
            spinner = Gtk.Spinner()
            spinner.set_size_request(26, 26)
            spinner.start()
            loading.pack_start(spinner, False, False, 0)
            loading.pack_start(
                Gtk.Label(label=f"Loading {spec.label}…"), False, False, 0
            )
            overlay.add_overlay(loading)

            def on_load_changed(_view, event) -> None:
                started = getattr(webkit.LoadEvent, "STARTED", None)
                finished = getattr(webkit.LoadEvent, "FINISHED", None)
                if event == started:
                    loading.show_all()
                elif event in (finished, None):
                    loading.hide()

            def on_load_failed(_view, _event, _uri, error) -> bool:
                loading.hide()
                # A blocked or unavailable provider falls through to the next
                # one instead of ending playback on the first failure.
                if self._advance_playback():
                    return False
                overlay.add_overlay(
                    self._player_error_box(
                        f"{spec.label} could not load this title: "
                        f"{getattr(error, 'message', error)}",
                        url,
                        retry=lambda: view.load_uri(url),
                    )
                )
                overlay.show_all()
                return False

            try:
                view.connect("load-changed", on_load_changed)
                view.connect("load-failed", on_load_failed)
            except (TypeError, AttributeError):
                loading.hide()
            view.load_uri(url)
            frame.add(overlay)
        else:
            frame.add(
                self._player_error_box(
                    "This build has no WebKitGTK, so the player cannot be "
                    "embedded.",
                    url,
                )
            )
        self._player.pack_start(frame, True, True, 0)
        self._player_frame = frame

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        note = Gtk.Label(
            label=(
                f"{self._playback_reason}Streaming is provided by "
                f"{spec.label}; whether this episode plays is up to that "
                "site, not this app."
            ),
            xalign=0,
        )
        self._playback_reason = ""
        note.set_line_wrap(True)
        note.get_style_context().add_class("movie-meta")
        row.pack_start(note, True, True, 0)
        if len(self._playback_attempts) > 1:
            other = Gtk.Button(label="Try another provider")
            other.get_style_context().add_class("movie-btn-dark")
            other.set_tooltip_text(
                "Use this if the stream is missing or will not start."
            )
            other.connect("clicked", lambda *_a: self._try_another_provider())
            row.pack_start(other, False, False, 0)
        self._player.pack_start(row, False, False, 0)
        self._player.show_all()
        self._stack.set_visible_child_name("player")
        self._update_back()

    def _apply_player_settings(self, view, spec: streaming.ProviderSpec) -> None:
        """Media permissions every player needs; security stays per provider."""
        settings = view.get_settings()
        for name, value in (
            ("enable_media", True),
            ("enable_media_stream", True),
            ("enable_mediasource", True),
            ("enable_webaudio", True),
            ("enable_webgl", True),
            ("enable_encrypted_media", True),
            ("enable_media_capabilities", True),
            ("media_playback_allows_inline", True),
            ("media_playback_requires_user_gesture", False),
        ):
            try:
                settings.set_property(name, value)
            except (TypeError, AttributeError):
                continue
        # A realistic browser User-Agent helps avoid bot-protection blocks.
        try:
            settings.set_property(
                "user-agent",
                "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
            )
        except (TypeError, AttributeError):
            pass
        if not spec.relaxed_web_security:
            return
        # Only the players that were already known to need this get it; the
        # security relaxations never apply to every provider.
        for name, value in (
            ("disable_web_security", True),
            ("allow_modal_dialogs", True),
            ("javascript_can_open_windows_automatically", True),
            ("allow_universal_access_from_file_urls", True),
            ("allow_file_access_from_file_urls", True),
        ):
            try:
                settings.set_property(name, value)
            except (TypeError, AttributeError):
                continue
        try:
            view.set_property("default-content-security-policy", "")
        except (TypeError, AttributeError):
            pass

    def _connect_player_fullscreen(self, view) -> None:
        """Let the player's own fullscreen button fill the real screen.

        WebKit asks the embedder for fullscreen instead of resizing itself, so
        without this handler the button only expands the video inside a small
        16:9 frame.
        """

        def on_enter(_view) -> bool:
            self._set_player_fullscreen(True)
            return False

        def on_leave(_view) -> bool:
            self._set_player_fullscreen(False)
            return False

        try:
            view.connect("enter-fullscreen", on_enter)
            view.connect("leave-fullscreen", on_leave)
        except (TypeError, AttributeError):
            pass

    def _set_player_fullscreen(self, active: bool) -> None:
        """Fullscreen the window and hand the whole screen to the video."""
        self._player_fullscreen = bool(active)
        toplevel = self.get_toplevel()
        if isinstance(toplevel, Gtk.Window):
            try:
                bar = toplevel.get_titlebar()
            except (AttributeError, TypeError):
                bar = None
            if bar is not None:
                try:
                    bar.set_visible(not active)
                except Exception:  # noqa: BLE001
                    pass
            try:
                if active:
                    toplevel.fullscreen()
                else:
                    toplevel.unfullscreen()
            except Exception:  # noqa: BLE001
                pass
        try:
            self._header.set_visible(not active)
        except Exception:  # noqa: BLE001
            pass
        # Nothing but the video stays on screen: title, "provider 2 of 6" and
        # the source note would otherwise sit above and below it.
        frame = getattr(self, "_player_frame", None)
        for child in self._player.get_children():
            if child is frame:
                continue
            try:
                child.set_visible(not active)
            except Exception:  # noqa: BLE001
                pass
        # The player keeps its 16:9 shape; in fullscreen it fills the screen
        # instead of sitting inside the reading margins.
        margin = 0 if active else 24
        for edge in ("start", "end"):
            getattr(self._player, f"set_margin_{edge}")(margin)
        getattr(self._player, "set_margin_top")(0 if active else 16)
        getattr(self._player, "set_margin_bottom")(0 if active else 16)

    def _player_error_box(self, message: str, url: str, retry=None) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_halign(Gtk.Align.CENTER)
        box.set_valign(Gtk.Align.CENTER)
        box.set_margin_start(24)
        box.set_margin_end(24)
        label = Gtk.Label(label=message)
        label.set_line_wrap(True)
        label.set_max_width_chars(70)
        label.set_justify(Gtk.Justification.CENTER)
        box.pack_start(label, False, False, 0)
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        buttons.set_halign(Gtk.Align.CENTER)
        if retry is not None:
            retry_button = Gtk.Button(label="Try again")
            retry_button.get_style_context().add_class("movie-btn-dark")
            retry_button.connect("clicked", lambda *_a: retry())
            buttons.pack_start(retry_button, False, False, 0)
        open_button = Gtk.Button(label="Open in browser")
        open_button.get_style_context().add_class("movie-btn-primary")
        open_button.connect("clicked", lambda *_a: self._open_external(url))
        buttons.pack_start(open_button, False, False, 0)
        box.pack_start(buttons, False, False, 0)
        box.show_all()
        return box

    def _open_external(self, url: str) -> None:
        toplevel = self.get_toplevel()
        if isinstance(toplevel, Gtk.Window):
            Gtk.show_uri_on_window(toplevel, url, Gdk.CURRENT_TIME)
        else:
            Gtk.show_uri(None, url, Gdk.CURRENT_TIME)

    def _open_player_message(self, title: str, message: str) -> None:
        """Stay on the watch page and explain why nothing can play."""
        current = self._stack.get_visible_child_name()
        if current not in (None, "player"):
            self._back_target = current
        for child in self._player.get_children():
            self._player.remove(child)
        heading = Gtk.Label(label=title, xalign=0)
        heading.set_ellipsize(Pango.EllipsizeMode.END)
        heading.get_style_context().add_class("movie-page-title")
        self._player.pack_start(heading, False, False, 0)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_valign(Gtk.Align.CENTER)
        label = Gtk.Label(label=message)
        label.set_line_wrap(True)
        label.set_max_width_chars(80)
        box.pack_start(label, False, False, 0)
        self._player.pack_start(box, True, True, 0)
        self._player.show_all()
        self._stack.set_visible_child_name("player")
        self._update_back()

    # -- watchlist ----------------------------------------------------------

    def _in_watchlist(self, key: str) -> bool:
        return key in self._watchlist()

    def _toggle_watchlist(self, item, button: Gtk.ToggleButton) -> None:
        entries = self._watchlist()
        if item.key in entries:
            entries = [entry for entry in entries if entry != item.key]
            message = "Removed from My List"
        else:
            entries.append(item.key)
            message = "Added to My List"
        self._settings.set(WATCHLIST_KEY, entries)
        button.set_active(item.key in entries)
        button.set_tooltip_text(message)

    # -- keys ---------------------------------------------------------------

    def _on_key(self, _widget, event) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            name = self._stack.get_visible_child_name()
            if name in ("details", "player"):
                self._navigate_back()
                return True
            self.destroy()
            return True
        return False


__all__ = ["Backdrop", "Images", "MediaCard", "MoviesWindow", "Poster", "PosterTile"]


