"""The blurred artwork module: size, cache, no cover, and the darkness bound."""

from __future__ import annotations

import struct

from lgl import artwork_background as ab


def white(width: int = ab.SAMPLE_WIDTH, height: int = ab.SAMPLE_HEIGHT) -> bytes:
    return bytes([255, 255, 255]) * (width * height)


def png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


def test_the_blurred_image_keeps_the_sample_size():
    out = ab.process(white(), ab.SAMPLE_WIDTH, ab.SAMPLE_HEIGHT)
    assert len(out) == ab.SAMPLE_WIDTH * ab.SAMPLE_HEIGHT * 3


def test_a_white_cover_is_dark_enough_for_body_text():
    """Even a pure white cover must not produce a bright background."""
    out = ab.process(white(), ab.SAMPLE_WIDTH, ab.SAMPLE_HEIGHT)
    assert max(out) <= ab.MAX_CHANNEL
    assert ab.mean_luma(out) <= ab.MAX_CHANNEL
    assert ab.mean_luma(out) >= 20


def test_the_cache_is_written_once_and_reused(tmp_path):
    calls = []

    def load(source: str):
        calls.append(source)
        return white(), ab.SAMPLE_WIDTH, ab.SAMPLE_HEIGHT

    first = ab.make_background("cover.png", 1600, 900, load=load, cache_dir=tmp_path)
    second = ab.make_background("cover.png", 1600, 900, load=load, cache_dir=tmp_path)

    assert first is not None and first == second and first.is_file()
    # The cached picture is already the size the pages paint.
    assert png_size(first.read_bytes()) == (1600, 900)
    assert calls == ["cover.png"]


def test_a_different_size_is_a_different_cache_entry(tmp_path):
    loaded = []

    def load(source: str):
        loaded.append(source)
        return white(), ab.SAMPLE_WIDTH, ab.SAMPLE_HEIGHT

    small = ab.make_background("cover.png", 800, 450, load=load, cache_dir=tmp_path)
    large = ab.make_background("cover.png", 1600, 900, load=load, cache_dir=tmp_path)

    assert small != large
    assert len(loaded) == 2


def test_no_cover_means_no_background(tmp_path):
    assert ab.make_background("", 1600, 900, load=lambda _s: (white(), 80, 45),
                              cache_dir=tmp_path) is None
    assert ab.make_background("   ", 1600, 900, load=lambda _s: (white(), 80, 45),
                              cache_dir=tmp_path) is None
    assert ab.make_background("missing.png", 1600, 900, load=lambda _s: None,
                              cache_dir=tmp_path) is None
    assert ab.make_background(None, 1600, 900, load=lambda _s: None,
                              cache_dir=tmp_path) is None
