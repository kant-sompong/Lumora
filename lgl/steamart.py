"""Keyless artwork search against Valve's public store.

RAWG carries far more than a poster - description, genres, release date,
screenshots - but it needs a key with a monthly quota, and a spent quota leaves
*every* imported game without artwork.  The store search that the Steam website's
own search box calls needs no key, returns an app id, and Steam's image CDN
serves a 2:3 ``library_600x900.jpg`` for most commercial games.

So this is a fallback, never a replacement: it only ever supplies a poster and a
page URL, never a description, developer, genre or screenshot, and a title it
does not know leaves the record exactly as it was.  A poster URL is a guess
until the CDN answers for it, so the chosen cover is checked before it is
claimed - a dead image URL is worse than no poster, because it would silence the
next sync.
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from dataclasses import replace
from typing import Any, Callable

from .sources import USER_AGENT, SourceError, SourceGame, request_json

STEAM_SEARCH_URL = "https://store.steampowered.com/api/storesearch/"
STEAM_HOSTS = ("store.steampowered.com",)
#: Hosts that serve Steam's images.  A record's own source must declare these
#: before the window may fetch a poster from them (see ``artwork.ARTWORK_HOSTS``).
STEAM_IMAGE_HOSTS = (
    "cdn.cloudflare.steamstatic.com",
    "cdn.akamai.steamstatic.com",
    "shared.akamai.steamstatic.com",
    "shared.fastly.steamstatic.com",
)
DEFAULT_TIMEOUT = 20.0


def poster_urls(app_id: Any) -> tuple[str, str]:
    """The 2:3 library poster, then the wide header, for one app id."""
    base = (
        "https://cdn.cloudflare.steamstatic.com/steam/apps/"
        + urllib.parse.quote(str(app_id or ""), safe="")
    )
    return f"{base}/library_600x900.jpg", f"{base}/header.jpg"


def url_serves_an_image(url: str, *, timeout: float = DEFAULT_TIMEOUT) -> bool:
    """True when this CDN path answers 200 to a HEAD request.

    Only the declared image hosts are contacted: a poster URL that points
    anywhere else is never fetched, whatever a response said.
    """
    try:
        parsed = urllib.parse.urlsplit(str(url or ""))
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host:
        return False
    if not any(host == allowed or host.endswith("." + allowed) for allowed in STEAM_IMAGE_HOSTS):
        return False
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(getattr(response, "status", 0) or 0) == 200
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        return False


def _game_from_item(item: Any) -> SourceGame | None:
    """One ``items[]`` entry as a record this project already understands."""
    if not isinstance(item, dict):
        return None
    raw_id = item.get("id")
    if not isinstance(raw_id, int) or isinstance(raw_id, bool) or raw_id <= 0:
        return None
    app_id = str(raw_id)
    title = " ".join(str(item.get("name") or "").split())
    if not title:
        return None
    poster, _header = poster_urls(app_id)
    return SourceGame(
        source_id="steam",
        game_id=app_id,
        title=title[:300],
        cover_url=poster,
        source_url=f"https://store.steampowered.com/app/{app_id}/",
    )


class SteamArtSource:
    """Search the Steam store; the only thing taken from it is artwork.

    No credentials, no pagination (the endpoint returns one short page), and no
    claim about ownership or availability: whether a game can be downloaded is
    somebody else's question.
    """

    id = "steam"
    name = "Steam store"
    hosts = STEAM_HOSTS
    image_hosts = STEAM_IMAGE_HOSTS

    def __init__(
        self,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        fetch_json: Callable[..., dict[str, Any]] = request_json,
        check_url: Callable[[str], bool] = url_serves_an_image,
    ) -> None:
        self.timeout = float(timeout)
        self._fetch_json = fetch_json
        self._check_url = check_url
        self.last_error = ""

    def search_games(self, title: str, *, limit: int = 20) -> list[SourceGame]:
        """Records whose name resembles *title*; picking one is the caller's job."""
        name = " ".join(str(title or "").split())
        if not name:
            raise SourceError("A title is needed to search the Steam store.")
        query = urllib.parse.urlencode(
            {"term": name[:120], "l": "english", "cc": "us"}
        )
        url = f"{STEAM_SEARCH_URL}?{query}"
        try:
            data = self._fetch_json(url, hosts=self.hosts, timeout=self.timeout)
        except SourceError as exc:
            self.last_error = str(exc)
            raise
        except (OSError, ValueError) as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            raise SourceError(f"The Steam store could not be read: {exc}") from exc
        items = data.get("items") if isinstance(data, dict) else None
        if items is None:
            raise SourceError("The Steam store did not return a search result list.")
        if not isinstance(items, (list, tuple)):
            raise SourceError("The Steam store returned a result list of the wrong type.")
        games: list[SourceGame] = []
        for item in list(items)[: max(1, int(limit))]:
            game = _game_from_item(item)
            if game is not None:
                games.append(game)
        return games

    def with_cover(self, candidate: Any) -> Any:
        """Swap the assumed poster for one the CDN actually serves.

        Without a served image the candidate keeps no cover at all, so the
        record stays "needs artwork" and a later sync can try again.
        """
        if not str(getattr(candidate, "cover_url", "") or ""):
            return candidate
        for url in poster_urls(getattr(candidate, "game_id", "")):
            if self._check_url(url):
                return replace(candidate, cover_url=url)
        return replace(candidate, cover_url="")


__all__ = [
    "STEAM_HOSTS",
    "STEAM_IMAGE_HOSTS",
    "STEAM_SEARCH_URL",
    "SteamArtSource",
    "poster_urls",
    "url_serves_an_image",
]
