"""Forced ani.pm routing for verified anime, in the Qt frontend.

Identity is Agent A's shared module, ``lgl.anime_identity``.  This module is
the one seam between it and the Qt page: it calls the published entry points,
reads the fields it publishes and turns the answer into a playback route.  It
never verifies, maps, searches titles, or inspects heuristics itself.

The published API this module calls::

    ai.identify_tmdb(kind, tmdb_id, title=..., year=..., settings=...)
    ai.identify(title=..., mal_id=..., settings=...)
    ai.identify(title=..., anilist_id=..., settings=...)

and the answer's fields ``verified``, ``playable_anilist_id`` and
``resolution_failed`` (plus ``is_anime``/``reason`` when present).

Identifier discipline: a MAL id is only ever passed as ``mal_id``, an AniList
id only as ``anilist_id``, and the id that reaches ani.pm is always
``playable_anilist_id``.  A module that is missing, raising or answering
``None`` means "cannot say", which is never verified anime: the user's own
provider keeps the play.

Routing rules, identical to the GTK frontend's:

* verified anime plays on ani.pm, whatever provider is selected or saved;
* a verified anime whose AniList entry could not be resolved
  (``resolution_failed``, or no ``playable_anilist_id``) is an error, never
  another provider and never a title search;
* everything else, including a failed lookup, keeps the provider passed in.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass

from . import anipm, streaming

#: Agent A's shared identity module.
IDENTITY_MODULE = "lgl.anime_identity"

#: Tracks ani.pm can only play when its API hands back an embed URL.
HARD_TRACKS: tuple[str, ...] = ("subHard", "dubHard")

#: What each shared-core resolution reason means for the person watching.
REASON_TEXT: dict[str, str] = {
    "anilist-missing": "AniList links no entry to this MyAnimeList title.",
    "anilist-ambiguous": (
        "AniList links more than one entry to it, so none can be chosen safely."
    ),
    "anilist-unvalidated": (
        "AniList could not confirm the entry the mapping suggested."
    ),
    "anilist-lookup-failed": "AniList could not be reached to confirm an entry.",
}

#: Said instead of playing somewhere else when a verified anime has no AniList id.
UNRESOLVED = (
    "“{title}” is verified anime on MyAnimeList, but its AniList entry could "
    "not be resolved (missing or ambiguous), so ani.pm cannot play it. "
    "Verified anime is not played on another provider."
)


@dataclass(frozen=True)
class Identity:
    """The answer this frontend needs from the shared module."""

    is_anime: bool = True
    #: ``verified`` is the shared core's VERIFIED_ANIME only; a resolution
    #: failure is NOT verified and is distinguished by ``resolution_failed``.
    verified: bool = False
    playable_anilist_id: int = 0
    resolution_failed: bool = False
    reason: str = ""

    @property
    def anilist_id(self) -> int:
        """The AniList id to play, or 0 when the shared core resolved none."""
        return self.playable_anilist_id if self.playable_anilist_id > 0 else 0

    @property
    def playable(self) -> bool:
        """Verified anime with exactly one AniList entry to play."""
        return self.verified and not self.resolution_failed and self.anilist_id > 0


@dataclass(frozen=True)
class Route:
    """Where one verified-anime play should go.

    ``forced`` marks a verified anime: the caller must use ``provider`` (always
    ani.pm), must not fall back anywhere, and must show ``error`` when no
    AniList id is available instead of playing the title.
    """

    provider: str = ""
    anilist_id: int = 0
    forced: bool = False
    error: str = ""


def _shared():
    """Agent A's module when it is importable, else ``None``."""
    try:
        return importlib.import_module(IDENTITY_MODULE)
    except Exception:  # noqa: BLE001 - an unavailable module is not an identity
        return None


def available() -> bool:
    """Whether the shared identity module can be called at all."""
    return _shared() is not None


def _normalise(answer) -> Identity | None:
    """Read the shared answer; only its published fields are used."""
    if answer is None:
        return None
    try:
        playable = int(getattr(answer, "playable_anilist_id", 0) or 0)
    except (TypeError, ValueError):
        playable = 0
    return Identity(
        is_anime=bool(getattr(answer, "is_anime", True)),
        verified=bool(getattr(answer, "verified", False)),
        playable_anilist_id=max(0, playable),
        resolution_failed=bool(getattr(answer, "resolution_failed", False)),
        reason=str(getattr(answer, "reason", "") or ""),
    )


def identify_tmdb(
    kind: str,
    tmdb_id: object,
    *,
    title: str = "",
    year: str = "",
    settings=None,
    **kwargs,
) -> Identity | None:
    """A TMDB title's identity, from the shared module.

    ``**kwargs`` are the shared module's own injection points (``mal=``,
    ``anilist=``, ``crosswalk=``, ``cache=``, ``now=``), passed through so
    callers and tests exercise the real implementation rather than a stand-in.
    """
    module = _shared()
    if module is None or not callable(getattr(module, "identify_tmdb", None)):
        return None
    try:
        answer = module.identify_tmdb(
            kind,
            int(tmdb_id),
            title=str(title or ""),
            year=str(year or ""),
            settings=settings,
            **kwargs,
        )
    except Exception:  # noqa: BLE001 - a lookup failure is not anime
        return None
    return _normalise(answer)


def identify_mal(
    mal_id: object,
    *,
    title: str = "",
    settings=None,
    **kwargs,
) -> Identity | None:
    """A title's identity; the shared module is the authority.

    ``**kwargs`` are the shared module's own injection points (``mal=``,
    ``anilist=``, ``crosswalk=``, ``cache=``, ``now=``), passed through so the
    real implementation runs instead of a stand-in.
    """
    module = _shared()
    if module is None or not callable(getattr(module, "identify", None)):
        return None
    try:
        answer = module.identify(
            title=str(title or ""),
            mal_id=int(mal_id),
            settings=settings,
            **kwargs,
        )
    except Exception:  # noqa: BLE001 - a lookup failure is not anime
        return None
    return _normalise(answer)


def identify_anilist(
    anilist_id: object,
    *,
    title: str = "",
    settings=None,
    **kwargs,
) -> Identity | None:
    """A title's identity; the shared module is the authority.

    ``**kwargs`` are the shared module's own injection points (``mal=``,
    ``anilist=``, ``crosswalk=``, ``cache=``, ``now=``), passed through so the
    real implementation runs instead of a stand-in.
    """
    module = _shared()
    if module is None or not callable(getattr(module, "identify", None)):
        return None
    try:
        answer = module.identify(
            title=str(title or ""),
            anilist_id=int(anilist_id),
            settings=settings,
            **kwargs,
        )
    except Exception:  # noqa: BLE001 - a lookup failure is not anime
        return None
    return _normalise(answer)


def plan(
    mal_id: object,
    title: str = "",
    *,
    provider: str = "",
    settings=None,
    identity=None,
) -> Route:
    """Decide where one MAL-keyed anime play goes.

    ``provider`` is the provider the page would use unforced (the Anime
    section's choice), and is returned untouched unless the shared module
    verifies this title as anime.  ``identity`` exists so tests can answer
    without the shared module; production callers pass nothing.
    """
    try:
        wanted = int(mal_id or 0)
    except (TypeError, ValueError):
        wanted = 0
    if wanted <= 0:
        return Route(provider=provider)
    answer = _ask(identity, wanted, title, settings)
    if answer is None:
        return Route(provider=provider)
    # The shared core reports a MAL-verified anime whose AniList entry could
    # not be resolved as RESOLUTION_FAILURE, and that status is *not*
    # ``verified``.  It must still never fall back to another provider.
    if answer.resolution_failed:
        return Route(forced=True, error=resolution_error(title, answer))
    if not answer.verified:
        return Route(provider=provider)
    if answer.anilist_id <= 0:
        return Route(forced=True, error=resolution_error(title, answer))
    return Route(
        provider=streaming.ANI_PROVIDER,
        anilist_id=answer.anilist_id,
        forced=True,
    )


def _ask(identity, mal_id: int, title: str, settings) -> Identity | None:
    if identity is None:
        return identify_mal(mal_id, title=title, settings=settings)
    try:
        answer = identity(mal_id, title=str(title or ""))
    except Exception:  # noqa: BLE001 - a raising lookup is not verified anime
        return None
    return _normalise(answer)


def resolution_error(title: str, identity=None) -> str:
    """The message for a verified anime with no playable AniList entry.

    The shared core's structured ``reason`` is appended when it has one, so a
    missing link, an ambiguous link and an unreachable AniList read differently.
    """
    detail = REASON_TEXT.get(str(getattr(identity, "reason", "") or ""), "")
    base = UNRESOLVED.format(title=_name(title))
    return f"{base} {detail}".strip()


def _name(title: str) -> str:
    return str(title or "").strip() or "This title"


def embed_url(anilist_id: object, episode: object = 1, *, language=None,
              api_embed: object = None) -> str:
    """The ani.pm URL for one AniList id and track; never from a MAL id.

    A hard track (``subHard``/``dubHard``) has no documented route to build,
    so it plays only when the API returned an embed URL: that URL is validated
    against the pinned ani.pm origin before use, and a missing or invalid one
    raises instead of quietly falling back to Sub or Dub.  Ordinary tracks
    keep the constructed route that has always been used.
    """
    text = str(api_embed or "").strip()
    track = anipm.language_key(language)
    if text or track in HARD_TRACKS:
        return anipm.playback_url(anilist_id, episode, track or language, text)
    return streaming.ani_url(anilist_id, episode, language=language)


__all__ = [
    "IDENTITY_MODULE",
    "HARD_TRACKS",
    "REASON_TEXT",
    "UNRESOLVED",
    "Identity",
    "Route",
    "available",
    "embed_url",
    "identify_anilist",
    "identify_mal",
    "identify_tmdb",
    "plan",
    "resolution_error",
]
