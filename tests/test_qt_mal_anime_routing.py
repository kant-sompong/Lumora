"""Forced anipm.pk routing for MAL-verified anime, in the Qt frontend.

Identity is answered by the shared anime core; here it is replaced with a stub
so these tests are about routing, not about the mapping table.  Everything runs
offscreen with a stub view: no Qt WebEngine, no network.
"""

from __future__ import annotations

import os
import sys
import threading
import time

import pytest

pytest.importorskip("PySide6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QUrl, Signal  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QHBoxLayout,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from lgl import (  # noqa: E402
    anime_identity,
    anime_map,
    qt_anime_routing,
    qt_media,
    qt_player,
    streaming,
)
from lgl.qt_widgets import Toast  # noqa: E402
from lgl.settings import Settings  # noqa: E402
from lgl.tmdb import Episode  # noqa: E402

ANIME = qt_media.Card(
    key="anime:21", title="One Piece", year="1999", kind="anime", mal_id=21
)
MOVIE = qt_media.Card(
    key="movie:603", title="The Matrix", year="1999", kind="movie", tmdb_id=603
)
TV = qt_media.Card(
    key="tv:1399", title="Game of Thrones", year="2011", kind="tv", tmdb_id=1399
)

VERIFIED = qt_anime_routing.Identity(
    is_anime=True, verified=True, playable_anilist_id=16498, reason="mapping"
)
UNVERIFIED = qt_anime_routing.Identity(
    is_anime=True, verified=False, reason="heuristic"
)
NOT_ANIME = qt_anime_routing.Identity(is_anime=False, verified=False)
ANIME_URL = "https://anipm.pk/embed/ani/16498/1/sub"

NO_ANILIST = qt_anime_routing.Identity(
    is_anime=True, verified=True, playable_anilist_id=0,
    resolution_failed=True, reason="ambiguous",
)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _close_windows(app):
    """Close each harness so late background deliveries reach nothing."""
    yield
    for widget in app.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    app.processEvents()


def pump(app, seconds: float = 0.05) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def settle(app, predicate=None, timeout: float = 4.0) -> None:
    """Pump until a worker's answer has been applied; identity runs off-thread."""
    end = time.time() + timeout
    while time.time() < end:
        pump(app)
        if predicate is not None and predicate():
            break
    pump(app, 0.02)


class StubPage(QObject):
    fullScreenRequested = Signal(object)

    def triggerAction(self, _action) -> None:  # noqa: N802 - Qt's name
        pass


class StubView(QWidget):
    """Records loads instead of touching the network."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._page = StubPage()
        self.loaded: list[str] = []

    def page(self) -> StubPage:
        return self._page

    def setUrl(self, url) -> None:  # noqa: N802 - Qt's name
        self.loaded.append(url.toString() if isinstance(url, QUrl) else str(url))

    def stop(self) -> None:
        pass


class Harness:
    """The real Media page inside a window shaped like the app."""

    def __init__(self, app: QApplication, settings=None, tmp_path=None) -> None:
        self.window = QMainWindow()
        root = QWidget()
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        column = QVBoxLayout()
        column.addWidget(self.stack)
        shell.addLayout(column)
        self.window.setCentralWidget(root)
        self.settings = Settings(
            (tmp_path / "settings.json") if tmp_path is not None else None,
            keyring=False,
        )
        for key, value in (settings or {}).items():
            self.settings.set(key, value)
        self.page = qt_media.MediaPage(
            settings=self.settings, player_factory=self._stub_player
        )
        self.window.toast = Toast(self.window)
        self.stack.addWidget(self.page)
        self.window.resize(1200, 780)
        self.window.show()
        pump(app)
        # These tests are about provider routing for MAL-keyed cards; the TMDB
        # identity path has its own suite (tests/test_qt_anime_routing.py).  Set
        # after show(), because the page builds its TMDB client on first load.
        self.page._tmdb = None

    @staticmethod
    def _stub_player(parent: QWidget) -> qt_player.EmbeddedPlayer:
        return qt_player.EmbeddedPlayer(parent, view_factory=StubView)

    @property
    def loaded(self) -> list[str]:
        return self.page.player.view.loaded

    @property
    def toast_text(self) -> str:
        return self.window.toast._label.text()


def use_identity(monkeypatch, answer) -> None:
    """Answer both identity entry points the page calls with one verdict.

    The real adapter is exercised against a fake ``lgl.anime_identity`` in the
    unit tests below; the page tests only need the seam.
    """

    def resolver(*_args, **_kwargs):
        if isinstance(answer, Exception):
            return None
        return answer

    monkeypatch.setattr(qt_anime_routing, "identify_mal", resolver)
    monkeypatch.setattr(qt_anime_routing, "identify_anilist", resolver)
    monkeypatch.setattr(qt_anime_routing, "identify_tmdb", resolver)


class Mal:
    """A MyAnimeList lookup: one matching entry, no match, or an outage."""

    def __init__(self, mal_id: int = 21, *, match: bool = True,
                 raises: bool = False) -> None:
        self.mal_id = mal_id
        self.match = match
        self.raises = raises
        self.calls: list[tuple] = []

    def search(self, title: str, *, limit: int = 10) -> list[int]:
        self.calls.append(("search", title))
        if self.raises:
            raise anime_identity.LookupUnavailable("MAL is offline")
        return [self.mal_id] if self.match else []

    def details(self, mal_id: int):
        self.calls.append(("details", mal_id))
        if self.raises:
            raise anime_identity.LookupUnavailable("MAL is offline")
        return anime_identity.MalMatch(
            mal_id=mal_id, title="One Piece", media_type="tv",
            start_date="1999-10-20", episodes=1000,
        )


class AniListLookup:
    """AniList's own link fields: one link, no link, or an outage."""

    def __init__(self, linked: int | None = 16498, *, back: int | None = None,
                 raises: bool = False) -> None:
        self.linked = linked
        self.back = back
        self.raises = raises
        self.calls: list[tuple] = []

    def anilist_for_mal(self, mal_id: int):
        self.calls.append(("anilist_for_mal", mal_id))
        if self.raises:
            raise anime_identity.LookupUnavailable("AniList is offline")
        return self.linked

    def mal_for_anilist(self, anilist_id: int):
        self.calls.append(("mal_for_anilist", anilist_id))
        if self.raises:
            raise anime_identity.LookupUnavailable("AniList is offline")
        return self.back


def fresh_cache() -> "anime_identity.IdentityCache":
    """A memory-only cache, so no test sees another test's answer."""
    return anime_identity.IdentityCache()


def no_crosswalk(_mal_id: int) -> tuple:
    return ()


# -- against the real shared core -----------------------------------------


def test_an_unavailable_core_means_cannot_say(monkeypatch):
    """A build without the shared module keeps today's provider path."""
    monkeypatch.setattr(
        qt_anime_routing, "IDENTITY_MODULE", "lgl.anime_identity_absent"
    )
    monkeypatch.delitem(sys.modules, "lgl.anime_identity_absent", raising=False)
    assert qt_anime_routing.identify_mal(21, title="One Piece") is None
    assert qt_anime_routing.plan(21, "One Piece", provider="cinesrc").forced is False


def test_the_real_core_verifies_a_mal_card():
    identity = qt_anime_routing.identify_mal(
        21, title="One Piece",
        mal=Mal(), anilist=AniListLookup(), crosswalk=no_crosswalk,
        cache=fresh_cache(),
    )
    assert identity.verified is True
    assert identity.resolution_failed is False
    assert identity.playable_anilist_id == 16498
    assert identity.anilist_id == 16498, "the id anipm.pk gets is an AniList id"


def test_the_real_core_without_a_playable_resolution_is_a_resolution_failure():
    identity = qt_anime_routing.identify_mal(
        21, title="One Piece",
        mal=Mal(), anilist=AniListLookup(linked=None), crosswalk=no_crosswalk,
        cache=fresh_cache(),
    )
    # The core reports a resolution failure as not verified, flagged instead.
    assert identity.verified is False
    assert identity.resolution_failed is True
    assert identity.playable_anilist_id == 0
    assert identity.anilist_id == 0, "a MAL id is not a standby AniList id"


def test_the_real_core_reports_an_unavailable_lookup_as_unverified():
    identity = qt_anime_routing.identify_mal(
        21, title="One Piece",
        mal=Mal(raises=True), anilist=AniListLookup(), crosswalk=no_crosswalk,
        cache=fresh_cache(),
    )
    assert identity.verified is False
    assert identity.resolution_failed is False
    assert qt_anime_routing.plan(
        21, "One Piece", provider="cinesrc", identity=None,
    ).forced is False


def test_the_real_core_does_not_verify_a_title_it_cannot_match():
    identity = qt_anime_routing.identify_tmdb(
        "tv", 1396, title="Breaking Bad",
        mal=Mal(match=False), anilist=AniListLookup(), crosswalk=no_crosswalk,
        cache=fresh_cache(),
    )
    assert identity.verified is False
    assert identity.playable_anilist_id == 0


def test_the_adapter_passes_settings_to_the_real_core(monkeypatch):
    seen = {}

    def identify(**kwargs):
        seen.update(kwargs)
        return anime_identity.AnimeIdentity()

    monkeypatch.setattr(anime_identity, "identify", identify)
    settings = object()
    qt_anime_routing.identify_mal(21, title="One Piece", settings=settings)
    assert seen["settings"] is settings
    assert seen["mal_id"] == 21


def test_a_raising_shared_lookup_is_not_verified_anime(monkeypatch):
    def identify(**_kwargs):
        raise RuntimeError("MyAnimeList is down")

    monkeypatch.setattr(anime_identity, "identify", identify)
    assert qt_anime_routing.identify_mal(21, title="One Piece") is None
    assert qt_anime_routing.plan(21, "One Piece", provider="cinesrc").forced is False


def test_a_mal_id_is_never_sent_to_identify_tmdb(monkeypatch):
    calls: list[tuple] = []

    def identify_tmdb(*args, **_kwargs):
        calls.append(args)
        return anime_identity.AnimeIdentity()

    monkeypatch.setattr(anime_identity, "identify_tmdb", identify_tmdb)
    assert qt_anime_routing.plan(21, "One Piece", provider="cinesrc").forced is False
    assert calls == [], "a MAL id must not be handed to identify_tmdb"


def test_an_anilist_id_is_never_sent_as_a_mal_id(monkeypatch):
    seen: list[dict] = []

    def identify(**kwargs):
        seen.append(kwargs)
        return anime_identity.AnimeIdentity()

    monkeypatch.setattr(anime_identity, "identify", identify)
    qt_anime_routing.identify_anilist(999, title="One Piece")
    assert seen[0]["anilist_id"] == 999
    assert seen[0].get("mal_id") is None, "no MAL id is invented for this page"


def test_a_missing_mal_client_id_keeps_the_selected_provider(app, monkeypatch, tmp_path):
    """The real core's lookup failure is UNVERIFIED: the provider keeps the play."""
    from lgl import mal

    monkeypatch.setattr(mal, "load_client_id", lambda **_kwargs: "")
    harness = Harness(app, {"anime_streaming_provider": "ani"}, tmp_path)
    harness.page._play_anime(ANIME)
    settle(app, lambda: harness.loaded or harness.toast_text)
    assert harness.loaded == ["https://anipm.pk/embed/mal/21/1/sub"], (
        "no MAL key means no verification, so the anime provider plays as before"
    )
    assert harness.page.provider_button.isEnabled() is True


def test_a_shared_answer_without_an_anilist_id_is_never_forced():
    """The MAL id is not a stand-in for the AniList id."""
    answer = qt_anime_routing.Identity(
        is_anime=True, verified=True, playable_anilist_id=0,
    )
    route = qt_anime_routing.plan(21, "One Piece", provider="ani", identity=lambda _m, **_k: answer)
    assert route.forced is True
    assert route.anilist_id == 0
    assert route.provider == ""


def test_the_embed_url_is_built_from_an_anilist_id_only():
    url = qt_anime_routing.embed_url(16498, 1, language="sub")
    assert url == "https://anipm.pk/embed/ani/16498/1/sub"
    assert "/embed/mal/" not in url


# -- the page: verified anime ---------------------------------------------


def test_a_verified_anime_plays_on_ani_pm(app, monkeypatch, tmp_path):
    use_identity(monkeypatch, VERIFIED)
    harness = Harness(app, tmp_path=tmp_path)
    harness.page._play_anime(ANIME)
    settle(app, lambda: harness.loaded or harness.toast_text)
    assert harness.loaded == ["https://anipm.pk/embed/ani/16498/1/sub"]


def test_a_saved_anime_provider_never_preempts_ani_pm(app, monkeypatch, tmp_path):
    """anipm.pk is the first provider whatever anime preference is saved."""
    use_identity(monkeypatch, VERIFIED)
    harness = Harness(
        app,
        {"anime_streaming_provider": "toflix", "streaming_provider": "cinesrc"},
        tmp_path,
    )
    harness.page._play_anime(ANIME)
    settle(app, lambda: harness.loaded or harness.toast_text)
    assert harness.loaded == [ANIME_URL]
    assert harness.settings.get("anime_streaming_provider") == "toflix", (
        "routing a verified anime must not rewrite the saved preference"
    )


def test_the_provider_menu_switches_a_verified_anime_for_this_session_only(
    app, monkeypatch, tmp_path
):
    use_identity(monkeypatch, VERIFIED)
    harness = Harness(app, {"anime_streaming_provider": "toflix"}, tmp_path)
    harness.page._play_anime(ANIME)
    settle(app, lambda: harness.loaded == [ANIME_URL])

    harness.page._choose_provider("vidy")
    settle(app, lambda: len(harness.loaded) > 1)

    assert "vidy" in harness.loaded[-1]
    assert "16498" in harness.loaded[-1], (
        "the verified AniList entry is kept, not searched for again"
    )
    assert harness.settings.get("anime_streaming_provider") == "toflix", (
        "a session switch must not overwrite the saved preference"
    )


def test_switching_back_to_ani_pm_replays_the_verified_entry(
    app, monkeypatch, tmp_path
):
    use_identity(monkeypatch, VERIFIED)
    harness = Harness(app, {"anime_streaming_provider": "toflix"}, tmp_path)
    harness.page._play_anime(ANIME)
    settle(app, lambda: harness.loaded == [ANIME_URL])

    harness.page._choose_provider("vidy")
    settle(app, lambda: len(harness.loaded) > 1)
    harness.page._choose_provider(streaming.ANI_PROVIDER)
    settle(app, lambda: harness.loaded[-1] == ANIME_URL)

    assert harness.loaded[-1] == ANIME_URL
    assert harness.settings.get("anime_streaming_provider") == "toflix"


def test_a_provider_pick_after_leaving_the_player_is_saved(
    app, monkeypatch, tmp_path
):
    use_identity(monkeypatch, VERIFIED)
    harness = Harness(app, tmp_path=tmp_path)
    harness.page._play_anime(ANIME)
    settle(app, lambda: harness.loaded == [ANIME_URL])

    harness.page._leave_player()
    harness.page._section = "anime"
    harness.page._choose_provider("toflix")

    assert harness.settings.get("anime_streaming_provider") == "toflix"


def test_the_selected_provider_does_not_affect_verified_anime(app, monkeypatch, tmp_path):
    use_identity(monkeypatch, VERIFIED)
    harness = Harness(app, tmp_path=tmp_path)
    harness.page._choose_provider("framextv")
    harness.page._play_anime(ANIME)
    settle(app, lambda: harness.loaded or harness.toast_text)
    assert harness.loaded == ["https://anipm.pk/embed/ani/16498/1/sub"]


def test_a_verified_anime_never_gets_the_mal_route(app, monkeypatch, tmp_path):
    use_identity(monkeypatch, VERIFIED)
    harness = Harness(app, tmp_path=tmp_path)
    harness.page._play_anime(ANIME)
    settle(app, lambda: harness.loaded or harness.toast_text)
    url = harness.loaded[-1]
    assert "/embed/mal/" not in url
    assert "/21/" not in url


def test_the_provider_can_be_changed_while_verified_anime_plays(app, monkeypatch, tmp_path):
    """anipm.pk is the first provider for verified anime, not a lock on the menu."""
    use_identity(monkeypatch, VERIFIED)
    harness = Harness(app, {"anime_streaming_provider": "toflix"}, tmp_path)
    harness.page._play_anime(ANIME)
    settle(app, lambda: harness.loaded or harness.toast_text)
    assert harness.page.provider_button.isEnabled() is True

    harness.page._choose_provider("vidy")
    pump(app)
    assert harness.page.provider_button.isEnabled() is True
    assert harness.toast_text == ""
    assert "vidy" in harness.loaded[-1], "the switch never replayed the title"


def test_leaving_the_player_gives_the_provider_menu_back(app, monkeypatch, tmp_path):
    use_identity(monkeypatch, VERIFIED)
    harness = Harness(app, tmp_path=tmp_path)
    harness.page._play_anime(ANIME)
    settle(app, lambda: harness.loaded or harness.toast_text)
    harness.page._leave_player()
    pump(app)
    assert harness.page.provider_button.isEnabled() is True


def test_another_play_ends_the_forced_session(app, monkeypatch, tmp_path):
    use_identity(monkeypatch, VERIFIED)
    harness = Harness(app, tmp_path=tmp_path)
    harness.page._play_anime(ANIME)
    settle(app, lambda: harness.loaded or harness.toast_text)
    # The movie is not verified: its TMDB route keeps the normal provider.
    monkeypatch.setattr(
        qt_anime_routing, "identify_tmdb", lambda *_a, **_k: UNVERIFIED
    )
    harness.page._play_tmdb(MOVIE)
    for _ in range(100):
        pump(app)
        if harness.loaded and harness.loaded[-1] != ANIME_URL:
            break
    assert harness.page.provider_button.isEnabled() is True
    assert harness.loaded[-1].startswith("https://cinesrc")


# -- the page: a verified anime without an AniList id ---------------------


def test_a_verified_anime_without_an_anilist_id_shows_an_error(app, monkeypatch, tmp_path):
    use_identity(monkeypatch, NO_ANILIST)
    harness = Harness(app, tmp_path=tmp_path)
    harness.page._play_anime(ANIME)
    settle(app, lambda: harness.loaded or harness.toast_text)
    assert harness.loaded == []
    assert harness.window.toast.isVisible() is True
    assert "verified anime" in harness.toast_text
    assert "AniList" in harness.toast_text


def test_a_failed_resolution_does_not_fall_back_to_another_provider(app, monkeypatch, tmp_path):
    use_identity(monkeypatch, NO_ANILIST)
    harness = Harness(app, {"anime_streaming_provider": "cinesrc", "streaming_provider": "cinesrc"}, tmp_path)
    harness.page._play_anime(ANIME)
    settle(app, lambda: harness.loaded or harness.toast_text)
    assert harness.loaded == [], "the title was played somewhere else"
    assert all("cinesrc" not in url for url in harness.loaded)


def test_a_failing_shared_lookup_does_not_force_ani_pm(app, monkeypatch, tmp_path):
    use_identity(monkeypatch, RuntimeError("MyAnimeList is down"))
    harness = Harness(app, tmp_path=tmp_path)
    harness.page._play_anime(ANIME)
    settle(app, lambda: harness.loaded or harness.toast_text)
    assert harness.loaded == ["https://anipm.pk/embed/mal/21/1/sub"]


# -- the page: unverified and non-anime -----------------------------------


def test_an_unverified_anime_keeps_the_selected_provider(app, monkeypatch, tmp_path):
    use_identity(monkeypatch, UNVERIFIED)
    harness = Harness(app, {"anime_streaming_provider": "ani"}, tmp_path)
    harness.page._play_anime(ANIME)
    settle(app, lambda: harness.loaded or harness.toast_text)
    assert harness.loaded == ["https://anipm.pk/embed/mal/21/1/sub"]
    assert harness.page.provider_button.isEnabled() is True


def test_a_non_anime_movie_keeps_the_normal_provider(app, monkeypatch, tmp_path):
    use_identity(monkeypatch, NOT_ANIME)
    harness = Harness(app, {"streaming_provider": "cinesrc"}, tmp_path)
    harness.page._play_tmdb(MOVIE)
    for _ in range(100):
        pump(app)
        if harness.loaded:
            break
    assert harness.loaded[-1].startswith("https://cinesrc")
    assert "603" in harness.loaded[-1]


def test_a_tmdb_episode_still_plays_and_keeps_its_numbers(app, monkeypatch, tmp_path):
    use_identity(monkeypatch, NOT_ANIME)
    harness = Harness(app, {"streaming_provider": "cinesrc"}, tmp_path)
    episode = type("Episode", (), {"number": 3, "title": "Lord Snow"})()
    harness.page._play_tmdb(TV, 1, episode)
    for _ in range(100):
        pump(app)
        if harness.loaded and harness.page._playing_episode:
            break
    assert harness.page._playing_episode == (1, 3)
    assert "1" in harness.loaded[-1] and "3" in harness.loaded[-1]
    assert harness.page.player.next_episode.isVisible() in (True, False)


def test_a_forced_anime_playback_does_not_inherit_tv_progress(app, monkeypatch, tmp_path):
    use_identity(monkeypatch, VERIFIED)
    harness = Harness(app, tmp_path=tmp_path)
    harness.page._playing_episode = (2, 5)
    harness.page._play_anime(ANIME)
    settle(app, lambda: harness.loaded or harness.toast_text)
    assert harness.page._playing_episode is None
    assert harness.page._playing_card is None
    assert harness.page.player.next_episode.isVisible() is False


# -- edges found in review ------------------------------------------------


class Verdict:
    """The shared core's answer, in Agent A's published shape."""

    def __init__(self, *, playable=16498, verified=True, is_anime=True,
                 failed=False):
        self.is_anime = is_anime
        self.verified = verified
        self.playable_anilist_id = playable
        self.resolution_failed = failed


def ask(answer, mal_id=21):
    return qt_anime_routing.plan(
        mal_id, "One Piece", provider="cinesrc", identity=lambda _m, **_k: answer
    )


def test_a_playable_anilist_id_in_the_shared_answer_is_a_resolution():
    route = ask(Verdict(playable=16498))
    assert route.forced is True
    assert route.anilist_id == 16498


def test_a_resolution_failure_never_guesses_an_anilist_entry():
    route = ask(Verdict(playable=0, failed=True))
    assert route.forced is True
    assert route.anilist_id == 0
    assert route.provider == ""


def test_an_unresolved_anilist_entry_is_not_a_fallback():
    route = ask(Verdict(playable=0))
    assert route.forced is True
    assert route.anilist_id == 0
    assert "AniList" in route.error


def test_switching_media_section_ends_the_provider_lock(app, monkeypatch, tmp_path):
    use_identity(monkeypatch, VERIFIED)
    harness = Harness(app, tmp_path=tmp_path)
    tip = harness.page.provider_button.toolTip()
    harness.page._play_anime(ANIME)
    settle(app, lambda: harness.loaded or harness.toast_text)
    assert harness.page.provider_button.isEnabled() is True

    harness.page._loaded = True
    harness.page.show_section("home")
    pump(app)
    assert harness.page.provider_button.isEnabled() is True
    assert harness.page.provider_button.toolTip() == tip


def test_a_second_verified_play_keeps_the_original_tooltip(app, monkeypatch, tmp_path):
    use_identity(monkeypatch, VERIFIED)
    harness = Harness(app, tmp_path=tmp_path)
    tip = harness.page.provider_button.toolTip()
    harness.page._play_anime(ANIME)
    settle(app, lambda: harness.loaded or harness.toast_text)
    harness.page._play_anime(ANIME)
    settle(app, lambda: harness.loaded or harness.toast_text)
    harness.page._leave_player()
    pump(app)
    assert harness.page.provider_button.isEnabled() is True
    assert harness.page.provider_button.toolTip() == tip


def test_a_player_that_cannot_open_does_not_strand_the_lock(app, monkeypatch, tmp_path):
    use_identity(monkeypatch, VERIFIED)
    harness = Harness(app, tmp_path=tmp_path)

    def broken(*_args, **_kwargs):
        raise RuntimeError("no video surface")

    monkeypatch.setattr(harness.page, "play_embedded", broken)
    harness.page._play_anime(ANIME)
    settle(app, lambda: harness.loaded or harness.toast_text)
    assert harness.page.provider_button.isEnabled() is True
    assert "could not be opened" in harness.toast_text


def test_a_verified_anilist_match_starts_on_ani_pm_whatever_is_saved(
    app, monkeypatch, tmp_path
):
    """A MAL-verified search match is a verified anime like any other."""
    use_identity(monkeypatch, VERIFIED)
    harness = Harness(app, {"anime_streaming_provider": "toflix"}, tmp_path)
    harness.page._detail_card = ANIME
    harness.page._anilist_match = ("One Piece", 999)
    harness.page._play_anilist_match()
    settle(app, lambda: harness.loaded or harness.toast_text)
    assert harness.loaded == [ANIME_URL]
    assert harness.settings.get("anime_streaming_provider") == "toflix"

    # A pick is session-only, and picking anipm.pk goes back to the match.
    harness.page._choose_provider("toflix")
    pump(app)
    assert len(harness.loaded) == 2
    assert harness.loaded[-1] == "https://toflix.co/embed/anime/16498/1", (
        "a switch must play the verified AniList entry, not the search's id"
    )
    assert harness.settings.get("anime_streaming_provider") == "toflix"

    harness.page._choose_provider(streaming.ANI_PROVIDER)
    pump(app)
    assert harness.loaded[-1] == ANIME_URL


def test_an_unverified_anilist_match_still_plays_the_match(app, monkeypatch, tmp_path):
    use_identity(monkeypatch, UNVERIFIED)
    harness = Harness(app, {"anime_streaming_provider": "toflix"}, tmp_path)
    harness.page._detail_card = ANIME
    harness.page._anilist_match = ("One Piece", 999)
    harness.page._play_anilist_match()
    settle(app, lambda: harness.loaded or harness.toast_text)
    assert harness.loaded and "999" in harness.loaded[-1]
    assert harness.page.provider_button.isEnabled() is True


class StubTmdb:
    """TMDB's multi-search, offline; records the query it was asked."""

    configured = True

    def __init__(self, results) -> None:
        self.results = list(results)
        self.queries: list[str] = []

    def search(self, query: str, *, page: int = 1):
        self.queries.append(query)
        return self.results


class SearchHit:
    """One TMDB result, in the shape ``card_from_tmdb`` reads."""

    def __init__(self, **fields) -> None:
        self.__dict__.update(fields)


def a_movie_hit() -> SearchHit:
    return SearchHit(
        key="movie:603", title="One Piece Film", year="2000", kind="movie",
        tmdb_id=603, poster_url="", backdrop_url="", overview="",
        rating=None, adult=False,
    )


def a_tv_hit() -> SearchHit:
    return SearchHit(
        key="tv:1399", title="One Piece", year="1999", kind="tv",
        tmdb_id=1399, poster_url="", backdrop_url="", overview="",
        rating=None, adult=False,
    )


def test_an_unverified_anilist_page_uses_the_selected_normal_provider(
    app, monkeypatch, tmp_path
):
    """Not an AniList provider: the selected provider's own TMDB route plays."""
    use_identity(monkeypatch, UNVERIFIED)
    harness = Harness(app, {"streaming_provider": "cinesrc"}, tmp_path)
    tmdb = StubTmdb([a_movie_hit()])
    harness.page._tmdb = tmdb
    harness.page._anilist_match = ("One Piece", 999)
    harness.page._play_anilist_match()
    settle(app, lambda: harness.loaded or harness.toast_text)
    assert tmdb.queries == ["One Piece"], (
        "the selected provider's title route was not asked"
    )
    assert harness.loaded and harness.loaded[-1].startswith("https://cinesrc")
    assert "603" in harness.loaded[-1]
    assert all(
        "anipm.pk" not in url and "toflix" not in url for url in harness.loaded
    ), "the selected provider was bypassed"
    assert harness.page.provider_button.isEnabled() is True


def test_an_unverified_anilist_tv_page_opens_details_not_playback(
    app, monkeypatch, tmp_path
):
    """A show has no episode to play yet: the matching card opens instead."""
    use_identity(monkeypatch, UNVERIFIED)
    harness = Harness(app, {"streaming_provider": "cinesrc"}, tmp_path)
    opened, plain = [], []
    monkeypatch.setattr(harness.page, "open_card", opened.append)
    monkeypatch.setattr(
        harness.page, "_play_tmdb_plain",
        lambda *args, **kwargs: plain.append((args, kwargs)),
    )
    tmdb = StubTmdb([a_tv_hit()])
    harness.page._tmdb = tmdb
    harness.page._anilist_match = ("One Piece", 999)
    harness.page._play_anilist_match()
    settle(app, lambda: opened or harness.toast_text)
    assert tmdb.queries == ["One Piece"]
    assert len(opened) == 1, "the matching details card was not opened"
    assert opened[0].kind == "tv" and opened[0].tmdb_id == 1399
    assert plain == [], "a show must not be played straight from the search"
    assert harness.loaded == []
    assert harness.page._provider() == "cinesrc", "the selected provider is kept"
    assert harness.settings.get("anime_streaming_provider", "") == "", (
        "the selected provider must not be rewritten"
    )


def test_a_stale_tmdb_search_cannot_open_details_after_a_newer_request(
    app, monkeypatch, tmp_path
):
    started, gate = threading.Event(), threading.Event()

    class SlowTmdb(StubTmdb):
        def search(self, query: str, *, page: int = 1):
            self.queries.append(query)
            started.set()
            gate.wait(10)
            return self.results

    use_identity(monkeypatch, UNVERIFIED)
    harness = Harness(app, {"streaming_provider": "cinesrc"}, tmp_path)
    opened = []
    monkeypatch.setattr(harness.page, "open_card", opened.append)
    harness.page._tmdb = SlowTmdb([a_tv_hit()])
    harness.page._anilist_match = ("One Piece", 999)
    harness.page._play_anilist_match()
    end = time.time() + 5
    while time.time() < end and not started.is_set():
        pump(app)  # the identity answer is delivered on the UI thread first
    if not started.is_set():
        gate.set()
    assert started.is_set(), "the TMDB search never started"

    # A newer Watch request supersedes the waiting search.
    monkeypatch.setattr(
        qt_anime_routing, "identify_tmdb", lambda *_a, **_k: UNVERIFIED
    )
    harness.page._play_tmdb(MOVIE)
    settle(app, lambda: harness.loaded)
    gate.set()
    pump(app, 0.4)
    assert opened == [], "a superseded search opened the details card"
    assert harness.loaded and harness.loaded[-1].startswith("https://cinesrc")


def test_a_verified_anilist_page_never_searches_tmdb(app, monkeypatch, tmp_path):
    use_identity(monkeypatch, VERIFIED)
    harness = Harness(app, {"streaming_provider": "cinesrc"}, tmp_path)
    tmdb = StubTmdb([a_movie_hit()])
    harness.page._tmdb = tmdb
    harness.page._anilist_match = ("One Piece", 999)
    harness.page._play_anilist_match()
    settle(app, lambda: harness.loaded)
    assert harness.loaded == ["https://anipm.pk/embed/ani/16498/1/sub"]
    assert tmdb.queries == [], "verified anime must not go through TMDB"


def test_an_anilist_resolution_failure_never_searches_tmdb(
    app, monkeypatch, tmp_path
):
    use_identity(monkeypatch, NO_ANILIST)
    harness = Harness(app, {"streaming_provider": "cinesrc"}, tmp_path)
    tmdb = StubTmdb([a_movie_hit()])
    harness.page._tmdb = tmdb
    harness.page._anilist_match = ("One Piece", 999)
    harness.page._play_anilist_match()
    settle(app, lambda: harness.toast_text)
    assert harness.loaded == [], "a resolution failure must not fall back"
    assert tmdb.queries == []
    assert "AniList" in harness.toast_text


# -- anipm.pk tracks, including the API-only hard tracks --------------------

HARD_SUB = "https://anipm.pk/embed/ani/16498/1/subHard?api=1"
HARD_DUB = "https://anipm.pk/embed/ani/16498/1/dubHard?api=1"


def a_verified_route() -> qt_anime_routing.Route:
    return qt_anime_routing.Route(
        provider=streaming.ANI_PROVIDER, anilist_id=16498, forced=True
    )


def test_a_hard_sub_plays_its_exact_api_embed(app, tmp_path):
    harness = Harness(app, {"anime_language": "subHard"}, tmp_path)
    harness.page._play_forced_anime(
        ANIME, a_verified_route(), api_embed=HARD_SUB
    )
    pump(app)
    assert harness.loaded == [HARD_SUB], "the requested hard-sub track played"
    assert harness.page.provider_button.isEnabled() is True


def test_a_hard_dub_plays_its_exact_api_embed(app, tmp_path):
    harness = Harness(app, {"anime_language": "dubHard"}, tmp_path)
    harness.page._play_forced_anime(
        ANIME, a_verified_route(), api_embed=HARD_DUB
    )
    pump(app)
    assert harness.loaded == [HARD_DUB], "the requested hard-dub track played"


def test_a_hard_sub_without_an_embed_is_an_error(app, monkeypatch, tmp_path):
    use_identity(monkeypatch, VERIFIED)
    harness = Harness(app, {"anime_language": "subHard"}, tmp_path)
    harness.page._play_anime(ANIME)
    settle(app, lambda: harness.toast_text)
    assert harness.loaded == [], "a hard track must never fall back to Sub"
    assert "Sub·Hard" in harness.toast_text
    assert "embed" in harness.toast_text
    assert harness.page.provider_button.isEnabled() is True


def test_a_hard_dub_without_an_embed_is_an_error(app, monkeypatch, tmp_path):
    use_identity(monkeypatch, VERIFIED)
    harness = Harness(app, {"anime_language": "dubHard"}, tmp_path)
    harness.page._play_anime(ANIME)
    settle(app, lambda: harness.toast_text)
    assert harness.loaded == [], "a hard track must never fall back to Dub"
    assert "Dub·Hard" in harness.toast_text
    assert "embed" in harness.toast_text


def test_a_hard_track_with_a_foreign_embed_is_an_error(app, tmp_path):
    harness = Harness(app, {"anime_language": "subHard"}, tmp_path)
    harness.page._play_forced_anime(
        ANIME, a_verified_route(),
        api_embed="https://evil.example/embed/ani/16498/1/subHard",
    )
    pump(app)
    assert harness.loaded == [], "an unvalidated embed must not play"
    assert "anipm.pk" in harness.toast_text


def test_ordinary_sub_and_dub_keep_the_constructed_route(
    app, monkeypatch, tmp_path
):
    use_identity(monkeypatch, VERIFIED)
    for language, expected in (
        ("sub", "https://anipm.pk/embed/ani/16498/1/sub"),
        ("dub", "https://anipm.pk/embed/ani/16498/1/dub"),
    ):
        harness = Harness(app, {"anime_language": language}, tmp_path)
        harness.page._play_anime(ANIME)
        settle(app, lambda: harness.loaded)
        assert harness.loaded == [expected], (
            f"ordinary {language} playback changed"
        )


# -- every Watch entry point ----------------------------------------------


@pytest.mark.parametrize("entry", ("_play_detail", "_play_current"))
def test_the_watch_buttons_play_ani_pm_whatever_is_saved(app, monkeypatch, tmp_path, entry):
    """Detail page and home hero both go through _play_card -> _play_anime."""
    use_identity(monkeypatch, VERIFIED)
    harness = Harness(
        app, {"anime_streaming_provider": "toflix", "streaming_provider": "cinesrc"},
        tmp_path,
    )
    harness.page._detail_card = ANIME
    harness.page._hero_card = ANIME
    getattr(harness.page, entry)()
    settle(app, lambda: harness.loaded or harness.toast_text)
    assert harness.loaded == [ANIME_URL]
    assert harness.settings.get("anime_streaming_provider") == "toflix"
    assert harness.page.provider_button.isEnabled() is True


# -- identity without a TMDB client or API key ----------------------------


def test_a_mal_verified_anime_plays_ani_pm_without_a_tmdb_client(app, monkeypatch, tmp_path):
    """The real core verifies by MAL id; no TMDB client is involved at all."""
    use_identity(monkeypatch, VERIFIED)
    # No mapping record for this show: the identity's own entry plays.
    monkeypatch.setattr(anime_map, "tmdb_to_anilist", lambda *a, **k: None)
    harness = Harness(app, {"streaming_provider": "cinesrc"}, tmp_path)
    assert harness.page._tmdb is None
    harness.page._play_tmdb(TV)
    for _ in range(100):
        pump(app)
        if harness.loaded:
            break
    assert harness.loaded == ["https://anipm.pk/embed/ani/16498/1/sub"]
    assert all("cinesrc" not in url for url in harness.loaded)


def test_a_verified_tmdb_card_starts_on_ani_pm_with_its_mapped_episode(
    app, monkeypatch, tmp_path
):
    """A TMDB catalogue card is the same verified session, with its episode."""
    use_identity(monkeypatch, VERIFIED)
    monkeypatch.setattr(
        anime_map, "tmdb_to_anilist",
        lambda *_a, **_k: anime_map.PlayTarget(
            anilist_id=16498, episode=12, confident=True,
        ),
    )
    harness = Harness(app, {"anime_streaming_provider": "vidy"}, tmp_path)
    episode = Episode(season=2, number=5, title="The Wolf and the Lion")
    harness.page._play_tmdb(TV, 2, episode)
    settle(app, lambda: harness.loaded or harness.toast_text)
    assert harness.loaded == ["https://anipm.pk/embed/ani/16498/12/sub"], (
        "the saved anime provider preempted anipm.pk, or the mapped episode was lost"
    )
    assert harness.page._playing_episode == (2, 5)

    # A manual pick is session-only, and plays the same season and episode.
    harness.page._choose_provider("toflix")
    pump(app)
    assert len(harness.loaded) == 2
    assert "toflix" in harness.loaded[-1]
    assert "/1399/2/5" in harness.loaded[-1]
    assert harness.settings.get("anime_streaming_provider") == "vidy", (
        "a manual switch overwrote the saved preference"
    )

    # Switching back restores the anime and its mapped episode.
    harness.page._choose_provider(streaming.ANI_PROVIDER)
    pump(app)
    assert harness.loaded[-1] == "https://anipm.pk/embed/ani/16498/12/sub"
    assert harness.page._playing_episode == (2, 5)


def test_a_verified_movies_session_offers_ani_pm_and_only_anime_providers(
    app, monkeypatch, tmp_path
):
    """Opened from Movies, a verified anime still decides the provider menu."""
    use_identity(monkeypatch, VERIFIED)
    monkeypatch.setattr(
        anime_map, "tmdb_to_anilist",
        lambda *_a, **_k: anime_map.PlayTarget(
            anilist_id=16498, episode=12, confident=True,
        ),
    )
    harness = Harness(app, {"anime_streaming_provider": "vidy"}, tmp_path)
    harness.page._section = "movies"
    harness.page._play_tmdb(TV, 2, Episode(season=2, number=5, title="The Wolf and the Lion"))
    settle(app, lambda: harness.loaded)
    assert harness.loaded == ["https://anipm.pk/embed/ani/16498/12/sub"]

    offered = harness.page._menu_providers()
    assert streaming.ANI_PROVIDER in offered
    assert set(offered) == {streaming.ANI_PROVIDER, *streaming.ANILIST_ANIME_PROVIDERS}
    assert "cinesrc" not in offered, "the Movies menu did not follow the session"

    menu = harness.page._provider_menu()
    texts = [action.text() for action in menu.actions()]
    assert any(text.startswith(streaming.LABELS[streaming.ANI_PROVIDER]) for text in texts)
    assert not any(text.startswith(streaming.LABELS["cinesrc"]) for text in texts)


def test_a_verified_movies_session_switches_from_the_menu_and_back(
    app, monkeypatch, tmp_path
):
    """anipm.pk → ToFlix → anipm.pk from the real menu, mapped episode restored."""
    use_identity(monkeypatch, VERIFIED)
    monkeypatch.setattr(
        anime_map, "tmdb_to_anilist",
        lambda *_a, **_k: anime_map.PlayTarget(
            anilist_id=16498, episode=12, confident=True,
        ),
    )
    harness = Harness(app, {"anime_streaming_provider": "vidy"}, tmp_path)
    harness.page._section = "movies"
    harness.page._play_tmdb(TV, 2, Episode(season=2, number=5, title="The Wolf and the Lion"))
    settle(app, lambda: harness.loaded)
    ani_url = "https://anipm.pk/embed/ani/16498/12/sub"
    assert harness.loaded == [ani_url]
    assert harness.page._active_provider() == streaming.ANI_PROVIDER
    assert harness.page.provider_button.text().startswith(
        streaming.LABELS[streaming.ANI_PROVIDER]
    )

    def pick(key: str) -> None:
        menu = harness.page._provider_menu()
        action = next(
            item for item in menu.actions()
            if item.text().startswith(streaming.LABELS[key])
        )
        action.trigger()
        pump(app)

    pick("toflix")
    assert harness.loaded[-1] == "https://toflix.co/embed/tv/1399/2/5"
    assert harness.page._active_provider() == "toflix", (
        "the badge did not follow the session's actual provider"
    )
    assert harness.page.provider_button.text().startswith(
        streaming.LABELS["toflix"]
    )
    assert harness.settings.get("anime_streaming_provider") == "vidy", (
        "a session switch overwrote the saved anime preference"
    )
    assert streaming.ANI_PROVIDER in harness.page._menu_providers(), (
        "anipm.pk was no longer offered, so the session could not switch back"
    )

    pick(streaming.ANI_PROVIDER)
    assert harness.loaded[-1] == ani_url, "the mapped AniList episode was lost"
    assert harness.page._active_provider() == streaming.ANI_PROVIDER
    assert harness.page._playing_episode == (2, 5)


def test_an_ordinary_movies_menu_is_unchanged_without_a_verified_session(
    app, tmp_path
):
    harness = Harness(app, {}, tmp_path)
    harness.page._section = "movies"
    assert harness.page._menu_providers() == streaming.PROVIDERS
    assert "cinesrc" in harness.page._menu_providers()


def test_a_movie_provider_preference_changes_independently_of_a_session(
    app, monkeypatch, tmp_path
):
    """A pick with no anime route is a movie preference, not a session switch."""
    use_identity(monkeypatch, VERIFIED)
    monkeypatch.setattr(
        anime_map, "tmdb_to_anilist",
        lambda *_a, **_k: anime_map.PlayTarget(
            anilist_id=16498, episode=12, confident=True,
        ),
    )
    harness = Harness(
        app,
        {"streaming_provider": "cinesrc", "anime_streaming_provider": "vidy"},
        tmp_path,
    )
    harness.page._section = "movies"
    harness.page._play_tmdb(TV, 2, Episode(season=2, number=5, title="The Wolf and the Lion"))
    settle(app, lambda: harness.loaded)
    loaded = list(harness.loaded)

    harness.page._choose_provider("velarium")
    pump(app)

    assert harness.settings.get("streaming_provider") == "velarium"
    assert harness.settings.get("anime_streaming_provider") == "vidy"
    assert harness.loaded == loaded, "a movie preference change replayed the anime"
    assert harness.page._active_provider() == streaming.ANI_PROVIDER


def test_a_missing_mal_client_id_keeps_the_provider_for_a_tmdb_title(app, monkeypatch, tmp_path):
    """Real core, no MAL key: lookup failure is UNVERIFIED, so the provider plays."""
    from lgl import mal

    monkeypatch.setattr(mal, "load_client_id", lambda **_kwargs: "")
    harness = Harness(app, {"streaming_provider": "cinesrc"}, tmp_path)
    assert harness.page._tmdb is None
    harness.page._play_tmdb(TV)
    for _ in range(100):
        pump(app)
        if harness.loaded:
            break
    assert harness.loaded, "the selected provider must still play"
    assert "1399" in harness.loaded[-1]
    assert harness.page.provider_button.isEnabled() is True


def test_a_resolution_failure_explains_the_shared_reason():
    ambiguous = qt_anime_routing.resolution_error(
        "One Piece",
        qt_anime_routing.Identity(
            verified=False, resolution_failed=True, reason="anilist-ambiguous",
        ),
    )
    assert "One Piece" in ambiguous
    assert "more than one" in ambiguous

    missing = qt_anime_routing.resolution_error(
        "One Piece",
        qt_anime_routing.Identity(
            verified=False, resolution_failed=True, reason="anilist-missing",
        ),
    )
    assert "no entry" in missing

    unknown = qt_anime_routing.resolution_error("One Piece", None)
    assert "AniList entry" in unknown


# -- AniList match: defensive resolution handling -------------------------


def test_an_anilist_match_resolution_failure_is_an_error(app, monkeypatch, tmp_path):
    """A resolution failure is never a reason to use the selected provider."""
    use_identity(monkeypatch, qt_anime_routing.Identity(
        verified=True, playable_anilist_id=0,
        resolution_failed=True, reason="ambiguous",
    ))
    harness = Harness(app, {"anime_streaming_provider": "toflix"}, tmp_path)
    harness.page._anilist_match = ("One Piece", 999)
    harness.page._play_anilist_match()
    settle(app, lambda: harness.toast_text)
    assert harness.loaded == [], "the match must not play somewhere else"
    assert "AniList" in harness.toast_text
    assert harness.page.provider_button.isEnabled() is True


# -- Watch tokens: an older lookup cannot play after a newer request ------


def _gated_plan(started, gate, route_id):
    def plan(mal_id, title="", *, provider="", settings=None, identity=None):
        if int(mal_id) == 21:
            started.set()
            gate.wait(10)
            return qt_anime_routing.Route(
                provider=streaming.ANI_PROVIDER, anilist_id=16498, forced=True,
            )
        return qt_anime_routing.Route(
            provider=streaming.ANI_PROVIDER, anilist_id=route_id, forced=True,
        )
    return plan


def test_an_older_anime_lookup_cannot_play_after_a_newer_request(app, monkeypatch, tmp_path):
    started, gate = threading.Event(), threading.Event()
    monkeypatch.setattr(
        qt_anime_routing, "plan", _gated_plan(started, gate, 777)
    )
    other = qt_media.Card(
        key="anime:22", title="Naruto", year="2002", kind="anime", mal_id=22
    )
    harness = Harness(app, tmp_path=tmp_path)
    harness.page._play_anime(ANIME)
    assert started.wait(5), "the first lookup never started"
    harness.page._play_anime(other)
    settle(app, lambda: harness.loaded)
    gate.set()
    pump(app, 0.4)
    assert harness.loaded == ["https://anipm.pk/embed/ani/777/1/sub"], (
        "the superseded answer started playback"
    )


def test_a_stale_lookup_failure_is_not_reported(app, monkeypatch, tmp_path):
    started, gate = threading.Event(), threading.Event()

    def plan(mal_id, title="", *, provider="", settings=None, identity=None):
        if int(mal_id) == 21:
            started.set()
            gate.wait(10)
            raise RuntimeError("MAL went away")
        return qt_anime_routing.Route(
            provider=streaming.ANI_PROVIDER, anilist_id=777, forced=True,
        )

    monkeypatch.setattr(qt_anime_routing, "plan", plan)
    other = qt_media.Card(
        key="anime:22", title="Naruto", year="2002", kind="anime", mal_id=22
    )
    harness = Harness(app, tmp_path=tmp_path)
    harness.page._play_anime(ANIME)
    assert started.wait(5), "the first lookup never started"
    harness.page._play_anime(other)
    settle(app, lambda: harness.loaded)
    gate.set()
    pump(app, 0.4)
    assert harness.loaded[-1].endswith("/777/1/sub")
    assert "could not be checked" not in harness.toast_text


def test_a_stale_tmdb_lookup_cannot_play_after_a_newer_request(app, monkeypatch, tmp_path):
    started, gate = threading.Event(), threading.Event()

    def identify_tmdb(kind, tmdb_id, *, title="", year="", settings=None, **_kw):
        started.set()
        gate.wait(10)
        return qt_anime_routing.Identity(verified=True, playable_anilist_id=16498)

    monkeypatch.setattr(qt_anime_routing, "identify_tmdb", identify_tmdb)
    monkeypatch.setattr(
        qt_anime_routing, "identify_mal",
        lambda *_a, **_k: qt_anime_routing.Identity(
            verified=True, playable_anilist_id=21,
        ),
    )
    monkeypatch.setattr(anime_map, "tmdb_to_anilist", lambda *_a, **_k: None)
    harness = Harness(app, tmp_path=tmp_path)
    harness.page._play_tmdb(TV)
    assert started.wait(5), "the TMDB lookup never started"
    harness.page._play_anime(ANIME)
    settle(app, lambda: harness.loaded)
    gate.set()
    pump(app, 0.4)
    assert harness.loaded == ["https://anipm.pk/embed/ani/21/1/sub"], (
        "the superseded TMDB answer started playback"
    )


def test_a_raising_identity_lookup_keeps_the_provider(app, monkeypatch, tmp_path):
    """A worker failure is reported and the provider path still runs."""

    def plan(*_args, **_kwargs):
        raise RuntimeError("identity exploded")

    monkeypatch.setattr(qt_anime_routing, "plan", plan)
    harness = Harness(app, {"anime_streaming_provider": "ani"}, tmp_path)
    harness.page._play_anime(ANIME)
    settle(app, lambda: harness.loaded or harness.toast_text)
    assert harness.loaded == ["https://anipm.pk/embed/mal/21/1/sub"]
    assert "could not be checked" in harness.toast_text
