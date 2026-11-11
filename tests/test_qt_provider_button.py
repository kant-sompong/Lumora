"""One provider button in the Media header: state text, dot and its menu."""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication, QComboBox, QWidget  # noqa: E402

from lgl import hidden_gems, mal, qt_media, release_feed, streaming, tmdb  # noqa: E402


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


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Building a MediaPage reloads the real shelves on the Qt pool.

    The header is what these tests measure, so every section loader and the
    startup provider ping answer locally: no TMDB, AniList or provider request
    may reach the pool and block the next test's work.
    """
    monkeypatch.setattr(tmdb, "load_api_key", lambda *args, **kwargs: "")
    monkeypatch.setattr(mal, "load_client_id", lambda *args, **kwargs: None)
    monkeypatch.setattr(release_feed, "load_feed",
                        lambda *args, **kwargs: release_feed.FeedResult())
    monkeypatch.setattr(hidden_gems, "load_gems",
                        lambda *args, **kwargs: hidden_gems.GemsResult())
    monkeypatch.setattr(hidden_gems, "load_anime_lists", lambda *args, **kwargs: {})
    monkeypatch.setattr(qt_media.provider_health.AutoSelect, "start",
                        lambda self, force=False: False)


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


def _squeeze(width, widget, child, needed):
    """Why a header widget did not get the room it asked for, for the log."""
    hint = child.sizeHint().width()
    floor = child.minimumSizeHint().width()
    metrics = child.fontMetrics()
    return {
        "asked_for": width,
        "page": widget.width(),
        "child": child.width(),
        "size_hint": hint,
        "minimum": floor,
        "needed": needed,
        "font": (child.font().family(), metrics.horizontalAdvance("Search Movies & TV")),
        "header": [w.width() for w in widget.provider_button.parent().children()
                   if isinstance(w, QWidget)],
    }


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

    widget._fill_provider_menu("home")

    assert widget.provider_button.text().startswith("Vidy")


def test_the_button_is_never_clipped_at_three_widths(app, tmp_path):
    widget, _ = page(tmp_path)
    widget._apply_health(RESULTS)
    widget.show()
    for width in (1000, 1280, 1920):
        widget.resize(width, 900)
        app.processEvents()
        button = widget.provider_button
        metrics = button.fontMetrics()
        # The ping is never elided, even where the header has no room to spare.
        ping_room = (button._text_left() + metrics.horizontalAdvance(button._suffix)
                     + button.PAD)
        assert button.width() >= ping_room, _squeeze(width, widget, button, ping_room)
        if width >= 1280:
            needed = metrics.horizontalAdvance(button.text()) + button._text_left() + button.PAD
            assert button.width() >= needed, _squeeze(width, widget, button, needed)
        assert widget.width() >= 1000, (width, widget.width())
    widget.close()


def test_the_button_keeps_its_full_text_at_1280(app, tmp_path):
    store = Store()
    store.set("streaming_provider", "cinesrc")
    store.set("provider_auto_select", False)
    widget = qt_media.MediaPage(settings=store, cache_root=tmp_path)
    widget._apply_health([{"provider": "cinesrc", "ms": 180, "ok": True,
                           "status": "fast", "reason": None}])
    widget.show()
    widget.resize(1280, 900)
    app.processEvents()

    button = widget.provider_button
    assert button.text() == "CineSrc · 180 ms"
    needed = button.fontMetrics().horizontalAdvance("CineSrc · 180 ms")
    assert button.width() >= needed, _squeeze(1280, widget, button, needed)
    widget.close()


STATES = (("unknown", None), ("offline", None), ("pending", None), ("ok", 180))


def test_the_name_is_not_elided_in_any_state_at_1280(app, tmp_path):
    """The reported bug: at 1280 px the header painted ``CineS… · not tested``."""
    widget, _ = page(tmp_path)
    widget.show()
    widget.resize(1280, 900)
    app.processEvents()
    button = widget.provider_button

    for status, ms in STATES:
        button.set_state("CineSrc", status, ms)
        app.processEvents()
        hint = button.sizeHint()
        button.resize(hint)
        assert button.displayed_text() == button.text(), (
            status, hint.width(), button.displayed_text())
    widget.close()


def test_the_width_does_not_change_while_a_test_runs(app, tmp_path):
    widget, _ = page(tmp_path)
    button = widget.provider_button

    widths = set()
    for status, ms in STATES:
        button.set_state("CineSrc", status, ms)
        widths.add(button.sizeHint().width())

    assert len(widths) == 1, widths
    widget.close()


def test_the_search_placeholder_fits_at_1280(app, tmp_path):
    widget, _ = page(tmp_path)
    widget.show()
    widget.resize(1280, 900)
    app.processEvents()

    field = widget.search
    text = field.placeholderText()
    assert text
    needed = field.fontMetrics().horizontalAdvance(text) + 24
    assert field.width() >= needed, _squeeze(1280, widget, field, needed)
    widget.close()
