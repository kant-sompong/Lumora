"""Streaming-provider adapters for the Movies & TV window.

CineSrc, Rive, frameXTV and Atlantic map a TMDB id (and season/episode for TV)
to a playable embed URL.  ani.pm (https://ani.pm/developers) is the anime-only
provider: it plays by AniList **or** MyAnimeList id and needs no key.  The
active providers are chosen by the user and stored in settings under
``streaming_provider`` and ``anime_streaming_provider``.

Every provider is described once, in :data:`SPECS`: the HTTPS origin its embed
may load from, the languages it accepts, and whether its player currently needs
the relaxed WebKit security profile.  Callers ask this module instead of
branching on provider names, and :func:`fallback_providers` gives the order to
try when one of them cannot play.

Embed formats, as published by each provider:

* CineSrc - ``https://cinesrc.st/embed/movie/<tmdb>`` and
  ``https://cinesrc.st/embed/tv/<tmdb>?s=<season>&e=<episode>``
  (https://cinesrc.st/docs).  It asks embedders for
  ``allow="autoplay; fullscreen; picture-in-picture"`` and documents neither a
  dub parameter nor a sandbox requirement.
* Rive - ``https://www.rivestream.app/embed?type=movie&id=<tmdb>`` and
  ``.../embed?type=tv&id=<tmdb>&season=<season>&episode=<episode>``
  (https://www.rivestream.app/embed/docs).  It documents no sandbox
  requirement and no dub parameter.

This desktop app loads the embed page directly in a ``WebKit2.WebView`` rather
than wrapping it in an ``<iframe>``, so no sandbox attribute exists at runtime.
:func:`iframe_sandbox` records the documented policy so a future HTML wrapper
applies it per provider instead of guessing.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from typing import Literal

Provider = Literal[
    "cinesrc", "rivestream", "toflix", "cinelite", "framextv", "atlantic", "ani"
]

#: Providers that can play a movie or TV episode from a TMDB id, in the order
#: they are tried when the preferred one fails.
PROVIDERS: tuple[str, ...] = (
    "cinesrc",
    "rivestream",
    "toflix",
    "cinelite",
    "framextv",
    "atlantic",
)
#: Providers that can play anime.  ani.pm uses an AniList/MAL id instead of
#: TMDB, so it is listed separately.
ANIME_PROVIDERS: tuple[str, ...] = (*PROVIDERS, "ani")
DEFAULT_PROVIDER = "cinesrc"
DEFAULT_ANIME_PROVIDER = "ani"
#: ani.pm's player API (https://ani.pm/developers) takes an AniList id,
#: e.g. ``/embed/ani/<anilist_id>/<episode>/<sub|dub>``, and a MAL id at
#: ``/embed/mal/<mal_id>/<episode>/<sub|dub|vostfr>``.
ANI_PROVIDER = "ani"
ANI_BASE = "https://ani.pm"

#: Human-readable labels for the selector.
LABELS: dict[str, str] = {
    "cinesrc": "CineSrc",
    "rivestream": "Rive",
    "toflix": "ToFlix",
    "cinelite": "CineLite",
    "framextv": "frameXTV",
    "atlantic": "Atlantic",
    "ani": "ani.pm (anime)",
}

#: Providers whose anime route is keyed by an **AniList** id.
ANILIST_ANIME_PROVIDERS: tuple[str, ...] = ("toflix", "cinelite")

#: Audio/subtitle paths each provider's embed accepts.  Only ani.pm documents
#: an audio-track parameter; the TMDB providers pick their own source, and the
#: two whose docs say nothing about audio are offered sub only rather than
#: pretending a dub flag exists.
LANGUAGES: dict[str, tuple[str, ...]] = {
    "cinesrc": ("sub",),
    "rivestream": ("sub",),
    "toflix": ("sub",),
    "cinelite": ("sub",),
    "framextv": ("sub", "dub"),
    "atlantic": ("sub", "dub"),
    "ani": ("sub", "dub", "vostfr"),
}
LANGUAGE_LABELS: dict[str, str] = {
    "sub": "Sub",
    "dub": "Dub",
    "vostfr": "VOSTFR",
}
DEFAULT_LANGUAGE = "sub"


class EmbedUrlError(ValueError):
    """A provider URL or setting that must not be loaded as trusted content."""


@dataclass(frozen=True)
class ProviderSpec:
    """One provider's playback contract.

    ``iframe_sandbox`` is ``None`` when the provider must not be sandboxed
    (ani.pm documents this) or when sandboxing has not been verified; a string
    holds the exact attribute value a wrapper may use.
    ``relaxed_web_security`` is True only for players that were already known
    to need it; setting it globally would weaken every provider.
    """

    key: str
    label: str
    host: str
    languages: tuple[str, ...]
    iframe_sandbox: str | None
    relaxed_web_security: bool = False
    embed_prefix: str = "/embed/"


SPECS: dict[str, ProviderSpec] = {
    "cinesrc": ProviderSpec(
        key="cinesrc", label="CineSrc", host="cinesrc.st",
        languages=LANGUAGES["cinesrc"], iframe_sandbox=None,
    ),
    "rivestream": ProviderSpec(
        key="rivestream", label="Rive", host="www.rivestream.app",
        languages=LANGUAGES["rivestream"], iframe_sandbox=None,
        embed_prefix="/embed",
    ),
    "toflix": ProviderSpec(
        key="toflix", label="ToFlix", host="toflix.co",
        languages=LANGUAGES["toflix"], iframe_sandbox=None,
    ),
    "cinelite": ProviderSpec(
        # CineLite's documented routes are rooted at "/" (/-<tmdbId>), so the
        # prefix is the site root; the pinned host is the actual boundary.
        key="cinelite", label="CineLite", host="embed.cinelite.xyz",
        languages=LANGUAGES["cinelite"], iframe_sandbox=None,
        embed_prefix="/",
    ),
    "framextv": ProviderSpec(
        key="framextv", label="frameXTV", host="framextv.xyz",
        languages=LANGUAGES["framextv"], iframe_sandbox=None,
        relaxed_web_security=True,
    ),
    "atlantic": ProviderSpec(
        key="atlantic", label="Atlantic", host="atlantic.st",
        languages=LANGUAGES["atlantic"], iframe_sandbox=None,
        relaxed_web_security=True,
    ),
    "ani": ProviderSpec(
        key="ani", label="ani.pm", host="ani.pm",
        languages=LANGUAGES["ani"], iframe_sandbox=None,
    ),
}


def provider_spec(provider: str) -> ProviderSpec:
    key = str(provider or "").strip().lower()
    spec = SPECS.get(key)
    if spec is None:
        raise EmbedUrlError(f"Unknown streaming provider: {provider!r}")
    return spec


def languages_for(provider: str) -> tuple[str, ...]:
    return provider_spec(provider).languages


def iframe_sandbox(provider: str) -> str | None:
    """The sandbox attribute value for a future iframe wrapper, if any.

    ``None`` means "do not add a sandbox attribute".  This app's WebKit player
    loads the page directly, so nothing is applied today.
    """
    return provider_spec(provider).iframe_sandbox


def has_anime_route(provider: str) -> bool:
    """Whether the provider documents an AniList-keyed anime route."""
    return provider_spec(provider).key in ANILIST_ANIME_PROVIDERS


def fallback_anime_providers(preferred: object) -> tuple[str, ...]:
    """The preferred anime provider first, then the other anime-capable ones."""
    order: list[str] = []
    key = str(preferred or "").strip().lower()
    if key in ANIME_PROVIDERS:
        order.append(key)
    for candidate in ANIME_PROVIDERS:
        if candidate not in order:
            order.append(candidate)
    return tuple(order)


def anime_url(provider: str, anilist_id: object, episode: object = 1) -> str:
    """Anime embed URL from an **AniList** id (ToFlix and CineLite routes).

    Never pass a MAL or TMDB id here: these routes are AniList-only, and a
    wrong identifier would play a different show.
    """
    anilist_id = _positive(anilist_id, "anilist_id")
    episode = _positive(episode, "episode")
    spec = provider_spec(provider)
    if spec.key == "toflix":
        return f"https://toflix.co/embed/anime/{anilist_id}/{episode}"
    if spec.key == "cinelite":
        return f"https://embed.cinelite.xyz/anime/{anilist_id}/{episode}"
    raise EmbedUrlError(f"{spec.label} has no AniList anime route.")


def fallback_providers(preferred: object) -> tuple[str, ...]:
    """The preferred provider first, then every other one in priority order.

    This is the order the player walks when a provider fails to load, so a
    blocked or empty provider falls through instead of ending playback.
    """
    order: list[str] = []
    key = str(preferred or "").strip().lower()
    if key in PROVIDERS:
        order.append(key)
    for candidate in PROVIDERS:
        if candidate not in order:
            order.append(candidate)
    return tuple(order)


def clean_language(provider: str, value: object) -> str:
    """Clamp a stored language to one the provider supports."""
    text = str(value or "").strip().lower()
    allowed = provider_spec(provider).languages
    return text if text in allowed else DEFAULT_LANGUAGE


def ensure_language(provider: str, value: object) -> str:
    """Return a supported language or explain why it cannot be used."""
    text = str(value or "").strip().lower()
    allowed = provider_spec(provider).languages
    if text not in allowed:
        readable = ", ".join(LANGUAGE_LABELS.get(item, item) for item in allowed)
        raise EmbedUrlError(
            f"{LABELS.get(provider, provider)} does not offer "
            f"{LANGUAGE_LABELS.get(text, text or 'that language')}; "
            f"available: {readable}."
        )
    return text


def validate_embed_url(provider: str, url: object) -> str:
    """Accept only this provider's HTTPS embed URL, exactly.

    Rejects other schemes, userinfo tricks (``https://ani.pm@evil``), explicit
    ports, lookalike hosts (``ani.pm.evil``) and paths outside the provider's
    ``/embed`` prefix.  Query strings are allowed: Rive and CineSrc carry the
    id, season and episode in the query.
    """
    spec = provider_spec(provider)
    text = str(url or "").strip()
    if not text:
        raise EmbedUrlError("The provider returned no playback URL.")
    try:
        parsed = urllib.parse.urlsplit(text)
        port = parsed.port
    except ValueError as error:
        raise EmbedUrlError(f"Malformed provider URL: {error}") from error
    if parsed.scheme != "https":
        raise EmbedUrlError("Provider playback must use HTTPS.")
    if parsed.username or parsed.password:
        raise EmbedUrlError("Provider URL must not carry credentials.")
    if port not in (None, 443):
        raise EmbedUrlError("Provider URL must use the default HTTPS port.")
    host = (parsed.hostname or "").lower().rstrip(".")
    if host != spec.host:
        raise EmbedUrlError(
            f"Expected {spec.host} for {spec.label}, got {host or 'no host'}."
        )
    base = spec.embed_prefix.rstrip("/")
    if parsed.path != base and not parsed.path.startswith(base + "/"):
        raise EmbedUrlError(
            f"{spec.label} playback must stay under {base}."
        )
    return text


def _positive(value: object, label: str) -> int:
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


def _quote(value: str) -> str:
    return urllib.parse.quote(str(value or ""), safe="")


def movie_url(provider: str, tmdb_id: object, *, title: str = "", dub: bool = False) -> str:
    tmdb_id = _positive(tmdb_id, "tmdb_id")
    if provider == "cinesrc":
        # https://cinesrc.st/docs - all content is identified by TMDB id.
        url = f"https://cinesrc.st/embed/movie/{tmdb_id}"
        if title:
            url += "?back=close"
        return url
    if provider == "rivestream":
        url = f"https://www.rivestream.app/embed?type=movie&id={tmdb_id}"
        if title:
            url += "&title=" + _quote(title)
        return url
    if provider == "toflix":
        return f"https://toflix.co/embed/movie/{tmdb_id}"
    if provider == "cinelite":
        return f"https://embed.cinelite.xyz/{tmdb_id}"
    if provider == "atlantic":
        return f"https://atlantic.st/embed/movie/{tmdb_id}"
    if provider == ANI_PROVIDER:
        raise ValueError("ani.pm is an anime provider; use ani_url() instead.")
    return f"https://framextv.xyz/embed/{tmdb_id}"


def episode_url(provider: str, tmdb_id: object, season: object, episode: object, *, title: str = "", dub: bool = False) -> str:
    tmdb_id = _positive(tmdb_id, "tmdb_id")
    season = _positive(season, "season")
    episode = _positive(episode, "episode")
    if provider == "cinesrc":
        # ?s= and ?e= are the documented aliases for season and episode.
        return f"https://cinesrc.st/embed/tv/{tmdb_id}?s={season}&e={episode}"
    if provider == "rivestream":
        return (
            "https://www.rivestream.app/embed"
            f"?type=tv&id={tmdb_id}&season={season}&episode={episode}"
        )
    if provider == "toflix":
        return f"https://toflix.co/embed/tv/{tmdb_id}/{season}/{episode}"
    if provider == "cinelite":
        return f"https://embed.cinelite.xyz/{tmdb_id}/{season}/{episode}"
    if provider == "atlantic":
        return f"https://atlantic.st/embed/tv/{tmdb_id}/{season}/{episode}"
    if provider == ANI_PROVIDER:
        raise ValueError("ani.pm is an anime provider; use ani_url() instead.")
    return f"https://framextv.xyz/embed/{tmdb_id}/{season}/{episode}"


def anilist_search(title: str, *, timeout: float = 15.0) -> int | None:
    """Search AniList by title and return the first anime's AniList id.

    Returns ``None`` when nothing matches or the request fails.  Uses the
    public AniList GraphQL endpoint (no key required).
    """
    text = " ".join(str(title or "").split())
    if not text:
        return None
    import json
    import urllib.request

    query = (
        "query ($search: String) {"
        " Media(search: $search, type: ANIME) { id title { romaji english } }"
        "}"
    )
    body = json.dumps({"query": query, "variables": {"search": text[:200]}}).encode("utf-8")
    request = urllib.request.Request(
        "https://graphql.anilist.co",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "native-game-library (personal desktop client)",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read(1024 * 1024).decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return None
    media = payload.get("data", {}).get("Media") if isinstance(payload, dict) else None
    if isinstance(media, dict) and isinstance(media.get("id"), int):
        return int(media["id"])
    return None


def _ani_language(language: object, dub: object) -> str:
    if language is None:
        return "dub" if dub else "sub"
    return ensure_language(ANI_PROVIDER, language)


def ani_url(
    anilist_id: object, episode: object = 1, *, language: object = None,
    dub: object = None,
) -> str:
    """ani.pm embed URL from an **AniList** id (never a MAL or TMDB id)."""
    anilist_id = _positive(anilist_id, "anilist_id")
    episode = _positive(episode, "episode")
    lang = _ani_language(language, dub)
    return f"{ANI_BASE}/embed/ani/{anilist_id}/{episode}/{lang}"


def ani_mal_url(
    mal_id: object, episode: object = 1, *, language: object = None,
    dub: object = None,
) -> str:
    """ani.pm embed URL from a **MyAnimeList** id.

    This is the route anime tiles use: the app already holds the MAL id, so an
    AniList lookup is not needed and the two identifiers stay separate.
    """
    mal_id = _positive(mal_id, "mal_id")
    episode = _positive(episode, "episode")
    lang = _ani_language(language, dub)
    return f"{ANI_BASE}/embed/mal/{mal_id}/{episode}/{lang}"


def is_valid_provider(value: object) -> bool:
    return str(value).strip().lower() in PROVIDERS


def clean_provider(value: object) -> str:
    text = str(value or "").strip().lower()
    return text if text in PROVIDERS else DEFAULT_PROVIDER


def is_valid_anime_provider(value: object) -> bool:
    return str(value).strip().lower() in ANIME_PROVIDERS


def clean_anime_provider(value: object) -> str:
    text = str(value or "").strip().lower()
    return text if text in ANIME_PROVIDERS else DEFAULT_ANIME_PROVIDER


__all__ = [
    "ANI_BASE",
    "ANI_PROVIDER",
    "ANIME_PROVIDERS",
    "DEFAULT_ANIME_PROVIDER",
    "DEFAULT_LANGUAGE",
    "DEFAULT_PROVIDER",
    "EmbedUrlError",
    "LABELS",
    "LANGUAGES",
    "LANGUAGE_LABELS",
    "PROVIDERS",
    "Provider",
    "ProviderSpec",
    "ANILIST_ANIME_PROVIDERS",
    "SPECS",
    "ani_mal_url",
    "ani_url",
    "anilist_search",
    "anime_url",
    "clean_anime_provider",
    "clean_language",
    "clean_provider",
    "ensure_language",
    "episode_url",
    "fallback_anime_providers",
    "fallback_providers",
    "has_anime_route",
    "iframe_sandbox",
    "is_valid_anime_provider",
    "is_valid_provider",
    "languages_for",
    "movie_url",
    "provider_spec",
    "validate_embed_url",
]
