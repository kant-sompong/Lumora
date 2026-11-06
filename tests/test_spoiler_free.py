"""Spoiler-free mode: what the detail views may show while it is on."""

from lgl import view_prefs


class Settings(dict):
    """The parts of lgl.settings.Settings these helpers touch."""

    def get(self, key, default=None):
        return dict.get(self, key, default)

    def set(self, key, value):
        dict.__setitem__(self, key, value)

    def save(self):
        return True


def test_the_mode_is_off_until_it_is_switched_on():
    settings = Settings()
    assert view_prefs.spoiler_free(settings) is False
    assert view_prefs.episode_title(settings, "The Long Night") == "The Long Night"
    assert view_prefs.spoiler_note(settings) == ""


def test_an_episode_title_becomes_the_shared_placeholder():
    settings = Settings()
    view_prefs.set_spoilers(settings, True)
    assert view_prefs.spoiler_free(settings) is True
    assert view_prefs.episode_title(settings, "The Long Night") == (
        view_prefs.HIDDEN_EPISODE
    )
    assert "Hidden" in view_prefs.episode_title(settings, "The Long Night")


def test_the_note_says_how_to_turn_the_mode_off():
    settings = Settings()
    view_prefs.set_spoilers(settings, True)
    note = view_prefs.spoiler_note(settings)
    assert "Spoiler-free mode is on" in note
    assert "Tools" in note


def test_switching_it_off_brings_the_titles_back():
    settings = Settings()
    view_prefs.set_spoilers(settings, True)
    assert view_prefs.episode_title(settings, "Pilot") == view_prefs.HIDDEN_EPISODE
    view_prefs.set_spoilers(settings, False)
    assert view_prefs.episode_title(settings, "Pilot") == "Pilot"
    assert view_prefs.spoiler_note(settings) == ""


def test_missing_settings_and_missing_titles_are_harmless():
    assert view_prefs.spoiler_free(None) is False
    assert view_prefs.episode_title(None, None) == ""
    assert view_prefs.spoiler_note(None) == ""
    assert view_prefs.episode_title(Settings(), "  ") == ""
    assert view_prefs.episode_title(Settings(), None) == ""
