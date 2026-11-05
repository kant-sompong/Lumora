"""What to offer under the search box while the user is still typing.

Suggestions are ranked the way people expect: a title that starts with what was
typed first, then a later word start, then a substring, then a loose subsequence
match. Duplicates are collapsed case-insensitively so a list with "Heat" and
"heat" does not offer both.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

MAX_SUGGESTIONS = 8


@dataclass(frozen=True)
class Suggestion:
    text: str
    kind: str = "title"


def _norm(value: object) -> str:
    return str(value or "").strip()


def _pairs(corpus: Iterable[object]) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for entry in corpus:
        if isinstance(entry, Suggestion):
            items.append((entry.text, entry.kind))
        elif isinstance(entry, (tuple, list)) and entry:
            text = _norm(entry[0])
            kind = _norm(entry[1]) if len(entry) > 1 else "title"
            if text:
                items.append((text, kind))
        else:
            text = _norm(entry)
            if text:
                items.append((text, "title"))
    return items


def _subsequence(needle: str, haystack: str) -> bool:
    iterator = iter(haystack)
    return all(char in iterator for char in needle)


def score(query: str, text: str) -> float:
    """0 when it does not match at all; higher is better."""
    wanted = _norm(query).casefold()
    candidate = _norm(text).casefold()
    if not wanted or not candidate:
        return 0.0
    if candidate.startswith(wanted):
        return 100.0 - len(candidate) * 0.1
    if any(word.startswith(wanted) for word in candidate.split()):
        return 70.0
    found = candidate.find(wanted)
    if found >= 0:
        return 50.0 - found * 0.1
    if _subsequence(wanted, candidate):
        return 25.0 - len(candidate) * 0.1
    return 0.0


def suggest(query: str, corpus: Iterable[object], limit: int = MAX_SUGGESTIONS) -> list[Suggestion]:
    """Best `limit` suggestions for `query`, de-duplicated case-insensitively."""
    if not _norm(query):
        return []
    best: dict[str, tuple[float, Suggestion]] = {}
    for text, kind in _pairs(corpus):
        value = score(query, text)
        if value <= 0:
            continue
        key = text.casefold()
        current = best.get(key)
        if current is None or value > current[0]:
            best[key] = (value, Suggestion(text, kind))
    ranked = sorted(best.values(), key=lambda pair: (-pair[0], len(pair[1].text), pair[1].text.casefold()))
    return [item for _value, item in ranked[: max(1, int(limit))]]


def complete(query: str, corpus: Iterable[object]) -> str | None:
    """The single best completion if it actually extends `query`, else None."""
    found = suggest(query, corpus, limit=1)
    if not found:
        return None
    text = found[0].text
    return text if len(text) > len(_norm(query)) else None


def describe(items: Iterable[Suggestion]) -> str:
    labels = [item.text for item in items]
    return ", ".join(labels) if labels else "No suggestions"


__all__ = ["MAX_SUGGESTIONS", "Suggestion", "complete", "describe", "score", "suggest"]
