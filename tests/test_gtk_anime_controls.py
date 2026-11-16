"""The anime page hero: Watch and Back, no episode or track controls.

The episode and the audio track are not page widgets any more.  Watch picks
the episode (the next after a play, else the first) and reads the track from
Settings; these tests pin both, on a stub page with the real widgets.
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

gi = pytest.importorskip("gi")

try:
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
except (ValueError, ImportError):  # pragma: no cover - GTK 3 typelib missing
    pytest.skip("GTK 3 is not installed", allow_module_level=True)

from lgl import anipm, mal, movies_ui  # noqa: E402


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


class Loader:
    def __init__(self) -> None:
        self.asks: list[str] = []

    def load(self, url, size, callback) -> None:
        self.asks.append(url)
        callback(None)


BOUND = (
    "_build_anime_details_page",
    "_fill_meta",
    "_primary_button",
    "_dark_button",
    "_watch_anime",
    "_anime_playback_choice",
    "_anime_track",
    "_anime_start_episode",
    "_render_ani_episodes",
    "_on_ani_episode_play",
)


def page(settings=None):
    """A page stub with the real anime-hero and episode-row methods bound."""
    stub = types.SimpleNamespace(
        _images=Loader(),
        _settings=Settings(settings or {}),
        _anime_detail_state={},
        _anime_detail_token=1,
        _current_ani_episodes=[],
        watched=[],
        played=[],
        attempts=[],
        started=[],
        navigated=[],
    )
    for name in BOUND:
        setattr(stub, name, types.MethodType(getattr(movies_ui.MoviesWindow, name), stub))
    stub._anime_episode_count = movies_ui.MoviesWindow._anime_episode_count
    stub._anime_provider = lambda: "ani"
    stub._load_detail_art = lambda *a, **k: None
    stub._navigate_back = lambda *a, **k: stub.navigated.append(True)
    stub._play_anime_pm = lambda item, episode, language: stub.played.append(
        (item, episode, language)
    )
    # The MAL path builds its attempts per track, so recording the build is
    # how these tests read back the language Watch chose.
    stub._anime_attempts = lambda item, episode, language, embed: (
        stub.attempts.append((item, episode, language)) or [("ani", "u")]
    )
    stub._start_anime_attempts = (
        lambda item, title, episode, attempts: stub.started.append(
            (item, title, episode, attempts)
        )
    )
    stub._anime_status = Gtk.Label()
    return stub


def item(**kwargs):
    base = dict(
        id=21,
        title="One Piece",
        year="1999",
        synopsis="A quiet journey.",
        poster_url="",
        rating=8.7,
        episodes=12,
        status="",
        media_type="TV",
    )
    base.update(kwargs)
    return mal.AnimeItem(**base)


def ani_title(**kwargs):
    base = dict(anilist_id=21, title="One Piece", episodes=12, year="1999")
    base.update(kwargs)
    return anipm.AniTitle(**base)


def widgets(node, kind):
    found = []

    def walk(current):
        if isinstance(current, kind):
            found.append(current)
        children = getattr(current, "get_children", None)
        if children is not None:
            for child in children():
                walk(child)

    walk(node)
    return found


def labels(node):
    return [label.get_text() for label in widgets(node, Gtk.Label)]


def button_text(button: Gtk.Button) -> str:
    """A hero button's words: Watch draws an icon plus a label."""
    return " ".join(text for text in labels(button) if text)


def action_row(page_widget) -> Gtk.Box:
    found = [
        box for box in widgets(page_widget, Gtk.Box)
        if box.get_style_context().has_class("movie-hero-actions")
    ]
    assert len(found) == 1
    return found[0]


# -- the hero --------------------------------------------------------------


@needs_display
def test_the_anime_hero_offers_watch_and_back_only():
    stub = page()
    hero = stub._build_anime_details_page(item())
    row = action_row(hero)
    assert [button_text(button) for button in widgets(row, Gtk.Button)] == [
        "Watch",
        "Back",
    ]


@needs_display
def test_the_anime_hero_has_no_episode_spinner():
    stub = page()
    hero = stub._build_anime_details_page(item())
    assert widgets(hero, Gtk.SpinButton) == []


@needs_display
def test_the_anime_hero_has_no_language_combo():
    stub = page()
    hero = stub._build_anime_details_page(item())
    assert widgets(hero, Gtk.ComboBox) == []
    assert "Episode" not in labels(action_row(hero))


@needs_display
def test_the_page_state_holds_no_removed_widgets():
    stub = page()
    stub._build_anime_details_page(item())
    assert "episode" not in stub._anime_detail_state
    assert "language" not in stub._anime_detail_state


@needs_display
def test_watch_in_the_hero_plays_this_title():
    stub = page()
    hero = stub._build_anime_details_page(item())
    watch = [
        button for button in widgets(action_row(hero), Gtk.Button)
        if button_text(button) == "Watch"
    ][0]
    watch.clicked()
    assert [entry[0].id for entry in stub.started] == [21]
    assert stub.started[0][2] == 1


@needs_display
def test_back_in_the_hero_goes_back():
    stub = page()
    hero = stub._build_anime_details_page(item())
    back = [
        button for button in widgets(action_row(hero), Gtk.Button)
        if button_text(button) == "Back"
    ][0]
    back.clicked()
    assert stub.navigated == [True]


# -- Watch: which episode --------------------------------------------------


def test_watch_starts_at_the_first_episode():
    stub = page()
    stub._watch_anime(ani_title())
    assert stub.played[-1][1] == 1


def test_watch_resumes_at_the_next_episode():
    show = ani_title()
    stub = page()
    stub._playing_episode = (1, 3)
    stub._anime_playing_key = show.key
    stub._watch_anime(show)
    assert stub.played[-1][1] == 4


def test_a_finished_series_starts_over():
    show = ani_title(episodes=12)
    stub = page()
    stub._playing_episode = (1, 12)
    stub._anime_playing_key = show.key
    stub._watch_anime(show)
    assert stub.played[-1][1] == 1


def test_progress_for_another_title_is_ignored():
    show = ani_title()
    stub = page()
    stub._playing_episode = (1, 5)
    stub._anime_playing_key = "anipm:999"
    stub._watch_anime(show)
    assert stub.played[-1][1] == 1


def test_an_unknown_episode_count_does_not_block_resuming():
    show = ani_title(episodes=0)
    stub = page()
    stub._current_ani_episodes = [types.SimpleNamespace(number=1)] * 5
    stub._playing_episode = (1, 4)
    stub._anime_playing_key = show.key
    stub._watch_anime(show)
    assert stub.played[-1][1] == 5


# -- Watch: which track ----------------------------------------------------


@pytest.mark.parametrize("track,expected", (("sub", "sub"), ("dub", "dub")))
def test_watch_plays_the_track_from_settings(track, expected):
    stub = page(settings={"anime_language": track})
    stub._watch_anime(ani_title())
    assert stub.played[-1][2] == expected


def test_the_dub_flag_is_the_fallback_when_no_track_is_set():
    stub = page(settings={"anime_dub": True})
    stub._watch_anime(ani_title())
    assert stub.played[-1][2] == "dub"


def test_a_vostfr_setting_reaches_an_anime_provider():
    show = item()
    stub = page(settings={"anime_language": "vostfr"})
    stub._watch_anime(show)
    assert stub.attempts[-1][2] == "vostfr"
    assert stub.started[-1][2] == 1


@pytest.mark.parametrize("track", ("sub", "dub", "vostfr"))
def test_the_track_helper_follows_settings(track):
    stub = page(settings={"anime_language": track})
    assert stub._anime_track() == track


# -- the episode rows keep their track buttons ----------------------------


def ani_episode(number, embeds):
    return anipm.AniEpisode(
        number=number,
        title=f"Episode {number}",
        embeds=embeds,
        default_embed="",
    )


def row_buttons(title, episodes, available=None):
    stub = page()
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    stub._anime_detail_state["ani_episode_box"] = box
    stub._render_ani_episodes(title, episodes, available)
    return stub, box


@needs_display
def test_the_episode_rows_keep_all_four_track_buttons():
    stub, box = row_buttons(
        ani_title(),
        [ani_episode(1, {"sub": "https://ani.pm/e/1", "dub": "https://ani.pm/e/2"})],
    )
    names = [
        button.get_label() for button in widgets(box, Gtk.Button)
    ]
    assert names == ["Sub", "Dub", "Sub·Hard", "Dub·Hard"]


@needs_display
def test_a_row_button_plays_the_clicked_track():
    stub, box = row_buttons(
        ani_title(),
        [ani_episode(3, {"sub": "https://ani.pm/e/1", "dub": "https://ani.pm/e/2"})],
    )
    buttons = {button.get_label(): button for button in widgets(box, Gtk.Button)}
    buttons["Dub"].clicked()
    assert stub.played[-1][1:] == (3, "dub")
    buttons["Sub"].clicked()
    assert stub.played[-1][1:] == (3, "sub")


@needs_display
def test_an_unavailable_track_button_is_disabled():
    stub, box = row_buttons(
        ani_title(), [ani_episode(2, {"sub": "https://ani.pm/e/1"})]
    )
    buttons = {button.get_label(): button for button in widgets(box, Gtk.Button)}
    assert buttons["Sub"].get_sensitive() is True
    assert buttons["Dub·Hard"].get_sensitive() is False


# -- the removed widgets are gone from the source -------------------------


def test_the_page_has_no_episode_spinner_or_language_combo_left():
    source = Path(movies_ui.__file__).read_text(encoding="utf-8")
    for gone in (
        "episode_spin",
        "language_combo",
        "on_language_changed",
        'state.get("episode")',
        'state.get("language")',
        '["episode"]',
        '["language"]',
    ):
        assert gone not in source, f"{gone} is still referenced"
