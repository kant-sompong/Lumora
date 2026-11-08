"""The provider list supplied for this task, and the URLs their docs describe.

Every URL here is transcribed from the provider's own documentation; the tests
pin them so a later edit cannot silently point a player at the wrong route.
Nothing is contacted.
"""

from __future__ import annotations

import pytest

from lgl import streaming

NEW_PROVIDERS = ("vidy", "chillflix", "vidcore", "velarium", "afterdusk", "moonflix")


def test_every_supplied_provider_is_registered():
    for key in NEW_PROVIDERS:
        assert key in streaming.PROVIDERS
        assert key in streaming.SPECS
        assert key in streaming.LABELS


def test_no_duplicate_entries():
    assert len(streaming.PROVIDERS) == len(set(streaming.PROVIDERS))
    assert len(streaming.ANIME_PROVIDERS) == len(set(streaming.ANIME_PROVIDERS))
    assert set(streaming.SPECS) == set(streaming.PROVIDERS) | {"ani"}


def test_toflix_was_already_integrated_and_still_matches_its_docs():
    # https://toflix.co/embed - the same routes the app already generated.
    assert streaming.PROVIDERS.count("toflix") == 1
    assert streaming.movie_url("toflix", 315162) == "https://toflix.co/embed/movie/315162"
    assert streaming.episode_url("toflix", 1396, 1, 1) == (
        "https://toflix.co/embed/tv/1396/1/1"
    )


@pytest.mark.parametrize("provider,movie,tv", [
    ("vidy", "https://www.vidy.st/movie/315162", "https://www.vidy.st/tv/1396/1/1"),
    ("chillflix", "https://chillflix.lol/embed/movie/315162",
     "https://chillflix.lol/embed/tv/1396/1/1"),
    ("vidcore", "https://vidcore.io/movie/315162", "https://vidcore.io/tv/1396/1/1"),
    ("velarium", "https://velarium.cc/en/watch/movie/315162",
     "https://velarium.cc/en/watch/tv/1396-1-1"),
    ("afterdusk", "https://afterdusk.app/embed/movie/315162",
     "https://afterdusk.app/embed/tv/1396/1/1"),
    ("moonflix", "https://player.moonflix.website/movie/315162",
     "https://player.moonflix.website/tv/1396/1/1"),
])
def test_movie_and_episode_urls(provider, movie, tv):
    movie_url = streaming.movie_url(provider, 315162)
    episode_url = streaming.episode_url(provider, 1396, 1, 1)
    assert movie_url == movie
    assert episode_url == tv
    # The player refuses anything that leaves the provider's own host.
    assert streaming.validate_embed_url(provider, movie_url) == movie_url
    assert streaming.validate_embed_url(provider, episode_url) == episode_url


def test_season_and_episode_are_encoded_per_provider():
    assert streaming.episode_url("velarium", 94997, 2, 7) == (
        "https://velarium.cc/en/watch/tv/94997-2-7"
    )
    assert streaming.episode_url("moonflix", 93405, 1, 3) == (
        "https://player.moonflix.website/tv/93405/1/3"
    )


def test_vidy_is_the_only_new_provider_with_an_anime_route():
    assert streaming.has_anime_route("vidy") is True
    assert streaming.anime_url("vidy", 21, 3) == "https://www.vidy.st/anime/21/3"
    for key in ("chillflix", "vidcore", "velarium", "afterdusk", "moonflix"):
        assert streaming.has_anime_route(key) is False
        with pytest.raises(streaming.EmbedUrlError):
            streaming.anime_url(key, 21, 3)


def test_anime_providers_include_the_new_route_only_where_documented():
    assert "vidy" in streaming.ANIME_PROVIDERS
    for key in ("chillflix", "vidcore", "velarium", "afterdusk", "moonflix"):
        assert key not in streaming.ANILIST_ANIME_PROVIDERS


def test_existing_providers_keep_their_urls():
    assert streaming.movie_url("cinesrc", 603) == "https://cinesrc.st/embed/movie/603"
    assert streaming.movie_url("rivestream", 603) == (
        "https://www.rivestream.app/embed?type=movie&id=603"
    )
    assert streaming.movie_url("cinelite", 603) == "https://embed.cinelite.xyz/603"
    assert streaming.movie_url("framextv", 603) == "https://framextv.xyz/embed/603"
    assert streaming.movie_url("atlantic", 603) == "https://atlantic.st/embed/movie/603"
    assert streaming.ani_mal_url(16498, 1) == "https://ani.pm/embed/mal/16498/1/sub"
    assert streaming.fallback_providers("cinesrc")[0] == "cinesrc"
    assert streaming.clean_provider("vidy") == "vidy"
    assert streaming.is_valid_provider("moonflix") is True


def test_new_providers_reject_foreign_hosts():
    with pytest.raises(streaming.EmbedUrlError):
        streaming.validate_embed_url("vidy", "https://evil.example/movie/603")


def test_unknown_provider_still_raises():
    with pytest.raises(streaming.EmbedUrlError):
        streaming.provider_spec("vidy-evil")
