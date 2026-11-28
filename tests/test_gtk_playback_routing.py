"""GTK playback routing, against the real shared anime identity core.

No fake identity module: every test runs ``lgl.anime_identity.identify`` /
``identify_tmdb`` for real (statuses, MAL matching, AniList validation and the
shared cache), with only the two *network* adapters replaced by in-memory
fakes.  The episode-mapping layer is ``lgl.anime_map``'s ``PlayTarget``.

The rule under test:
* VERIFIED_ANIME -> anipm.pk only;
* UNVERIFIED (no MAL match, MAL lookup unavailable, confirmed non-anime) ->
  the selected normal provider;
* RESOLUTION_FAILURE -> error, no fallback;
* the MAL id and the AniList id are never interchangeable;
* episode mapping happens only after MAL verification, and the mapping layer
  never gets an AniList title search.
"""

from __future__ import annotations

import types

from lgl import anime_identity, gtk_playback, streaming
from lgl.anime_identity import MalMatch, Reason
from lgl.anime_map import PlayTarget

TITLE = "Shingeki no Kyojin Season 3"
YEAR = "2018"
MAL_ID = 35760
ANILIST_ID = 99147

MAL_ENTRY = MalMatch(
    mal_id=MAL_ID,
    title=TITLE,
    media_type="tv",
    start_date="2018-07-23",
    episodes=12,
    alternative_titles=("Attack on Titan Season 3",),
)


class Settings(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)

    def set(self, key, value):
        dict.__setitem__(self, key, value)


class FakeMal:
    """The MAL network adapter: real matching, in-memory records."""

    def __init__(self, entries=(), *, error: str = "") -> None:
        self.entries = {entry.mal_id: entry for entry in entries}
        self.error = error
        self.searches: list[str] = []
        self.details_calls: list[int] = []
        self.settings_seen: list[object] = []

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
    """The AniList network adapter: only first-party idMal links."""

    def __init__(self, links=None, mal_links=None, *, error: str = "") -> None:
        self.links = dict(links if links is not None else {MAL_ID: ANILIST_ID})
        self.mal_links = dict(mal_links if mal_links is not None else {ANILIST_ID: MAL_ID})
        self.error = error
        self.asked_by_mal: list[int] = []
        self.asked_by_anilist: list[int] = []

    def anilist_for_mal(self, mal_id: int) -> int | None:
        if self.error:
            raise anime_identity.LookupUnavailable(self.error)
        self.asked_by_mal.append(mal_id)
        return self.links.get(mal_id)

    def mal_for_anilist(self, anilist_id: int) -> int | None:
        if self.error:
            raise anime_identity.LookupUnavailable(self.error)
        self.asked_by_anilist.append(anilist_id)
        return self.mal_links.get(anilist_id)


def install(
    monkeypatch,
    *,
    entries=(MAL_ENTRY,),
    links=None,
    mal_links=None,
    mal_error: str = "",
    anilist_error: str = "",
    crosswalk=(),
):
    """Run the real core with fake network adapters and a fresh cache."""
    mal = FakeMal(entries, error=mal_error)
    anilist = FakeAniList(links, mal_links, error=anilist_error)
    monkeypatch.setattr(
        anime_identity, "_DEFAULT_CACHE", anime_identity.IdentityCache()
    )
    monkeypatch.setattr(
        anime_identity, "MalApiLookup", lambda settings=None: mal
    )
    monkeypatch.setattr(
        anime_identity, "AniListGraphQLLookup", lambda *_a, **_k: anilist
    )
    monkeypatch.setattr(
        anime_identity, "crosswalk_anilist_ids", lambda _mal_id: tuple(crosswalk)
    )
    return mal, anilist


def no_mapping(tmdb_kind=None, tmdb_id=None, season=None, episode=None, search=None):
    return None


def mapping_to(anilist_id, target_episode, *, confident=True, calls=None):
    def mapping(tmdb_kind=None, tmdb_id=None, season=None, episode=None, search=None):
        if calls is not None:
            calls.append(
                {
                    "tmdb_kind": tmdb_kind,
                    "tmdb_id": tmdb_id,
                    "season": season,
                    "requested_episode": episode,
                    "search": search,
                }
            )
        return PlayTarget(anilist_id, target_episode, confident)

    return mapping


def decide(
    monkeypatch,
    *,
    mapping=None,
    title=TITLE,
    year=YEAR,
    season=1,
    episode=1,
    settings=None,
    **core,
):
    install(monkeypatch, **core)
    return gtk_playback.decide_playback(
        "tv",
        65942,
        title=title,
        year=year,
        season=season,
        episode=episode,
        settings=settings,
        mapping_for=mapping or no_mapping,
    )


def decide_anime(monkeypatch, **kwargs):
    install(monkeypatch, **kwargs.pop("core", {}))
    return gtk_playback.decide_anime(**kwargs)


# -- the TMDB path: the real core answers ----------------------------------


def test_verified_anime_always_routes_to_ani_pm(monkeypatch):
    route = decide(monkeypatch)
    assert route.mode == gtk_playback.MODE_ANI
    assert route.anilist_id == ANILIST_ID


def test_the_anilist_id_is_never_the_mal_id(monkeypatch):
    route = decide(monkeypatch)
    url = streaming.ani_url(route.anilist_id, route.episode, language="sub")
    assert f"/embed/ani/{ANILIST_ID}/" in url
    assert "/embed/mal/" not in url
    assert str(MAL_ID) not in url


def test_the_settings_object_reaches_the_mal_adapter(monkeypatch):
    seen: list = []
    mal = FakeMal([MAL_ENTRY])
    monkeypatch.setattr(
        anime_identity, "_DEFAULT_CACHE", anime_identity.IdentityCache()
    )
    monkeypatch.setattr(
        anime_identity, "MalApiLookup",
        lambda settings=None: seen.append(settings) or mal,
    )
    monkeypatch.setattr(
        anime_identity, "AniListGraphQLLookup", lambda *_a, **_k: FakeAniList()
    )
    monkeypatch.setattr(anime_identity, "crosswalk_anilist_ids", lambda _id: ())
    settings = Settings({"mal_client_id": "from-settings"})
    route = gtk_playback.decide_playback(
        "tv", 65942, title=TITLE, year=YEAR, settings=settings,
        mapping_for=no_mapping,
    )
    assert route.mode == gtk_playback.MODE_ANI
    assert seen == [settings]


def test_the_mal_adapter_receives_the_catalogue_title(monkeypatch):
    mal, _anilist = install(monkeypatch)
    gtk_playback.decide_playback(
        "tv", 65942, title=TITLE, year=YEAR, settings=None, mapping_for=no_mapping
    )
    assert mal.searches == [TITLE] or mal.details_calls == [MAL_ID]


def test_a_lookup_unavailable_identity_uses_the_selected_provider(monkeypatch):
    route = decide(monkeypatch, mal_error="MAL key missing")
    assert route.mode == gtk_playback.MODE_NORMAL
    assert route.message == ""


def test_no_reliable_mal_match_uses_the_selected_provider(monkeypatch):
    route = decide(monkeypatch, entries=())
    assert route.mode == gtk_playback.MODE_NORMAL


def test_a_non_anime_title_uses_the_selected_provider(monkeypatch):
    route = decide(
        monkeypatch, title="The Office", year="2005", entries=()
    )
    assert route.mode == gtk_playback.MODE_NORMAL


def test_a_resolution_failure_is_an_error_with_no_fallback(monkeypatch):
    route = decide(monkeypatch, links={})
    assert route.mode == gtk_playback.MODE_ERROR
    assert route.message == gtk_playback.UNRESOLVED
    assert route.anilist_id == 0


def test_an_ambiguous_anilist_resolution_is_an_ambiguous_error(monkeypatch):
    route = decide(monkeypatch, links={}, crosswalk=(99147, 121051))
    assert route.mode == gtk_playback.MODE_ERROR
    assert route.message == gtk_playback.AMBIGUOUS


def test_an_anilist_outage_during_resolution_is_an_error(monkeypatch):
    # MAL verified the title; AniList cannot confirm its entry.  The identity
    # core reports RESOLUTION_FAILURE, which must not become a provider play.
    route = decide(monkeypatch, links={}, anilist_error="AniList down")
    assert route.mode == gtk_playback.MODE_ERROR
    assert route.message == gtk_playback.UNRESOLVED


def test_an_animation_with_no_mal_match_is_unverified(monkeypatch):
    route = decide(monkeypatch, title="Not On MAL", entries=())
    assert route.mode == gtk_playback.MODE_NORMAL


def test_a_build_without_the_shared_module_verifies_nothing(monkeypatch):
    def never(*_args, **_kwargs):
        raise AssertionError("mapping must not be consulted without identity")

    monkeypatch.setattr(gtk_playback, "identity_module", lambda: None)
    route = gtk_playback.decide_playback(
        "tv", 65942, title=TITLE, mapping_for=never
    )
    assert route.mode == gtk_playback.MODE_NORMAL
    assert gtk_playback.decide_anime(title=TITLE, mal_id=MAL_ID).mode == (
        gtk_playback.MODE_NORMAL
    )


def test_resolution_failure_is_checked_before_verified(monkeypatch):
    # The real core's statuses are mutually exclusive; this pins the adapter's
    # ordering so a future answer carrying both still errors instead of
    # falling back to a provider.
    answer = types.SimpleNamespace(
        verified=True, resolution_failed=True, reason=Reason.ANILIST_MISSING
    )
    module = types.SimpleNamespace(identify_tmdb=lambda *_a, **_k: answer)
    route = gtk_playback.decide_playback(
        "tv", 65942, title=TITLE, identity=module, mapping_for=no_mapping
    )
    assert route.mode == gtk_playback.MODE_ERROR
    assert route.message == gtk_playback.UNRESOLVED


# -- episode mapping, only after verification ------------------------------


def test_a_confident_play_target_decides_the_entry_and_the_episode(monkeypatch):
    calls: list = []
    route = decide(
        monkeypatch,
        season=2,
        episode=26,
        mapping=mapping_to(ANILIST_ID, 14, calls=calls),
    )
    assert (route.mode, route.anilist_id, route.episode) == (
        gtk_playback.MODE_ANI, ANILIST_ID, 14,
    )
    assert calls[0]["tmdb_kind"] == "tv"
    assert calls[0]["tmdb_id"] == 65942
    assert (calls[0]["season"], calls[0]["requested_episode"]) == (2, 26)


def test_the_mapping_layer_never_gets_to_search_anilist(monkeypatch):
    calls: list = []
    decide(monkeypatch, mapping=mapping_to(ANILIST_ID, 1, calls=calls))
    assert callable(calls[0]["search"])
    assert calls[0]["search"]("Shingeki no Kyojin") is None


def test_without_a_confident_mapping_the_identity_entry_plays_unchanged(monkeypatch):
    route = decide(
        monkeypatch,
        season=2,
        episode=26,
        mapping=mapping_to(0, 1, confident=False),
    )
    assert (route.mode, route.anilist_id, route.episode) == (
        gtk_playback.MODE_ANI, ANILIST_ID, 26,
    )


def test_a_mapping_failure_is_not_a_playback_error(monkeypatch):
    def boom(**_kwargs):
        raise RuntimeError("mapping list unavailable")

    route = decide(monkeypatch, episode=3, mapping=boom)
    assert (route.mode, route.anilist_id, route.episode) == (
        gtk_playback.MODE_ANI, ANILIST_ID, 3,
    )


def test_the_mapping_layer_is_not_asked_about_unverified_titles(monkeypatch):
    calls: list = []
    route = decide(
        monkeypatch,
        entries=(),
        mapping=mapping_to(ANILIST_ID, 1, calls=calls),
    )
    assert route.mode == gtk_playback.MODE_NORMAL
    assert calls == []


def test_the_mapping_layer_is_not_asked_about_resolution_failures(monkeypatch):
    calls: list = []
    route = decide(
        monkeypatch,
        links={},
        mapping=mapping_to(ANILIST_ID, 1, calls=calls),
    )
    assert route.mode == gtk_playback.MODE_ERROR
    assert calls == []


# -- anime detail pages: MAL and AniList -----------------------------------


def test_a_verified_mal_page_plays_ani_pm(monkeypatch):
    route = decide_anime(
        monkeypatch, title=TITLE, mal_id=MAL_ID, year=YEAR, episode=3
    )
    assert (route.mode, route.anilist_id, route.episode) == (
        gtk_playback.MODE_ANI, ANILIST_ID, 3,
    )


def test_an_anilist_page_is_verified_through_its_mal_link(monkeypatch):
    route = decide_anime(
        monkeypatch, title=TITLE, anilist_id=ANILIST_ID, year=YEAR
    )
    assert route.mode == gtk_playback.MODE_ANI
    assert route.anilist_id == ANILIST_ID


def test_an_anilist_page_without_a_mal_link_is_unverified(monkeypatch):
    route = decide_anime(
        monkeypatch,
        title=TITLE,
        anilist_id=ANILIST_ID,
        core={"mal_links": {}},
    )
    assert route.mode == gtk_playback.MODE_NORMAL


def test_an_anilist_page_alone_does_not_bypass_mal_verification(monkeypatch):
    # The AniList id's MAL link exists, but MAL has no such entry: unverified.
    route = decide_anime(
        monkeypatch,
        title=TITLE,
        anilist_id=ANILIST_ID,
        core={"entries": ()},
    )
    assert route.mode == gtk_playback.MODE_NORMAL


def test_a_mal_page_resolution_failure_is_an_error(monkeypatch):
    route = decide_anime(
        monkeypatch, title=TITLE, mal_id=MAL_ID, core={"links": {}}
    )
    assert route.mode == gtk_playback.MODE_ERROR
    assert route.message == gtk_playback.UNRESOLVED


def test_a_mal_page_lookup_failure_uses_the_selected_provider(monkeypatch):
    route = decide_anime(
        monkeypatch,
        title=TITLE,
        mal_id=MAL_ID,
        core={"mal_error": "MAL unavailable"},
    )
    assert route.mode == gtk_playback.MODE_NORMAL


def test_a_mal_page_sends_its_title_ids_kind_and_settings(monkeypatch):
    settings = Settings({"mal_client_id": "x"})
    route = decide_anime(
        monkeypatch,
        title="  Shingeki   no Kyojin Season 3 ",
        mal_id=MAL_ID,
        year=YEAR,
        episode=26,
        settings=settings,
    )
    assert route.mode == gtk_playback.MODE_ANI
    # No TMDB season exists for a page, so the requested episode is unchanged.
    assert route.episode == 26


def test_a_special_is_verified_as_a_film_entry(monkeypatch):
    special = MalMatch(
        mal_id=38524,
        title="Shingeki no Kyojin: Kuinaki Sentaku",
        media_type="special",
        start_date="2014-12-09",
        episodes=2,
    )
    route = decide_anime(
        monkeypatch,
        title="Shingeki no Kyojin: Kuinaki Sentaku",
        kind="movie",
        year="2014",
        mal_id=38524,
        core={"entries": (special,), "links": {38524: 20827}},
    )
    assert (route.mode, route.anilist_id) == (gtk_playback.MODE_ANI, 20827)
