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

# Light theme: a neutral warm-grey canvas, white surfaces, one deep-green accent.
LIGHT = {
    "bg": "#f1f2f4",
    "surface": "#ffffff",
    "surface_alt": "#f7f8f9",
    "border": "#d8dde2",
    "ink": "#1a1e22",
    "muted": "#5c6773",
    "faint": "#8a949e",
    "accent": "#146b5b",
    "accent_hover": "#0f584a",
    "accent_soft": "#e4f1ed",
    "danger": "#a03a31",
    "warning_bg": "#fbf4e5",
    "warning_border": "#e7d5ab",
    "warning_ink": "#6a4a12",
    "selection": "#d9ece6",
    "stripe": "#fafbfc",
    "trough": "#e7eaed",
    "button_pressed": "#e6e9ec",
    "button_active": "#eef1f3",
    "border_active": "#c4cbd2",
    "heading_active": "#eef0f2",
    "scrollbar": "#c7cdd3",
    "scrollbar_active": "#aeb6be",
    "progress_trough": "#e5e9ec",
    "accent_active": "#10604f",
    "accent_disabled": "#b9cdc8",
    "accent_disabled_fg": "#f2f5f4",
    "tab_active": "#e9ebee",
    "quiet_pressed": "#eceff1",
}

# Dark theme: every surface is genuinely dark, including tables, fields, menus,
# scrollbars, and the status bar.  A brighter green is used so the accent keeps
# its contrast against the dark canvas.
DARK = {
    "bg": "#15181b",
    "surface": "#1e2226",
    "surface_alt": "#191c1f",
    "border": "#333a41",
    "ink": "#e6eaee",
    "muted": "#a4aeb8",
    "faint": "#7b8791",
    "accent": "#4fb79f",
    "accent_hover": "#3fa48e",
    "accent_soft": "#1e3b35",
    "danger": "#e17b72",
    "warning_bg": "#3a3222",
    "warning_border": "#5a4b2c",
    "warning_ink": "#ecdcad",
    "selection": "#28473f",
    "stripe": "#22272b",
    "trough": "#2a3036",
    "button_pressed": "#2b3238",
    "button_active": "#262d33",
    "border_active": "#465059",
    "heading_active": "#2a3137",
    "scrollbar": "#4a545d",
    "scrollbar_active": "#5d6872",
    "progress_trough": "#2a3036",
    "accent_active": "#3f9c86",
    "accent_disabled": "#2f4a44",
    "accent_disabled_fg": "#9fb6b0",
    "tab_active": "#252b30",
    "quiet_pressed": "#2b3238",
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
            "NativeGameLibrary"
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
