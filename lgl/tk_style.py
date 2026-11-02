"""Visual language for the Tkinter frontend.

A small, self-contained theme: a neutral warm-grey canvas, white surfaces, a
single deep-green accent, and a compact type scale.  Everything is plain
``ttk`` styling so it behaves the same on Windows 10/11 and on the Linux
machine used for development.

No external fonts are required; the first family that exists on the machine
is used.  Sizes are in points so Tk's DPI scaling takes care of high-DPI
displays.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from dataclasses import dataclass
from tkinter import font as tkfont
from tkinter import ttk
from typing import Iterable

# --------------------------------------------------------------------------
# Palette
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Palette
# --------------------------------------------------------------------------

# Colors from lgl/theme.py — single source of truth for both frontends.
ACCENT = "#3b82f6"
ACCENT_FILL = "#2563eb"
ACCENT_FILL_HOVER = "#1d4ed8"
ACCENT_TEXT = "#7db2ff"  # dark theme
ACCENT_TEXT_LIGHT = "#1d4ed8"  # light theme
FAVORITE = "#b8863b"

# Light theme: matches lgl/theme.py LIGHT palette
LIGHT = {
    "bg": "#f5f6f8",
    "surface": "#ffffff",
    "surface_alt": "#f5f6f8",  # raised = surface in light
    "border": "#e5e7eb",
    "ink": "#111827",
    "muted": "#5f6673",
    "faint": "#9ca3af",
    "accent": ACCENT,
    "accent_hover": "#2563eb",
    "accent_soft": "#dbeafe",
    "danger": "#c02626",
    "warning_bg": "#fef3c7",
    "warning_border": "#fcd34d",
    "warning_ink": "#92400e",
    "selection": "#dbeafe",
    "stripe": "#f5f6f8",
    "trough": "#e5e7eb",
    "button_pressed": "#e5e7eb",
    "button_active": "#f5f6f8",
    "border_active": "#d1d5db",
    "heading_active": "#e5e7eb",
    "scrollbar": "#d1d5db",
    "scrollbar_active": "#9ca3af",
    "progress_trough": "#e5e7eb",
    "accent_active": "#1d4ed8",
    "accent_disabled": "#93c5fd",
    "accent_disabled_fg": "#1e3a8a",
    "tab_active": "#eff6ff",
    "quiet_pressed": "#e5e7eb",
}

# Dark theme: matches lgl/theme.py DARK palette
DARK = {
    "bg": "#101216",
    "surface": "#171a20",
    "surface_alt": "#1e222a",
    "border": "#262b34",
    "ink": "#e8eaed",
    "muted": "#9aa3af",
    "faint": "#6b7280",
    "accent": ACCENT,
    "accent_hover": "#5b9bff",
    "accent_soft": "#1e3a8a",
    "danger": "#ef4444",
    "warning_bg": "#37301a",
    "warning_border": "#92400e",
    "warning_ink": "#fde68a",
    "selection": "#1e3a8a",
    "stripe": "#101216",
    "trough": "#262b34",
    "button_pressed": "#1e222a",
    "button_active": "#101216",
    "border_active": "#374151",
    "heading_active": "#1e222a",
    "scrollbar": "#374151",
    "scrollbar_active": "#4b5563",
    "progress_trough": "#262b34",
    "accent_active": "#2563eb",
    "accent_disabled": "#1e3a8a",
    "accent_disabled_fg": "#93c5fd",
    "tab_active": "#1e222a",
    "quiet_pressed": "#1e222a",
}

# Module-level names kept for callers that read colours while building widgets.
# ``setup`` rewrites them for the active theme before any widget is made.
BG = LIGHT["bg"]
SURFACE = LIGHT["surface"]
SURFACE_ALT = LIGHT["surface_alt"]
BORDER = LIGHT["border"]
INK = LIGHT["ink"]
MUTED = LIGHT["muted"]
FAINT = LIGHT["faint"]
ACCENT = LIGHT["accent"]
ACCENT_HOVER = LIGHT["accent_hover"]
ACCENT_SOFT = LIGHT["accent_soft"]
DANGER = LIGHT["danger"]
WARNING_BG = LIGHT["warning_bg"]
WARNING_BORDER = LIGHT["warning_border"]
WARNING_INK = LIGHT["warning_ink"]
SELECTION = LIGHT["selection"]
STRIPE = LIGHT["stripe"]

PALETTE: dict[str, str] = dict(LIGHT)


def resolve_theme(theme: str) -> str:
    """Return ``light`` or ``dark`` for a stored theme choice."""
    if theme == "dark":
        return "dark"
    if theme == "light":
        return "light"
    return "dark" if _system_is_dark() else "light"


def _system_is_dark() -> bool:
    """Best-effort desktop preference lookup; never raises."""
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            ) as key:
                light, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return not bool(light)
        except Exception:
            return False

    env = os.environ.get("GTK_THEME", "").lower()
    if "dark" in env:
        return True
    for key, setting in (
        ("color-scheme", "org.gnome.desktop.interface color-scheme"),
        ("gtk-theme", "org.gnome.desktop.interface gtk-theme"),
    ):
        try:
            result = subprocess.run(
                ["gsettings", "get", *setting.split()],
                capture_output=True, text=True, timeout=2, check=False,
            )
        except Exception:
            continue
        value = (result.stdout or "").lower()
        if key == "color-scheme" and "prefer-dark" in value:
            return True
        if key == "gtk-theme" and "dark" in value:
            return True
    return False


@dataclass
class Theme:
    style: ttk.Style
    fonts: "Fonts"
    px: float


@dataclass
class Fonts:
    family: str
    base: tkfont.Font
    base_bold: tkfont.Font
    small: tkfont.Font
    small_bold: tkfont.Font
    caption: tkfont.Font
    title: tkfont.Font
    heading: tkfont.Font
    link: tkfont.Font
    mono: tkfont.Font


# --------------------------------------------------------------------------
# Fonts and DPI
# --------------------------------------------------------------------------

_BODY_CANDIDATES = (
    "Segoe UI Variable Text",
    "Segoe UI",
    "Cantarell",
    "Ubuntu",
    "Noto Sans",
    "DejaVu Sans",
    "Liberation Sans",
)
_MONO_CANDIDATES = (
    "Cascadia Mono",
    "Consolas",
    "Ubuntu Mono",
    "DejaVu Sans Mono",
    "Liberation Mono",
    "Courier New",
)


def _pick(families: Iterable[str], available: set[str], fallback: str) -> str:
    for name in families:
        if name.casefold() in available:
            return name
    return fallback


def _font(root: tk.Misc, family: str, size: int, weight: str = "normal",
          slant: str = "roman", underline: bool = False) -> tkfont.Font:
    return tkfont.Font(
        root=root,
        family=family,
        size=size,
        weight=weight,
        slant=slant,
        underline=underline,
    )


def apply_dpi(root: tk.Misc) -> float:
    """Make Tk fonts and spacing match the real display density.

    Returns the pixel scale relative to 96 DPI, used for a handful of
    pixel-valued options (row heights) that Tk does not scale on its own.
    """
    try:
        scaling = float(root.winfo_fpixels("1i")) / 72.0
        if scaling > 0:
            root.tk.call("tk", "scaling", scaling)
    except (tk.TclError, ValueError):
        pass
    try:
        return max(0.75, min(4.0, float(root.winfo_fpixels("1i")) / 96.0))
    except (tk.TclError, ValueError):
        return 1.0


def enable_native_dpi_awareness() -> None:
    """Ask Windows for per-monitor DPI handling before Tk starts."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "Lumora"
        )
    except Exception:
        pass


def _build_fonts(root: tk.Misc) -> Fonts:
    available = {name.casefold() for name in tkfont.families(root)}
    family = _pick(_BODY_CANDIDATES, available, "TkDefaultFont")
    mono_family = _pick(_MONO_CANDIDATES, available, "TkFixedFont")

    fonts = Fonts(
        family=family,
        base=_font(root, family, 10),
        base_bold=_font(root, family, 10, "bold"),
        small=_font(root, family, 9),
        small_bold=_font(root, family, 9, "bold"),
        caption=_font(root, family, 8, "bold"),
        title=_font(root, family, 17, "bold"),
        heading=_font(root, family, 12, "bold"),
        link=_font(root, family, 10, underline=True),
        mono=_font(root, mono_family, 9),
    )

    # Keep the classic named fonts in the same family so menus and dialogs
    # do not fall back to something different.
    for name, size, weight in (
        ("TkDefaultFont", 10, "normal"),
        ("TkTextFont", 10, "normal"),
        ("TkMenuFont", 10, "normal"),
        ("TkHeadingFont", 10, "bold"),
        ("TkCaptionFont", 9, "normal"),
        ("TkSmallCaptionFont", 8, "normal"),
        ("TkIconFont", 10, "normal"),
        ("TkTooltipFont", 9, "normal"),
    ):
        try:
            named = tkfont.nametofont(name, root=root)
            named.configure(family=family, size=size, weight=weight)
        except tk.TclError:
            continue
    return fonts


# --------------------------------------------------------------------------
# Style
# --------------------------------------------------------------------------


def setup(root: tk.Misc, theme: str = "system") -> Theme:
    """Build the palette and ttk styles for ``theme``.

    ``theme`` is ``system`` (follow the desktop), ``light``, or ``dark``.
    Tk bakes colours into widgets when they are created, so a theme change
    takes effect on the next start rather than live.
    """
    global PALETTE, BG, SURFACE, SURFACE_ALT, BORDER, INK, MUTED, FAINT
    global ACCENT, ACCENT_HOVER, ACCENT_SOFT, DANGER
    global WARNING_BG, WARNING_BORDER, WARNING_INK, SELECTION, STRIPE

    palette = DARK if resolve_theme(theme) == "dark" else LIGHT
    PALETTE = dict(palette)
    globals().update(palette)

    px = apply_dpi(root)
    fonts = _build_fonts(root)
    style = ttk.Style(root)

    # Native Tk widgets (menus, plain frames) are not covered by ttk styles.
    for pattern, value in (
        ("*Menu.background", SURFACE),
        ("*Menu.foreground", INK),
        ("*Menu.activeBackground", ACCENT),
        ("*Menu.activeForeground", "#ffffff"),
        ("*Menu.selectColor", INK),
        ("*Menu.disabledForeground", FAINT),
        ("*Toplevel.background", BG),
        ("*TCombobox*Listbox.background", SURFACE),
        ("*TCombobox*Listbox.foreground", INK),
        ("*TCombobox*Listbox.selectBackground", SELECTION),
        ("*TCombobox*Listbox.selectForeground", INK),
    ):
        try:
            root.option_add(pattern, value)
        except tk.TclError:
            pass

    # clam is used deliberately: it is available on every supported Tk build
    # and can be recoloured consistently, unlike the native Windows theme.
    if "clam" in style.theme_names():
        style.theme_use("clam")

    style.configure(
        ".",
        background=BG,
        foreground=INK,
        font=fonts.base,
        bordercolor=BORDER,
        focuscolor=ACCENT,
        troughcolor=PALETTE["trough"],
        selector=SELECTION,
    )

    # Frames ---------------------------------------------------------------
    style.configure("TFrame", background=BG)
    style.configure("App.TFrame", background=BG)
    style.configure("Surface.TFrame", background=SURFACE)
    style.configure("Toolbar.TFrame", background=SURFACE)
    style.configure("Banner.TFrame", background=WARNING_BG)

    # Labels ---------------------------------------------------------------
    style.configure("TLabel", background=BG, foreground=INK)
    style.configure("Surface.TLabel", background=SURFACE, foreground=INK)
    style.configure("Small.TLabel", background=SURFACE, foreground=MUTED,
                    font=fonts.small)
    style.configure("SmallBold.TLabel", background=SURFACE, foreground=INK,
                    font=fonts.small_bold)
    style.configure("FieldLabel.TLabel", background=SURFACE, foreground=MUTED,
                    font=fonts.small_bold)
    style.configure("Caption.TLabel", background=SURFACE, foreground=MUTED,
                    font=fonts.caption)
    style.configure("EmptyTitle.TLabel", background=SURFACE, foreground=INK,
                    font=fonts.heading)
    style.configure("EmptyText.TLabel", background=SURFACE, foreground=MUTED,
                    font=fonts.base)
    style.configure("Title.TLabel", background=SURFACE, foreground=INK,
                    font=fonts.title)
    style.configure("Heading.TLabel", background=SURFACE, foreground=INK,
                    font=fonts.heading)
    style.configure("Meta.TLabel", background=SURFACE, foreground=MUTED,
                    font=fonts.base)
    style.configure("Link.TLabel", background=SURFACE, foreground=ACCENT,
                    font=fonts.link)
    style.configure("Warning.TLabel", background=WARNING_BG,
                    foreground=WARNING_INK, font=fonts.base)
    style.configure("WarningBold.TLabel", background=WARNING_BG,
                    foreground=WARNING_INK, font=fonts.base_bold)
    style.configure("Error.TLabel", background=SURFACE, foreground=DANGER,
                    font=fonts.small)
    style.configure("Status.TLabel", background=SURFACE_ALT, foreground=MUTED,
                    font=fonts.small, padding=(12, 5))
    style.configure("StatusError.TLabel", background=SURFACE_ALT,
                    foreground=DANGER, font=fonts.small, padding=(12, 5))
    style.configure("Hint.TLabel", background=SURFACE, foreground=FAINT,
                    font=fonts.base)

    # Buttons --------------------------------------------------------------
    style.configure(
        "TButton",
        background=SURFACE,
        foreground=INK,
        bordercolor=BORDER,
        lightcolor=SURFACE,
        darkcolor=SURFACE,
        focusthickness=1,
        focuscolor=ACCENT,
        relief="flat",
        padding=(12, 6),
        font=fonts.base,
    )
    style.map(
        "TButton",
        background=[
            ("disabled", SURFACE_ALT),
            ("pressed", PALETTE["button_pressed"]),
            ("active", PALETTE["button_active"]),
        ],
        foreground=[("disabled", FAINT)],
        bordercolor=[("focus", ACCENT), ("active", PALETTE["border_active"])],
    )
    style.configure(
        "Accent.TButton",
        background=ACCENT,
        foreground="#ffffff",
        bordercolor=ACCENT,
        lightcolor=ACCENT,
        darkcolor=ACCENT,
        relief="flat",
        padding=(14, 7),
        font=fonts.base_bold,
    )
    style.map(
        "Accent.TButton",
        background=[
            ("disabled", PALETTE["accent_disabled"]),
            ("pressed", ACCENT_HOVER),
            ("active", PALETTE["accent_active"]),
        ],
        foreground=[("disabled", PALETTE["accent_disabled_fg"])],
        bordercolor=[("disabled", PALETTE["accent_disabled"]), ("focus", ACCENT)],
    )
    style.configure(
        "Quiet.TButton",
        background=SURFACE,
        foreground=MUTED,
        bordercolor=SURFACE,
        lightcolor=SURFACE,
        darkcolor=SURFACE,
        padding=(8, 4),
        font=fonts.small,
    )
    style.map(
        "Quiet.TButton",
        background=[("active", SURFACE_ALT), ("pressed", PALETTE["quiet_pressed"])],
        foreground=[("active", INK)],
    )
    style.configure("TSeparator", background=BORDER)

    # Inputs ---------------------------------------------------------------
    style.configure(
        "TEntry",
        fieldbackground=SURFACE,
        foreground=INK,
        insertcolor=INK,
        bordercolor=BORDER,
        lightcolor=SURFACE,
        darkcolor=SURFACE,
        padding=(8, 5),
    )
    style.map(
        "TEntry",
        bordercolor=[("focus", ACCENT)],
        lightcolor=[("focus", ACCENT)],
        darkcolor=[("focus", ACCENT)],
    )
    style.configure(
        "TCombobox",
        fieldbackground=SURFACE,
        background=SURFACE,
        foreground=INK,
        arrowcolor=MUTED,
        bordercolor=BORDER,
        lightcolor=SURFACE,
        darkcolor=SURFACE,
        padding=(8, 4),
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", SURFACE), ("disabled", SURFACE_ALT)],
        foreground=[("disabled", FAINT)],
        selectbackground=[("readonly", SURFACE)],
        selectforeground=[("readonly", INK)],
        bordercolor=[("focus", ACCENT), ("hover", PALETTE["border_active"])],
    )
    style.configure("TCheckbutton", background=SURFACE, foreground=INK,
                    focuscolor=ACCENT, padding=(0, 2))
    style.map(
        "TCheckbutton",
        background=[("active", SURFACE)],
        foreground=[("disabled", FAINT)],
        indicatorcolor=[("selected", ACCENT), ("!selected", SURFACE)],
    )
    style.configure("Surface.TCheckbutton", background=SURFACE, foreground=INK,
                    focuscolor=ACCENT)
    style.map("Surface.TCheckbutton", background=[("active", SURFACE)])

    # Tabs -----------------------------------------------------------------
    style.configure("TNotebook", background=BG, borderwidth=0,
                    tabmargins=(0, 2, 0, 0))
    style.configure(
        "TNotebook.Tab",
        background=BG,
        foreground=MUTED,
        bordercolor=BG,
        lightcolor=BG,
        darkcolor=BG,
        padding=(18, 8),
        font=fonts.base,
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", SURFACE), ("active", PALETTE["tab_active"])],
        foreground=[("selected", INK), ("active", INK)],
        bordercolor=[("selected", BORDER)],
        lightcolor=[("selected", SURFACE)],
    )

    # Data tables ----------------------------------------------------------
    rowheight = max(20, int(round(26 * px)))
    style.configure(
        "Treeview",
        background=SURFACE,
        fieldbackground=SURFACE,
        foreground=INK,
        borderwidth=0,
        relief="flat",
        rowheight=rowheight,
        font=fonts.base,
    )
    style.map(
        "Treeview",
        background=[("selected", SELECTION)],
        foreground=[("selected", INK)],
    )
    style.configure(
        "Treeview.Heading",
        background=SURFACE_ALT,
        foreground=MUTED,
        bordercolor=BORDER,
        lightcolor=SURFACE_ALT,
        darkcolor=SURFACE_ALT,
        relief="flat",
        borderwidth=0,
        padding=(8, 6),
        font=fonts.small_bold,
    )
    style.map(
        "Treeview.Heading",
        background=[("active", PALETTE["heading_active"])],
        foreground=[("active", INK)],
    )

    # Scrollbars -----------------------------------------------------------
    style.configure(
        "TScrollbar",
        background=PALETTE["scrollbar"],
        troughcolor=SURFACE_ALT,
        bordercolor=SURFACE_ALT,
        arrowcolor=MUTED,
        lightcolor=PALETTE["scrollbar"],
        darkcolor=PALETTE["scrollbar"],
        width=max(10, int(round(12 * px))),
    )
    style.map("TScrollbar", background=[("active", PALETTE["scrollbar_active"])])

    # Progress -------------------------------------------------------------
    style.configure(
        "TProgressbar",
        troughcolor=PALETTE["progress_trough"],
        background=ACCENT,
        bordercolor=PALETTE["progress_trough"],
        lightcolor=ACCENT,
        darkcolor=ACCENT,
        thickness=max(5, int(round(6 * px))),
    )
    style.configure(
        "Verify.TProgressbar",
        troughcolor=PALETTE["progress_trough"],
        background=ACCENT,
        bordercolor=PALETTE["progress_trough"],
        lightcolor=ACCENT,
        darkcolor=ACCENT,
        thickness=max(5, int(round(6 * px))),
    )

    # A custom style name needs its own layout; without this, ttk raises
    # "Layout Horizontal.Verify.TProgressbar not found" when the widget is
    # built. ttk cannot inherit a layout here (Style.layout("TProgressbar")
    # fails on some Tk builds), so the standard horizontal layout is declared.
    try:
        style.layout(
            "Verify.TProgressbar",
            [
                (
                    "Horizontal.Progressbar.trough",
                    {
                        "sticky": "nswe",
                        "children": [
                            (
                                "Horizontal.Progressbar.pbar",
                                {"side": "left", "sticky": "ns"},
                            )
                        ],
                    },
                )
            ],
        )
    except tk.TclError:
        pass

    style.configure("TPanedwindow", background=BG, sashwidth=max(4, int(round(6 * px))))
    return Theme(style=style, fonts=fonts, px=px)


# --------------------------------------------------------------------------
# Tooltip
# --------------------------------------------------------------------------


class Tooltip:
    """A single, lazy tooltip attached to any widget."""

    _DELAY = 550

    def __init__(self, widget: tk.Misc, text: str) -> None:
        self.widget = widget
        self.text = text
        self._after: str | None = None
        self._window: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event: tk.Event) -> None:
        self._cancel()
        try:
            self._after = self.widget.after(self._DELAY, self._show)
        except tk.TclError:
            self._after = None

    def _cancel(self) -> None:
        if self._after is not None:
            try:
                self.widget.after_cancel(self._after)
            except tk.TclError:
                pass
            self._after = None

    def _show(self) -> None:
        self._after = None
        if self._window is not None or not self.text:
            return
        try:
            x = self.widget.winfo_pointerx() + 14
            y = self.widget.winfo_pointery() + 20
        except tk.TclError:
            return
        window = tk.Toplevel(self.widget)
        window.wm_overrideredirect(True)
        try:
            window.attributes("-topmost", True)
        except tk.TclError:
            pass
        label = tk.Label(
            window,
            text=self.text,
            background="#2b3238",
            foreground="#f5f7f8",
            font=("TkDefaultFont", 9),
            justify="left",
            padx=9,
            pady=6,
            wraplength=340,
        )
        label.pack()
        window.wm_geometry(f"+{x}+{y}")
        self._window = window

    def _hide(self, _event: tk.Event | None = None) -> None:
        self._cancel()
        if self._window is not None:
            try:
                self._window.destroy()
            except tk.TclError:
                pass
            self._window = None


def tooltip(widget: tk.Misc, text: str) -> None:
    Tooltip(widget, text)
