"""Qt stylesheet generated from the shared design tokens in ``theme.py``.

The GTK frontend turns the palette into ``@define-color`` lines and keeps all
literal colours out of ``style.css``.  This module does the same job for Qt:
it renders one QSS string from the same dictionaries, so the Windows frontend
cannot drift away from the Linux one in colour, spacing or radii.
"""

from __future__ import annotations

from . import theme
from .theme import RADIUS, SPACE, TYPE


def alpha(color: str, opacity: float) -> str:
    """``#rrggbb`` + opacity -> ``rgba(r, g, b, a)`` (QSS has no alpha(@x, n))."""
    text = color.strip()
    if text.startswith("rgba") or text.startswith("rgb"):
        return text
    body = text.lstrip("#")
    if len(body) == 3:
        body = "".join(ch * 2 for ch in body)
    red, green, blue = (int(body[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgba({red}, {green}, {blue}, {max(0.0, min(1.0, opacity)):.3f})"


def font_family() -> str:
    """Segoe UI Variable on Windows 11, then the usual fallbacks."""
    return '"Segoe UI Variable Text", "Segoe UI", "Cantarell", "Noto Sans", sans-serif'


def build_stylesheet(resolved: str) -> str:
    """Return the application QSS for ``"dark"`` or ``"light"``."""
    c = dict(theme.palette(resolved))
    fg = c["text"]
    sm, md, lg = RADIUS["sm"], RADIUS["md"], RADIUS["lg"]
    body, small, title, page = TYPE["body"], TYPE["small"], TYPE["title"], TYPE["page"]

    return f"""
* {{
    font-family: {font_family()};
    font-size: {body}px;
    color: {fg};
}}
QMainWindow, QDialog, QWidget#root {{ background: {c['bg']}; }}
QWidget {{ background: transparent; }}
QToolTip {{
    background: {c['raised']}; color: {fg};
    border: 1px solid {c['divider']}; border-radius: {sm}px; padding: 4px 8px;
}}

/* ---- header bar (window chrome row) ---------------------------------- */
QFrame#headerbar {{
    background: {c['surface']};
    border-bottom: 1px solid {alpha(fg, 0.08)};
}}
QPushButton#viewtab {{
    background: transparent; border: none; border-radius: {md}px;
    padding: 6px 22px; color: {c['muted']}; font-weight: 500;
}}
QPushButton#viewtab:hover {{ background: {alpha(fg, 0.05)}; color: {fg}; }}
QPushButton#viewtab:checked {{ background: {alpha(fg, 0.10)}; color: {fg}; }}

/* ---- buttons / inputs -------------------------------------------------- */
QPushButton, QToolButton {{
    background: {c['raised']}; border: 1px solid {alpha(fg, 0.12)};
    border-radius: {md}px; padding: 6px 14px; color: {fg};
}}
QPushButton:hover, QToolButton:hover {{ background: {c['hover']}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {alpha(fg, 0.14)}; }}
QPushButton:checked {{ background: {alpha(c['accent'], 0.18)}; border-color: {alpha(c['accent'], 0.5)}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {c['faint']}; background: {alpha(fg, 0.03)}; }}
QPushButton#flat, QToolButton#flat {{ background: transparent; border: none; }}
QPushButton#flat:hover, QToolButton#flat:hover {{ background: {alpha(fg, 0.07)}; }}
QPushButton#suggested {{
    background: {c['accent_fill']}; color: {c['on_accent']}; border: none; font-weight: 600;
}}
QPushButton#suggested:hover {{ background: {c['accent_fill_hover']}; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}

QLineEdit, QComboBox {{
    background: {c['surface']}; border: 1px solid {alpha(fg, 0.14)};
    border-radius: {md}px; padding: 7px 10px; selection-background-color: {alpha(c['accent'], 0.45)};
}}
QLineEdit:focus, QComboBox:focus {{ border: 1px solid {c['accent']}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox QAbstractItemView {{
    background: {c['raised']}; border: 1px solid {c['divider']};
    selection-background-color: {alpha(c['accent'], 0.25)}; outline: none; padding: 4px;
}}

QMenu {{
    background: {c['raised']}; border: 1px solid {c['divider']};
    border-radius: {md}px; padding: 6px;
}}
QMenu::item {{ padding: 7px 22px 7px 14px; border-radius: {sm}px; }}
QMenu::item:selected {{ background: {alpha(fg, 0.08)}; }}
QMenu::separator {{ height: 1px; background: {c['divider']}; margin: 5px 8px; }}

/* ---- scroll bars: thin overlay style ---------------------------------- */
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {alpha(fg, 0.22)}; border-radius: 3px; min-height: 36px; }}
QScrollBar::handle:vertical:hover {{ background: {alpha(fg, 0.38)}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {alpha(fg, 0.22)}; border-radius: 3px; min-width: 36px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---- library / archive toolbar ---------------------------------------- */
QFrame#toolbar {{ background: transparent; }}
QLabel#usage {{ color: {alpha(fg, 0.46)}; font-size: {small}px; }}
QFrame#metabar {{ border-bottom: 1px solid {alpha(fg, 0.05)}; }}

/* ---- tiles ----------------------------------------------------------------- */
QFrame#tile {{ background: transparent; border-radius: {md}px; }}
QFrame#tile[hover="true"] {{ background: {alpha(fg, 0.035)}; }}
QFrame#tile[selected="true"] {{ background: {alpha(c['accent'], 0.10)}; }}
QLabel#tile-title {{ font-weight: 600; font-size: {body - 1}px; color: {alpha(fg, 0.92)}; }}
QLabel#tile-meta {{ font-size: {small}px; color: {alpha(fg, 0.48)}; }}
QLabel#tile-compat {{ font-size: {small}px; color: {alpha(fg, 0.38)}; }}
QToolButton#favorite-star {{
    background: {alpha(c['bg'], 0.55)}; border: none; border-radius: 11px;
    padding: 0; color: {alpha(fg, 0.38)};
}}
QToolButton#favorite-star:hover {{ background: {alpha(c['bg'], 0.90)}; }}
QToolButton#favorite-star:checked {{ color: {c['favorite']}; }}

/* ---- archive filter column ------------------------------------------------ */
QFrame#filters {{ background: {c['surface']}; border-right: 1px solid {alpha(fg, 0.08)}; }}
QLabel#filter-head {{
    color: {c['faint']}; font-size: {small - 1}px; font-weight: 700; letter-spacing: 0.6px;
}}
QPushButton#chip {{
    background: transparent; border: none; border-radius: {sm}px;
    padding: 6px 10px; text-align: left; color: {c['muted']};
}}
QPushButton#chip:hover {{ background: {alpha(fg, 0.06)}; color: {fg}; }}
QPushButton#chip:checked {{ background: {alpha(c['accent'], 0.16)}; color: {c['accent_text']}; font-weight: 600; }}

/* ---- detail panel ---------------------------------------------------------- */
QFrame#detail {{ background: {c['surface']}; border-left: 1px solid {alpha(fg, 0.08)}; }}
QLabel#detail-eyebrow {{
    color: {c['faint']}; font-size: {small - 1}px; font-weight: 700; letter-spacing: 0.6px;
}}
QLabel#detail-title {{ font-size: {title + 2}px; font-weight: 700; }}
QLabel#detail-sub {{ color: {c['muted']}; }}
QLabel#kv-key {{ color: {c['faint']}; font-size: {small - 1}px; font-weight: 700; }}
QLabel#kv-val {{ color: {fg}; }}
QLabel#kv-warn {{ color: {c['warning']}; }}
QLabel#kv-ok {{ color: {c['success']}; }}
QLabel#detail-note {{ color: {c['muted']}; font-size: {small}px; }}

/* ---- toast + empty state --------------------------------------------------- */
QFrame#toast {{
    background: {c['raised']}; border: 1px solid {c['divider']}; border-radius: {lg}px;
}}
QLabel#empty-title {{ font-size: {title}px; font-weight: 600; }}
QLabel#empty-sub {{ color: {c['muted']}; }}
QLabel#page-title {{ font-size: {page}px; font-weight: 700; }}
"""


__all__ = ["alpha", "build_stylesheet", "font_family", "SPACE"]
