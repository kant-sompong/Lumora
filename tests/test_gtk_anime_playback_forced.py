"""GTK playback windows: verified anime plays on ani.pm and nowhere else.

Every test drives the real :class:`lgl.movies_ui.MoviesWindow` play entry
points and the real ``lgl.anime_identity`` core; only the MAL and AniList
*network* adapters are replaced with in-memory fakes, so the identity statuses,
matching rules, AniList validation and cache are the production ones.  The
player is captured instead of opened, so the assertions are about the route the
window chose, the URL it built and the fallback chain it kept.
"""

from __future__ import annotations

import threading
import time

import pytest

gi = pytest.importorskip("gi")

try:
    gi.require_version("Gtk", "3.0")
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import Gtk
except (ValueError, ImportError):  # pragma: no cover - GTK 3 not installed
    pytest.skip("GTK 3 is not installed", allow_module_level=True)

pytest.importorskip("cairo")

from lgl import anime_identity, anime_map, anipm, mal, movies_ui, streaming  # noqa: E402
from lgl.anime_identity import MalMatch  # noqa: E402
from lgl.anime_map import PlayTarget  # noqa: E402
from lgl.tmdb import Episode, MediaDetails, MediaItem, Season  # noqa: E402

pytestmark = pytest.mark.skipif(
    not Gtk.init_check()[0], reason="no display for GTK"
)

TITLE = "Shingeki no Kyojin Season 3"
YEAR = "2018"
MAL_ID = 35760
ANILIST_ID = 99147
ANIME_DETAILS = {"original_language": "ja", "genres": [{"id": 16, "name": "Animation"}]}
WESTERN_DETAILS = {"original_language": "en", "genres": [{"id": 16, "name": "Animation"}]}

MAL_ENTRY = MalMatch(
    mal_id=MAL_ID,
    title=TITLE,
    media_type="tv",
    start_date="2018-07-23",
    episodes=12,
    alternative_titles=("Attack on Titan Season 3",),
)

#: A TMDB *film* entry, so the kind has to agree with MAL's media type.
MOVIE_TITLE = "Shingeki no Kyojin: Chronicle"
MOVIE_MAL_ID = 43608
MOVIE_ENTRY = MalMatch(
    mal_id=MOVIE_MAL_ID,
    title=MOVIE_TITLE,
    media_type="movie",
    start_date="2020-07-17",
    episodes=1,
)


def pump(seconds: float = 0.01) -> None:
    end = time.time() + seconds
    while time.time() < end:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        time.sleep(0.002)


def wait_for(predicate, seconds: float = 4.0) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        pump(0.02)
        if predicate():
            return True
    return False


class Store(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)

    def set(self, key, value):
        self[key] = value

    def save(self):
        return True


class FakeMal:
    def __init__(self, entries=(), *, error: str = "") -> None:
        self.entries = {entry.mal_id: entry for entry in entries}
        self.error = error
        self.searches: list[str] = []
        self.details_calls: list[int] = []

    def search(self, title: str, *, limit: int = 5) -> list[int]:
        if self.error:
            raise anime_identity.LookupUnavailable(self.error)
        self.searches.append(title)
        return list(self.entries)[:limit]

    def details(self, mal_id: int) -> MalMatch:
        if self.error:
            raise anime_identity.LookupUnavailable(self.error)
        self.details_calls.append(mal_id)
        entry = self.entries.get(mal_id)
        if entry is None:
            raise anime_identity.LookupUnavailable(f"no MAL entry {mal_id}")
        return entry


class FakeAniList:
    def __init__(self, links=None, mal_links=None, *, error: str = "") -> None:
        self.links = dict(links if links is not None else {MAL_ID: ANILIST_ID})
        self.mal_links = dict(mal_links if mal_links is not None else {ANILIST_ID: MAL_ID})
        self.error = error

    def anilist_for_mal(self, mal_id: int) -> int | None:
        if self.error:
            raise anime_identity.LookupUnavailable(self.error)
        return self.links.get(mal_id)

    def mal_for_anilist(self, anilist_id: int) -> int | None:
        if self.error:
            raise anime_identity.LookupUnavailable(self.error)
        return self.mal_links.get(anilist_id)


def install_core(
    monkeypatch,
    *,
    entries=(MAL_ENTRY,),
    links=None,
    mal_links=None,
    mal_error: str = "",
    anilist_error: str = "",
    crosswalk=(),
):
    """The real identity core, with its two network adapters faked."""
    mal_adapter = FakeMal(entries, error=mal_error)
    anilist_adapter = FakeAniList(links, mal_links, error=anilist_error)
    monkeypatch.setattr(
        anime_identity, "_DEFAULT_CACHE", anime_identity.IdentityCache()
    )
    monkeypatch.setattr(
        anime_identity, "MalApiLookup", lambda settings=None: mal_adapter
    )
    monkeypatch.setattr(
        anime_identity, "AniListGraphQLLookup", lambda *_a, **_k: anilist_adapter
    )
    monkeypatch.setattr(
        anime_identity, "crosswalk_anilist_ids", lambda _mal_id: tuple(crosswalk)
    )
    return mal_adapter, anilist_adapter


class StubTmdb:
    """The window's TMDB client: answers details documents, no network."""

    configured = True
    documents: dict = {}

    def __init__(self, *_args, **_kwargs):
        pass

    def get(self, path, params=None):
        return dict(self.documents.get(path, {}))

    def trending(self, kind="movie", **_kwargs):
        return []

    def popular(self, kind="movie", **_kwargs):
        return []

    def search(self, query, **_kwargs):
        return []


class Captured:
    """What the window did with the play instead of opening the player."""

    def __init__(self) -> None:
        self.players: list[dict] = []
        self.messages: list[tuple[str, str]] = []
        #: The keyword options each message was shown with, e.g. alternatives.
        self.message_options: list[dict] = []
        self.attempts: list[list[tuple[str, str]]] = []


@pytest.fixture
def window(monkeypatch):
    monkeypatch.setattr(movies_ui, "TmdbClient", StubTmdb)
    monkeypatch.setattr(movies_ui.MoviesWindow, "_load_home", lambda self: None)
    frame = Gtk.OffscreenWindow()
    settings = Store(
        {
            "network_timeout": 5,
            "streaming_provider": "cinesrc",
            # No anime provider is saved: ani.pm is the default for anime, and
            # the tests that check an explicit pick set it themselves.
            "anime_dub": False,
        }
    )
    media = movies_ui.MoviesWindow(frame, settings)
    frame.add(media)
    frame.show_all()
    pump()
    captured = Captured()
    media._open_player = lambda url, title, **kwargs: captured.players.append(
        {"url": url, "title": title, **kwargs}
    )
    def capture_message(title, message, **options):
        captured.messages.append((title, message))
        captured.message_options.append(options)

    media._open_player_message = capture_message
    media._play_attempts = lambda attempts, title: captured.attempts.append(attempts)
    media._record_playback = lambda *_a, **_k: None
    media._verify_current_attempt = lambda: None
    # Episode mapping is the shared mapping layer's job; tests that need a
    # target install one, and no test may hit the network for it.
    monkeypatch.setattr(anime_map, "tmdb_to_anilist", lambda *_a, **_k: None)
    media.captured = captured
    try:
        yield media
    finally:
        frame.destroy()
        pump(0.02)


def movie(tmdb_id=65942, title=MOVIE_TITLE, year="2020"):
    return MediaItem(kind="movie", tmdb_id=tmdb_id, title=title, year=year)


def play_movie(media, monkeypatch, *, details=None, **core):
    StubTmdb.documents = {"/movie/65942": dict(details or ANIME_DETAILS)}
    core.setdefault("entries", (MOVIE_ENTRY,))
    core.setdefault("links", {MOVIE_MAL_ID: ANILIST_ID})
    install_core(monkeypatch, **core)
    media._play(movie())
    assert wait_for(
        lambda: media.captured.players
        or media.captured.messages
        or media.captured.attempts
    ), "the play never finished"
    return media.captured


# -- TMDB plays ------------------------------------------------------------


def test_verified_anime_always_plays_on_ani_pm(window, monkeypatch):
    captured = play_movie(window, monkeypatch)
    assert not captured.messages
    assert len(captured.players) == 1
    player = captured.players[0]
    assert player["provider"] == streaming.ANI_PROVIDER
    assert player["attempts"][0] == (streaming.ANI_PROVIDER, player["url"])
    assert f"/embed/ani/{ANILIST_ID}/" in player["url"]
    assert "/embed/mal/" not in player["url"]
    assert str(MOVIE_MAL_ID) not in player["url"]


def test_the_selected_normal_provider_cannot_change_that(window, monkeypatch):
    window._settings.set("streaming_provider", "vidy")
    captured = play_movie(window, monkeypatch)
    assert captured.players[0]["provider"] == streaming.ANI_PROVIDER
    assert "vidy" not in captured.players[0]["url"]


def test_the_saved_anime_provider_never_preempts_ani_pm(window, monkeypatch):
    """A saved anime pick only orders the chain behind ani.pm."""
    window._settings.set("anime_streaming_provider", "toflix")
    captured = play_movie(window, monkeypatch)
    attempts = captured.players[0]["attempts"]
    assert attempts[0][0] == streaming.ANI_PROVIDER
    assert f"/embed/ani/{ANILIST_ID}/" in attempts[0][1]
    providers = [provider for provider, _url in attempts]
    assert providers.index("toflix") == 1, "the saved pick is offered next"


def test_try_another_provider_switches_away_from_ani_pm_for_this_session(
    window, monkeypatch
):
    captured = play_movie(window, monkeypatch)
    player = captured.players[0]
    assert player["provider"] == streaming.ANI_PROVIDER
    window._playback_attempts = list(player["attempts"])
    window._playback_index = 0
    window._playback_title = player["title"]

    window._try_another_provider()

    assert len(captured.players) == 2
    switched = captured.players[1]
    assert switched["provider"] == player["attempts"][1][0]
    assert switched["provider"] != streaming.ANI_PROVIDER
    assert switched["title"] == player["title"]
    assert window._settings.get("anime_streaming_provider", "") == ""


def test_a_manual_pick_switches_this_play_without_saving_it(window, monkeypatch):
    window._active_section = "anime"
    captured = play_movie(window, monkeypatch)
    assert captured.players[0]["provider"] == streaming.ANI_PROVIDER

    window._choose_provider("toflix")

    assert captured.attempts, "the picked provider never played"
    assert captured.attempts[-1][0][0] == "toflix"
    assert window._settings.get("anime_streaming_provider", "") == "", (
        "a manual switch must not overwrite the saved preference"
    )


def test_switching_back_to_ani_pm_replays_the_same_title(window, monkeypatch):
    window._active_section = "anime"
    captured = play_movie(window, monkeypatch)
    window._choose_provider("toflix")
    players_before = len(captured.players)

    window._choose_provider(streaming.ANI_PROVIDER)

    assert len(captured.players) == players_before + 1
    replayed = captured.players[-1]
    assert replayed["provider"] == streaming.ANI_PROVIDER
    assert replayed["url"] == captured.players[0]["url"]


def test_a_saved_anime_provider_survives_a_manual_switch(window, monkeypatch):
    window._active_section = "anime"
    window._settings.set("anime_streaming_provider", "toflix")
    play_movie(window, monkeypatch)

    window._choose_provider("cinesrc")

    assert window._settings.get("anime_streaming_provider") == "toflix"


def test_a_provider_pick_after_leaving_the_player_is_saved(window, monkeypatch):
    window._active_section = "anime"
    play_movie(window, monkeypatch)

    window.stop_playback()
    window._choose_provider("cinesrc")

    assert window._settings.get("anime_streaming_provider") == "cinesrc"


def test_an_ani_pm_url_failure_is_an_error_with_an_explicit_choice(
    window, monkeypatch
):
    def broken(*_args, **_kwargs):
        raise streaming.EmbedUrlError("ani.pm has no route for this entry")

    monkeypatch.setattr(streaming, "ani_url", broken)
    captured = play_movie(window, monkeypatch)

    assert captured.players == [], "ani.pm's failure played somewhere else"
    message = captured.messages[0][1]
    assert "ani.pm could not play this title" in message
    assert "Pick another provider" in message
    # Offered, never used automatically: an explicit click plays them.
    alternatives = captured.message_options[0]["alternatives"]
    assert alternatives
    assert all(provider != streaming.ANI_PROVIDER for provider, _url in alternatives)


def test_a_resolution_failure_offers_the_other_providers_explicitly(
    window, monkeypatch
):
    captured = play_movie(window, monkeypatch, links={})
    assert captured.players == []
    assert captured.message_options[0]["alternatives"], (
        "the owner was given no provider to try"
    )


def test_a_resolution_failure_is_an_error_and_plays_nothing(window, monkeypatch):
    captured = play_movie(window, monkeypatch, links={})
    assert captured.players == []
    assert captured.attempts == []
    assert captured.messages[0][1] == movies_ui.gtk_playback.UNRESOLVED


def test_an_ambiguous_anilist_resolution_is_an_error(window, monkeypatch):
    captured = play_movie(window, monkeypatch, links={}, crosswalk=(ANILIST_ID, 121051))
    assert captured.players == []
    assert captured.messages[0][1] == movies_ui.gtk_playback.AMBIGUOUS


def test_an_unverified_title_uses_the_selected_provider(window, monkeypatch):
    captured = play_movie(window, monkeypatch, entries=())
    assert captured.players == []
    assert captured.attempts
    assert captured.attempts[0][0][0] == "cinesrc"


def test_a_mal_lookup_failure_uses_the_selected_provider(window, monkeypatch):
    captured = play_movie(window, monkeypatch, mal_error="MAL unavailable")
    assert captured.players == []
    assert captured.messages == []
    assert captured.attempts[0][0][0] == "cinesrc"


def test_a_confirmed_non_anime_title_uses_the_selected_provider(window, monkeypatch):
    captured = play_movie(
        window, monkeypatch, entries=(), details=WESTERN_DETAILS
    )
    assert captured.players == []
    assert captured.attempts[0][0][0] == "cinesrc"


def test_ani_pm_alone_has_no_fallback_when_it_is_the_only_attempt(window, monkeypatch):
    captured = play_movie(window, monkeypatch)
    url = captured.players[0]["url"]
    window._playback_attempts = [(streaming.ANI_PROVIDER, url)]
    window._playback_index = 0
    window._playback_title = "One"
    assert window._advance_playback() is False
    window._try_another_provider()
    assert captured.messages
    assert "Every configured provider" in captured.messages[-1][1]


def test_a_tv_card_still_opens_details_instead_of_playing(window, monkeypatch):
    opened: list = []
    monkeypatch.setattr(window, "_open_details", opened.append)
    window._play(MediaItem(kind="tv", tmdb_id=65942, title=TITLE))
    assert len(opened) == 1
    assert window.captured.players == []


# -- episode mapping, after verification -----------------------------------


def episode_details() -> MediaDetails:
    return MediaDetails(
        kind="tv",
        tmdb_id=65942,
        title=TITLE,
        seasons=(
            Season(number=1, name="Season 1", episode_count=85),
            Season(number=2, name="Season 2", episode_count=25),
        ),
    )


def season_two(window) -> None:
    window._current_details = episode_details()
    window._season_cache = {
        2: Season(
            number=2,
            name="Season 2",
            episode_count=25,
            episodes=(
                Episode(season=1, number=26, title="Each One's Promise"),
                Episode(season=1, number=27, title="The Next One"),
            ),
        )
    }


def test_episode_playback_keeps_tmdb_numbers_and_next_episode(window, monkeypatch):
    StubTmdb.documents = {"/tv/65942": dict(ANIME_DETAILS)}
    install_core(monkeypatch)
    monkeypatch.setattr(
        anime_map, "tmdb_to_anilist",
        lambda *_a, **_k: PlayTarget(ANILIST_ID, 1, True, "season 2 starts the entry"),
    )
    season_two(window)
    window._play_episode(2, Episode(season=1, number=26, title="Each One's Promise"))
    assert wait_for(lambda: window.captured.players), "the episode never played"
    player = window.captured.players[0]
    assert player["provider"] == streaming.ANI_PROVIDER
    assert f"/embed/ani/{ANILIST_ID}/1/" in player["url"]
    assert window._playing_episode == (2, 26)
    following = window._next_episode()
    assert following is not None
    assert (following[0], following[1].number) == (2, 27)


def test_switching_providers_keeps_the_episode_and_the_next_episode(
    window, monkeypatch
):
    StubTmdb.documents = {"/tv/65942": dict(ANIME_DETAILS)}
    install_core(monkeypatch)
    monkeypatch.setattr(anime_map, "tmdb_to_anilist", lambda *_a, **_k: None)
    season_two(window)
    window._active_section = "anime"
    window._play_episode(2, Episode(season=1, number=26, title="Each One's Promise"))
    assert wait_for(lambda: window.captured.players), "the episode never played"
    assert window.captured.players[0]["provider"] == streaming.ANI_PROVIDER
    assert window._playing_episode == (2, 26)

    window._choose_provider("toflix")

    assert window.captured.attempts, "the picked provider never played"
    chain = window.captured.attempts[-1]
    assert chain[0][0] == "toflix"
    # The same season and episode, in TMDB numbers, all the way down.
    assert chain[0][1] == "https://toflix.co/embed/tv/65942/2/26"
    assert all("65942" in url for _provider, url in chain)
    assert window._playing_episode == (2, 26)
    following = window._next_episode()
    assert following is not None
    assert (following[0], following[1].number) == (2, 27)


def test_a_confident_mapping_offsets_the_played_episode(window, monkeypatch):
    StubTmdb.documents = {"/tv/65942": dict(ANIME_DETAILS)}
    install_core(monkeypatch)
    asked: list = []

    def mapped(tmdb_kind, tmdb_id, season, episode, *, search=None):
        asked.append((tmdb_kind, tmdb_id, season, episode, search))
        return PlayTarget(ANILIST_ID, 14, True, "alternate ordering")

    monkeypatch.setattr(anime_map, "tmdb_to_anilist", mapped)
    season_two(window)
    window._play_episode(2, Episode(season=1, number=26, title="Each One's Promise"))
    assert wait_for(lambda: window.captured.players), "the episode never played"
    assert f"/embed/ani/{ANILIST_ID}/14/" in window.captured.players[0]["url"]
    assert asked[0][:4] == ("tv", 65942, 2, 26)
    assert asked[0][4] is not None and asked[0][4]("Shingeki no Kyojin") is None
    assert window._playing_episode == (2, 26)


def test_without_a_confident_mapping_the_episode_is_unchanged(window, monkeypatch):
    StubTmdb.documents = {"/tv/65942": dict(ANIME_DETAILS)}
    install_core(monkeypatch)
    season_two(window)
    window._play_episode(2, Episode(season=1, number=26, title="Each One's Promise"))
    assert wait_for(lambda: window.captured.players), "the episode never played"
    assert f"/embed/ani/{ANILIST_ID}/26/" in window.captured.players[0]["url"]
    assert window._playing_episode == (2, 26)


def test_resume_survives_a_second_episode_play(window, monkeypatch):
    StubTmdb.documents = {"/tv/65942": dict(ANIME_DETAILS)}
    install_core(monkeypatch)
    window._current_details = episode_details()
    window._play_episode(1, Episode(season=1, number=1, title="Start"))
    assert wait_for(lambda: window.captured.players)
    assert window._playing_episode == (1, 1)
    window._play_episode(1, Episode(season=1, number=2, title="Second"))
    assert wait_for(lambda: len(window.captured.players) == 2)
    assert window._playing_episode == (1, 2)


# -- anime detail pages: MAL and ani.pm/AniList ----------------------------


def mal_item(**kwargs):
    base = dict(id=MAL_ID, title=TITLE, episodes=12, year=YEAR)
    base.update(kwargs)
    return mal.AnimeItem(**base)


def ani_page(**kwargs):
    base = dict(anilist_id=ANILIST_ID, mal_id=0, title=TITLE, episodes=12, year=YEAR)
    base.update(kwargs)
    return anipm.AniTitle(**base)


def test_a_verified_mal_page_plays_ani_pm(window, monkeypatch):
    install_core(monkeypatch)
    window._watch_anime(mal_item())
    assert wait_for(lambda: window.captured.players or window.captured.messages)
    player = window.captured.players[0]
    assert player["provider"] == streaming.ANI_PROVIDER
    assert f"/embed/ani/{ANILIST_ID}/1/" in player["url"]
    assert player["attempts"][0][0] == streaming.ANI_PROVIDER
    assert window._playing_episode == (1, 1)


def test_an_unverified_mal_page_uses_the_selected_normal_provider(window, monkeypatch):
    install_core(monkeypatch, entries=())
    window._watch_anime(mal_item(title="Not On MAL"))
    assert wait_for(lambda: "TMDB" in window._anime_status.get_text())
    assert window.captured.players == []
    assert window.captured.messages == []


def test_an_unavailable_mal_lookup_uses_the_selected_normal_provider(window, monkeypatch):
    install_core(monkeypatch, mal_error="MAL unavailable")
    window._watch_anime(mal_item())
    assert wait_for(lambda: "TMDB" in window._anime_status.get_text())
    assert window.captured.players == []
    assert window.captured.messages == []


def test_an_anilist_page_is_verified_through_its_mal_link(window, monkeypatch):
    mal_adapter, _anilist = install_core(monkeypatch)
    window._watch_anime(ani_page())
    assert wait_for(lambda: window.captured.players or window.captured.messages)
    assert f"/embed/ani/{ANILIST_ID}/" in window.captured.players[0]["url"]
    # The page's AniList id went to AniList's idMal lookup, never to MAL as a
    # MAL id: the only MAL entry asked for is the link AniList reported.
    assert 99147 not in mal_adapter.details_calls


def test_an_anilist_page_alone_does_not_bypass_mal_verification(window, monkeypatch):
    install_core(monkeypatch, mal_links={})
    window._watch_anime(ani_page())
    assert wait_for(lambda: "TMDB" in window._anime_status.get_text())
    assert window.captured.players == []


def test_an_ani_pm_build_failure_offers_another_provider(window, monkeypatch):
    """A resolved ani.pm play that cannot be built leaves the choice explicit."""
    install_core(monkeypatch)
    called: list = []
    monkeypatch.setattr(
        window, "_watch_anime_unverified", lambda *args: called.append(args)
    )

    def broken(*_args, **_kwargs):
        raise streaming.EmbedUrlError("ani.pm has no route for this entry")

    monkeypatch.setattr(anipm, "playback_url", broken)
    item = mal_item()
    window._watch_anime(item)
    assert wait_for(lambda: window.captured.messages)
    assert window.captured.players == []
    options = window.captured.message_options[-1]
    label, action = options["alternative_action"]
    assert label == "Try another provider"
    action()
    assert called and called[0][0] is item


def test_a_verified_movies_session_offers_ani_pm_and_anime_routes(window, monkeypatch):
    """Opened from Movies, a verified anime still decides the header menu."""
    window._active_section = "movies"
    play_movie(window, monkeypatch)
    assert window._forced_playback
    offered = window._provider_choices()
    assert streaming.ANI_PROVIDER in offered
    assert set(offered) == {streaming.ANI_PROVIDER, *streaming.ANILIST_ANIME_PROVIDERS}
    assert "cinesrc" not in offered
    labels = [
        child.get_label() for child in window._provider_menu.get_children()
        if isinstance(child, Gtk.RadioMenuItem)
    ]
    assert len(labels) == len(offered)
    assert streaming.LABELS["cinesrc"] not in labels
    assert window._provider_label.get_text() == streaming.LABELS[streaming.ANI_PROVIDER]


def test_a_verified_movies_session_switches_and_restores_the_mapped_episode(
    window, monkeypatch
):
    install_core(monkeypatch)
    window._active_section = "movies"
    route = PlayTarget(ANILIST_ID, 12, True, "mapped episode")
    window._play_forced_ani(movie(), None, None, route)
    ani_url = window.captured.players[0]["url"]
    assert f"/embed/ani/{ANILIST_ID}/12/" in ani_url
    assert window._forced_playback["provider"] == streaming.ANI_PROVIDER
    assert window._provider_label.get_text() == streaming.LABELS[streaming.ANI_PROVIDER]

    window._choose_provider("toflix")
    assert window.captured.attempts[-1][0][0] == "toflix"
    assert window._forced_playback["provider"] == "toflix", (
        "the header button did not follow the session's actual provider"
    )
    assert window._provider_label.get_text() == streaming.LABELS["toflix"]
    assert window._settings.get("anime_streaming_provider", "") == "", (
        "a session switch overwrote the saved anime preference"
    )
    assert streaming.ANI_PROVIDER in window._provider_choices(), (
        "ani.pm was no longer offered, so the session could not switch back"
    )

    window._choose_provider(streaming.ANI_PROVIDER)
    assert window.captured.players[-1]["url"] == ani_url, (
        "the mapped AniList episode was lost"
    )
    assert window._forced_playback["provider"] == streaming.ANI_PROVIDER


def test_a_failed_play_of_another_anime_clears_the_previous_session(
    window, monkeypatch
):
    install_core(monkeypatch)
    window._active_section = "movies"
    window._play_forced_ani(
        movie(), None, None, PlayTarget(ANILIST_ID, 1, True, "first"),
    )
    assert window._forced_playback

    def broken(*_args, **_kwargs):
        raise streaming.EmbedUrlError("no route for this entry")

    monkeypatch.setattr(streaming, "ani_url", broken)
    window._play_forced_ani(
        movie(tmdb_id=2, title="Another One"), None, None,
        PlayTarget(99148, 1, True, "second"),
    )

    assert window.captured.messages, "the failure was not reported"
    assert window._forced_playback is None, "anime A's session outlived anime B's failure"
    assert window._provider_choices() == streaming.PROVIDERS
    assert window._provider_label.get_text() == streaming.LABELS["cinesrc"]


def test_a_movie_provider_preference_changes_independently_of_a_session(
    window, monkeypatch
):
    window._active_section = "movies"
    play_movie(window, monkeypatch)
    players = len(window.captured.players)

    window._choose_provider("velarium")

    assert window._settings.get("streaming_provider") == "velarium"
    assert window._settings.get("anime_streaming_provider", "") == "", (
        "a movie preference change rewrote the anime preference"
    )
    assert len(window.captured.players) == players, (
        "a movie preference change replayed the anime"
    )
    assert window._forced_playback["provider"] == streaming.ANI_PROVIDER
    assert window._provider_label.get_text() == streaming.LABELS[streaming.ANI_PROVIDER]


def test_the_anime_provider_menu_only_offers_anime_routes(window):
    window._active_section = "anime"
    window._refresh_provider_button()
    offered = window._provider_choices()
    assert streaming.ANI_PROVIDER in offered
    assert set(offered) == {streaming.ANI_PROVIDER, *streaming.ANILIST_ANIME_PROVIDERS}
    labels = [
        child.get_label() for child in window._provider_menu.get_children()
        if isinstance(child, Gtk.RadioMenuItem)
    ]
    assert len(labels) == len(offered)
    assert streaming.LABELS["cinesrc"] not in labels

    window._active_section = "movies"
    assert window._provider_choices() == streaming.PROVIDERS


def test_a_mal_page_resolution_failure_errors(window, monkeypatch):
    install_core(monkeypatch, links={})
    window._watch_anime(mal_item())
    assert wait_for(lambda: window.captured.messages or window.captured.players)
    assert window.captured.players == []
    assert window.captured.messages[0][1] == movies_ui.gtk_playback.UNRESOLVED


def test_an_episode_button_cannot_bypass_mal_verification(window, monkeypatch):
    install_core(monkeypatch)
    window._on_ani_episode_play(None, ani_page(), 3, "dub")
    assert wait_for(lambda: window.captured.players or window.captured.messages)
    player = window.captured.players[0]
    assert f"/embed/ani/{ANILIST_ID}/3/dub" in player["url"]
    assert player["attempts"][0][0] == streaming.ANI_PROVIDER


def test_the_mal_page_resumes_at_the_next_episode(window, monkeypatch):
    install_core(monkeypatch)
    item = mal_item()
    window._playing_episode = (1, 3)
    window._anime_playing_key = item.key
    window._watch_anime(item)
    assert wait_for(lambda: window.captured.players or window.captured.messages)
    assert f"/embed/ani/{ANILIST_ID}/4/" in window.captured.players[0]["url"]
    assert window._playing_episode == (1, 4)


# -- hard-sub / hard-dub tracks are never substituted ----------------------


def hard_episode(number=3, **embeds):
    return anipm.AniEpisode(
        number=number,
        title=f"Episode {number}",
        embeds=embeds,
        default_embed="",
    )


def test_a_hard_track_plays_the_api_embed(window, monkeypatch):
    install_core(monkeypatch)
    window._current_ani_episodes = [
        hard_episode(3, dubHard="https://ani.pm/embed/hard-dub"),
    ]
    window._on_ani_episode_play(None, ani_page(), 3, "dubHard")
    assert wait_for(lambda: window.captured.players or window.captured.messages)
    assert window.captured.messages == []
    player = window.captured.players[0]
    assert player["url"] == "https://ani.pm/embed/hard-dub"
    assert player["provider"] == streaming.ANI_PROVIDER
    # Not substituted for the ordinary track.
    assert not player["url"].endswith("/sub")
    assert not player["url"].endswith("/dub")


def test_a_missing_hard_track_is_a_clear_error_not_a_substitution(window, monkeypatch):
    install_core(monkeypatch)
    window._current_ani_episodes = [
        hard_episode(3, sub="https://ani.pm/embed/sub", dub="https://ani.pm/embed/dub"),
    ]
    window._on_ani_episode_play(None, ani_page(), 3, "subHard")
    assert wait_for(lambda: window.captured.messages or window.captured.players)
    assert window.captured.players == []
    message = window.captured.messages[0][1]
    assert "Sub·Hard" in message
    assert "Pick Sub or Dub" in message


def test_a_sub_track_still_plays_without_a_page_embed(window, monkeypatch):
    install_core(monkeypatch)
    window._on_ani_episode_play(None, ani_page(), 3, "sub")
    assert wait_for(lambda: window.captured.players or window.captured.messages)
    assert f"/embed/ani/{ANILIST_ID}/3/sub" in window.captured.players[0]["url"]


# -- stale routing workers cannot play or overwrite ------------------------


def test_an_older_tmdb_route_cannot_overwrite_a_newer_play(window, monkeypatch):
    entered_first = threading.Event()
    release_first = threading.Event()
    order: list = []

    def decide(kind, tmdb_id, **kwargs):
        order.append(tmdb_id)
        if tmdb_id == 1:
            entered_first.set()
            release_first.wait(3)
        return movies_ui.gtk_playback.PlaybackRoute(
            movies_ui.gtk_playback.MODE_ANI, 1000 + tmdb_id, 1
        )

    monkeypatch.setattr(movies_ui.gtk_playback, "decide_playback", decide)
    window._play(movie(tmdb_id=1, title="First"))
    assert entered_first.wait(3)
    window._play(movie(tmdb_id=2, title="Second"))
    assert wait_for(lambda: window.captured.players), "the newer play never finished"
    release_first.set()
    time.sleep(0.2)
    pump(0.2)
    urls = [player["url"] for player in window.captured.players]
    assert len(urls) == 1, urls
    assert "/embed/ani/1002/" in urls[0]
    assert order == [1, 2]


def test_an_older_anime_page_route_cannot_overwrite_a_newer_play(window, monkeypatch):
    entered_first = threading.Event()
    release_first = threading.Event()

    def decide_anime(**kwargs):
        if kwargs["title"] == "First":
            entered_first.set()
            release_first.wait(3)
            return movies_ui.gtk_playback.PlaybackRoute(
                movies_ui.gtk_playback.MODE_ANI, 1111, 1
            )
        return movies_ui.gtk_playback.PlaybackRoute(
            movies_ui.gtk_playback.MODE_ANI, 2222, 1
        )

    monkeypatch.setattr(movies_ui.gtk_playback, "decide_anime", decide_anime)
    window._watch_anime(ani_page(title="First"))
    assert entered_first.wait(3)
    window._watch_anime(ani_page(title="Second"))
    assert wait_for(lambda: window.captured.players), "the newer play never finished"
    release_first.set()
    time.sleep(0.2)
    pump(0.2)
    urls = [player["url"] for player in window.captured.players]
    assert len(urls) == 1, urls
    assert "/embed/ani/2222/" in urls[0]
