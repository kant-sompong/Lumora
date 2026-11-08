"""Qt provider health interface: the strip, the measured list and pinning.

Offscreen, no network: health results are handed in as canned data.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from lgl import provider_health as ph  # noqa: E402
from lgl import qt_media, streaming  # noqa: E402
from lgl.settings import Settings  # noqa: E402

RESULTS = [
    {"provider": "cinesrc", "ok": True, "ms": 180, "status": "fast", "reason": None},
    {"provider": "toflix", "ok": True, "ms": 420, "status": "ok", "reason": None},
    {"provider": "vidy", "ok": True, "ms": 120, "status": "fast", "reason": None},
    {"provider": "moonflix", "ok": False, "ms": None, "status": "unreachable",
     "reason": "DNS failed"},
]


@pytest.fixture
def page(tmp_path):
    app = QApplication.instance() or QApplication([])
    assert app is not None
    settings = Settings(tmp_path / "config.json", keyring=False)
    widget = qt_media.MediaPage(settings, tmp_path)
    yield widget, settings
    widget.close()


def test_health_controls_exist(page):
    widget, _ = page
    assert widget.health_btn.text() == "Test connection"
    assert widget.health_refresh.text() == "Refresh"
    assert widget.fastest_btn.text() == "Use fastest"


def test_results_are_listed_fastest_first_with_latency_and_status(page):
    widget, _ = page
    widget._apply_health(RESULTS)
    assert not widget.health_bar.isVisibleTo(widget)
    widget._toggle_health_list()
    assert widget.health_list.isVisibleTo(widget)
    assert widget.health_rows.count() == len(RESULTS)
    texts = []
    for index in range(widget.health_rows.count()):
        row = widget.health_rows.itemAt(index).widget()
        texts.append(" ".join(
            child.text() for child in row.findChildren(type(widget.health_note))
        ))
    assert "120 ms" in texts[0] and "fast" in texts[0]
    assert "Vidy" in texts[0]
    assert "--" in texts[-1] and "unreachable" in texts[-1]


def test_unreachable_rows_are_greyed_out(page):
    widget, _ = page
    widget._apply_health(RESULTS)
    last = widget.health_rows.itemAt(widget.health_rows.count() - 1).widget()
    styles = [child.styleSheet() for child in last.findChildren(type(widget.health_note))]
    assert any("color" in style for style in styles)


def test_active_provider_badge_shows_time(page):
    widget, settings = page
    settings.set("streaming_provider", "toflix")
    settings.set("provider_auto_select", False)
    widget.show_section("home")
    widget._apply_health(RESULTS)
    assert widget.health_badge.text() == "ToFlix 420 ms"


def test_use_fastest_selects_the_quickest_reachable(page):
    widget, settings = page
    widget._apply_health(RESULTS)
    widget.fastest_btn.click()
    assert settings.get("streaming_provider") == "vidy"
    assert widget.provider_combo.currentData() == "vidy"
    assert "Vidy" in widget.health_note.text()


def test_use_fastest_keeps_the_provider_when_nothing_is_reachable(page):
    widget, settings = page
    settings.set("streaming_provider", "toflix")
    widget._apply_health([
        {"provider": key, "ok": False, "ms": None, "status": "unreachable",
         "reason": "DNS failed"}
        for key in streaming.PROVIDERS
    ])
    widget.fastest_btn.click()
    assert settings.get("streaming_provider") == "toflix"
    assert widget.health_note.text() == ph.NO_PROVIDER_MESSAGE


def test_movie_and_anime_selections_are_independent(page):
    widget, settings = page
    widget.show_section("anime")
    widget._apply_health(RESULTS)
    widget.fastest_btn.click()
    assert settings.get("anime_streaming_provider") == "vidy"
    assert settings.get("streaming_provider") in (None, streaming.DEFAULT_PROVIDER)


def test_anime_use_fastest_ignores_providers_without_an_anime_route(page):
    widget, settings = page
    widget.show_section("anime")
    widget._apply_health([
        {"provider": "chillflix", "ok": True, "ms": 40, "status": "fast", "reason": None},
        {"provider": "vidy", "ok": True, "ms": 200, "status": "fast", "reason": None},
    ])
    widget.use_fastest()
    assert settings.get("anime_streaming_provider") == "vidy"


def test_manual_pick_switches_automatic_selection_off(page):
    widget, settings = page
    widget.show_section("home")
    index = widget.provider_combo.findData("moonflix")
    widget.provider_combo.setCurrentIndex(index)
    assert settings.get("provider_auto_select") is False
    assert widget.health.manual is True
    assert widget.health_note.text() == ph.MANUAL_MESSAGE


def test_automatic_selection_respects_playback(page):
    widget, settings = page
    widget.show_section("home")
    settings.set("streaming_provider", "toflix")
    widget._fill_provider_combo("home")
    widget.stack.setCurrentWidget(widget.player)
    widget._apply_health(RESULTS)
    assert settings.get("streaming_provider") == "toflix"


def test_automatic_selection_applies_when_idle(page):
    widget, settings = page
    widget.show_section("home")
    settings.set("streaming_provider", "toflix")
    widget._fill_provider_combo("home")
    widget._apply_health(RESULTS)
    assert settings.get("streaming_provider") == "vidy"


def test_closing_the_page_stops_the_engine(page):
    widget, _ = page
    closed = []
    widget.health.shutdown = lambda: closed.append(True)
    widget.close()
    assert closed == [True]
