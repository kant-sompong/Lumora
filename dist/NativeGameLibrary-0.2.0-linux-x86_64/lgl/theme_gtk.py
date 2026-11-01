"""GTK side of the theme system: token providers, mode resolution, live updates.

Two providers sit above the system theme at ``APPLICATION`` priority:

* the **token provider**, generated from :mod:`lgl.theme` and replaced every
  time the mode changes (no restart, no stale colours);
* the **structural provider**, the shared ``style.css``, which only ever names
  tokens such as ``@bg``/``@fg`` and therefore needs no reloading.

The provider is installed on the *screen*, so every window — main window,
Settings, Kryoto, sources, lightbox, launch confirmation, new dialogs created
later — is painted by the same tokens.  That is the fix for dialogs that used
to stay light while the main window went dark.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import gi

#: Pin every namespace: without the Gdk line, GI hands us Gdk 4.0 on a system
#: that has both, and the app then fails to import its own toolkit.
gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from . import theme as tokens  # noqa: E402

#: Same priority the previous stylesheet used, now shared by both providers.
PRIORITY = Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION

_STYLESHEET = Path(__file__).with_name("style.css")

#: Sentinel: "look the real object up" as opposed to an explicit ``None``
#: (which means "there is none", used by the headless tests).
_AUTO = object()


def _log(message: str) -> None:
    print(f"Native Game Library: {message}", file=sys.stderr)


def _to_hex(colour: Any) -> str:
    """``#rrggbb`` for a ``Gdk.RGBA``, ignoring alpha."""
    try:
        red = int(round(float(colour.red) * 255))
        green = int(round(float(colour.green) * 255))
        blue = int(round(float(colour.blue) * 255))
    except (AttributeError, TypeError, ValueError):
        return ""
    return f"#{red:02x}{green:02x}{blue:02x}"


def audit_toplevels(*, log: bool = True) -> list[str]:
    """Visible windows whose background is not painted from the palette.

    The theme reaches every window through the screen-wide provider, so a
    window that resolves to a *system* colour means a rule is missing from
    ``style.css`` - not that somebody forgot a class.  That is the check worth
    running after opening a dialog in a new colour mode, and it is cheap enough
    to call from a debug timer.
    """
    if _MANAGER is None:
        return []
    palette = tokens.palette(_MANAGER.resolved)
    wanted = {palette[key].lower() for key in ("bg", "surface", "raised")}
    problems: list[str] = []
    for window in Gtk.Window.list_toplevels():
        try:
            if not window.get_visible():
                continue
            background = window.get_style_context().get_background_color(
                Gtk.StateFlags.NORMAL
            )
        except (TypeError, AttributeError, RuntimeError):
            continue
        actual = _to_hex(background)
        if not actual or actual in wanted:
            continue
        name = str(window.get_title() or type(window).__name__)
        problems.append(name)
        if log:
            _log(
                f"theme audit: {name} paints {actual}, not a {_MANAGER.resolved} "
                "palette colour"
            )
    return problems


def start_audit(interval_ms: int = 1500) -> None:
    """Log any window the palette failed to reach, when ``LGL_DEBUG_THEME`` is set.

    One timer covers every dialog this app will ever open, including ones added
    later, which a per-dialog call could not.
    """
    if not os.environ.get("LGL_DEBUG_THEME"):
        return
    reported: set[str] = set()

    def tick() -> bool:
        for name in audit_toplevels(log=False):
            if name not in reported:
                reported.add(name)
                _log(f"theme audit: {name} is not painted from the palette")
        return True

    GLib.timeout_add(int(interval_ms), tick)


class ThemeManager:
    """Owns the token provider and keeps it in step with the desktop."""

    def __init__(
        self,
        *,
        screen: Any = _AUTO,
        settings: Any = _AUTO,
        portal: Any = _AUTO,
    ) -> None:
        self._screen = Gdk.Screen.get_default() if screen is _AUTO else screen
        self._settings = Gtk.Settings.get_default() if settings is _AUTO else settings
        self._portal = _appearance_portal() if portal is _AUTO else portal
        self._token_provider: Any = None
        self._structural_provider: Any = None
        self._handlers: list[tuple[Any, int]] = []
        self._mode = tokens.DEFAULT_MODE
        self._resolved = "light"
        self._applying = False
        #: The desktop's own prefer-dark request, captured before we ever write
        #: to that property.  Reading it live would latch: after asking for dark
        #: once, our own value would keep "Follow system" dark forever.
        self._desktop_prefer_dark = False
        if self._settings is not None:
            try:
                self._desktop_prefer_dark = bool(
                    self._settings.get_property("gtk-application-prefer-dark-theme")
                )
            except (TypeError, AttributeError):
                self._desktop_prefer_dark = False

    # -- state --------------------------------------------------------------

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def resolved(self) -> str:
        """The palette in use right now: ``"dark"`` or ``"light"``."""
        return self._resolved

    @property
    def is_dark(self) -> bool:
        return self._resolved == "dark"

    # -- lifecycle ----------------------------------------------------------

    def install(self) -> None:
        """Load the structural sheet, watch the environment, then apply."""
        self._load_structural()
        self._connect()
        self.apply(self._mode)
        start_audit()

    def shutdown(self) -> None:
        for source, handler in self._handlers:
            try:
                source.disconnect(handler)
            except (TypeError, RuntimeError):
                pass
        self._handlers.clear()

    def apply(self, mode: str) -> None:
        """Apply ``dark``, ``light`` or ``system`` immediately."""
        mode = tokens.normalize_mode(mode)
        self._mode = mode
        resolved = tokens.resolve(mode, system_dark=self.system_is_dark())
        self._resolved = resolved
        self._swap_token_provider(resolved)
        self._reset_widget_styles()
        self._sync_prefer_dark(resolved)

    # -- environment --------------------------------------------------------

    def system_is_dark(self) -> bool:
        """Is the desktop dark?  Portal first, then the theme name, then the flag.

        ``gtk-application-prefer-dark-theme`` alone was the old bug: it is an
        application request and stays ``False`` on a dark desktop.
        """
        if self._portal is not None:
            try:
                scheme = int(self._portal.get_uint("color-scheme"))
            except (AttributeError, GLib.Error, TypeError, ValueError):
                scheme = 0
            if scheme == 1:
                return True
            if scheme == 2:
                return False
        name = ""
        if self._settings is not None:
            try:
                name = str(self._settings.get_property("gtk-theme-name") or "")
            except (TypeError, AttributeError):
                name = ""
        verdict = tokens.is_dark_theme_name(name)
        if verdict is not None:
            return verdict
        return self._desktop_prefer_dark

    def _connect(self) -> None:
        if self._settings is not None:
            for signal in (
                "notify::gtk-theme-name",
                "notify::gtk-application-prefer-dark-theme",
            ):
                try:
                    self._handlers.append(
                        (self._settings, self._settings.connect(signal, self._on_environment))
                    )
                except (TypeError, AttributeError):
                    continue
        if self._portal is not None:
            try:
                self._handlers.append(
                    (
                        self._portal,
                        self._portal.connect("changed::color-scheme", self._on_environment),
                    )
                )
            except (TypeError, AttributeError):
                pass

    def _on_environment(self, *_args: Any) -> None:
        # Follow-system only.  An explicit Dark/Light choice must not be
        # overridden by the desktop switching at dusk.
        if self._applying or self._mode != "system":
            return
        resolved = tokens.resolve("system", system_dark=self.system_is_dark())
        if resolved == self._resolved:
            return
        self.apply("system")

    # -- providers ----------------------------------------------------------

    def _add(self, provider: Any) -> None:
        if self._screen is None:
            return
        Gtk.StyleContext.add_provider_for_screen(self._screen, provider, PRIORITY)

    def _remove(self, provider: Any) -> None:
        if self._screen is None or provider is None:
            return
        try:
            Gtk.StyleContext.remove_provider_for_screen(self._screen, provider)
        except (TypeError, RuntimeError):
            pass

    def _load_structural(self) -> None:
        if self._structural_provider is not None or self._screen is None:
            return
        if not _STYLESHEET.is_file():
            _log(f"no stylesheet at {_STYLESHEET}")
            return
        provider = Gtk.CssProvider()
        try:
            provider.load_from_path(str(_STYLESHEET))
        except GLib.Error as exc:
            _log(f"could not load {_STYLESHEET.name}: {exc}")
            return
        self._add(provider)
        self._structural_provider = provider

    def _swap_token_provider(self, resolved: str) -> None:
        if self._screen is None:
            return
        provider = Gtk.CssProvider()
        try:
            provider.load_from_data(tokens.css_variables(resolved).encode("utf-8"))
        except GLib.Error as exc:
            _log(f"could not build the {resolved} palette: {exc}")
            return
        previous = self._token_provider
        self._remove(previous)
        self._add(provider)
        self._token_provider = provider

    def _reset_widget_styles(self) -> None:
        """Force every widget to re-resolve its style against the new palette.

        Swapping the provider is not enough on its own: GTK3 caches the resolved
        background and foreground per style context, so a widget that has not
        been redrawn keeps the old colour (observed as a light window whose
        buttons were still dark).  ``reset_widgets`` is the documented way to
        drop those caches for the whole screen.
        """
        if self._screen is None:
            return
        reset = getattr(Gtk.StyleContext, "reset_widgets", None)
        if reset is not None:
            try:
                reset(self._screen)
                return
            except (TypeError, RuntimeError, GLib.Error):
                pass
        for window in Gtk.Window.list_toplevels():
            _invalidate_tree(window)

    def _sync_prefer_dark(self, resolved: str) -> None:
        """Ask GTK for the matching base variant so native widgets agree."""
        if self._settings is None:
            return
        want = resolved == "dark"
        try:
            current = bool(
                self._settings.get_property("gtk-application-prefer-dark-theme")
            )
        except (TypeError, AttributeError):
            return
        if current == want:
            return
        self._applying = True
        try:
            self._settings.set_property("gtk-application-prefer-dark-theme", want)
        except (TypeError, AttributeError):
            pass
        finally:
            self._applying = False


def _invalidate_tree(widget: Any) -> None:
    """Fallback for :meth:`ThemeManager._reset_widget_styles`."""
    context = getattr(widget, "get_style_context", None)
    if context is None:
        return
    try:
        context().invalidate()
    except (TypeError, RuntimeError):
        return
    try:
        widget.queue_draw()
    except (TypeError, RuntimeError):
        pass
    if isinstance(widget, Gtk.Container):
        try:
            widget.forall(lambda child: _invalidate_tree(child))
        except (TypeError, RuntimeError):
            pass


def _appearance_portal() -> Any:
    """The freedesktop appearance setting, when the desktop exports it."""
    try:
        source = Gio.SettingsSchemaSource.get_default()
        schema = source.lookup("org.freedesktop.appearance", True) if source else None
        if schema is None:
            return None
        return Gio.Settings.new_full(schema, None, None)
    except (GLib.Error, AttributeError, TypeError):
        return None


_MANAGER: ThemeManager | None = None


def manager() -> ThemeManager:
    """The process-wide manager (created on first use)."""
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = ThemeManager()
    return _MANAGER


def install() -> None:
    manager().install()


def apply(mode: str) -> None:
    manager().apply(mode)


def resolved() -> str:
    return manager().resolved


def is_dark() -> bool:
    return manager().is_dark


def shutdown() -> None:
    global _MANAGER
    if _MANAGER is not None:
        _MANAGER.shutdown()
        _MANAGER = None


__all__ = [
    "PRIORITY",
    "ThemeManager",
    "apply",
    "audit_toplevels",
    "start_audit",
    "install",
    "is_dark",
    "manager",
    "resolved",
    "shutdown",
]
