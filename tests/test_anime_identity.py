"""MAL is the authority: identity answers, and AniList only after MAL agrees.

Every lookup here is a fake, so no test touches MyAnimeList or AniList.  The
records are the real values for Attack on Titan (MAL 16498 / AniList 16498 for
season 1, MAL 35760 / AniList 99147 for season 3), which is exactly why the two
identifiers must never be swapped.
"""

from __future__ import annotations

import inspect


from lgl import anime_identity as identity
from lgl.anime_identity import (
    IdentityStatus,
    MalMatch,
    Reason,
    identity_key,
    identify,
    identify_tmdb,
    matches_catalogue,
)

NOW = 1_000_000.0

AOT_S3 = MalMatch(
    mal_id=35760,
    title="Shingeki no Kyojin Season 3",
    media_type="tv",
    start_date="2018-07-23",
    episodes=12,
    alternative_titles=("Attack on Titan Season 3",),
)
AOT_S1 = MalMatch(
    mal_id=16498,
    title="Shingeki no Kyojin",
    media_type="tv",
    start_date="2013-04-07",
    episodes=25,
    alternative_titles=("Attack on Titan",),
)


class FakeMal:
    """Records every call; can fail on purpose."""

    def __init__(self, entries=(), *, error: str = "") -> None:
        self.entries = {entry.mal_id: entry for entry in entries}
        self.error = error
        self.searches: list[str] = []
        self.details_calls: list[int] = []

    def search(self, title: str, *, limit: int = 5) -> list[int]:
        if self.error:
            raise identity.LookupUnavailable(self.error)
        self.searches.append(title)
        return list(self.entries)[:limit]

    def details(self, mal_id: int) -> MalMatch:
        if self.error:
            raise identity.LookupUnavailable(self.error)
        self.details_calls.append(mal_id)
        entry = self.entries.get(mal_id)
        if entry is None:
            raise identity.LookupUnavailable(f"no MAL entry {mal_id}")
        return entry


class FakeAniList:
    """Only AniList's own links; unset ids are "no link"."""

    def __init__(self, links=None, mal_links=None, *, error: str = "") -> None:
        self.links = dict(links or {})
        self.mal_links = dict(mal_links or {})
        self.error = error
        self.asked_by_mal: list[int] = []
        self.asked_by_anilist: list[int] = []

    def anilist_for_mal(self, mal_id: int) -> int | None:
        if self.error:
            raise identity.LookupUnavailable(self.error)
        self.asked_by_mal.append(mal_id)
        return self.links.get(mal_id)

    def mal_for_anilist(self, anilist_id: int) -> int | None:
        if self.error:
            raise identity.LookupUnavailable(self.error)
        self.asked_by_anilist.append(anilist_id)
        return self.mal_links.get(anilist_id)


def make(tmp_path, name="identity.json"):
    return identity.IdentityCache(tmp_path / name)


# -- verified ---------------------------------------------------------------

def test_a_mal_entry_verifies_and_its_anilist_id_is_resolved_separately(tmp_path):
    mal = FakeMal([AOT_S3])
    anilist = FakeAniList({35760: 99147})
    cache = make(tmp_path)

    result = identify(
        title="Attack on Titan Season 3", kind="tv", year="2018",
        mal=mal, anilist=anilist, cache=cache, now=NOW,
    )

    assert result.status is IdentityStatus.VERIFIED_ANIME
    assert result.verified is True
    assert result.mal_id == 35760, "the MAL id of the matched entry"
    assert result.anilist_id == 99147, "the AniList id, resolved separately"
    assert result.anilist_id != result.mal_id, "the two identifiers never merge"
    assert result.anilist.validated is True
    assert result.anilist.source == Reason.ANILIST_MAL_LINK
    assert result.playable_anilist_id == 99147
    assert result.mal.title == AOT_S3.title
    assert anilist.asked_by_mal == [35760]


def test_an_existing_mal_id_needs_no_search(tmp_path):
    mal = FakeMal([AOT_S1])
    result = identify(
        title="whatever", mal_id=16498, mal=mal,
        anilist=FakeAniList({16498: 16498}), cache=make(tmp_path), now=NOW,
    )

    assert result.verified is True
    assert result.mal_id == 16498
    assert mal.searches == [], "an id the caller already has is used directly"
    assert mal.details_calls == [16498]


# -- unverified -------------------------------------------------------------

def test_a_near_title_is_not_a_match(tmp_path):
    mal = FakeMal([AOT_S1])
    anilist = FakeAniList({16498: 16498})
    result = identify(
        title="Attack on Titan Final Season", kind="tv", year="2013",
        mal=mal, anilist=anilist, cache=make(tmp_path), now=NOW,
    )

    assert result.status is IdentityStatus.UNVERIFIED
    assert result.mal_id is None
    assert result.reason == Reason.NO_MAL_MATCH
    assert anilist.asked_by_mal == [], "no MAL match means no AniList guess"


def test_a_year_mismatch_is_not_a_match(tmp_path):
    mal = FakeMal([AOT_S1])
    result = identify(
        title="Attack on Titan", kind="tv", year="2019",
        mal=mal, anilist=FakeAniList(), cache=make(tmp_path), now=NOW,
    )

    assert result.status is IdentityStatus.UNVERIFIED
    assert result.reason == Reason.NO_MAL_MATCH


def test_a_lookup_failure_is_unverified_and_not_cached(tmp_path):
    cache = make(tmp_path)
    broken = FakeMal(error="network down")
    result = identify(
        title="Attack on Titan", kind="tv", year="2013",
        mal=broken, anilist=FakeAniList(), cache=cache, now=NOW,
    )

    assert result.status is IdentityStatus.UNVERIFIED
    assert result.lookup_failed is True
    assert result.reason == Reason.LOOKUP_UNAVAILABLE
    assert len(cache) == 0, "an unavailable lookup is retried, not remembered"

    # The next attempt uses a working MAL and can still verify.
    retry = identify(
        title="Attack on Titan", kind="tv", year="2013",
        mal=FakeMal([AOT_S1]), anilist=FakeAniList({16498: 16498}),
        cache=cache, now=NOW,
    )
    assert retry.verified is True


def test_two_equally_good_entries_are_ambiguous(tmp_path):
    twin = MalMatch(mal_id=99999, title="Attack on Titan", media_type="tv",
                    start_date="2013-04-07")
    mal = FakeMal([AOT_S1, twin])
    result = identify(
        title="Attack on Titan", kind="tv", year="2013",
        mal=mal, anilist=FakeAniList({16498: 16498}),
        cache=make(tmp_path), now=NOW,
    )

    assert result.status is IdentityStatus.UNVERIFIED
    assert result.reason == Reason.AMBIGUOUS_MAL_MATCH
    assert [candidate.mal_id for candidate in result.candidates] == [16498, 99999]
    assert result.mal_id is None


def test_a_movie_kind_never_verifies_a_tv_entry(tmp_path):
    result = identify(
        title="Attack on Titan", kind="movie", year="2013",
        mal=FakeMal([AOT_S1]), anilist=FakeAniList(),
        cache=make(tmp_path), now=NOW,
    )
    assert result.status is IdentityStatus.UNVERIFIED
    assert result.reason == Reason.NO_MAL_MATCH


# -- resolution failure -----------------------------------------------------

def test_mal_verified_with_no_anilist_link_is_a_resolution_failure(tmp_path):
    result = identify(
        title="Attack on Titan Season 3", kind="tv", year="2018",
        mal=FakeMal([AOT_S3]), anilist=FakeAniList(),
        crosswalk=lambda _mal_id: (), cache=make(tmp_path), now=NOW,
    )

    assert result.status is IdentityStatus.RESOLUTION_FAILURE
    assert result.resolution_failed is True
    assert result.mal_id == 35760, "the MAL id is kept: MAL did verify"
    assert result.anilist_id is None, "no id is invented"
    assert result.playable_anilist_id is None
    assert result.reason == Reason.ANILIST_MISSING


def test_an_ambiguous_crosswalk_is_a_resolution_failure(tmp_path):
    result = identify(
        title="Attack on Titan Season 3", kind="tv", year="2018",
        mal=FakeMal([AOT_S3]), anilist=FakeAniList(),
        crosswalk=lambda _mal_id: (99147, 104578),
        cache=make(tmp_path), now=NOW,
    )

    assert result.status is IdentityStatus.RESOLUTION_FAILURE
    assert result.anilist_id is None
    assert result.anilist.candidates == (99147, 104578)
    assert result.reason == Reason.ANILIST_AMBIGUOUS


def test_a_crosswalk_association_for_another_entry_is_rejected(tmp_path):
    """The crosswalk says 99147; AniList says 99147 is a different MAL id."""
    anilist = FakeAniList(mal_links={99147: 16498})
    result = identify(
        title="Attack on Titan Season 3", kind="tv", year="2018",
        mal=FakeMal([AOT_S3]), anilist=anilist,
        crosswalk=lambda _mal_id: (99147,), cache=make(tmp_path), now=NOW,
    )

    assert result.status is IdentityStatus.RESOLUTION_FAILURE
    assert result.anilist_id is None, "a wrong association is never played"
    assert result.anilist.candidates == (99147,)
    assert result.reason == Reason.ANILIST_UNVALIDATED
    assert anilist.asked_by_anilist == [99147], "it was checked, then refused"


def test_a_validated_crosswalk_candidate_is_accepted(tmp_path):
    anilist = FakeAniList(mal_links={99147: 35760})
    result = identify(
        title="Attack on Titan Season 3", kind="tv", year="2018",
        mal=FakeMal([AOT_S3]), anilist=anilist,
        crosswalk=lambda _mal_id: (99147,), cache=make(tmp_path), now=NOW,
    )

    assert result.verified is True
    assert result.anilist_id == 99147
    assert result.anilist.source == Reason.ANILIST_CROSSWALK
    assert result.anilist.validated is True


def test_a_crosswalk_that_repeats_the_mal_id_is_not_an_anilist_id(tmp_path):
    result = identify(
        title="Attack on Titan Season 3", kind="tv", year="2018",
        mal=FakeMal([AOT_S3]), anilist=FakeAniList(),
        crosswalk=lambda _mal_id: (35760,), cache=make(tmp_path), now=NOW,
    )

    assert result.status is IdentityStatus.RESOLUTION_FAILURE
    assert result.anilist_id is None
    assert result.reason == Reason.ANILIST_MISSING


def test_an_anilist_outage_does_not_turn_into_a_guess(tmp_path):
    result = identify(
        title="Attack on Titan Season 3", kind="tv", year="2018",
        mal=FakeMal([AOT_S3]), anilist=FakeAniList(error="offline"),
        crosswalk=lambda _mal_id: (99147,), cache=make(tmp_path), now=NOW,
    )

    assert result.status is IdentityStatus.RESOLUTION_FAILURE
    assert result.anilist_id is None
    assert result.anilist.candidates == (99147,)


# -- heuristics are gone ----------------------------------------------------

def test_the_api_takes_no_language_or_genre_signal():
    params = inspect.signature(identity.identify).parameters
    assert "original_language" not in params
    assert "genre_ids" not in params
    assert "genres" not in params


def test_a_japanese_animation_looking_title_is_not_auto_verified(tmp_path):
    """No title, language or genre signal can verify: only a MAL match can."""
    result = identify(
        title="Some Anime-Looking Show", kind="tv", year="2020",
        mal=FakeMal([]), anilist=FakeAniList(), cache=make(tmp_path), now=NOW,
    )
    assert result.status is IdentityStatus.UNVERIFIED
    assert result.verified is False


def test_matching_is_exact_on_title_and_compatible_on_kind():
    assert matches_catalogue(AOT_S1, title="attack on titan!", year="2013", kind="tv")
    assert not matches_catalogue(AOT_S1, title="Attack on Titan Season 2", kind="tv")
    assert matches_catalogue(
        MalMatch(mal_id=1, title="Film", media_type="movie"),
        title="Film", kind="movie",
    )
    assert not matches_catalogue(
        MalMatch(mal_id=1, title="Film", media_type="tv"),
        title="Film", kind="movie",
    )


# -- cache ------------------------------------------------------------------

def test_a_verified_answer_is_cached_and_reused(tmp_path):
    cache = make(tmp_path)
    first = identify(
        title="Attack on Titan", kind="tv", year="2013",
        mal=FakeMal([AOT_S1]), anilist=FakeAniList({16498: 16498}),
        cache=cache, now=NOW,
    )
    assert first.verified is True

    second = identify(
        title="Attack on Titan", kind="tv", year="2013",
        mal=FakeMal(error="must not be called"),
        anilist=FakeAniList(error="must not be called"),
        cache=cache, now=NOW + 60,
    )
    assert second.status is IdentityStatus.VERIFIED_ANIME
    assert second.anilist_id == 16498
    assert second.mal_id == 16498


def test_an_unverified_answer_is_cached_too(tmp_path):
    cache = make(tmp_path)
    identify(
        title="Not Anime", kind="tv", year="2013",
        mal=FakeMal([]), anilist=FakeAniList(), cache=cache, now=NOW,
    )
    second = identify(
        title="Not Anime", kind="tv", year="2013",
        mal=FakeMal(error="must not be called"), cache=cache, now=NOW + 60,
    )
    assert second.status is IdentityStatus.UNVERIFIED
    assert second.reason == Reason.NO_MAL_MATCH


def test_the_cache_expires(tmp_path):
    cache = make(tmp_path)
    identify(
        title="Not Anime", kind="tv", year="2013",
        mal=FakeMal([]), anilist=FakeAniList(), cache=cache, now=NOW,
    )
    assert cache.get(identity_key(kind="tv", title="Not Anime", year="2013"),
                     now=NOW) is not None
    assert cache.get(
        identity_key(kind="tv", title="Not Anime", year="2013"),
        now=NOW + identity.TTL_OTHER + 1,
    ) is None


def test_the_disk_cache_survives_a_new_instance(tmp_path):
    path = tmp_path / "identity.json"
    first = identity.IdentityCache(path)
    identify(
        title="Attack on Titan", kind="tv", year="2013",
        mal=FakeMal([AOT_S1]), anilist=FakeAniList({16498: 16498}),
        cache=first, now=NOW,
    )
    assert path.exists()

    reloaded = identity.IdentityCache(path)
    cached = reloaded.get(
        identity_key(kind="tv", title="Attack on Titan", year="2013"),
        now=NOW + 60,
    )
    assert cached is not None
    assert cached.status is IdentityStatus.VERIFIED_ANIME
    assert cached.mal_id == 16498
    assert cached.anilist_id == 16498
    assert cached.mal is not None and cached.mal.title == AOT_S1.title


def test_identify_tmdb_keys_by_tmdb_id(tmp_path):
    cache = make(tmp_path)
    first = identify_tmdb(
        "tv", 1429, title="Attack on Titan", year="2013",
        mal=FakeMal([AOT_S1]), anilist=FakeAniList({16498: 16498}),
        cache=cache, now=NOW,
    )
    assert first.verified is True

    second = identify_tmdb(
        "tv", 1429, title="Different title text", year="2013",
        mal=FakeMal(error="must not be called"),
        anilist=FakeAniList(error="must not be called"),
        cache=cache, now=NOW + 60,
    )
    assert second.mal_id == 16498, "the TMDB id is the cache identity"


def test_a_failed_mal_id_lookup_is_unverified_not_a_crash(tmp_path):
    result = identify(
        title="", mal_id=35760, mal=FakeMal(error="rate limited"),
        cache=make(tmp_path), now=NOW,
    )
    assert result.status is IdentityStatus.UNVERIFIED
    assert result.lookup_failed is True
    assert result.reason == Reason.LOOKUP_UNAVAILABLE


def test_resolve_anilist_can_be_called_directly():
    linked = identity.resolve_anilist(35760, anilist=FakeAniList({35760: 99147}))
    assert linked.ok is True and linked.anilist_id == 99147

    missing = identity.resolve_anilist(
        35760, anilist=FakeAniList(), crosswalk=lambda _mal_id: ()
    )
    assert missing.ok is False
    assert missing.anilist_id is None
    assert missing.reason == Reason.ANILIST_MISSING


def test_settings_are_passed_to_the_default_mal_lookup(tmp_path, monkeypatch):
    """Frontends pass their settings object; the key comes from settings/env."""
    seen = []

    def fake_client_id(*, settings=None):
        seen.append(settings)
        return ""

    monkeypatch.setattr(identity.mal_api, "load_client_id", fake_client_id)
    marker = object()
    result = identify(
        title="Attack on Titan", kind="tv", year="2013",
        settings=marker, cache=make(tmp_path), now=NOW,
    )
    assert result.status is IdentityStatus.UNVERIFIED
    assert result.lookup_failed is True
    assert seen == [marker], "the app's settings reach the MAL client lookup"


def test_a_given_lookup_ignores_settings(tmp_path):
    mal = FakeMal([AOT_S1])
    result = identify(
        title="Attack on Titan", kind="tv", year="2013",
        mal=mal, settings=object(), anilist=FakeAniList({16498: 16498}),
        cache=make(tmp_path), now=NOW,
    )
    assert result.verified is True
    assert mal.searches == ["Attack on Titan"]


# -- an AniList detail page (has an AniList id, no MAL id) -------------------

def test_an_anilist_page_is_verified_through_its_own_mal_link(tmp_path):
    mal = FakeMal([AOT_S3])
    anilist = FakeAniList(mal_links={99147: 35760})
    result = identify(
        title="Attack on Titan Season 3", anilist_id=99147,
        mal=mal, anilist=anilist, cache=make(tmp_path), now=NOW,
    )

    assert result.verified is True
    assert result.mal_id == 35760, "MAL verified the entry AniList linked to"
    assert result.anilist_id == 99147, "the page's own validated AniList id"
    assert result.anilist.source == Reason.ANILIST_MAL_LINK
    assert mal.searches == [], "an id is verified by id, not by title search"
    assert anilist.asked_by_anilist == [99147]


def test_an_anilist_id_with_no_mal_link_verifies_nothing(tmp_path):
    mal = FakeMal(error="must not be called")
    result = identify(
        title="Some AniList-only title", anilist_id=777,
        mal=mal, anilist=FakeAniList(), cache=make(tmp_path), now=NOW,
    )

    assert result.status is IdentityStatus.UNVERIFIED
    assert result.lookup_failed is False
    assert result.reason == Reason.NO_MAL_MATCH
    assert result.mal_id is None and result.anilist_id is None
    assert mal.details_calls == [], "no MAL ids means no MAL lookup"


def test_an_anilist_lookup_failure_is_unverified(tmp_path):
    result = identify(
        title="", anilist_id=99147, mal=FakeMal([AOT_S3]),
        anilist=FakeAniList(error="offline"), cache=make(tmp_path), now=NOW,
    )
    assert result.status is IdentityStatus.UNVERIFIED
    assert result.lookup_failed is True
    assert result.reason == Reason.LOOKUP_UNAVAILABLE


def test_an_anilist_page_whose_mal_entry_fails_is_unverified(tmp_path):
    result = identify(
        title="", anilist_id=99147,
        mal=FakeMal(error="rate limited"),
        anilist=FakeAniList(mal_links={99147: 35760}),
        cache=make(tmp_path), now=NOW,
    )
    assert result.status is IdentityStatus.UNVERIFIED
    assert result.lookup_failed is True
    assert result.mal_id is None


def test_an_anilist_page_answer_is_cached_by_anilist_id(tmp_path):
    cache = make(tmp_path)
    identify(
        title="", anilist_id=99147, mal=FakeMal([AOT_S3]),
        anilist=FakeAniList(mal_links={99147: 35760}),
        cache=cache, now=NOW,
    )
    again = identify(
        title="", anilist_id=99147,
        mal=FakeMal(error="must not be called"),
        anilist=FakeAniList(error="must not be called"),
        cache=cache, now=NOW + 60,
    )
    assert again.verified is True
    assert again.mal_id == 35760 and again.anilist_id == 99147


def test_a_given_mal_id_wins_over_an_anilist_id(tmp_path):
    anilist = FakeAniList({35760: 99147})
    result = identify(
        title="", mal_id=35760, anilist_id=1,
        mal=FakeMal([AOT_S3]), anilist=anilist,
        cache=make(tmp_path), now=NOW,
    )
    assert result.verified is True
    assert result.anilist_id == 99147
    assert anilist.asked_by_anilist == [], "the ignored id is never asked"
