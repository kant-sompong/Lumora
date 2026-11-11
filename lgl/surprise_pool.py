"""Which titles "Surprise me" may draw from, given the page on screen.

Both frontends already share :func:`lgl.discovery.random_pick`; this module owns
the other half — the pool.  Browse and Library draw only from games, Media draws
only from the section that is open (Movies picks a movie, Anime picks an anime),
and only a window with no page context at all mixes the two.

The rows it returns are the same ``{"kind", "key", "title"}`` dicts the rest of
the app already navigates by, so nothing here needs to know a toolkit.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

#: The kinds a Media section can narrow the pool to.
MEDIA_KINDS = ("movie", "tv", "anime")

#: Media sections whose rows are all one kind; anything else offers every card.
SECTION_KINDS = {
    "movies": "movie",
    "movie": "movie",
    "tv": "tv",
    "shows": "tv",
    "anime": "anime",
    "gems": "anime",
    "hidden_gems": "anime",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _row(kind: str, key: Any, title: Any) -> dict[str, str] | None:
    key_text = _text(key)
    if not key_text:
        return None
    return {"kind": kind, "key": key_text, "title": _text(title) or key_text}


def game_rows(entries: Iterable[Any]) -> list[dict[str, str]]:
    """Library or Browse entries as random-pick rows; ones with no id are skipped."""
    rows: list[dict[str, str]] = []
    for entry in entries or ():
        if isinstance(entry, Mapping):
            key = entry.get("identifier") or entry.get("key")
            title = entry.get("title")
        else:
            key = getattr(entry, "identifier", None) or getattr(entry, "key", None)
            title = getattr(entry, "title", None)
        row = _row("game", key, title)
        if row is not None:
            rows.append(row)
    return rows


def kind_for_section(section: Any) -> str:
    """The one media kind a section shows, or ``""`` when it shows them all."""
    return SECTION_KINDS.get(_text(section).lower(), "")


def media_rows(cards: Iterable[Any], kind: str = "") -> list[dict[str, str]]:
    """Media cards as random-pick rows, optionally only one kind."""
    wanted = _text(kind).lower()
    rows: list[dict[str, str]] = []
    for card in cards or ():
        card_kind = _text(getattr(card, "kind", "")).lower() or "media"
        if wanted and card_kind != wanted:
            continue
        row = _row(card_kind, getattr(card, "key", ""), getattr(card, "title", ""))
        if row is not None:
            rows.append(row)
    return rows


def pool_for(
    page: Any,
    *,
    games: Iterable[Any] = (),
    media: Iterable[Any] = (),
    section: Any = "",
) -> list[dict[str, str]]:
    """The pool for *page*: games for Browse/Library, one Media section otherwise.

    ``page`` is the frontend's own name for the open page: ``library``,
    ``browse`` (or GTK's ``archive``) or ``media``.  An unknown page keeps the
    old mixed pool, so a window that has not chosen a page yet still picks
    something.
    """
    name = _text(page).lower()
    if name in ("library", "browse", "archive"):
        return game_rows(games)
    if name == "media":
        return media_rows(media, kind_for_section(section))
    return game_rows(games) + media_rows(media)
