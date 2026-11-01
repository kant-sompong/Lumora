"""frameXTV embed URLs, built from the two documented formats.

Movies:      ``https://framextv.xyz/embed/{tmdbId}``
TV episodes: ``https://framextv.xyz/embed/{tmdbId}/{season}/{episode}``

Nothing else is assumed: no undocumented parameters, no "download" URLs, no
claim that a title is available.  The app embeds exactly this URL and the site
decides what it can play.
"""

from __future__ import annotations

EMBED_ORIGIN = "https://framextv.xyz"
MOVIE_PATH = "/embed/{tmdb_id}"
EPISODE_PATH = "/embed/{tmdb_id}/{season}/{episode}"


def _positive(value: object, label: str) -> int:
    """A positive integer id/number, or ``ValueError`` with a useful message."""
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a positive number.")
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
    else:
        raise ValueError(f"{label} must be a positive number.")
    if number <= 0:
        raise ValueError(f"{label} must be a positive number.")
    return number


def movie_url(tmdb_id: object) -> str:
    """Embed URL for a movie."""
    return EMBED_ORIGIN + MOVIE_PATH.format(tmdb_id=_positive(tmdb_id, "The TMDB id"))


def episode_url(tmdb_id: object, season: object, episode: object) -> str:
    """Embed URL for one TV episode."""
    return EMBED_ORIGIN + EPISODE_PATH.format(
        tmdb_id=_positive(tmdb_id, "The TMDB id"),
        season=_positive(season, "The season number"),
        episode=_positive(episode, "The episode number"),
    )


def embed_url(
    kind: str,
    tmdb_id: object,
    *,
    season: object | None = None,
    episode: object | None = None,
) -> str:
    """The embed URL for a ``"movie"`` or a ``"tv"`` title.

    A movie with season/episode numbers is a programming mistake, not something
    to guess about, so it raises instead of silently dropping them.
    """
    chosen = str(kind or "").strip().lower()
    if chosen not in ("movie", "tv"):
        raise ValueError("Only 'movie' and 'tv' can be played.")
    if chosen == "movie":
        if season is not None or episode is not None:
            raise ValueError("A movie has no season or episode.")
        return movie_url(tmdb_id)
    if season is None or episode is None:
        raise ValueError("A TV episode needs both a season and an episode number.")
    return episode_url(tmdb_id, season, episode)


def is_embed_url(url: object) -> bool:
    """True for an https URL on the documented embed origin."""
    text = str(url or "").strip()
    return text.startswith(EMBED_ORIGIN + "/") and " " not in text


__all__ = [
    "EMBED_ORIGIN",
    "EPISODE_PATH",
    "MOVIE_PATH",
    "embed_url",
    "episode_url",
    "is_embed_url",
    "movie_url",
]
