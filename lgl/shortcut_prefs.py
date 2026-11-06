"""User overrides for keyboard bindings, stored in the settings file.

The defaults live with the frontend; this module only remembers what the user
changed and resolves it against those defaults, rejecting a binding that is
empty or already taken by another action.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

OVERRIDES_KEY = "shortcut_overrides"


def overrides(settings: Any) -> dict[str, str]:
    """Every stored binding, junk-tolerant."""
    if settings is None:
        return {}
    try:
        raw = settings.get(OVERRIDES_KEY)
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(raw, Mapping):
        return {}
    return {
        str(key): str(value).strip()
        for key, value in raw.items()
        if str(value or "").strip()
    }


def binding(settings: Any, shortcut_id: str, default: str) -> str:
    return overrides(settings).get(str(shortcut_id), default)


def effective(settings: Any, defaults: Mapping[str, str]) -> dict[str, str]:
    """The defaults with every valid override applied."""
    stored = overrides(settings)
    return {
        str(shortcut_id): stored.get(str(shortcut_id), str(keys))
        for shortcut_id, keys in defaults.items()
    }


def _write(settings: Any, mapping: Mapping[str, str]) -> bool:
    setter = getattr(settings, "set", None)
    if settings is None or not callable(setter):
        return False
    setter(OVERRIDES_KEY, dict(mapping))
    saver = getattr(settings, "save", None)
    if callable(saver):
        try:
            saver()
        except Exception:  # noqa: BLE001
            return False
    return True


def conflicts(bindings: Mapping[str, str]) -> list[tuple[str, str]]:
    """Pairs of action ids that share a binding, ignoring case and spacing."""
    seen: dict[str, str] = {}
    clashes: list[tuple[str, str]] = []
    for shortcut_id, keys in bindings.items():
        normal = "".join(str(keys).lower().split())
        if not normal:
            continue
        other = seen.get(normal)
        if other is not None:
            clashes.append((other, str(shortcut_id)))
        else:
            seen[normal] = str(shortcut_id)
    return clashes


def set_bindings(settings: Any, wanted: Mapping[str, str]) -> bool:
    """Store overrides, refusing empty keys or a binding used twice."""
    cleaned = {
        str(key): " ".join(str(value).split())
        for key, value in wanted.items()
        if str(value or "").strip()
    }
    if len(cleaned) != len(wanted) or conflicts(cleaned):
        return False
    return _write(settings, cleaned)


def reset(settings: Any, shortcut_id: str | None = None) -> bool:
    """Forget one override, or every one of them."""
    mapping = overrides(settings)
    if shortcut_id is None:
        mapping = {}
    else:
        mapping.pop(str(shortcut_id), None)
    return _write(settings, mapping)


__all__ = [
    "OVERRIDES_KEY",
    "binding",
    "conflicts",
    "effective",
    "overrides",
    "reset",
    "set_bindings",
]
