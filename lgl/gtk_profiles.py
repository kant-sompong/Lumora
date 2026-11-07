"""The GTK 3 profiles window: list, create, rename, delete and switch.

The model is :mod:`lgl.profiles`; this window only draws it and forwards the
four actions, so both frontends follow the same rules: names are unique, the
last profile can never be deleted, and the active profile is remembered in
``profiles.json``.  Each profile already has its own folder for the library,
favourites and history, and every row shows that folder so it is obvious where a
profile's files live.

GTK 3 only, like the rest of the Linux frontend.
"""

from __future__ import annotations

from typing import Any, Callable

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Pango

from . import platform_support
from .profiles import DEFAULT_ID, PROFILES_FILE, SECURITY_NOTE, Profile, Profiles

#: Shown under the list.  Profiles are folders, not accounts, so this says what
#: they really separate and what a switch actually does.
PROFILE_NOTE = (
    f"{SECURITY_NOTE}\n\n"
    "Each profile keeps its own settings, library, favourites and history in its "
    "own folder. Switching records which profile is in use; restart Lumora and it "
    "reads that profile. The Default profile keeps the folder this install has "
    "always used, nothing is copied or moved, and no profile ever deletes game files."
)


class ProfileRow(Gtk.ListBoxRow):
    """One profile: its name, whether it is in use, and its folder."""

    def __init__(self, profile: Profile, *, active: bool, folder: str) -> None:
        super().__init__()
        self.profile_id = profile.id

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        name = Gtk.Label(label=profile.name, xalign=0)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.get_style_context().add_class("game-title")
        top.pack_start(name, True, True, 0)
        if active:
            tag = Gtk.Label(label="In use")
            tag.set_valign(Gtk.Align.CENTER)
            tag.get_style_context().add_class("status-pill")
            tag.get_style_context().add_class("status-completed")
            top.pack_end(tag, False, False, 0)
        box.pack_start(top, False, False, 0)

        path = Gtk.Label(label=folder, xalign=0)
        path.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        path.get_style_context().add_class("dim")
        box.pack_start(path, False, False, 0)

        self.add(box)
        self.set_tooltip_text(f"{profile.name}\n{folder}")
        self.get_accessible().set_name(
            f"{profile.name}, in use" if active else profile.name
        )


class ProfilesWindow(Gtk.Window):
    """The profile list with create, rename, delete and switch."""

    def __init__(self, profiles: Profiles, *, owner: Gtk.Window | None = None,
                 on_switch: Callable[[Profile], None] | None = None) -> None:
        super().__init__(title="Profiles")
        self.set_default_size(580, 520)
        self.set_modal(True)
        if isinstance(owner, Gtk.Window):
            self.set_transient_for(owner)
            self.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
        self._profiles = profiles
        self._on_switch = on_switch
        self._base = profiles.path.parent
        self._rows: dict[str, ProfileRow] = {}

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        outer.set_margin_top(16)
        outer.set_margin_bottom(14)
        outer.set_margin_start(16)
        outer.set_margin_end(16)

        heading = Gtk.Label(label="PROFILES", xalign=0)
        heading.get_style_context().add_class("section-label")
        outer.pack_start(heading, False, False, 0)

        self._active_label = Gtk.Label(label="", xalign=0)
        self._active_label.get_style_context().add_class("dim")
        outer.pack_start(self._active_label, False, False, 0)

        self._list = Gtk.ListBox()
        self._list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._list.set_activate_on_single_click(False)
        self._list.connect("row-activated", lambda *_: self.switch())
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.add(self._list)
        outer.pack_start(scroller, True, True, 0)

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._switch_btn = Gtk.Button(label="Use this profile")
        self._switch_btn.set_tooltip_text("Make this the profile Lumora uses")
        self._switch_btn.connect("clicked", lambda *_: self.switch())
        controls.pack_start(self._switch_btn, False, False, 0)

        self._new_btn = Gtk.Button(label="New…")
        self._new_btn.set_tooltip_text("Create another profile")
        self._new_btn.connect("clicked", lambda *_: self._new_clicked())
        controls.pack_start(self._new_btn, False, False, 0)

        self._rename_btn = Gtk.Button(label="Rename…")
        self._rename_btn.set_tooltip_text("Rename the selected profile")
        self._rename_btn.connect("clicked", lambda *_: self._rename_clicked())
        controls.pack_start(self._rename_btn, False, False, 0)

        self._delete_btn = Gtk.Button(label="Delete")
        self._delete_btn.set_tooltip_text("Delete the selected profile's entry; its files stay")
        self._delete_btn.connect("clicked", lambda *_: self._delete_clicked())
        controls.pack_start(self._delete_btn, False, False, 0)

        close = Gtk.Button(label="Close")
        close.connect("clicked", lambda *_: self.destroy())
        controls.pack_end(close, False, False, 0)
        outer.pack_start(controls, False, False, 0)

        self._status = Gtk.Label(label="", xalign=0)
        self._status.set_line_wrap(True)
        self._status.set_no_show_all(True)
        outer.pack_start(self._status, False, False, 0)

        note = Gtk.Label(label=PROFILE_NOTE, xalign=0)
        note.set_line_wrap(True)
        note.get_style_context().add_class("dim")
        outer.pack_start(note, False, False, 0)

        self.add(outer)
        self.refresh()

    # -- the list -----------------------------------------------------------
    def current_id(self) -> str:
        row = self._list.get_selected_row()
        return str(getattr(row, "profile_id", "") or "")

    def select(self, profile_id: str) -> bool:
        row = self._rows.get(str(profile_id))
        if row is None:
            return False
        self._list.select_row(row)
        return True

    def refresh(self) -> None:
        """Rebuild the list, keeping the selection where it was."""
        wanted = self.current_id() or self._profiles.active_id
        for child in self._list.get_children():
            self._list.remove(child)
        self._rows.clear()
        active = self._profiles.active_id
        for profile in self._profiles.list():
            folder = str(self._profiles.data_dir(self._base, profile.id))
            row = ProfileRow(profile, active=profile.id == active, folder=folder)
            self._rows[profile.id] = row
            self._list.add(row)
            if profile.id == wanted:
                self._list.select_row(row)
        if self._list.get_selected_row() is None and self._rows:
            first = next(iter(self._rows.values()))
            self._list.select_row(first)
        self._list.show_all()

        active_profile = self._profiles.active()
        count = len(self._rows)
        self._active_label.set_text(
            f"{active_profile.name} is in use · "
            f"{count} profile{'s' if count != 1 else ''}"
        )
        has_rows = bool(count)
        self._switch_btn.set_sensitive(has_rows)
        self._rename_btn.set_sensitive(has_rows)
        self._delete_btn.set_sensitive(count > 1)

    # -- actions ------------------------------------------------------------
    def switch(self) -> bool:
        profile_id = self.current_id()
        if not profile_id:
            return False
        changed = self._profiles.activate(profile_id)
        self.refresh()
        if changed:
            active = self._profiles.active()
            self._set_status(
                f"Restart Lumora to switch to {active.name}. "
                "Its settings, library, favourites and history are used from then on."
            )
            if callable(self._on_switch):
                try:
                    self._on_switch(active)
                except Exception:  # noqa: BLE001 - a UI callback must not break a switch
                    pass
        return changed

    def create(self, name: str) -> bool:
        profile = self._profiles.create(name)
        self.refresh()
        if profile is None:
            return False
        self.select(profile.id)
        self._set_status(f"Created {profile.name}.")
        return True

    def rename(self, name: str) -> bool:
        profile_id = self.current_id()
        changed = bool(profile_id) and self._profiles.rename(profile_id, name)
        self.refresh()
        if changed:
            self._set_status(f"Renamed to {name.strip()}.")
        return changed

    def delete(self) -> bool:
        profile_id = self.current_id()
        profile = self._profiles.get(profile_id)
        if not profile_id or profile_id == DEFAULT_ID:
            return False
        removed = self._profiles.remove(profile_id)
        self.refresh()
        if removed and profile is not None:
            self._set_status(f"Deleted {profile.name}. Its folder is still on disk.")
        return removed

    # -- button handlers ----------------------------------------------------
    def _set_status(self, text: str) -> None:
        self._status.set_text(text)
        if text:
            self._status.show()
        else:
            self._status.hide()

    def _warn(self, title: str, message: str) -> None:
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.OK,
            text=title,
        )
        dialog.format_secondary_text(message)
        dialog.run()
        dialog.destroy()

    def _ask_name(self, title: str, initial: str = "") -> str:
        dialog = Gtk.Dialog(title=title, transient_for=self, modal=True)
        dialog.add_buttons(
            "Cancel", Gtk.ResponseType.CANCEL, "OK", Gtk.ResponseType.OK
        )
        dialog.set_default_response(Gtk.ResponseType.OK)
        entry = Gtk.Entry()
        entry.set_text(initial)
        entry.set_activates_default(True)
        entry.set_width_chars(28)
        entry.set_margin_top(12)
        entry.set_margin_bottom(12)
        entry.set_margin_start(12)
        entry.set_margin_end(12)
        content = dialog.get_content_area()
        content.pack_start(entry, False, False, 0)
        dialog.show_all()
        response = dialog.run()
        text = entry.get_text()
        dialog.destroy()
        return text if response == Gtk.ResponseType.OK else ""

    def _new_clicked(self) -> None:
        name = self._ask_name("New profile")
        if not name:
            return
        if not self.create(name):
            self._warn(
                "New profile",
                "That name is empty or already used, or there are already as many "
                "profiles as Lumora allows.",
            )

    def _rename_clicked(self) -> None:
        profile = self._profiles.get(self.current_id())
        if profile is None:
            return
        name = self._ask_name("Rename profile", profile.name)
        if not name:
            return
        if not self.rename(name):
            self._warn("Rename profile", "That name is empty or already used.")

    def _delete_clicked(self) -> None:
        profile = self._profiles.get(self.current_id())
        if profile is None:
            return
        if profile.id == DEFAULT_ID:
            self._warn(
                "Delete profile",
                "The Default profile cannot be deleted: its folder holds this "
                "install's existing library and settings.",
            )
            return
        if len(self._profiles.list()) <= 1:
            self._warn("Delete profile", "The last profile cannot be deleted.")
            return
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=f"Delete “{profile.name}”?",
        )
        dialog.format_secondary_text(
            "The profile's entry is removed; the files in its folder are kept, "
            "and game files are never deleted."
        )
        response = dialog.run()
        dialog.destroy()
        if response == Gtk.ResponseType.YES:
            self.delete()


def show_profiles(owner: Any = None, profiles: Profiles | None = None,
                  on_switch: Callable[[Profile], None] | None = None) -> ProfilesWindow:
    """Open the profiles window over ``owner`` and return it."""
    store = profiles or Profiles(platform_support.data_dir() / PROFILES_FILE)
    window = ProfilesWindow(
        store, owner=owner if isinstance(owner, Gtk.Window) else None,
        on_switch=on_switch,
    )
    window.show_all()
    window.present()
    return window


__all__ = ["PROFILE_NOTE", "ProfileRow", "ProfilesWindow", "show_profiles"]
