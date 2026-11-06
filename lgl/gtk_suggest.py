"""Smart suggestions under the GTK header search box.

The ranking lives in :mod:`lgl.suggestions`; this module only builds the corpus
(library titles, plus media titles the window already holds — never a network
load) and keeps a ``Gtk.EntryCompletion`` model in step with what was typed.
GTK's completion filters by prefix by default, so the match function accepts
every row and ``suggest`` alone decides what is offered.  GTK 3 only:
``pack_start``-free, ``.add()`` where needed and ``get_style_context``.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from . import suggestions

#: How many suggestions the popup offers, matching the Qt window.
MAX_ROWS = 8


def corpus_titles(entries: Iterable[Any], cards: Iterable[Any] = ()) -> list[str]:
    """Search corpus: library titles (or identifiers), then loaded media titles."""
    titles: list[str] = []
    for entry in entries:
        text = str(
            getattr(entry, "title", "")
            or getattr(entry, "identifier", "")
            or ""
        ).strip()
        if text:
            titles.append(text)
    for card in cards:
        text = str(getattr(card, "title", "") or "").strip()
        if text:
            titles.append(text)
    return titles


def loaded_cards(window: Any) -> list[Any]:
    """Media cards the movies view already has; nothing is fetched here."""
    view = getattr(window, "media_view", None)
    if view is None:
        return []
    getter = getattr(view, "loaded_cards", None)
    if callable(getter):
        try:
            return list(getter())
        except Exception:  # noqa: BLE001 - a broken view simply has no titles
            return []
    try:
        return list(getattr(view, "_loaded_cards", ()) or ())
    except TypeError:
        return []


def library_entries(window: Any) -> list[Any]:
    """The library index rows, or nothing when the window has no store yet."""
    store = getattr(window, "library_store", None)
    getter = getattr(store, "entries", None)
    if not callable(getter):
        return []
    try:
        return list(getter())
    except Exception:  # noqa: BLE001 - an unreadable index suggests nothing
        return []


def corpus(window: Any) -> list[str]:
    """Everything the header box may suggest from, without any loading."""
    return corpus_titles(library_entries(window), loaded_cards(window))


def model_rows(
    text: str, titles: Iterable[str], limit: int = MAX_ROWS
) -> list[str]:
    """The suggestion texts for ``text``; empty for an empty query."""
    return [match.text for match in suggestions.suggest(text, titles, limit=limit)]


def update_model(
    store: Gtk.ListStore, text: str, titles: Iterable[str], limit: int = MAX_ROWS
) -> list[str]:
    """Replace the completion model with the current suggestions."""
    rows = model_rows(text, titles, limit=limit)
    store.clear()
    for row in rows:
        store.append([row])
    return rows


def match_any(
    _completion: Gtk.EntryCompletion | None,
    _key: str,
    _iterator: Gtk.TreeIter | None,
    _data: Any = None,
) -> bool:
    """Accept every row: ``suggest`` has already chosen and ranked them."""
    return True


def attach(entry: Gtk.Entry, window: Any) -> Gtk.EntryCompletion:
    """Give ``entry`` a suggestion popup filled from ``window``'s library."""
    store = Gtk.ListStore(str)
    completion = Gtk.EntryCompletion()
    completion.set_model(store)
    completion.set_text_column(0)
    completion.set_popup_completion(True)
    completion.set_popup_single_match(False)
    completion.set_inline_completion(False)
    completion.set_minimum_key_length(0)
    completion.set_match_func(match_any, None)

    def refresh(*_args: Any) -> None:
        update_model(store, entry.get_text(), corpus(window))

    def choose(
        _completion: Gtk.EntryCompletion, _model: Gtk.TreeModel, path: Gtk.TreePath
    ) -> bool:
        row = store.get_iter(path)
        value = store.get_value(row, 0)
        entry.set_text(str(value))
        entry.set_position(-1)
        return True

    completion.connect("match-selected", choose)
    entry.set_completion(completion)
    entry.connect("changed", refresh)
    refresh()
    return completion


__all__ = [
    "MAX_ROWS",
    "attach",
    "corpus",
    "corpus_titles",
    "library_entries",
    "loaded_cards",
    "match_any",
    "model_rows",
    "update_model",
]
