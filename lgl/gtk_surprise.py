"""The GTK "Surprise me…" pick: one random title from what is already here.

The pick itself is :func:`lgl.discovery.random_pick`; this module only builds
the pool from the library (and any media cards the movies view already holds)
and then reuses the app's own way of jumping to a title.  It never loads
anything from the network, so the pool is only ever what is on screen already.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from . import discovery

#: Shown when there is nothing to pick from.
EMPTY_MESSAGE = "Nothing to pick yet — add a game or open Movies & TV."


def build_pool(
    entries: Iterable[Any], cards: Iterable[Any] = ()
) -> list[dict[str, str]]:
    """Random-pick rows: every library game, then any loaded media cards.

    Each row keeps the ``kind`` and ``key`` the rest of the app navigates by,
    plus the ``title`` used in the message.  Entries without an identifier are
    skipped because there would be nothing to open.
    """
    pool: list[dict[str, str]] = []
    for entry in entries:
        key = str(getattr(entry, "identifier", "") or "").strip()
        if not key:
            continue
        title = str(getattr(entry, "title", "") or "").strip() or key
        pool.append({"kind": "game", "key": key, "title": title})
    for card in cards:
        key = str(getattr(card, "key", "") or "").strip()
        if not key:
            continue
        kind = str(getattr(card, "kind", "") or "").strip() or "media"
        title = str(getattr(card, "title", "") or "").strip() or key
        pool.append({"kind": kind, "key": key, "title": title})
    return pool


def loaded_cards(parent: Any) -> list[Any]:
    """Media cards the movies view already has, without asking for more.

    ``MoviesWindow`` builds its tiles from network results and does not keep a
    list of them today, so this normally returns nothing; if it grows an
    accessor, it is used here and nothing is fetched.
    """
    view = getattr(parent, "media_view", None)
    if view is None:
        return []
    getter = getattr(view, "loaded_cards", None)
    if callable(getter):
        try:
            return list(getter())
        except Exception:  # noqa: BLE001 - a broken view just has no cards
            return []
    try:
        return list(getattr(view, "_loaded_cards", ()) or ())
    except TypeError:
        return []


def library_entries(parent: Any) -> list[Any]:
    """The library index rows, or nothing when the window has no store."""
    store = getattr(parent, "library_store", None)
    getter = getattr(store, "entries", None)
    if not callable(getter):
        return []
    try:
        return list(getter())
    except Exception:  # noqa: BLE001 - an unreadable index is empty, not fatal
        return []


def jump_to(parent: Any, kind: str, key: str) -> None:
    """Go where the Recently viewed rows and the palette go."""
    apply_view = getattr(parent, "_apply_view", None)
    if str(kind) == "game":
        if callable(apply_view):
            apply_view("library")
        view = getattr(parent, "library_view", None)
        select = getattr(view, "_select_saved_tile", None)
        if callable(select):
            select(str(key))
        return
    if callable(apply_view):
        apply_view("media")


def surprise(parent: Any) -> Mapping[str, str] | None:
    """Pick one title, say which, and take the user there."""
    pool = build_pool(library_entries(parent), loaded_cards(parent))
    choice = discovery.random_pick(pool)
    toast = getattr(parent, "toast", None)
    if choice is None:
        if callable(toast):
            toast(EMPTY_MESSAGE, error=True)
        return None
    if callable(toast):
        toast(f"Random pick: {choice['title']}")
    jump_to(parent, choice["kind"], choice["key"])
    return choice


__all__ = [
    "EMPTY_MESSAGE",
    "build_pool",
    "jump_to",
    "library_entries",
    "loaded_cards",
    "surprise",
]
