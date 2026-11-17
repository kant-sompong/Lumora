"""Which provider a GTK playback request goes to, decided by MAL identity.

Identity comes from the shared core, Agent A's :mod:`lgl.anime_identity`:

* :func:`anime_identity.identify_tmdb` answers for a TMDB title;
* :func:`anime_identity.identify` answers for a MAL/AniList catalogue entry
  (``title``, ``mal_id``, ``anilist_id``), which is what an anime page holds;

both take the frontend's settings object so the MAL client id can be loaded.
``identity.verified`` says whether the MAL mapping list confirms the title, and
``identity.playable_anilist_id`` is the AniList entry that plays on ani.pm.

The rule, identical for every Watch entry point:

* **verified** anime plays on ani.pm only - not the provider selected for
  movies and TV, and not a saved anime-provider preference.  The AniList id is
  the identity's ``playable_anilist_id``, never a MAL id, and a verified title
  whose AniList entry is missing or ambiguous is an error (:data:`UNRESOLVED` /
  :data:`AMBIGUOUS`), never a silent fall back to another provider;
* an **unverified** title - including one whose lookup was unavailable, had no
  reliable MAL match, or that this build cannot look up at all - keeps the
  user's selected provider;
* a **non-anime** title keeps the user's selected provider.

No heuristic verifies anything, and there is no path that establishes anime
identity without the shared MAL verification API: only ``identity.verified``
routes a play to ani.pm.  When the module is absent, nothing is verified and
plays stay with the selected provider.

Once identity is verified, the *episode* is mapped by the shared mapping layer,
:func:`anime_map.tmdb_to_anilist` (season-aware: Re:Zero's split seasons,
episode groups and ``PlayTarget`` offsets), and only a ``confident`` target is
played.  The mapping layer never establishes identity and never decides the
route: it is asked only after ``identity.verified`` said yes, and when it has no
confident answer the play uses the identity's ``playable_anilist_id`` with the
episode number unchanged.  Title searches are disabled for this lookup, so the
mapping layer cannot invent an AniList entry the identity did not verify.

This module holds the decision only: it imports no GTK, touches no settings
and never builds a URL, so the rule can be tested without a display and
:mod:`lgl.movies_ui` stays the only place that opens the player.
"""

from __future__ import annotations

import importlib
import inspect
from dataclasses import dataclass
from typing import Any, Callable, Mapping

#: ``PlaybackRoute.mode`` values.
MODE_NORMAL = "normal"
MODE_ANI = "ani"
MODE_ERROR = "error"

#: Shown when a verified title has no single AniList entry to play.  Verified
#: anime is never quietly handed to another provider.
UNRESOLVED = (
    "This title is confirmed anime, but its AniList entry could not be "
    "resolved, so ani.pm cannot play it. No other provider is used for "
    "verified anime."
)
AMBIGUOUS = (
    "This title is confirmed anime, but it matches more than one AniList "
    "entry, so ani.pm cannot choose one safely. No other provider is used "
    "for verified anime."
)


@dataclass(frozen=True)
class PlaybackRoute:
    """Where one playback request goes, and why.

    ``mode`` is :data:`MODE_ANI` for forced ani.pm playback (with the AniList
    id and the episode of that entry), :data:`MODE_ERROR` for a verified title
    that cannot be played there (``message`` says so), or
    :data:`MODE_NORMAL` for the user's selected provider.
    """

    mode: str = MODE_NORMAL
    anilist_id: int = 0
    episode: int = 1
    message: str = ""


def identity_module() -> Any:
    """The shared identity module (Agent A's), or ``None`` when absent.

    Imported lazily so a test can inject its own module without touching the
    real one.  A build without it verifies nothing, so every play stays with
    the selected provider.
    """
    try:
        return importlib.import_module("lgl.anime_identity")
    except Exception:  # noqa: BLE001 - no module means nothing is verified
        return None


def decide_playback(
    tmdb_kind: str,
    tmdb_id: int,
    *,
    title: str = "",
    year: object = "",
    season: int = 1,
    episode: int = 1,
    settings: object = None,
    identity: Any = None,
    mapping_for: Callable[..., Any] | None = None,
) -> PlaybackRoute:
    """The route for one TMDB title, from the shared MAL identity API.

    ``title`` and ``year`` are the catalogue title the shared identity API
    matches on MyAnimeList.  ``settings`` is the window's settings object,
    passed through so the identity API can load the MAL client id.  No TMDB
    details document is needed: identity never guesses from language or genre.
    ``identity`` is the shared module, looked up when not given.
    ``mapping_for`` is the episode-mapping seam, ``anime_map.tmdb_to_anilist``
    by default; it is asked only after the identity verified the title, and it
    never decides the route.

    A lookup that fails or cannot answer is **unverified**: the selected
    provider keeps the play.  Only ``identity.verified`` sends a play to
    ani.pm.  A resolution failure (``identity.resolution_failed``) is an error
    with no fallback.
    """
    kind = "tv" if str(tmdb_kind).lower() == "tv" else "movie"
    try:
        number = int(tmdb_id)
    except (TypeError, ValueError):
        return PlaybackRoute()
    if number <= 0:
        return PlaybackRoute()

    module = identity if identity is not None else identity_module()
    identify = getattr(module, "identify_tmdb", None) if module is not None else None
    if not callable(identify):
        return PlaybackRoute()
    wanted_season = int(season or 1)
    wanted_episode = max(1, int(episode or 1))
    kwargs = _identity_kwargs(
        identify,
        {
            "title": " ".join(str(title or "").split()),
            "year": str(year or ""),
            "settings": settings,
        },
    )
    try:
        answer = identify(kind, number, **kwargs)
    except Exception:  # noqa: BLE001 - an unavailable lookup is not anime
        return PlaybackRoute()
    return _route_from_answer(
        answer,
        wanted_episode,
        lambda: _confident_episode(
            kind, number, wanted_season, wanted_episode, mapping_for
        ),
    )


def decide_anime(
    *,
    title: str = "",
    kind: str = "tv",
    year: object = "",
    mal_id: object = None,
    anilist_id: object = None,
    episode: int = 1,
    settings: object = None,
    identity: Any = None,
) -> PlaybackRoute:
    """The route for an anime-page title, verified by the shared MAL API.

    Anime detail pages (MAL and ani.pm/AniList) carry no TMDB id, so the
    identity is asked with what the page holds: ``title``, ``mal_id`` and
    ``anilist_id`` (only the ids that are known).  An AniList id alone never
    bypasses MAL verification - it is another input to the same identity call.

    The three-way answer is the same as :func:`decide_playback`: verified with
    one AniList entry plays on ani.pm, unverified (including a lookup that
    fails) keeps the selected provider, and a verified title without a usable
    AniList entry is an error.
    """
    wanted_episode = max(1, int(episode or 1))
    module = identity if identity is not None else identity_module()
    identify = getattr(module, "identify", None) if module is not None else None
    if not callable(identify):
        return PlaybackRoute()
    text = " ".join(str(title or "").split())
    wanted_mal = _positive_or_none(mal_id)
    wanted_anilist = _positive_or_none(anilist_id)
    kwargs = _identity_kwargs(
        identify,
        {
            "title": text,
            "kind": "tv" if str(kind).lower() == "tv" else "movie",
            "year": str(year or ""),
            "mal_id": wanted_mal,
            "anilist_id": wanted_anilist,
            "settings": settings,
        },
    )
    if not kwargs:
        return PlaybackRoute()
    try:
        answer = identify(**kwargs)
    except Exception:  # noqa: BLE001 - an unavailable lookup is not anime
        return PlaybackRoute()
    # A page already names its AniList entry, so there is no TMDB season to
    # map: the identity's playable entry and episode are used unchanged.
    return _route_from_answer(answer, wanted_episode)


def _positive_or_none(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _identity_kwargs(fn: Callable[..., Any], values: Mapping[str, Any]) -> dict[str, Any]:
    """The keywords to pass a shared identity call.

    ``identify_tmdb`` declares ``**kwargs`` and forwards to ``identify``, so a
    plain named-parameter filter would drop ``settings``.  The values here are
    only the ones ``identify`` itself declares (``title``, ``year``,
    ``settings``, ids), so forwarding them is safe; without ``**kwargs`` the
    filter is the same as :func:`_supported`.
    """
    try:
        parameters = inspect.signature(fn).parameters
    except (TypeError, ValueError):  # pragma: no cover - builtins without a signature
        return {}
    if any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    ):
        return {name: value for name, value in values.items() if value is not None}
    return {name: value for name, value in values.items() if name in parameters}


def _supported(fn: Callable[..., Any], values: Mapping[str, Any]) -> dict[str, Any]:
    """Only the keywords ``fn`` declares, so an API skew cannot break a play."""
    try:
        parameters = inspect.signature(fn).parameters
    except (TypeError, ValueError):  # pragma: no cover - builtins without a signature
        return {}
    return {name: value for name, value in values.items() if name in parameters}


def _route_from_answer(
    answer: object,
    fallback_episode: int,
    mapping: Callable[[], tuple[int, int] | None] | None = None,
) -> PlaybackRoute:
    """One shared answer, turned into the three-way route.

    ``mapping`` is the season-aware episode mapping, asked only after the
    identity verified the title: a confident ``PlayTarget`` gives both the
    AniList entry and the episode of it that plays.  Without a confident
    mapping the identity's ``playable_anilist_id`` is played with the episode
    number unchanged.
    """
    # Always before ``verified``: RESOLUTION_FAILURE is verified=False with
    # resolution_failed=True, and it must be an error, not a provider fallback.
    if bool(getattr(answer, "resolution_failed", False)):
        # The shared API could not resolve a playable entry.  Verified anime
        # never falls back to another provider.
        reason = str(getattr(answer, "reason", "") or "")
        if "ambiguous" in reason:
            return PlaybackRoute(MODE_ERROR, message=AMBIGUOUS)
        return PlaybackRoute(MODE_ERROR, message=UNRESOLVED)
    if not bool(getattr(answer, "verified", False)):
        # Unverified, no reliable MAL match, or a lookup that answered nothing:
        # the user's selected provider keeps the play.
        return PlaybackRoute()
    if mapping is not None:
        mapped = mapping()
        if mapped is not None:
            return PlaybackRoute(MODE_ANI, mapped[0], mapped[1])
    playable = _positive_or_none(getattr(answer, "playable_anilist_id", None))
    if playable is None:
        reason = str(getattr(answer, "reason", "") or "")
        if "ambiguous" in reason:
            return PlaybackRoute(MODE_ERROR, message=AMBIGUOUS)
        return PlaybackRoute(MODE_ERROR, message=UNRESOLVED)
    return PlaybackRoute(MODE_ANI, playable, _identity_episode(answer, fallback_episode))


def _confident_episode(
    kind: str,
    tmdb_id: int,
    season: int,
    episode: int,
    mapping_for: Callable[..., Any] | None = None,
) -> tuple[int, int] | None:
    """The confident ``PlayTarget`` as ``(anilist_id, episode)``, else ``None``.

    Only the shared mapping layer's confident answer is used: it owns
    season-aware episode numbering (split seasons, episode groups, Re:Zero's
    offsets).  The title search inside the mapping layer is disabled, so it can
    never invent an AniList entry here: the identity is what verifies, and its
    ``playable_anilist_id`` is the fallback when the mapping list has no
    confident answer.
    """
    resolver = mapping_for
    if resolver is None:
        from . import anime_map

        resolver = getattr(anime_map, "tmdb_to_anilist", None)
    if not callable(resolver):
        return None
    try:
        target = resolver(
            **_supported(
                resolver,
                {
                    "tmdb_kind": kind,
                    "tmdb_id": tmdb_id,
                    "season": season,
                    "episode": episode,
                    "search": lambda _title: None,
                },
            )
        )
    except Exception:  # noqa: BLE001 - no mapping means the identity's episode
        return None
    if target is None or not getattr(target, "confident", False):
        return None
    anilist_id = _positive_or_none(getattr(target, "anilist_id", None))
    if anilist_id is None:
        return None
    played = _positive_or_none(getattr(target, "episode", None)) or episode
    return anilist_id, played


def _identity_episode(answer: object, fallback: int) -> int:
    """The episode of the playable AniList entry, else the requested episode.

    The identity API may name the entry-local episode; when it does not, the
    number the page is on is used unchanged.  Episode-local mapping is the
    mapping layer's business; this only reads what the answer exposes.
    """
    for name in ("playable_episode", "anilist_episode", "episode"):
        value = getattr(answer, name, None)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
            return value
    return max(1, int(fallback))


__all__ = [
    "AMBIGUOUS",
    "MODE_ANI",
    "MODE_ERROR",
    "MODE_NORMAL",
    "PlaybackRoute",
    "UNRESOLVED",
    "decide_anime",
    "decide_playback",
    "identity_module",
]
