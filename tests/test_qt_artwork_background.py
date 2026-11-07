"""The Qt artwork backdrop: off paints nothing, on fades a blurred picture in."""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from lgl import platform_support, qt_library, view_prefs  # noqa: E402


class Store(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)

    def set(self, key, value):
        self[key] = value


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def cover() -> QImage:
    image = QImage(64, 36, QImage.Format_RGB888)
    image.fill(0xFFFFFF)
    return image


def test_the_setting_off_paints_nothing(app, tmp_path, monkeypatch):
    monkeypatch.setattr(platform_support, "cache_dir", lambda: tmp_path)
    page = QWidget()
    page.resize(800, 600)
    backdrop = qt_library.ArtworkBackdrop(page, Store({view_prefs.ARTWORK: False}))

    backdrop.show_image("cover-white.png", cover())

    assert backdrop.image is None
    assert backdrop.alpha == 0.0


def test_the_setting_on_fades_in_a_darkened_picture(app, tmp_path, monkeypatch):
    monkeypatch.setattr(platform_support, "cache_dir", lambda: tmp_path)
    settings = Store({view_prefs.ARTWORK: True, view_prefs.TRANSITIONS: "off"})
    page = QWidget()
    page.resize(800, 600)
    backdrop = qt_library.ArtworkBackdrop(page, settings)

    backdrop.show_image("cover-white.png", cover())
    deadline = time.time() + 15
    while backdrop.image is None and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)

    assert backdrop.image is not None
    assert backdrop.image.width() == 1600
    assert backdrop.alpha == 1.0  # transitions are off, so no fade
    # The cached picture really is dark: white artwork must not glow.
    sample = backdrop.image.scaled(80, 45).convertToFormat(QImage.Format_RGB888)
    pixels = qt_library._image_pixels(sample)
    assert max(pixels) <= 110
