"""The GTK 3 window for rebinding the app's keyboard shortcuts.

The action list is here because the defaults are GTK accelerator strings; the
user's changes are kept by :mod:`lgl.shortcut_prefs`, the same store the Qt
editor writes, so the rules (no empty keys, no key used twice) are shared.  The
main window asks :func:`action_for` on every key press, which means a saved
binding works immediately instead of on the next start.

Every action in :data:`BINDINGS` is rebindable and is always listed, so the
window can never open empty.  ``Escape`` is refused because it closes windows,
and so is a bare printable key, which would swallow typing in the search box;
Ctrl or Alt combinations and the function keys are accepted.

GTK 3 only: :mod:`lgl.ui` has already pinned the namespace by the time this
module loads, and asking for 4.0 would raise.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk, Pango

from . import shortcut_prefs

#: Used when there is no display to ask for the real mask (a bare test run);
#: ``Gtk.accelerator_get_default_mod_mask`` needs a keymap.
_FALLBACK_MODIFIERS = int(
    Gdk.ModifierType.SHIFT_MASK
    | Gdk.ModifierType.CONTROL_MASK
    | Gdk.ModifierType.MOD1_MASK
    | Gdk.ModifierType.SUPER_MASK
)


@dataclass(frozen=True)
class Binding:
    """One rebindable action and the key it ships with."""

    id: str
    label: str
    default: str
    description: str = ""
    group: str = "General"


BINDINGS: tuple[Binding, ...] = (
    Binding(
        "nav.browse", "Go to Browse", "<Control>1",
        "Switches to the collection page.", "Navigation",
    ),
    Binding(
        "nav.library", "Go to Library", "<Control>2",
        "Switches to your downloaded games.", "Navigation",
    ),
    Binding(
        "downloads.toggle", "Show or hide the download queue", "<Control>d",
        "Opens the queue, or hides it again.", "Navigation",
    ),
    Binding(
        "search", "Focus search", "<Control>f",
        "Puts the cursor in the search box; Ctrl+K does the same.", "Search",
    ),
    Binding(
        "refresh", "Refresh", "F5",
        "Reloads the current page.", "General",
    ),
    Binding(
        "palette", "Command palette", "<Control><Shift>p",
        "Opens the command palette.", "General",
    ),
    Binding(
        "settings", "Open settings", "<Control>comma",
        "Opens the settings window.", "Navigation",
    ),
)

#: Extra keys that stay bound to an action without being its binding, so the
#: header's "Ctrl K" hint keeps working.  They are reserved: :func:`validate`
#: refuses to give them to another action.
ALIASES: dict[str, tuple[str, ...]] = {"search": ("<Control>k",)}

DEFAULTS: dict[str, str] = {binding.id: binding.default for binding in BINDINGS}
LABELS: dict[str, str] = {binding.id: binding.label for binding in BINDINGS}


# -- accelerator handling ----------------------------------------------------


def _parse(keys: object) -> tuple[int, int]:
    """``(keyval, mods)`` for an accelerator string; ``(0, 0)`` when unusable.

    PyGObject has returned both a two-tuple and a ``(ok, keyval, mods)``
    three-tuple over the years, so both shapes are accepted here.
    """
    text = " ".join(str(keys or "").split())
    if not text:
        return 0, 0
    try:
        parsed = Gtk.accelerator_parse(text)
    except (TypeError, ValueError):
        return 0, 0
    values = list(parsed) if isinstance(parsed, (tuple, list)) else []
    try:
        if len(values) == 3:
            ok, keyval, mods = values
            return (int(keyval), int(mods)) if ok else (0, 0)
        if len(values) == 2:
            return int(values[0]), int(values[1])
    except (TypeError, ValueError):
        return 0, 0
    return 0, 0


def _mod_mask() -> int:
    try:
        mask = Gtk.accelerator_get_default_mod_mask()
    except (AttributeError, TypeError):
        return _FALLBACK_MODIFIERS
    try:
        return int(mask)
    except (TypeError, ValueError):
        return _FALLBACK_MODIFIERS


def _canonical(keyval: int, state: int) -> str:
    """Normalised form used to compare two bindings, or "" for no key."""
    if int(keyval) <= 0:
        return ""
    mods = int(state) & _mod_mask()
    try:
        name = Gtk.accelerator_name(int(keyval), Gdk.ModifierType(mods)) or ""
    except (TypeError, ValueError):
        return ""
    return name.strip().lower()


def canonical(keys: object) -> str:
    """Normalised form of an accelerator string, or "" when it is not one."""
    return _canonical(*_parse(keys))


def label_for(keys: object) -> str:
    """How a key combination reads in the window: ``<Control>f`` -> ``Ctrl+F``."""
    keyval, mods = _parse(keys)
    if keyval <= 0:
        return "Not set"
    try:
        return Gtk.accelerator_get_label(keyval, Gdk.ModifierType(mods)) or "Not set"
    except (TypeError, ValueError):
        return "Not set"


#: Modifiers are written in this order, so one key always saves the same
#: way whatever order the user pressed them in.
_MODIFIER_ORDER = (
    ("Control", int(Gdk.ModifierType.CONTROL_MASK)),
    ("Alt", int(Gdk.ModifierType.MOD1_MASK)),
    ("Shift", int(Gdk.ModifierType.SHIFT_MASK)),
    ("Super", int(Gdk.ModifierType.SUPER_MASK)),
)


def _storage(keyval: int, mods: int) -> str:
    """The string saved in the settings file: ``<Control>f``, ``F5``."""
    if int(keyval) <= 0:
        return ""
    name = Gdk.keyval_name(int(keyval))
    if not name:
        return ""
    mods = int(mods) & _mod_mask()
    parts = "".join(f"<{label}>" for label, mask in _MODIFIER_ORDER if mods & mask)
    return parts + name


def _usable(keyval: int, state: int) -> bool:
    """Whether this key may be bound at all."""
    keyval = int(keyval)
    if keyval <= 0 or keyval == Gdk.KEY_Escape:
        return False
    if int(state) & _mod_mask():
        return True
    # A function key types nothing, so it needs no modifier; a bare letter does.
    return Gdk.KEY_F1 <= keyval <= Gdk.KEY_F35


# -- what the main window asks ----------------------------------------------


def resolved(settings: Any) -> dict[str, str]:
    """Every action's current key, with unusable overrides falling back."""
    stored = shortcut_prefs.effective(settings, DEFAULTS)
    current: dict[str, str] = {}
    for binding in BINDINGS:
        keyval, mods = _parse(stored.get(binding.id))
        saved = _storage(keyval, mods) if _usable(keyval, mods) else ""
        current[binding.id] = saved or binding.default
    return current


def action_for(settings: Any, keyval: int, state: int) -> str:
    """The action bound to this key press, or "" when nothing is."""
    pressed = _canonical(int(keyval), int(state))
    if not pressed:
        return ""
    current = resolved(settings)
    for binding in BINDINGS:
        if pressed == canonical(current.get(binding.id, binding.default)):
            return binding.id
        for extra in ALIASES.get(binding.id, ()):
            if pressed == canonical(extra):
                return binding.id
    return ""


def validate(wanted: Mapping[str, str]) -> str:
    """Why these bindings cannot be saved, or "" when they can."""
    seen: dict[str, str] = {}
    for action_id, keys in wanted.items():
        action_id = str(action_id)
        label = LABELS.get(action_id, action_id)
        keyval, mods = _parse(keys)
        if keyval <= 0:
            return f"{label} needs a key."
        if not _usable(keyval, mods):
            return f"{label}: {label_for(keys)} cannot be used here."
        if not _storage(keyval, mods):
            return f"{label}: that key cannot be bound."
        normal = _canonical(keyval, mods)
        owner = seen.get(normal)
        if owner is not None:
            return f"{label_for(keys)} is already used by {LABELS.get(owner, owner)}."
        seen[normal] = action_id
    for action_id, extras in ALIASES.items():
        for extra in extras:
            owner = seen.get(canonical(extra))
            if owner and owner != action_id:
                return (
                    f"{label_for(extra)} is already used by "
                    f"{LABELS.get(owner, owner)}."
                )
    return ""


def save_bindings(settings: Any, wanted: Mapping[str, str]) -> str:
    """Write the bindings to the settings file; "" means saved."""
    problem = validate(wanted)
    if problem:
        return problem
    stored = {
        str(action_id): _storage(*_parse(keys)) for action_id, keys in wanted.items()
    }
    if not shortcut_prefs.set_bindings(settings, stored):
        return "These shortcuts could not be saved."
    return ""


# -- the window --------------------------------------------------------------


class ShortcutsWindow(Gtk.Window):
    """Every action with its current key; click a key, then press the new one."""

    def __init__(self, settings: Any = None, *, owner: Gtk.Window | None = None) -> None:
        super().__init__(title="Keyboard shortcuts")
        self.set_default_size(540, 520)
        self.set_modal(True)
        if isinstance(owner, Gtk.Window):
            self.set_transient_for(owner)
            self.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
        self._settings = settings
        self._draft: dict[str, str] = {}
        self._buttons: dict[str, Gtk.Button] = {}
        self._capturing: str | None = None

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        outer.set_margin_top(16)
        outer.set_margin_bottom(14)
        outer.set_margin_start(16)
        outer.set_margin_end(16)

        heading = Gtk.Label(label="KEYBOARD SHORTCUTS", xalign=0)
        heading.get_style_context().add_class("section-label")
        outer.pack_start(heading, False, False, 0)

        hint = Gtk.Label(
            label="Click a key, then press the combination you want. "
                  "Escape cancels the capture; Save keeps the changes.",
            xalign=0,
        )
        hint.set_line_wrap(True)
        hint.get_style_context().add_class("dim")
        outer.pack_start(hint, False, False, 0)

        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        for group, bindings in _grouped():
            group_label = Gtk.Label(label=group.upper(), xalign=0)
            group_label.get_style_context().add_class("section-label")
            group_label.set_margin_top(6)
            rows.pack_start(group_label, False, False, 0)
            for binding in bindings:
                rows.pack_start(self._build_row(binding), False, False, 0)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.add(rows)
        outer.pack_start(scroller, True, True, 0)

        self._note = Gtk.Label(label="", xalign=0)
        self._note.set_line_wrap(True)
        self._note.set_no_show_all(True)
        outer.pack_start(self._note, False, False, 0)

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        reset = Gtk.Button(label="Reset")
        reset.set_tooltip_text("Restore every key to the one Lumora ships with")
        reset.connect("clicked", lambda *_: self.reset())
        controls.pack_start(reset, False, False, 0)
        controls.pack_start(Gtk.Box(), True, True, 0)
        close = Gtk.Button(label="Close")
        close.connect("clicked", lambda *_: self.destroy())
        controls.pack_end(close, False, False, 0)
        self._save_btn = Gtk.Button(label="Save")
        self._save_btn.set_can_default(True)
        self._save_btn.connect("clicked", lambda *_: self._save_clicked())
        controls.pack_end(self._save_btn, False, False, 0)
        outer.pack_start(controls, False, False, 0)

        self.add(outer)
        self.connect("key-press-event", self._on_key)
        self.show_all()
        self._load()

    # -- construction -------------------------------------------------------
    def _build_row(self, binding: Binding) -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.set_margin_top(2)
        label = Gtk.Label(label=binding.label, xalign=0)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_hexpand(True)
        if binding.description:
            row.set_tooltip_text(binding.description)
        row.pack_start(label, True, True, 0)
        button = Gtk.Button(label="")
        button.set_tooltip_text(binding.description or binding.label)
        button.connect("clicked", lambda _button, action_id=binding.id: self.begin_capture(action_id))
        row.pack_end(button, False, False, 0)
        self._buttons[binding.id] = button
        return row

    # -- state --------------------------------------------------------------
    def values(self) -> dict[str, str]:
        """The bindings as edited, before Save is pressed."""
        return dict(self._draft)

    def _load(self) -> None:
        self._draft = resolved(self._settings)
        self._capturing = None
        self._show_current()

    def _show_current(self) -> None:
        for action_id, button in self._buttons.items():
            button.set_label(label_for(self._draft.get(action_id, "")))

    def _set_note(self, text: str) -> None:
        self._note.set_text(text)
        if text:
            self._note.show()
        else:
            self._note.hide()

    # -- capturing ----------------------------------------------------------
    def begin_capture(self, action_id: str) -> None:
        """Wait for the next key press and give it to *action_id*."""
        if action_id not in self._buttons:
            return
        self._capturing = action_id
        self._buttons[action_id].set_label("Press a key…")
        self._set_note(f"Press the new combination for {LABELS.get(action_id, action_id)}.")

    def _end_capture(self) -> None:
        self._capturing = None
        self._show_current()

    def _on_key(self, _window: Gtk.Window, event: Gdk.EventKey) -> bool:
        if not self._capturing:
            return False
        keyval = int(getattr(event, "keyval", 0) or 0)
        state = int(getattr(event, "state", 0) or 0)
        if keyval == Gdk.KEY_Escape:
            self._end_capture()
            self._set_note("")
            return True
        if not _usable(keyval, state):
            self._set_note(
                "Use Ctrl or Alt with a letter, or a function key such as F6. "
                "Escape cancels."
            )
            return True
        action_id = self._capturing
        keys = _storage(keyval, state)
        problem = self.bind(action_id, keys)
        self._end_capture()
        if problem:
            self._set_note(problem)
        else:
            self._set_note(
                f"{LABELS.get(action_id, action_id)} is now {label_for(keys)}. "
                "Choose Save to keep it."
            )
        return True

    def bind(self, action_id: str, keys: str) -> str:
        """Give *action_id* the *keys*; returns the reason when refused."""
        wanted = dict(self._draft)
        wanted[action_id] = str(keys)
        problem = validate(wanted)
        if problem:
            return problem
        self._draft = wanted
        self._show_current()
        return ""

    # -- saving -------------------------------------------------------------
    def save(self) -> str:
        """Store the edited bindings; "" means they were saved."""
        return save_bindings(self._settings, self._draft)

    def reset(self) -> None:
        """Forget every override, restoring the keys Lumora ships with."""
        shortcut_prefs.reset(self._settings)
        self._load()
        self._set_note("Restored the default keys.")

    def _save_clicked(self) -> None:
        problem = self.save()
        if problem:
            self._set_note(problem)
            return
        self._load()
        self._set_note("Saved. The new keys work straight away.")


def _grouped() -> list[tuple[str, list[Binding]]]:
    groups: dict[str, list[Binding]] = {}
    for binding in BINDINGS:
        groups.setdefault(binding.group, []).append(binding)
    return list(groups.items())


def show_shortcuts(owner: Any = None, settings: Any = None) -> ShortcutsWindow:
    """Open the shortcuts window over ``owner`` and return it."""
    window = ShortcutsWindow(
        settings, owner=owner if isinstance(owner, Gtk.Window) else None
    )
    window.show_all()
    window.present()
    return window


__all__ = [
    "ALIASES",
    "BINDINGS",
    "DEFAULTS",
    "LABELS",
    "Binding",
    "ShortcutsWindow",
    "action_for",
    "canonical",
    "label_for",
    "resolved",
    "save_bindings",
    "show_shortcuts",
    "validate",
]
