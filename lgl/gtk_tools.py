"""The GTK tools window: the same sections the Qt window shows.

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

gi.require_version("Gtk", "4.0")
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
        self._status.set_wrap(True)
        self._status.add_css_class("dim-label")

        self._stack = Gtk.Stack()
        switcher = Gtk.StackSwitcher(stack=self._stack)
        outer.append(switcher)
        outer.append(Gtk.Separator())
        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        scroller.set_child(self._stack)
        outer.append(scroller)
        outer.append(self._status)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        buttons.set_halign(Gtk.Align.END)
        close = Gtk.Button(label="Close")
        close.connect("clicked", lambda *_: self.close())
        buttons.append(close)
        outer.append(buttons)

        self.set_child(outer)
        self.reload()

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
        child = self._stack.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self._stack.remove(child)
            child = following
        for section in panel.sections:
            self._stack.add_titled(self._page(section), section.key, section.title)

    def _page(self, section: tool_panels.Section) -> Gtk.Widget:
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        body.set_margin_top(12)
        body.set_margin_bottom(12)
        body.set_margin_start(14)
        body.set_margin_end(14)
        if section.description:
            note = Gtk.Label(label=section.description, xalign=0)
            note.set_wrap(True)
            note.add_css_class("dim-label")
            body.append(note)
        for row in section.rows:
            body.append(self._row(row))
        return body

    def _row(self, row: tool_panels.Row) -> Gtk.Widget:
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text.set_hexpand(True)
        label = Gtk.Label(label=row.label, xalign=0)
        label.set_wrap(True)
        text.append(label)
        description = row.description or (row.value if row.kind == tool_panels.INFO else "")
        if description:
            note = Gtk.Label(label=description, xalign=0)
            note.set_wrap(True)
            note.add_css_class("dim-label")
            text.append(note)
        line.append(text)

        if row.kind == tool_panels.TOGGLE and row.action:
            switch = Gtk.Switch()
            switch.set_active(bool(row.state))
            switch.set_valign(Gtk.Align.CENTER)
            switch.connect("state-set", self._on_toggle, row.action)
            line.append(switch)
        elif row.kind == tool_panels.CHOICE and row.action:
            labels = [text for _value, text in row.options]
            values = [value for value, _text in row.options]
            dropdown = Gtk.DropDown.new_from_strings(labels)
            if row.value in values:
                dropdown.set_selected(values.index(row.value))
            dropdown.connect("notify::selected", self._on_choice, row.action, values)
            line.append(dropdown)
        elif row.kind == tool_panels.BUTTON and row.action:
            button = Gtk.Button(label=row.value or row.label)
            button.connect("clicked", self._on_button, row.action)
            line.append(button)
        elif row.value:
            value = Gtk.Label(label=row.value, xalign=1)
            value.add_css_class("dim-label")
            line.append(value)
        return line

    # -- actions --------------------------------------------------------
    def _on_toggle(self, _switch, state, action):
        self._dispatch(action, bool(state))
        return False

    def _on_choice(self, dropdown, _param, action, values):
        index = dropdown.get_selected()
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
        message = tool_panels.apply(
            action,
            self.settings,
            value,
            profile_store=self.profile_store,
            media_roots=self.media_roots,
        )
        self._say(message)
        self.reload()

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
    window.present()
    return window


__all__ = ["ToolsWindow", "show_tools"]
