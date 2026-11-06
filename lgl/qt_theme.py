"""Qt stylesheet generated from the shared design tokens in ``theme.py``.

The GTK frontend turns the palette into ``@define-color`` lines and keeps all
literal colours out of ``style.css``.  This module does the same job for Qt:
it renders one QSS string from the same dictionaries, so the Windows frontend
cannot drift away from the Linux one in colour, spacing or radii.

The class names below (``rail``, ``page-title``, ``filter-row``…) mirror the
CSS classes used by the GTK build, so a change to one is easy to mirror in the
other.
"""

from __future__ import annotations

from . import theme
from .theme import RADIUS, TYPE


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


def build_stylesheet(
    resolved: str, *, compact: bool = False, accent: str | None = None
) -> str:
    """Return the application QSS for ``"dark"`` or ``"light"``.

    ``accent`` retints the accent family through :func:`lgl.theme.with_accent`,
    and ``compact`` appends the denser padding block used by compact mode.  Both
    default to the shipped look, so existing callers are unaffected.
    """
    c = dict(theme.palette(resolved))
    if accent:
        c = theme.with_accent(c, accent)
    fg = c["text"]
    sm, md, lg = RADIUS["sm"], RADIUS["md"], RADIUS["lg"]
    body, small, title, page = TYPE["body"], TYPE["small"], TYPE["title"], TYPE["page"]

    qss = f"""
* {{
    font-family: {font_family()};
    font-size: {body}px;
    color: {fg};
}}
QMainWindow, QDialog, QWidget#appRoot, QWidget#pageBody {{ background: {c['bg']}; }}
QWidget {{ background: transparent; }}
QToolTip {{
    background: {c['raised']}; color: {fg};
    border: 1px solid {c['divider']}; border-radius: {sm}px; padding: 4px 8px;
}}

/* ---- window header -------------------------------------------------- */
QFrame#headerBar {{
    background: {c['bg']};
    border-bottom: 1px solid {alpha(fg, 0.05)};
}}
QFrame#searchPill {{
    background: {alpha(fg, 0.045)};
    border: 1px solid {alpha(fg, 0.07)};
    border-radius: 20px;
}}
QFrame#searchPill:focus-within {{
    border: 1px solid {alpha(c['accent'], 0.85)};
    background: {alpha(fg, 0.02)};
}}
QLineEdit#searchField {{
    background: transparent; border: none; padding: 0;
    color: {fg}; selection-background-color: {alpha(c['accent'], 0.45)};
}}
QLabel#keyHint {{ color: {c['faint']}; font-size: {small}px; }}

QAbstractButton#iconButton {{
    background: transparent; border: none; border-radius: {md}px;
}}
QAbstractButton#iconButton:hover {{ background: {alpha(fg, 0.07)}; }}
QAbstractButton#iconButton:pressed {{ background: {alpha(fg, 0.12)}; }}
QAbstractButton#iconButton:disabled {{ background: transparent; }}

/* ---- buttons ------------------------------------------------------- */
QPushButton, QToolButton {{
    background: {c['raised']}; border: 1px solid {alpha(fg, 0.12)};
    border-radius: {md}px; padding: 6px 14px; color: {fg};
}}
QPushButton:hover, QToolButton:hover {{ background: {c['hover']}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {alpha(fg, 0.14)}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {c['faint']}; background: {alpha(fg, 0.03)}; }}
QPushButton#flat, QToolButton#flat {{ background: transparent; border: none; }}
QPushButton#flat:hover, QToolButton#flat:hover {{ background: {alpha(fg, 0.07)}; }}
QPushButton#suggested {{
    background: {c['accent_fill']}; color: {c['on_accent']}; border: none; font-weight: 600;
}}
QPushButton#suggested:hover {{ background: {c['accent_fill_hover']}; }}
QPushButton#suggested:disabled {{ background: {alpha(fg, 0.10)}; color: {c['faint']}; }}
QPushButton#quiet {{
    background: transparent; border: 1px solid {alpha(fg, 0.14)}; color: {fg};
}}
QPushButton#quiet:hover {{ background: {alpha(fg, 0.06)}; }}
QPushButton#small {{ padding: 3px 10px; }}
QPushButton#pill {{
    background: transparent; border: 1px solid {alpha(fg, 0.10)};
    border-radius: 14px; padding: 5px 14px; color: {c['muted']};
}}
QPushButton#pill:hover {{ background: {alpha(fg, 0.06)}; color: {fg}; }}
QPushButton#pill:checked {{
    background: {alpha(fg, 0.10)}; border-color: transparent; color: {fg};
}}
QToolButton::menu-indicator {{ image: none; width: 0; }}

/* ---- inputs -------------------------------------------------------- */
QLineEdit, QComboBox, QSpinBox {{
    background: {c['surface']}; border: 1px solid {alpha(fg, 0.14)};
    border-radius: {md}px; padding: 7px 10px;
    selection-background-color: {alpha(c['accent'], 0.45)};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border: 1px solid {c['accent']}; }}
QLineEdit#librarySearch {{
    background: {alpha(fg, 0.05)}; border: 1px solid {alpha(fg, 0.08)};
    border-radius: 20px; padding: 9px 16px 9px 38px;
}}
QLineEdit#librarySearch:focus {{ border: 1px solid {c['accent']}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
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
QMenu::item:disabled {{ color: {c['faint']}; }}
QMenu::separator {{ height: 1px; background: {c['divider']}; margin: 5px 8px; }}
QMenu::indicator {{ width: 14px; height: 14px; }}

QCheckBox, QRadioButton {{ color: {fg}; spacing: 8px; }}
QCheckBox::indicator {{
    width: 15px; height: 15px; border-radius: 4px;
    border: 1px solid {alpha(fg, 0.28)}; background: transparent;
}}
QCheckBox::indicator:checked {{
    background: {c['accent_fill']}; border-color: {c['accent_fill']};
}}
QRadioButton::indicator {{
    width: 15px; height: 15px; border-radius: 8px;
    border: 1px solid {alpha(fg, 0.28)}; background: transparent;
}}
QRadioButton::indicator:checked {{
    border: 4px solid {c['accent_fill']}; background: {c['bg']};
}}

/* ---- scroll bars: thin overlay style -------------------------------- */
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {alpha(fg, 0.22)}; border-radius: 3px; min-height: 36px; }}
QScrollBar::handle:vertical:hover {{ background: {alpha(fg, 0.38)}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {alpha(fg, 0.22)}; border-radius: 3px; min-width: 36px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---- page furniture ------------------------------------------------- */
QLabel#pageTitle {{ font-size: {page}px; font-weight: 700; }}
QLabel#pageCount {{ color: {c['muted']}; font-size: {body}px; }}
QLabel#pageSub {{ color: {c['muted']}; font-size: {body}px; }}
QLabel#sectionTitle {{ font-size: {title}px; font-weight: 700; }}
QLabel#sectionCount {{ color: {c['muted']}; font-size: {small}px; font-weight: 400; }}
QLabel#sectionNote {{ color: {c['faint']}; font-size: {small}px; }}
QLabel#eyebrow {{
    color: {c['faint']}; font-size: {small - 1}px; font-weight: 700; letter-spacing: 0.6px;
}}
QLabel#statLine {{ color: {alpha(fg, 0.46)}; font-size: {small}px; }}
QLabel#emptyTitle {{ font-size: {title}px; font-weight: 600; }}
QLabel#emptySub {{ color: {c['muted']}; }}
QLabel#emptyHint {{ color: {c['faint']}; font-size: {small}px; }}

/* ---- browse: filter column ------------------------------------------ */
QFrame#filters {{ background: {c['bg']}; border-right: 1px solid {alpha(fg, 0.06)}; }}
QLabel#filterGroup {{
    color: {c['faint']}; font-size: {small - 1}px; font-weight: 700; letter-spacing: 0.5px;
}}
QPushButton#filterRow {{
    background: transparent; border: none; border-radius: {sm}px;
    padding: 4px 6px; text-align: left; color: {alpha(fg, 0.86)};
}}
QPushButton#filterRow:hover {{ background: {alpha(fg, 0.05)}; }}
QPushButton#filterRow:checked {{ color: {fg}; }}
QPushButton#segment {{
    background: transparent; border: 1px solid {alpha(fg, 0.12)};
    border-radius: {sm}px; padding: 3px 8px; color: {c['muted']}; font-size: {small}px;
}}
QPushButton#segment:hover {{ background: {alpha(fg, 0.06)}; color: {fg}; }}
QPushButton#segment:checked {{
    background: {alpha(fg, 0.10)}; border-color: {alpha(fg, 0.18)}; color: {fg};
}}
QPushButton#radioPill {{
    background: transparent; border: 1px solid {alpha(fg, 0.12)};
    border-radius: {sm}px; padding: 5px 10px; color: {c['muted']}; text-align: center;
}}
QPushButton#radioPill:hover {{ background: {alpha(fg, 0.05)}; color: {fg}; }}
QPushButton#radioPill:checked {{
    background: {alpha(fg, 0.09)}; border-color: {alpha(fg, 0.16)}; color: {fg};
}}
QPushButton#resetLink {{
    background: transparent; border: none; color: {c['muted']}; text-align: center;
    padding: 5px 4px;
}}
QPushButton#resetLink:hover {{ color: {fg}; }}

/* ---- sort cluster / view toggle ------------------------------------- */
QPushButton#sortPill {{
    background: {c['raised']}; border: 1px solid {alpha(fg, 0.10)};
    border-radius: {md}px; padding: 5px 34px 5px 14px; color: {fg};
}}
QPushButton#sortPill:hover {{ background: {c['hover']}; }}
QPushButton#sortDirection, QAbstractButton#viewToggle {{
    background: {c['raised']}; border: 1px solid {alpha(fg, 0.10)};
    border-radius: {md}px;
}}
QPushButton#sortDirection:hover, QAbstractButton#viewToggle:hover {{ background: {c['hover']}; }}
QAbstractButton#viewToggle:checked {{
    background: {alpha(c['accent'], 0.18)}; border-color: {alpha(c['accent'], 0.4)};
}}

/* ---- cover cards / list rows ---------------------------------------- */
QFrame#coverCard {{ background: transparent; border-radius: {md}px; }}
QFrame#coverCard[hover="true"] {{ background: {alpha(fg, 0.035)}; }}
QFrame#coverCard[selected="true"] {{ background: {alpha(c['accent'], 0.10)}; }}
QLabel#coverTitle {{ font-weight: 600; font-size: {body}px; color: {alpha(fg, 0.94)}; }}
QLabel#coverMeta {{ font-size: {small + 1}px; color: {alpha(fg, 0.5)}; }}
QFrame#listRow {{ border-bottom: 1px solid {alpha(fg, 0.05)}; background: transparent; }}
QFrame#listRow:hover {{ background: {alpha(fg, 0.035)}; }}
QFrame#listRow[selected="true"] {{ background: {alpha(c['accent'], 0.10)}; }}
QLabel#listTitle {{ font-weight: 600; font-size: {body + 1}px; }}
QLabel#listMeta {{ color: {alpha(fg, 0.46)}; font-size: {small + 1}px; }}

/* ---- detail panel ---------------------------------------------------- */
QFrame#detail {{ background: {c['surface']}; border-left: 1px solid {alpha(fg, 0.08)}; }}
QLabel#detailEyebrow {{
    color: {c['faint']}; font-size: {small - 1}px; font-weight: 700; letter-spacing: 0.6px;
}}
QLabel#detailTitle {{ font-size: {title + 3}px; font-weight: 700; }}
QLabel#detailSub {{ color: {c['muted']}; }}
QLabel#kvKey {{
    color: {c['faint']}; font-size: {small - 1}px; font-weight: 700; letter-spacing: 0.4px;
}}
QLabel#kvVal {{ color: {fg}; }}
QLabel#kvWarn {{ color: {c['warning']}; }}
QLabel#kvOk {{ color: {c['success']}; }}
QLabel#detailNote {{ color: {c['muted']}; font-size: {small + 1}px; }}
QLabel#detailSection {{ font-weight: 700; font-size: {body}px; }}
QFrame#kvLine {{ background: transparent; }}
QFrame#detailDivider {{ background: {alpha(fg, 0.07)}; }}

/* ---- downloads ------------------------------------------------------- */
QFrame#dlRow {{ background: transparent; border-bottom: 1px solid {alpha(fg, 0.05)}; }}
QLabel#dlName {{ font-weight: 600; }}
QLabel#dlGame {{ color: {c['muted']}; font-size: {small}px; }}
QLabel#dlDetail {{ color: {c['muted']}; font-size: {small}px; }}
QFrame#dlRow[state="failed"] QLabel#dlDetail {{ color: {c['danger']}; }}
QFrame#dlRow[state="completed"] QLabel#dlDetail {{ color: {c['success']}; }}
QProgressBar#dlBar {{ background: {alpha(fg, 0.10)}; border: none; border-radius: 3px; }}
QProgressBar#dlBar::chunk {{ background: {c['accent']}; border-radius: 3px; }}
QFrame#dlRow[state="failed"] QProgressBar#dlBar::chunk {{ background: {c['danger']}; }}
QFrame#dlRow[state="paused"] QProgressBar#dlBar::chunk,
QFrame#dlRow[state="cancelled"] QProgressBar#dlBar::chunk {{ background: {alpha(fg, 0.35)}; }}
QFrame#dlFoot {{ background: {c['surface']}; border-top: 1px solid {alpha(fg, 0.08)}; }}

/* ---- popovers / menus built as widgets -------------------------------- */
QFrame#popover {{
    background: {c['raised']}; border: 1px solid {c['divider']};
    border-radius: {md}px;
}}
QLabel#popoverTitle {{ color: {c['muted']}; font-size: {small}px; }}
QPushButton#popoverRow {{
    background: transparent; border: none; border-radius: {sm}px;
    padding: 7px 10px; text-align: left;
}}
QPushButton#popoverRow:hover {{ background: {alpha(fg, 0.08)}; }}
QPushButton#popoverRow:checked {{ background: {alpha(c['accent'], 0.18)}; }}
QPushButton#popoverAction {{
    background: transparent; border: none; border-radius: {sm}px;
    padding: 8px 10px; text-align: left; font-weight: 600;
}}
QPushButton#popoverAction:hover {{ background: {alpha(fg, 0.08)}; }}

/* ---- media episode selector ------------------------------------------- */
QFrame#episodeRow {{
    background: {alpha(fg, 0.035)};
    border: 1px solid {alpha(fg, 0.07)};
    border-radius: {md}px;
}}
QFrame#episodeRow:hover {{
    background: {alpha(fg, 0.075)};
    border-color: {alpha(fg, 0.16)};
}}
QLabel#episodeNumber {{ color: {c['muted']}; font-weight: 700; }}
QLabel#episodeTitle {{ font-weight: 600; }}
QLabel#episodeMeta {{ color: {c['faint']}; font-size: {small}px; }}
QLabel#episodePlay {{ color: {c['accent']}; font-size: {body}px; }}
QComboBox#seasonPicker {{ padding: 6px 10px; }}

/* ---- toast ------------------------------------------------------------ */
QFrame#toast {{
    background: {c['raised']}; border: 1px solid {c['divider']}; border-radius: {lg}px;
}}
QFrame#toast[error="true"] {{ border-color: {alpha(c['danger'], 0.6)}; }}

/* ---- dialogs ---------------------------------------------------------- */
QDialog#sheet {{ background: {c['bg']}; }}
QFrame#sheetHeader {{
    background: {c['surface']}; border-bottom: 1px solid {alpha(fg, 0.07)};
}}
QPushButton#sheetTab {{
    background: transparent; border: none; border-radius: {sm}px;
    padding: 6px 14px; color: {c['muted']};
}}
QPushButton#sheetTab:hover {{ background: {alpha(fg, 0.05)}; color: {fg}; }}
QPushButton#sheetTab:checked {{
    background: {alpha(fg, 0.12)}; color: {fg};
}}
QLabel#settLabel {{ font-weight: 600; }}
QLabel#settDesc {{ color: {c['muted']}; font-size: {small + 1}px; }}
QLabel#settPath {{ color: {c['muted']}; font-size: {small + 1}px; }}
QFrame#settDivider {{ background: {alpha(fg, 0.06)}; }}

/* ---- media ------------------------------------------------------------ */
QFrame#mediaTopBar {{ background: {c['bg']}; }}
QLabel#mediaBrand {{ font-size: {body + 1}px; font-weight: 700; }}
QPushButton#mediaNav {{
    background: transparent; border: none; padding: 5px 12px;
    color: {c['muted']}; border-radius: {sm}px;
}}
QPushButton#mediaNav:hover {{ color: {fg}; }}
QPushButton#mediaNav:checked {{ color: {fg}; font-weight: 600; }}
QPushButton#heroAction {{
    background: transparent; border: none; color: {alpha(fg, 0.82)};
    padding: 8px 14px; font-weight: 600;
}}
QPushButton#heroAction:hover {{ color: {fg}; background: {alpha(fg, 0.08)}; border-radius: {md}px; }}
QPushButton#heroPlay {{
    background: {c['accent_fill']}; color: {c['on_accent']}; border: none;
    border-radius: {md}px; padding: 9px 22px; font-weight: 600;
}}
QPushButton#heroPlay:hover {{ background: {c['accent_fill_hover']}; }}
QLabel#heroKind {{
    color: {alpha(fg, 0.8)}; font-size: {small}px; font-weight: 700; letter-spacing: 1.2px;
}}
QLabel#heroTitle {{ font-size: 34px; font-weight: 800; }}
QLabel#heroMeta {{ color: {alpha(fg, 0.78)}; }}
QLabel#heroOverview {{ color: {alpha(fg, 0.8)}; font-size: {body + 1}px; }}
QLabel#posterTitle {{ font-weight: 600; font-size: {body}px; }}
QLabel#posterMeta {{ color: {alpha(fg, 0.46)}; font-size: {small + 1}px; }}
QPushButton#posterCard {{ background: transparent; border: none; padding: 0; }}
QPushButton#posterCard:hover {{ background: {alpha(fg, 0.04)}; border-radius: {md}px; }}
QPushButton#carouselArrow {{
    background: {alpha(c['bg'], 0.72)}; border: 1px solid {alpha(fg, 0.12)};
    border-radius: 16px; padding: 0;
}}
QPushButton#carouselArrow:hover {{ background: {c['raised']}; }}
QLabel#mediaNote {{ color: {c['muted']}; font-size: {body}px; }}

/* ---- misc -------------------------------------------------------------- */
QFrame#metabar {{ background: {alpha(fg, 0.05)}; }}
QLabel#statusBar {{ color: {alpha(fg, 0.34)}; font-size: {small}px; }}
QToolButton#flat::menu-indicator, QPushButton#kebab::menu-indicator {{ image: none; width: 0; }}
QPushButton#kebab {{
    background: {c['raised']}; border: 1px solid {alpha(fg, 0.10)};
    border-radius: {md}px; padding: 0;
}}
QPushButton#kebab:hover {{ background: {c['hover']}; }}
"""
    if not compact:
        return qss
    # Only padding and type sizes change: colours still come from the palette
    # above, so an accent or a theme switch keeps working in compact mode.
    return qss + f"""
/* ---- compact density -------------------------------------------------- */
QPushButton, QToolButton {{ padding: 3px 9px; }}
QPushButton#small {{ padding: 2px 7px; }}
QPushButton#pill {{ padding: 3px 10px; }}
QPushButton#filterRow {{ padding: 2px 4px; }}
QPushButton#sheetTab {{ padding: 4px 10px; }}
QLineEdit, QComboBox, QSpinBox {{ padding: 4px 7px; }}
QLineEdit#librarySearch {{ padding: 6px 14px 6px 34px; }}
QMenu::item {{ padding: 4px 20px 4px 12px; }}
QLabel#coverTitle {{ font-size: {small}px; }}
QLabel#coverMeta, QLabel#listMeta {{ font-size: {small}px; }}
QLabel#listTitle {{ font-size: {body}px; }}
QFrame#coverCard {{ border-radius: {sm}px; }}
"""


__all__ = ["alpha", "build_stylesheet", "font_family"]
