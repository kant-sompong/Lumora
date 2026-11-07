"""The command palette: one flat, searchable list of everything reachable.

The list is built here rather than in either frontend so GTK and Qt offer the
same commands in the same order.  Matching is a subsequence score, which is what
makes "opset" find "Open settings" as well as a plain prefix does.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

#: How many matches the palette shows at once.
MAX_RESULTS = 12

#: Pages that exist in both frontends, keyed by the stack page name.
PAGES: tuple[tuple[str, str], ...] = (
    ("archive", "Browse"),
    ("library", "Library"),
    ("discover", "Discover"),
    ("downloads", "Downloads"),
    ("media", "Movies & TV"),
)

#: Tabs that used to be a separate menu entry: key, title, group, page, tab.
TAB_TARGETS: tuple[tuple[str, str, str, str, str], ...] = (
    ("tab.discover.for-you", "Recommendations", "Discover", "discover", "for-you"),
    ("tab.discover.hidden-gems", "Hidden gems", "Discover", "discover", "hidden-gems"),
    ("tab.library.favourites", "Favourites", "Library", "library", "favourites"),
    ("tab.library.collections", "Collections", "Library", "library", "collections"),
    ("tab.library.tags", "Tags", "Library", "library", "tags"),
    ("tab.library.activity", "Activity", "Library", "library", "activity"),
    ("tab.library.statistics", "Statistics", "Library", "library", "statistics"),
    ("tab.library.recent", "Recently viewed", "Library", "library", "recent"),
    ("tab.media.calendar", "Release calendar", "Movies & TV", "media", "calendar"),
    ("tab.media.compare", "Compare seasons", "Movies & TV", "media", "compare"),
)

#: Settings pages and the actions that open them.
SETTINGS_PAGES: tuple[tuple[str, str], ...] = (
    ("general", "General settings"),
    ("locations", "Download locations"),
    ("providers", "Provider settings"),
    ("media", "Media settings"),
    ("shortcuts", "Keyboard shortcuts"),
)


@dataclass(frozen=True)
class Command:
    """One row in the palette."""

    key: str
    title: str
    group: str = ""
    hint: str = ""
    action: str = ""

    def label(self) -> str:
        return f"{self.title} ({self.group})" if self.group else self.title


def commands(*, with_settings: bool = True) -> list[Command]:
    """Every command, in a stable order: pages, then settings, then tools."""
    items = [
        Command(f"page.{name}", f"Go to {label}", "Page", hint=f"{label} page")
        for name, label in PAGES
    ]
    items += [
        Command(key, title, group, hint=f"{group} · {title} tab")
        for key, title, group, _page, _tab in TAB_TARGETS
    ]
    if with_settings:
        items += [
            Command(f"settings.{page}", label, "Settings", hint=f"Open {label}")
            for page, label in SETTINGS_PAGES
        ]
    items += [
        Command("tools.open", "Open tools", "Tools", hint="Providers, updates, plugins"),
        Command("search.focus", "Search the collection", "Tools", hint="Ctrl+K"),
        Command("updates.check", "Check for updates", "Tools"),
        Command("backup.create", "Create a backup", "Tools"),
        Command("diagnostics.create", "Save a support report", "Tools"),
    ]
    return items


def _is_subsequence(needle: str, haystack: str) -> bool:
    iterator = iter(haystack)
    return all(char in iterator for char in needle)


def score(query: str, command: Command) -> float:
    """How well ``query`` matches ``command``; ``0`` means it does not match.

    Ranking: title prefix beats a title word start, which beats a subsequence,
    which beats a match that only appears in the group or hint.
    """
    text = str(query or "").strip().casefold()
    if not text:
        return 1.0
    title = command.title.casefold()
    if title.startswith(text):
        return 100.0 - len(title) * 0.1
    words = title.replace("-", " ").split()
    if any(word.startswith(text) for word in words):
        return 80.0
    if text in title:
        return 60.0 - title.index(text) * 0.1
    if _is_subsequence(text, title):
        return 40.0 - len(title) * 0.1
    haystack = f"{command.group} {command.hint}".casefold()
    if text in haystack:
        return 20.0
    return 0.0


def search(
    query: str,
    *,
    items: Sequence[Command] | None = None,
    limit: int = MAX_RESULTS,
) -> list[Command]:
    """The commands matching ``query``, best first."""
    pool = list(items) if items is not None else commands()
    # Going to a page is the common intent, so it outranks a settings row that
    # happens to share the same words ("down" is Downloads before Download
    # locations).
    def _weighted(command: Command) -> float:
        base = score(query, command)
        if base <= 0:
            return 0.0
        return base + (20.0 if command.key.startswith("page.") else 0.0)

    scored = [(_weighted(command), command) for command in pool]
    matched = [(value, command) for value, command in scored if value > 0]
    matched.sort(key=lambda pair: (-pair[0], pair[1].title.casefold()))
    return [command for _value, command in matched[: max(1, int(limit))]]


def page_for(key: str) -> str:
    """The stack page a ``page.*`` command goes to, or an empty string."""
    text = str(key or "")
    return text.split(".", 1)[1] if text.startswith("page.") else ""


def action_for(command: Command) -> str | None:
    """What the frontend should do: a page name, a settings page, or an action."""
    if command.key.startswith("tab."):
        _prefix, page, tab = command.key.split(".", 2)
        return f"tab:{page}/{tab}"
    if command.key.startswith("page."):
        return command.key.split(".", 1)[1]
    if command.key.startswith("settings."):
        return command.key
    return command.action or command.key


def destination(command: Command) -> tuple[str, str, str]:
    """Where a command lands: ``("page", page, tab)``, ``("settings", page, "")``
    or ``("action", name, "")``."""
    key = command.key
    if key.startswith("tab."):
        _prefix, page, tab = key.split(".", 2)
        return ("page", page, tab)
    if key.startswith("page."):
        return ("page", page_for(key), "")
    if key.startswith("settings."):
        return ("settings", key.split(".", 1)[1], "")
    return ("action", str(action_for(command) or ""), "")


def flatten(items: Iterable[Command]) -> list[str]:
    return [item.label() for item in items]


__all__ = [
    "MAX_RESULTS",
    "PAGES",
    "SETTINGS_PAGES",
    "TAB_TARGETS",
    "Command",
    "action_for",
    "commands",
    "destination",
    "flatten",
    "page_for",
    "score",
    "search",
]
