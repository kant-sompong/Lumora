"""Spoiler-free mode in the Qt media detail view."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

qt_widgets = pytest.importorskip("PySide6.QtWidgets")

from lgl import view_prefs
from lgl.qt_media import EpisodeRow, MediaPage


class Settings(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)

    def set(self, key, value):
        dict.__setitem__(self, key, value)

    def save(self):
        return True


@pytest.fixture(scope="module")
def app():
    return qt_widgets.QApplication.instance() or qt_widgets.QApplication([])


@pytest.fixture
def page(app):
    return MediaPage(settings=Settings())


def test_the_episode_row_shows_the_title_it_is_given(page):
    row = EpisodeRow(3, view_prefs.HIDDEN_EPISODE)
    title = row.findChild(qt_widgets.QLabel, "episodeTitle")
    assert title is not None
    assert title.full_text() == view_prefs.HIDDEN_EPISODE
    # The number stays, so the episode is still identifiable and playable.
    number = row.findChild(qt_widgets.QLabel, "episodeNumber")
    assert number is not None and number.text() == "03"


def test_the_note_is_hidden_while_the_mode_is_off(page):
    page._sync_spoiler_note()
    assert page.spoiler_note.isHidden()
    assert page.spoiler_note.text() == ""


def test_the_note_explains_how_to_turn_the_mode_off(page):
    view_prefs.set_spoilers(page.settings, True)
    try:
        page._sync_spoiler_note()
        assert not page.spoiler_note.isHidden()
        assert "Spoiler-free mode is on" in page.spoiler_note.text()
        assert "Tools" in page.spoiler_note.text()
    finally:
        view_prefs.set_spoilers(page.settings, False)
    page._sync_spoiler_note()
    assert page.spoiler_note.isHidden()
