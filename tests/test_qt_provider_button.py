"""One provider button in the Media header: state text, dot and its menu."""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication, QComboBox  # noqa: E402

from lgl import qt_media, streaming  # noqa: E402


class Store(dict):
    network_timeout = 15

    def get(self, key, default=None):
        return dict.get(self, key, default)

    def set(self, key, value):
        self[key] = value

    def save(self):
        return True

    def __getattr__(self, name):
        return None


RESULTS = [
    {"provider": "toflix", "ms": 180, "ok": True, "status": "reachable"},
    {"provider": "vidy", "ms": 420, "ok": True, "status": "reachable"},
    {"provider": "moonflix", "ms": None, "ok": False, "status": "unreachable"},
]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def page(tmp_path):
    store = Store()
    store.set("streaming_provider", "toflix")
    store.set("provider_auto_select", False)
    widget = qt_media.MediaPage(settings=store, cache_root=tmp_path)
    return widget, store


def pump(app, seconds=0.2):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def test_one_button_and_no_provider_combo_in_the_header(app, tmp_path):
    widget, _ = page(tmp_path)
    header = widget.provider_button.parent()

    assert widget.provider_button.isVisibleTo(widget)
    assert header.findChildren(QComboBox) == []
    assert widget.health_badge is widget.provider_button


def test_the_menu_lists_auto_every_provider_and_test(app, tmp_path):
    widget, _ = page(tmp_path)
    widget._apply_health(RESULTS)

    menu = widget._provider_menu()
    texts = [action.text() for action in menu.actions() if not action.isSeparator()]

    assert texts[0].startswith("Auto (fastest:")
    assert texts[-1] == "Test connections now"
    for key in streaming.PROVIDERS:
        assert any(streaming.LABELS.get(key, key) in text for text in texts)
    assert any("180 ms" in text for text in texts)
    assert any("offline" in text for text in texts)


def test_picking_a_row_saves_the_setting_and_updates_the_button(app, tmp_path):
    widget, store = page(tmp_path)

    widget._choose_provider("vidy")

    assert store.get("streaming_provider") == "vidy"
    assert widget.provider_button.text().startswith("Vidy")
    assert widget.provider_button.text().endswith("not tested")


def test_auto_shows_the_fastest_provider(app, tmp_path):
    widget, store = page(tmp_path)
    store.set("provider_auto_select", True)
    widget._apply_health(RESULTS)

    texts = [a.text() for a in widget._provider_menu().actions() if not a.isSeparator()]

    assert texts[0] == "Auto (fastest: ToFlix)"


def test_offline_and_pending_text(app, tmp_path):
    widget, _ = page(tmp_path)
    widget._apply_health([{"provider": "toflix", "ms": None, "ok": False,
                           "status": "unreachable"}])
    assert widget.provider_button.text() == "ToFlix: offline"

    widget._measuring = True
    widget.health_results = []
    widget._update_badge()
    assert widget.provider_button.text() == "ToFlix · …"


def test_the_settings_choice_and_the_button_stay_in_sync(app, tmp_path):
    widget, store = page(tmp_path)
    store.set("streaming_provider", "vidy")

    widget._fill_provider_combo("home")

    assert widget.provider_button.text().startswith("Vidy")


def test_the_button_is_never_clipped_at_three_widths(app, tmp_path):
    widget, _ = page(tmp_path)
    widget._apply_health(RESULTS)
    for width in (1000, 1280, 1920):
        widget.resize(width, 900)
        widget.layout().activate() if widget.layout() else None
        app.processEvents()
        button = widget.provider_button
        needed = button.fontMetrics().horizontalAdvance(button.text()) + 34
        assert button.width() >= needed, (width, button.width(), needed)
        assert widget.width() >= 1000
