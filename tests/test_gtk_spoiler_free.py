"""Spoiler-free mode in the GTK media detail views.

The render helpers are called on a stub window: they only need the settings, so
building a whole MoviesWindow (and its providers) would prove nothing extra.
"""

import types

import pytest

gi = pytest.importorskip("gi")

try:
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
except (ValueError, ImportError):  # pragma: no cover - GTK 3 not installed
    pytest.skip("GTK 3 is not installed", allow_module_level=True)

from lgl import movies_ui, view_prefs


def has_display() -> bool:
    return bool(Gtk.init_check()[0])


needs_display = pytest.mark.skipif(not has_display(), reason="no display for GTK")


class Settings(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)

    def set(self, key, value):
        dict.__setitem__(self, key, value)

    def save(self):
        return True


def window(settings):
    return types.SimpleNamespace(_settings=settings, _season_token=1)


class Episode(types.SimpleNamespace):
    """A season episode: the renderer only asks which tracks it can play."""

    def playable_languages(self, _allow):
        return ()


def season(*titles):
    episodes = [
        types.SimpleNamespace(number=index, title=title, air_date="")
        for index, title in enumerate(titles, start=1)
    ]
    return types.SimpleNamespace(number=1, episodes=episodes)


def texts(widget):
    found = []

    def walk(item):
        if isinstance(item, Gtk.Label):
            found.append(item.get_text())
        children = getattr(item, "get_children", None)
        if children is not None:
            for child in children():
                walk(child)

    walk(widget)
    return found


@needs_display
def test_a_season_list_hides_its_titles():
    settings = Settings()
    view_prefs.set_spoilers(settings, True)
    box = Gtk.ListBox()
    movies_ui.MoviesWindow._apply_season(window(settings), 1, box, season("The Long Night"))
    shown = texts(box)
    assert view_prefs.HIDDEN_EPISODE in shown
    assert "The Long Night" not in shown
    # The episode number and the Play button must survive: it is still playable.
    assert "01" in shown
    assert "Play" in shown


@needs_display
def test_a_season_list_shows_its_titles_again():
    settings = Settings()
    box = Gtk.ListBox()
    movies_ui.MoviesWindow._apply_season(window(settings), 1, box, season("The Long Night"))
    shown = texts(box)
    assert "The Long Night" in shown
    assert view_prefs.HIDDEN_EPISODE not in shown


@needs_display
def test_the_anime_episode_section_says_the_mode_is_on():
    settings = Settings()
    view_prefs.set_spoilers(settings, True)
    body = Gtk.Box()
    stub = windows_anime(settings)
    movies_ui.MoviesWindow._build_ani_episode_section(stub, body, object())
    shown = texts(body)
    assert any("Spoiler-free mode is on" in text for text in shown)


@needs_display
def test_the_anime_episode_list_hides_its_titles():
    settings = Settings()
    view_prefs.set_spoilers(settings, True)
    stub = windows_anime(settings)
    body = Gtk.Box()
    stub._anime_detail_state = {"ani_episode_box": body}
    episodes = [Episode(number=4, title="The Last Stand")]
    movies_ui.MoviesWindow._render_ani_episodes(stub, object(), episodes, None)
    shown = texts(body)
    assert any("Episode 4 · Hidden (spoiler-free mode)" in text for text in shown)
    assert "The Last Stand" not in " ".join(shown)


def windows_anime(settings):
    return types.SimpleNamespace(
        _settings=settings,
        _current_ani_series=None,
        _current_ani_episodes=[],
        _anime_detail_state={},
    )
