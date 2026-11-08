"""GTK provider health sheet and the two Media settings switches.

No provider is contacted: results are handed to the dialog as canned data.
"""

from __future__ import annotations

import pytest

gi = pytest.importorskip("gi")
try:
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
except (ValueError, ImportError):  # pragma: no cover
    pytest.skip("GTK 3 is not installed", allow_module_level=True)

from lgl import movies_ui, provider_health as ph, streaming  # noqa: E402
from lgl.settings import Settings  # noqa: E402

if not Gtk.init_check()[0]:  # pragma: no cover
    pytest.skip("no GTK display", allow_module_level=True)

RESULTS = [
    {"provider": "cinesrc", "ok": True, "ms": 180, "status": "fast", "reason": None},
    {"provider": "velarium", "ok": True, "ms": 420, "status": "ok", "reason": None},
    {"provider": "vidy", "ok": True, "ms": 120, "status": "fast", "reason": None},
    {"provider": "afterdusk", "ok": False, "ms": None, "status": "unreachable",
     "reason": "DNS failed"},
]


class FakeWindow:
    def __init__(self, settings):
        self.settings = settings
        self._settings = settings
        self._active_section = "home"
        self._parent = Gtk.Window()
        self._playback_attempts = []

    def _streaming_provider(self):
        return streaming.clean_provider(
            self._settings.get("streaming_provider", streaming.DEFAULT_PROVIDER)
        )

    def _anime_provider(self):
        return streaming.clean_anime_provider(
            self._settings.get("anime_streaming_provider", streaming.DEFAULT_ANIME_PROVIDER)
        )

    def _set_streaming_provider(self, key):
        self._settings.set("streaming_provider", streaming.clean_provider(key))

    def _set_anime_provider(self, key):
        self._settings.set("anime_streaming_provider", streaming.clean_anime_provider(key))


@pytest.fixture
def dialog(tmp_path):
    settings = Settings(tmp_path / "config.json", keyring=False)
    window = FakeWindow(settings)
    sheet = movies_ui.ProviderHealthDialog(window, settings)
    yield sheet, window, settings
    sheet.destroy()
    window._parent.destroy()


def test_dialog_lists_every_provider(dialog):
    sheet, _, _ = dialog
    assert set(sheet._rows) == set(streaming.PROVIDERS)


def test_results_show_latency_and_status(dialog):
    sheet, _, _ = dialog
    sheet._apply(RESULTS)
    name, ms, status = sheet._rows["vidy"]
    assert ms.get_text() == "120 ms"
    assert "fast" in status.get_text()
    assert "3 of 4 providers reachable" in sheet._headline.get_text()


def test_unreachable_rows_are_dimmed(dialog):
    sheet, _, _ = dialog
    sheet._apply(RESULTS)
    name, _, _ = sheet._rows["afterdusk"]
    assert name.get_style_context().has_class("dim")
    healthy, _, _ = sheet._rows["vidy"]
    assert not healthy.get_style_context().has_class("dim")


def test_active_provider_line_shows_the_measured_time(dialog):
    sheet, _, settings = dialog
    settings.set("provider_auto_select", False)
    settings.set("streaming_provider", "velarium")
    sheet._apply(RESULTS)
    assert "Velarium · 420 ms" in sheet._active.get_text()


def test_use_fastest_selects_the_quickest(dialog):
    sheet, _, settings = dialog
    settings.set("provider_auto_select", False)
    sheet._apply(RESULTS)
    sheet.use_fastest()
    assert settings.get("streaming_provider") == "vidy"


def test_use_fastest_does_nothing_without_a_reachable_provider(dialog):
    sheet, _, settings = dialog
    settings.set("provider_auto_select", False)
    settings.set("streaming_provider", "cinesrc")
    sheet._apply([
        {"provider": key, "ok": False, "ms": None, "status": "unreachable",
         "reason": "DNS failed"}
        for key in streaming.PROVIDERS
    ])
    sheet.use_fastest()
    assert settings.get("streaming_provider") == "cinesrc"
    assert sheet._headline.get_text() == ph.NO_PROVIDER_MESSAGE


def test_anime_section_picks_an_anime_provider(dialog):
    sheet, window, settings = dialog
    window._active_section = "anime"
    settings.set("provider_auto_select", False)
    sheet._apply(RESULTS)
    sheet.use_fastest()
    assert settings.get("anime_streaming_provider") == "vidy"
    assert settings.get("streaming_provider") in (None, streaming.DEFAULT_PROVIDER)


def test_automatic_selection_is_skipped_while_playing(dialog):
    sheet, window, settings = dialog
    settings.set("streaming_provider", "cinesrc")
    window._playback_attempts = [("cinesrc", "https://cinesrc.st/embed/movie/1")]
    sheet._apply(RESULTS)
    assert settings.get("streaming_provider") == "cinesrc"


class SettingsOwner(Gtk.Window):
    """The slice of the main window the settings sheet touches."""

    def __init__(self, settings, download_dir):
        super().__init__()
        self.settings = settings
        self.download_dir = download_dir


def media_settings(tmp_path):
    from lgl import ui as gtk_ui

    settings = Settings(tmp_path / "config.json", keyring=False)
    dialog = gtk_ui.SettingsDialog(SettingsOwner(settings, tmp_path), page="media")
    return dialog, settings


def test_media_settings_offer_both_choices(tmp_path):
    dialog, settings = media_settings(tmp_path)
    assert dialog._media_auto_provider.get_active() is True
    assert dialog._media_pinned_provider.get_active() is False
    dialog._media_auto_provider.set_active(False)
    assert settings.get(ph.AUTO_KEY) is False
    assert dialog._media_pinned_provider.get_active() is True
    dialog.destroy()


def test_manual_provider_pick_switches_automatic_off(tmp_path):
    dialog, settings = media_settings(tmp_path)
    assert settings.get(ph.AUTO_KEY, True) is True
    dialog._media_provider.set_active_id("vidy")
    assert settings.get(ph.AUTO_KEY) is False
    assert dialog._media_auto_provider.get_active() is False
    assert dialog._media_pinned_provider.get_active() is True
    dialog.destroy()


def test_pinned_switch_turns_automatic_back_on(tmp_path):
    dialog, settings = media_settings(tmp_path)
    dialog._media_pinned_provider.set_active(True)
    assert settings.get(ph.AUTO_KEY) is False
    dialog._media_pinned_provider.set_active(False)
    assert settings.get(ph.AUTO_KEY) is True
    assert dialog._media_auto_provider.get_active() is True
    dialog.destroy()
