"""TMDB -> AniList routing on the Windows play path.

Identity comes from Agent A's ``lgl/anime_identity`` and episode mapping from
``lgl/anime_map``; both are stubbed here so the tests pin the routing contract
rather than either module's implementation: verified, verified with no AniList
resolution, unverified, non-anime, saved providers, confident episodes, an
unsure mapping, and the mapping-free fallback.  No network: the TMDB client is
a stub and the player is a stub view.
"""

from __future__ import annotations

import os
import time

import pytest

pytest.importorskip("PySide6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QUrl, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QWidget  # noqa: E402

from lgl import (  # noqa: E402
    anime_map,
    anipm,
    qt_anime_routing,
    qt_media,
    qt_player,
    streaming,
)
from lgl.settings import Settings  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def pump(app, seconds: float = 0.05) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def wait_for(app, predicate, seconds: float = 8.0) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class StubPage(QObject):
    fullScreenRequested = Signal(object)

    def triggerAction(self, _action) -> None:
        pass


class StubView(QWidget):
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


class Tmdb:
    """The public client surface the play path touches, and nothing else."""

    def __init__(self, document: dict | None = None) -> None:
        self.document = document if document is not None else {
            "original_language": "ja",
            "genres": [{"id": 16}, {"id": 18}],
        }
        self.paths: list[str] = []

    def get(self, path: str, params=None) -> dict:
        self.paths.append(path)
        return self.document


class Verdict:
    def __init__(self, *, is_anime: bool = True, verified: bool = True) -> None:
        self.is_anime = is_anime
        self.verified = verified
        self.mal_id = None
        self.anilist_ids = ()
        self.reason = ""


class Target:
    def __init__(self, anilist_id: int = 0, episode: int = 1, *,
                 confident: bool = True, candidates=()) -> None:
        self.anilist_id = anilist_id
        self.episode = episode
        self.confident = confident
        self.candidates = tuple(candidates)


class Page:
    """A MediaPage wired for routing only: no history, no network, no player."""

    def __init__(self, app, *, settings=None, provider=streaming.DEFAULT_PROVIDER,
                 tmdb=None) -> None:
        self.app = app
        self.settings = settings
        self.widget = qt_media.MediaPage(
            self.settings,
            player_factory=lambda parent: qt_player.EmbeddedPlayer(
                parent, view_factory=StubView
            ),
        )
        self.widget._tmdb = tmdb or Tmdb()
        self.widget._provider = lambda: provider
        self.widget._language = lambda: "sub"
        self.widget._record_history = lambda card: None
        self.urls: list[str] = []
        self.widget.play_embedded = self._play_embedded
        self.widget._refresh_next_episode = lambda: None
        self.plain: list[tuple] = []
        self._plain = self.widget._play_tmdb_plain

        def plain(card, provider_key, season=None, episode=None):
            self.plain.append((card, provider_key, season, episode))

        self.widget._play_tmdb_plain = plain

    def _play_embedded(self, url: str, title: str = "") -> None:
        self.urls.append(url)

    def close(self) -> None:
        self.widget.deleteLater()


TV = qt_media.Card(key="tv:1396", title="Breaking Bad", kind="tv", tmdb_id=1396)


def episode(number: int = 3) -> object:
    return qt_media.tmdb.Episode(season=2, number=number, title="Bit by a Dead Bee")


@pytest.fixture()
def stubbed(monkeypatch):
    """Every contract call recorded, and the identity/target the test sets."""
    calls: dict[str, list] = {"identity": [], "mapped": []}
    state = {
        "verdict": Verdict(),
        "target": Target(anilist_id=42, episode=5),
        #: The AniList entry the shared identity resolved, 0 for none.
        "playable": 42,
        #: Whether the shared core reports a failed AniList resolution.
        "failed": False,
    }

    def identify_tmdb(kind, tmdb_id, *, title="", year="", settings=None):
        calls["identity"].append((kind, tmdb_id))
        verdict = state["verdict"]
        return qt_anime_routing.Identity(
            is_anime=verdict.is_anime,
            verified=verdict.verified,
            playable_anilist_id=state["playable"],
            resolution_failed=state["failed"],
        )

    def mapped(kind, tmdb_id, season=1, ep=1, *, title=""):
        calls["mapped"].append((kind, tmdb_id, season, ep, title))
        return state["target"]

    monkeypatch.setattr(
        qt_anime_routing, "identify_tmdb", identify_tmdb, raising=False
    )
    monkeypatch.setattr(anime_map, "tmdb_to_anilist", mapped, raising=False)
    monkeypatch.setattr(anipm, "AniPmClient", lambda *a, **k: _NoTitles())
    return calls, state


class _NoTitles:
    def title(self, _anilist_id):
        class Named:
            title = ""

        return Named()


# -- confident --------------------------------------------------------------

def test_a_confident_mapping_plays_the_anilist_episode(app, stubbed):
    calls, _state = stubbed
    page = Page(app)
    page.widget._play_tmdb(TV, 2, episode(3))
    assert wait_for(app, lambda: page.urls)

    assert page.urls == [streaming.ani_url(42, 5, language="sub")]
    assert calls["mapped"] == [("tv", 1396, 2, 3, "Breaking Bad")]
    assert calls["identity"] == [("tv", 1396)], "the shared identity is asked once"
    assert page.plain == [], "a confident anime must not fall back to the provider"
    assert page.widget._playing_episode == (2, 3), (
        "next-episode bookkeeping must stay in TMDB numbers"
    )
    page.close()


def test_identity_needs_no_tmdb_details_document(app, stubbed):
    """The shared module owns its own lookup; the page asks TMDB for nothing."""
    calls, _state = stubbed
    page = Page(app)
    page.widget._play_tmdb(TV, 2, episode(3))
    assert wait_for(app, lambda: page.urls)
    page.widget._play_tmdb(TV, 2, episode(4))
    assert wait_for(app, lambda: len(page.urls) == 2)
    assert page.widget._tmdb.paths == [], "identity no longer depends on details"
    assert len(calls["identity"]) == 2
    page.close()


# -- not confident ----------------------------------------------------------

def test_an_unsure_mapping_asks_which_part_and_plays_the_pick(app, stubbed, monkeypatch):
    _calls, state = stubbed
    state["target"] = Target(
        confident=False,
        candidates=(Target(anilist_id=11, episode=1), Target(anilist_id=22, episode=1)),
    )
    opened: list[dict] = []

    class FakeDialog:
        def __init__(self, show_title, candidates, *, parent=None) -> None:
            self.show_title = show_title
            self.candidates = list(candidates)
            self.picked = None
            self.titles: list[str] = []
            opened.append({"title": show_title, "count": len(self.candidates)})

        def set_titles(self, titles) -> None:
            self.titles = list(titles)

        def isVisible(self) -> bool:  # noqa: N802 - Qt's name
            return True

        def exec(self):
            self.picked = self.candidates[1]
            return qt_media.QDialog.Accepted

        def choice(self):
            return self.picked

    monkeypatch.setattr(qt_media, "WhichPartDialog", FakeDialog, raising=False)
    page = Page(app)
    page.widget._play_tmdb(TV, 2, episode(3))
    assert wait_for(app, lambda: page.urls)

    assert opened == [{"title": "Breaking Bad", "count": 2}]
    assert page.urls == [streaming.ani_url(22, 1, language="sub")], (
        "the pick decides which AniList entry plays"
    )
    page.close()


def test_cancelling_which_part_plays_nothing(app, stubbed, monkeypatch):
    _calls, state = stubbed
    state["target"] = Target(confident=False, candidates=(Target(1, 1), Target(2, 1)))

    class Cancelled:
        def __init__(self, *a, **k) -> None:
            self.picked = None

        def set_titles(self, _titles) -> None:
            pass

        def isVisible(self) -> bool:  # noqa: N802
            return True

        def exec(self):
            return qt_media.QDialog.Rejected

        def choice(self):
            return None

    monkeypatch.setattr(qt_media, "WhichPartDialog", Cancelled, raising=False)
    page = Page(app)
    page.widget._play_tmdb(TV, 2, episode(3))
    pump(app, 0.4)

    assert page.urls == [] and page.plain == []
    page.close()


# -- no mapping -------------------------------------------------------------

def test_no_mapping_plays_the_shared_anilist_id_without_a_title_search(
    app, stubbed, monkeypatch
):
    """Verified with one AniList entry: play that entry, never search a title.

    The old fallback searched the title and could land on a different entry
    than the one MAL verification resolved.
    """
    _calls, state = stubbed
    state["target"] = None
    searches: list[str] = []

    def search(title):
        searches.append(title)
        return 777

    monkeypatch.setattr(streaming, "anilist_search", search, raising=False)
    page = Page(app)
    page.widget._play_tmdb(TV, 2, episode(3))
    assert wait_for(app, lambda: page.urls)

    assert searches == [], "a verified anime must not be re-identified by title"
    assert page.urls == [streaming.ani_url(42, 3, language="sub")]
    assert page.plain == []
    page.close()


def test_a_verified_anime_with_no_anilist_resolution_is_an_error(
    app, stubbed, monkeypatch
):
    _calls, state = stubbed
    state["target"] = None
    state["playable"] = 0
    searches: list[str] = []

    def search(title):
        searches.append(title)
        return 777

    monkeypatch.setattr(streaming, "anilist_search", search, raising=False)
    errors: list[str] = []
    page = Page(app)
    page.widget._note_error = errors.append
    page.widget._play_tmdb(TV, 2, episode(3))
    assert wait_for(app, lambda: errors)

    assert "AniList entry" in errors[0]
    assert page.plain == [], "no provider fallback for a verified anime"
    assert page.urls == []
    assert searches == [], "no title search either"
    page.close()


def test_a_resolution_failure_is_an_error_not_a_fallback(app, stubbed):
    _calls, state = stubbed
    state["failed"] = True
    errors: list[str] = []
    page = Page(app)
    page.widget._note_error = errors.append
    page.widget._play_tmdb(TV, 2, episode(3))
    assert wait_for(app, lambda: errors)

    assert "AniList entry" in errors[0]
    assert page.plain == [] and page.urls == []
    page.close()


# -- unaffected paths -------------------------------------------------------

def test_a_non_anime_title_plays_exactly_as_before(app, stubbed):
    calls, state = stubbed
    state["verdict"] = Verdict(is_anime=False, verified=False)
    page = Page(app)
    page.widget._play_tmdb(TV, 2, episode(3))
    assert wait_for(app, lambda: page.plain)

    assert calls["mapped"] == [], "a non-anime title never touches the mapping"
    assert page.plain[0][1] == streaming.DEFAULT_PROVIDER, "same provider as today"
    assert page.urls == []
    page.close()


def test_the_verdict_runs_for_any_provider_but_a_guess_never_reroutes(app, stubbed):
    """A ja + Animation guess keeps the selected provider and asks nothing."""
    calls, state = stubbed
    state["verdict"] = Verdict(is_anime=True, verified=False)
    page = Page(app, provider="vidy")
    page.widget._play_tmdb(TV, 2, episode(3))
    assert wait_for(app, lambda: page.plain)

    assert calls["identity"] == [("tv", 1396)], (
        "the identity is checked whatever provider is selected"
    )
    assert page.widget._tmdb.paths == [], "identity needs no details document"
    assert calls["mapped"] == [], "a guess must not reach the mapping"
    assert page.plain[0][1] == "vidy", "the user's provider keeps the play"
    assert page.urls == []
    page.close()


# -- a verified anime plays on anipm.pk ---------------------------------------

def test_a_verified_anime_plays_on_anipm_without_saving_a_setting(app, stubbed, tmp_path):
    _calls, state = stubbed
    state["verdict"] = Verdict(is_anime=True, verified=True)
    settings = Settings(tmp_path / "settings.json", keyring=False)
    page = Page(app, settings=settings, provider="cinesrc")
    page.widget._play_tmdb(TV, 2, episode(3))
    assert wait_for(app, lambda: page.urls)

    assert page.urls == [streaming.ani_url(42, 5, language="sub")]
    assert page.plain == [], "the general provider must not play this"
    assert settings.get("anime_streaming_provider", "") == "", (
        "a play must not write a preference"
    )
    page.close()


@pytest.mark.parametrize(
    "general, saved",
    [
        ("vidy", ""),          # the movies provider is not an anime choice
        ("cinesrc", ""),
    ],
)
def test_a_verified_anime_plays_on_anipm_without_an_anime_choice(
    app, stubbed, tmp_path, general, saved
):
    """Verified anime uses anipm.pk until an anime provider is picked."""
    calls, state = stubbed
    state["verdict"] = Verdict(is_anime=True, verified=True)
    settings = Settings(tmp_path / "settings.json", keyring=False)
    if saved:
        settings.set("anime_streaming_provider", saved)
    page = Page(app, settings=settings, provider=general)
    page.widget._play_tmdb(TV, 2, episode(3))
    assert wait_for(app, lambda: page.urls)

    assert page.urls == [streaming.ani_url(42, 5, language="sub")]
    assert page.plain == [], "no provider may take a verified anime"
    assert calls["mapped"], "episode mapping still decides which episode"
    assert settings.get("anime_streaming_provider", "") == saved, (
        "a play must not write a preference"
    )
    page.close()



# -- the dialog itself ------------------------------------------------------

def test_the_which_part_sheet_uses_the_shared_frame(app):
    from lgl import design_components
    from lgl.qt_which_part import WhichPartDialog, candidate_label

    dialog = WhichPartDialog("Breaking Bad", [Target(1, 1), Target(2, 1)])
    layout = dialog.layout()
    assert dialog.objectName() == "sheet"
    assert layout.contentsMargins().top() == design_components.DIALOG_PADDING
    assert layout.spacing() == design_components.DIALOG_GAP
    labels = [button.text() for button in dialog.buttons()]
    assert labels == ["Part 1", "Part 2"], "no title yet means the numbered fallback"

    dialog.set_titles(["Season one", ""])
    assert [button.text() for button in dialog.buttons()] == [
        candidate_label(0, "Season one"), "Part 2",
    ]

    dialog.buttons()[0].click()
    pump(app)
    assert dialog.result() == QDialog.Accepted
    assert dialog.choice().anilist_id == 1


def test_escape_closes_the_which_part_sheet(app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from lgl.qt_which_part import WhichPartDialog

    dialog = WhichPartDialog("Show", [Target(1, 1), Target(2, 1)])
    dialog.show()
    pump(app)
    QTest.keyClick(dialog, Qt.Key_Escape)
    pump(app)
    assert dialog.result() == QDialog.Rejected
    assert dialog.choice() is None

# -- real mapping records, real conversion ---------------------------------

#: The Fribb list's shape, with the Attack on Titan records as the list has
#: them: one entry per TMDB season, and season 3 split at episode 13.
AOT_TMDB = 1429
AOT_RECORDS = (
    {"anilist_id": 16498, "type": "TV", "themoviedb_id": {"tv": AOT_TMDB},
     "season": {"tmdb": 1}, "episode_offset": {"tmdb": 0}},
    {"anilist_id": 99147, "type": "TV", "themoviedb_id": {"tv": AOT_TMDB},
     "season": {"tmdb": 3}, "episode_offset": {"tmdb": 0}},
    {"anilist_id": 104578, "type": "TV", "themoviedb_id": {"tv": AOT_TMDB},
     "season": {"tmdb": 3}, "episode_offset": {"tmdb": 12}},
    {"anilist_id": 131681, "type": "TV", "themoviedb_id": {"tv": AOT_TMDB},
     "season": {"tmdb": 4}, "episode_offset": {"tmdb": 0}},
)

AOT = qt_media.Card(
    key=f"tv:{AOT_TMDB}", title="Attack on Titan", kind="tv", tmdb_id=AOT_TMDB
)


@pytest.fixture()
def real_mapping(monkeypatch):
    """The real module with the mapping records injected, and no sheet allowed."""
    monkeypatch.setattr(
        anime_map, "load_mapping",
        lambda **kwargs: anime_map.MappingList(records=AOT_RECORDS),
        raising=False,
    )

    class Never:
        def __init__(self, *args, **kwargs) -> None:
            raise AssertionError("the Which-part sheet must not open here")

    monkeypatch.setattr(qt_media, "WhichPartDialog", Never, raising=False)
    # The shared identity verifies this title; the records below decide the
    # episode, which is all anime_map still does.
    monkeypatch.setattr(
        qt_anime_routing, "identify_tmdb",
        lambda *args, **kwargs: qt_anime_routing.Identity(
            verified=True, playable_anilist_id=16498,
        ),
        raising=False,
    )
    return None


@pytest.mark.parametrize(
    "season, number, anilist_id, anilist_episode",
    [
        (3, 1, 99147, 1),    # season 3's first entry
        (3, 13, 104578, 1),  # season 3 splits here; offset 12 restarts at 1
        (3, 22, 104578, 10),
        (1, 5, 16498, 5),
    ],
)
def test_a_real_mapping_plays_without_asking(app, real_mapping, season, number,
                                             anilist_id, anilist_episode):
    page = Page(app)
    page.widget._play_tmdb(AOT, season, episode(number))
    assert wait_for(app, lambda: page.urls)

    assert page.urls == [
        streaming.ani_url(anilist_id, anilist_episode, language="sub")
    ], f"S{season}E{number} should play AniList {anilist_id} episode {anilist_episode}"
    assert page.plain == []
    page.close()


def test_watch_now_without_a_season_uses_season_one_episode_one(app, real_mapping):
    """The hero's Play has no season chosen: S1E1, still no sheet."""
    page = Page(app)
    page.widget._play_card(AOT)
    assert wait_for(app, lambda: page.urls)

    assert page.urls == [streaming.ani_url(16498, 1, language="sub")]
    page.close()


def test_a_verified_anime_ignores_the_general_provider(app, real_mapping):
    """No anime provider saved: the general provider never gets this play."""
    page = Page(app, provider=streaming.DEFAULT_PROVIDER)
    page.widget._play_tmdb(AOT, 3, episode(1))
    assert wait_for(app, lambda: page.urls)

    assert page.urls == [streaming.ani_url(99147, 1, language="sub")]
    assert page.plain == [], (
        f"{streaming.DEFAULT_PROVIDER} must not play a verified anime"
    )
    page.close()


def test_a_heuristic_only_anime_keeps_the_selected_provider(app, monkeypatch):
    """Japanese + Animation with no list entry is a guess: no reroute at all."""
    monkeypatch.setattr(
        anime_map, "load_mapping",
        lambda **kwargs: anime_map.MappingList(),
        raising=False,
    )
    asked: list[tuple] = []
    monkeypatch.setattr(
        anime_map, "tmdb_to_anilist",
        lambda *a, **k: asked.append(a) or None,
        raising=False,
    )
    monkeypatch.setattr(
        qt_anime_routing, "identify_tmdb",
        lambda *args, **kwargs: qt_anime_routing.Identity(
            is_anime=True, verified=False,
        ),
        raising=False,
    )
    page = Page(app, provider="vidy")
    page.widget._play_tmdb(AOT, 3, episode(1))
    assert wait_for(app, lambda: page.plain)

    assert asked == [], "a guess must not reach the mapping lookup"
    assert page.plain[0][1] == "vidy"
    assert page.urls == []
    page.close()


def test_an_english_animation_is_not_anime_and_plays_as_today(app, monkeypatch):
    monkeypatch.setattr(
        anime_map, "load_mapping",
        lambda **kwargs: anime_map.MappingList(records=AOT_RECORDS),
        raising=False,
    )
    tmdb_client = Tmdb({"original_language": "en", "genres": [{"id": 16}]})
    unlisted = qt_media.Card(key="tv:999", title="An English Cartoon",
                             kind="tv", tmdb_id=999)
    page = Page(app, provider=streaming.DEFAULT_PROVIDER, tmdb=tmdb_client)
    page.widget._play_tmdb(unlisted, 3, episode(1))
    assert wait_for(app, lambda: page.plain)

    assert page.plain[0][1] == streaming.DEFAULT_PROVIDER
    assert page.urls == []
    assert tmdb_client.paths == [], "identity needs no details document"
    page.close()


def test_repeated_plays_read_the_details_document_once(app, real_mapping):
    page = Page(app, provider=streaming.DEFAULT_PROVIDER)
    page.widget._play_tmdb(AOT, 3, episode(1))
    assert wait_for(app, lambda: bool(page.urls))
    page.widget._play_tmdb(AOT, 3, episode(13))
    assert wait_for(app, lambda: len(page.urls) == 2)

    assert page.urls == [
        streaming.ani_url(99147, 1, language="sub"),
        streaming.ani_url(104578, 1, language="sub"),
    ]
    assert page.widget._tmdb.paths == [], "identity needs no details document"
    page.close()


# -- no TMDB client or API key ---------------------------------------------


def test_identity_runs_without_a_tmdb_client(app, stubbed):
    """A missing TMDB client must not disable anime identity routing."""
    _calls, state = stubbed
    state["verdict"] = Verdict(is_anime=True, verified=True)
    page = Page(app)
    page.widget._tmdb = None
    page.widget._play_tmdb(TV, 2, episode(3))
    assert wait_for(app, lambda: page.urls)

    assert page.urls == [streaming.ani_url(42, 5, language="sub")]
    assert page.plain == [], "a verified anime still plays on anipm.pk"
    page.close()


def test_an_unverified_title_keeps_the_provider_without_a_tmdb_client(app, stubbed):
    _calls, state = stubbed
    state["verdict"] = Verdict(is_anime=True, verified=False)
    page = Page(app, provider="vidy")
    page.widget._tmdb = None
    page.widget._play_tmdb(TV, 2, episode(3))
    assert wait_for(app, lambda: page.plain)

    assert page.plain[0][1] == "vidy", "an unverified title keeps the provider"
    assert page.urls == []
    page.close()


def test_a_resolution_failure_without_a_tmdb_client_is_an_error(app, stubbed):
    _calls, state = stubbed
    state["failed"] = True
    errors: list[str] = []
    page = Page(app)
    page.widget._tmdb = None
    page.widget._note_error = errors.append
    page.widget._play_tmdb(TV, 2, episode(3))
    assert wait_for(app, lambda: errors)

    assert "AniList entry" in errors[0]
    assert page.plain == [] and page.urls == [], "no fallback, no stream"
    page.close()
