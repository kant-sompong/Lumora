"""The GTK profiles window and the store behind it.

Skips itself when GTK 3 is missing; the window tests also skip without a display,
because GTK 3 cannot build a window without one.
"""

import pytest

gi = pytest.importorskip("gi")

try:
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
except (ValueError, ImportError):  # pragma: no cover - GTK 3 typelib missing
    pytest.skip("GTK 3 is not installed", allow_module_level=True)

from lgl import gtk_profiles
from lgl.profiles import PROFILES_FILE, Profiles


def store(tmp_path):
    return Profiles(tmp_path / PROFILES_FILE)


def window(profiles):
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    return gtk_profiles.ProfilesWindow(profiles)


def test_the_window_uses_the_shared_profile_file(tmp_path):
    profiles = store(tmp_path)
    assert gtk_profiles.PROFILES_FILE == PROFILES_FILE
    # The one profile every install starts with, so the list can never be empty.
    assert [profile.name for profile in profiles.list()] == ["Default"]
    assert "not accounts" in gtk_profiles.PROFILE_NOTE


def test_the_window_lists_profiles_and_marks_the_one_in_use(tmp_path):
    profiles = store(tmp_path)
    profiles.create("Couch")
    win = window(profiles)
    try:
        assert len(win._rows) == 2
        assert profiles.active_id in win._rows
        assert win._delete_btn.get_sensitive() is True
        assert "in use" in win._active_label.get_text()
        # A profile's folder is shown, since that is where its files live.
        assert str(profiles.data_dir(tmp_path, profiles.active_id)) in (
            win._rows[profiles.active_id].get_tooltip_text()
        )
    finally:
        win.destroy()


def test_create_rename_switch_and_delete(tmp_path):
    profiles = store(tmp_path)
    win = window(profiles)
    try:
        assert win.create("Couch") is True
        created = next(p for p in profiles.list() if p.name == "Couch")
        assert win.current_id() == created.id

        assert win.rename("Living room") is True
        assert profiles.get(created.id).name == "Living room"

        assert win.switch() is True
        assert profiles.active_id == created.id
        assert profiles.active().name == "Living room"

        assert win.delete() is True
        assert profiles.get(created.id) is None
        assert len(profiles.list()) == 1
    finally:
        win.destroy()


def test_a_duplicate_or_empty_name_is_refused(tmp_path):
    profiles = store(tmp_path)
    win = window(profiles)
    try:
        assert win.create("Couch") is True
        assert win.create("couch") is False
        assert win.create("   ") is False
        win.select(profiles.list()[0].id)
        assert win.rename("") is False
        assert len(profiles.list()) == 2
    finally:
        win.destroy()


def test_the_last_profile_can_never_be_deleted(tmp_path):
    profiles = store(tmp_path)
    win = window(profiles)
    try:
        assert win._delete_btn.get_sensitive() is False
        assert win.delete() is False
        assert [p.name for p in profiles.list()] == ["Default"]
    finally:
        win.destroy()


def test_a_switch_is_remembered_on_disk(tmp_path):
    profiles = store(tmp_path)
    win = window(profiles)
    try:
        assert win.create("Second") is True
        second = next(p for p in profiles.list() if p.name == "Second")
        assert win.switch() is True
    finally:
        win.destroy()
    assert Profiles(tmp_path / PROFILES_FILE).active_id == second.id
