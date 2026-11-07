"""The GTK tools window: the same sections the Qt window shows.

Written against GTK 3, the toolkit the rest of the Linux app uses.  GTK 3 and 4
cannot be loaded into one process, so this file must not ask for 4.0.

Layout and wording come from :mod:`lgl.tool_panels`; this file only draws rows
and forwards actions, which is what keeps the two platforms behaving alike.

Long work runs on a worker thread and reports back through ``GLib.idle_add``, so
checking for updates never freezes the window.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Sequence

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from . import __version__, backup, diagnostics, platform_support, updates, tool_panels  # noqa: E402


def _snapshot(settings: Any) -> dict[str, Any]:
    if settings is None:
        return {}
    for name in ("as_dict", "to_dict"):
        method = getattr(settings, name, None)
        if callable(method):
            try:
                value = method()
            except Exception:  # noqa: BLE001
                continue
            if isinstance(value, dict):
                return dict(value)
    data = getattr(settings, "data", None)
    return dict(data) if isinstance(data, dict) else {}


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


class ToolsWindow(Gtk.Window):
    """A stack of feature pages; every change applies immediately."""

    def __init__(
        self,
        settings: Any,
        *,
        profile_store: Any = None,
        media_roots: Sequence[str] = (),
        plugins: Sequence[Any] = (),
        rejected_plugins: Sequence[Any] = (),
        upcoming: Sequence[Any] = (),
        transient_for: Gtk.Window | None = None,
    ):
        super().__init__(title="Tools")
        self.set_default_size(860, 640)
        if transient_for is not None:
            self.set_transient_for(transient_for)
            self.set_modal(True)
        self.settings = settings
        self._parent = transient_for
        self.profile_store = profile_store
        self.media_roots = list(media_roots)
        self._plugins = list(plugins)
        self._rejected = list(rejected_plugins)
        self._upcoming = list(upcoming)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        outer.set_margin_top(12)
        outer.set_margin_bottom(12)
        outer.set_margin_start(12)
        outer.set_margin_end(12)

        self._status = Gtk.Label(label="", xalign=0)
        self._status.set_line_wrap(True)
        self._status.set_max_width_chars(60)
        self._status.get_style_context().add_class("dim-label")

        self._stack = Gtk.Stack()
        # Ten pages do not fit as tabs in a window this wide, so they go in a
        # sidebar on the left.
        sidebar = Gtk.StackSidebar()
        sidebar.set_stack(self._stack)
        sidebar.set_size_request(220, -1)
        scroller = Gtk.ScrolledWindow()
        scroller.set_hexpand(True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.add(self._stack)
        middle = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        middle.pack_start(sidebar, False, False, 0)
        middle.pack_start(scroller, True, True, 0)
        outer.pack_start(middle, True, True, 0)
        outer.pack_start(self._status, False, False, 0)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        buttons.set_halign(Gtk.Align.END)
        close = Gtk.Button(label="Close")
        close.connect("clicked", lambda *_: self.close())
        buttons.pack_start(close, False, False, 0)
        outer.pack_start(buttons, False, False, 0)

        self.add(outer)
        self.reload()
        outer.show_all()

    # -- rendering ------------------------------------------------------
    def reload(self) -> None:
        panel = tool_panels.build(
            self.settings,
            plugins=self._plugins,
            rejected_plugins=self._rejected,
            upcoming=self._upcoming,
            media_roots=self.media_roots,
            profile_store=self.profile_store,
            backup_dir=platform_support.data_dir(),
            diagnostics_path=platform_support.data_dir() / "support-report.txt",
        )
        for child in self._stack.get_children():
            self._stack.remove(child)
        for section in panel.sections:
            page = self._page(section)
            page.show_all()
            self._stack.add_titled(page, section.key, section.title)

    def _page(self, section: tool_panels.Section) -> Gtk.Widget:
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        body.set_margin_top(12)
        body.set_margin_bottom(12)
        body.set_margin_start(14)
        body.set_margin_end(14)
        if section.description:
            note = Gtk.Label(label=section.description, xalign=0)
            note.set_line_wrap(True)
            note.set_max_width_chars(60)
            note.get_style_context().add_class("dim-label")
            body.pack_start(note, False, False, 0)
        for row in section.rows:
            body.pack_start(self._row(row), False, False, 0)
        return body

    def _row(self, row: tool_panels.Row) -> Gtk.Widget:
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text.set_hexpand(True)
        label = Gtk.Label(label=row.label, xalign=0)
        label.set_line_wrap(True)
        label.set_max_width_chars(60)
        text.pack_start(label, False, False, 0)
        description = row.description or (row.value if row.kind == tool_panels.INFO else "")
        if description:
            note = Gtk.Label(label=description, xalign=0)
            note.set_line_wrap(True)
            note.set_max_width_chars(60)
            note.get_style_context().add_class("dim-label")
            text.pack_start(note, False, False, 0)
        line.pack_start(text, True, True, 0)

        if row.kind == tool_panels.TOGGLE and row.action:
            switch = Gtk.Switch()
            switch.set_active(bool(row.state))
            switch.set_valign(Gtk.Align.CENTER)
            switch.connect("state-set", self._on_toggle, row.action)
            line.pack_end(switch, False, False, 0)
        elif row.kind == tool_panels.CHOICE and row.action:
            labels = [text for _value, text in row.options]
            values = [value for value, _text in row.options]
            dropdown = Gtk.ComboBoxText()
            for text_label in labels:
                dropdown.append_text(text_label)
            if row.value in values:
                dropdown.set_active(values.index(row.value))
            dropdown.connect("changed", self._on_choice, row.action, values)
            line.pack_end(dropdown, False, False, 0)
        elif row.kind == tool_panels.BUTTON and row.action:
            button = Gtk.Button(label=row.value or row.label)
            button.connect("clicked", self._on_button, row.action)
            line.pack_end(button, False, False, 0)
        elif row.value:
            value = Gtk.Label(label=row.value, xalign=1)
            value.get_style_context().add_class("dim-label")
            line.pack_end(value, False, False, 0)
        return line

    # -- actions --------------------------------------------------------
    def _on_toggle(self, _switch, state, action):
        self._dispatch(action, bool(state))
        return False

    def _on_choice(self, dropdown, action, values):
        index = dropdown.get_active()
        if 0 <= index < len(values):
            self._dispatch(action, values[index])

    def _on_button(self, _button, action):
        self._dispatch(action, True)

    def _say(self, message: str) -> None:
        self._status.set_text(str(message))

    def _dispatch(self, action: str, value: Any) -> None:
        if action == "updates.check":
            self._say("Checking the releases page…")
            thread = threading.Thread(target=self._check_updates, daemon=True)
            thread.start()
            return
        if action == "backup.create":
            self._run_in_thread(self._backup, lambda report: self._say(f"Backup written to {report.path}."))
            return
        if action == "diagnostics.create":
            self._run_in_thread(self._report, lambda path: self._say(f"Support report saved to {path}."))
            return
        if action == "home.shelves":
            from .gtk_shelves import ShelvesDialog

            dialog = ShelvesDialog(self.settings, self)
            dialog.run()
            dialog.destroy()
            self._say("Shelves saved.")
            self.reload()
            refresh = getattr(self._parent, "apply_interface_prefs", None)
            if callable(refresh):
                refresh()
            return
        message = tool_panels.apply(
            action,
            self.settings,
            value,
            profile_store=self.profile_store,
            media_roots=self.media_roots,
        )
        self._say(message)
        self.reload()
        # Interface rows take effect immediately in the window behind this one.
        refresh = getattr(self._parent, "apply_interface_prefs", None)
        if callable(refresh):
            refresh()

    def _run_in_thread(self, work, done) -> None:
        def runner() -> None:
            try:
                result = work()
            except Exception as exc:  # noqa: BLE001
                GLib.idle_add(self._say, f"That did not work: {exc}")
                return
            GLib.idle_add(done, result)

        threading.Thread(target=runner, daemon=True).start()

    def _check_updates(self) -> None:
        try:
            status = updates.check_for_update(__version__)
        except Exception as exc:  # noqa: BLE001 - offline is normal
            GLib.idle_add(self._say, f"Could not check for updates: {exc}")
            return
        GLib.idle_add(self._say, status.message)

    def _backup(self) -> Any:
        destination = platform_support.data_dir() / "lumora-backup.zip"
        return backup.create_backup(
            destination,
            library=_read_json(platform_support.data_dir() / "library.json"),
            collections=_read_json(platform_support.data_dir() / "collections.json"),
            settings=_snapshot(self.settings),
            app_version=__version__,
        )

    def _report(self) -> Path:
        return diagnostics.write_report(
            platform_support.data_dir() / "support-report.txt",
            app_version=__version__,
            settings=self.settings,
            extra={"plugins": len(self._plugins)},
        )


def show_tools(parent: Any, settings: Any, **kwargs: Any) -> ToolsWindow:
    window = ToolsWindow(settings, transient_for=parent if isinstance(parent, Gtk.Window) else None, **kwargs)
    window.show_all()
    window.present()
    return window


__all__ = ["ToolsWindow", "show_tools"]
