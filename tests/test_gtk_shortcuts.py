"""The GTK shortcuts window and the bindings the main window obeys.

These skip themselves when GTK 3 is missing; the window tests also skip when
there is no display, since GTK 3 cannot create a window without one.
"""

import pytest

gi = pytest.importorskip("gi")

try:
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, Gtk
except (ValueError, ImportError):  # pragma: no cover - GTK 3 typelib missing
    pytest.skip("GTK 3 is not installed", allow_module_level=True)

from lgl import gtk_shortcuts, shortcut_prefs


class Settings(dict):
    """The slice of the settings API the editor uses."""

    def set(self, key, value):
        self[key] = value

    def save(self):
        return True


def press(keys):
    """The keyval and state Gdk reports for an accelerator string."""
    keyval, mods = Gtk.accelerator_parse(keys)
    return int(keyval), int(mods)


def window(settings):
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    return gtk_shortcuts.ShortcutsWindow(settings)


# -- the bindings ------------------------------------------------------------


def test_every_action_is_listed_once_with_a_default_and_a_label():
    ids = [binding.id for binding in gtk_shortcuts.BINDINGS]
    assert len(ids) == len(set(ids))
    assert set(ids) == set(gtk_shortcuts.DEFAULTS)
    assert all(gtk_shortcuts.DEFAULTS[action_id] for action_id in ids)
    assert all(gtk_shortcuts.label_for(gtk_shortcuts.DEFAULTS[i]) != "Not set" for i in ids)
    assert gtk_shortcuts.validate(gtk_shortcuts.DEFAULTS) == ""


def test_a_key_press_maps_to_its_action():
    settings = Settings()
    assert gtk_shortcuts.action_for(settings, *press("<Control>1")) == "nav.browse"
    assert gtk_shortcuts.action_for(settings, *press("<Control>2")) == "nav.library"
    assert gtk_shortcuts.action_for(settings, *press("<Control>f")) == "search"
    assert gtk_shortcuts.action_for(settings, *press("<Control>k")) == "search"
    assert gtk_shortcuts.action_for(settings, *press("F5")) == "refresh"
    assert gtk_shortcuts.action_for(settings, *press("<Control><Shift>p")) == "palette"
    assert gtk_shortcuts.action_for(settings, *press("<Control>comma")) == "settings"
    assert gtk_shortcuts.action_for(settings, *press("a")) == ""


def test_a_saved_key_replaces_the_default_the_main_window_uses():
    settings = Settings()
    wanted = dict(gtk_shortcuts.DEFAULTS)
    wanted["search"] = "<Control><Shift>f"
    assert gtk_shortcuts.save_bindings(settings, wanted) == ""
    assert shortcut_prefs.overrides(settings)["search"] == "<Control><Shift>f"
    assert gtk_shortcuts.action_for(settings, *press("<Control><Shift>f")) == "search"
    # Ctrl+F is free again, but the reserved alias keeps Ctrl+K on search.
    assert gtk_shortcuts.action_for(settings, *press("<Control>f")) == ""
    assert gtk_shortcuts.action_for(settings, *press("<Control>k")) == "search"


def test_a_key_that_is_already_used_is_refused():
    wanted = dict(gtk_shortcuts.DEFAULTS)
    wanted["refresh"] = "<Control>f"
    assert "already used" in gtk_shortcuts.validate(wanted)

    wanted["refresh"] = "<control>F"  # same key, spelled differently
    assert "already used" in gtk_shortcuts.validate(wanted)

    wanted["refresh"] = "<Control>k"  # reserved for Focus search
    assert "already used" in gtk_shortcuts.validate(wanted)


def test_a_refused_key_is_not_saved():
    settings = Settings()
    wanted = dict(gtk_shortcuts.DEFAULTS)
    wanted["refresh"] = "<Control>f"
    assert gtk_shortcuts.save_bindings(settings, wanted) != ""
    assert shortcut_prefs.overrides(settings) == {}


def test_keys_that_would_break_typing_or_closing_are_refused():
    for keys in ("a", "1", "Escape", ""):
        wanted = dict(gtk_shortcuts.DEFAULTS)
        wanted["refresh"] = keys
        assert gtk_shortcuts.validate(wanted), keys


def test_a_broken_saved_key_falls_back_to_the_default():
    settings = Settings({"shortcut_overrides": {"refresh": "nonsense"}})
    assert gtk_shortcuts.resolved(settings)["refresh"] == "F5"


def test_no_settings_and_junk_overrides_leave_the_defaults():
    defaults = {binding.id: binding.default for binding in gtk_shortcuts.BINDINGS}
    assert gtk_shortcuts.resolved(None) == defaults
    settings = Settings({"shortcut_overrides": {"nav.browse": "not a key"}})
    current = gtk_shortcuts.resolved(settings)
    assert gtk_shortcuts.canonical(current["nav.browse"]) == gtk_shortcuts.canonical(
        "<Control>1"
    )


# -- the window --------------------------------------------------------------


def test_the_window_lists_every_action_with_its_key():
    settings = Settings()
    win = window(settings)
    try:
        assert len(win._buttons) == len(gtk_shortcuts.BINDINGS)
        assert win._buttons["refresh"].get_label() == gtk_shortcuts.label_for("F5")
    finally:
        win.destroy()


def test_clicking_a_key_then_pressing_one_rebinds_it():
    settings = Settings()
    win = window(settings)
    try:
        win.begin_capture("refresh")
        assert "Press" in win._buttons["refresh"].get_label()

        class Event:
            keyval = Gdk.KEY_r
            state = int(Gdk.ModifierType.CONTROL_MASK) | int(Gdk.ModifierType.SHIFT_MASK)

        assert win._on_key(win, Event()) is True
        assert gtk_shortcuts.canonical(win.values()["refresh"]) == gtk_shortcuts.canonical(
            "<Control><Shift>r"
        )
        assert win.save() == ""
        assert gtk_shortcuts.action_for(settings, *press("<Control><Shift>r")) == "refresh"
    finally:
        win.destroy()


def test_the_window_refuses_a_key_another_action_uses():
    settings = Settings()
    win = window(settings)
    try:
        win.begin_capture("refresh")
        message = win.bind("refresh", "<Control>f")
        assert "already used" in message
        assert gtk_shortcuts.canonical(win.values()["refresh"]) == gtk_shortcuts.canonical("F5")
    finally:
        win.destroy()


def test_escape_cancels_a_capture_without_changing_the_key():
    settings = Settings()
    win = window(settings)
    try:
        win.begin_capture("refresh")

        class Event:
            keyval = Gdk.KEY_Escape
            state = 0

        assert win._on_key(win, Event()) is True
        assert win._buttons["refresh"].get_label() == gtk_shortcuts.label_for("F5")
        assert win.save() == ""
    finally:
        win.destroy()


def test_reset_restores_every_default():
    settings = Settings()
    win = window(settings)
    try:
        assert win.bind("refresh", "<Control><Shift>r") == ""
        assert win.save() == ""
        win.reset()
        assert shortcut_prefs.overrides(settings) == {}
        assert gtk_shortcuts.action_for(settings, *press("F5")) == "refresh"
        assert win._buttons["refresh"].get_label() == gtk_shortcuts.label_for("F5")
    finally:
        win.destroy()
