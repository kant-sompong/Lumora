"""Decide which tags are actually genres.

archive.org does not have a genre field.  Its ``subject`` list mixes the item's
own title, the series it belongs to, and upload provenance ("GOG Games",
"ACTiVATED", "Uploaded by …", "Steam-Rip").  Those were being offered in the
Genre dropdown, so the list read like a second title column.

This module keeps the decision in one place and out of GTK: a tag is a genre
when every meaningful word in it is a genre word ("Action RPG",
"Puzzle-Platformer", "Massively Multiplayer") and it is not simply the title of
a record we already show.  Anything else — provenance, series names, titles —
is not offered, because there is nothing to filter by.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

#: Genre words, in the singular plus the spellings tags actually use.  A tag
#: must be made only of these (and :data:`_CONNECTORS`) to count as a genre.
_GENRE_WORDS = frozenset(
    """
    action adventure arcade board card casual educational family fighting
    horror indie massively multiplayer music party platformer platform puzzle
    racing rhythm rpg role playing shooter sim simulation sport sports strategy
    tactics tactical stealth survival trivia word sandbox roguelike roguelite
    metroidvania brawler mmo mmorpg moba fps tps fpp tpp jrpg 4x turn based real
    time tower defense defence visual novel point click hidden object bullet
    hell battle royale dating shmup shoot em up idle clicker management city
    building builder farming deckbuilder deck dungeon crawler open world first
    third person coop co op soulslike platforming hack slash beat space sci fi
    fantasy mystery detective adult nsfw mature erotic eroge hentai anime
    comedy narrative story historical modern exploration combat crafting
    economic managerial pinball chess programming espionage naval rally soccer
    off road touring team flight walking simulator interactive fiction virtual
    life game games
    """.split()
)

#: Words that join genre words without changing the meaning ("Point **&**
#: Click", "Role-Playing" splits into role/playing anyway).
_CONNECTORS = frozenset({"and", "or", "the", "of", "a", "an", "with"})

_SPLIT = re.compile(r"[^0-9a-z]+")

#: Longer than any real genre label ("Massively Multiplayer Online Role
#: Playing Game" is the longest common one); stops a title sneaking through.
MAX_WORDS = 8


def normalize(value: object) -> str:
    """A tag as a comparable string: collapsed whitespace, casefolded."""
    return " ".join(str(value or "").split()).casefold()


def is_genre(tag: object, *, titles: Iterable[str] = ()) -> bool:
    """Is *tag* a genre, as opposed to a title or upload provenance?"""
    text = normalize(tag)
    if not text or not any(char.isalpha() for char in text):
        return False
    if text in {normalize(title) for title in titles}:
        return False
    words = [word for word in _SPLIT.split(text) if word and word not in _CONNECTORS]
    if not words or len(words) > MAX_WORDS:
        return False
    return all(word in _GENRE_WORDS for word in words)


def genre_options(
    values: Iterable[object], *, titles: Iterable[str] = ()
) -> list[str]:
    """The genre labels to offer, sorted, deduplicated, in first-seen spelling.

    Titles are normalized *once*: with thousands of records and a tag per
    record, normalizing them per tag turned one dropdown refresh into tens of
    seconds.
    """
    known = {normalize(title) for title in titles}
    seen: dict[str, str] = {}
    for value in values:
        text = " ".join(str(value or "").split())
        key = text.casefold()
        if key in seen or key in known:
            continue
        if not is_genre(text):
            continue
        seen[key] = text
    return [seen[key] for key in sorted(seen)]
