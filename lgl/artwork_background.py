"""Blurred, darkened artwork for the background of a title page.

The Interface preference already existed; this module makes the picture it
names.  It works on plain RGB bytes (no GUI toolkit, no Pillow - the frontends
decode a cover with GdkPixbuf or QImage and pass the small sample in), blurs it
heavily, flattens the contrast and darkens it, and writes a PNG to a cache keyed
by the artwork source and the window size.

The output is deliberately dark: a mean channel of at most about 90/255, so the
body text of either theme still passes contrast over it.  Both frontends paint
it at a further 55% opacity over their own page colour.

Everything here is pure Python plus ``zlib``; :func:`process` is the part tests
measure, and :func:`make_background` is the part the windows call.
"""

from __future__ import annotations

import hashlib
import struct
import zlib
from pathlib import Path
from typing import Any, Callable

from . import platform_support

#: Heavily blurred: the artwork must read as a mood, never as a picture.
BLUR_RADIUS = 6
BLUR_PASSES = 3
#: Contrast and brightness: 255 -> about 89, a dark grey with no highlights.
CONTRAST = 0.55
DARKEN = 0.45
#: The pixel sample both frontends decode a cover down to before blurring.
SAMPLE_WIDTH = 80
SAMPLE_HEIGHT = 45
#: The cached picture is this big; pages scale it to their own size, so the
#: cache is keyed by cover and this size rather than by window size.
BACKGROUND_SIZE = (1600, 900)
#: No RGB channel in the finished image may exceed this (0-255).
MAX_CHANNEL = 110
#: Cache folder under ``platform_support.cache_dir()``.
CACHE_DIRNAME = "artwork-backgrounds"

Loader = Callable[[str], Any]


def _clamp(value: float) -> int:
    return 0 if value <= 0 else 255 if value >= 255 else int(round(value))


def _box_blur(pixels: bytes | bytearray, width: int, height: int, radius: int) -> bytearray:
    """One separable box blur over an RGB buffer, edges clamped."""
    if radius <= 0 or width <= 0 or height <= 0:
        return bytearray(pixels)
    source = bytearray(pixels)
    horizontal = bytearray(len(source))
    for y in range(height):
        base = y * width * 3
        for channel in range(3):
            total = 0
            for x in range(-radius, radius + 1):
                total += source[base + min(width - 1, max(0, x)) * 3 + channel]
            span = radius * 2 + 1
            for x in range(width):
                horizontal[base + x * 3 + channel] = total // span
                out_index = min(width - 1, max(0, x - radius))
                in_index = min(width - 1, max(0, x + radius + 1))
                total += source[base + in_index * 3 + channel] - source[base + out_index * 3 + channel]
    result = bytearray(len(source))
    for x in range(width):
        for channel in range(3):
            total = 0
            for y in range(-radius, radius + 1):
                total += horizontal[min(height - 1, max(0, y)) * width * 3 + x * 3 + channel]
            span = radius * 2 + 1
            for y in range(height):
                result[y * width * 3 + x * 3 + channel] = total // span
                out_index = min(height - 1, max(0, y - radius))
                in_index = min(height - 1, max(0, y + radius + 1))
                total += (
                    horizontal[in_index * width * 3 + x * 3 + channel]
                    - horizontal[out_index * width * 3 + x * 3 + channel]
                )
    return result


def process(pixels: bytes | bytearray, width: int, height: int) -> bytes:
    """Blur, flatten and darken an RGB buffer; returns the same size."""
    if width <= 0 or height <= 0 or len(pixels) < width * height * 3:
        return b""
    blurred = bytearray(pixels)
    for _ in range(BLUR_PASSES):
        blurred = _box_blur(blurred, width, height, BLUR_RADIUS)
    styled = bytearray(len(blurred))
    for index, value in enumerate(blurred):
        # Pull towards mid-grey (low contrast), then take the light out.
        styled[index] = _clamp(((value - 128) * CONTRAST + 128) * DARKEN)
    return bytes(styled)


def resize(
    pixels: bytes | bytearray, width: int, height: int, target_width: int, target_height: int
) -> bytes:
    """Nearest-neighbour scale of an RGB buffer, enough after this much blur."""
    if width <= 0 or height <= 0 or target_width <= 0 or target_height <= 0:
        return b""
    if target_width == width and target_height == height:
        return bytes(pixels)
    columns = [min(width - 1, x * width // target_width) * 3 for x in range(target_width)]
    out = bytearray(target_width * target_height * 3)
    for y in range(target_height):
        source_row = (y * height // target_height) * width * 3
        base = y * target_width * 3
        for x, source_column in enumerate(columns):
            index = base + x * 3
            out[index : index + 3] = pixels[
                source_row + source_column : source_row + source_column + 3
            ]
    return bytes(out)


def mean_luma(pixels: bytes | bytearray) -> float:
    """Average perceived brightness, 0-255; the darkness test uses this."""
    if not pixels:
        return 0.0
    total = 0.0
    count = 0
    for index in range(0, len(pixels) - 2, 3):
        total += 0.2126 * pixels[index] + 0.7152 * pixels[index + 1] + 0.0722 * pixels[index + 2]
        count += 1
    return total / count if count else 0.0


def cache_file(
    source: str, width: int, height: int, *, cache_dir: Path | str | None = None
) -> Path:
    """Where the finished background for this source and size lives."""
    directory = (
        Path(cache_dir) if cache_dir is not None
        else platform_support.cache_dir() / CACHE_DIRNAME
    )
    digest = hashlib.sha1(
        f"{source}|{int(width)}x{int(height)}|{BLUR_RADIUS}|{CONTRAST}|{DARKEN}".encode("utf-8")
    ).hexdigest()[:20]
    return directory / f"{digest}.png"


def _png(pixels: bytes, width: int, height: int) -> bytes:
    """A minimal RGB PNG writer, so no imaging library is needed anywhere."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        block = tag + data
        return struct.pack(">I", len(data)) + block + struct.pack(">I", zlib.crc32(block) & 0xFFFFFFFF)

    rows = b"".join(
        b"\x00" + pixels[y * width * 3 : (y + 1) * width * 3] for y in range(height)
    )
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows, 9))
        + chunk(b"IEND", b"")
    )


def make_background(
    source: Any,
    width: int,
    height: int,
    *,
    load: Loader | None = None,
    cache_dir: Path | str | None = None,
    force: bool = False,
) -> Path | None:
    """A cached blurred background for ``source``, or ``None`` without artwork.

    ``load(source)`` returns ``(rgb_bytes, sample_width, sample_height)``; the
    frontends decode a cover with their own toolkit and hand that in, so this
    module never needs an image decoder.  An empty source, a loader that finds
    nothing, or an unwritable cache all mean ``None`` - the page then keeps its
    normal background.
    """
    name = str(source or "").strip()
    if not name or not callable(load):
        return None
    target = cache_file(name, width, height, cache_dir=cache_dir)
    if target.is_file() and not force:
        return target
    try:
        loaded = load(name)
    except Exception:  # noqa: BLE001 - a broken cover is not worth a crash
        return None
    if not loaded:
        return None
    try:
        pixels, sample_width, sample_height = loaded
    except (TypeError, ValueError):
        return None
    styled = process(pixels, sample_width, sample_height)
    if not styled:
        return None
    if width > 0 and height > 0:
        styled = resize(styled, sample_width, sample_height, int(width), int(height))
        sample_width, sample_height = int(width), int(height)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_bytes(_png(styled, sample_width, sample_height))
        tmp.replace(target)
    except OSError:
        return None
    return target


__all__ = [
    "BACKGROUND_SIZE",
    "BLUR_PASSES",
    "BLUR_RADIUS",
    "CACHE_DIRNAME",
    "CONTRAST",
    "DARKEN",
    "MAX_CHANNEL",
    "SAMPLE_HEIGHT",
    "SAMPLE_WIDTH",
    "cache_file",
    "make_background",
    "mean_luma",
    "process",
    "resize",
]
