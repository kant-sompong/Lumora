"""Split a repack title into the parts a person actually reads.

A catalog title carries three different kinds of information at once::

    "Hollow Knight: Silksong (v1.0.30000) [Linux, GOG]"
    "Two Point Museum + 6 DLC (v12.0.244617) [Steam-Rip]"

The grid wants the game's name, the detail page wants the version and the tags,
and the raw title belongs in a tooltip.  This module is the one place that
splits them, so a tile and a row cannot disagree about what a game is called.

It is deliberately pure: no GTK, no I/O, no network.  Everything it returns for
a given string is deterministic, which is what makes it testable and what lets
the Tkinter frontend reuse it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_OPEN = "([{"
_CLOSE = ")]}"
#: A parenthetical that is just a year is a release date, not a tag.
_YEAR = re.compile(r"^(?:19|20)\d{2}$")
#: ``v1.0.30000``, ``1.0.30000``, ``v1.2.3a``, ``version 2.1``.
_VERSION = re.compile(
    r"^(?:(?:v|ver\.?|version)\s*)?\d+(?:\.\d+)+[0-9a-z\-]*$"
    r"|^v\d+[0-9a-z\-]*$",
    re.IGNORECASE,
)
#: ``+ 6 DLC`` / ``+6 DLCs`` / ``+ 2 expansions``: counted extras.
_DLC = re.compile(r"\+\s*(\d+)\s*(?:dlc|dlcs|expansion|expansions|episode|episodes)\b", re.IGNORECASE)
#: An uncounted ``+ DLC`` / ``+ All DLC`` suffix still belongs to the extras.
_PLUS_EXTRA = re.compile(r"\+\s*(?:\d+\s*)?(?:dlc|dlcs|expansions?|episodes?|soundtrack)\b", re.IGNORECASE)
_NAME_TRIM = " \t-_:;,.·|/+–—"


@dataclass(frozen=True)
class ParsedTitle:
    """One catalog title, taken apart."""

    raw: str
    name: str
    version: str = ""
    extras: str = ""
    tags: tuple[str, ...] = ()
    year: str = ""

    @property
    def label(self) -> str:
        """The grid/list label: the name, plus a counted extra when there is one."""
        if self.extras:
            return f"{self.name} {self.extras}".strip()
        return self.name

    @property
    def tooltip(self) -> str:
        """The untouched original, for a tooltip."""
        return self.raw

    @property
    def is_empty(self) -> bool:
        return not self.name


def split_groups(text: str) -> tuple[str, list[str]]:
    """Return (*text outside the brackets*, *top-level bracket contents*).

    Nesting is tracked by depth so ``"[Linux (GOG), v1.0]"`` yields one group and
    not two fragments; an unterminated bracket keeps its text instead of
    silently dropping part of the title.
    """
    kept: list[str] = []
    groups: list[str] = []
    buffer: list[str] = []
    depth = 0
    for char in str(text or ""):
        if char in _OPEN:
            if depth:
                buffer.append(char)
            depth += 1
            continue
        if char in _CLOSE and depth:
            depth -= 1
            if depth == 0:
                groups.append("".join(buffer).strip())
                buffer.clear()
            else:
                buffer.append(char)
            continue
        if depth:
            buffer.append(char)
        else:
            kept.append(char)
    if buffer:
        kept.append("".join(buffer))
    return "".join(kept), [group for group in groups if group]


def clean_version(value: Any) -> str:
    """``"v1.0.30000"`` -> ``"1.0.30000"``; anything unusable -> ``""``."""
    text = " ".join(str(value or "").split())
    text = re.sub(r"^(?:version|ver\.?|v)\s*", "", text, flags=re.IGNORECASE)
    if not text or not _VERSION.match(str(value or "") + ("" if text == value else "")):
        # The original had to look like a version; fall back to a plain check.
        if not re.match(r"^\d+(?:\.\d+)*[0-9a-z\-]*$", text, re.IGNORECASE):
            return ""
    return text[:40]


def clean_name(value: Any) -> str:
    """Collapse whitespace and remove separators left where a suffix was."""
    text = " ".join(str(value or "").replace("\u00a0", " ").split())
    text = re.sub(r"\s*\+\s*$", "", text)
    text = text.strip(_NAME_TRIM)
    # A colon or dash left dangling by a removed subtitle reads like a typo.
    text = re.sub(r"\s*[-–—]\s*$", "", text)
    return text.strip(_NAME_TRIM)


def parse(raw: Any) -> ParsedTitle:
    """Take one raw title apart.  Never raises, whatever it is handed."""
    text = " ".join(str(raw or "").replace("\u00a0", " ").split())
    if not text:
        return ParsedTitle(raw="", name="")

    outside, groups = split_groups(text)
    version = ""
    year = ""
    tags: list[str] = []
    for group in groups:
        for piece in re.split(r"[,;/]", group):
            piece = piece.strip()
            if not piece:
                continue
            if not year and _YEAR.match(piece):
                year = piece
                continue
            if not version and _VERSION.match(piece):
                version = clean_version(piece)
                if version:
                    continue
            if piece.casefold() not in {tag.casefold() for tag in tags}:
                tags.append(piece)

    extras = ""
    # A repack sometimes writes the extra inside the brackets: "(+ 6 DLC)".
    counted = _DLC.search(outside) or _DLC.search(" ".join(groups))
    if counted:
        number = int(counted.group(1))
        extras = f"+ {number} DLC"
    elif _PLUS_EXTRA.search(outside):
        extras = "+ DLC"
    without_extras = _PLUS_EXTRA.sub(" ", outside)

    name = clean_name(without_extras)
    if not name:
        # A title made only of suffixes ("(v1.0) [Linux]") is still a title.
        name = clean_name(outside) or text
    return ParsedTitle(
        raw=text,
        name=name[:300],
        version=version,
        extras=extras,
        tags=tuple(tags[:12]),
        year=year,
    )


def sort_name(raw: Any) -> str:
    """The name used for alphabetical order, without a leading article.

    ``"The Long Dark"`` sorts under **L**, which is what every game library does
    and what a person expects when they look for it.
    """
    name = parse(raw).name.casefold()
    for article in ("the ", "a ", "an "):
        if name.startswith(article):
            return name[len(article):]
    return name


__all__ = [
    "ParsedTitle",
    "clean_name",
    "clean_version",
    "parse",
    "sort_name",
    "split_groups",
]
