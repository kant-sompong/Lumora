"""The sidebar: 72 px wide, 56 px items, labels that fit at three widths.

The window is built for real at 1000, 1280 and 1920 px, because "the label fits
at 1280" says nothing about the narrowest supported window.
"""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402

from lgl import qt_main  # noqa: E402
from lgl.design_tokens import RADIUS, TEXT_SIZES, TOKENS  # noqa: E402
from lgl.qt_theme import palette as token_palette  # noqa: E402
from lgl.settings import Settings  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def pump(app, seconds=0.25):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


@pytest.fixture
def window(app, tmp_path):
    qt_main.apply_theme(app, "dark")
    window = qt_main.Window(
        None, None, None, tmp_path, Settings(tmp_path / "config.json", keyring=False)
    )
    window.show()
    pump(app)
    yield window
    window.close()


def test_the_rail_is_72_px_with_56_px_items(window):
    rail = window._rail

    assert rail.width() == qt_main.RAIL_WIDTH == 72
    assert all(button.width() == 56 and button.height() == 56
               for button in window.rail_buttons.values())
    assert rail.layout().contentsMargins().left() == 8
    assert rail.layout().spacing() == 8


def test_the_rail_uses_the_tokens(window):
    assert TEXT_SIZES["caption"] == 11
    button = window.rail_buttons["browse"]
    assert button.TRANSITION_MS == 140
    assert RADIUS[1] == 10
    dark = token_palette("dark")
    assert dark["bg"] == TOKENS["dark"]["bg"]
    assert dark["accent"] == TOKENS["dark"]["accent"]


@pytest.mark.parametrize("width", [1000, 1280, 1920])
def test_every_sidebar_label_fits(window, app, width):
    window.resize(width, 820)
    pump(app, 0.2)

    for key, button in window.rail_buttons.items():
        assert button.label_fits(), f"{key} label does not fit at {width}px"
        # ... and the button itself stays inside the rail.
        assert button.geometry().right() <= window._rail.width()


def test_the_navigation_items_did_not_change(window):
    assert list(window.rail_buttons) == [
        "browse", "library", "media", "downloads", "settings", "about",
    ]


def test_the_active_item_paints_with_the_accent(window):
    window.rail_buttons["library"].set_active(True)
    pump(window, 0.2) if isinstance(window, QApplication) else pump(
        QApplication.instance(), 0.2
    )

    assert window.rail_buttons["library"]._active is True
    assert window.rail_buttons["library"]._phase > 0
