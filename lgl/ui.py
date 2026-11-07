"""GTK3 desktop UI for browsing the Internet Archive collection.

The window is a thin, theme-aware shell over the backend modules:

* collection and item lookups always run in worker threads;
* the download manager reports from its own thread, so all UI refreshes are
  marshalled back with ``GLib.idle_add``;
* downloaded files are never executed without an explicit, check-boxed
  confirmation in the launch dialog.

No GTK call is made from a worker thread.
"""

from __future__ import annotations

import collections
import json
import os
import math
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import zlib
from pathlib import Path
from typing import Callable, Sequence

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import (  # noqa: E402
    Gdk,
    GdkPixbuf,
    GLib,
    GObject,
    Gtk,
    Pango,
    PangoCairo,
)

from . import browse_filters
from . import profiles
from . import recently_viewed
from . import APP_ID, APP_NAME, __version__, platform_support  # noqa: E402
from . import external_dm  # noqa: E402
from . import gtk_shortcuts
from . import kryoto  # noqa: E402
from . import magnet as magnetlib  # noqa: E402
from . import metadata as metalib  # noqa: E402
from . import theme_gtk  # noqa: E402
from . import view_prefs
from .gtk_quick_preview import QuickPreview  # noqa: E402
from .archive import (  # noqa: E402
    ArchiveClient,
    ArchiveError,
    ArchiveFile,
    ArchiveItem,
    CollectionResult,
    GameSummary,
)
from .artwork import (  # noqa: E402
    SYNC_WORKERS as ARTWORK_SYNC_WORKERS,
    ArtworkSearchers,
    Candidate as ArtworkCandidate,
    apply_candidate as apply_artwork_candidate,
    find_candidates as find_artwork_candidates,
    needs_artwork as artwork_needed,
    pick_candidate as pick_artwork_candidate,
    rank_candidates as rank_artwork_candidates,
    remember_miss as remember_artwork_miss,
    remembered_miss as artwork_miss_remembered,
    with_screenshots as with_artwork_screenshots,
)
from .catalog_store import (  # noqa: E402
    CatalogGame,
    CatalogStore,
    DOWNLOAD_STATE_LABELS,
    DOWNLOAD_STATE_RANK,
    MagnetRecord,
    MirrorRecord,
)
from .compatibility import analyze_folder, explain as explain_compatibility  # noqa: E402
from .importer import (  # noqa: E402
    CatalogImporter,
    ImportOptions,
    ImportOutcome,
    SidecarRefreshReport,
    refresh_sidecar_magnets,
)
from .sources import (  # noqa: E402
    SOURCE_TYPES,
    Capability,
    CapabilityReport,
    ConfiguredJsonSource,
    KryotoSource,
    RawgSource,
    SourceError,
    normalize_source_config,
    source_for_url,
)
from .downloader import DownloadManager, DownloadStatus, TaskSnapshot  # noqa: E402
from .feeds import FeedError, LocalFileSource, local_source  # noqa: E402
from .filetypes import is_shell  # noqa: E402
from .browse import SIZE_FILTERS, count_label, genre_counts, size_matches
from .genres import genre_options  # noqa: E402
from .integrity import (  # noqa: E402
    IntegrityResult,
    IntegrityStatus,
    verify_file,
)
from .launch import LaunchError, launcher_rank, launch, make_executable  # noqa: E402
from .library import (  # noqa: E402
    GameFolder,
    LocalFile,
    game_folder,
    scan_game_folder,
    write_manifest,
)
from .library_store import (  # noqa: E402
    DuplicateGroup,
    LibraryEntry,
    LibraryStore,
    default_descending,
)
from .settings import (  # noqa: E402
    MAX_DOWNLOAD_RETRIES,
    MAX_NETWORK_TIMEOUT,
    MIN_DOWNLOAD_RETRIES,
    MIN_NETWORK_TIMEOUT,
    Settings,
    config_path as settings_path,
)
from .torrent import (  # noqa: E402
    EngineUnavailable,
    TorrentError,
    TorrentManager,
    TorrentTask,
)
from .util import (  # noqa: E402
    AppError,
    check_download_dir,
    default_download_dir,
    human_eta,
    human_size,
    human_speed,
    safe_filename,
    validate_identifier,
)

# -- small helpers -----------------------------------------------------------

_ICON = Gtk.IconSize.BUTTON


def _spawn(work: Callable[[], object], on_success: Callable[[object], None],
           on_error: Callable[[BaseException], None]) -> None:
    """Run *work* off the main loop and deliver the result through GLib."""

    def runner() -> None:
        try:
            result = work()
        except BaseException as exc:  # noqa: BLE001 - reported to the UI
            GLib.idle_add(on_error, exc)
        else:
            GLib.idle_add(on_success, result)

    threading.Thread(target=runner, daemon=True, name="lgl-ui-worker").start()


def _error_text(exc: BaseException) -> str:
    text = str(exc).strip()
    if isinstance(exc, AppError) and text:
        return text
    if text:
        return text
    return "Something went wrong. Please try again."


def _flat_button(icon_name: str, tooltip: str) -> Gtk.Button:
    button = Gtk.Button()
    button.add(Gtk.Image.new_from_icon_name(icon_name, _ICON))
    button.set_relief(Gtk.ReliefStyle.NONE)
    button.set_tooltip_text(tooltip)
    button.get_accessible().set_name(tooltip)
    button.set_valign(Gtk.Align.CENTER)
    return button


def _small_button(label: str, tooltip: str | None = None) -> Gtk.Button:
    button = Gtk.Button(label=label)
    button.set_relief(Gtk.ReliefStyle.NONE)
    button.get_style_context().add_class("small-button")
    if tooltip:
        button.set_tooltip_text(tooltip)
    button.set_valign(Gtk.Align.CENTER)
    return button


def apply_search_visibility(pill: Gtk.Widget | None, view: str | None) -> bool:
    """Show the search pill only where it searches something.

    Only Browse queries the collection; Library, Downloads and Media have their
    own controls.  Hiding just the entry left an empty capsule sitting in the
    middle of those pages' headers, so the whole pill is what gets hidden.
    """
    visible = (view or "archive") == "archive"
    if pill is not None:
        # set_visible(False) alone is undone by the next show_all(), which put the
        # capsule back on Library/Downloads/Media, so the pill is also marked
        # no-show-all for as long as it is hidden.
        pill.set_no_show_all(not visible)
        pill.set_visible(visible)
    return visible


def _filter_footer(on_reset: Callable[..., None]) -> Gtk.Box:
    """The reset control, on its own.

    It used to sit under an "Availability" heading with nothing else beneath it,
    which read as a section that had failed to load.
    """
    footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    footer.get_style_context().add_class("filter-group")
    reset = Gtk.Button(label="Reset all filters")
    reset.get_style_context().add_class("link-button")
    reset.set_halign(Gtk.Align.START)
    reset.connect("clicked", lambda *_: on_reset())
    footer.pack_start(reset, False, False, 0)
    return footer


def _year_range_row(
    on_change: Callable[[], None],
) -> tuple[Gtk.Box, Gtk.Entry, Gtk.Entry]:
    """From/to year fields: the only place the Browse year filter is stored.

    A half-typed year never filters anything, because the range is read when the
    box is confirmed (Enter) or left, not on every keystroke.
    """
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    row.get_style_context().add_class("filter-row")
    row.set_margin_top(6)
    # Same heading style as "Released", so the row reads as part of the filter
    # column instead of a form dropped into it.
    caption = Gtk.Label(label="Years", xalign=0)
    caption.get_style_context().add_class("filter-title")
    caption.set_valign(Gtk.Align.CENTER)
    row.pack_start(caption, False, False, 0)

    def changed(*_args) -> bool:
        on_change()
        return False

    def entry(placeholder: str) -> Gtk.Entry:
        field = Gtk.Entry()
        field.set_width_chars(6)
        field.set_max_width_chars(6)
        # .filter-year gives the field the chips' text size and height; without
        # it the entries tower over the decade chips beside them.
        field.get_style_context().add_class("filter-year")
        field.set_placeholder_text(placeholder)
        field.set_input_purpose(Gtk.InputPurpose.DIGITS)
        field.set_tooltip_text(
            f"{placeholder} year, e.g. 1995. Empty means no bound."
        )
        field.get_accessible().set_name(f"{placeholder} year")
        field.connect("activate", changed)
        field.connect("focus-out-event", changed)
        return field

    start = entry("From")
    end = entry("To")
    dash = Gtk.Label(label="\u2013")
    dash.get_style_context().add_class("filter-title")
    dash.set_valign(Gtk.Align.CENTER)
    # Not expanding: the fields keep their six characters instead of stretching
    # across the column, which is what made them look oversized.
    row.pack_start(start, False, False, 0)
    row.pack_start(dash, False, False, 0)
    row.pack_start(end, False, False, 0)
    return row, start, end


def _section_label(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text.upper(), xalign=0)
    label.get_style_context().add_class("section-label")
    return label


def _sort_cluster(combo: Gtk.ComboBoxText, direction: Gtk.Button) -> Gtk.Box:
    """A sort picker with its direction button beside it, on one row.

    Both the Library toolbar and the Browse sidebar use this, so the two sort
    controls cannot drift apart in spacing or button height.
    """
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    row.get_style_context().add_class("sort-group")
    row.set_valign(Gtk.Align.CENTER)
    row.pack_start(combo, True, True, 0)
    row.pack_start(direction, False, False, 0)
    return row


class _Picker(Gtk.MenuButton):
    """A compact popover picker that keeps the small ComboBox-style API.

    ``Gtk.ComboBoxText`` opens its list as a menu, which a scrolled sidebar
    clips and which cannot be searched or height-capped; a real tag list made
    that unusable.  This opens a normal popover instead — never cut off,
    scrolls past a fixed height, and filters as you type — while still
    offering ``get_active_id``, ``set_active_id``, ``append`` and
    ``remove_all`` so the call sites do not change.
    """

    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(
        self,
        options=(),
        *,
        tooltip: str = "",
        accessible: str = "",
        searchable: bool = False,
        width: int = 230,
        list_height: int = 280,
    ) -> None:
        super().__init__()
        self._ids: list[str] = []
        self._labels: dict[str, str] = {}
        self._items: dict[str, Gtk.Button] = {}
        self._active = ""
        self._searchable = bool(searchable)

        self.set_valign(Gtk.Align.CENTER)
        self.get_style_context().add_class("picker")
        if tooltip:
            self.set_tooltip_text(tooltip)
        if accessible:
            self.get_accessible().set_name(accessible)

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._value = Gtk.Label(xalign=0.0)
        self._value.set_ellipsize(Pango.EllipsizeMode.END)
        self._value.set_max_width_chars(20)
        head.pack_start(self._value, True, True, 0)
        head.pack_start(
            Gtk.Image.new_from_icon_name("pan-down-symbolic", Gtk.IconSize.MENU),
            False,
            False,
            0,
        )
        self.add(head)

        popover = Gtk.Popover()
        popover.set_position(Gtk.PositionType.BOTTOM)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        body.set_margin_start(6)
        body.set_margin_end(6)
        body.set_margin_top(6)
        body.set_margin_bottom(6)
        body.set_size_request(width, -1)
        self._search_entry: Gtk.SearchEntry | None = None
        if self._searchable:
            self._search_entry = Gtk.SearchEntry()
            self._search_entry.set_placeholder_text("Search…")
            self._search_entry.connect("changed", self._on_search_changed)
            body.pack_start(self._search_entry, False, False, 0)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_propagate_natural_height(True)
        scroller.set_max_content_height(list_height)
        self._rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        scroller.add(self._rows)
        body.pack_start(scroller, True, True, 0)
        body.show_all()
        popover.add(body)
        popover.connect("show", self._on_popover_shown)
        self._popover = popover
        self.set_popover(popover)

        for identifier, label in options:
            self.append(identifier, label)
        if options:
            self.set_active_id(options[0][0])

    # -- the ComboBox-shaped API -------------------------------------------

    def append(self, identifier: str, label: str) -> None:
        identifier = str(identifier)
        if identifier in self._labels:
            return
        button = Gtk.Button(label=str(label))
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.set_halign(Gtk.Align.FILL)
        button.set_valign(Gtk.Align.CENTER)
        button.get_style_context().add_class("picker-item")
        child = button.get_child()
        if isinstance(child, Gtk.Label):
            child.set_xalign(0.0)
            child.set_ellipsize(Pango.EllipsizeMode.END)
        button.set_tooltip_text(str(label))
        button.connect("clicked", lambda _b, value=identifier: self._choose(value))
        button.show_all()
        self._rows.pack_start(button, False, False, 0)
        self._ids.append(identifier)
        self._labels[identifier] = str(label)
        self._items[identifier] = button

    def remove_all(self) -> None:
        for button in self._items.values():
            button.destroy()
        self._items.clear()
        self._ids.clear()
        self._labels.clear()
        self._active = ""
        self._value.set_text("")

    def get_model(self) -> list[tuple[str, str]]:
        """Rows as ``(label, identifier)`` pairs, like a ComboBoxText model."""
        return [(self._labels[identifier], identifier) for identifier in self._ids]

    def get_active_id(self) -> str:
        return self._active

    def get_active_text(self) -> str:
        return self._labels.get(self._active, "")

    def set_active_id(self, identifier: object) -> bool:
        value = str(identifier or "")
        if value not in self._labels:
            return False
        changed = value != self._active
        self._active = value
        self._value.set_text(self._labels[value])
        for key, button in self._items.items():
            context = button.get_style_context()
            if key == value:
                context.add_class("picker-selected")
            else:
                context.remove_class("picker-selected")
        if changed:
            self.emit("changed")
        return True

    def _choose(self, identifier: str) -> None:
        self._popover.popdown()
        self.set_active_id(identifier)

    def _on_popover_shown(self, *_args) -> None:
        if self._search_entry is not None:
            self._search_entry.set_text("")
        for button in self._items.values():
            button.set_visible(True)

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        needle = entry.get_text().strip().casefold()
        for identifier, button in self._items.items():
            button.set_visible(
                not needle or needle in self._labels[identifier].casefold()
            )


def _path_from_file_url(url: str) -> Path:
    """Rebuild a local catalog's path from the ``file://`` URL stored on it."""
    split = urllib.parse.urlsplit(url or "")
    if split.scheme.lower() != "file":
        raise ValueError("This source is not a local file.")
    if split.netloc not in ("", "localhost"):
        raise ValueError("A file URL on another host is not supported.")
    return Path(urllib.request.url2pathname(split.path))


def _shorten_home(path: Path) -> str:
    try:
        home = Path.home()
        text = str(path)
        if text == str(home):
            return "~"
        if text.startswith(str(home) + "/"):
            return "~" + text[len(str(home)):]
        return text
    except (OSError, ValueError):
        return str(path)


def _archive_image_url(url: str) -> bool:
    """True for an https image URL this client is allowed to fetch."""
    try:
        split = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    host = (split.hostname or "").lower()
    return split.scheme == "https" and (
        host == "archive.org" or host.endswith(".archive.org")
    )


def _format_count(n: int) -> str:
    return f"{n:,}"


def _download_count(n: int) -> str:
    return f"{_format_count(n)} download" + ("" if n == 1 else "s")


def _plural(n: int, singular: str, plural: str | None = None) -> str:
    return singular if n == 1 else (plural or singular + "s")


def _animations_enabled() -> bool:
    """Respect the desktop's reduced-motion preference."""
    try:
        settings = Gtk.Settings.get_default()
        if settings is None:
            return True
        return bool(settings.get_property("gtk-enable-animations"))
    except (TypeError, AttributeError, GLib.Error):
        return True


def _style_stack(stack: Gtk.Stack) -> None:
    if _animations_enabled():
        stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        stack.set_transition_duration(140)
    else:
        stack.set_transition_type(Gtk.StackTransitionType.NONE)
        stack.set_transition_duration(0)


def _style_revealer(revealer: Gtk.Revealer,
                    kind: Gtk.RevealerTransitionType = Gtk.RevealerTransitionType.SLIDE_DOWN) -> None:
    if _animations_enabled():
        revealer.set_transition_type(kind)
        revealer.set_transition_duration(160)
    else:
        revealer.set_transition_type(Gtk.RevealerTransitionType.NONE)
        revealer.set_transition_duration(0)


def _icon_text_button(icon_name: str, label: str, tooltip: str) -> Gtk.Button:
    button = Gtk.Button()
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
    box.pack_start(Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.MENU), False, False, 0)
    box.pack_start(Gtk.Label(label=label), False, False, 0)
    button.add(box)
    button.set_relief(Gtk.ReliefStyle.NONE)
    button.get_style_context().add_class("small-button")
    button.set_tooltip_text(tooltip)
    button.get_accessible().set_name(tooltip)
    button.set_valign(Gtk.Align.CENTER)
    return button


def load_css() -> None:
    """Install the token palette and the structural stylesheet.

    The palette is its own provider so :meth:`MainWindow.apply_theme` can swap
    it at runtime.  ``style.css`` only names tokens, never a literal colour, so
    it never needs reloading and cannot go stale.
    """
    theme_gtk.install()


# -- toast -------------------------------------------------------------------


class Toast(Gtk.Revealer):
    """A single short-lived message anchored to the bottom of the window."""

    def __init__(self) -> None:
        super().__init__()
        _style_revealer(self, Gtk.RevealerTransitionType.SLIDE_UP)
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.END)
        self.set_margin_bottom(26)
        self.set_no_show_all(True)

        self._box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        self._box.get_style_context().add_class("toast")
        self._icon = Gtk.Image.new_from_icon_name(
            "dialog-information-symbolic", Gtk.IconSize.MENU
        )
        self._icon.set_valign(Gtk.Align.CENTER)
        self._label = Gtk.Label(xalign=0)
        self._label.set_line_wrap(True)
        self._label.set_max_width_chars(62)
        self._box.pack_start(self._icon, False, False, 0)
        self._box.pack_start(self._label, True, True, 0)
        self.add(self._box)
        self._timeout = 0

    def show_message(self, text: str, error: bool = False) -> None:
        if self._timeout:
            GLib.source_remove(self._timeout)
            self._timeout = 0
        context = self._box.get_style_context()
        if error:
            context.add_class("error")
            self._icon.set_from_icon_name("dialog-error-symbolic", Gtk.IconSize.MENU)
        else:
            context.remove_class("error")
            self._icon.set_from_icon_name("dialog-information-symbolic", Gtk.IconSize.MENU)
        self._label.set_text(text)
        self.show()
        self.set_reveal_child(True)
        self._timeout = GLib.timeout_add(4600, self._hide)

    def dismiss(self) -> None:
        if self._timeout:
            GLib.source_remove(self._timeout)
            self._timeout = 0
        self.set_reveal_child(False)

    def _hide(self) -> bool:
        self._timeout = 0
        self.set_reveal_child(False)
        return False


# -- game rows ---------------------------------------------------------------


def _decode_image_bytes(data: object) -> "GdkPixbuf.Pixbuf | None":
    """Decode image bytes that came back from the image pool."""
    if not isinstance(data, (bytes, bytearray)) or not data:
        return None
    loader = GdkPixbuf.PixbufLoader()
    try:
        loader.write(bytes(data))
        loader.close()
    except GLib.Error:
        return None
    return loader.get_pixbuf()


def _archive_thumb_url(identifier: str) -> str:
    """archive.org's public item-image service; no metadata lookup needed."""
    if not identifier or identifier.startswith("catalog:"):
        return ""
    return "https://archive.org/services/img/" + urllib.parse.quote(identifier, safe="")


def _bare_title(title: object) -> str:
    """The game's name without version or store tags: ``Doom (v1.9)`` -> ``Doom``."""
    text = " ".join(str(title or "").split())
    for marker in (" (", " [", " {"):
        head, sep, _tail = text.partition(marker)
        if sep and head.strip():
            text = head.strip()
    return text


def _tile_meta(game: "GameSummary") -> str:
    bits: list[str] = []
    if game.item_size:
        bits.append(human_size(game.item_size))
    if game.year:
        bits.append(str(game.year))
    if game.is_catalog:
        state = DOWNLOAD_STATE_LABELS.get(game.download_state)
        if state:
            bits.append(state)
    return " · ".join(bits)


def _media_icon() -> str:
    """A film/TV icon that this icon theme actually has.

    Icon names differ between icon themes, and a missing name draws a broken
    image, so the first available candidate wins.
    """
    theme = Gtk.IconTheme.get_default()
    for name in (
        "applications-multimedia-symbolic",
        "video-display-symbolic",
        "video-x-generic-symbolic",
        "camera-video-symbolic",
        "media-playback-start-symbolic",
    ):
        if theme is None or theme.has_icon(name):
            return name
    return "applications-multimedia-symbolic"


#: Density flag for the cover grids, set from the Interface preference.  It is
#: module-level because tiles are built deep inside the views; the only writer
#: is :func:`set_compact_layout`, called from ``MainWindow``.
_COMPACT_LAYOUT = False


def set_compact_layout(compact: bool) -> bool:
    """Set the cover-grid density; returns ``True`` when the value changed."""
    global _COMPACT_LAYOUT
    changed = bool(compact) != _COMPACT_LAYOUT
    _COMPACT_LAYOUT = bool(compact)
    return changed


def card_cover_size(layout_name: str = "poster") -> tuple[int, int]:
    """Cover size in the Browse grid for a home card layout.

    Compact mode keeps its own, smaller size; poster and large differ only by
    the multiplier in :func:`lgl.home_layout.scale`.
    """
    from . import home_layout

    if _COMPACT_LAYOUT:
        return 118, 177
    factor = home_layout.scale(layout_name)
    return int(150 * factor), int(225 * factor)


class BrowseTile(Gtk.FlowBoxChild):
    """One cover in the Browse grid: artwork, then name and one meta line."""

    COVER_WIDTH = 150
    COVER_HEIGHT = 225

    def __init__(self, window: "MainWindow", game: GameSummary) -> None:
        super().__init__()
        from . import home_layout

        self.game = game
        self._window = window
        self._token = 0
        try:
            layout_name = home_layout.HomeLayout(getattr(window, "settings", None)).layout()
        except (AttributeError, TypeError, ValueError):
            layout_name = "poster"
        self.COVER_WIDTH, self.COVER_HEIGHT = card_cover_size(layout_name)
        self.get_style_context().add_class("browse-tile")

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        card.get_style_context().add_class("cover-card")
        self.add(card)

        frame = Gtk.Box()
        frame.get_style_context().add_class("tile-cover")
        frame.set_size_request(self.COVER_WIDTH, self.COVER_HEIGHT)
        self._art = CoverArt()
        self._art.set_monogram(game.title or game.identifier or "?")
        frame.add(self._art)
        card.pack_start(frame, False, False, 0)

        title = Gtk.Label(
            label=_bare_title(game.title) or game.identifier, xalign=0
        )
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_single_line_mode(True)
        title.set_margin_top(7)
        title.get_style_context().add_class("tile-title")
        card.pack_start(title, False, False, 0)

        meta_text = _tile_meta(game)
        meta = Gtk.Label(label=meta_text, xalign=0)
        meta.set_ellipsize(Pango.EllipsizeMode.END)
        meta.set_single_line_mode(True)
        meta.get_style_context().add_class("tile-meta")
        meta.set_no_show_all(True)
        if meta_text:
            meta.show()
        card.pack_start(meta, False, False, 0)

        self.set_tooltip_text(game.title + "\n" + game.identifier)
        self.get_accessible().set_name(game.title or game.identifier)

    def set_cover(self, pixbuf: "GdkPixbuf.Pixbuf | None") -> None:
        if pixbuf is not None:
            self._art.set_pixbuf(pixbuf)

    def load_cover(self) -> None:
        """Ask for this cover once; a missing picture keeps the monogram."""
        game = self.game
        url = game.cover_url or _archive_thumb_url(game.identifier)
        if not url:
            return
        hosts = None
        if game.is_catalog and game.catalog_source:
            hosts = self._window.image_hosts_for(game.catalog_source) or None
        self._token += 1
        token = self._token

        def done(payload: object) -> None:
            if token != self._token:
                return
            pixbuf = _decode_image_bytes(payload)
            if pixbuf is None:
                return
            self.set_cover(_cover_thumbnail(pixbuf))

        def failed(_exc: object) -> None:
            return

        self._window.fetch_poster(
            game.identifier, url, done, failed, allowed_hosts=hosts
        )


class GameRow(Gtk.ListBoxRow):
    """One archive item in the left-hand collection list."""

    def __init__(self, game: GameSummary) -> None:
        super().__init__()
        self.game = game
        self.get_style_context().add_class("game-row")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title = Gtk.Label(label=game.title, xalign=0)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_single_line_mode(True)
        title.get_style_context().add_class("game-title")

        bits: list[str] = []
        if game.year:
            bits.append(game.year)
        if game.item_size:
            bits.append(human_size(game.item_size))
        if game.is_catalog:
            # What is known about its links, never a private popularity count it
            # does not have.  "link broken" is shown rather than hidden.
            state = DOWNLOAD_STATE_LABELS.get(game.download_state)
            if state:
                bits.append(state)
        else:
            bits.append(_download_count(game.downloads))
        meta = Gtk.Label(label=" · ".join(bits), xalign=0)
        meta.set_ellipsize(Pango.EllipsizeMode.END)
        meta.set_single_line_mode(True)
        meta.get_style_context().add_class("game-meta")

        box.pack_start(title, False, False, 0)
        box.pack_start(meta, False, False, 0)
        self.add(box)
        self.set_tooltip_text(f"{game.title}\n{game.identifier}")
        self.get_accessible().set_name(f"{game.title}, {game.subtitle}")


# -- download rows -----------------------------------------------------------


class TaskRow(Gtk.ListBoxRow):
    """One task in the bottom queue. Updated in place while it runs."""

    def __init__(self, snapshot: TaskSnapshot, title: str) -> None:
        super().__init__()
        self.task_id = snapshot.id
        self.get_style_context().add_class("task-row")

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._file = Gtk.Label(label=snapshot.filename, xalign=0)
        self._file.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self._file.set_single_line_mode(True)
        self._file.get_style_context().add_class("task-file")
        self._tag = Gtk.Label(label="Queued")
        self._tag.get_style_context().add_class("status-pill")
        self._tag.set_valign(Gtk.Align.CENTER)
        top.pack_start(self._file, True, True, 0)
        top.pack_start(self._tag, False, False, 0)
        outer.pack_start(top, False, False, 0)

        self._game = Gtk.Label(label=title, xalign=0)
        self._game.set_ellipsize(Pango.EllipsizeMode.END)
        self._game.set_single_line_mode(True)
        self._game.get_style_context().add_class("task-game")
        outer.pack_start(self._game, False, False, 0)

        self._progress = Gtk.ProgressBar()
        self._progress.set_valign(Gtk.Align.CENTER)
        self._progress.get_style_context().add_class("download-progress")
        outer.pack_start(self._progress, False, False, 0)

        self._detail = Gtk.Label(label="", xalign=0)
        self._detail.set_ellipsize(Pango.EllipsizeMode.END)
        self._detail.set_single_line_mode(True)
        self._detail.get_style_context().add_class("task-detail")
        outer.pack_start(self._detail, False, False, 0)

        self._error = Gtk.Label(label="", xalign=0)
        self._error.set_line_wrap(True)
        self._error.set_max_width_chars(90)
        self._error.get_style_context().add_class("error-text")
        self._error.set_no_show_all(True)
        outer.pack_start(self._error, False, False, 0)

        self.add(outer)
        self.update(snapshot, title)

    def update(self, snapshot: TaskSnapshot, title: str) -> None:
        self._file.set_text(snapshot.filename)
        self._game.set_text(title)
        self._file.set_tooltip_text(snapshot.filename)
        self._game.set_tooltip_text(title)
        self._tag.set_text(snapshot.status.value.capitalize())
        context = self._tag.get_style_context()
        for name in ("running", "completed", "failed", "paused"):
            context.remove_class(f"status-{name}")
        context.add_class(f"status-{snapshot.status.value}")
        self._tag.set_tooltip_text(f"Status: {snapshot.status.value}")

        progress = snapshot.progress
        if snapshot.status == DownloadStatus.RUNNING and snapshot.total is None:
            self._progress.pulse()
        elif progress is None:
            self._progress.set_fraction(1.0 if snapshot.status == DownloadStatus.COMPLETED else 0.0)
        else:
            self._progress.set_fraction(progress)

        self._detail.set_text(self._detail_text(snapshot))
        if snapshot.error:
            self._error.set_text(snapshot.error)
            self._error.show()
        else:
            self._error.hide()

    @staticmethod
    def _detail_text(snapshot: TaskSnapshot) -> str:
        downloaded = human_size(snapshot.downloaded)
        total = human_size(snapshot.total) if snapshot.total else "unknown size"
        status = snapshot.status
        if status == DownloadStatus.RUNNING:
            parts = [f"{downloaded} of {total}"]
            if snapshot.speed:
                parts.append(human_speed(snapshot.speed))
            if snapshot.eta is not None:
                parts.append(f"{human_eta(snapshot.eta)} left")
            return " · ".join(parts)
        if status == DownloadStatus.QUEUED:
            return "Waiting to start"
        if status == DownloadStatus.PAUSED:
            return f"Paused at {downloaded} of {total}"
        if status == DownloadStatus.COMPLETED:
            return f"Saved to {Path(snapshot.final_path).parent}"
        if status == DownloadStatus.CANCELLED:
            return f"Cancelled at {downloaded}; partial file kept"
        if snapshot.retry_limit:
            return (
                f"Failed after {downloaded} · attempt {snapshot.attempts} "
                f"of {snapshot.retry_limit}"
            )
        return f"Failed after {downloaded}"


class TorrentRow(Gtk.ListBoxRow):
    """One magnet task in the torrent section of the download queue."""

    STATUS_TEXT = {
        "queued": "Queued",
        "downloading": "Downloading",
        "paused": "Paused",
        "completed": "Completed",
        "failed": "Failed",
    }

    def __init__(self, task: TorrentTask) -> None:
        super().__init__()
        self.task_id = task.task_id
        self.get_style_context().add_class("task-row")
        self.get_style_context().add_class("torrent-row")

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._title = Gtk.Label(xalign=0)
        self._title.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self._title.set_single_line_mode(True)
        self._title.get_style_context().add_class("task-file")
        self._tag = Gtk.Label(label="Queued")
        self._tag.get_style_context().add_class("status-pill")
        self._tag.set_valign(Gtk.Align.CENTER)
        top.pack_start(self._title, True, True, 0)
        top.pack_start(self._tag, False, False, 0)
        outer.pack_start(top, False, False, 0)

        self._progress = Gtk.ProgressBar()
        self._progress.set_valign(Gtk.Align.CENTER)
        self._progress.get_style_context().add_class("download-progress")
        outer.pack_start(self._progress, False, False, 0)

        self._detail = Gtk.Label(label="", xalign=0)
        self._detail.set_ellipsize(Pango.EllipsizeMode.END)
        self._detail.set_single_line_mode(True)
        self._detail.get_style_context().add_class("task-detail")
        outer.pack_start(self._detail, False, False, 0)

        self._error = Gtk.Label(label="", xalign=0)
        self._error.set_ellipsize(Pango.EllipsizeMode.END)
        self._error.set_single_line_mode(True)
        self._error.get_style_context().add_class("error-text")
        self._error.set_no_show_all(True)
        outer.pack_start(self._error, False, False, 0)

        self.add(outer)
        self.update(task)

    def update(self, task: TorrentTask) -> None:
        name = task.title or f"{task.infohash[:12]}…"
        self._title.set_text(name)
        self._title.set_tooltip_text(task.magnet)
        self._tag.set_text(self.STATUS_TEXT.get(task.status, task.status.capitalize()))
        context = self._tag.get_style_context()
        for name in ("queued", "running", "completed", "failed", "paused"):
            context.remove_class(f"status-{name}")
        context.add_class(
            "status-running" if task.status == "downloading" else f"status-{task.status}"
        )

        if task.status == "downloading" and task.total <= 0:
            self._progress.pulse()
        else:
            self._progress.set_fraction(task.fraction)

        self._detail.set_text(self._detail_text(task))
        if task.error:
            self._error.set_text(task.error)
            self._error.set_no_show_all(False)
            self._error.show()
        else:
            self._error.set_text("")
            self._error.hide()

    @staticmethod
    def _detail_text(task: TorrentTask) -> str:
        completed = human_size(task.completed)
        total = human_size(task.total) if task.total else "size unknown"
        peers = f"{task.peers} {_plural(task.peers, 'peer')}"
        if task.status == "downloading":
            speed = human_speed(task.speed) if task.speed else "no speed yet"
            return f"{completed} of {total} · {speed} · {peers}"
        if task.status == "queued":
            return "Waiting to start"
        if task.status == "paused":
            return f"Paused at {completed} of {total}"
        if task.status == "completed":
            folder = task.folder or task.save_dir
            return f"Finished · {human_size(task.total)}" if not folder else (
                f"Finished · {human_size(task.total)} · {folder}"
            )
        return f"Stopped at {completed} of {total}"


# -- download queue ----------------------------------------------------------


class DownloadsPanel(Gtk.Box):
    """Bottom queue: one live row per task plus selected-task actions."""

    def __init__(self, owner: "MainWindow") -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._owner = owner
        self._manager: DownloadManager = owner.manager
        self._torrents: TorrentManager = owner.torrents
        self._rows: dict[str, TaskRow] = {}
        self._torrent_rows: dict[str, TorrentRow] = {}
        self._selected_id: str | None = None
        self._selected_torrent_id: str | None = None
        self._last_status: dict[str, DownloadStatus] = {}
        self._updating = False
        self._updating_torrents = False

        self.get_style_context().add_class("downloads-panel")
        self.set_size_request(-1, 286)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.get_style_context().add_class("downloads-header")
        header.pack_start(_section_label("Downloads"), False, False, 0)
        self._count = Gtk.Label(label="", xalign=0)
        self._count.get_style_context().add_class("count-label")
        header.pack_start(self._count, False, False, 0)
        header.pack_start(Gtk.Box(), True, True, 0)

        self._pause_btn = _small_button("Pause", "Pause or resume the selected download")
        self._cancel_btn = _small_button("Cancel", "Cancel the selected download and keep its partial file")
        self._retry_btn = _small_button("Retry", "Retry the selected failed or cancelled download")
        self._remove_btn = _small_button("Remove", "Remove the selected finished task from the queue")
        self._clear_btn = _small_button("Clear finished", "Remove all finished tasks from the queue")
        self._collapse_btn = _flat_button("go-down-symbolic", "Hide the download queue")
        for button in (self._pause_btn, self._cancel_btn, self._retry_btn,
                       self._remove_btn, self._clear_btn):
            header.pack_start(button, False, False, 0)
        header.pack_start(self._collapse_btn, False, False, 0)
        self.pack_start(header, False, False, 0)

        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._stack.set_transition_duration(110)

        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        empty.set_valign(Gtk.Align.CENTER)
        empty.set_halign(Gtk.Align.CENTER)
        empty_title = Gtk.Label(label="No downloads yet")
        empty_title.get_style_context().add_class("empty-text")
        empty_hint = Gtk.Label(label="Select a game and add files to see them here.")
        empty_hint.get_style_context().add_class("dim")
        empty_hint.get_style_context().add_class("small")
        empty.pack_start(empty_title, False, False, 0)
        empty.pack_start(empty_hint, False, False, 0)
        self._stack.add_named(empty, "empty")

        self._list = Gtk.ListBox()
        self._list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._list.set_activate_on_single_click(True)
        self._list.connect("row-selected", self._on_row_selected)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.add(self._list)
        self._stack.add_named(scroller, "list")

        self.pack_start(self._stack, True, True, 0)
        self._stack.set_visible_child_name("empty")

        # -- torrents -------------------------------------------------------
        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep_context = separator.get_style_context()
        sep_context.add_class("torrent-separator")
        self.pack_start(separator, False, False, 0)

        self._torrent_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._torrent_section.get_style_context().add_class("torrent-section")

        torrent_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        torrent_header.get_style_context().add_class("torrent-header")
        torrent_header.pack_start(_section_label("Torrents"), False, False, 0)
        self._torrent_count = Gtk.Label(label="", xalign=0)
        self._torrent_count.get_style_context().add_class("count-label")
        torrent_header.pack_start(self._torrent_count, False, False, 0)
        self._engine_hint = Gtk.Label(label="", xalign=0)
        self._engine_hint.set_ellipsize(Pango.EllipsizeMode.END)
        self._engine_hint.get_style_context().add_class("torrent-engine")
        torrent_header.pack_start(self._engine_hint, True, True, 0)

        self._t_start_btn = _small_button("Start", "Start or resume the selected torrent")
        self._t_pause_btn = _small_button("Pause", "Pause the selected torrent")
        self._t_remove_btn = _small_button(
            "Remove", "Forget the selected torrent; downloaded files are kept"
        )
        self._t_clear_btn = _small_button(
            "Clear finished", "Forget all finished torrents; downloaded files are kept"
        )
        for button in (
            self._t_start_btn, self._t_pause_btn,
            self._t_remove_btn, self._t_clear_btn,
        ):
            torrent_header.pack_end(button, False, False, 0)
        self._torrent_section.pack_start(torrent_header, False, False, 0)

        self._torrent_stack = Gtk.Stack()
        self._torrent_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._torrent_stack.set_transition_duration(110)

        torrent_empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        torrent_empty.set_valign(Gtk.Align.CENTER)
        torrent_empty.set_halign(Gtk.Align.CENTER)
        torrent_empty_title = Gtk.Label(label="No torrent downloads.")
        torrent_empty_title.get_style_context().add_class("empty-text")
        torrent_empty_hint = Gtk.Label(
            label="Magnet downloads appear here while a torrent engine runs."
        )
        torrent_empty_hint.get_style_context().add_class("dim")
        torrent_empty_hint.get_style_context().add_class("small")
        torrent_empty.pack_start(torrent_empty_title, False, False, 0)
        torrent_empty.pack_start(torrent_empty_hint, False, False, 0)
        self._torrent_stack.add_named(torrent_empty, "empty")

        self._torrent_list = Gtk.ListBox()
        self._torrent_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._torrent_list.set_activate_on_single_click(True)
        self._torrent_list.connect("row-selected", self._on_torrent_row_selected)
        torrent_scroller = Gtk.ScrolledWindow()
        torrent_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        torrent_scroller.set_size_request(-1, 84)
        torrent_scroller.add(self._torrent_list)
        self._torrent_stack.add_named(torrent_scroller, "list")

        self._torrent_section.pack_start(self._torrent_stack, True, True, 0)
        self._torrent_stack.set_visible_child_name("empty")
        self.pack_start(self._torrent_section, False, False, 0)

        self._pause_btn.connect("clicked", self._on_pause)
        self._cancel_btn.connect("clicked", self._on_cancel)
        self._retry_btn.connect("clicked", self._on_retry)
        self._remove_btn.connect("clicked", self._on_remove)
        self._clear_btn.connect("clicked", self._on_clear)
        self._collapse_btn.connect("clicked", lambda *_: owner.set_downloads_visible(False))
        self._t_start_btn.connect("clicked", self._on_torrent_start)
        self._t_pause_btn.connect("clicked", self._on_torrent_pause)
        self._t_remove_btn.connect("clicked", self._on_torrent_remove)
        self._t_clear_btn.connect("clicked", self._on_torrent_clear)
        self._update_actions(None)
        self._update_torrent_actions(None)
        self.update_engine_hint()

    # -- public -------------------------------------------------------------

    def select_task(self, task_id: str) -> None:
        self._selected_id = task_id
        row = self._rows.get(task_id)
        if row is not None:
            self._list.select_row(row)

    def select_torrent(self, task_id: str) -> None:
        self._selected_torrent_id = task_id
        row = self._torrent_rows.get(task_id)
        if row is not None:
            self._torrent_list.select_row(row)

    def update_engine_hint(self) -> None:
        """Show which torrent engine is usable, without claiming anything else."""
        info = self._torrents.engine_status()
        if info.available:
            self._engine_hint.set_text(info.summary)
            self._engine_hint.set_tooltip_text(
                f"Using {info.path}" if info.path else "Built into the application"
            )
        else:
            self._engine_hint.set_text("No torrent engine")
            self._engine_hint.set_tooltip_text(info.reason or info.summary)

    def update_torrents(self, tasks: Sequence[TorrentTask]) -> None:
        self._updating_torrents = True
        seen: set[str] = set()

        for task in tasks:
            seen.add(task.task_id)
            row = self._torrent_rows.get(task.task_id)
            if row is None:
                row = TorrentRow(task)
                self._torrent_rows[task.task_id] = row
                self._torrent_list.add(row)
                row.show_all()
            else:
                row.update(task)

        for task_id in list(self._torrent_rows):
            if task_id not in seen:
                row = self._torrent_rows.pop(task_id)
                self._torrent_list.remove(row)
                if self._selected_torrent_id == task_id:
                    self._selected_torrent_id = None

        if tasks:
            self._torrent_stack.set_visible_child_name("list")
            active = sum(1 for task in tasks if not task.finished)
            if active:
                self._torrent_count.set_text(
                    f"{active} active · {len(tasks)} "
                    f"{_plural(len(tasks), 'torrent')}"
                )
            else:
                self._torrent_count.set_text(
                    f"{len(tasks)} {_plural(len(tasks), 'torrent')}"
                )
        else:
            self._torrent_stack.set_visible_child_name("empty")
            self._torrent_count.set_text("")

        selected_row = self._torrent_list.get_selected_row()
        if self._selected_torrent_id and self._selected_torrent_id in self._torrent_rows:
            if selected_row is None or selected_row.task_id != self._selected_torrent_id:
                self._torrent_list.select_row(
                    self._torrent_rows[self._selected_torrent_id]
                )
        elif selected_row is not None and selected_row.task_id not in self._torrent_rows:
            self._torrent_list.unselect_all()

        self._updating_torrents = False
        self._update_torrent_actions(self._selected_torrent())

    def update(self, snapshots: Sequence[TaskSnapshot]) -> None:
        self._updating = True
        titles = self._owner.game_title
        seen: set[str] = set()

        for snapshot in snapshots:
            seen.add(snapshot.id)
            row = self._rows.get(snapshot.id)
            if row is None:
                row = TaskRow(snapshot, titles(snapshot.identifier))
                self._rows[snapshot.id] = row
                self._list.add(row)
                row.show_all()
            else:
                row.update(snapshot, titles(snapshot.identifier))

            previous = self._last_status.get(snapshot.id)
            if previous is not None and previous != snapshot.status:
                if snapshot.status == DownloadStatus.COMPLETED:
                    self._owner.on_task_completed(snapshot)
                elif snapshot.status == DownloadStatus.FAILED:
                    self._owner.on_task_failed(snapshot)
            self._last_status[snapshot.id] = snapshot.status

        for task_id in list(self._rows):
            if task_id not in seen:
                row = self._rows.pop(task_id)
                self._list.remove(row)
                self._last_status.pop(task_id, None)
                if self._selected_id == task_id:
                    self._selected_id = None

        if snapshots:
            self._stack.set_visible_child_name("list")
            active = sum(1 for s in snapshots if not s.status.terminal)
            if active:
                self._count.set_text(
                    f"{active} active · {len(snapshots)} {_plural(len(snapshots), 'task')}"
                )
            else:
                self._count.set_text(f"{len(snapshots)} {_plural(len(snapshots), 'task')}")
        else:
            self._stack.set_visible_child_name("empty")
            self._count.set_text("")

        selected_row = self._list.get_selected_row()
        if self._selected_id and self._selected_id in self._rows:
            if selected_row is None or selected_row.task_id != self._selected_id:
                self._list.select_row(self._rows[self._selected_id])
        elif selected_row is not None and selected_row.task_id not in self._rows:
            self._list.unselect_all()

        self._updating = False
        self._update_actions(self._selected_snapshot())
        self._owner.set_download_badge(len(snapshots))

    # -- internals ----------------------------------------------------------

    def _selected_snapshot(self) -> TaskSnapshot | None:
        if not self._selected_id:
            return None
        return self._manager.get(self._selected_id)

    def _on_row_selected(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        self._selected_id = row.task_id if isinstance(row, TaskRow) else None
        if not self._updating:
            self._update_actions(self._selected_snapshot())

    def _update_actions(self, snapshot: TaskSnapshot | None) -> None:
        status = snapshot.status if snapshot else None
        terminal = status.terminal if status else False

        if status == DownloadStatus.PAUSED:
            self._pause_btn.set_label("Resume")
        else:
            self._pause_btn.set_label("Pause")
        self._pause_btn.set_sensitive(
            status in (DownloadStatus.RUNNING, DownloadStatus.PAUSED)
        )
        self._cancel_btn.set_sensitive(status is not None and not terminal)
        can_retry = bool(snapshot.can_retry) if snapshot else False
        self._retry_btn.set_sensitive(can_retry)
        if snapshot and status in (DownloadStatus.FAILED, DownloadStatus.CANCELLED) \
                and not can_retry:
            self._retry_btn.set_tooltip_text(
                f"Retry limit reached ({snapshot.attempts} of "
                f"{snapshot.retry_limit} attempts used)"
            )
        else:
            self._retry_btn.set_tooltip_text("Put this failed download back in the queue")
        self._remove_btn.set_sensitive(bool(terminal))

        has_terminal = any(
            task.status.terminal for task in self._manager.snapshots()
        )
        self._clear_btn.set_sensitive(has_terminal)

    def _on_pause(self, _button: Gtk.Button) -> None:
        snapshot = self._selected_snapshot()
        if snapshot is None:
            return
        if snapshot.status == DownloadStatus.PAUSED:
            self._manager.resume(snapshot.id)
        elif snapshot.status == DownloadStatus.RUNNING:
            self._manager.pause(snapshot.id)

    def _on_cancel(self, _button: Gtk.Button) -> None:
        snapshot = self._selected_snapshot()
        if snapshot is not None:
            self._manager.cancel(snapshot.id)

    def _on_retry(self, _button: Gtk.Button) -> None:
        snapshot = self._selected_snapshot()
        if snapshot is not None:
            self._manager.retry(snapshot.id)

    def _on_remove(self, _button: Gtk.Button) -> None:
        snapshot = self._selected_snapshot()
        if snapshot is not None and snapshot.status.terminal:
            self._manager.remove(snapshot.id)

    def _on_clear(self, _button: Gtk.Button) -> None:
        removed = self._manager.clear_finished()
        if removed:
            self._owner.toast(f"Cleared {removed} finished {_plural(removed, 'task')}.")

    # -- torrents -----------------------------------------------------------

    def _selected_torrent(self) -> TorrentTask | None:
        if not self._selected_torrent_id:
            return None
        return self._torrents.get(self._selected_torrent_id)

    def _on_torrent_row_selected(
        self, _list: Gtk.ListBox, row: Gtk.ListBoxRow | None
    ) -> None:
        self._selected_torrent_id = (
            row.task_id if isinstance(row, TorrentRow) else None
        )
        if not self._updating_torrents:
            self._update_torrent_actions(self._selected_torrent())

    def _update_torrent_actions(self, task: TorrentTask | None) -> None:
        status = task.status if task else ""
        self._t_start_btn.set_label("Resume" if status == "paused" else "Start")
        self._t_start_btn.set_sensitive(status in ("queued", "paused", "failed"))
        self._t_pause_btn.set_sensitive(status == "downloading")
        self._t_remove_btn.set_sensitive(task is not None)
        self._t_clear_btn.set_sensitive(
            any(item.finished for item in self._torrents.tasks())
        )

    def _on_torrent_start(self, _button: Gtk.Button) -> None:
        task = self._selected_torrent()
        if task is None:
            return
        try:
            if task.status == "paused":
                self._torrents.resume(task.task_id)
            else:
                self._torrents.start(task.task_id)
        except TorrentError as exc:
            self._owner.toast(_error_text(exc), error=True)
        self._owner.torrents_changed()

    def _on_torrent_pause(self, _button: Gtk.Button) -> None:
        task = self._selected_torrent()
        if task is None:
            return
        try:
            self._torrents.pause(task.task_id)
        except TorrentError as exc:
            self._owner.toast(_error_text(exc), error=True)
        self._owner.torrents_changed()

    def _on_torrent_remove(self, _button: Gtk.Button) -> None:
        task = self._selected_torrent()
        if task is None:
            return
        confirm = Gtk.MessageDialog(
            transient_for=self._owner,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.CANCEL,
            text="Remove this torrent from the queue?",
        )
        confirm.add_button("Remove", Gtk.ResponseType.OK)
        confirm.format_secondary_text(
            "The queue entry is forgotten. Files already downloaded are kept; "
            "nothing on disk is deleted."
        )
        response = confirm.run()
        confirm.destroy()
        if response != Gtk.ResponseType.OK:
            return
        try:
            self._torrents.remove(task.task_id)
        except TorrentError as exc:
            self._owner.toast(_error_text(exc), error=True)
        self._selected_torrent_id = None
        self._owner.torrents_changed()

    def _on_torrent_clear(self, _button: Gtk.Button) -> None:
        removed = self._torrents.clear_finished()
        if removed:
            self._owner.toast(
                f"Cleared {removed} finished {_plural(removed, 'torrent')}. "
                "Downloaded files were kept."
            )
        self._owner.torrents_changed()


# -- library ----------------------------------------------------------------

COVER_WIDTH = 180
COVER_HEIGHT = 240
_COVER_MAX_WIDTH = 420
_COVER_MAX_HEIGHT = 560
_COVER_CACHE_LIMIT = 72
_IMAGE_CONCURRENCY = 4
_IMAGE_TIMEOUT = 12.0
_MAX_SCREENSHOTS = 6

INTEGRITY_TEXT = {
    "verified_sha256": "SHA-256 verified",
    "verified_legacy": "Verified with SHA-1/MD5 (no SHA-256 published)",
    "mismatch": "Checksum mismatch",
    "unavailable": "No published checksum",
    "error": "Could not read file",
}

STATUS_TEXT = {
    "downloaded": "Downloaded",
    "installed": "Installed",
    "incomplete": "Incomplete",
    "failed": "Failed",
    "missing": "Missing",
}

STATUS_FILTERS = (
    ("all", "All statuses"),
    ("downloaded", "Downloaded"),
    ("installed", "Installed"),
    ("incomplete", "Incomplete"),
    ("missing", "Missing"),
)

COMPAT_FILTERS = (
    ("all", "All platforms"),
    ("Native Linux", "Native Linux"),
    ("Windows", "Windows"),
    ("DOS", "DOS"),
    ("Java", "Java"),
    ("Source only", "Source only"),
    ("Unknown", "Unknown"),
)

#: Readable names, in the order the menu shows them.  The keys are stored in
#: the settings file and shared with the Tkinter frontend, so they never change.
LIBRARY_SORTS = (
    ("title", "Title"),
    ("recent", "Recently seen"),
    ("added", "Recently added"),
    ("played", "Recently played"),
    ("year", "Release year"),
    ("size", "Installed size"),
    ("developer", "Developer"),
    ("genre", "Genre"),
    ("platform", "Platform"),
)

#: Archive list sort modes.  Archive.org records carry a creator and subject
#: tags but no genre field; those tags are shown as "Genre", as in the Library.
ARCHIVE_SORTS = (
    ("title", "Title"),
    ("year", "Year"),
    ("size", "Size"),
    ("downloads", "Downloads"),
    ("downloadable", "Download"),
    ("creator", "Creator"),
    ("genre", "Genre"),
)

_FORMAT_FILTERS = (
    ("all", "All formats"),
    ("zip", "Zip archive"),
    ("7zip", "7-Zip archive"),
    ("rar", "RAR archive"),
    ("tar", "Tar archive"),
    ("disc", "Disc image"),
    ("package", "Package / AppImage"),
    ("shell", "Shell installer"),
)

_FORMAT_SUFFIXES = {
    "zip": (".zip",),
    "7zip": (".7z",),
    "rar": (".rar",),
    "tar": (".tar", ".tgz", ".tar.gz", ".tar.xz", ".tar.bz2", ".tbz2", ".txz"),
    "disc": (".iso", ".img", ".cue", ".bin", ".mdf"),
    "package": (".deb", ".rpm", ".appimage", ".flatpak", ".snap"),
    "shell": (".sh",),
}

_YEAR_FILTERS = (
    ("all", "All years"),
    ("pre1990", "Before 1990"),
    ("1990s", "1990s"),
    ("2000s", "2000s"),
    ("2010s", "2010s"),
    ("2020s", "2020s and later"),
)

_LICENSE_FILTERS = (
    ("all", "All licenses"),
    ("publicdomain", "Public domain"),
    ("cc", "Creative Commons"),
    ("other", "Other license"),
    ("none", "No license listed"),
)


def _license_group(url: str) -> str:
    text = (url or "").lower()
    if not text:
        return "none"
    if "publicdomain" in text or "public-domain" in text or "mark" in text:
        return "publicdomain"
    if "creativecommons" in text or "/licenses/" in text:
        return "cc"
    return "other"


def _has_format(item: ArchiveItem | None, bucket: str) -> bool:
    if item is None:
        return False
    suffixes = _FORMAT_SUFFIXES.get(bucket, ())
    for archive_file in item.files:
        name = archive_file.name.lower()
        if any(name.endswith(suffix) for suffix in suffixes):
            return True
    return False


def _year_value(text: str) -> int | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        value = int(raw[:4])
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _sorted_games(games: Sequence[GameSummary], key: str,
                  descending: bool) -> list[GameSummary]:
    """Sort the archive list; records without the sorted value go last."""
    items = list(games)
    if key in ("creator", "genre", "subject"):
        def text_value(game: GameSummary) -> str:
            if key == "creator":
                return " ".join(str(game.creator or "").casefold().split())
            tags = [
                " ".join(str(tag).casefold().split())
                for tag in game.subjects
                if str(tag).strip()
            ]
            return min(tags) if tags else ""

        # Stable passes: title order first, then the chosen text key, then a
        # missing-value pass — so ties and blanks never depend on direction.
        items.sort(key=lambda game: (game.title.casefold(), game.identifier))
        items.sort(key=text_value, reverse=descending)
        items.sort(key=lambda game: 0 if text_value(game) else 1)
        return items
    if key == "downloadable":
        # Usable first; a game whose only links failed a check sorts last so the
        # broken ones are easy to find in one scroll.
        ordered = sorted(
            items,
            key=lambda game: (
                DOWNLOAD_STATE_RANK.get(game.download_state, 1),
                game.title.casefold(),
                game.identifier,
            ),
        )
        return list(reversed(ordered)) if descending else ordered
    if key == "title" or key not in ("year", "size", "downloads"):
        items.sort(key=lambda game: (game.title.casefold(), game.identifier))
        if descending:
            items.reverse()
        return items

    def value(game: GameSummary) -> int | None:
        if key == "year":
            return _year_value(game.year)
        if key == "size":
            return game.item_size if game.item_size else None
        return game.downloads if game.downloads else None

    present: list[tuple[int, GameSummary]] = []
    blanks: list[GameSummary] = []
    for game in items:
        number = value(game)
        if number is None:
            blanks.append(game)
        else:
            present.append((number, game))
    present.sort(
        key=lambda pair: (
            pair[0], pair[1].title.casefold(), pair[1].identifier,
        ),
        reverse=descending,
    )
    blanks.sort(key=lambda game: (game.title.casefold(), game.identifier))
    return [game for _number, game in present] + blanks



def _integrity_label(status: str) -> str:
    return INTEGRITY_TEXT.get(status or "", "Not checked")


def _integrity_class(status: str) -> str:
    if status in ("verified_sha256", "verified_legacy"):
        return "integrity-ok"
    if status in ("mismatch", "error"):
        return "integrity-bad"
    if status == "unavailable":
        return "integrity-warn"
    return "dim"


def _cover_thumbnail(pixbuf: GdkPixbuf.Pixbuf) -> GdkPixbuf.Pixbuf | None:
    """Downscale a cover for the grid, keeping its natural aspect ratio.

    The picture is never cropped and never enlarged; the tile view letterboxes
    whatever shape comes back from disk.
    """
    width, height = pixbuf.get_width(), pixbuf.get_height()
    if width <= 0 or height <= 0:
        return None
    scale = min(_COVER_MAX_WIDTH / width, _COVER_MAX_HEIGHT / height, 1.0)
    if scale >= 1.0:
        return pixbuf
    new_width = max(1, int(round(width * scale)))
    new_height = max(1, int(round(height * scale)))
    return pixbuf.scale_simple(new_width, new_height, GdkPixbuf.InterpType.BILINEAR)


def _rounded_rect(cr, x: float, y: float, width: float, height: float,
                  radius: float) -> None:
    radius = max(0.0, min(radius, width / 2.0, height / 2.0))
    cr.new_sub_path()
    cr.arc(x + width - radius, y + radius, radius, -math.pi / 2, 0)
    cr.arc(x + width - radius, y + height - radius, radius, 0, math.pi / 2)
    cr.arc(x + radius, y + height - radius, radius, math.pi / 2, math.pi)
    cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
    cr.close_path()


def _load_cover_pixbuf(path: str) -> GdkPixbuf.Pixbuf | None:
    """Read and decode one cover file. Runs on a worker thread."""
    try:
        if not os.path.isfile(path):
            return None
        if os.path.getsize(path) > 32 * 1024 * 1024:
            return None
        with open(path, "rb") as handle:
            data = handle.read()
        loader = GdkPixbuf.PixbufLoader()
        try:
            loader.write(data)
            loader.close()
        except GLib.Error:
            return None
        pixbuf = loader.get_pixbuf()
    except (OSError, ValueError, TypeError, GLib.Error):
        return None
    if pixbuf is None:
        return None
    return _cover_thumbnail(pixbuf)


_MONOGRAM_COLORS = (
    (0.34, 0.40, 0.52),
    (0.45, 0.37, 0.30),
    (0.33, 0.44, 0.38),
    (0.47, 0.35, 0.43),
    (0.38, 0.38, 0.46),
    (0.44, 0.42, 0.32),
    (0.36, 0.43, 0.47),
    (0.48, 0.40, 0.35),
)


def _monogram_text(title: str) -> str:
    words = [w for w in re.split(r"[^0-9A-Za-z\u00c0-\u024f]+", title or "") if w]
    if not words:
        return "?"
    if len(words) >= 2:
        return (words[0][0] + words[1][0]).upper()
    word = words[0]
    return word[:2].upper() if len(word) > 1 else word.upper()


def _monogram_color(title: str) -> tuple[float, float, float]:
    index = zlib.crc32((title or "").encode("utf-8")) % len(_MONOGRAM_COLORS)
    return _MONOGRAM_COLORS[index]


def paint_monogram(widget: Gtk.Widget, cr, width: float, height: float,
                   title: str) -> None:
    """Draw a stable letter tile for a game without usable artwork."""
    red, green, blue = _monogram_color(title)
    _rounded_rect(cr, 0.0, 0.0, width, height, 8.0)
    cr.set_source_rgb(red, green, blue)
    cr.fill()
    layout = widget.create_pango_layout(_monogram_text(title))
    size = max(14.0, min(width, height) * 0.36)
    description = Pango.FontDescription()
    description.set_family("Sans")
    description.set_weight(Pango.Weight.BOLD)
    description.set_absolute_size(size * Pango.SCALE)
    layout.set_font_description(description)
    text_width, text_height = layout.get_pixel_size()
    cr.move_to((width - text_width) / 2.0, (height - text_height) / 2.0)
    cr.set_source_rgb(1.0, 1.0, 1.0)
    PangoCairo.show_layout(cr, layout)


class CoverArt(Gtk.DrawingArea):
    """Game artwork filling a rounded tile, with a letter-tile fallback."""

    def __init__(self) -> None:
        super().__init__()
        self.set_hexpand(True)
        self.set_vexpand(True)
        self._pixbuf: GdkPixbuf.Pixbuf | None = None
        self._monogram = ""
        self.connect("draw", self._draw)

    def set_pixbuf(self, pixbuf: GdkPixbuf.Pixbuf | None) -> None:
        self._pixbuf = pixbuf
        self.queue_draw()

    def set_monogram(self, title: str) -> None:
        self._monogram = title or ""
        self.queue_draw()

    def _draw(self, area: Gtk.DrawingArea, cr) -> bool:
        allocation = area.get_allocation()
        if allocation.width <= 0 or allocation.height <= 0:
            return False
        pixbuf = self._pixbuf
        if pixbuf is None:
            if self._monogram:
                paint_monogram(self, cr, allocation.width, allocation.height,
                               self._monogram)
                return False
            return False
        width, height = pixbuf.get_width(), pixbuf.get_height()
        if width <= 0 or height <= 0:
            return False
        scale = max(allocation.width / width, allocation.height / height)
        draw_width = width * scale
        draw_height = height * scale
        x = (allocation.width - draw_width) / 2.0
        y = (allocation.height - draw_height) / 2.0
        _rounded_rect(cr, 0.0, 0.0, allocation.width, allocation.height, 8.0)
        cr.clip()
        cr.save()
        cr.translate(x, y)
        cr.scale(scale, scale)
        Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
        cr.paint()
        cr.restore()
        return False


class LibraryTile(Gtk.FlowBoxChild):
    """One game folder in the cover grid."""

    #: The cover box follows the picture's own aspect ratio, read from the
    #: image header before layout, so long posters get a tall tile.
    MIN_COVER_HEIGHT = 110
    MAX_COVER_HEIGHT = 420
    DEFAULT_ASPECT = 1.5

    #: Cover box used by the compact list layout.
    COMPACT_COVER = (76, 102)

    def __init__(self, view: "LibraryView", entry: LibraryEntry,
                 extra_copies: int = 0, *, compact: bool = False) -> None:
        super().__init__()
        self.entry = entry
        self._view = view
        self._updating = True
        self._compact = bool(compact)
        self._extra_copies = max(0, int(extra_copies))
        self._cover_box = (
            self.COMPACT_COVER if self._compact
            else self._measure_cover(entry.cover_path)
        )
        self.get_style_context().add_class("library-tile")
        # The flow itself fills the viewport, but a tile keeps its own height:
        # with only a few games the rows used to stretch to the window bottom.
        self.set_valign(Gtk.Align.START)

        card = Gtk.Box(
            orientation=(
                Gtk.Orientation.HORIZONTAL if self._compact
                else Gtk.Orientation.VERTICAL
            ),
            spacing=12 if self._compact else 0,
        )
        card.get_style_context().add_class("cover-card")
        if self._compact:
            card.get_style_context().add_class("cover-card-row")
        card.set_hexpand(True)
        self.add(card)

        overlay = Gtk.Overlay()
        overlay.set_hexpand(True)
        self._stack = Gtk.Stack()
        _style_stack(self._stack)
        self._stack.get_style_context().add_class("tile-cover")
        self._stack.set_hexpand(True)
        self._stack.set_size_request(*self._cover_box)
        self._art = CoverArt()
        self._art.set_monogram(entry.title or entry.identifier or "?")
        self._stack.add_named(self._art, "image")

        placeholder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        placeholder.set_halign(Gtk.Align.CENTER)
        placeholder.set_valign(Gtk.Align.CENTER)
        icon = Gtk.Image.new_from_icon_name("applications-games-symbolic", Gtk.IconSize.DIALOG)
        icon.get_style_context().add_class("dim")
        placeholder.pack_start(icon, False, False, 0)
        self._stack.add_named(placeholder, "empty")
        self._stack.set_visible_child_name("image")
        overlay.add(self._stack)

        status = entry.status
        if status in ("incomplete", "missing", "failed"):
            badge_text = STATUS_TEXT.get(status, status.title())
            badge = Gtk.Label(label=badge_text)
            badge.get_style_context().add_class("tile-badge")
            badge.get_style_context().add_class(f"badge-{status}")
            badge.set_halign(Gtk.Align.START)
            badge.set_valign(Gtk.Align.START)
            badge.set_margin_start(7)
            badge.set_margin_top(7)
            overlay.add_overlay(badge)

        if self._extra_copies:
            copies = Gtk.Label(
                label=(
                    f"+{self._extra_copies} copy"
                    if self._extra_copies == 1
                    else f"+{self._extra_copies} copies"
                )
            )
            copies.get_style_context().add_class("tile-badge")
            copies.get_style_context().add_class("badge-copies")
            copies.set_halign(Gtk.Align.START)
            copies.set_valign(Gtk.Align.END)
            copies.set_margin_start(7)
            copies.set_margin_bottom(7)
            copies.set_tooltip_text(
                "The same title is downloaded in more than one folder; "
                "select the game to see the other copies"
            )
            overlay.add_overlay(copies)

        self._star = Gtk.ToggleButton()
        self._star.set_relief(Gtk.ReliefStyle.NONE)
        self._star.set_active(bool(entry.favorite))
        self._star.get_style_context().add_class("favorite-star")
        self._star.set_halign(Gtk.Align.END)
        self._star.set_valign(Gtk.Align.START)
        self._star.set_margin_end(6)
        self._star.set_margin_top(6)
        self._star.set_tooltip_text("Favorite")
        self._star.get_accessible().set_name("Favorite")
        self._star.connect("toggled", self._on_star_toggled)
        if not self._compact:
            overlay.add_overlay(self._star)
        self._sync_star()

        card.pack_start(overlay, False, False, 0)

        text_block = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        text_block.set_size_request(-1, -1 if self._compact else 66)
        text_block.set_valign(
            Gtk.Align.CENTER if self._compact else Gtk.Align.START
        )
        if self._compact:
            text_block.set_hexpand(True)

        title_text = entry.title or entry.identifier or "Untitled game"
        title = Gtk.Label(label=title_text, xalign=0)
        title.set_line_wrap(True)
        title.set_lines(1 if self._compact else 2)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_max_width_chars(80 if self._compact else 30)
        title.set_hexpand(True)
        title.get_style_context().add_class("tile-title")
        text_block.pack_start(title, False, False, 0)

        bits: list[str] = []
        if entry.year:
            bits.append(entry.year)
        if entry.size:
            bits.append(human_size(entry.size))
        elif entry.file_count:
            bits.append(_plural(entry.file_count, "file"))
        if not bits and entry.creator:
            bits.append(entry.creator)
        meta = Gtk.Label(label=" · ".join(bits) or "No details recorded", xalign=0)
        meta.set_ellipsize(Pango.EllipsizeMode.END)
        meta.set_single_line_mode(True)
        meta.set_max_width_chars(80 if self._compact else 30)
        meta.set_hexpand(True)
        meta.set_margin_top(3)
        meta.get_style_context().add_class("tile-meta")
        text_block.pack_start(meta, False, False, 0)

        compat = Gtk.Label(label=entry.compatibility or "Unknown", xalign=0)
        compat.set_ellipsize(Pango.EllipsizeMode.END)
        compat.set_single_line_mode(True)
        compat.set_max_width_chars(80 if self._compact else 30)
        compat.set_hexpand(True)
        compat.set_margin_top(2)
        compat.get_style_context().add_class("tile-compat")
        text_block.pack_start(compat, False, False, 0)

        card.pack_start(text_block, True, True, 0)
        if self._compact:
            # In a row layout the star sits at the end instead of over the cover.
            self._star.set_valign(Gtk.Align.CENTER)
            self._star.set_halign(Gtk.Align.END)
            card.pack_end(self._star, False, False, 0)

        lines = [title_text]
        if entry.creator:
            lines.append(entry.creator)
        lines.append(f"{STATUS_TEXT.get(status, status)} · {entry.compatibility or 'Unknown'}")
        if self._extra_copies:
            lines.append(
                f"{self._extra_copies + 1} copies of this title"
            )
        self.set_tooltip_text("\n".join(lines))
        self.get_accessible().set_name(
            f"{title_text}, {STATUS_TEXT.get(status, status)}, "
            f"{entry.compatibility or 'unknown platform'}"
        )
        self._updating = False
        view.request_cover(entry.cover_path, self._on_cover)

    def _measure_cover(self, path: str) -> tuple[int, int]:
        """Cover box for a picture, using only the image header (no decode)."""
        ratio = None
        if path:
            try:
                _fmt, width, height = GdkPixbuf.Pixbuf.get_file_info(path)
            except (GLib.Error, TypeError, ValueError):
                width = height = 0
            if width and height:
                ratio = height / width
        if ratio is None or ratio <= 0:
            ratio = self.DEFAULT_ASPECT
        width = 140 if _COMPACT_LAYOUT else COVER_WIDTH
        height = int(round(width * ratio))
        height = max(self.MIN_COVER_HEIGHT, min(self.MAX_COVER_HEIGHT, height))
        return width, height

    def _on_cover(self, pixbuf: GdkPixbuf.Pixbuf | None) -> None:
        if pixbuf is None:
            # Keep the generated letter tile; only the picture is missing.
            self._stack.set_visible_child_name("image")
            return
        self._art.set_pixbuf(pixbuf)
        self._stack.set_visible_child_name("image")

    def _on_star_toggled(self, button: Gtk.ToggleButton) -> None:
        if self._updating:
            return
        self._sync_star()
        self._view.set_favorite(self.entry, button.get_active())

    def _sync_star(self) -> None:
        active = bool(self.entry.favorite)
        name = "starred-symbolic" if active else "non-starred-symbolic"
        image = Gtk.Image.new_from_icon_name(name, Gtk.IconSize.BUTTON)
        image.set_halign(Gtk.Align.CENTER)
        self._star.set_image(image)
        tooltip = "Remove from favorites" if active else "Add to favorites"
        self._star.set_tooltip_text(tooltip)
        self._star.get_accessible().set_name(tooltip)

    def sync_star(self) -> None:
        self._updating = True
        self._star.set_active(bool(self.entry.favorite))
        self._updating = False
        self._sync_star()


class LibraryDetail(Gtk.Box):
    """Right-hand panel with facts and actions for one library entry."""

    def __init__(self, view: "LibraryView") -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._view = view
        self._entry: LibraryEntry | None = None
        self._compat_token = 0
        self._updating = False
        self._viewed_key = ""
        self._chosen_file: LocalFile | None = None
        self._local_files: list[LocalFile] = []
        self.get_style_context().add_class("library-detail")
        self.set_size_request(318, -1)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.set_margin_start(16)
        header.set_margin_end(8)
        header.set_margin_top(12)
        header.set_margin_bottom(4)
        header.pack_start(_section_label("Selected game"), False, False, 0)
        header.pack_start(Gtk.Box(), True, True, 0)
        close = _flat_button("window-close-symbolic", "Close the detail panel")
        close.connect("clicked", lambda *_: view.clear_selection())
        header.pack_end(close, False, False, 0)
        self.pack_start(header, False, False, 0)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        if hasattr(scroller, "set_propagate_natural_width"):
            scroller.set_propagate_natural_width(False)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        content.set_margin_start(16)
        content.set_margin_end(16)
        content.set_margin_top(6)
        content.set_margin_bottom(18)
        scroller.add(content)
        self.pack_start(scroller, True, True, 0)

        self._title = Gtk.Label(xalign=0)
        self._title.set_line_wrap(True)
        self._title.set_max_width_chars(60)
        self._title.set_selectable(True)
        self._title.get_style_context().add_class("panel-title")
        content.pack_start(self._title, False, False, 0)

        self._subtitle = Gtk.Label(xalign=0)
        self._subtitle.set_line_wrap(True)
        self._subtitle.set_max_width_chars(60)
        self._subtitle.set_margin_top(4)
        self._subtitle.set_selectable(True)
        self._subtitle.get_style_context().add_class("dim")
        self._subtitle.get_style_context().add_class("small")
        content.pack_start(self._subtitle, False, False, 0)

        grid = Gtk.Grid()
        grid.set_row_spacing(7)
        grid.set_column_spacing(12)
        grid.set_margin_top(15)
        self._facts: dict[str, Gtk.Label] = {}
        for row, name in enumerate(("Status", "Compatibility", "Size", "Files", "Integrity")):
            caption = Gtk.Label(label=name.upper(), xalign=0)
            caption.set_valign(Gtk.Align.START)
            caption.get_style_context().add_class("fact-name")
            value = Gtk.Label(label="—", xalign=0)
            value.set_line_wrap(True)
            value.set_max_width_chars(46)
            value.set_hexpand(True)
            value.set_selectable(True)
            value.get_style_context().add_class("fact-value")
            grid.attach(caption, 0, row, 1, 1)
            grid.attach(value, 1, row, 1, 1)
            self._facts[name] = value
        content.pack_start(grid, False, False, 0)

        self._copies_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._copies_box.set_margin_top(14)
        self._copies_box.set_no_show_all(True)
        self._copies_box.pack_start(
            _section_label("Other copies"), False, False, 0
        )
        self._copies_list = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=3
        )
        self._copies_box.pack_start(self._copies_list, False, False, 0)
        self._copies_note = Gtk.Label(xalign=0)
        self._copies_note.set_line_wrap(True)
        self._copies_note.set_text(
            "The same title also sits in these folders. Each copy is listed "
            "separately in the index; nothing is merged or deleted."
        )
        self._copies_note.get_style_context().add_class("dim")
        self._copies_note.get_style_context().add_class("small")
        self._copies_box.pack_start(self._copies_note, False, False, 0)
        content.pack_start(self._copies_box, False, False, 0)

        self._meta_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self._meta_box.set_margin_top(14)
        self._meta_box.set_no_show_all(True)
        self._meta_box.pack_start(_section_label("About"), False, False, 0)
        self._meta_facts = Gtk.Grid(column_spacing=12, row_spacing=5)
        self._meta_values: dict[str, Gtk.Label] = {}
        for row, name in enumerate(
            ("Developer", "Publisher", "Released", "Version", "Genres", "Platforms", "Source")
        ):
            caption = Gtk.Label(label=name.upper(), xalign=0)
            caption.set_valign(Gtk.Align.START)
            caption.get_style_context().add_class("fact-name")
            value = Gtk.Label(label="—", xalign=0)
            value.set_line_wrap(True)
            value.set_max_width_chars(46)
            value.set_hexpand(True)
            value.set_selectable(True)
            value.get_style_context().add_class("fact-value")
            self._meta_facts.attach(caption, 0, row, 1, 1)
            self._meta_facts.attach(value, 1, row, 1, 1)
            self._meta_values[name] = value
        self._meta_box.pack_start(self._meta_facts, False, False, 0)
        self._source_btn = Gtk.Button(label="Open source page")
        self._source_btn.set_halign(Gtk.Align.START)
        self._source_btn.set_no_show_all(True)
        self._source_btn.get_style_context().add_class("small-button")
        self._source_btn.connect("clicked", self._on_open_source)
        self._meta_box.pack_start(self._source_btn, False, False, 0)
        self._meta_description = Gtk.Label(xalign=0)
        self._meta_description.set_line_wrap(True)
        self._meta_description.set_max_width_chars(60)
        self._meta_description.set_margin_top(8)
        self._meta_description.set_selectable(True)
        self._meta_description.get_style_context().add_class("small")
        self._meta_box.pack_start(self._meta_description, False, False, 0)
        self._meta_note = Gtk.Label(xalign=0)
        self._meta_note.set_line_wrap(True)
        self._meta_note.set_max_width_chars(60)
        self._meta_note.set_margin_top(6)
        self._meta_note.get_style_context().add_class("dim")
        self._meta_note.get_style_context().add_class("small")
        self._meta_box.pack_start(self._meta_note, False, False, 0)
        content.pack_start(self._meta_box, False, False, 0)

        self._magnet_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self._magnet_box.set_margin_top(14)
        self._magnet_box.set_no_show_all(True)
        magnet_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        magnet_header.pack_start(_section_label("Magnet link"), False, False, 0)
        self._magnet_origin = Gtk.Label(label="")
        self._magnet_origin.get_style_context().add_class("tile-badge")
        self._magnet_origin.set_valign(Gtk.Align.CENTER)
        magnet_header.pack_end(self._magnet_origin, False, False, 0)
        self._magnet_box.pack_start(magnet_header, False, False, 0)
        self._magnet_value = Gtk.Label(xalign=0)
        self._magnet_value.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self._magnet_value.set_selectable(True)
        self._magnet_value.set_max_width_chars(52)
        self._magnet_value.get_style_context().add_class("mono")
        self._magnet_value.get_style_context().add_class("small")
        self._magnet_box.pack_start(self._magnet_value, False, False, 0)
        magnet_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        magnet_actions.set_homogeneous(True)
        self._magnet_copy = Gtk.Button(label="Copy")
        self._magnet_copy.set_tooltip_text("Copy the magnet link to the clipboard")
        self._magnet_copy.connect("clicked", self._on_magnet_copy)
        self._magnet_open = Gtk.Button(label="Open…")
        self._magnet_open.set_tooltip_text(
            "Hand the link to the desktop's magnet handler"
        )
        self._magnet_open.connect("clicked", self._on_magnet_open)
        magnet_actions.pack_start(self._magnet_copy, True, True, 0)
        magnet_actions.pack_start(self._magnet_open, True, True, 0)
        self._magnet_box.pack_start(magnet_actions, False, False, 0)
        self._magnet_note = Gtk.Label(xalign=0)
        self._magnet_note.set_line_wrap(True)
        self._magnet_note.set_max_width_chars(60)
        self._magnet_note.set_text(
            "A magnet link is metadata only. It is not checked, and a valid link "
            "does not mean the content is reachable, complete, or licensed."
        )
        self._magnet_note.get_style_context().add_class("dim")
        self._magnet_note.get_style_context().add_class("small")
        self._magnet_box.pack_start(self._magnet_note, False, False, 0)
        content.pack_start(self._magnet_box, False, False, 0)

        self._compat_explanation = Gtk.Label(xalign=0)
        self._compat_explanation.set_line_wrap(True)
        self._compat_explanation.set_max_width_chars(60)
        self._compat_explanation.set_selectable(True)
        self._compat_explanation.set_margin_top(12)
        self._compat_explanation.get_style_context().add_class("dim")
        self._compat_explanation.get_style_context().add_class("small")
        content.pack_start(self._compat_explanation, False, False, 0)

        self._start_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self._start_box.set_margin_top(14)
        self._start_box.set_no_show_all(True)
        self._start_box.pack_start(_section_label("Start with"), False, False, 0)
        self._start_scroll = Gtk.ScrolledWindow()
        self._start_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        # Let the frame take the height of its rows (up to a cap) so a partly
        # visible row can never overlap the note below it.
        self._start_scroll.set_min_content_height(36)
        self._start_scroll.set_max_content_height(180)
        self._start_scroll.set_overlay_scrolling(False)
        self._start_scroll.set_propagate_natural_height(True)
        self._start_scroll.get_style_context().add_class("start-frame")
        self._start_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._start_scroll.add(self._start_list)
        self._start_box.pack_start(self._start_scroll, False, False, 0)
        self._start_note = Gtk.Label(xalign=0)
        self._start_note.set_line_wrap(True)
        self._start_note.set_max_width_chars(60)
        self._start_note.get_style_context().add_class("dim")
        self._start_note.get_style_context().add_class("small")
        self._start_box.pack_start(self._start_note, False, False, 0)
        content.pack_start(self._start_box, False, False, 0)

        self._progress_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self._progress_box.set_margin_top(12)
        self._progress_box.set_no_show_all(True)
        self._progress = Gtk.ProgressBar()
        self._progress.get_style_context().add_class("verify-progress")
        self._status = Gtk.Label(xalign=0)
        self._status.set_line_wrap(True)
        self._status.set_max_width_chars(60)
        self._status.set_selectable(True)
        self._status.get_style_context().add_class("dim")
        self._status.get_style_context().add_class("small")
        self._progress_box.pack_start(self._progress, False, False, 0)
        self._progress_box.pack_start(self._status, False, False, 0)
        content.pack_start(self._progress_box, False, False, 0)

        actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        actions.set_margin_top(17)
        first = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        first.set_homogeneous(True)
        self._open_btn = Gtk.Button(label="Open folder")
        self._open_btn.set_tooltip_text("Open this game folder in the file manager")
        self._open_btn.connect("clicked", self._on_open)
        self._files_btn = Gtk.Button(label="Show files")
        self._files_btn.set_tooltip_text("List the files stored in this folder")
        self._files_btn.connect("clicked", self._on_files)
        first.pack_start(self._open_btn, True, True, 0)
        first.pack_start(self._files_btn, True, True, 0)

        self._edit_btn = Gtk.Button(label="Edit details…")
        self._edit_btn.set_tooltip_text(
            "Edit metadata, notes, and the optional magnet link for this game"
        )
        self._edit_btn.connect("clicked", self._on_edit)

        second = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        second.set_homogeneous(True)
        self._verify_btn = Gtk.Button(label="Verify integrity")
        self._verify_btn.set_tooltip_text(
            "Compare one file with the checksum published on archive.org"
        )
        self._verify_btn.connect("clicked", self._on_verify)
        self._favorite = Gtk.ToggleButton()
        fav_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        self._fav_image = Gtk.Image.new_from_icon_name(
            "non-starred-symbolic", Gtk.IconSize.BUTTON
        )
        fav_box.pack_start(self._fav_image, False, False, 0)
        fav_box.pack_start(Gtk.Label(label="Favorite"), False, False, 0)
        self._favorite.add(fav_box)
        self._favorite.set_tooltip_text("Mark this game as a favorite")
        self._favorite.connect("toggled", self._on_favorite)
        second.pack_start(self._verify_btn, True, True, 0)
        second.pack_start(self._favorite, True, True, 0)

        self._launch_btn = Gtk.Button(label="Review & launch…")
        self._launch_btn.set_tooltip_text(
            "Review the chosen file, then run it after an explicit confirmation"
        )
        self._launch_btn.connect("clicked", self._on_launch)

        third = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        third.set_homogeneous(True)
        self._remove_btn = Gtk.Button(label="Remove from library")
        self._remove_btn.set_tooltip_text(
            "Remove this entry from the library index; files on disk are kept"
        )
        self._remove_btn.get_style_context().add_class("destructive-action")
        self._remove_btn.connect("clicked", self._on_remove)
        third.pack_start(self._remove_btn, True, True, 0)
        actions.pack_start(first, False, False, 0)
        actions.pack_start(self._edit_btn, False, False, 0)
        actions.pack_start(second, False, False, 0)
        actions.pack_start(self._launch_btn, False, False, 0)
        actions.pack_start(third, False, False, 0)
        content.pack_start(actions, False, False, 0)

    # -- public -------------------------------------------------------------

    def show_entry(self, entry: LibraryEntry,
                   copies: Sequence[LibraryEntry] | None = None) -> None:
        if entry.identifier != self._viewed_key:
            self._viewed_key = entry.identifier
            recently_viewed.record(
                recently_viewed.default_path(),
                kind="game",
                key=entry.identifier,
                title=entry.title,
                subtitle=entry.display_subtitle,
            )
        self._entry = entry
        self._show_copies(list(copies or []))
        self._title.set_text(entry.title or entry.identifier)
        self._title.set_tooltip_text(entry.title or entry.identifier)
        creator_bits = [value for value in (entry.creator, entry.year) if value]
        self._subtitle.set_text(" · ".join(creator_bits) or "No creator or year recorded")

        status = entry.status
        self._facts["Status"].set_text(STATUS_TEXT.get(status, status.title()))
        self._facts["Compatibility"].set_text(entry.compatibility or "Unknown")
        self._facts["Size"].set_text(human_size(entry.size) if entry.size else "—")
        self._facts["Files"].set_text(
            f"{entry.file_count} {_plural(entry.file_count, 'file')}"
            if entry.file_count else "No files counted"
        )
        self._set_integrity(entry.integrity)
        self._show_metadata(entry)
        self._show_magnet(entry)

        self._updating = True
        self._favorite.set_active(bool(entry.favorite))
        self._updating = False
        self._sync_favorite()

        self._verify_btn.set_sensitive(True)
        self._progress_box.hide()
        self._status.set_text("")
        self._chosen_file = None
        self._local_files = []
        self._launch_btn.set_sensitive(False)
        self._reset_start_files()
        self._compat_token += 1
        token = self._compat_token
        folder = Path(entry.folder)

        if not entry.is_game:
            self._compat_explanation.set_text(
                "This folder has no game manifest, executable, or archive."
            )
            self._start_box.hide()
            return

        self._compat_explanation.set_text("Checking compatibility…")

        def work():
            compat = analyze_folder(folder)
            try:
                scanned = scan_game_folder(folder)
            except OSError:
                scanned = None
            return compat, scanned

        def success(result: object) -> None:
            compat, scanned = result if isinstance(result, tuple) else (None, None)
            self._on_compat(token, compat)
            self._populate_start_files(token, folder, scanned)

        def failure(_exc: object) -> None:
            self._on_compat(token, None)
            self._populate_start_files(token, folder, None)

        _spawn(work, success, failure)

    def _show_copies(self, copies: Sequence[LibraryEntry]) -> None:
        for child in list(self._copies_list.get_children()):
            self._copies_list.remove(child)
        if not copies:
            self._copies_box.hide()
            return
        for index, copy in enumerate(copies, start=2):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            marker = Gtk.Label(label=f"{index}.")
            marker.get_style_context().add_class("dim")
            marker.get_style_context().add_class("small")
            marker.set_valign(Gtk.Align.START)
            description = copy.folder or copy.identifier
            label = Gtk.Label(label=description, xalign=0)
            label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            label.set_tooltip_text(
                f"{description}\n{human_size(copy.size) if copy.size else 'size unknown'}"
            )
            label.get_style_context().add_class("small")
            row.pack_start(marker, False, False, 0)
            row.pack_start(label, True, True, 0)
            self._copies_list.pack_start(row, False, False, 0)
        self._copies_box.set_no_show_all(False)
        self._copies_box.show_all()
        self._copies_box.set_no_show_all(True)

    def set_favorite_state(self, favorite: bool) -> None:
        self._updating = True
        self._favorite.set_active(bool(favorite))
        self._updating = False
        self._sync_favorite()

    # -- integrity ----------------------------------------------------------

    def _set_integrity(self, status: str) -> None:
        label = self._facts["Integrity"]
        label.set_text(_integrity_label(status))
        context = label.get_style_context()
        for name in ("integrity-ok", "integrity-warn", "integrity-bad", "dim"):
            context.remove_class(name)
        context.add_class(_integrity_class(status))

    def start_verifying(self) -> None:
        self._verify_btn.set_sensitive(False)
        self._progress.set_fraction(0.0)
        self._status.set_text("Reading the file and published checksums…")
        self._progress_box.set_no_show_all(False)
        self._progress_box.show_all()
        self._progress_box.set_no_show_all(True)

    def update_progress(self, done: int, total: int) -> None:
        if total <= 0:
            self._progress.pulse()
            return
        fraction = max(0.0, min(1.0, done / total))
        self._progress.set_fraction(fraction)
        self._status.set_text(f"Verifying… {int(fraction * 100)}%")

    def finish_verifying(self, result: IntegrityResult, filename: str) -> None:
        self._verify_btn.set_sensitive(True)
        self._progress_box.hide()
        self._set_integrity(result.status.value)
        lines: list[str] = []
        if filename:
            lines.append(f"Checked {filename}.")
        if result.message:
            lines.append(result.message)
        self._status.set_text(" ".join(lines))
        self._status.show()
        if self._entry is not None:
            self._entry.integrity = result.status.value

    # -- internals ----------------------------------------------------------

    def _sync_favorite(self) -> None:
        active = self._favorite.get_active()
        icon = "starred-symbolic" if active else "non-starred-symbolic"
        self._fav_image.set_from_icon_name(icon, Gtk.IconSize.BUTTON)
        self._favorite.set_tooltip_text(
            "Remove this game from favorites" if active else "Mark this game as a favorite"
        )

    def _on_compat(self, token: int, compat: object) -> None:
        if token != self._compat_token or self._entry is None:
            return
        if compat is None or not hasattr(compat, "status"):
            self._compat_explanation.set_text(
                "Compatibility could not be checked for this folder."
            )
            return
        self._compat_explanation.set_text(explain_compatibility(compat))

    # -- launch candidate ---------------------------------------------------

    @staticmethod
    def _launch_rank(local_file: LocalFile) -> int:
        kind = (local_file.kind or "").lower()
        if "elf" in kind or "appimage" in kind:
            return 0
        if "shell" in kind or is_shell(local_file.path.name):
            return 1
        return 2

    def _reset_start_files(self) -> None:
        for child in list(self._start_list.get_children()):
            self._start_list.remove(child)
        self._start_note.set_text("")
        self._start_box.hide()

    def _populate_start_files(self, token: int, folder: Path,
                              scanned: object) -> None:
        if token != self._compat_token or self._entry is None:
            return
        self._reset_start_files()
        files = list(getattr(scanned, "files", []) or []) if scanned else []
        self._local_files = files
        if not files:
            self._start_note.set_text("No files were found in this folder.")
            self._show_start_box()
            return

        title = (getattr(self._entry, "title", "") or folder.name or "").strip()
        ranked = [
            (
                launcher_rank(
                    local_file.path.name, kind=local_file.kind, title=title
                ),
                local_file,
            )
            for local_file in files
        ]
        candidates = [item for item in ranked if item[0] is not None]
        if candidates:
            # Plausibility first, then the ordering this list already used
            # (native before script, launchable before not) and finally the
            # path, so equal candidates stay in a stable, familiar order.
            candidates.sort(
                key=lambda item: (
                    item[0],
                    self._launch_rank(item[1]),
                    0 if item[1].launchable else 1,
                    str(item[1].path).casefold(),
                )
            )
            shown = [local_file for _rank, local_file in candidates[:8]]
        else:
            # Nothing looked like an entry point, so offer the launchable files
            # rather than hiding the only way in.
            files.sort(
                key=lambda f: (0 if f.launchable else 1, self._launch_rank(f),
                               str(f.path).casefold())
            )
            shown = files[:80]
        group: Gtk.RadioButton | None = None
        best: LocalFile | None = None
        for local_file in shown:
            try:
                name = str(local_file.path.relative_to(folder))
            except (ValueError, OSError):
                name = local_file.path.name
            radio = Gtk.RadioButton(label=name)
            if group is None:
                group = radio
            else:
                radio.join_group(group)
            radio.set_tooltip_text(
                f"{local_file.kind} · {human_size(local_file.size)}\n{name}"
                if local_file.launchable
                else "Not a recognised executable or script, so it cannot be launched."
            )
            child = radio.get_child()
            if isinstance(child, Gtk.Label):
                child.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
                child.set_max_width_chars(30)
                child.set_xalign(0)
            if local_file.launchable:
                if best is None:
                    best = local_file
                radio.connect(
                    "toggled", self._on_start_toggled, local_file
                )
            else:
                radio.set_sensitive(False)
                if local_file.path.name.casefold().endswith((".exe", ".bat", ".cmd")):
                    radio.set_tooltip_text(
                        f"{local_file.kind} · {human_size(local_file.size)}\n"
                        "A Windows program: run it with Wine or Proton. This "
                        "launcher only starts native executables and scripts."
                    )
                    if isinstance(child, Gtk.Label):
                        child.set_text(f"{name} · needs Wine/Proton")
                elif isinstance(child, Gtk.Label):
                    child.set_text(f"{name} · not launchable")
            self._start_list.pack_start(radio, False, False, 0)
        if best is None:
            self._start_note.set_text(
                "No launcher was found. Only a shell script, AppImage, or ELF "
                "executable can be started from here; a Windows .exe needs "
                "Wine or Proton."
            )
        else:
            note = "The chosen file is reviewed before anything runs."
            if candidates and len(candidates) > len(shown):
                note += (
                    f" {len(candidates) - len(shown)} other executable(s) are "
                    "not listed."
                )
            self._start_note.set_text(note)
        self._chosen_file = best
        self._launch_btn.set_sensitive(best is not None)
        if best is not None:
            self._launch_btn.set_tooltip_text(
                f"Review the warning, then launch {best.path.name}"
            )
        self._show_start_box()

    def _show_start_box(self) -> None:
        self._start_box.set_no_show_all(False)
        self._start_box.show_all()
        self._start_box.set_no_show_all(True)

    def _on_start_toggled(self, button: Gtk.RadioButton,
                          local_file: LocalFile) -> None:
        if not button.get_active() or not local_file.launchable:
            return
        self._chosen_file = local_file
        self._launch_btn.set_sensitive(True)
        self._launch_btn.set_tooltip_text(
            f"Review the warning, then launch {local_file.path.name}"
        )

    # -- metadata and magnet ------------------------------------------------

    def _show_metadata(self, entry: LibraryEntry) -> None:
        values = {
            "Developer": entry.developer,
            "Publisher": entry.publisher,
            "Released": entry.release_date or entry.year,
            "Version": entry.version,
            "Genres": ", ".join(entry.genres),
            "Platforms": ", ".join(entry.platforms),
            "Source": entry.metadata_provider or entry.source_url,
        }
        any_value = False
        for name, value in values.items():
            self._meta_values[name].set_text(value or "—")
            any_value = any_value or bool(value)
        self._meta_values["Source"].set_tooltip_text(entry.source_url or None)
        self._source_btn.set_visible(bool(entry.source_url))
        self._source_btn.set_tooltip_text(entry.source_url or None)
        self._meta_description.set_text(entry.description or "")

        supplied = [
            name for name in metalib.METADATA_FIELDS
            if metalib.provenance_of(entry, name)
        ]
        if supplied:
            providers = sorted({metalib.provenance_of(entry, name) for name in supplied})
            self._meta_note.set_text(
                "Where these fields came from: "
                + ", ".join("you" if p == metalib.USER_PROVIDER else p for p in providers)
            )
        elif any_value:
            self._meta_note.set_text(
                "Metadata read from the game folder or its manifest. Nothing has "
                "been checked against a store page."
            )
        else:
            self._meta_note.set_text(
                "No extra metadata yet. Use “Edit details…” to add some; catalog "
                "refreshes never overwrite fields you typed yourself."
            )
        self._meta_box.set_no_show_all(False)
        self._meta_box.show_all()
        self._meta_box.set_no_show_all(True)
        # ``show_all`` would reveal optional rows, so set visibility last.
        self._meta_description.set_visible(bool(entry.description))
        self._source_btn.set_visible(bool(entry.source_url))

    def _show_magnet(self, entry: LibraryEntry) -> None:
        link = magnetlib.magnet_from_entry(
            entry.magnet, entry.infohash, entry.magnet_origin
        )
        self._magnet_uri = link.uri if link else ""
        if link is None:
            self._magnet_box.hide()
            return
        self._magnet_value.set_text(link.uri)
        self._magnet_value.set_tooltip_text(link.uri)
        self._magnet_origin.set_text(
            "built from infohash" if link.constructed else "from a link you supplied"
        )
        self._magnet_copy.set_sensitive(True)
        self._magnet_open.set_sensitive(True)
        self._magnet_box.set_no_show_all(False)
        self._magnet_box.show_all()
        self._magnet_box.set_no_show_all(True)

    def _on_open_source(self, _button: Gtk.Button) -> None:
        if self._entry is None or not self._entry.source_url:
            return
        if not platform_support.open_url(self._entry.source_url):
            self._view.owner.toast(
                "No browser answered for this source page.", error=True
            )

    def _on_edit(self, _button: Gtk.Button) -> None:
        if self._entry is None:
            return
        dialog = MetadataDialog(self._view, self._entry)
        try:
            while True:
                response = dialog.run()
                if response != Gtk.ResponseType.OK:
                    break
                error = dialog.try_save()
                if error is None:
                    break
                dialog.show_error(error)
        finally:
            dialog.destroy()

    def _on_magnet_copy(self, _button: Gtk.Button) -> None:
        uri = getattr(self, "_magnet_uri", "")
        if not uri:
            return
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(uri, -1)
        clipboard.store()
        self._view.owner.toast("Magnet link copied to the clipboard.")

    def _on_magnet_open(self, _button: Gtk.Button) -> None:
        if self._entry is None:
            return
        self._view.open_magnet(self._entry)

    def _on_remove(self, _button: Gtk.Button) -> None:
        if self._entry is None:
            return
        entry = self._entry
        dialog = Gtk.MessageDialog(
            transient_for=self.get_toplevel(),
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.CANCEL,
            text="Remove this entry from the library?",
        )
        dialog.add_button("Remove entry", Gtk.ResponseType.OK)
        dialog.format_secondary_text(
            "Files stay on disk. Only the library index entry is removed; "
            "nothing is deleted."
        )
        dialog.set_default_response(Gtk.ResponseType.CANCEL)
        ok = dialog.get_widget_for_response(Gtk.ResponseType.OK)
        if ok is not None:
            ok.get_style_context().add_class("destructive-action")
        response = dialog.run()
        dialog.destroy()
        if response == Gtk.ResponseType.OK:
            self._view.remove_entry(entry)

    def _on_favorite(self, button: Gtk.ToggleButton) -> None:
        if self._updating or self._entry is None:
            return
        self._sync_favorite()
        self._view.set_favorite(self._entry, button.get_active())

    def _on_open(self, _button: Gtk.Button) -> None:
        if self._entry is not None:
            self._view.owner.open_folder(Path(self._entry.folder))

    def _on_files(self, _button: Gtk.Button) -> None:
        if self._entry is not None:
            self._view.show_files(self._entry)

    def _on_verify(self, _button: Gtk.Button) -> None:
        if self._entry is not None:
            self._view.verify_entry(self._entry, self)

    def _on_launch(self, _button: Gtk.Button) -> None:
        if self._entry is not None:
            self._view.launch_entry(self._entry, self._chosen_file)


class MetadataDialog(Gtk.Dialog):
    """Edit optional metadata, notes, and the magnet link for one game."""

    #: (field, label) for single-line metadata fields.
    TEXT_FIELDS = (
        ("title", "Title"),
        ("creator", "Creator"),
        ("year", "Year"),
        ("developer", "Developer"),
        ("publisher", "Publisher"),
        ("release_date", "Release date"),
        ("version", "Version"),
        ("genres", "Genres"),
        ("platforms", "Platforms"),
        ("source_url", "Source page"),
    )

    def __init__(self, view: "LibraryView", entry: LibraryEntry) -> None:
        super().__init__(
            title=f"Edit details — {entry.title or entry.identifier}",
            transient_for=view.owner,
            modal=True,
        )
        self._view = view
        self._entry = entry
        self.set_default_size(582, 640)
        self.set_resizable(True)
        self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        save = self.add_button("Save", Gtk.ResponseType.OK)
        save.get_style_context().add_class("suggested-action")

        content = self.get_content_area()
        content.set_spacing(0)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=11)
        box.set_margin_start(20)
        box.set_margin_end(20)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        scroller.add(box)
        content.pack_start(scroller, True, True, 0)

        grid = Gtk.Grid(column_spacing=10, row_spacing=7)
        self._entries: dict[str, Gtk.Entry] = {}
        for row, (name, label) in enumerate(self.TEXT_FIELDS):
            caption = Gtk.Label(label=label, xalign=0)
            caption.get_style_context().add_class("fact-name")
            widget = Gtk.Entry()
            value = getattr(entry, name, "")
            if name in metalib.LIST_FIELDS:
                widget.set_text(", ".join(value or ()))
            else:
                widget.set_text(str(value or ""))
            widget.set_hexpand(True)
            widget.set_width_chars(30)
            if name != "title":
                widget.set_placeholder_text("optional")
            hint = Gtk.Label(label=self._hint(name))
            hint.get_style_context().add_class("dim")
            hint.get_style_context().add_class("small")
            hint.set_size_request(78, -1)
            grid.attach(caption, 0, row, 1, 1)
            grid.attach(widget, 1, row, 1, 1)
            grid.attach(hint, 2, row, 1, 1)
            self._entries[name] = widget
        if "genres" in self._entries:
            self._entries["genres"].set_tooltip_text("Comma-separated, e.g. RPG, Adventure")
        if "release_date" in self._entries:
            self._entries["release_date"].set_tooltip_text("YYYY, YYYY-MM, or YYYY-MM-DD")
        if "source_url" in self._entries:
            self._entries["source_url"].set_tooltip_text("An http(s) page; other schemes are refused")
        box.pack_start(grid, False, False, 0)

        desc_label = Gtk.Label(label="Description", xalign=0)
        desc_label.get_style_context().add_class("fact-name")
        box.pack_start(desc_label, False, False, 0)
        desc_scroller = Gtk.ScrolledWindow()
        desc_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        desc_scroller.set_min_content_height(96)
        desc_scroller.get_style_context().add_class("start-frame")
        self._description = Gtk.TextView()
        self._description.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._description.set_left_margin(8)
        self._description.set_right_margin(8)
        self._description.set_top_margin(6)
        self._description.set_bottom_margin(6)
        self._description.get_buffer().set_text(entry.description or "")
        desc_scroller.add(self._description)
        box.pack_start(desc_scroller, False, False, 0)

        notes_label = Gtk.Label(label="Your notes", xalign=0)
        notes_label.get_style_context().add_class("fact-name")
        self._notes = Gtk.Entry()
        self._notes.set_text(entry.notes or "")
        self._notes.set_placeholder_text("Only you see this")
        box.pack_start(notes_label, False, False, 0)
        box.pack_start(self._notes, False, False, 0)

        magnet_label = Gtk.Label(label="Magnet link (optional)", xalign=0)
        magnet_label.get_style_context().add_class("fact-name")
        self._magnet = Gtk.Entry()
        self._magnet.set_text(entry.magnet or "")
        self._magnet.set_placeholder_text("magnet:?xt=urn:btih:… or leave empty")
        self._magnet.set_width_chars(40)
        box.pack_start(magnet_label, False, False, 0)
        box.pack_start(self._magnet, False, False, 0)

        infohash_label = Gtk.Label(label="Infohash only (optional)", xalign=0)
        infohash_label.get_style_context().add_class("fact-name")
        self._infohash = Gtk.Entry()
        self._infohash.set_text(entry.infohash or "")
        self._infohash.set_placeholder_text("40 hex or 32 base32 characters")
        box.pack_start(infohash_label, False, False, 0)
        box.pack_start(self._infohash, False, False, 0)

        magnet_note = Gtk.Label(
            label=(
                "An infohash is only turned into a magnet link when it is valid; "
                "the result is labelled “built from infohash”. Nothing is "
                "downloaded or fetched here."
            )
        )
        magnet_note.set_xalign(0)
        magnet_note.set_line_wrap(True)
        magnet_note.set_max_width_chars(64)
        magnet_note.get_style_context().add_class("dim")
        magnet_note.get_style_context().add_class("small")
        box.pack_start(magnet_note, False, False, 0)

        info = Gtk.Label(
            label=(
                "Fields you save here are marked as yours and are never "
                "overwritten when catalog metadata is merged."
            )
        )
        info.set_xalign(0)
        info.set_line_wrap(True)
        info.set_max_width_chars(64)
        info.get_style_context().add_class("dim")
        info.get_style_context().add_class("small")
        box.pack_start(info, False, False, 0)

        self._error = Gtk.Label(label="")
        self._error.set_xalign(0)
        self._error.set_line_wrap(True)
        self._error.set_max_width_chars(64)
        self._error.get_style_context().add_class("error-text")
        self._error.set_no_show_all(True)
        box.pack_start(self._error, False, False, 0)

        content.show_all()

    def _hint(self, name: str) -> str:
        source = metalib.provenance_of(self._entry, name)
        if source == metalib.USER_PROVIDER:
            return "you"
        return source or "not set"

    def show_error(self, message: str) -> None:
        self._error.set_text(message)
        self._error.set_no_show_all(False)
        self._error.show()

    def try_save(self) -> str | None:
        """Validate and store the form. Returns an error message, or None."""
        values: dict[str, object] = {}
        for name, widget in self._entries.items():
            try:
                values[name] = metalib.clean_field(name, widget.get_text())
            except metalib.MetadataError as exc:
                return f"{name}: {exc}"
        buffer = self._description.get_buffer()
        values["description"] = metalib.clean_description(
            buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
        )
        if not str(values.get("title") or "").strip():
            return "A title is required."
        magnet_text = self._magnet.get_text().strip()
        infohash_text = self._infohash.get_text().strip()
        try:
            self._view.save_details(
                self._entry,
                values,
                notes=self._notes.get_text(),
                magnet=magnet_text,
                infohash=infohash_text,
            )
        except (metalib.MetadataError, magnetlib.MagnetError, KeyError) as exc:
            return _error_text(exc)
        except OSError as exc:
            return f"The details were applied but could not be saved: {exc}"
        return None


class LibraryView(Gtk.Box):
    """Cover grid, filters, and detail panel for downloaded games."""

    def __init__(self, owner: "MainWindow") -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.owner = owner
        self._store: LibraryStore = owner.library_store
        self._lock = owner.library_lock
        self._settings = owner.settings
        self._tiles: dict[str, LibraryTile] = {}
        self._entries: list[LibraryEntry] = []
        self._visible: list[LibraryEntry] = []
        self._copies: dict[str, list[LibraryEntry]] = {}
        self._view_mode = self._settings.library_view
        self._syncing_genres = False
        self._restored_selection = False
        self._filter_source = 0
        self._rescan_source = 0
        self._scanning = False
        self._verify_token = 0
        self._verify_busy = False
        self._cover_cache: collections.OrderedDict[str, GdkPixbuf.Pixbuf] = collections.OrderedDict()
        self._cover_misses: set[str] = set()
        self._cover_pending: set[str] = set()
        self._cover_waiters: dict[str, list[Callable[[GdkPixbuf.Pixbuf | None], None]]] = {}

        self.get_style_context().add_class("library-view")
        self.pack_start(self._build_toolbar(), False, False, 0)
        self.pack_start(self._build_content(), True, True, 0)
        self._apply_flow_mode()
        self._sync_view_toggle()
        self.refresh()
        GLib.idle_add(self._release_split)

    # -- construction -------------------------------------------------------

    @staticmethod
    def _combo(options: Sequence[tuple[str, str]], tooltip: str,
               accessible: str) -> Gtk.ComboBoxText:
        combo = Gtk.ComboBoxText()
        for identifier, label in options:
            combo.append(identifier, label)
        combo.set_active(0)
        combo.set_tooltip_text(tooltip)
        combo.get_accessible().set_name(accessible)
        combo.set_valign(Gtk.Align.CENTER)
        return combo

    def _build_toolbar(self) -> Gtk.Widget:
        toolbar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        toolbar.get_style_context().add_class("library-toolbar")

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        self._search = Gtk.SearchEntry()
        self._search.set_placeholder_text("Search library")
        self._search.set_width_chars(16)
        self._search.set_hexpand(True)
        self._search.set_tooltip_text("Search title, identifier, or creator (Ctrl+F)")
        self._search.get_accessible().set_name("Search the local library")
        self._search.connect("changed", self._on_search_changed)
        self._search.connect("stop-search", lambda *_: self._search.set_text(""))
        row.pack_start(self._search, True, True, 0)

        self._view_toggle = Gtk.ToggleButton()
        self._view_toggle.set_active(self._view_mode == "list")
        self._view_toggle.set_tooltip_text(
            "Switch between the cover grid and a compact list"
        )
        self._view_toggle.get_accessible().set_name("Compact list view")
        self._view_toggle.connect("toggled", self._on_view_toggled)
        row.pack_start(self._view_toggle, False, False, 0)

        self._genre_combo = _Picker(
            (("all", "All genres"),),
            tooltip="Filter by genre",
            accessible="Filter by genre",
            searchable=True,
        )
        self._genre_combo.connect("changed", self._on_filter_changed)

        self._status_combo = self._combo(
            STATUS_FILTERS, "Filter by download status", "Filter by status"
        )
        self._status_combo.connect("changed", self._on_filter_changed)
        self._compat_combo = self._combo(
            COMPAT_FILTERS, "Filter by likely platform", "Filter by platform"
        )
        self._compat_combo.connect("changed", self._on_filter_changed)

        self._favorites_only = Gtk.ToggleButton(label="Favorites")
        self._favorites_only.set_tooltip_text("Show favorites only")
        self._favorites_only.get_accessible().set_name("Show favorites only")
        self._favorites_only.connect("toggled", self._on_filter_changed)

        self._show_all_folders = Gtk.ToggleButton(label="Show non-game folders")
        self._show_all_folders.set_active(not self._settings.library_games_only)
        self._show_all_folders.set_tooltip_text(
            "Also show folders that do not look like games"
        )
        self._show_all_folders.connect("toggled", self._on_library_filter_toggled)

        self._show_missing = Gtk.ToggleButton(label="Show missing folders")
        self._show_missing.set_active(self._settings.library_show_missing)
        self._show_missing.set_tooltip_text(
            "Keep entries whose folder is no longer on disk"
        )
        self._show_missing.connect("toggled", self._on_library_filter_toggled)

        self._group_copies = Gtk.ToggleButton(label="Group copies")
        self._group_copies.set_active(self._settings.library_group_copies)
        self._group_copies.set_tooltip_text(
            "Show one tile per title when the same game is downloaded twice"
        )
        self._group_copies.connect("toggled", self._on_library_filter_toggled)

        self._hide_adult = Gtk.ToggleButton(label="Hide adult titles")
        self._hide_adult.set_active(self._settings.library_hide_adult)
        self._hide_adult.set_tooltip_text(
            "Hide entries whose genres, notes or title carry an NSFW/adult/18+ "
            "tag. This reads metadata only; it is not a content rating."
        )
        self._hide_adult.connect("toggled", self._on_library_filter_toggled)

        self._sort_combo = _Picker(
            LIBRARY_SORTS, tooltip="Sort the library by…", accessible="Sort order"
        )
        self._sort_combo.set_active_id(self._settings.library_sort)
        self._sort_combo.connect("changed", self._on_sort_changed)

        self._sort_desc = Gtk.Button()
        self._sort_desc.get_style_context().add_class("sort-direction")
        self._sort_desc.set_valign(Gtk.Align.CENTER)
        self._sort_desc.connect("clicked", self._on_sort_direction_clicked)
        row.pack_start(
            _sort_cluster(self._sort_combo, self._sort_desc), False, False, 0
        )
        self._sort_descending = self._saved_sort_direction()
        self._sync_sort_direction()

        filters = Gtk.MenuButton()
        filters.set_tooltip_text("Filter the library")
        filters.get_accessible().set_name("Filter the library")
        filters.set_valign(Gtk.Align.CENTER)
        filters.set_label("Filters")
        popover = Gtk.Popover()
        popover.set_position(Gtk.PositionType.BOTTOM)
        pop_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9)
        pop_body.set_margin_start(14)
        pop_body.set_margin_end(14)
        pop_body.set_margin_top(12)
        pop_body.set_margin_bottom(14)
        heading = Gtk.Label(label="Filters", xalign=0)
        heading.get_style_context().add_class("popover-title")
        pop_body.pack_start(heading, False, False, 0)
        for caption, widget in (
            ("Status", self._status_combo),
            ("Platform", self._compat_combo),
            ("Genre", self._genre_combo),
        ):
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            caption_label = Gtk.Label(label=caption, xalign=0)
            caption_label.get_style_context().add_class("field-label")
            caption_label.set_size_request(66, -1)
            line.pack_start(caption_label, False, False, 0)
            line.pack_start(widget, True, True, 0)
            pop_body.pack_start(line, False, False, 0)
        pop_body.pack_start(
            Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0
        )
        for toggle in (
            self._favorites_only,
            self._show_all_folders,
            self._show_missing,
            self._group_copies,
            self._hide_adult,
        ):
            toggle.set_halign(Gtk.Align.START)
            pop_body.pack_start(toggle, False, False, 0)
        clear = Gtk.Button(label="Clear filters")
        clear.get_style_context().add_class("small-button")
        clear.set_halign(Gtk.Align.START)
        clear.connect("clicked", lambda *_: self._reset_filters())
        pop_body.pack_start(clear, False, False, 0)
        pop_body.show_all()
        popover.add(pop_body)
        filters.set_popover(popover)
        self._library_filter_btn = filters
        row.pack_start(filters, False, False, 0)

        self._rescan_btn = _flat_button(
            "view-refresh-symbolic",
            "Scan the download folder for game folders (F5)",
        )
        self._rescan_btn.connect("clicked", lambda *_: self.rescan())
        row.pack_start(self._rescan_btn, False, False, 0)

        # Bulk selection lives in gtk_bulk; the toolbar only hosts its toggle
        # and the bar it reveals, so the rules stay in one place.
        from .gtk_bulk import BulkActions

        self.bulk = BulkActions(self)
        row.pack_start(self.bulk.toggle, False, False, 0)

        more = Gtk.MenuButton()
        more.set_tooltip_text("More library actions")
        more.set_valign(Gtk.Align.CENTER)
        more.set_image(
            Gtk.Image.new_from_icon_name("view-more-symbolic", _ICON)
        )
        menu = Gtk.Menu()
        for label, callback in (
            ("Show duplicate files…", self.show_duplicates),
            ("Export library…", self.export_library),
            ("Import library…", self.import_library),
        ):
            item = Gtk.MenuItem(label=label)
            item.connect("activate", lambda _item, cb=callback: cb())
            menu.append(item)
        menu.append(Gtk.SeparatorMenuItem())
        self._forget_btn = Gtk.MenuItem(label="Forget missing entries")
        self._forget_btn.set_sensitive(False)
        self._forget_btn.set_tooltip_text(
            "Remove entries whose folder is gone; files on disk are never touched"
        )
        self._forget_btn.connect("activate", lambda *_: self.forget_missing())
        menu.append(self._forget_btn)
        menu.show_all()
        more.set_popup(menu)
        row.pack_start(more, False, False, 0)

        toolbar.pack_start(row, False, False, 0)
        toolbar.pack_start(self.bulk.bar, False, False, 0)

        meta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        meta.get_style_context().add_class("library-meta")
        self._usage = Gtk.Label(label="", xalign=0)
        self._usage.set_ellipsize(Pango.EllipsizeMode.END)
        self._usage.set_hexpand(True)
        self._usage.get_style_context().add_class("usage-label")
        meta.pack_start(self._usage, True, True, 0)

        toolbar.pack_start(meta, False, False, 0)
        return toolbar

    def _build_content(self) -> Gtk.Widget:
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_wide_handle(False)
        paned.set_position(560)
        self._paned = paned

        self._grid_stack = Gtk.Stack()
        _style_stack(self._grid_stack)
        self._grid_stack.get_style_context().add_class("library-grid")

        self._flow = Gtk.FlowBox()
        self._flow.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._flow.set_activate_on_single_click(True)
        self._flow.set_homogeneous(True)
        self._flow.set_column_spacing(12)
        self._flow.set_row_spacing(14)
        self._flow.set_min_children_per_line(2)
        self._flow.set_max_children_per_line(6)
        # The flow fills the viewport and gets its breathing room from CSS
        # padding: transparent margins left an unpainted band that rendered
        # black on some compositors.
        self._flow.set_valign(Gtk.Align.FILL)
        self._flow.set_margin_start(0)
        self._flow.set_margin_end(0)
        self._flow.set_margin_top(0)
        self._flow.set_margin_bottom(0)
        self._flow.get_style_context().add_class("library-flow")
        self._flow.get_accessible().set_name("Local game library")
        self._flow.connect("selected-children-changed", self._on_selection_changed)
        self.preview = QuickPreview(self)
        self.preview.attach(self._flow)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.get_style_context().add_class("library-grid-scroll")
        scroller.add(self._flow)
        self._grid_scroll = scroller
        self._grid_stack.add_named(scroller, "grid")

        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9)
        empty.set_halign(Gtk.Align.CENTER)
        empty.set_valign(Gtk.Align.CENTER)
        empty_icon = Gtk.Image.new_from_icon_name(
            "applications-games-symbolic", Gtk.IconSize.DIALOG
        )
        empty_icon.get_style_context().add_class("dim")
        empty_title = Gtk.Label(label="Your library is empty")
        empty_title.get_style_context().add_class("empty-title")
        empty_text = Gtk.Label(
            label=(
                "Games appear here once a folder with your downloads sits in the "
                "download location. Nothing is scanned or started on its own."
            )
        )
        empty_text.set_line_wrap(True)
        empty_text.set_justify(Gtk.Justification.CENTER)
        empty_text.set_max_width_chars(46)
        empty_text.get_style_context().add_class("empty-text")
        self._empty_path = Gtk.Label(label="")
        self._empty_path.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self._empty_path.set_max_width_chars(50)
        self._empty_path.get_style_context().add_class("mono")
        rescan = Gtk.Button(label="Rescan library")
        rescan.set_halign(Gtk.Align.CENTER)
        rescan.connect("clicked", lambda *_: self.rescan())
        empty.pack_start(empty_icon, False, False, 0)
        empty.pack_start(empty_title, False, False, 0)
        empty.pack_start(empty_text, False, False, 0)
        empty.pack_start(self._empty_path, False, False, 0)
        empty.pack_start(rescan, False, False, 0)
        self._grid_stack.add_named(empty, "empty")

        noresults = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        noresults.set_halign(Gtk.Align.CENTER)
        noresults.set_valign(Gtk.Align.CENTER)
        noresults_icon = Gtk.Image.new_from_icon_name(
            "system-search-symbolic", Gtk.IconSize.DIALOG
        )
        noresults_icon.get_style_context().add_class("dim")
        noresults_title = Gtk.Label(label="No games match")
        noresults_title.get_style_context().add_class("empty-title")
        noresults_text = Gtk.Label(
            label="Try a shorter search, or clear the filters."
        )
        noresults_text.get_style_context().add_class("empty-text")
        clear = Gtk.Button(label="Clear filters")
        clear.set_halign(Gtk.Align.CENTER)
        clear.connect("clicked", lambda *_: self._reset_filters())
        noresults.pack_start(noresults_icon, False, False, 0)
        noresults.pack_start(noresults_title, False, False, 0)
        noresults.pack_start(noresults_text, False, False, 0)
        noresults.pack_start(clear, False, False, 0)
        self._grid_stack.add_named(noresults, "noresults")

        paned.pack1(self._grid_stack, False, False)

        self._detail = LibraryDetail(self)
        self._detail_revealer = Gtk.Revealer()
        _style_revealer(self._detail_revealer, Gtk.RevealerTransitionType.SLIDE_LEFT)
        self._detail_revealer.set_reveal_child(False)
        self._detail_revealer.add(self._detail)
        self._detail_revealer.set_hexpand(True)
        paned.pack2(self._detail_revealer, True, False)
        self.connect("size-allocate", self._on_paned_resized)
        GLib.idle_add(self._release_split)
        return paned

    # -- filtering ----------------------------------------------------------

    def refresh(self) -> None:
        """Rescan the index and rebuild the grid without losing the scroll."""
        adjustment = None
        saved = 0.0
        scroller = getattr(self, "_grid_scroll", None)
        if scroller is not None:
            adjustment = scroller.get_vadjustment()
            if adjustment is not None:
                saved = adjustment.get_value()
        with self._lock:
            self._entries = self._store.entries()
        self._apply_filters()
        if adjustment is not None:
            GLib.idle_add(self._restore_grid_scroll, adjustment, saved)

    @property
    def scanning(self) -> bool:
        return self._scanning

    def _apply_filters(self) -> None:
        self._sync_genres()
        status = self._status_combo.get_active_id()
        status_value = None if status in (None, "all") else status
        favorite = True if self._favorites_only.get_active() else None
        compatibility = self._compat_combo.get_active_id()
        compat_value = None if compatibility in (None, "all") else compatibility
        genre = self._genre_combo.get_active_id()
        genre_value = None if genre in (None, "all") else genre
        sort_key = self._sort_combo.get_active_id() or "title"
        descending = self._sort_descending

        filtered = LibraryStore.filter_entries(
            self._entries,
            query=self._search.get_text().strip(),
            status=status_value,
            favorite=favorite,
            compatibility=compat_value,
            genre=genre_value,
            games_only=self._settings.library_games_only,
            include_missing=self._settings.library_show_missing,
            exclude_adult=self._hide_adult.get_active(),
        )
        ordered = LibraryStore.sort_entries(
            filtered, sort_key, descending=descending
        )
        if self._settings.library_group_copies:
            self._visible, self._copies = LibraryStore.group_copies(ordered)
        else:
            self._visible, self._copies = ordered, {}
        self._populate(self._visible)
        self._update_usage()

        if not self._entries:
            self._grid_stack.set_visible_child_name("empty")
        elif not self._visible:
            self._grid_stack.set_visible_child_name("noresults")
        else:
            self._grid_stack.set_visible_child_name("grid")

        self._sync_filter_button()

    def _sync_filter_button(self) -> int:
        """Count the active filters on the one button that owns them all.

        Search is not counted: it has its own field.  Every other filter lives
        in that popover, so a badge is the only hint the toolbar needs.
        """
        combos = (self._status_combo, self._compat_combo, self._genre_combo)
        count = sum(
            1 for combo in combos if combo.get_active_id() not in (None, "all")
        )
        count += sum(
            1
            for on in (
                self._favorites_only.get_active(),
                self._hide_adult.get_active(),
                not self._settings.library_games_only,
                self._settings.library_show_missing,
            )
            if on
        )
        button = self._library_filter_btn
        button.set_label(f"Filter · {count}" if count else "Filter")
        button.set_tooltip_text(
            f"{count} filter{'s' if count != 1 else ''} on — click to change"
            if count else "Filter the library"
        )
        context = button.get_style_context()
        if count:
            context.add_class("filter-active")
        else:
            context.remove_class("filter-active")
        return count

    def _populate(self, entries: Sequence[LibraryEntry]) -> None:
        self.preview.grid_rebuilt()
        selected = self._selected_entry()
        selected_id = selected.identifier if selected is not None else None
        for child in list(self._flow.get_children()):
            self._flow.remove(child)
        self._tiles = {}
        compact = self._view_mode == "list"
        for entry in entries:
            tile = LibraryTile(
                self, entry,
                len(self._copies.get(entry.identifier, [])),
                compact=compact,
            )
            self._tiles[entry.identifier] = tile
            self._flow.add(tile)
            self.preview.track(tile)
        self._flow.show_all()
        bulk = getattr(self, "bulk", None)
        if bulk is not None:
            bulk.sync()

        if selected_id is not None and selected_id in self._tiles:
            GLib.idle_add(self._reselect_tile, selected_id)

        if self._detail_revealer.get_reveal_child():
            current = self._selected_entry()
            if current is None:
                self._detail_revealer.set_reveal_child(False)
        if selected_id is None:
            GLib.idle_add(self._release_split)

        if not self._restored_selection and self._tiles:
            self._restored_selection = True
            saved = self._settings.library_selected
            if selected_id is None and saved:
                GLib.idle_add(self._select_saved_tile, saved)

    def _restore_grid_scroll(self, adjustment: Gtk.Adjustment, value: float) -> bool:
        upper = max(0.0, adjustment.get_upper() - adjustment.get_page_size())
        adjustment.set_value(max(0.0, min(float(value), upper)))
        return False

    def _select_saved_tile(self, identifier: str) -> bool:
        tile = self._tiles.get(identifier)
        if tile is None:
            return False
        self._flow.select_child(tile)
        GLib.idle_add(self._scroll_tile_into_view, tile)
        return False

    def _scroll_tile_into_view(self, tile: Gtk.Widget) -> bool:
        scroller = getattr(self, "_grid_scroll", None)
        if scroller is None:
            return False
        adjustment = scroller.get_vadjustment()
        allocation = tile.get_allocation()
        if adjustment is None or allocation.height <= 0:
            return False
        top = allocation.y
        bottom = top + allocation.height
        value = adjustment.get_value()
        page = adjustment.get_page_size()
        if top < value:
            adjustment.set_value(max(0.0, top - 12))
        elif bottom > value + page:
            adjustment.set_value(min(bottom - page + 12,
                                     max(0.0, adjustment.get_upper() - page)))
        return False

    def _reselect_tile(self, identifier: str) -> bool:
        tile = self._tiles.get(identifier)
        if tile is not None:
            self._flow.select_child(tile)
        return False

    def _update_usage(self) -> None:
        with self._lock:
            usage = self._store.disk_usage()
        games = sum(
            1
            for entry in self._entries
            if entry.is_game
            and (self._settings.library_show_missing or entry.status != "missing")
        )
        total = int(usage.get("total_bytes", 0))
        free = int(usage.get("free_bytes", 0))
        hidden = max(0, len(self._entries) - len(self._visible))
        parts = [f"{len(self._visible)} shown", f"{games} {_plural(games, 'game')}"]
        if hidden:
            parts.append(f"{hidden} hidden")
        parts.append(human_size(total))
        if free:
            parts.append(f"{human_size(free)} free")
        self._usage.set_text(" · ".join(parts))
        self._usage.set_tooltip_text(
            f"Local library totals for {_shorten_home(self.owner.download_dir)}"
        )
        self._empty_path.set_text(_shorten_home(self.owner.download_dir))
        self._empty_path.set_tooltip_text(str(self.owner.download_dir))
        has_missing = any(entry.status == "missing" for entry in self._entries)
        self._forget_btn.set_sensitive(has_missing)
        self._forget_btn.set_tooltip_text(
            "Remove entries whose folder is gone; files on disk are never touched"
            if has_missing
            else "No missing entries to forget"
        )

    def _on_search_changed(self, _entry: Gtk.SearchEntry) -> None:
        if self._filter_source:
            GLib.source_remove(self._filter_source)
        self._filter_source = GLib.timeout_add(160, self._apply_filters_idle)

    def _apply_filters_idle(self) -> bool:
        self._filter_source = 0
        self._apply_filters()
        return False

    def _apply_flow_mode(self) -> None:
        compact = self._view_mode == "list"
        self._flow.set_homogeneous(not compact)
        self._flow.set_min_children_per_line(1 if compact else 2)
        self._flow.set_max_children_per_line(1 if compact else 6)
        self._flow.set_row_spacing(6 if compact else 18)

    def _sync_view_toggle(self) -> None:
        compact = self._view_mode == "list"
        image = Gtk.Image.new_from_icon_name(
            "view-list-symbolic" if not compact else "view-grid-symbolic",
            _ICON,
        )
        self._view_toggle.set_image(image)
        self._view_toggle.set_tooltip_text(
            "Switch to the compact list" if not compact
            else "Switch to the cover grid"
        )

    def _on_view_toggled(self, button: Gtk.ToggleButton) -> None:
        self.set_view_mode("list" if button.get_active() else "grid")

    def set_view_mode(self, mode: str) -> None:
        """Switch the library between cover grid and compact list."""
        mode = "list" if mode == "list" else "grid"
        if mode == self._view_mode:
            self._sync_view_toggle()
            return
        self._view_mode = mode
        try:
            self._settings.set_library_view(mode)
        except (OSError, ValueError):
            pass
        self._apply_flow_mode()
        self._sync_view_toggle()
        self._apply_filters()

    def _sync_genres(self) -> None:
        """Keep the genre filter in step with the genres actually present.

        Tags that merely repeat a game's own title are dropped: archive.org
        items are routinely tagged with their name, and a title is not a genre.
        """
        titles = {
            str(entry.title).strip()
            for entry in self._entries
            if str(entry.title or "").strip()
        }
        labels = genre_options(
            (genre for entry in self._entries for genre in entry.genres),
            titles=titles,
        )
        current = self._genre_combo.get_active_id() or "all"
        model = self._genre_combo.get_model()
        existing: list[str] = []
        if model is not None:
            for row in model:
                existing.append(str(row[0]))
        if existing == ["all"] + labels:
            return
        self._syncing_genres = True
        try:
            self._genre_combo.remove_all()
            self._genre_combo.append("all", "All genres")
            for label in labels:
                self._genre_combo.append(label, label)
            keep = current if current == "all" or current in labels else "all"
            self._genre_combo.set_active_id(keep)
        finally:
            self._syncing_genres = False

    def _on_filter_changed(self, *_args) -> None:
        if self._syncing_genres:
            return
        if self._filter_source:
            GLib.source_remove(self._filter_source)
            self._filter_source = 0
        self._apply_filters()

    def _on_library_filter_toggled(self, *_args) -> None:
        self._settings.set_library_games_only(not self._show_all_folders.get_active())
        self._settings.set_library_show_missing(self._show_missing.get_active())
        self._settings.set_library_group_copies(self._group_copies.get_active())
        self._settings.set_library_hide_adult(self._hide_adult.get_active())
        self._apply_filters()

    def forget_missing(self) -> None:
        count = sum(1 for entry in self._entries if entry.status == "missing")
        if not count:
            self.owner.toast("No missing entries to forget.")
            return
        dialog = Gtk.MessageDialog(
            transient_for=self.owner,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.CANCEL,
            text=f"Forget {count} missing {_plural(count, 'entry', 'entries')}?",
        )
        dialog.add_button("Forget entries", Gtk.ResponseType.OK)
        dialog.format_secondary_text(
            "These folders are no longer on disk. Only the library index is "
            "changed; nothing on disk is deleted."
        )
        dialog.set_default_response(Gtk.ResponseType.CANCEL)
        response = dialog.run()
        dialog.destroy()
        if response != Gtk.ResponseType.OK:
            return
        with self._lock:
            removed = self._store.forget_missing()
            try:
                self._store.save()
            except OSError as exc:
                self.owner.toast(
                    f"Entries were forgotten but the index could not be saved: {exc}",
                    error=True,
                )
        self.refresh()
        self.owner.toast(
            f"Forgot {removed} {_plural(removed, 'entry', 'entries')}."
        )

    def remove_entry(self, entry: LibraryEntry) -> None:
        """Forget one library entry; downloaded files are left untouched."""
        with self._lock:
            removed = self._store.remove(entry.identifier)
            if removed:
                try:
                    self._store.save()
                except OSError as exc:
                    self.owner.toast(
                        f"The entry was removed but the index could not be saved: {exc}",
                        error=True,
                    )
        if not removed:
            return
        self.clear_selection()
        self.refresh()
        self.owner.toast(
            f"Removed {entry.title or entry.identifier} from the library. "
            "Files were kept on disk."
        )

    def _saved_sort_direction(self) -> bool:
        saved = self._settings.library_sort_descending
        if saved is None:
            return default_descending(self._settings.library_sort)
        return saved

    def _sync_sort_direction(self) -> None:
        """Paint the direction button from ``self._sort_descending``."""
        descending = self._sort_descending
        icon = "pan-down-symbolic" if descending else "pan-up-symbolic"
        self._sort_desc.set_image(
            Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.MENU)
        )
        self._sort_desc.set_tooltip_text(
            "Descending — newest, largest and latest first (click for ascending)"
            if descending else
            "Ascending — oldest, smallest and earliest first (click for descending)"
        )
        self._sort_desc.get_accessible().set_name(
            "Sort descending" if descending else "Sort ascending"
        )

    def _on_sort_changed(self, _combo: Gtk.ComboBoxText) -> None:
        value = self._sort_combo.get_active_id() or "title"
        try:
            self._settings.set_library_sort(value)
        except (OSError, ValueError):
            pass
        # Each mode starts in its own natural direction; the toggle exists to
        # override it, not to leak one mode's direction into the next.
        self._sort_descending = default_descending(value)
        try:
            self._settings.set_library_sort_descending(self._sort_descending)
        except (OSError, ValueError):
            pass
        self._sync_sort_direction()
        self._apply_filters()

    def _on_sort_direction_clicked(self, _button: Gtk.Button) -> None:
        self._sort_descending = not self._sort_descending
        try:
            self._settings.set_library_sort_descending(self._sort_descending)
        except (OSError, ValueError):
            pass
        self._sync_sort_direction()
        self._apply_filters()

    def _reset_filters(self) -> None:
        self._search.set_text("")
        self._status_combo.set_active_id("all")
        self._compat_combo.set_active_id("all")
        self._genre_combo.set_active_id("all")
        self._favorites_only.set_active(False)
        self._hide_adult.set_active(False)
        self._apply_filters()

    def focus_search(self) -> None:
        self._search.grab_focus()
        self._search.select_region(0, -1)

    # -- covers -------------------------------------------------------------

    def request_cover(self, path: str, callback: Callable[[GdkPixbuf.Pixbuf | None], None]) -> None:
        if not path:
            callback(None)
            return
        cached = self._cover_cache.get(path)
        if cached is not None:
            self._cover_cache.move_to_end(path)
            callback(cached)
            return
        if path in self._cover_misses:
            callback(None)
            return
        if path in self._cover_pending:
            self._cover_waiters.setdefault(path, []).append(callback)
            return
        self._cover_pending.add(path)
        self._cover_waiters[path] = [callback]
        _spawn(
            lambda: _load_cover_pixbuf(path),
            lambda pixbuf: self._deliver_cover(path, pixbuf),
            lambda _exc: self._deliver_cover(path, None),
        )

    def _deliver_cover(self, path: str, pixbuf: object) -> None:
        self._cover_pending.discard(path)
        waiters = self._cover_waiters.pop(path, [])
        if isinstance(pixbuf, GdkPixbuf.Pixbuf):
            self._cover_cache[path] = pixbuf
            self._cover_cache.move_to_end(path)
            while len(self._cover_cache) > _COVER_CACHE_LIMIT:
                self._cover_cache.popitem(last=False)
        else:
            self._cover_misses.add(path)
        for callback in waiters:
            try:
                callback(pixbuf if isinstance(pixbuf, GdkPixbuf.Pixbuf) else None)
            except Exception:
                continue

    # -- selection ----------------------------------------------------------

    def _selected_entry(self) -> LibraryEntry | None:
        children = self._flow.get_selected_children()
        if not children:
            return None
        tile = children[0]
        return tile.entry if isinstance(tile, LibraryTile) else None

    def _on_selection_changed(self, _flow: Gtk.FlowBox) -> None:
        self.preview.selection_changed()
        entry = self._selected_entry()
        if entry is None:
            self._detail_revealer.set_reveal_child(False)
            GLib.idle_add(self._release_split)
            return
        self._detail.show_entry(entry, self._copies.get(entry.identifier, []))
        self._detail_revealer.set_reveal_child(True)
        try:
            self._settings.set_library_selected(entry.identifier)
        except (OSError, ValueError):
            pass
        GLib.idle_add(self._place_split)

    def _release_split(self) -> bool:
        allocation = self._paned.get_allocation()
        width = allocation.width if allocation is not None else 0
        if width > 0 and not self._detail_revealer.get_reveal_child():
            self._paned.set_position(width)
        return False

    def _on_paned_resized(self, _paned: Gtk.Paned,
                          _allocation: Gdk.Rectangle) -> None:
        """Keep the detail column at a fixed, readable width when selected."""
        if self._detail_revealer.get_reveal_child():
            GLib.idle_add(self._place_split)

    def _place_split(self) -> bool:
        allocation = self._paned.get_allocation()
        width = allocation.width if allocation is not None else 0
        if width <= 0:
            return False
        grid_min = 380
        detail_width = int(min(460, max(340, width * 0.32)))
        position = max(grid_min, width - detail_width)
        position = min(position, width - 320)
        self._paned.set_position(position)
        return False

    def clear_selection(self) -> None:
        self.preview.selection_changed()
        self._flow.unselect_all()
        self._detail_revealer.set_reveal_child(False)
        GLib.idle_add(self._release_split)

    # -- favourites ---------------------------------------------------------

    def set_favorite(self, entry: LibraryEntry, value: bool) -> None:
        with self._lock:
            changed = self._store.set_favorite(entry.identifier, value)
            if changed:
                try:
                    self._store.save()
                except OSError as exc:
                    self.owner.toast(
                        f"The favorite was changed but could not be saved: {exc}",
                        error=True,
                    )
        entry.favorite = bool(value)
        tile = self._tiles.get(entry.identifier)
        if tile is not None:
            tile.sync_star()
        selected = self._selected_entry()
        if selected is not None and selected.identifier == entry.identifier:
            self._detail.set_favorite_state(entry.favorite)
        if value is False and self._favorites_only.get_active():
            self._apply_filters()

    # -- metadata / magnet actions -----------------------------------------

    def save_details(self, entry: LibraryEntry, values: dict[str, object], *,
                     notes: str | None = None, magnet: str = "",
                     infohash: str = "") -> None:
        """Persist hand-edited details. Raises on an invalid value.

        Validation happens before anything is written, so a rejected form never
        leaves the entry half-updated.
        """
        link = None
        if magnet.strip():
            link = magnetlib.parse_magnet(magnet)
        elif infohash.strip():
            link = magnetlib.build_magnet(infohash, display_name=entry.title)

        with self._lock:
            try:
                self._store.set_user_metadata(entry.identifier, values)
                if link is None:
                    stored = self._store.set_magnet(entry.identifier, "")
                elif magnet.strip():
                    stored = self._store.set_magnet(entry.identifier, link.uri)
                else:
                    stored = self._store.set_infohash(entry.identifier, link.infohash)
                # A background rescan can replace the stored object while this
                # form is open.  Keep the caller's object in step with what was
                # actually written (it may still be on screen), then show the
                # stored object rather than the stale copy.
                if stored is not None and stored is not entry:
                    entry.magnet = stored.magnet
                    entry.infohash = stored.infohash
                    entry.magnet_origin = stored.magnet_origin
                    entry = stored
                if notes is not None:
                    entry.notes = metalib.clean_description(notes)[:4000]
                self._store.save()
            except KeyError:
                raise

        self._detail.show_entry(entry, self._copies.get(entry.identifier, []))
        self._apply_filters()
        self.owner.toast("Game details saved.")

    def open_magnet(self, entry: LibraryEntry) -> None:
        """Hand a stored magnet URI to the configured client or the desktop."""
        link = magnetlib.magnet_from_entry(
            entry.magnet, entry.infohash, entry.magnet_origin
        )
        if link is None:
            self.owner.toast("There is no valid magnet link on this game.", error=True)
            return
        self.open_magnet_uri(link.uri)

    def open_magnet_uri(self, uri: str) -> None:
        """Hand one validated magnet URI to the desktop handler.

        Shared by a library entry and by a catalog record whose JSON sidecar
        published a magnet.  The Settings field is the aria2c *engine* path, not
        a general magnet handler, so this action deliberately leaves the
        managed queue alone and lets the desktop decide.
        """
        try:
            link = magnetlib.parse_magnet(uri)
        except magnetlib.MagnetError:
            self.owner.toast("That is not a valid magnet link.", error=True)
            return
        if platform_support.open_magnet(link.uri):
            self.owner.toast("Opening the magnet link with the desktop handler.")
            return
        self.owner.toast(
            "No magnet handler answered. Install a torrent client, or use "
            "Download with torrent in the catalog details.",
            error=True,
        )

    def clear_magnet(self, entry: LibraryEntry) -> None:
        with self._lock:
            changed = self._store.clear_magnet(entry.identifier)
            if changed:
                try:
                    self._store.save()
                except OSError as exc:
                    self.owner.toast(
                        f"The magnet link was cleared but could not be saved: {exc}",
                        error=True,
                    )
        if changed:
            entry.magnet = ""
            entry.infohash = ""
            entry.magnet_origin = ""
            self._detail.show_entry(entry, self._copies.get(entry.identifier, []))
            self.owner.toast("Magnet link removed.")

    # -- scan / export / import --------------------------------------------

    def rescan(self, silent: bool = False) -> None:
        if self._scanning:
            return
        self._scanning = True
        self._rescan_btn.set_sensitive(False)
        self._usage.set_text("Scanning the download folder…")

        def success(report: object) -> None:
            self._scanning = False
            self._rescan_btn.set_sensitive(True)
            self.refresh()
            if not silent:
                summary = (
                    f"Library scanned: {report.scanned} "
                    f"{_plural(report.scanned, 'folder')}, "
                    f"{report.added} new, {report.missing} missing."
                )
                if report.errors:
                    self.owner.toast(
                        f"{summary} {len(report.errors)} folder(s) could not be read.",
                        error=True,
                    )
                else:
                    self.owner.toast(summary)
            if report.missing and silent:
                self.owner.toast(
                    f"{report.missing} {_plural(report.missing, 'game')} "
                    "could not be found on disk.",
                    error=True,
                )

        def failure(exc: object) -> None:
            self._scanning = False
            self._rescan_btn.set_sensitive(True)
            self.refresh()
            self.owner.toast(
                f"Could not scan the library: {_error_text(exc)}", error=True
            )

        def work():
            with self._lock:
                return self._store.scan()

        _spawn(work, success, failure)

    def schedule_rescan(self, delay: int = 1200) -> None:
        if self._rescan_source:
            GLib.source_remove(self._rescan_source)
        self._rescan_source = GLib.timeout_add(delay, self._scheduled_rescan)

    def _scheduled_rescan(self) -> bool:
        self._rescan_source = 0
        self.rescan(silent=True)
        return False

    def download_root_changed(self) -> None:
        self.refresh()
        self.rescan(silent=True)

    def export_library(self) -> None:
        dialog = Gtk.FileChooserNative(
            title="Export library index",
            transient_for=self.owner,
            action=Gtk.FileChooserAction.SAVE,
            accept_label="Export",
            cancel_label="Cancel",
        )
        dialog.set_current_name("library.json")
        if hasattr(dialog, "set_do_overwrite_confirmation"):
            dialog.set_do_overwrite_confirmation(True)
        response = dialog.run()
        path = dialog.get_filename()
        dialog.destroy()
        if response != Gtk.ResponseType.ACCEPT or not path:
            return

        def success(count: object) -> None:
            self.owner.toast(
                f"Exported {count} {_plural(int(count), 'entry', 'entries')} to {path}."
            )

        def failure(exc: object) -> None:
            self.owner.toast(f"Export failed: {_error_text(exc)}", error=True)

        def work():
            with self._lock:
                return self._store.export_json(Path(path))

        _spawn(work, success, failure)

    def import_library(self) -> None:
        dialog = Gtk.FileChooserNative(
            title="Import library index",
            transient_for=self.owner,
            action=Gtk.FileChooserAction.OPEN,
            accept_label="Import",
            cancel_label="Cancel",
        )
        filter_json = Gtk.FileFilter()
        filter_json.set_name("JSON files")
        filter_json.add_pattern("*.json")
        dialog.add_filter(filter_json)
        response = dialog.run()
        path = dialog.get_filename()
        dialog.destroy()
        if response != Gtk.ResponseType.ACCEPT or not path:
            return

        def success(report: object) -> None:
            self.refresh()
            message = (
                f"Imported {report.imported} "
                f"{_plural(report.imported, 'entry', 'entries')}"
            )
            if report.skipped:
                message += f", skipped {report.skipped}"
            if report.errors:
                message += f". {report.errors[0]}"
            self.owner.toast(message, error=bool(report.errors))

        def failure(exc: object) -> None:
            self.owner.toast(f"Import failed: {_error_text(exc)}", error=True)

        def work():
            with self._lock:
                return self._store.import_json(Path(path), merge=True)

        _spawn(work, success, failure)

    # -- integrity ----------------------------------------------------------

    def verify_entry(self, entry: LibraryEntry, detail: LibraryDetail) -> None:
        if self._verify_busy:
            self.owner.toast("A check is already running.")
            return
        self._verify_busy = True
        self._verify_token += 1
        token = self._verify_token
        detail.start_verifying()

        client = self.owner.client
        archive_lock = self.owner.archive_lock
        identifier = entry.identifier
        folder = Path(entry.folder)

        def progress(done: int, total: int) -> None:
            GLib.idle_add(self._on_verify_progress, token, done, total)

        def work():
            try:
                scanned = scan_game_folder(folder)
            except OSError as exc:
                return ("error", IntegrityResult(
                    status=IntegrityStatus.ERROR,
                    message=f"Could not read the folder: {exc}",
                ), "")
            candidates = [f for f in scanned.files if f.size > 0]
            if not candidates:
                return ("error", IntegrityResult(
                    status=IntegrityStatus.ERROR,
                    message="No readable files were found in this folder.",
                ), "")
            target = max(candidates, key=lambda f: f.size)

            item = None
            try:
                with archive_lock:
                    item = client.fetch_item(identifier, force=False)
            except Exception:
                item = None

            archive_file = None
            if item is not None:
                by_name = {Path(f.name).name: f for f in item.files}
                archive_file = by_name.get(Path(target.path).name)
                if archive_file is None:
                    stem = Path(target.path).stem
                    matches = [f for f in item.files if Path(f.name).stem == stem]
                    if matches:
                        archive_file = max(matches, key=lambda f: f.size or 0)
            if archive_file is None:
                message = (
                    "Could not read the archive.org metadata, so no published "
                    "checksum is available."
                    if item is None
                    else "No published checksum matches the files in this folder."
                )
                return ("missing", IntegrityResult(
                    status=IntegrityStatus.UNAVAILABLE, message=message,
                ), Path(target.path).name)

            try:
                return ("ok", verify_file(target.path, archive_file, progress=progress),
                        Path(target.path).name)
            except Exception as exc:
                return ("error", IntegrityResult(
                    status=IntegrityStatus.ERROR,
                    message=f"Could not read the file: {exc}",
                ), Path(target.path).name)

        _spawn(work, lambda payload: self._on_verified(token, entry, detail, payload),
               lambda exc: self._on_verify_failed(token, detail, exc))

    def _on_verify_progress(self, token: int, done: int, total: int) -> bool:
        if token != self._verify_token:
            return False
        self._detail.update_progress(done, total)
        return False

    def _on_verified(self, token: int, entry: LibraryEntry,
                     detail: LibraryDetail, payload: object) -> None:
        if token != self._verify_token:
            return
        self._verify_busy = False
        if not (isinstance(payload, tuple) and len(payload) == 3):
            detail.finish_verifying(
                IntegrityResult(status=IntegrityStatus.ERROR,
                                message="The check did not produce a result."),
                "",
            )
            return
        _kind, result, filename = payload
        if not isinstance(result, IntegrityResult):
            return
        with self._lock:
            stored = self._store.get(entry.identifier)
            if stored is not None:
                stored.integrity = result.status.value
                try:
                    self._store.save()
                except OSError as exc:
                    self.owner.toast(
                        f"The result could not be saved: {exc}", error=True
                    )
        entry.integrity = result.status.value
        detail.finish_verifying(result, filename)
        self.owner.toast(_integrity_label(result.status.value),
                         error=result.status in (IntegrityStatus.MISMATCH,
                                                 IntegrityStatus.ERROR))

    def _on_verify_failed(self, token: int, detail: LibraryDetail,
                          exc: object) -> None:
        if token != self._verify_token:
            return
        self._verify_busy = False
        detail.finish_verifying(
            IntegrityResult(status=IntegrityStatus.ERROR,
                            message=_error_text(exc)),
            "",
        )

    # -- actions ------------------------------------------------------------

    def launch_entry(self, entry: LibraryEntry,
                     chosen: LocalFile | None = None) -> None:
        if chosen is not None and chosen.launchable:
            path = Path(chosen.path)
            if path.is_file():
                self.owner.review_launch(chosen)
                return
            self.owner.toast(
                "The chosen file is no longer on disk. Pick another file.",
                error=True,
            )
        folder = Path(entry.folder)

        def success(scanned: object) -> None:
            executables = list(getattr(scanned, "executables", []) or [])
            if not executables:
                self.owner.toast(
                    "No launchable file was found in this folder.", error=True
                )
                return
            self.owner.review_launch(executables[0])

        def failure(exc: object) -> None:
            self.owner.toast(
                f"Could not read the folder: {_error_text(exc)}", error=True
            )

        _spawn(lambda: scan_game_folder(folder), success, failure)

    def show_files(self, entry: LibraryEntry) -> None:
        folder = Path(entry.folder)

        def success(files: object) -> None:
            self._files_dialog(entry, files)

        def failure(exc: object) -> None:
            self.owner.toast(
                f"Could not list the folder: {_error_text(exc)}", error=True
            )

        _spawn(lambda: _list_folder(folder), success, failure)

    def _files_dialog(self, entry: LibraryEntry, files: object) -> None:
        entries = list(files) if isinstance(files, list) else []
        dialog = Gtk.Dialog(
            title=f"Files in {entry.title or entry.identifier}",
            transient_for=self.owner,
            modal=True,
        )
        dialog.set_default_size(620, 460)
        dialog.add_button("Close", Gtk.ResponseType.CLOSE)
        content = dialog.get_content_area()
        content.set_spacing(8)
        content.set_margin_start(18)
        content.set_margin_end(18)
        content.set_margin_top(14)
        content.set_margin_bottom(14)

        header = Gtk.Label(
            label=(
                f"{len(entries)} {_plural(len(entries), 'file')} · "
                f"{human_size(sum(size for _name, size in entries))}"
            ),
            xalign=0,
        )
        header.get_style_context().add_class("count-label")
        content.pack_start(header, False, False, 0)

        if not entries:
            empty = Gtk.Label(label="This folder has no files to show.")
            empty.get_style_context().add_class("dim")
            content.pack_start(empty, False, False, 0)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.get_style_context().add_class("file-frame")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        for name, size in entries:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            row.set_margin_start(12)
            row.set_margin_end(12)
            row.set_margin_top(5)
            row.set_margin_bottom(5)
            label = Gtk.Label(label=name, xalign=0)
            label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            label.set_hexpand(True)
            label.set_selectable(True)
            if name.endswith(".part"):
                label.get_style_context().add_class("integrity-warn")
            size_label = Gtk.Label(label=human_size(size), xalign=1)
            size_label.get_style_context().add_class("dim")
            size_label.get_style_context().add_class("small")
            row.pack_start(label, True, True, 0)
            row.pack_start(size_label, False, False, 0)
            box.pack_start(row, False, False, 0)
        scroller.add(box)
        content.pack_start(scroller, True, True, 0)

        content.show_all()
        dialog.run()
        dialog.destroy()

    def show_duplicates(self) -> None:
        dialog = Gtk.Dialog(
            title="Duplicate files",
            transient_for=self.owner,
            modal=True,
        )
        dialog.set_default_size(680, 480)
        dialog.add_button("Close", Gtk.ResponseType.CLOSE)
        content = dialog.get_content_area()
        content.set_spacing(8)
        content.set_margin_start(18)
        content.set_margin_end(18)
        content.set_margin_top(14)
        content.set_margin_bottom(14)

        note = Gtk.Label(
            label=(
                "Files that look identical across game folders. Nothing is "
                "deleted or moved; open a folder to look for yourself."
            ),
            xalign=0,
        )
        note.set_line_wrap(True)
        note.set_max_width_chars(78)
        note.get_style_context().add_class("dim")
        note.get_style_context().add_class("small")
        content.pack_start(note, False, False, 0)

        stack = Gtk.Stack()
        _style_stack(stack)
        spinner_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        spinner_page.set_valign(Gtk.Align.CENTER)
        spinner_page.set_halign(Gtk.Align.CENTER)
        spinner = Gtk.Spinner()
        spinner.start()
        spinner.set_size_request(26, 26)
        spinner_label = Gtk.Label(label="Comparing files…")
        spinner_label.get_style_context().add_class("dim")
        spinner_page.pack_start(spinner, False, False, 0)
        spinner_page.pack_start(spinner_label, False, False, 0)
        stack.add_named(spinner_page, "loading")

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        results = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        scroller.add(results)
        stack.add_named(scroller, "list")

        empty = Gtk.Label(label="No duplicate files were found.")
        empty.set_valign(Gtk.Align.CENTER)
        empty.get_style_context().add_class("empty-text")
        stack.add_named(empty, "empty")

        stack.set_visible_child_name("loading")
        content.pack_start(stack, True, True, 0)

        def success(groups: object) -> None:
            if not dialog.get_visible():
                return
            items = list(groups) if isinstance(groups, list) else []
            if not items:
                stack.set_visible_child_name("empty")
                return
            for group in items:
                if isinstance(group, DuplicateGroup):
                    results.pack_start(self._duplicate_group(group), False, False, 0)
            results.show_all()
            stack.set_visible_child_name("list")

        def failure(exc: object) -> None:
            if not dialog.get_visible():
                return
            empty.set_text(f"Could not compare files: {_error_text(exc)}")
            stack.set_visible_child_name("empty")

        def work():
            with self._lock:
                return self._store.find_duplicates()

        _spawn(work, success, failure)
        content.show_all()
        dialog.run()
        dialog.destroy()

    def _duplicate_group(self, group: DuplicateGroup) -> Gtk.Widget:
        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        frame.get_style_context().add_class("duplicate-group")
        kind = "Identical SHA-256" if group.kind == "sha256" else "Same name and size"
        heading = Gtk.Label(
            label=f"{kind} · {human_size(group.size)} · {len(group.paths)} copies",
            xalign=0,
        )
        heading.get_style_context().add_class("duplicate-heading")
        frame.pack_start(heading, False, False, 0)
        for raw in group.paths:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            path = Path(raw)
            label = Gtk.Label(label=str(path), xalign=0)
            label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            label.set_hexpand(True)
            label.set_selectable(True)
            label.get_style_context().add_class("mono")
            label.set_tooltip_text(str(path))
            open_button = _small_button("Open folder", "Open the folder containing this file")
            parent = path.parent
            open_button.connect(
                "clicked", lambda _b, folder=parent: self.owner.open_folder(folder)
            )
            row.pack_start(label, True, True, 0)
            row.pack_start(open_button, False, False, 0)
            frame.pack_start(row, False, False, 0)
        return frame


def _list_folder(folder: Path) -> list[tuple[str, int]]:
    """Visible files in *folder* as (relative path, size). Read-only."""
    root = Path(folder)
    files: list[tuple[str, int]] = []
    if not root.is_dir():
        return files
    try:
        for current, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = sorted(
                d for d in dirs
                if not d.startswith(".") and not Path(current, d).is_symlink()
            )
            for name in sorted(names):
                if len(files) >= 2000:
                    return files
                if name.startswith("."):
                    continue
                path = Path(current) / name
                if path.is_symlink() or not path.is_file():
                    continue
                try:
                    size = path.stat().st_size
                    shown = str(path.relative_to(root))
                except (OSError, ValueError):
                    continue
                files.append((shown, size))
    except OSError:
        return files
    return files


# -- details view ------------------------------------------------------------


class ScreenshotTile(Gtk.Button):
    """One clickable gameplay screenshot thumbnail."""

    def __init__(self, index: int,
                 on_click: Callable[[int], None]) -> None:
        super().__init__()
        self.index = index
        self.set_relief(Gtk.ReliefStyle.NONE)
        self.get_style_context().add_class("screenshot-thumb")
        self.set_tooltip_text("Open this screenshot")
        self.set_size_request(158, 89)
        self.set_valign(Gtk.Align.CENTER)

        self._stack = Gtk.Stack()
        _style_stack(self._stack)
        self._art = CoverArt()
        self._stack.add_named(self._art, "image")

        loading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        loading.set_halign(Gtk.Align.CENTER)
        loading.set_valign(Gtk.Align.CENTER)
        spinner = Gtk.Spinner()
        spinner.set_size_request(18, 18)
        spinner.start()
        loading.pack_start(spinner, False, False, 0)
        self._stack.add_named(loading, "loading")
        self._stack.set_visible_child_name("loading")
        self.add(self._stack)
        self.connect("clicked", lambda *_: on_click(self.index))

    def set_pixbuf(self, pixbuf: GdkPixbuf.Pixbuf | None) -> None:
        if pixbuf is None:
            return
        self._art.set_pixbuf(pixbuf)
        self._stack.set_visible_child_name("image")


class ScreenshotLightbox(Gtk.Dialog):
    """Full-size screenshot viewer with arrow-key navigation."""

    def __init__(self, parent: Gtk.Window, item: ArchiveItem,
                 files: Sequence[ArchiveFile], index: int,
                 fetch: Callable[[str, str, Callable[[object], None],
                                  Callable[[object], None]], None]) -> None:
        super().__init__(title="Screenshots", transient_for=parent, modal=True)
        self._item = item
        self._files = list(files)
        self._index = max(0, min(index, len(self._files) - 1))
        self._fetch = fetch
        self._cache: dict[str, GdkPixbuf.Pixbuf] = {}
        self._pixbuf: GdkPixbuf.Pixbuf | None = None
        self._token = 0
        self.set_default_size(980, 680)
        self.add_button("Close", Gtk.ResponseType.CLOSE)
        self.connect("response", lambda dialog, _r: dialog.destroy())
        self.connect("key-press-event", self._on_key_press)

        content = self.get_content_area()
        content.set_spacing(8)
        content.set_margin_start(10)
        content.set_margin_end(10)
        content.set_margin_top(10)
        content.set_margin_bottom(10)

        self._stack = Gtk.Stack()
        _style_stack(self._stack)
        self._area = Gtk.DrawingArea()
        self._area.connect("draw", self._draw)
        self._stack.add_named(self._area, "image")

        loading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        loading.set_halign(Gtk.Align.CENTER)
        loading.set_valign(Gtk.Align.CENTER)
        spinner = Gtk.Spinner()
        spinner.set_size_request(26, 26)
        spinner.start()
        label = Gtk.Label(label="Loading screenshot…")
        label.get_style_context().add_class("dim")
        loading.pack_start(spinner, False, False, 0)
        loading.pack_start(label, False, False, 0)
        self._stack.add_named(loading, "loading")
        self._stack.set_visible_child_name("loading")
        content.pack_start(self._stack, True, True, 0)

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        bar.set_halign(Gtk.Align.CENTER)
        self._prev = _flat_button("go-previous-symbolic", "Previous screenshot")
        self._prev.connect("clicked", lambda *_: self.step(-1))
        self._counter = Gtk.Label(label="")
        self._counter.get_style_context().add_class("count-label")
        self._next = _flat_button("go-next-symbolic", "Next screenshot")
        self._next.connect("clicked", lambda *_: self.step(1))
        bar.pack_start(self._prev, False, False, 0)
        bar.pack_start(self._counter, False, False, 0)
        bar.pack_start(self._next, False, False, 0)
        content.pack_start(bar, False, False, 0)

        self.show_all()
        self._prev.set_visible(len(self._files) > 1)
        self._next.set_visible(len(self._files) > 1)
        self._show_index(self._index)

    def _show_index(self, index: int) -> None:
        if not self._files:
            return
        self._index = (index + len(self._files)) % len(self._files)
        self._counter.set_text(
            f"{self._index + 1} of {len(self._files)}"
        )
        self._prev.set_sensitive(len(self._files) > 1)
        self._next.set_sensitive(len(self._files) > 1)
        archive_file = self._files[self._index]
        url = self._item.file_url(archive_file)
        cached = self._cache.get(url)
        if cached is not None:
            self._pixbuf = cached
            self._stack.set_visible_child_name("image")
            self._area.queue_draw()
            return
        self._pixbuf = None
        self._stack.set_visible_child_name("loading")
        self._token += 1
        token = self._token
        key = f"{self._item.identifier}-view-{self._index}"

        def success(data: object) -> None:
            if token != self._token or not self.get_visible():
                return
            if not isinstance(data, (bytes, bytearray)):
                return
            pixbuf = self._decode(bytes(data))
            if pixbuf is None:
                return
            self._cache[url] = pixbuf
            self._pixbuf = pixbuf
            self._stack.set_visible_child_name("image")
            self._area.queue_draw()

        def failure(_exc: object) -> None:
            if token == self._token and self.get_visible():
                self._counter.set_text(
                    f"{self._index + 1} of {len(self._files)} · not available"
                )

        self._fetch(key, url, success, failure)

    @staticmethod
    def _decode(data: bytes) -> GdkPixbuf.Pixbuf | None:
        loader = GdkPixbuf.PixbufLoader()
        try:
            loader.write(data)
            loader.close()
        except GLib.Error:
            return None
        return loader.get_pixbuf()

    def _draw(self, area: Gtk.DrawingArea, cr) -> bool:
        pixbuf = self._pixbuf
        if pixbuf is None:
            return False
        allocation = area.get_allocation()
        width, height = pixbuf.get_width(), pixbuf.get_height()
        if width <= 0 or height <= 0:
            return False
        scale = min(allocation.width / width, allocation.height / height, 1.0)
        draw_width = width * scale
        draw_height = height * scale
        x = (allocation.width - draw_width) / 2.0
        y = (allocation.height - draw_height) / 2.0
        cr.save()
        cr.translate(x, y)
        cr.scale(scale, scale)
        Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
        cr.paint()
        cr.restore()
        return False

    def step(self, delta: int) -> None:
        if len(self._files) > 1:
            self._show_index(self._index + delta)

    def _on_key_press(self, _dialog: Gtk.Dialog,
                      event: Gdk.EventKey) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            self.response(Gtk.ResponseType.CLOSE)
            return True
        if event.keyval in (Gdk.KEY_Left, Gdk.KEY_Up):
            self.step(-1)
            return True
        if event.keyval in (Gdk.KEY_Right, Gdk.KEY_Down):
            self.step(1)
            return True
        return False


class DetailsView(Gtk.Box):
    """Right-hand pane: metadata, file table, and downloaded files."""

    POSTER_WIDTH = 220
    POSTER_HEIGHT = 300

    COL_NAME = 0
    COL_KIND = 1
    COL_SIZE = 2
    COL_FORMAT = 3
    COL_RAW = 4
    COL_DL = 5
    COL_AUX = 6
    COL_MD5 = 7
    COL_SHA1 = 8

    LOCAL_NAME = 0
    LOCAL_KIND = 1
    LOCAL_SIZE = 2
    LOCAL_PATH = 3
    LOCAL_LAUNCHABLE = 4

    def __init__(self, owner: "MainWindow") -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._owner = owner
        self._item: ArchiveItem | None = None
        self._files_by_name: dict[str, ArchiveFile] = {}
        self._local_folder: Path | None = None
        self._retry_cb: Callable[[], None] | None = None
        self._poster_token = 0
        self._poster_pixbuf: GdkPixbuf.Pixbuf | None = None
        self._poster_raw: GdkPixbuf.Pixbuf | None = None
        self._poster_title = ""
        self._poster_fallback_tried = False
        self._poster_box = (self.POSTER_WIDTH, self.POSTER_HEIGHT)
        self._poster_cache: dict[str, GdkPixbuf.Pixbuf] = {}
        self._shot_cache: dict[str, GdkPixbuf.Pixbuf] = {}
        self._shot_token = 0
        self._shot_loaded = 0
        self._shot_failed = 0
        self._lightbox: "ScreenshotLightbox | None" = None
        self._catalog_token = 0
        self._catalog_url = ""
        self.connect("size-allocate", self._on_details_resize)

        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._stack.set_transition_duration(130)
        self.pack_start(self._stack, True, True, 0)

        self._build_empty_page()
        self._build_loading_page()
        self._build_error_page()
        self._build_content_page()
        self._build_catalog_page()
        self._build_selection_bar()
        self.show_empty()

    # -- pages --------------------------------------------------------------

    @staticmethod
    def _centered(icon_name: str, icon_size: int = Gtk.IconSize.DIALOG) -> tuple[Gtk.Box, Gtk.Image]:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_halign(Gtk.Align.CENTER)
        box.set_valign(Gtk.Align.CENTER)
        box.set_margin_start(32)
        box.set_margin_end(32)
        image = Gtk.Image.new_from_icon_name(icon_name, icon_size)
        image.get_style_context().add_class("dim")
        box.pack_start(image, False, False, 0)
        return box, image

    def _build_empty_page(self) -> None:
        box, _image = self._centered("applications-games-symbolic")
        title = Gtk.Label(label="Select a game")
        title.get_style_context().add_class("empty-title")
        text = Gtk.Label(
            label="Choose an archive item on the left to read its notes and files.",
            justify=Gtk.Justification.CENTER,
        )
        text.set_line_wrap(True)
        text.set_max_width_chars(44)
        text.get_style_context().add_class("empty-text")
        box.pack_start(title, False, False, 0)
        box.pack_start(text, False, False, 0)
        self._stack.add_named(box, "empty")

    def _build_loading_page(self) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_halign(Gtk.Align.CENTER)
        box.set_valign(Gtk.Align.CENTER)
        spinner = Gtk.Spinner()
        spinner.set_size_request(28, 28)
        spinner.start()
        self._loading_label = Gtk.Label(label="Loading archive item…")
        self._loading_label.get_style_context().add_class("empty-text")
        self._loading_game = Gtk.Label(label="")
        self._loading_game.set_ellipsize(Pango.EllipsizeMode.END)
        self._loading_game.set_max_width_chars(50)
        self._loading_game.get_style_context().add_class("dim")
        self._loading_game.get_style_context().add_class("small")
        box.pack_start(spinner, False, False, 0)
        box.pack_start(self._loading_label, False, False, 0)
        box.pack_start(self._loading_game, False, False, 0)
        self._stack.add_named(box, "loading")

    def _build_error_page(self) -> None:
        box, _image = self._centered("dialog-error-symbolic")
        self._error_label = Gtk.Label(label="")
        self._error_label.set_line_wrap(True)
        self._error_label.set_max_width_chars(46)
        self._error_label.set_justify(Gtk.Justification.CENTER)
        self._error_label.get_style_context().add_class("empty-text")
        retry = Gtk.Button(label="Try again")
        retry.set_halign(Gtk.Align.CENTER)
        retry.connect("clicked", lambda *_: self._retry_cb and self._retry_cb())
        box.pack_start(self._error_label, False, False, 0)
        box.pack_start(retry, False, False, 0)
        self._stack.add_named(box, "error")

    def _build_content_page(self) -> None:
        outer = Gtk.ScrolledWindow()
        outer.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        outer.set_hexpand(True)
        outer.get_style_context().add_class("details")
        self._content_scroll = outer

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        content.set_margin_start(28)
        content.set_margin_end(28)
        content.set_margin_top(24)
        content.set_margin_bottom(24)
        outer.add(content)

        hero = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=24)
        content.pack_start(hero, False, False, 0)

        self._shots_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._shots_section.set_margin_top(20)
        self._shots_section.pack_start(_section_label("Screenshots"), False, False, 0)
        self._shots_scroll = Gtk.ScrolledWindow()
        self._shots_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        self._shots_scroll.set_min_content_height(104)
        self._shots_scroll.get_style_context().add_class("screenshot-strip")
        self._shots_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._shots_row.set_valign(Gtk.Align.CENTER)
        self._shots_row.set_halign(Gtk.Align.START)
        self._shots_row.set_hexpand(False)
        self._shots_scroll.add(self._shots_row)
        self._shots_section.pack_start(self._shots_scroll, False, False, 0)
        self._shots_section.set_no_show_all(True)
        self._shots_section.hide()
        self._shot_tiles: list[ScreenshotTile] = []
        self._shot_files: list[ArchiveFile] = []
        content.pack_start(self._shots_section, False, False, 0)

        poster_column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        poster_column.set_valign(Gtk.Align.START)
        poster_column.pack_start(_section_label("Poster"), False, False, 0)
        poster_column.pack_start(self._build_poster(), False, False, 0)
        hero.pack_start(poster_column, False, False, 0)

        text_column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        hero.pack_start(text_column, True, True, 0)

        self._title = Gtk.Label(xalign=0)
        self._title.set_line_wrap(True)
        self._title.set_max_width_chars(52)
        self._title.set_selectable(True)
        self._title.get_style_context().add_class("detail-title")
        text_column.pack_start(self._title, False, False, 0)

        self._meta = Gtk.Label(xalign=0)
        self._meta.set_line_wrap(True)
        self._meta.set_max_width_chars(70)
        self._meta.set_selectable(True)
        self._meta.set_margin_top(6)
        self._meta.get_style_context().add_class("meta-line")
        text_column.pack_start(self._meta, False, False, 0)

        links = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        links.set_margin_top(7)
        self._identifier = Gtk.Label(xalign=0)
        self._identifier.set_ellipsize(Pango.EllipsizeMode.END)
        self._identifier.set_max_width_chars(44)
        self._identifier.set_selectable(True)
        self._identifier.get_style_context().add_class("mono")
        self._identifier.set_margin_end(6)
        self._archive_link = Gtk.LinkButton.new_with_label("", "archive.org page")
        self._archive_link.set_relief(Gtk.ReliefStyle.NONE)
        self._archive_link.get_style_context().add_class("small")
        self._license_link = Gtk.LinkButton.new_with_label("", "License")
        self._license_link.set_relief(Gtk.ReliefStyle.NONE)
        self._license_link.get_style_context().add_class("small")
        self._license_link.set_no_show_all(True)
        links.pack_start(self._identifier, True, True, 0)
        links.pack_start(self._archive_link, False, False, 0)
        links.pack_start(self._license_link, False, False, 0)
        text_column.pack_start(links, False, False, 0)

        about = _section_label("About")
        about.set_margin_top(20)
        about.set_margin_bottom(7)
        text_column.pack_start(about, False, False, 0)

        self._description_scroll = Gtk.ScrolledWindow()
        self._description_scroll.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC
        )
        self._description_scroll.set_min_content_height(92)
        self._description_scroll.set_min_content_width(240)
        self._description_scroll.set_max_content_height(200)
        self._description_scroll.set_max_content_width(760)
        self._description_scroll.get_style_context().add_class("description-frame")
        self._description_view = Gtk.Label(xalign=0, yalign=0)
        self._description_view.set_line_wrap(True)
        self._description_view.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self._description_view.set_selectable(True)
        self._description_view.set_margin_start(14)
        self._description_view.set_margin_end(14)
        self._description_view.set_margin_top(10)
        self._description_view.set_margin_bottom(10)
        self._description_view.get_style_context().add_class("description-text")
        self._description_scroll.add(self._description_view)
        text_column.pack_start(self._description_scroll, False, False, 0)

        self._description_empty = Gtk.Label(
            label="No description was provided for this archive item."
        )
        self._description_empty.set_xalign(0)
        self._description_empty.set_line_wrap(True)
        self._description_empty.set_margin_top(10)
        self._description_empty.set_margin_bottom(10)
        self._description_empty.get_style_context().add_class("dim")
        self._description_empty.set_no_show_all(True)
        text_column.pack_start(self._description_empty, False, False, 0)

        self._subjects_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        self._subjects_box.set_margin_top(18)
        self._subjects_box.set_no_show_all(True)
        self._subjects_box.pack_start(_section_label("Subjects"), False, False, 0)
        self._subjects_flow = Gtk.FlowBox()
        self._subjects_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self._subjects_flow.set_column_spacing(6)
        self._subjects_flow.set_row_spacing(6)
        self._subjects_flow.set_max_children_per_line(10)
        self._subjects_flow.set_homogeneous(False)
        self._subjects_flow.set_valign(Gtk.Align.START)
        self._subjects_box.pack_start(self._subjects_flow, False, False, 0)
        content.pack_start(self._subjects_box, False, False, 0)

        rule = Gtk.Separator()
        rule.get_style_context().add_class("rule")
        rule.set_margin_top(22)
        rule.set_margin_bottom(18)
        content.pack_start(rule, False, False, 0)

        files_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        files_header.pack_start(_section_label("Files"), False, False, 0)
        self._files_count = Gtk.Label(label="", xalign=0)
        self._files_count.get_style_context().add_class("file-summary")
        files_header.pack_start(self._files_count, False, False, 0)
        files_header.pack_start(Gtk.Box(), True, True, 0)
        aux_label = Gtk.Label(label="Show auxiliary files")
        aux_label.get_style_context().add_class("dim")
        aux_label.get_style_context().add_class("small")
        self._aux_switch = Gtk.Switch()
        self._aux_switch.set_valign(Gtk.Align.CENTER)
        self._aux_switch.set_tooltip_text(
            "Show archive metadata, screenshots, thumbnails, and torrent files"
        )
        self._aux_switch.connect("notify::active", self._on_aux_toggled)
        files_header.pack_start(aux_label, False, False, 0)
        files_header.pack_start(self._aux_switch, False, False, 0)
        content.pack_start(files_header, False, False, 0)

        self._restricted_note = Gtk.Label(
            label="This archive item is marked restricted. Downloads are unavailable here."
        )
        self._restricted_note.set_xalign(0)
        self._restricted_note.set_line_wrap(True)
        self._restricted_note.set_max_width_chars(72)
        self._restricted_note.set_margin_top(8)
        self._restricted_note.get_style_context().add_class("error-text")
        self._restricted_note.set_no_show_all(True)
        content.pack_start(self._restricted_note, False, False, 0)

        note = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        note.get_style_context().add_class("note-box")
        note.set_margin_top(9)
        note.set_margin_bottom(10)
        note_icon = Gtk.Image.new_from_icon_name(
            "dialog-information-symbolic", Gtk.IconSize.MENU
        )
        note_icon.set_valign(Gtk.Align.START)
        note_text = Gtk.Label(
            label=(
                "Self-extracting installers (.sh) are third-party programs uploaded with "
                "the item. They are not verified native executables and are never run "
                "automatically."
            )
        )
        note_text.set_xalign(0)
        note_text.set_line_wrap(True)
        note_text.set_max_width_chars(74)
        note_text.get_style_context().add_class("note-text")
        note.pack_start(note_icon, False, False, 0)
        note.pack_start(note_text, True, True, 0)
        content.pack_start(note, False, False, 0)

        file_scroller, _tree = self._build_file_table()
        content.pack_start(file_scroller, False, False, 0)

        self._build_local(content)
        self._stack.add_named(outer, "content")

    # -- imported catalog records -----------------------------------------

    def _build_catalog_page(self) -> None:
        """Details for an imported catalog record (not an archive.org item)."""
        outer = Gtk.ScrolledWindow()
        outer.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        content.set_margin_start(26)
        content.set_margin_end(26)
        content.set_margin_top(22)
        content.set_margin_bottom(26)
        outer.add(content)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        self._catalog_poster = Gtk.Image.new_from_icon_name(
            "applications-games-symbolic", Gtk.IconSize.DIALOG
        )
        self._catalog_poster.set_size_request(168, 224)
        self._catalog_poster.set_valign(Gtk.Align.START)
        self._catalog_poster.get_style_context().add_class("poster-frame")
        top.pack_start(self._catalog_poster, False, False, 0)

        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        badge = Gtk.Label(label="Imported catalog record")
        badge.get_style_context().add_class("tile-badge")
        badge.set_halign(Gtk.Align.START)
        self._catalog_title = Gtk.Label(xalign=0)
        self._catalog_title.set_line_wrap(True)
        self._catalog_title.set_max_width_chars(52)
        self._catalog_title.set_selectable(True)
        self._catalog_title.get_style_context().add_class("panel-title")
        self._catalog_subtitle = Gtk.Label(xalign=0)
        self._catalog_subtitle.set_line_wrap(True)
        self._catalog_subtitle.set_selectable(True)
        self._catalog_subtitle.get_style_context().add_class("dim")
        self._catalog_subtitle.get_style_context().add_class("small")
        titles.pack_start(badge, False, False, 0)
        titles.pack_start(self._catalog_title, False, False, 0)
        titles.pack_start(self._catalog_subtitle, False, False, 0)
        top.pack_start(titles, True, True, 0)
        content.pack_start(top, False, False, 0)

        self._catalog_facts: dict[str, Gtk.Label] = {}
        self._catalog_grid = Gtk.Grid(column_spacing=12, row_spacing=6)
        self._catalog_grid.set_margin_top(16)
        for row, name in enumerate(
            (
                "Year", "Developer", "Publisher", "Genres", "Platforms",
                "Version", "Size", "Source", "Provider", "Identifier",
            )
        ):
            caption = Gtk.Label(label=name.upper(), xalign=0)
            caption.set_valign(Gtk.Align.START)
            caption.get_style_context().add_class("fact-name")
            value = Gtk.Label(label="—", xalign=0)
            value.set_line_wrap(True)
            value.set_max_width_chars(58)
            value.set_selectable(True)
            value.get_style_context().add_class("fact-value")
            self._catalog_grid.attach(caption, 0, row, 1, 1)
            self._catalog_grid.attach(value, 1, row, 1, 1)
            self._catalog_facts[name] = value
        content.pack_start(self._catalog_grid, False, False, 0)

        self._catalog_source_btn = Gtk.Button(label="Open source page")
        self._catalog_source_btn.set_halign(Gtk.Align.START)
        self._catalog_source_btn.set_margin_top(12)
        self._catalog_source_btn.set_tooltip_text(
            "Open the source page for this record in your browser"
        )
        self._catalog_source_btn.connect("clicked", self._on_catalog_source)

        self._catalog_record: CatalogGame | None = None
        self._catalog_shot_urls: list[str] = []
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        actions.set_margin_top(12)
        self._catalog_source_btn.set_margin_top(0)
        actions.pack_start(self._catalog_source_btn, False, False, 0)
        self._catalog_sync_btn = Gtk.Button(label="Sync poster & details…")
        self._catalog_sync_btn.set_tooltip_text(
            "Search RAWG by this game's name and attach the poster, description, "
            "and gameplay screenshots it returns"
        )
        self._catalog_sync_btn.connect("clicked", self._on_catalog_sync)
        actions.pack_start(self._catalog_sync_btn, False, False, 0)
        content.pack_start(actions, False, False, 0)

        self._catalog_description = Gtk.Label(xalign=0)
        self._catalog_description.set_line_wrap(True)
        self._catalog_description.set_max_width_chars(78)
        self._catalog_description.set_selectable(True)
        self._catalog_description.set_margin_top(14)
        content.pack_start(self._catalog_description, False, False, 0)

        self._catalog_shots_section = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=6
        )
        self._catalog_shots_section.set_margin_top(16)
        self._catalog_shots_section.pack_start(
            _section_label("Gameplay screenshots"), False, False, 0
        )
        self._catalog_shots_scroll = Gtk.ScrolledWindow()
        self._catalog_shots_scroll.set_policy(
            Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER
        )
        self._catalog_shots_scroll.set_min_content_height(104)
        self._catalog_shots_scroll.get_style_context().add_class("screenshot-strip")
        self._catalog_shots_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8
        )
        self._catalog_shots_row.set_valign(Gtk.Align.CENTER)
        self._catalog_shots_row.set_halign(Gtk.Align.START)
        self._catalog_shots_scroll.add(self._catalog_shots_row)
        self._catalog_shots_section.pack_start(
            self._catalog_shots_scroll, False, False, 0
        )
        self._catalog_shots_section.set_no_show_all(True)
        self._catalog_shots_section.hide()
        content.pack_start(self._catalog_shots_section, False, False, 0)

        # Fields the source supplied that the model has no column for.  Shown as
        # text only: they are data, not links, and nothing here is clickable.
        self._catalog_provider = Gtk.Label(xalign=0)
        self._catalog_provider.set_line_wrap(True)
        self._catalog_provider.set_max_width_chars(78)
        self._catalog_provider.set_selectable(True)
        self._catalog_provider.set_margin_top(12)
        self._catalog_provider.set_no_show_all(True)
        self._catalog_provider.get_style_context().add_class("small")
        self._catalog_provider.get_style_context().add_class("dim")
        content.pack_start(self._catalog_provider, False, False, 0)

        mirrors_heading = _section_label("Mirror records")
        mirrors_heading.set_margin_top(18)
        content.pack_start(mirrors_heading, False, False, 0)
        self._catalog_mirrors = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        content.pack_start(self._catalog_mirrors, False, False, 0)

        magnets_heading = _section_label("Magnet links")
        magnets_heading.set_margin_top(16)
        content.pack_start(magnets_heading, False, False, 0)
        self._catalog_magnets = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        content.pack_start(self._catalog_magnets, False, False, 0)

        self._catalog_note = Gtk.Label(xalign=0)
        self._catalog_note.set_line_wrap(True)
        self._catalog_note.set_max_width_chars(78)
        self._catalog_note.set_margin_top(16)
        self._catalog_note.get_style_context().add_class("dim")
        self._catalog_note.get_style_context().add_class("small")
        self._catalog_note.set_text(
            "This entry comes from an imported catalog. Nothing has been downloaded, "
            "installed, or launched, and mirror records are the source's own claims."
        )
        content.pack_start(self._catalog_note, False, False, 0)

        self._stack.add_named(outer, "catalog")

    def show_catalog(self, game: GameSummary, record: CatalogGame, *,
                     magnets: Sequence[MagnetRecord] = (),
                     mirrors: Sequence[MirrorRecord] = ()) -> None:
        self._catalog_token += 1
        token = self._catalog_token
        self._item = None
        self._catalog_record = record
        self._stack.set_visible_child_name("catalog")

        self._catalog_title.set_text(record.title or game.title)
        subtitle_bits = [
            value for value in (
                record.year,
                record.developer or record.creator,
                game.catalog_source,
            ) if value
        ]
        self._catalog_subtitle.set_text(
            " · ".join(subtitle_bits) or "No further metadata was reported by the source."
        )
        provider_fields = (
            record.extra.get("source_fields")
            if isinstance(record.extra, dict) else None
        )
        provider_fields = provider_fields if isinstance(provider_fields, dict) else {}
        version = record.extra.get("version") if isinstance(record.extra, dict) else ""
        version = version or provider_fields.get("version") or ""
        sizes = []
        install_size = provider_fields.get("size")
        download_size = provider_fields.get("download_size")
        if install_size:
            sizes.append(f"install {install_size}")
        if download_size:
            sizes.append(f"download {download_size}")
        if not sizes:
            estimated = self._owner.catalog_store.estimated_size(record.key)
            if estimated:
                sizes.append(human_size(estimated))
        facts = {
            "Year": record.year or record.release_date,
            "Developer": record.developer,
            "Publisher": record.publisher,
            "Genres": ", ".join(record.genres),
            "Platforms": ", ".join(record.platforms),
            "Version": str(version),
            "Size": " · ".join(sizes),
            "Source": game.catalog_source or record.source_id,
            # Every core field on this page came from this record's provider;
            # the provider's own fields are shown separately below.
            "Provider": (game.catalog_source or record.source_id),
            "Identifier": record.game_id,
        }
        for name, value in facts.items():
            self._catalog_facts[name].set_text(value or "—")
        self._catalog_description.set_text(record.description or "")
        self._catalog_description.set_visible(bool(record.description))
        provider = record.extra.get("source_fields") if isinstance(record.extra, dict) else None
        if isinstance(provider, dict) and provider:
            listed = " · ".join(
                f"{name}: {_provider_value(value)}"
                for name, value in list(provider.items())[:12]
            )
            self._catalog_provider.set_text(
                "Reported by the source (informational — not used as links): " + listed
            )
            self._catalog_provider.set_no_show_all(False)
            self._catalog_provider.show()
        else:
            self._catalog_provider.set_text("")
            self._catalog_provider.hide()

        self._catalog_url = record.source_url
        self._catalog_source_btn.set_visible(bool(record.source_url))
        self._catalog_source_btn.set_tooltip_text(record.source_url or None)

        self._fill_catalog_shots(record)
        self._fill_catalog_mirrors(mirrors)
        self._fill_catalog_magnets(magnets, record.title or game.title)

        # Poster: only from the archive host this client is allowed to fetch.
        self._catalog_poster.set_from_icon_name(
            "applications-games-symbolic", Gtk.IconSize.DIALOG
        )
        if record.cover_url and self._owner.can_fetch_image(
            record.cover_url, record.source_id
        ):
            self._owner.fetch_poster(
                f"{record.source_id}-{record.game_id or 'catalog'}",
                record.cover_url,
                lambda data: self._on_catalog_poster(token, data),
                lambda _exc: self._catalog_poster.set_tooltip_text(
                    "This poster could not be loaded."
                ),
                allowed_hosts=self._owner.image_hosts_for(record.source_id),
            )

    def _fill_catalog_mirrors(self, mirrors: Sequence[MirrorRecord]) -> None:
        for child in list(self._catalog_mirrors.get_children()):
            self._catalog_mirrors.remove(child)
        if not mirrors:
            empty = Gtk.Label(
                label=(
                    "No source links were reported for this game. It is listed "
                    "in the catalog but not downloadable from it."
                ),
                xalign=0,
            )
            empty.set_line_wrap(True)
            empty.set_max_width_chars(78)
            empty.get_style_context().add_class("dim")
            empty.get_style_context().add_class("small")
            self._catalog_mirrors.pack_start(empty, False, False, 0)
        for mirror in mirrors:
            label = mirror.label or mirror.filename or mirror.host or mirror.url
            bits = [label]
            if mirror.size:
                bits.append(human_size(mirror.size))
            if mirror.format:
                bits.append(mirror.format)
            # Never show a link as working unless a check actually proved it.
            bits.append(mirror.status_label())
            if mirror.checksum_value:
                bits.append(
                    f"{mirror.checksum_algorithm or 'checksum'}: "
                    f"{mirror.checksum_value[:16]}…"
                )
            summary = Gtk.Label(label=" · ".join(bits), xalign=0)
            summary.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            summary.set_max_width_chars(92)
            summary.set_tooltip_text(mirror.url)
            summary.get_style_context().add_class("small")
            self._catalog_mirrors.pack_start(summary, False, False, 0)

            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            name = Gtk.Label(label=label, xalign=0)
            name.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            name.set_max_width_chars(68)
            name.set_tooltip_text(label)
            name.get_style_context().add_class("fact-name")
            detail_bits = []
            if mirror.host:
                detail_bits.append(mirror.host)
            if mirror.url_kind:
                detail_bits.append(mirror.url_kind.replace("-", " "))
            if mirror.content_type:
                detail_bits.append(mirror.content_type)
            if detail_bits:
                detail = Gtk.Label(label=" · ".join(detail_bits), xalign=0)
                detail.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
                detail.set_max_width_chars(78)
                detail.get_style_context().add_class("dim")
                detail.get_style_context().add_class("small")
                text.pack_start(detail, False, False, 0)
            url = Gtk.Label(label=mirror.url, xalign=0)
            url.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            url.set_max_width_chars(78)
            url.set_selectable(True)
            url.set_tooltip_text(mirror.url)
            url.get_style_context().add_class("mono")
            url.get_style_context().add_class("small")
            text.pack_start(name, False, False, 0)
            text.pack_start(url, False, False, 0)
            row.pack_start(text, True, True, 0)
            copy = Gtk.Button(label="Copy")
            copy.set_tooltip_text("Copy this source URL exactly")
            copy.connect(
                "clicked", lambda _b, value=mirror.url: self._owner.copy_text(value)
            )
            open_btn = Gtk.Button(label="Open")
            open_btn.set_tooltip_text("Open this link in the default browser")
            open_btn.connect(
                "clicked", lambda _b, value=mirror.url: self._owner.open_external_url(value)
            )
            check = Gtk.Button(label="Check")
            check.set_tooltip_text(
                "Ask the server about this link. Nothing is downloaded."
            )
            check.connect(
                "clicked",
                lambda _b, item=mirror: self._owner.check_catalog_mirror(item),
            )
            row.pack_end(copy, False, False, 0)
            row.pack_end(open_btn, False, False, 0)
            row.pack_end(check, False, False, 0)
            download = Gtk.Button(label="Download")
            if kryoto.is_downloadable_link(mirror.check_status, mirror.url_kind):
                download.set_tooltip_text(
                    "Queue this verified file with the existing download manager"
                )
                download.get_style_context().add_class("suggested-action")
            else:
                download.set_tooltip_text(
                    "Checks this link first and queues it only if the server "
                    "answers with a file. A web page is never downloaded."
                )
            download.connect(
                "clicked",
                lambda _b, item=mirror: self._owner.download_catalog_mirror(
                    item, self._catalog_record.title if self._catalog_record else ""
                ),
            )
            row.pack_end(download, False, False, 0)
            self._catalog_mirrors.pack_start(row, False, False, 0)
        self._catalog_mirrors.show_all()

    def _fill_catalog_magnets(
        self, magnets: Sequence[MagnetRecord], title: str = ""
    ) -> None:
        for child in list(self._catalog_magnets.get_children()):
            self._catalog_magnets.remove(child)
        if not magnets:
            empty = Gtk.Label(
                label="This source did not expose a magnet link for this game.",
                xalign=0,
            )
            empty.get_style_context().add_class("dim")
            empty.get_style_context().add_class("small")
            self._catalog_magnets.pack_start(empty, False, False, 0)
        for record in magnets:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            origin = "built from infohash" if record.origin == "constructed" else "from source"
            label = Gtk.Label(
                label=f"{record.infohash[:16]}… · {origin}", xalign=0
            )
            label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            label.set_tooltip_text(record.uri)
            label.get_style_context().add_class("mono")
            label.get_style_context().add_class("small")
            download = Gtk.Button(
                label="Download with torrent" if len(magnets) == 1 else "Download"
            )
            download.set_tooltip_text(
                "Start this magnet with the torrent engine. The link is not "
                "verified; seeding is off."
            )
            if len(magnets) == 1:
                download.get_style_context().add_class("suggested-action")
            download.connect("clicked", self._on_catalog_download, record, title)
            copy = Gtk.Button(label="Copy")
            copy.set_tooltip_text("Copy the magnet link")
            copy.connect("clicked", self._on_copy_catalog_magnet, record.uri)
            open_btn = Gtk.Button(label="Open…")
            open_btn.set_tooltip_text(
                "Hand the magnet link to the desktop's magnet handler"
            )
            open_btn.connect("clicked", self._on_open_catalog_magnet, record.uri)
            row.pack_start(label, True, True, 0)
            row.pack_end(download, False, False, 0)
            row.pack_end(copy, False, False, 0)
            row.pack_end(open_btn, False, False, 0)
            self._catalog_magnets.pack_start(row, False, False, 0)
        self._catalog_magnets.show_all()

    def _on_catalog_download(
        self, _button: Gtk.Button, record: MagnetRecord, title: str
    ) -> None:
        self._owner.start_torrent_download(record, title)

    def _on_open_catalog_magnet(self, _button: Gtk.Button, uri: str) -> None:
        self._owner.open_magnet_uri(uri)

    def _on_copy_catalog_magnet(self, _button: Gtk.Button, uri: str) -> None:
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(uri, -1)
        clipboard.store()
        self._owner.toast("Magnet link copied to the clipboard.")

    def _on_catalog_source(self, _button: Gtk.Button) -> None:
        if not self._catalog_url:
            return
        if not platform_support.open_url(self._catalog_url):
            self._owner.toast("No browser answered for this source page.", error=True)

    def _on_catalog_sync(self, _button: Gtk.Button) -> None:
        if self._catalog_record is not None:
            self._owner.sync_catalog_artwork(self._catalog_record)

    def _fill_catalog_shots(self, record: CatalogGame) -> None:
        """Show the gameplay screenshots a sync (or the source) stored."""
        for child in list(self._catalog_shots_row.get_children()):
            self._catalog_shots_row.remove(child)
        self._catalog_shot_urls = []
        stored = record.extra.get("screenshots") if isinstance(record.extra, dict) else None
        allowed = self._owner.image_hosts_for(record.source_id)
        urls = [
            url for url in (stored if isinstance(stored, list) else [])
            if isinstance(url, str)
            and self._owner.can_fetch_image(url, record.source_id)
        ][:_MAX_SCREENSHOTS]
        if not urls:
            self._catalog_shots_section.hide()
            return
        self._catalog_shot_urls = urls
        for index, url in enumerate(urls):
            tile = ScreenshotTile(index, self._open_catalog_shot)
            self._catalog_shots_row.pack_start(tile, False, False, 0)
            self._owner.fetch_poster(
                f"catalog-shot-{record.key}-{index}",
                url,
                lambda data, t=tile: self._on_catalog_shot_loaded(t, data),
                lambda _exc, t=tile: t.hide(),
                allowed_hosts=allowed,
            )
        self._catalog_shots_section.set_no_show_all(False)
        self._catalog_shots_section.show_all()
        self._catalog_shots_section.set_no_show_all(True)

    def _on_catalog_shot_loaded(self, tile: ScreenshotTile, data: object) -> None:
        if not isinstance(data, (bytes, bytearray)):
            tile.hide()
            return
        try:
            pixbuf = self._decode_poster(bytes(data))
        except (GLib.Error, ValueError, TypeError):
            pixbuf = None
        if pixbuf is None:
            tile.hide()
            return
        tile.set_pixbuf(pixbuf)

    def _open_catalog_shot(self, index: int) -> None:
        if 0 <= index < len(self._catalog_shot_urls):
            if not platform_support.open_url(self._catalog_shot_urls[index]):
                self._owner.toast(
                    "No browser answered for this screenshot.", error=True
                )

    def _on_catalog_poster(self, token: int, data: object) -> None:
        if token != self._catalog_token or not isinstance(data, (bytes, bytearray)):
            return
        try:
            loader = GdkPixbuf.PixbufLoader()
            loader.write(data)
            loader.close()
            pixbuf = loader.get_pixbuf()
        except (GLib.Error, TypeError, ValueError):
            return
        if pixbuf is None:
            return
        width, height = pixbuf.get_width(), pixbuf.get_height()
        if width <= 0 or height <= 0:
            return
        scale = min(168 / width, 224 / height)
        scaled = pixbuf.scale_simple(
            max(1, int(width * scale)),
            max(1, int(height * scale)),
            GdkPixbuf.InterpType.BILINEAR,
        )
        if scaled is not None:
            self._catalog_poster.set_from_pixbuf(scaled)

    def _build_poster(self) -> Gtk.Widget:
        stack = Gtk.Stack()
        stack.set_size_request(self.POSTER_WIDTH, self.POSTER_HEIGHT)
        stack.set_halign(Gtk.Align.CENTER)
        stack.set_valign(Gtk.Align.START)
        stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        stack.set_transition_duration(140)
        stack.get_style_context().add_class("poster-frame")
        self._poster_stack = stack

        loading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        loading.set_halign(Gtk.Align.CENTER)
        loading.set_valign(Gtk.Align.CENTER)
        spinner = Gtk.Spinner()
        spinner.set_size_request(22, 22)
        spinner.get_style_context().add_class("dim")
        spinner.start()
        loading.pack_start(spinner, False, False, 0)
        stack.add_named(loading, "loading")

        self._poster_area = Gtk.DrawingArea()
        self._poster_area.connect("draw", self._draw_poster)
        stack.add_named(self._poster_area, "image")

        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9)
        empty.set_halign(Gtk.Align.CENTER)
        empty.set_valign(Gtk.Align.CENTER)
        empty.set_margin_start(16)
        empty.set_margin_end(16)
        icon = Gtk.Image.new_from_icon_name(
            "image-missing-symbolic", Gtk.IconSize.DIALOG
        )
        icon.get_style_context().add_class("dim")
        empty_text = Gtk.Label(label="No poster available")
        empty_text.set_line_wrap(True)
        empty_text.set_justify(Gtk.Justification.CENTER)
        empty_text.set_max_width_chars(20)
        empty_text.get_style_context().add_class("poster-empty-text")
        empty.pack_start(icon, False, False, 0)
        empty.pack_start(empty_text, False, False, 0)
        stack.add_named(empty, "empty")

        stack.set_visible_child_name("loading")
        return stack

    def _draw_poster(self, area: Gtk.DrawingArea, cr) -> bool:
        alloc = area.get_allocation()
        pixbuf = self._poster_pixbuf
        if pixbuf is None:
            if self._poster_title and alloc.width > 0 and alloc.height > 0:
                paint_monogram(self, cr, alloc.width, alloc.height,
                               self._poster_title)
            return False
        width, height = pixbuf.get_width(), pixbuf.get_height()
        if width <= 0 or height <= 0:
            return False
        x = (alloc.width - width) / 2.0
        y = (alloc.height - height) / 2.0
        _rounded_rect(cr, x, y, width, height, 10.0)
        cr.clip()
        Gdk.cairo_set_source_pixbuf(cr, pixbuf, x, y)
        cr.paint()
        return False

    # -- poster -------------------------------------------------------------

    def _poster_dimensions(self, pixbuf: GdkPixbuf.Pixbuf | None = None) -> tuple[int, int]:
        """Poster box from the available width and the picture's own aspect.

        Tall artwork gets a tall box instead of being letterboxed into a fixed
        portrait frame, so archive posters keep their natural shape.
        """
        allocation = self.get_allocation()
        pane_width = max(0, allocation.width)
        content_width = max(0, pane_width - 56)
        if content_width <= 0:
            width = self.POSTER_WIDTH
        else:
            width = int(round(content_width * 0.30))
            width = max(190, min(360, width))
        ratio = self.POSTER_HEIGHT / self.POSTER_WIDTH
        if pixbuf is not None and pixbuf.get_width() > 0 and pixbuf.get_height() > 0:
            ratio = pixbuf.get_height() / pixbuf.get_width()
            height = int(round(width * ratio))
            return width, max(120, min(720, height))
        if content_width <= 0:
            return width, self.POSTER_HEIGHT
        height = int(round(content_width * 0.52))
        height = max(300, min(470, height))
        return width, height

    def _sync_poster_box(self) -> None:
        box = self._poster_dimensions(self._poster_raw)
        if abs(box[0] - self._poster_box[0]) < 2 and abs(
            box[1] - self._poster_box[1]
        ) < 2:
            return
        self._poster_box = box
        self._poster_stack.set_size_request(box[0], box[1])

    def _on_details_resize(self, _widget: Gtk.Widget,
                           _allocation: Gdk.Rectangle) -> None:
        before = self._poster_box
        self._sync_poster_box()
        if self._poster_box != before:
            self._render_poster()

    def _load_poster(self, item: ArchiveItem) -> None:
        self._poster_token += 1
        token = self._poster_token
        self._poster_box = self._poster_dimensions()
        self._poster_stack.set_size_request(*self._poster_box)
        self._poster_fallback_tried = False
        url = item.poster_url
        cached = self._poster_cache.get(item.identifier)
        if cached is None:
            cached = self._poster_cache.get(url)
        if cached is not None:
            self._poster_raw = cached
            self._sync_poster_box()
            self._render_poster()
            return

        self._poster_raw = None
        self._poster_pixbuf = None
        self._poster_stack.set_visible_child_name("loading")
        self._owner.fetch_poster(
            item.identifier,
            url,
            lambda data: self._on_poster_loaded(token, item, data),
            lambda _exc: self._on_poster_failed(token, item),
        )

    def _on_poster_loaded(self, token: int, item: ArchiveItem, data: object) -> None:
        if token != self._poster_token or self._item is None:
            return
        if self._item.identifier != item.identifier:
            return
        if not isinstance(data, (bytes, bytearray)):
            self._show_poster_empty()
            return
        try:
            pixbuf = self._decode_poster(bytes(data))
        except (GLib.Error, ValueError, TypeError):
            pixbuf = None
        if pixbuf is None:
            self._show_poster_empty()
            return
        self._poster_cache[item.identifier] = pixbuf
        self._poster_cache[item.poster_url] = pixbuf
        self._poster_raw = pixbuf
        self._sync_poster_box()
        self._render_poster()

    def _on_poster_failed(self, token: int, item: ArchiveItem) -> None:
        if token != self._poster_token or self._item is None:
            return
        if self._item.identifier != item.identifier:
            return
        # archive.org's generated thumbnail occasionally returns HTTP 500;
        # fall back to the item's own first image before giving up.
        if not self._poster_fallback_tried:
            poster = item.poster_file
            if poster is not None and poster.name:
                self._poster_fallback_tried = True
                fallback = item.file_url(poster)
                self._owner.fetch_poster(
                    f"{item.identifier}-poster-fallback",
                    fallback,
                    lambda data: self._on_poster_loaded(token, item, data),
                    lambda _exc: self._show_poster_empty(),
                )
                return
        self._show_poster_empty()

    def _decode_poster(self, data: bytes) -> GdkPixbuf.Pixbuf | None:
        loader = GdkPixbuf.PixbufLoader()
        try:
            loader.write(data)
            loader.close()
        except GLib.Error:
            return None
        return loader.get_pixbuf()

    def _fit_poster(self, pixbuf: GdkPixbuf.Pixbuf) -> GdkPixbuf.Pixbuf | None:
        width, height = pixbuf.get_width(), pixbuf.get_height()
        if width <= 0 or height <= 0:
            return None
        box_width, box_height = self._poster_box
        scale = min(box_width / width, box_height / height, 1.0)
        if scale >= 1.0:
            return pixbuf
        new_width = max(1, int(round(width * scale)))
        new_height = max(1, int(round(height * scale)))
        return pixbuf.scale_simple(
            new_width, new_height, GdkPixbuf.InterpType.BILINEAR
        )

    def _render_poster(self) -> None:
        raw = self._poster_raw
        if raw is None:
            return
        fitted = self._fit_poster(raw)
        if fitted is None:
            self._show_poster_empty()
            return
        self._poster_pixbuf = fitted
        self._poster_stack.set_visible_child_name("image")
        self._poster_area.queue_draw()

    def _show_poster(self, pixbuf: GdkPixbuf.Pixbuf) -> None:
        self._poster_raw = pixbuf
        self._render_poster()

    def _show_poster_empty(self) -> None:
        self._poster_raw = None
        self._poster_pixbuf = None
        if self._poster_title:
            self._poster_stack.set_visible_child_name("image")
            self._poster_area.queue_draw()
        else:
            self._poster_stack.set_visible_child_name("empty")

    # -- screenshots --------------------------------------------------------

    def _clear_screenshots(self) -> None:
        self._shot_token += 1
        for child in list(self._shots_row.get_children()):
            self._shots_row.remove(child)
        self._shot_tiles = []
        self._shot_files = []
        self._shots_section.hide()

    def _load_screenshots(self, item: ArchiveItem) -> None:
        self._shot_token += 1
        token = self._shot_token
        for child in list(self._shots_row.get_children()):
            self._shots_row.remove(child)
        self._shot_tiles = []
        try:
            files = list(item.screenshot_files)[:_MAX_SCREENSHOTS]
        except (AttributeError, TypeError):
            files = []
        self._shot_files = files
        self._shot_loaded = 0
        self._shot_failed = 0
        if not files:
            self._shots_section.hide()
            return

        for index, archive_file in enumerate(files):
            tile = ScreenshotTile(index, self._open_lightbox)
            self._shots_row.pack_start(tile, False, False, 0)
            self._shot_tiles.append(tile)
            url = item.file_url(archive_file)
            cached = self._shot_cache.get(url)
            if cached is not None:
                tile.set_pixbuf(cached)
                continue
            self._owner.fetch_poster(
                f"{item.identifier}-thumb-{index}",
                url,
                lambda data, t=tile, u=url, tok=token:
                    self._on_shot_loaded(tok, u, t, data),
                lambda _exc, t=tile, tok=token:
                    self._on_shot_failed(tok, t),
            )

        self._shots_section.set_no_show_all(False)
        self._shots_section.show_all()
        self._shots_section.set_no_show_all(True)

    def _on_shot_loaded(self, token: int, url: str, tile: ScreenshotTile,
                        data: object) -> None:
        if token != self._shot_token or self._item is None:
            return
        if not isinstance(data, (bytes, bytearray)):
            return
        try:
            pixbuf = self._decode_poster(bytes(data))
        except (GLib.Error, ValueError, TypeError):
            pixbuf = None
        if pixbuf is None:
            return
        self._shot_cache[url] = pixbuf
        self._shot_loaded += 1
        tile.set_pixbuf(pixbuf)

    def _on_shot_failed(self, token: int, tile: ScreenshotTile) -> None:
        """Drop a thumbnail that could not load instead of leaving a blank box."""
        if token != self._shot_token:
            return
        self._shot_failed += 1
        if tile.get_parent() is self._shots_row:
            self._shots_row.remove(tile)
        if self._shot_loaded == 0 and self._shot_failed >= len(self._shot_files):
            self._shots_section.hide()

    def _open_lightbox(self, index: int) -> None:
        if self._item is None or not self._shot_files:
            return
        window = self.get_toplevel()
        if not isinstance(window, Gtk.Window):
            return
        dialog = ScreenshotLightbox(
            window, self._item, self._shot_files, index,
            self._owner.fetch_poster,
        )
        self._lightbox = dialog
        dialog.run()
        self._lightbox = None


    def _build_selection_bar(self) -> None:
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        bar.get_style_context().add_class("selection-dock")
        bar.set_no_show_all(True)
        self._selection_bar = bar
        self._selected_label = Gtk.Label(label="No files selected", xalign=0)
        self._selected_label.get_style_context().add_class("selection-count")
        bar.pack_start(self._selected_label, False, False, 0)
        hint = Gtk.Label(label="Ctrl-click to select several files")
        hint.get_style_context().add_class("dim")
        hint.get_style_context().add_class("small")
        bar.pack_start(hint, False, False, 0)
        bar.pack_start(Gtk.Box(), True, True, 0)
        self._download_btn = Gtk.Button(label="Download selected…")
        self._download_btn.get_style_context().add_class("suggested-action")
        self._download_btn.set_tooltip_text(
            "Download the selected files into the chosen download folder"
        )
        self._download_btn.set_sensitive(False)
        self._download_btn.connect("clicked", self._on_download_clicked)
        bar.pack_start(self._download_btn, False, False, 0)
        self.pack_start(bar, False, False, 0)

    def _build_file_table(self) -> tuple[Gtk.ScrolledWindow, Gtk.TreeView]:
        store = Gtk.ListStore(str, str, str, str, str, bool, bool, str, str)
        self._store = store
        self._file_filter = Gtk.TreeModelFilter(child_model=store)
        self._file_filter.set_visible_func(self._file_visible)

        tree = Gtk.TreeView(model=self._file_filter)
        self._file_tree = tree
        tree.set_headers_visible(True)
        tree.set_enable_search(False)
        tree.set_has_tooltip(True)
        tree.connect("query-tooltip", self._on_file_tooltip)
        tree.connect("row-activated", self._on_file_activated)
        selection = tree.get_selection()
        selection.set_mode(Gtk.SelectionMode.MULTIPLE)
        selection.connect("changed", self._on_file_selection_changed)

        name_renderer = Gtk.CellRendererText()
        name_column = Gtk.TreeViewColumn("Name", name_renderer, text=self.COL_NAME)
        name_column.set_expand(True)
        name_column.set_min_width(170)
        name_column.set_resizable(True)
        name_column.set_cell_data_func(name_renderer, self._name_cell_data)
        tree.append_column(name_column)

        for title, column_id, width, align in (
            ("Type", self.COL_KIND, 112, 0.0),
            ("Size", self.COL_SIZE, 76, 1.0),
            ("Format", self.COL_FORMAT, 100, 0.0),
        ):
            renderer = Gtk.CellRendererText()
            renderer.set_property("ellipsize", Pango.EllipsizeMode.END)
            renderer.set_property("xalign", align)
            column = Gtk.TreeViewColumn(title, renderer, text=column_id)
            column.set_min_width(width)
            column.set_resizable(True)
            column.set_sizing(Gtk.TreeViewColumnSizing.FIXED)
            tree.append_column(column)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_min_content_height(170)
        scroller.set_max_content_height(300)
        scroller.get_style_context().add_class("file-frame")
        scroller.add(tree)
        return scroller, tree

    # -- visibility ---------------------------------------------------------

    def show_empty(self) -> None:
        self._item = None
        self._poster_token += 1
        self._clear_screenshots()
        self._selection_bar.hide()
        self._stack.set_visible_child_name("empty")

    def show_loading(self, title: str) -> None:
        self._poster_token += 1
        self._clear_screenshots()
        self._selection_bar.hide()
        self._loading_game.set_text(title)
        self._stack.set_visible_child_name("loading")

    def show_error(self, message: str, retry: Callable[[], None] | None) -> None:
        self._poster_token += 1
        self._clear_screenshots()
        self._selection_bar.hide()
        self._error_label.set_text(message)
        self._retry_cb = retry
        self._stack.set_visible_child_name("error")

    def _show_selection_bar(self) -> None:
        # no-show-all keeps the dock hidden during window-wide show_all calls;
        # it has to be lifted briefly so the dock itself is actually shown.
        self._selection_bar.set_no_show_all(False)
        self._selection_bar.show_all()
        self._selection_bar.set_no_show_all(True)

    def show_item(self, item: ArchiveItem) -> None:
        self._item = item
        self._poster_title = item.title
        self._title.set_text(item.title)
        self._title.set_tooltip_text(item.title)

        bits: list[str] = []
        if item.creator:
            bits.append(item.creator)
        if item.date:
            bits.append(item.date)
        elif item.year:
            bits.append(item.year)
        if item.item_size:
            bits.append(human_size(item.item_size))
        self._meta.set_text(" · ".join(bits) or "No creator or date listed.")
        self._identifier.set_text(item.identifier)
        self._identifier.set_tooltip_text(item.identifier)
        self._archive_link.set_uri(item.download_url)

        if item.licenseurl and item.licenseurl.startswith(("http://", "https://")):
            self._license_link.set_uri(item.licenseurl)
            self._license_link.set_tooltip_text(item.licenseurl)
            self._license_link.show()
        else:
            self._license_link.hide()

        for child in self._subjects_flow.get_children():
            child.destroy()
        if item.subjects:
            for subject in item.subjects[:24]:
                label = Gtk.Label(label=subject)
                label.set_ellipsize(Pango.EllipsizeMode.END)
                label.set_max_width_chars(40)
                label.get_style_context().add_class("subject-pill")
                label.set_tooltip_text(subject)
                self._subjects_flow.add(label)
            self._subjects_box.show_all()
        else:
            self._subjects_box.hide()

        self._description_view.set_text(item.description or "")
        if item.description:
            self._description_scroll.show()
            self._description_empty.hide()
        else:
            self._description_scroll.hide()
            self._description_empty.show()

        self._restricted_note.set_visible(item.restricted)
        self._populate_files(item)
        self._load_poster(item)
        self._load_screenshots(item)
        self._show_selection_bar()
        self._stack.set_visible_child_name("content")
        GLib.idle_add(self._reset_scroll)
        GLib.timeout_add(120, self._reset_scroll)
        GLib.timeout_add(420, self._reset_scroll)

    def _reset_scroll(self) -> bool:
        adjustment = self._content_scroll.get_vadjustment()
        if adjustment is not None:
            adjustment.set_value(0.0)
        inner = self._description_scroll.get_vadjustment()
        if inner is not None:
            inner.set_value(0.0)
        return False

    def show_launch_status(self, text: str, error: bool = False) -> None:
        self._local_status.set_text(text)
        self._local_status.set_tooltip_text(text)
        context = self._local_status.get_style_context()
        if error:
            context.add_class("error-text")
        else:
            context.remove_class("error-text")
        self._local_status.show()

    # -- file table ---------------------------------------------------------

    def _populate_files(self, item: ArchiveItem) -> None:
        self._store.clear()
        self._files_by_name = {f.name: f for f in item.files}
        self._file_tree.get_selection().unselect_all()
        for archive_file in item.files:
            downloadable = archive_file.downloadable and not item.restricted
            kind = archive_file.kind
            if not archive_file.downloadable:
                kind += " · private"
            self._store.append([
                archive_file.name,
                kind,
                human_size(archive_file.size),
                archive_file.format or "—",
                archive_file.name,
                downloadable,
                archive_file.auxiliary,
                archive_file.md5,
                archive_file.sha1,
            ])
        self._file_filter.refilter()
        self._update_file_summary()
        self._update_selection_state()

    def _file_visible(self, model: Gtk.TreeModel, tree_iter: Gtk.TreeIter, _data=None) -> bool:
        if self._aux_switch.get_active():
            return True
        return not model.get_value(tree_iter, self.COL_AUX)

    def _on_aux_toggled(self, _switch: Gtk.Switch, _pspec) -> None:
        if not hasattr(self, "_file_filter"):
            return
        self._file_filter.refilter()
        self._update_file_summary()
        self._update_selection_state()

    def _update_file_summary(self) -> None:
        total = len(self._files_by_name)
        visible = total if self._aux_switch.get_active() else sum(
            1 for f in self._files_by_name.values() if not f.auxiliary
        )
        if visible == total:
            self._files_count.set_text(f"{total} {_plural(total, 'file')}")
        else:
            self._files_count.set_text(f"{visible} of {total} files")

    def _name_cell_data(self, _column, renderer, model, tree_iter, _data) -> None:
        downloadable = model.get_value(tree_iter, self.COL_DL)
        renderer.set_property("sensitive", bool(downloadable))
        renderer.set_property("ellipsize", Pango.EllipsizeMode.MIDDLE)

    def _selectable_files(self) -> list[ArchiveFile]:
        if self._item is None:
            return []
        model = self._file_tree.get_model()
        if model is None:
            return []
        rows = self._file_tree.get_selection().get_selected_rows()[1]
        files: list[ArchiveFile] = []
        for path in rows:
            tree_iter = model.get_iter(path)
            if not model.get_value(tree_iter, self.COL_DL):
                continue
            raw = model.get_value(tree_iter, self.COL_RAW)
            archive_file = self._files_by_name.get(raw)
            if archive_file is not None:
                files.append(archive_file)
        return files

    def _update_selection_state(self) -> None:
        count = len(self._selectable_files())
        if count:
            self._selected_label.set_text(f"{count} {_plural(count, 'file')} selected")
        else:
            self._selected_label.set_text("No files selected")
        allowed = (
            count > 0
            and self._item is not None
            and not self._item.restricted
        )
        self._download_btn.set_sensitive(allowed)
        if self._item is not None and self._item.restricted:
            self._download_btn.set_tooltip_text("This item is restricted.")
        elif count == 0:
            self._download_btn.set_tooltip_text(
                "Select one or more downloadable files first"
            )
        else:
            self._download_btn.set_tooltip_text(
                "Download the selected files into the chosen download folder"
            )

    def _on_file_selection_changed(self, _selection: Gtk.TreeSelection) -> None:
        self._update_selection_state()

    def _on_file_activated(self, tree: Gtk.TreeView, path: Gtk.TreePath, _column) -> None:
        if self._item is None:
            return
        model = tree.get_model()
        tree_iter = model.get_iter(path)
        if not model.get_value(tree_iter, self.COL_DL):
            return
        raw = model.get_value(tree_iter, self.COL_RAW)
        archive_file = self._files_by_name.get(raw)
        if archive_file is not None:
            self._owner.download_files(self._item, [archive_file])

    def _on_download_clicked(self, _button: Gtk.Button) -> None:
        if self._item is None:
            return
        files = self._selectable_files()
        if files:
            self._owner.download_files(self._item, files)

    def _on_file_tooltip(self, tree: Gtk.TreeView, x: int, y: int,
                         keyboard_mode: bool, tooltip: Gtk.Tooltip) -> bool:
        if keyboard_mode:
            return False
        info = tree.get_path_at_pos(int(x), int(y))
        if not info:
            return False
        path, _column, _cell_x, _cell_y = info
        model = tree.get_model()
        try:
            tree_iter = model.get_iter(path)
        except (ValueError, TypeError):
            return False
        name = model.get_value(tree_iter, self.COL_RAW)
        kind = model.get_value(tree_iter, self.COL_KIND)
        size = model.get_value(tree_iter, self.COL_SIZE)
        md5 = model.get_value(tree_iter, self.COL_MD5)
        sha1 = model.get_value(tree_iter, self.COL_SHA1)
        lines = [name, f"{kind} · {size}"]
        if md5:
            lines.append(f"MD5: {md5}")
        if sha1:
            lines.append(f"SHA-1: {sha1}")
        if not model.get_value(tree_iter, self.COL_DL):
            lines.append("Not downloadable from this app.")
        tooltip.set_text("\n".join(lines))
        return True

    # -- downloaded files ---------------------------------------------------

    def _build_local(self, content: Gtk.Box) -> None:
        self._local_expander = Gtk.Expander(label="Downloaded files")
        self._local_expander.get_style_context().add_class("local-frame")
        self._local_expander.set_margin_top(2)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        body.set_margin_start(12)
        body.set_margin_end(12)
        body.set_margin_top(10)
        body.set_margin_bottom(11)

        self._local_empty = Gtk.Label(
            label="No files from this archive item have been downloaded yet."
        )
        self._local_empty.set_xalign(0)
        self._local_empty.set_line_wrap(True)
        self._local_empty.get_style_context().add_class("dim")
        self._local_empty.get_style_context().add_class("small")
        self._local_empty.set_no_show_all(True)
        body.pack_start(self._local_empty, False, False, 0)

        self._local_store = Gtk.ListStore(
            str, str, str, str, bool
        )
        self._local_tree = Gtk.TreeView(model=self._local_store)
        self._local_tree.set_headers_visible(True)
        self._local_tree.set_enable_search(False)
        local_selection = self._local_tree.get_selection()
        local_selection.set_mode(Gtk.SelectionMode.SINGLE)
        local_selection.connect("changed", self._on_local_selection_changed)
        self._local_tree.connect("row-activated", self._on_local_activated)

        local_renderer = Gtk.CellRendererText()
        local_renderer.set_property("ellipsize", Pango.EllipsizeMode.MIDDLE)
        local_name = Gtk.TreeViewColumn(
            "File", local_renderer, text=self.LOCAL_NAME
        )
        local_name.set_expand(True)
        local_name.set_resizable(True)
        local_name.set_min_width(180)
        self._local_tree.append_column(local_name)

        kind_renderer = Gtk.CellRendererText()
        kind_renderer.set_property("ellipsize", Pango.EllipsizeMode.END)
        kind_column = Gtk.TreeViewColumn(
            "Detected type", kind_renderer, text=self.LOCAL_KIND
        )
        kind_column.set_resizable(True)
        kind_column.set_min_width(110)
        self._local_tree.append_column(kind_column)

        size_renderer = Gtk.CellRendererText()
        size_renderer.set_property("xalign", 1.0)
        size_column = Gtk.TreeViewColumn(
            "Size", size_renderer, text=self.LOCAL_SIZE
        )
        size_column.set_resizable(True)
        size_column.set_min_width(78)
        self._local_tree.append_column(size_column)

        self._local_scroll = Gtk.ScrolledWindow()
        self._local_scroll.set_policy(
            Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC
        )
        self._local_scroll.set_min_content_height(84)
        self._local_scroll.set_max_content_height(170)
        self._local_scroll.add(self._local_tree)
        self._local_scroll.set_no_show_all(True)
        body.pack_start(self._local_scroll, False, False, 0)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._open_folder_btn = Gtk.Button(label="Open folder")
        self._open_folder_btn.connect("clicked", self._on_open_folder)
        self._launch_btn = Gtk.Button(label="Review & launch…")
        self._launch_btn.set_sensitive(False)
        self._launch_btn.set_tooltip_text("Select a launchable downloaded file first")
        self._launch_btn.connect("clicked", self._on_launch)
        self._launch_btn.set_no_show_all(True)
        buttons.pack_start(self._open_folder_btn, False, False, 0)
        buttons.pack_start(self._launch_btn, False, False, 0)
        buttons.pack_start(Gtk.Box(), True, True, 0)
        body.pack_start(buttons, False, False, 0)

        self._local_status = Gtk.Label(label="", xalign=0)
        self._local_status.set_line_wrap(True)
        self._local_status.set_selectable(True)
        self._local_status.set_max_width_chars(90)
        self._local_status.get_style_context().add_class("dim")
        self._local_status.get_style_context().add_class("small")
        self._local_status.set_no_show_all(True)
        body.pack_start(self._local_status, False, False, 0)

        self._local_expander.add(body)
        content.pack_start(self._local_expander, False, False, 0)

        self._local_files: dict[str, LocalFile] = {}

    def set_local(self, folder: GameFolder, path: Path) -> None:
        self._local_folder = path
        self._local_files = {str(f.path): f for f in folder.files}
        self._local_store.clear()
        self._local_status.hide()

        for local_file in folder.files:
            try:
                shown = str(local_file.path.relative_to(path))
            except ValueError:
                shown = local_file.path.name
            self._local_store.append([
                shown,
                local_file.kind,
                human_size(local_file.size),
                str(local_file.path),
                local_file.launchable,
            ])

        total = len(folder.files)
        self._local_expander.set_label(
            f"Downloaded files ({total})" if total else "Downloaded files"
        )
        if total:
            self._local_scroll.show()
            self._local_launch_visible(True)
            self._local_empty.hide()
            self._local_expander.set_expanded(True)
            first_launchable = next(
                (i for i, f in enumerate(folder.files) if f.launchable), None
            )
            if first_launchable is not None:
                path_iter = self._local_store.get_iter(
                    Gtk.TreePath.new_from_indices([first_launchable])
                )
                self._local_tree.get_selection().select_iter(path_iter)
        else:
            self._local_scroll.hide()
            self._local_launch_visible(False)
            self._local_empty.show()
            self._local_expander.set_expanded(False)
        self._update_launch_button()

    def _local_launch_visible(self, visible: bool) -> None:
        if visible:
            self._launch_btn.show()
        else:
            self._launch_btn.hide()

    def _selected_local_file(self) -> LocalFile | None:
        model, tree_iter = self._local_tree.get_selection().get_selected()
        if tree_iter is None:
            return None
        path = model.get_value(tree_iter, self.LOCAL_PATH)
        return self._local_files.get(path)

    def _update_launch_button(self) -> None:
        local_file = self._selected_local_file()
        launchable = bool(local_file and local_file.launchable)
        self._launch_btn.set_sensitive(launchable)
        if launchable and local_file is not None:
            self._launch_btn.set_tooltip_text(
                f"Review the warning, then launch {local_file.path.name}"
            )
        else:
            self._launch_btn.set_tooltip_text("Select a launchable downloaded file first")

    def _on_local_selection_changed(self, _selection: Gtk.TreeSelection) -> None:
        self._update_launch_button()

    def _on_local_activated(self, _tree: Gtk.TreeView, _path: Gtk.TreePath,
                            _column) -> None:
        local_file = self._selected_local_file()
        if local_file is not None and local_file.launchable:
            self._owner.review_launch(local_file)

    def _on_open_folder(self, _button: Gtk.Button) -> None:
        if self._local_folder is not None:
            self._owner.open_folder(self._local_folder)

    def _on_launch(self, _button: Gtk.Button) -> None:
        local_file = self._selected_local_file()
        if local_file is not None and local_file.launchable:
            self._owner.review_launch(local_file)


# -- main window -------------------------------------------------------------


class MainWindow(Gtk.ApplicationWindow):
    """Single-window browser for the collection, downloads, and local files."""

    def __init__(self, application: Gtk.Application, client: ArchiveClient,
                 manager: DownloadManager, settings: Settings,
                 download_dir: Path, library_store: LibraryStore,
                 startup_warning: str | None = None,
                 catalog_store: CatalogStore | None = None) -> None:
        super().__init__(application=application)
        self._client = client
        self._manager = manager
        self.client = client
        self.manager = manager
        self._settings = settings
        self.settings = settings
        self._download_dir = download_dir
        self._startup_warning = startup_warning
        self.library_store = library_store
        try:
            self._session_profile = profiles.active_profile().name
        except Exception:  # noqa: BLE001 - a header label must never break
            self._session_profile = ""
        self.catalog_store = catalog_store if catalog_store is not None else CatalogStore()
        self.library_lock = threading.Lock()

        self.torrents = TorrentManager(
            platform_support.data_dir() / "torrents.json",
            download_dir,
            engine_path=settings.torrent_client,
            on_complete=self._torrent_completed_from_thread,
        )
        self._torrent_timer = 0
        self._torrent_refreshing = False
        self._torrent_finished: set[str] = set()

        self._games: list[GameSummary] = []
        self._games_by_id: dict[str, GameSummary] = {}
        self._catalog_games: list[GameSummary] = []
        self._catalog_records: dict[str, CatalogGame] = {}
        self._items: dict[str, ArchiveItem] = {}
        self._visible_ids: set[str] = set()
        self._visible_games: list[GameSummary] = []
        self._shown_limit = self.ROW_LIMIT
        self._sort_rank: dict[str, int] = {}
        self._current_game: GameSummary | None = None
        self._current_item: ArchiveItem | None = None
        self._item_token = 0
        self._local_token = 0
        self._search_source = 0
        self._selection_source = 0
        self._collection_loading = False
        self._collection_error: str | None = None
        self._closing = False
        self._archive_lock = threading.Lock()
        self.archive_lock = self._archive_lock
        # Images are independent of item/collection metadata, so they get their
        # own small pool instead of queueing behind the archive lock.  Requests
        # for the same URL share one download.
        self._image_slots = threading.Semaphore(_IMAGE_CONCURRENCY)
        self._image_inflight: dict[str, list[tuple[Callable[[object], None],
                                                   Callable[[object], None]]]] = {}
        self._image_lock = threading.Lock()
        self._shutdown_done = False
        self._started = False
        # Gtk.Stack ignores a visible-child request before the window is shown,
        # so the saved view is applied once in _restore_view() and the notify
        # handler must not persist the temporary default until then.
        self._restore_pending = True

        self._main_stack = Gtk.Stack()
        _style_stack(self._main_stack)
        self._version_name = None

        width, height = self._settings.window_size
        self.set_title(APP_NAME)
        self.set_default_size(width, height)
        self.set_size_request(900, 600)
        self.set_titlebar(self._build_header())
        self.add(self._build_body())
        self.apply_theme(self._settings.theme)
        self.apply_interface_prefs()
        self.connect("key-press-event", self._on_key_press)
        self.connect("delete-event", self._on_delete_event)
        self.connect("show", self._on_show)
        self._apply_download_dir_label()
        self.downloads_panel.update_torrents(self.torrents.tasks())
        # The saved view is restored in _on_show: a Gtk.Stack cannot select a
        # child that has not been shown yet, and switching here would silently
        # fall back to the first page and overwrite the stored preference.
        if self._settings.maximized:
            self.maximize()

    # -- construction -------------------------------------------------------

    @property
    def download_dir(self) -> Path:
        return self._download_dir

    def _build_header(self) -> Gtk.HeaderBar:
        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.set_has_subtitle(False)
        header.get_style_context().add_class("app-header")

        # The view switcher is kept as the state mirror behind Ctrl+1/Ctrl+2
        # and the saved view; the rail is what the user actually clicks.
        self._view_switcher = Gtk.StackSwitcher()
        self._view_switcher.get_style_context().add_class("view-switcher")
        self._view_switcher.set_stack(self._main_stack)
        self._view_switcher.set_no_show_all(True)
        self._view_switcher.hide()

        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        nav.get_style_context().add_class("nav-buttons")
        self._history_nav = nav
        self._back_btn = _flat_button("go-previous-symbolic", "Back to the grid")
        self._back_btn.set_sensitive(False)
        self._back_btn.connect("clicked", lambda *_: self.go_back())
        nav.pack_start(self._back_btn, False, False, 0)
        self._forward_btn = _flat_button("go-next-symbolic", "Forward")
        self._forward_btn.set_sensitive(False)
        self._forward_btn.set_tooltip_text("Forward")
        nav.pack_start(self._forward_btn, False, False, 0)
        header.pack_start(nav)

        pill = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        pill.get_style_context().add_class("search-pill")
        self._search = Gtk.SearchEntry()
        __import__("lgl.gtk_suggest", fromlist=["attach"]).attach(self._search, self)
        self._search.set_placeholder_text("Search games")
        self._search.set_width_chars(18)
        self._search.set_max_width_chars(28)
        self._search.set_hexpand(False)
        self._search.set_tooltip_text(
            "Search title, year, identifier, creator, or subject (Ctrl+K)"
        )
        self._search.get_accessible().set_name("Search the game collection")
        self._search.connect("changed", self._on_search_changed)
        self._search.connect("activate", self._on_search_activate)
        self._search.connect("stop-search", lambda *_: self._search.set_text(""))
        pill.pack_start(self._search, True, True, 0)
        self._search_hint = Gtk.Label(label="Ctrl K")
        self._search_hint.get_style_context().add_class("key-hint")
        self._search_hint.set_valign(Gtk.Align.CENTER)
        pill.pack_end(self._search_hint, False, False, 0)
        header.set_custom_title(pill)
        self._search_pill = pill

        # Refresh button (swirl icon) next to the burger menu.
        refresh_btn = Gtk.Button()
        refresh_btn.set_relief(Gtk.ReliefStyle.NONE)
        refresh_btn.set_tooltip_text("Refresh (rescan library, reload collection)")
        refresh_btn.get_accessible().set_name("Refresh (rescan library, reload collection)")
        refresh_btn.add(Gtk.Image.new_from_icon_name("view-refresh-symbolic", _ICON))
        refresh_btn.connect("clicked", lambda *_: self._combined_refresh())
        refresh_btn.set_valign(Gtk.Align.CENTER)
        header.pack_end(refresh_btn)
        header.pack_end(self._build_menu_button())

        # Which profile's files are open, so it is never a guess.
        self._profile_label = Gtk.Label()
        self._profile_label.get_style_context().add_class("dim")
        self._profile_label.set_valign(Gtk.Align.CENTER)
        self._profile_label.set_tooltip_text(
            "Active profile — Tools → Profiles… to switch"
        )
        self._set_profile_name()
        header.pack_end(self._profile_label)
        return header

    def _set_profile_name(self, name: str | None = None,
                          was: str | None = None) -> None:
        """Put the active profile's name in the header."""
        if name is None:
            try:
                name = profiles.load().active().name
            except Exception:  # noqa: BLE001 - a header label must never break
                name = ""
        if was and was != name:
            self._profile_label.set_text(f"Profile · {was} → {name}")
        else:
            self._profile_label.set_text(f"Profile · {name}" if name else "")

    def _combined_refresh(self) -> None:
        """Rescan library + reload collection in one action."""
        try:
            self.library_view.rescan()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.refresh_active_view()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.load_collection(force=True)
        except Exception:  # noqa: BLE001
            pass

    def _build_menu_button(self) -> Gtk.MenuButton:
        menu = Gtk.Menu()

        def add_item(label: str, callback: Callable[[], None]) -> None:
            item = Gtk.MenuItem(label=label)
            item.connect("activate", lambda *_: callback())
            menu.append(item)

        add_item("Website Sources…", self.show_website_sources)
        add_item("Kryoto Catalog…", self.show_kryoto_catalog)
        add_item("Show Download Queue", lambda: self.set_downloads_visible(True))

        # Maintenance submenu
        maintenance = Gtk.Menu()
        self._artwork_sync_item = Gtk.MenuItem(label="Sync artwork for collection")
        self._artwork_sync_item.set_tooltip_text(
            "Search RAWG by name for imported games whose poster cannot be shown "
            "and attach the artwork it matches"
        )
        self._artwork_sync_item.set_sensitive(False)
        self._artwork_sync_item.connect(
            "activate", lambda *_: self.sync_collection_artwork()
        )
        maintenance.append(self._artwork_sync_item)
        self._artwork_retry_item = Gtk.MenuItem(
            label="Re-check games with no artwork match"
        )
        self._artwork_retry_item.set_tooltip_text(
            "Search RAWG again right now for imported games a previous sync left "
            "without a poster, instead of waiting for that result to expire"
        )
        self._artwork_retry_item.set_sensitive(False)
        self._artwork_retry_item.connect(
            "activate", lambda *_: self.sync_collection_artwork(force=True)
        )
        maintenance.append(self._artwork_retry_item)
        maintenance_item = Gtk.MenuItem(label="Maintenance")
        maintenance_item.set_submenu(maintenance)
        menu.append(maintenance_item)

        menu.append(Gtk.SeparatorMenuItem())
        add_item("Command palette…", self.show_palette)
        add_item("Release calendar…", self.show_calendar)
        add_item("Discover…", lambda: __import__("lgl.gtk_discover", fromlist=["show_discover"]).show_discover(self, self.settings))
        add_item("Tags…", lambda: __import__("lgl.gtk_library_tools", fromlist=["show_tags"]).show_tags(self, self.settings))
        add_item("Find duplicates…", lambda: __import__("lgl.gtk_library_tools", fromlist=["show_duplicates"]).show_duplicates(self))
        add_item("Edit details…", lambda: __import__("lgl.gtk_library_tools", fromlist=["show_edit_details"]).show_edit_details(self))
        add_item("Favourites…", lambda: __import__("lgl.gtk_activity", fromlist=["show_favourites"]).show_favourites(self))
        add_item("Activity…", lambda: __import__("lgl.gtk_activity", fromlist=["show_activity"]).show_activity(self))
        add_item("Recently viewed…", self.show_recent)
        add_item("Keyboard shortcuts…", self.show_shortcuts)
        add_item("Profiles…", self.show_profiles)
        add_item("Collections…", lambda: __import__("lgl.gtk_collections", fromlist=["show_collections"]).show_collections(self))
        add_item("Tools…", self.show_tools)
        add_item("Surprise me…", lambda: __import__("lgl.gtk_surprise", fromlist=["surprise"]).surprise(self))
        add_item("Statistics…", lambda: __import__("lgl.gtk_stats", fromlist=["show_statistics"]).show_statistics(self))
        add_item("Settings…", lambda: self.show_settings())
        add_item("About Lumora", self.show_about)
        menu.show_all()

        button = Gtk.MenuButton()
        button.set_popup(menu)
        button.add(Gtk.Image.new_from_icon_name("open-menu-symbolic", _ICON))
        button.set_tooltip_text("Application menu")
        button.get_accessible().set_name("Application menu")
        button.set_valign(Gtk.Align.CENTER)
        return button

    def _build_rail(self) -> Gtk.Widget:
        """The icon rail on the far left: the app's four destinations."""
        rail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        rail.get_style_context().add_class("app-rail")
        rail.set_size_request(68, -1)

        self._rail_buttons: dict[str, Gtk.Button] = {}
        for key, icon, label, tooltip in (
            ("archive", "view-grid-symbolic", "Browse",
             "Browse the collection (Ctrl+1)"),
            ("library", "applications-games-symbolic", "Library",
             "Your downloaded games (Ctrl+2)"),
            ("downloads", "folder-download-symbolic", "Downloads",
             "Show the download queue (Ctrl+D)"),
        ):
            rail.pack_start(
                self._rail_button(key, icon, label, tooltip), False, False, 0
            )
        # Media is now a real destination in the main stack, like the others.
        self._media_button = self._rail_button(
            "media", _media_icon(), "Media", "Movies & TV",
            callback=lambda: self._apply_view("media"), register=True,
        )
        rail.pack_start(self._media_button, False, False, 0)
        rail.pack_start(Gtk.Box(), True, True, 0)

        rail.pack_start(
            self._rail_button(
                "settings", "emblem-system-symbolic", "Settings",
                "Settings (Ctrl+,)", callback=lambda: self.show_settings(),
                register=False,
            ), False, False, 0
        )
        rail.pack_start(
            self._rail_button(
                "about", "help-about-symbolic", "About",
                "About this application", callback=lambda: self.show_about(),
                register=False,
            ), False, False, 0
        )
        self._sync_rail()
        return rail

    def _rail_button(self, key: str, icon: str, label: str, tooltip: str,
                     callback: Callable[[], None] | None = None,
                     register: bool = True) -> Gtk.Button:
        button = Gtk.Button()
        button.get_style_context().add_class("rail-button")
        button.set_relief(Gtk.ReliefStyle.NONE)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.pack_start(Gtk.Image.new_from_icon_name(icon, _ICON), False, False, 0)
        text = Gtk.Label(label=label)
        text.get_style_context().add_class("rail-label")
        box.pack_start(text, False, False, 0)
        if key == "downloads":
            # The queue count follows the destination now that the header
            # toggle is gone.
            self._badge = Gtk.Label(label="0")
            self._badge.get_style_context().add_class("rail-badge")
            self._badge.set_no_show_all(True)
            self._badge.set_halign(Gtk.Align.CENTER)
            box.pack_start(self._badge, False, False, 0)
        button.add(box)
        button.set_tooltip_text(tooltip)
        button.get_accessible().set_name(label)
        if callback is not None:
            button.connect("clicked", lambda *_: callback())
        elif key == "downloads":
            button.connect("clicked", lambda *_: self.set_downloads_visible(True))
        else:
            button.connect("clicked", lambda *_, name=key: self._apply_view(name))
        if register:
            self._rail_buttons[key] = button
        return button

    def _sync_rail(self) -> None:
        current = self.active_view()
        if current == "game":
            current = "archive"
        for key, button in getattr(self, "_rail_buttons", {}).items():
            context = button.get_style_context()
            if key == current:
                context.add_class("rail-on")
            else:
                context.remove_class("rail-on")

    def go_back(self) -> None:
        """Close the open game and return to the results."""
        if getattr(self, "_current_game", None) is None:
            return
        self._current_game = None
        self._current_item = None
        self._item_token += 1
        self._details.show_empty()
        self._grid_selecting = True
        try:
            self._browse_flow.unselect_all()
        finally:
            self._grid_selecting = False
        if self._list.get_selected_row() is not None:
            self._list.unselect_all()
        self._back_btn.set_sensitive(False)
        if self._main_stack.get_visible_child_name() == "game":
            self._main_stack.set_visible_child_name("archive")

    def _build_body(self) -> Gtk.Widget:
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        body.pack_start(self._build_queue_banner(), False, False, 0)

        archive = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        self._banner = Gtk.Revealer()
        self._banner.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self._banner.set_transition_duration(160)
        banner_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        banner_box.get_style_context().add_class("banner")
        banner_icon = Gtk.Image.new_from_icon_name(
            "dialog-warning-symbolic", Gtk.IconSize.MENU
        )
        banner_icon.set_valign(Gtk.Align.CENTER)
        self._banner_label = Gtk.Label(label="", xalign=0)
        self._banner_label.set_line_wrap(True)
        self._banner_label.set_max_width_chars(90)
        self._banner_label.get_style_context().add_class("banner-text")
        banner_retry = _small_button("Retry")
        banner_retry.connect("clicked", lambda *_: self.load_collection(force=True))
        banner_close = _flat_button("window-close-symbolic", "Dismiss this notice")
        banner_close.connect("clicked", lambda *_: self._banner.set_reveal_child(False))
        banner_box.pack_start(banner_icon, False, False, 0)
        banner_box.pack_start(self._banner_label, True, True, 0)
        banner_box.pack_start(banner_retry, False, False, 0)
        banner_box.pack_start(banner_close, False, False, 0)
        self._banner.add(banner_box)
        archive.pack_start(self._banner, False, False, 0)

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_position(216)
        paned.set_wide_handle(False)
        paned.pack1(self._build_browse_filters(), False, False)
        paned.pack2(self._build_sidebar(), True, True)
        archive.pack_start(paned, True, True, 0)

        self._main_stack.add_titled(archive, "archive", "Archive")

        # The game page is a destination of its own: the grid keeps the whole
        # width, and a game's details only appear once one is opened.
        self._game_page = self._build_right_pane()
        self._main_stack.add_named(self._game_page, "game")

        self.library_view = LibraryView(self)
        self._main_stack.add_titled(self.library_view, "library", "Library")

        # Downloads is a page, not a panel that eats the bottom of every view.
        self.downloads_panel = DownloadsPanel(self)
        self._downloads_revealer = Gtk.Revealer()
        self._downloads_revealer.set_transition_type(Gtk.RevealerTransitionType.NONE)
        self._downloads_revealer.add(self.downloads_panel)
        self._downloads_revealer.set_reveal_child(True)
        self._main_stack.add_named(self._downloads_revealer, "downloads")

        # Movies & TV is now an in-app destination instead of a popup window.
        from .movies_ui import MoviesWindow

        self.media_view = MoviesWindow(self, self.settings)
        self._main_stack.add_named(self.media_view, "media")

        self._main_stack.connect("notify::visible-child-name", self._on_view_changed)
        body.pack_start(self._main_stack, True, True, 0)

        shell = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        shell.get_style_context().add_class("app-shell")
        shell.pack_start(self._build_rail(), False, False, 0)
        shell.pack_start(body, True, True, 0)

        overlay = Gtk.Overlay()
        overlay.add(shell)
        self._toast = Toast()
        overlay.add_overlay(self._toast)
        overlay.set_overlay_pass_through(self._toast, True)
        return overlay

    def _build_queue_banner(self) -> Gtk.Widget:
        self._queue_banner = Gtk.Revealer()
        self._queue_banner.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self._queue_banner.set_transition_duration(160)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.get_style_context().add_class("queue-banner")
        icon = Gtk.Image.new_from_icon_name(
            "folder-download-symbolic", Gtk.IconSize.MENU
        )
        icon.set_valign(Gtk.Align.CENTER)
        label = Gtk.Label(
            label="Unfinished downloads restored — resume them when ready.",
            xalign=0,
        )
        label.set_line_wrap(True)
        label.get_style_context().add_class("banner-text")
        resume = _small_button("Resume all")
        resume.connect("clicked", lambda *_: self.resume_unfinished())
        dismiss = _flat_button("window-close-symbolic", "Dismiss this notice")
        dismiss.connect("clicked", lambda *_: self._queue_banner.set_reveal_child(False))
        box.pack_start(icon, False, False, 0)
        box.pack_start(label, True, True, 0)
        box.pack_start(resume, False, False, 0)
        box.pack_start(dismiss, False, False, 0)
        self._queue_banner.add(box)
        return self._queue_banner

    # -- Browse page ---------------------------------------------------------

    def _build_sidebar(self) -> Gtk.Widget:
        """The Browse results page: a title row with its controls, then results."""
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        page.get_style_context().add_class("browse-page")

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header.get_style_context().add_class("browse-header")

        heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        title = Gtk.Label(label="Browse", xalign=0)
        title.get_style_context().add_class("page-title")
        heading.pack_start(title, False, False, 0)
        self._sidebar_count = Gtk.Label(label="Loading…", xalign=0)
        self._sidebar_count.get_style_context().add_class("page-count")
        self._sidebar_count.set_valign(Gtk.Align.END)
        self._sidebar_count.set_margin_bottom(5)
        heading.pack_start(self._sidebar_count, False, False, 0)
        self._sidebar_spinner = Gtk.Spinner()
        self._sidebar_spinner.set_size_request(14, 14)
        self._sidebar_spinner.set_valign(Gtk.Align.END)
        self._sidebar_spinner.set_margin_bottom(7)
        heading.pack_start(self._sidebar_spinner, False, False, 0)
        header.pack_start(heading, True, True, 0)

        self._browse_sort = _Picker(
            ARCHIVE_SORTS,
            tooltip="Sort the archive list",
            accessible="Sort the archive list",
            width=160,
        )
        self._browse_sort.set_active_id(self._settings.browse_sort)
        self._browse_sort.connect("changed", self._on_browse_sort_changed)
        self._browse_desc = Gtk.Button()
        self._browse_desc.get_style_context().add_class("sort-direction")
        self._browse_desc.set_valign(Gtk.Align.CENTER)
        self._browse_desc.connect("clicked", self._on_browse_direction_clicked)
        header.pack_start(
            _sort_cluster(self._browse_sort, self._browse_desc), False, False, 0
        )
        self._sync_browse_sort()

        self._browse_view = "grid"
        self._browse_view_btn = Gtk.ToggleButton()
        self._browse_view_btn.get_style_context().add_class("view-toggle")
        self._browse_view_btn.set_active(False)
        self._browse_view_btn.set_valign(Gtk.Align.CENTER)
        self._browse_view_btn.set_tooltip_text(
            "Switch between covers and a compact list"
        )
        self._browse_view_btn.get_accessible().set_name("Compact list view")
        self._browse_view_btn.connect("toggled", self._on_browse_view_toggled)
        header.pack_start(self._browse_view_btn, False, False, 0)
        self._sync_browse_view()

        self._artwork_sync_running = False
        self._artwork_sync_cancel = False
        page.pack_start(header, False, False, 0)

        self._sidebar_stack = Gtk.Stack()
        self._sidebar_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._sidebar_stack.set_transition_duration(120)

        loading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        loading.set_valign(Gtk.Align.CENTER)
        loading.set_halign(Gtk.Align.CENTER)
        loading_spinner = Gtk.Spinner()
        loading_spinner.start()
        loading_spinner.set_size_request(22, 22)
        loading_text = Gtk.Label(label="Loading collection…")
        loading_text.get_style_context().add_class("dim")
        loading.pack_start(loading_spinner, False, False, 0)
        loading.pack_start(loading_text, False, False, 0)
        self._sidebar_stack.add_named(loading, "loading")

        error = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9)
        error.set_valign(Gtk.Align.CENTER)
        error.set_halign(Gtk.Align.CENTER)
        error.set_margin_start(20)
        error.set_margin_end(20)
        error_icon = Gtk.Image.new_from_icon_name(
            "network-error-symbolic", Gtk.IconSize.DIALOG
        )
        error_icon.get_style_context().add_class("dim")
        self._sidebar_error_label = Gtk.Label(label="")
        self._sidebar_error_label.set_line_wrap(True)
        self._sidebar_error_label.set_justify(Gtk.Justification.CENTER)
        self._sidebar_error_label.set_max_width_chars(32)
        self._sidebar_error_label.get_style_context().add_class("dim")
        error_retry = Gtk.Button(label="Retry")
        error_retry.set_halign(Gtk.Align.CENTER)
        error_retry.connect("clicked", lambda *_: self.load_collection(force=True))
        error.pack_start(error_icon, False, False, 0)
        error.pack_start(self._sidebar_error_label, False, False, 0)
        error.pack_start(error_retry, False, False, 0)
        self._sidebar_stack.add_named(error, "error")

        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        empty.set_valign(Gtk.Align.CENTER)
        empty.set_halign(Gtk.Align.CENTER)
        empty_title = Gtk.Label(label="No games found")
        empty_title.get_style_context().add_class("empty-text")
        empty_text = Gtk.Label(label="The collection list is empty.")
        empty_text.get_style_context().add_class("dim")
        empty.pack_start(empty_title, False, False, 0)
        empty.pack_start(empty_text, False, False, 0)
        self._sidebar_stack.add_named(empty, "empty")

        no_results = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        no_results.set_valign(Gtk.Align.CENTER)
        no_results.set_halign(Gtk.Align.CENTER)
        self._no_results_label = Gtk.Label(label="No matches")
        self._no_results_label.get_style_context().add_class("empty-text")
        no_results_text = Gtk.Label(label="Try a shorter search term.")
        no_results_text.get_style_context().add_class("dim")
        clear = Gtk.Button(label="Clear search")
        clear.set_halign(Gtk.Align.CENTER)
        clear.connect("clicked", lambda *_: self._clear_search())
        no_results.pack_start(self._no_results_label, False, False, 0)
        no_results.pack_start(no_results_text, False, False, 0)
        no_results.pack_start(clear, False, False, 0)
        self._sidebar_stack.add_named(no_results, "noresults")

        self._browse_flow = Gtk.FlowBox()
        self._browse_flow.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._browse_flow.set_activate_on_single_click(True)
        self._browse_flow.set_homogeneous(True)
        self._browse_flow.set_column_spacing(10)
        self._browse_flow.set_row_spacing(14)
        self._browse_flow.set_min_children_per_line(3)
        self._browse_flow.set_max_children_per_line(12)
        self._browse_flow.set_valign(Gtk.Align.START)
        self._browse_flow.get_style_context().add_class("browse-grid")
        self._browse_flow.get_accessible().set_name("Game covers")
        self._browse_flow.connect("selected-children-changed", self._on_grid_selected)
        self._grid_tiles: dict[str, "BrowseTile"] = {}
        self._grid_selecting = False
        self._grid_covers_loaded = 0
        self._grid_count = 0
        grid_scroller = Gtk.ScrolledWindow()
        grid_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        grid_scroller.add(self._browse_flow)
        self._browse_scroller = grid_scroller
        self._browse_grid_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._shelves_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._shelves_box.set_margin_start(24)
        self._shelves_box.set_margin_end(24)
        self._shelves_box.set_margin_top(8)
        self._shelves_box.set_margin_bottom(4)
        self._shelves_box.set_visible(False)
        self._browse_grid_page.pack_start(self._shelves_box, False, False, 0)
        self._browse_grid_page.pack_start(grid_scroller, True, True, 0)
        grid_adjustment = grid_scroller.get_vadjustment()
        if grid_adjustment is not None:
            grid_adjustment.connect("value-changed", self._on_grid_scrolled)
        self._sidebar_stack.add_named(self._browse_grid_page, "grid")

        self._list = Gtk.ListBox()
        self._list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._list.set_activate_on_single_click(True)
        self._list.set_filter_func(self._game_visible)
        self._list.set_sort_func(self._sort_rows)
        self._list.connect("row-selected", self._on_row_selected)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.add(self._list)
        self._sidebar_scroller = scroller
        adjustment = scroller.get_vadjustment()
        if adjustment is not None:
            adjustment.connect("value-changed", self._on_sidebar_scrolled)
        self._sidebar_stack.add_named(scroller, "list")

        self._sidebar_stack.set_visible_child_name("loading")
        page.pack_start(self._sidebar_stack, True, True, 0)

        self._row_note = Gtk.Label(label="", xalign=0)
        self._row_note.set_line_wrap(True)
        self._row_note.set_max_width_chars(90)
        self._row_note.set_margin_start(24)
        self._row_note.set_margin_end(24)
        self._row_note.set_margin_bottom(8)
        self._row_note.get_style_context().add_class("dim")
        self._row_note.get_style_context().add_class("small")
        self._row_note.set_no_show_all(True)
        page.pack_start(self._row_note, False, False, 0)

        self._format_note = Gtk.Label(label="")
        self._format_note.set_xalign(0)
        self._format_note.set_line_wrap(True)
        self._format_note.set_max_width_chars(90)
        self._format_note.set_margin_start(24)
        self._format_note.set_margin_end(24)
        self._format_note.set_margin_bottom(8)
        self._format_note.set_no_show_all(True)
        self._format_note.get_style_context().add_class("dim")
        self._format_note.get_style_context().add_class("small")
        page.pack_start(self._format_note, False, False, 0)

        self._sidebar_footer = Gtk.Label(label="", xalign=0)
        self._sidebar_footer.set_ellipsize(Pango.EllipsizeMode.END)
        self._sidebar_footer.get_style_context().add_class("sidebar-footer")
        self._sidebar_footer.set_no_show_all(True)
        page.pack_start(self._sidebar_footer, False, False, 0)
        return page

    # -- Browse filters ------------------------------------------------------

    def _build_browse_filters(self) -> Gtk.Widget:
        """The left column: every filter visible, grouped, with live counts."""
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        column.get_style_context().add_class("browse-filters")
        column.set_size_request(216, -1)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        body.get_style_context().add_class("filter-body")
        body.set_margin_start(16)
        body.set_margin_end(16)
        body.set_margin_top(16)
        body.set_margin_bottom(24)

        # The pickers still hold the filter state the code reads; the chips and
        # rows below are the visible controls, so they are never packed here.
        self._year_combo = LibraryView._combo(
            _YEAR_FILTERS, "Filter by release year", "Filter by year"
        )
        self._license_combo = LibraryView._combo(
            _LICENSE_FILTERS, "Filter by license", "Filter by license"
        )
        self._format_combo = LibraryView._combo(
            _FORMAT_FILTERS, "Filter by main file format", "Filter by format"
        )
        for combo in (
            self._year_combo,
            self._license_combo,
            self._format_combo,
        ):
            combo.set_no_show_all(True)
            combo.hide()
            combo.connect("changed", self._on_archive_filter_changed)

        self._chip_groups: dict[int, tuple[Gtk.ComboBoxText,
                                           list[tuple[str, Gtk.Button]]]] = {}
        self._syncing_chips = False
        self._size_bucket = "all"
        self._genre_counts: list[tuple[str, int]] = []
        self._genre_checks: dict[str, Gtk.CheckButton] = {}
        self._genre_excludes: dict[str, Gtk.ToggleButton] = {}
        self._genre_expanded = False
        self._include_genres: set[str] = set()
        self._exclude_genres: set[str] = set()

        # Genre ------------------------------------------------------------
        self._genre_more = Gtk.Button(label="All")
        self._genre_more.get_style_context().add_class("link-button")
        self._genre_more.set_tooltip_text("Show every genre")
        self._genre_more.connect("clicked", lambda *_: self._toggle_genres())
        genre_section = self._filter_section("Genre", self._genre_more)
        self._genre_section = genre_section
        self._genre_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        # Expanded to every genre the list would otherwise run the height of the
        # whole column; keep it a compact, scrollable block instead.
        genre_scroll = Gtk.ScrolledWindow()
        genre_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        genre_scroll.set_max_content_height(232)
        genre_scroll.set_propagate_natural_height(True)
        genre_scroll.get_style_context().add_class("genre-scroll")
        genre_scroll.add(self._genre_list)
        genre_section.pack_start(genre_scroll, False, False, 0)
        body.pack_start(genre_section, False, False, 0)

        # Size -------------------------------------------------------------
        size_section = self._filter_section("Size")
        self._size_chips = self._chip_row(
            list(SIZE_FILTERS), self._on_size_chip
        )
        size_section.pack_start(self._size_chips, False, False, 0)
        body.pack_start(size_section, False, False, 0)

        # Released / Licence / Format --------------------------------------
        # Released stays as shortcuts over the year range under it: a decade
        # chip fills the two year fields, so there is one year state and the
        # summary line can never disagree with the controls.
        year_section = self._filter_section("Released")
        self._year_chips = self._chip_row(
            list(_YEAR_FILTERS), self._on_filter_chip, self._year_combo
        )
        self._chip_groups[id(self._year_combo)] = (
            self._year_combo,
            self._year_chips.chips,  # type: ignore[attr-defined]
        )
        year_section.pack_start(self._year_chips, False, False, 0)
        (
            self._year_row,
            self._year_from_entry,
            self._year_to_entry,
        ) = _year_range_row(self._on_year_entry_changed)
        year_section.pack_start(self._year_row, False, False, 0)
        self._year_section = year_section
        body.pack_start(year_section, False, False, 0)

        for title, combo, options in (
            ("Licence", self._license_combo, _LICENSE_FILTERS),
            ("File format", self._format_combo, _FORMAT_FILTERS),
        ):
            section = self._filter_section(title)
            row = self._chip_row(list(options), self._on_filter_chip, combo)
            self._chip_groups[id(combo)] = (combo, row.chips)  # type: ignore[attr-defined]
            section.pack_start(row, False, False, 0)
            body.pack_start(section, False, False, 0)

        # What is actually being applied, in words: a filter that is on is
        # never invisible, and an empty line means nothing is filtered.
        self._filter_summary = Gtk.Label(label="", xalign=0)
        self._filter_summary.set_line_wrap(True)
        self._filter_summary.get_style_context().add_class("dim")
        self._filter_summary.get_style_context().add_class("small")
        self._filter_summary.set_no_show_all(True)
        self._filter_summary.hide()
        body.pack_start(self._filter_summary, False, False, 0)

        # Reset sits in its own footer: there is no availability filter here, so
        # a heading above one link was an empty section.
        body.pack_start(_filter_footer(self._reset_archive_filters), False, False, 0)

        scroller.add(body)
        column.pack_start(scroller, True, True, 0)
        self._sync_chips()
        return column

    # -- content filters (year range and genres) -----------------------------

    def _year_bounds(self) -> tuple[int | None, int | None]:
        """The years in the two fields, in order; ``None`` means no bound."""
        start = getattr(self, "_year_from_entry", None)
        end = getattr(self, "_year_to_entry", None)
        low = browse_filters.year_number(start.get_text()) if start else None
        high = browse_filters.year_number(end.get_text()) if end else None
        if low is not None and high is not None and low > high:
            low, high = high, low
        return low, high

    def _set_year_bounds(self, low: int | None, high: int | None) -> None:
        if getattr(self, "_year_from_entry", None) is not None:
            self._year_from_entry.set_text("" if low is None else str(low))
        if getattr(self, "_year_to_entry", None) is not None:
            self._year_to_entry.set_text("" if high is None else str(high))

    def _on_year_entry_changed(self, *_args) -> bool:
        self._apply_filter()
        return False

    def _browse_content_filters(self) -> browse_filters.Filters:
        """The year range and genre choices, in the form content_filters reads."""
        low, high = self._year_bounds()
        return browse_filters.filters_for(
            min_year=low,
            max_year=high,
            genres=self._include_genres,
            exclude_genres=self._exclude_genres,
        )

    def _genre_display(self) -> dict[str, str]:
        chosen = self._include_genres | self._exclude_genres
        return {name.casefold(): name for name in chosen}

    def _update_filter_summary(self) -> None:
        """Show the content_filters summary and hide facets no title carries."""
        summary = getattr(self, "_filter_summary", None)
        if summary is None:
            return
        text = browse_filters.summary(
            self._browse_content_filters(), display=self._genre_display()
        )
        summary.set_text(text)
        self._show_filter_widget(summary, bool(text))
        present = browse_filters.available(self._games)
        for section, field in (
            (getattr(self, "_genre_section", None), browse_filters.GENRES),
            (getattr(self, "_year_section", None), browse_filters.YEAR),
        ):
            if section is not None:
                self._show_filter_widget(section, field in present)

    @staticmethod
    def _show_filter_widget(widget: Gtk.Widget, visible: bool) -> None:
        """Show or hide a filter control; the next show_all() must not undo it."""
        widget.set_no_show_all(not visible)
        widget.set_visible(visible)

    def _filter_section(self, title: str,
                        extra: Gtk.Widget | None = None) -> Gtk.Box:
        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        section.get_style_context().add_class("filter-group")
        heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        label = Gtk.Label(label=title, xalign=0)
        label.get_style_context().add_class("filter-title")
        heading.pack_start(label, False, False, 0)
        if extra is not None:
            heading.pack_end(extra, False, False, 0)
        section.pack_start(heading, False, False, 0)
        return section

    def _chip_row(self, options: Sequence[tuple[str, str]],
                  handler: Callable[..., None],
                  combo: Gtk.ComboBoxText | None = None) -> Gtk.Widget:
        # A wrapping flow, not a box: eight chips in one line used to force the
        # whole filter column to be as wide as their sum.
        row = Gtk.FlowBox()
        row.set_selection_mode(Gtk.SelectionMode.NONE)
        row.set_homogeneous(False)
        row.set_min_children_per_line(1)
        row.set_max_children_per_line(8)
        row.set_column_spacing(4)
        row.set_row_spacing(4)
        row.set_valign(Gtk.Align.START)
        row.set_activate_on_single_click(True)
        row.get_style_context().add_class("chip-row")
        chips: list[tuple[str, Gtk.Button]] = []
        for value, label in options:
            chip = Gtk.Button(label=label)
            chip.get_style_context().add_class("chip")
            chip.set_relief(Gtk.ReliefStyle.NONE)
            chip.set_tooltip_text(label)
            # Combo-backed chips need the combo they drive; the size chips do
            # not.  Passing only the value raised TypeError on every click and
            # the filter silently did nothing.
            if combo is None:
                chip.connect("clicked", handler, value)
            else:
                chip.connect("clicked", handler, value, combo)
            row.add(chip)
            chips.append((value, chip))
        row.chips = chips  # type: ignore[attr-defined]
        return row

    def _mark_chips(self, widgets: Sequence[tuple[str, Gtk.Button]],
                    current: str) -> None:
        for value, chip in widgets:
            context = chip.get_style_context()
            if value == current:
                context.add_class("chip-on")
            else:
                context.remove_class("chip-on")

    def _sync_chips(self) -> None:
        for combo, widgets in list(getattr(self, "_chip_groups", {}).values()):
            current = combo.get_active_id() or "all"
            if combo is getattr(self, "_year_combo", None):
                current = browse_filters.bucket_for(*self._year_bounds())
            self._mark_chips(widgets, current)
        if hasattr(self, "_size_chips"):
            self._mark_chips(
                getattr(self._size_chips, "chips", []),
                getattr(self, "_size_bucket", "all"),
            )

    def _on_size_chip(self, _button: Gtk.Button, value: str) -> None:
        self._size_bucket = value
        self._sync_chips()
        self._apply_filter()

    def _on_filter_chip(self, _button: Gtk.Button, value: str,
                        combo: Gtk.ComboBoxText) -> None:
        self._syncing_chips = True
        try:
            combo.set_active_id(value)
        finally:
            self._syncing_chips = False
        if combo is getattr(self, "_year_combo", None):
            self._set_year_bounds(*browse_filters.bucket_bounds(value))
        self._sync_chips()
        self._apply_filter()

    def _on_genre_row(self, button: Gtk.CheckButton, label: str) -> None:
        """Include one genre; several may be included at once."""
        if self._syncing_chips:
            return
        if button.get_active():
            self._include_genres.add(label)
            self._exclude_genres.discard(label)
        else:
            self._include_genres.discard(label)
        self._sync_genre_toggles()
        self._apply_filter()

    def _on_genre_exclude_toggled(self, button: Gtk.ToggleButton, label: str) -> None:
        """Never show one genre; it cannot be included at the same time."""
        if self._syncing_chips:
            return
        if button.get_active():
            self._exclude_genres.add(label)
            self._include_genres.discard(label)
        else:
            self._exclude_genres.discard(label)
        self._sync_genre_toggles()
        self._apply_filter()

    def _sync_genre_toggles(self) -> None:
        """Put both controls back in step with the two genre sets."""
        self._syncing_chips = True
        try:
            for label, check in self._genre_checks.items():
                check.set_active(label in self._include_genres)
            for label, toggle in self._genre_excludes.items():
                toggle.set_active(label in self._exclude_genres)
        finally:
            self._syncing_chips = False

    def _toggle_genres(self) -> None:
        self._genre_expanded = not self._genre_expanded
        self._rebuild_genre_rows()

    def _rebuild_genre_rows(self) -> None:
        if not hasattr(self, "_genre_list"):
            return
        counts = getattr(self, "_genre_counts", [])
        labels = [label for label, _count in counts]
        chosen = self._include_genres | self._exclude_genres
        if chosen and not chosen.issubset(set(labels)):
            self._genre_expanded = True
        for child in list(self._genre_list.get_children()):
            self._genre_list.remove(child)
        self._genre_checks = {}
        self._genre_excludes = {}
        self._syncing_chips = True
        try:
            limit = len(counts) if self._genre_expanded else 6
            for label, count in counts[:limit]:
                line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                line.get_style_context().add_class("filter-row")
                check = Gtk.CheckButton(label=label)
                check.get_style_context().add_class("filter-check")
                check.set_active(label in self._include_genres)
                check.set_tooltip_text(f"Only show titles tagged {label}")
                check.connect("toggled", self._on_genre_row, label)
                line.pack_start(check, True, True, 0)
                number = Gtk.Label(label=count_label(count))
                number.get_style_context().add_class("filter-count")
                line.pack_end(number, False, False, 0)
                never = Gtk.ToggleButton(label="\u2715")
                never.get_style_context().add_class("chip")
                never.set_relief(Gtk.ReliefStyle.NONE)
                never.set_active(label in self._exclude_genres)
                never.set_tooltip_text(f"Never show titles tagged {label}")
                never.get_accessible().set_name(f"Exclude {label}")
                never.connect("toggled", self._on_genre_exclude_toggled, label)
                line.pack_end(never, False, False, 0)
                self._genre_list.pack_start(line, False, False, 0)
                self._genre_checks[label] = check
                self._genre_excludes[label] = never
        finally:
            self._syncing_chips = False
        if len(counts) > 6:
            self._genre_more.set_label(
                "Less" if self._genre_expanded else f"All {len(counts)}"
            )
            self._genre_more.show()
        else:
            self._genre_more.hide()
        self._genre_list.show_all()

    def _archive_filters_active(self) -> bool:
        low, high = self._year_bounds()
        return bool(
            low is not None
            or high is not None
            or self._include_genres
            or self._exclude_genres
            or any(
                combo.get_active_id() not in (None, "all")
                for combo in (self._license_combo, self._format_combo)
            )
            or getattr(self, "_size_bucket", "all") not in (None, "all")
        )

    # -- Browse view mode and grid ------------------------------------------

    def _on_browse_view_toggled(self, _button: Gtk.ToggleButton) -> None:
        self._browse_view = "list" if self._browse_view_btn.get_active() else "grid"
        self._sync_browse_view()
        self._apply_filter()

    def _sync_browse_view(self) -> None:
        mode = getattr(self, "_browse_view", "grid")
        icon = "view-list-symbolic" if mode == "grid" else "view-grid-symbolic"
        self._browse_view_btn.set_image(
            Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.MENU)
        )
        self._browse_view_btn.set_tooltip_text(
            "Show a compact list" if mode == "grid" else "Show covers"
        )

    def _rebuild_grid(self, visible: Sequence[GameSummary]) -> None:
        flow = getattr(self, "_browse_flow", None)
        if flow is None:
            return
        selected = self._selected_identifier()
        self._grid_selecting = True
        try:
            for child in list(flow.get_children()):
                flow.remove(child)
            self._grid_tiles = {}
            self._grid_covers_loaded = 0
            self._grid_count = min(len(visible), self.GRID_BATCH)
            for game in visible[: self._grid_count]:
                tile = BrowseTile(self, game)
                self._grid_tiles[game.identifier] = tile
                flow.add(tile)
        finally:
            self._grid_selecting = False
        flow.show_all()
        self._rebuild_shelves()
        self._load_visible_covers()
        if selected and selected in self._grid_tiles:
            GLib.idle_add(self._restore_grid_selection, selected)

    def _rebuild_shelves(self) -> None:
        """Custom shelves above the cover grid, drawn from the loaded catalogue."""
        from . import shelves as shelves_lib

        box = getattr(self, "_shelves_box", None)
        if box is None:
            return
        for child in list(box.get_children()):
            box.remove(child)
        rows = [
            {
                "key": game.identifier,
                "title": game.title,
                "year": game.year,
                "genres": list(game.subjects),
                "rating": None,
            }
            for game in self._games
        ]
        rendered = shelves_lib.render(getattr(self, "settings", None), rows)
        for name, items in rendered:
            heading = Gtk.Label(label=name.upper(), xalign=0)
            heading.get_style_context().add_class("shelf-title")
            heading.set_margin_top(6)
            box.pack_start(heading, False, False, 0)
            flow = Gtk.FlowBox()
            flow.set_selection_mode(Gtk.SelectionMode.NONE)
            flow.set_homogeneous(False)
            flow.set_min_children_per_line(1)
            flow.set_max_children_per_line(12)
            flow.set_column_spacing(6)
            flow.set_row_spacing(4)
            for item in items:
                key = str(item.get("key") or "")
                button = Gtk.Button(label=str(item.get("title") or key))
                button.get_style_context().add_class("chip")
                button.set_relief(Gtk.ReliefStyle.NONE)
                button.set_tooltip_text(str(item.get("title") or key))
                button.connect("clicked", self._on_shelf_item, key)
                flow.add(button)
            box.pack_start(flow, False, False, 0)
        box.set_visible(bool(rendered))
        if rendered:
            box.show_all()

    def _on_shelf_item(self, _button: Gtk.Button, key: str) -> None:
        game = next((item for item in self._games if item.identifier == key), None)
        if game is not None:
            self._select_game(game)

    def _restore_grid_selection(self, identifier: str) -> bool:
        tile = self._grid_tiles.get(identifier)
        if tile is None:
            return False
        self._grid_selecting = True
        try:
            self._browse_flow.select_child(tile)
        finally:
            self._grid_selecting = False
        return False

    def _selected_identifier(self) -> str | None:
        game = getattr(self, "_current_game", None)
        return game.identifier if game is not None else None

    def _on_grid_selected(self, _flow: Gtk.FlowBox) -> None:
        if getattr(self, "_grid_selecting", False):
            return
        for child in self._browse_flow.get_selected_children():
            if isinstance(child, BrowseTile):
                self._select_game(child.game)
                return

    #: Tiles built per slice; the grid never materialises thousands at once.
    GRID_BATCH = 120

    def _on_grid_scrolled(self, adjustment: Gtk.Adjustment) -> None:
        self._load_visible_covers()
        if self._sidebar_stack.get_visible_child_name() != "grid":
            return
        if self._shown_limit >= len(self._visible_games):
            self._load_visible_covers()
            return
        remaining = adjustment.get_upper() - (
            adjustment.get_value() + adjustment.get_page_size()
        )
        if remaining <= 600:
            self._load_more_grid()

    def _load_more_grid(self) -> bool:
        if self._grid_count >= len(self._visible_games):
            return False
        self._grid_count = min(len(self._visible_games), self._grid_count + 60)
        for game in self._visible_games[: self._grid_count]:
            if game.identifier in self._grid_tiles:
                continue
            tile = BrowseTile(self, game)
            self._grid_tiles[game.identifier] = tile
            self._browse_flow.add(tile)
        self._browse_flow.show_all()
        self._load_visible_covers()
        self._update_row_note(len(self._visible_games), self._grid_count)
        return True

    def _load_visible_covers(self) -> None:
        """Request covers for the next slice of tiles, never all at once."""
        tiles = list(getattr(self, "_grid_tiles", {}).values())
        if not tiles:
            return
        target = min(len(tiles), self._grid_covers_loaded + 48)
        while self._grid_covers_loaded < target:
            tiles[self._grid_covers_loaded].load_cover()
            self._grid_covers_loaded += 1

    def _sync_archive_genres(self) -> None:
        """Keep the genre list in step with the tags present, with counts."""
        titles = {
            _bare_title(game.title)
            for game in self._games
            if str(game.title or "").strip()
        }
        self._genre_counts = genre_counts(self._games, titles=titles)
        self._rebuild_genre_rows()

    def _on_archive_filter_changed(self, *_args) -> None:
        self._apply_filter()

    def _sync_browse_sort(self) -> None:
        self._update_browse_direction_icon()

    def _update_browse_direction_icon(self) -> None:
        descending = bool(self._settings.browse_descending)
        icon = "pan-down-symbolic" if descending else "pan-up-symbolic"
        self._browse_desc.set_image(
            Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.MENU)
        )
        self._browse_desc.set_tooltip_text(
            "Descending — highest, largest and newest first (click for ascending)"
            if descending else
            "Ascending — lowest, smallest and oldest first (click for descending)"
        )
        self._browse_desc.get_accessible().set_name(
            "Sort descending" if descending else "Sort ascending"
        )

    def _on_browse_sort_changed(self, _combo: Gtk.ComboBoxText) -> None:
        value = self._browse_sort.get_active_id() or "title"
        try:
            self._settings.set_browse_sort(value)
        except (OSError, ValueError):
            pass
        self._apply_filter()

    def _on_browse_direction_clicked(self, _button: Gtk.Button) -> None:
        try:
            self._settings.set_browse_descending(
                not bool(self._settings.browse_descending)
            )
        except (OSError, ValueError):
            pass
        self._update_browse_direction_icon()
        self._apply_filter()

    def _sort_rows(self, row_a: Gtk.ListBoxRow,
                   row_b: Gtk.ListBoxRow) -> int:
        if not isinstance(row_a, GameRow) or not isinstance(row_b, GameRow):
            return 0
        rank_a = self._sort_rank.get(row_a.game.identifier, 1 << 30)
        rank_b = self._sort_rank.get(row_b.game.identifier, 1 << 30)
        return (rank_a > rank_b) - (rank_a < rank_b)

    def _reset_archive_filters(self) -> None:
        for combo in (
            self._year_combo,
            self._license_combo,
            self._format_combo,
        ):
            combo.set_active_id("all")
        self._set_year_bounds(None, None)
        self._size_bucket = "all"
        self._include_genres = set()
        self._exclude_genres = set()
        self._sync_genre_toggles()
        self._sync_chips()
        self._apply_filter()

    def _filter_archive(self, games: Sequence[GameSummary]) -> list[GameSummary]:
        license_group = self._license_combo.get_active_id() or "all"
        fmt = self._format_combo.get_active_id() or "all"
        size = getattr(self, "_size_bucket", "all")
        content = self._browse_content_filters()

        def keep(game: GameSummary) -> bool:
            if not size_matches(game.item_size, size):
                return False
            if license_group != "all" and _license_group(game.licenseurl) != license_group:
                return False
            if fmt != "all" and not _has_format(self._items.get(game.identifier), fmt):
                return False
            return browse_filters.matches(game, content)

        return [game for game in games if keep(game)]

    def _build_right_pane(self) -> Gtk.Widget:
        pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        pane.get_style_context().add_class("game-page")
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        top.get_style_context().add_class("game-header")
        back = _flat_button("go-previous-symbolic", "Back to Browse (Esc)")
        back.connect("clicked", lambda *_: self.go_back())
        top.pack_start(back, False, False, 0)
        crumb = Gtk.Label(label="Browse", xalign=0)
        crumb.get_style_context().add_class("crumb")
        top.pack_start(crumb, False, False, 0)
        pane.pack_start(top, False, False, 0)
        self._details = DetailsView(self)
        pane.pack_start(self._details, True, True, 0)

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bar.get_style_context().add_class("dir-bar")
        icon = Gtk.Image.new_from_icon_name("folder-symbolic", Gtk.IconSize.MENU)
        icon.get_style_context().add_class("dim")
        bar.pack_start(icon, False, False, 0)
        self._dir_label = Gtk.Label(label="", xalign=0)
        self._dir_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self._dir_label.get_style_context().add_class("dir-label")
        bar.pack_start(self._dir_label, True, True, 0)
        open_btn = _flat_button("folder-open-symbolic", "Open the download folder")
        open_btn.connect("clicked", lambda *_: self.open_folder(self._download_dir))
        bar.pack_start(open_btn, False, False, 0)
        choose = Gtk.Button(label="Choose folder…")
        choose.get_style_context().add_class("small-button")
        choose.set_tooltip_text("Choose where downloaded game folders are stored")
        choose.connect("clicked", lambda *_: self.choose_download_folder())
        bar.pack_start(choose, False, False, 0)
        pane.pack_start(bar, False, False, 0)
        return pane

    # -- lifecycle ----------------------------------------------------------

    def _on_show(self, *_args) -> None:
        if getattr(self, "_started", False):
            return
        self._started = True
        GLib.idle_add(self._restore_view)
        GLib.idle_add(self._focus_initial)
        self.load_collection()
        if self._manager.has_unfinished():
            self._queue_banner.set_reveal_child(True)
        if self.torrents.has_unfinished():
            self._ensure_torrent_poll()
        if self._settings.get("rescan_on_startup", True):
            GLib.timeout_add(400, self._startup_rescan)
        if self._startup_warning:
            GLib.timeout_add(600, self._show_startup_warning)

    def _startup_rescan(self) -> bool:
        if not self._closing and not self.library_view.scanning:
            self.library_view.rescan(silent=True)
        return False

    # -- views --------------------------------------------------------------

    def apply_theme(self, mode: str) -> None:
        """Apply dark, light, or the desktop preference immediately."""
        theme_gtk.apply(mode)

    def apply_interface_prefs(self) -> None:
        """Apply compact mode, the chosen accent and the transition amount.

        Called on start-up and again whenever the Tools window closes, so the
        Interface page has an effect without a restart.  Any preference that
        cannot be read falls back to the shipped look instead of failing.
        """
        try:
            prefs = view_prefs.effective(self._settings)
        except (AttributeError, TypeError, ValueError):
            return
        context = self.get_style_context()
        if prefs[view_prefs.COMPACT]:
            context.add_class("compact")
        else:
            context.remove_class("compact")
        if set_compact_layout(prefs[view_prefs.COMPACT]):
            # Tiles already on screen keep their size; rebuild the library so a
            # change from the Interface page is visible without a restart.
            refresh = getattr(getattr(self, "library_view", None), "refresh", None)
            if callable(refresh):
                refresh()
        visible = getattr(self, "_visible_games", None)
        rebuild = getattr(self, "_rebuild_grid", None)
        if callable(rebuild) and visible is not None:
            rebuild(visible)
        theme_gtk.set_accent(prefs[view_prefs.ACCENT])
        self._apply_transitions(str(prefs[view_prefs.TRANSITIONS]))
        self.queue_resize()

    def _apply_transitions(self, choice: str) -> None:
        """Set the page transition amount, honouring reduced motion."""
        if choice == "off" or not _animations_enabled():
            kind = Gtk.StackTransitionType.NONE
            duration = 0
        elif choice == "full":
            kind = Gtk.StackTransitionType.SLIDE_LEFT_RIGHT
            duration = 260
        else:
            kind = Gtk.StackTransitionType.CROSSFADE
            duration = 140
        try:
            self._main_stack.set_transition_type(kind)
            self._main_stack.set_transition_duration(duration)
        except (AttributeError, TypeError):
            return

    def show_palette(self) -> None:
        """Open the command palette over the main window."""
        from . import gtk_palette

        self._palette_window = gtk_palette.show_palette(self)

    def _style_view_switcher(self) -> None:
        for button in self._view_switcher.get_children():
            button.set_focus_on_click(False)

    def _name_switcher_buttons(self) -> bool:
        self._style_view_switcher()
        return False

    def _apply_view(self, name: str) -> None:
        if name not in ("archive", "library", "downloads", "media"):
            name = "archive"
        self._main_stack.set_visible_child_name(name)
        GLib.idle_add(self._name_switcher_buttons)
        GLib.idle_add(self._focus_initial)

    def _restore_view(self) -> bool:
        """Apply the view saved in settings once the window is on screen."""
        if not self._restore_pending:
            return False
        self._restore_pending = False
        self._apply_view(self._settings.active_view)
        return False

    def _on_view_changed(self, stack: Gtk.Stack, _param) -> None:
        name = stack.get_visible_child_name() or "archive"
        if self._restore_pending:
            return
        # The game page is opened from the grid, so it is never a saved view.
        if name != "game":
            try:
                self._settings.set_active_view(name)
            except (OSError, ValueError):
                pass
        # Only Browse searches the collection; the other pages keep their own.
        apply_search_visibility(self._search_pill, name)
        # Movies & TV has its own Back button, so the game history arrows
        # would only sit there confusingly (they cannot affect playback).
        nav = getattr(self, "_history_nav", None)
        if nav is not None:
            nav.set_visible(name != "media")
        self._sync_rail()
        if name == "library":
            self.library_view.refresh()
        elif name == "downloads":
            self.downloads_panel.update_torrents(self.torrents.tasks())
            self._ensure_torrent_poll()
        elif name == "media":
            try:
                self.media_view.refresh_providers()
            except Exception:  # noqa: BLE001 - best-effort
                pass
        if name != "media":
            try:
                self.media_view.stop_playback()
            except Exception:  # noqa: BLE001 - best-effort
                pass
        GLib.idle_add(self._focus_initial)

    def active_view(self) -> str:
        return self._main_stack.get_visible_child_name() or "archive"

    def refresh_active_view(self) -> None:
        if self.active_view() == "library":
            self.library_view.rescan()
        else:
            self.load_collection(force=True)

    def focus_search(self) -> None:
        if self.active_view() == "library":
            self.library_view.focus_search()
            return
        self._search.grab_focus()
        self._search.select_region(0, -1)

    def resume_unfinished(self) -> None:
        try:
            count = self._manager.resume()
        except (OSError, ValueError) as exc:
            self.toast(f"Could not resume downloads: {_error_text(exc)}", error=True)
            return
        if count:
            self.set_downloads_visible(True)
            self.toast(f"Resumed {count} {_plural(count, 'download')}.")
        else:
            self.toast("Nothing left to resume.")
        self._queue_banner.set_reveal_child(False)

    def _focus_initial(self) -> bool:
        if self._closing or not self.get_realized():
            return False
        view = self._main_stack.get_visible_child_name()
        if view == "library":
            self.library_view.focus_search()
        elif view == "archive":
            self._search.grab_focus()
        return False

    def _show_startup_warning(self) -> bool:
        if self._startup_warning:
            self.toast(self._startup_warning, error=True)
        return False

    def toast(self, text: str, error: bool = False) -> None:
        self._toast.show_message(text, error=error)

    def game_title(self, identifier: str) -> str:
        game = self._games_by_id.get(identifier)
        return game.title if game is not None else identifier

    def set_download_badge(self, count: int) -> None:
        badge = getattr(self, "_badge", None)
        if badge is None:
            return
        badge.set_text(str(count))
        if count:
            badge.show()
        else:
            badge.hide()

    def set_downloads_visible(self, visible: bool) -> None:
        """Show the Downloads page, or leave it for Browse."""
        if visible:
            self._main_stack.set_visible_child_name("downloads")
            self.downloads_panel.update_torrents(self.torrents.tasks())
            self._ensure_torrent_poll()
        elif self._main_stack.get_visible_child_name() == "downloads":
            self._main_stack.set_visible_child_name("archive")

    def _apply_download_dir_label(self) -> None:
        self._dir_label.set_text(_shorten_home(self._download_dir))
        self._dir_label.set_tooltip_text(str(self._download_dir))

    # -- collection ---------------------------------------------------------

    def load_collection(self, force: bool = False) -> None:
        if self._collection_loading or self._closing:
            return
        self._collection_loading = True
        self._collection_error = None
        self._sidebar_spinner.start()
        if not self._games:
            self._sidebar_count.set_text("Loading…")
            self._sidebar_stack.set_visible_child_name("loading")

        def work() -> CollectionResult:
            return self._client.fetch_collection(force=force)

        _spawn(work, self._on_collection_loaded, self._on_collection_failed)

    def _on_collection_loaded(self, result: CollectionResult) -> None:
        if self._closing:
            return
        self._collection_loading = False
        self._sidebar_spinner.stop()

        previous = self._current_game.identifier if self._current_game else None
        self._games = list(result.games)
        self._merge_catalog_games()
        self._populate_games()

        self._search.set_placeholder_text(
            f"Search {len(self._games)} games" if self._games else "Search games"
        )
        self._sidebar_footer.show()
        if result.fetched_at:
            stamp = time.strftime("%d %b %Y, %H:%M", time.localtime(result.fetched_at))
        else:
            stamp = ""
        source = "Saved list" if result.from_cache else "Live list"
        footer = f"{source} · {stamp}" if stamp else source
        self._sidebar_footer.set_text(footer)
        self._sidebar_footer.set_tooltip_text(
            f"{len(self._games)} items · {footer}"
        )

        if result.warning:
            self._show_banner(result.warning)
        else:
            self._banner.set_reveal_child(False)

        target = previous or self._settings.last_game
        if target and target in self._visible_ids:
            self._restore_selection(target)
        elif self._current_game is None and not self._search.get_text().strip():
            self._select_first_visible()

    def _on_collection_failed(self, exc: BaseException) -> None:
        if self._closing:
            return
        self._collection_loading = False
        self._sidebar_spinner.stop()
        message = _error_text(exc)
        self._collection_error = message
        self._sidebar_error_label.set_text(message)
        # Imported catalog records are still usable offline, so show them
        # instead of an empty error page when the archive cannot be reached.
        self._merge_catalog_games()
        if not self._games:
            self._sidebar_count.set_text("Unavailable")
            self._sidebar_stack.set_visible_child_name("error")
        else:
            self._populate_games()
        self._show_banner(
            f"Could not refresh the collection. {message}"
        )

    def _show_banner(self, text: str) -> None:
        self._banner_label.set_text(text)
        self._banner.set_reveal_child(True)

    def _merge_catalog_games(self) -> None:
        """Append imported catalog records to the Browse list.

        Records whose source id already exists as an archive.org identifier are
        skipped so the same game never appears twice.  The row list is capped
        separately; the full list stays available to search and filters.
        """
        catalog_games, records = self._catalog_summaries()
        self._catalog_games = catalog_games
        self._artwork_sync_item.set_sensitive(bool(catalog_games))
        self._artwork_retry_item.set_sensitive(bool(catalog_games))
        self._catalog_records = records
        base = [game for game in self._games if not game.is_catalog]
        known = {game.identifier for game in base}
        known.update(game.identifier for game in base if game.identifier)
        merged = list(base)
        for game in catalog_games:
            record = records.get(game.catalog_key)
            if record is not None and record.game_id in known:
                continue
            merged.append(game)
        self._games = merged
        self._games_by_id = {game.identifier: game for game in self._games}
        self._search.set_placeholder_text(
            f"Search {len(self._games)} games" if self._games else "Search games"
        )

    def _catalog_summaries(self) -> tuple[list[GameSummary], dict[str, CatalogGame]]:
        records: dict[str, CatalogGame] = {}
        games: list[GameSummary] = []
        for record in self.catalog_store.iter_games():
            key = record.key
            source = self.catalog_store.get_source(record.source_id)
            games.append(
                GameSummary(
                    identifier=f"catalog:{key}",
                    title=record.title or record.game_id,
                    year=record.year,
                    downloads=0,
                    item_size=self.catalog_store.estimated_size(key),
                    creator=record.creator or record.developer or record.publisher,
                    subjects=list(record.genres),
                    catalog_key=key,
                    catalog_source=(source.name if source is not None else "") or record.source_id,
                    source_url=record.source_url,
                    cover_url=record.cover_url,
                    download_state=self.catalog_store.download_state(key),
                )
            )
            records[key] = record
        return games, records

    def reload_catalog(self, announce: bool = False) -> None:
        """Re-read imported catalog records and refresh the Browse list."""
        previous = self._current_game.identifier if self._current_game else None
        self._merge_catalog_games()
        self._populate_games()
        if previous and previous in self._games_by_id:
            GLib.idle_add(self._restore_selection, previous)
        if announce and self._catalog_games:
            self.toast(f"Browse now lists {len(self._catalog_games)} imported catalog game(s).")

    def sync_collection_artwork(self, force: bool = False) -> None:
        """Sync posters and details for every imported game that needs them.

        Runs in a worker, saves in batches, and stops on a second click.  A game
        whose provider results are ambiguous is left alone and counted: a batch
        is not a place to guess which game a name means.

        Lookups are bounded-concurrent (``artwork.SYNC_WORKERS`` requests        in flight) because each one is a full HTTP round-trip, and a game that had
        no match is remembered for a week so the next run does not re-ask.

        ``force`` ignores that memory: the person asked again, so asking the
        provider again is the whole point of the run.
        """
        if self._artwork_sync_running:
            self._artwork_sync_cancel = True
            self.toast("Stopping the artwork sync after the requests in flight…")
            return
        key = self.settings.api_key("rawg")
        if not key:
            # No key is not a reason to leave every game without a poster: the
            # keyless store search can still cover most of them, it just cannot
            # supply descriptions, genres or screenshots.
            self.toast(
                "No RAWG API key is set, so only posters from the Steam store "
                "search are added (Settings → Providers adds the key)."
            )
        recently_checked = time.time()
        records = [
            record for record in self.catalog_store.iter_games()
            if artwork_needed(self.catalog_store, record)
            and (force or not artwork_miss_remembered(record, recently_checked))
        ]
        if not records:
            self.toast(
                "Every imported game already has a poster, or was searched "
                "recently with no match. Use “Re-check games with no artwork "
                "match” to ask again now."
            )
            return
        total = len(records)
        self._artwork_sync_running = True
        self._artwork_sync_cancel = False
        self._artwork_sync_item.set_label("Stop artwork sync")
        self._show_banner(f"Syncing artwork for {total} imported game(s)…")

        def work() -> object:
            from concurrent.futures import ThreadPoolExecutor, as_completed

            from .sources import RawgSource
            from .steamart import SteamArtSource

            rawg = RawgSource(key) if key else None
            # RAWG first - it also fills description, genres and screenshots -
            # then the keyless store search for whatever it cannot answer,
            # including the whole run when its quota is spent.
            searchers = ArtworkSearchers(
                [rawg, SteamArtSource()] if rawg is not None else [SteamArtSource()]
            )
            summary = {
                "added": 0, "ambiguous": 0, "missing": 0, "failed": 0,
                "unchanged": 0, "cancelled": False, "total": total,
                "skipped": 0, "stop_status": None, "stop_detail": "",
            }
            guard = threading.Lock()
            applied = 0
            finished = 0
            last_tick = 0.0

            def lookup(record: CatalogGame) -> tuple[str, str, object]:
                """Ask each provider in turn, then fetch screenshots when useful."""
                if searchers.exhausted:
                    return record.key, "skipped", None
                title = (record.title or record.game_id or "").strip()
                if not title:
                    return record.key, "missing", None
                # One request per provider, so ask for a wide page: the entry
                # whose title matches exactly is often not in the top few.
                chosen = searchers.choose(title, record.year, limit=20)
                if chosen is None:
                    return (
                        record.key,
                        "skipped" if searchers.exhausted else "missing",
                        None,
                    )
                extra = record.extra if isinstance(record.extra, dict) else {}
                if (
                    rawg is not None
                    and chosen.provider == rawg.id
                    and not extra.get("screenshots")
                ):
                    # A second request per game; not needed once they are stored.
                    chosen = with_artwork_screenshots(rawg, chosen)
                return record.key, "chosen", chosen

            def record_result(game_key: str, status: str, chosen: object) -> None:
                nonlocal applied
                with guard:
                    if isinstance(chosen, ArtworkCandidate):
                        try:
                            filled = apply_artwork_candidate(
                                self.catalog_store, game_key, chosen
                            )
                        except SourceError:
                            summary["failed"] += 1
                            return
                        summary["added" if filled else "unchanged"] += 1
                        if not chosen.cover_url:
                            # The match has no artwork to give; do not re-ask
                            # about this game on every run.
                            try:
                                remember_artwork_miss(self.catalog_store, game_key)
                            except (KeyError, ValueError):
                                pass
                        applied += 1
                        if applied % 100 == 0:
                            try:
                                self.catalog_store.save()
                            except OSError:
                                pass
                        return
                    if status == "skipped":
                        # Not a match failure: the request never reached a
                        # provider that would answer, so nothing is remembered.
                        summary["skipped"] += 1
                        return
                    if status in ("missing", "ambiguous"):
                        # Remember the miss so the next run does not re-ask.
                        try:
                            remember_artwork_miss(self.catalog_store, game_key)
                        except (KeyError, ValueError):
                            pass
                    summary[status if status in summary else "failed"] += 1

            pool = ThreadPoolExecutor(
                max_workers=max(1, min(ARTWORK_SYNC_WORKERS, total)),
                thread_name_prefix="lgl-artwork",
            )
            futures = [pool.submit(lookup, record) for record in records]
            try:
                for future in as_completed(futures):
                    if self._artwork_sync_cancel:
                        summary["cancelled"] = True
                        for pending in futures:
                            pending.cancel()
                        break
                    try:
                        game_key, status, chosen = future.result()
                    except Exception:  # noqa: BLE001 - one game must not stop the run
                        summary["failed"] += 1
                        finished += 1
                        continue
                    record_result(game_key, status, chosen)
                    finished += 1
                    if searchers.exhausted:
                        # Every provider refused the request itself, so
                        # everything still queued would be skipped anyway.
                        refusals = searchers.refused()
                        exc = refusals[-1] if refusals else None
                        summary["stop_status"] = getattr(exc, "status", None)
                        summary["stop_detail"] = str(
                            getattr(exc, "detail", "") or ""
                        )
                        for pending_future in futures:
                            pending_future.cancel()
                        summary["skipped"] += total - finished
                        break
                    tick = time.monotonic()
                    if tick - last_tick > 0.4:
                        last_tick = tick
                        GLib.idle_add(self._artwork_sync_progress, finished, total)
            finally:
                # Wait for the requests already in flight: they touch the store.
                pool.shutdown(wait=True, cancel_futures=True)
            with guard:
                try:
                    self.catalog_store.save()
                except OSError:
                    pass
            return summary

        def done(result: object) -> None:
            summary = result if isinstance(result, dict) else {}
            self._artwork_sync_running = False
            self._artwork_sync_cancel = False
            self._artwork_sync_item.set_label("Sync artwork for collection")
            self._banner.set_reveal_child(False)
            self.reload_catalog()
            parts: list[str] = []
            if summary.get("added"):
                parts.append(f"added artwork to {summary['added']}")
            if summary.get("ambiguous"):
                parts.append(f"{summary['ambiguous']} need a manual pick")
            if summary.get("missing"):
                parts.append(f"{summary['missing']} had no match")
            if summary.get("failed"):
                parts.append(f"{summary['failed']} failed")
            if summary.get("skipped") and summary.get("stop_status"):
                parts.append(f"{summary['skipped']} not checked")
            if summary.get("cancelled"):
                parts.append("stopped early")
            detail = summary.get("stop_detail") or "the provider refused the request"
            status = summary.get("stop_status")
            self.toast(
                "Artwork sync: "
                + (", ".join(parts) if parts else "nothing to add")
                + "."
                + (
                    f" Stopped: {detail} (HTTP {status}). Nothing else was "
                    "changed; sync again once the provider accepts requests."
                    if status
                    else ""
                ),
                error=bool(status),
            )

        def failed(exc: BaseException) -> None:
            self._artwork_sync_running = False
            self._artwork_sync_cancel = False
            self._artwork_sync_item.set_label("Sync artwork for collection")
            self._banner.set_reveal_child(False)
            self.toast(f"Artwork sync failed: {_error_text(exc)}", error=True)

        _spawn(work, done, failed)

    def _artwork_sync_progress(self, done: int, total: int) -> bool:
        if self._artwork_sync_running:
            self._show_banner(f"Syncing artwork… {done} of {total}")
        return False

    def sync_catalog_artwork(self, record: CatalogGame) -> None:
        """Search RAWG for this imported record's name and attach what it returns.

        A name is not an identity: only a single exact title match is applied
        without asking, and anything ambiguous opens a picker instead.
        """
        key = self.settings.api_key("rawg")
        if not key:
            self.toast(
                "Artwork sync needs a free RAWG API key: Settings → Providers.",
                error=True,
            )
            return
        title = (record.title or record.game_id or "").strip()
        if not title:
            self.toast("This record has no title to search for.", error=True)
            return
        self.toast(f"Searching RAWG for “{title[:48]}”…")

        def work() -> object:
            from .sources import RawgSource

            source = RawgSource(key)
            return source, find_artwork_candidates(source, title)

        def found(result: object) -> None:
            if not isinstance(result, tuple) or len(result) != 2:
                return
            source, games = result
            candidates = rank_artwork_candidates(games, title, record.year)
            if not candidates:
                self.toast(f"No RAWG match for “{title[:48]}”.", error=True)
                return
            chosen = pick_artwork_candidate(candidates, title, record.year)
            if chosen is not None:
                self._attach_catalog_artwork(record, chosen, source)
                return
            dialog = ArtworkMatchDialog(self, title, candidates)
            response = dialog.run()
            picked = dialog.selected if response == Gtk.ResponseType.OK else None
            dialog.destroy()
            if picked is not None:
                self._attach_catalog_artwork(record, picked, source)

        _spawn(
            work,
            found,
            lambda exc: self.toast(
                f"RAWG lookup failed: {_error_text(exc)}", error=True
            ),
        )

    def _attach_catalog_artwork(
        self, record: CatalogGame, candidate: ArtworkCandidate, source: object
    ) -> None:
        """Fetch the chosen result's screenshots, then merge it into the record."""
        self.toast(f"Loading artwork for “{candidate.title[:40]}”…")

        def work() -> object:
            return with_artwork_screenshots(source, candidate)

        def done(result: object) -> None:
            if not isinstance(result, ArtworkCandidate):
                return
            filled = apply_artwork_candidate(
                self.catalog_store, record.key, result, replace_cover=True
            )
            try:
                self.catalog_store.save()
            except OSError as exc:
                self.toast(f"The catalog could not be saved: {exc}", error=True)
            self.reload_catalog()
            # Re-render the panel directly: selection restore may skip it when
            # the same row stays selected, and then the new poster is invisible.
            current = self._current_game
            if current is not None and current.catalog_key == record.key:
                self._show_catalog_game(current)
            if filled:
                self.toast(f"Added {', '.join(filled)} from RAWG.")
            else:
                self.toast("RAWG had nothing this record was missing.")

        _spawn(
            work,
            done,
            lambda exc: self.toast(
                f"RAWG artwork could not be read: {_error_text(exc)}", error=True
            ),
        )

    def _populate_games(self) -> None:
        self._apply_filter()

    #: At most this many rows are materialised at once; the full list is still
    #: searched and filtered so thousands of imported records stay responsive.
    ROW_LIMIT = 400

    def _displayed_games(self, visible: Sequence[GameSummary]) -> list[GameSummary]:
        """The rows that should exist now: a window of the result set."""
        shown = list(visible[: self._shown_limit])
        current_id = self._current_game.identifier if self._current_game else None
        if current_id and current_id not in {game.identifier for game in shown}:
            selected = next(
                (game for game in visible if game.identifier == current_id), None
            )
            if selected is not None:
                shown.append(selected)
        return shown

    def _update_row_note(self, total: int, shown: int) -> None:
        if total > shown:
            self._row_note.set_text(
                f"Showing {shown} of {total} matches — scroll for more or refine "
                "the search."
            )
            self._row_note.set_visible(True)
        else:
            self._row_note.set_visible(False)

    def _rebuild_rows(self, visible: Sequence[GameSummary]) -> None:
        for row in list(self._list.get_children()):
            self._list.remove(row)
        shown = self._displayed_games(visible)
        for game in shown:
            self._list.add(GameRow(game))
        self._list.show_all()
        self._update_row_note(len(visible), len(shown))

    def _load_more_rows(self) -> bool:
        """Append the next batch instead of rebuilding thousands of rows."""
        if self._shown_limit >= len(self._visible_games):
            return False
        self._shown_limit += self.ROW_LIMIT
        shown = self._displayed_games(self._visible_games)
        existing = {
            row.game.identifier for row in self._list.get_children()
            if isinstance(row, GameRow)
        }
        for game in shown:
            if game.identifier not in existing:
                self._list.add(GameRow(game))
        self._list.show_all()
        self._update_row_note(len(self._visible_games), len(shown))
        return True

    def _on_sidebar_scrolled(self, adjustment: Gtk.Adjustment) -> None:
        if self._sidebar_stack.get_visible_child_name() != "list":
            return
        if self._shown_limit >= len(self._visible_games):
            return
        remaining = adjustment.get_upper() - (adjustment.get_value() + adjustment.get_page_size())
        if remaining <= 240:
            self._load_more_rows()

    def _apply_filter(self) -> None:
        if self._search_source:
            GLib.source_remove(self._search_source)
            self._search_source = 0
        query = self._search.get_text().strip()
        self._sync_archive_genres()
        visible = _sorted_games(
            self._filter_archive(ArchiveClient.search(self._games, query)),
            self._settings.browse_sort,
            bool(self._settings.browse_descending),
        )
        self._visible_games = visible
        self._shown_limit = self.ROW_LIMIT
        self._visible_ids = {game.identifier for game in visible}
        self._sort_rank = {
            game.identifier: index for index, game in enumerate(visible)
        }
        self._rebuild_rows(visible)
        self._list.invalidate_sort()
        self._list.invalidate_filter()

        filters_active = self._archive_filters_active()
        self._sync_chips()
        self._update_filter_summary()
        fmt = self._format_combo.get_active_id() or "all"
        if fmt != "all":
            self._format_note.set_text(
                "Format filtering only covers games whose file list has been "
                "inspected in this session."
            )
            self._format_note.set_visible(True)
        else:
            self._format_note.set_visible(False)

        if not self._games:
            self._sidebar_count.set_text("No games")
        elif query or filters_active:
            self._sidebar_count.set_text(
                f"{count_label(len(visible))} of {count_label(len(self._games))}"
            )
        else:
            self._sidebar_count.set_text(
                f"{count_label(len(self._games))} "
                f"{_plural(len(self._games), 'game')}"
            )

        if self._collection_loading and not self._games:
            page = "loading"
        elif self._collection_error and not self._games:
            page = "error"
        elif not self._games:
            page = "empty"
        elif (query or filters_active) and not visible:
            self._no_results_label.set_text("No matches")
            page = "noresults"
        else:
            page = "grid" if getattr(self, "_browse_view", "grid") == "grid" else "list"
        if page == "grid":
            self._rebuild_grid(visible)
        self._sidebar_stack.set_visible_child_name(page)

    def _apply_filter_idle(self) -> bool:
        self._search_source = 0
        self._apply_filter()
        return False

    def _on_search_changed(self, _entry: Gtk.SearchEntry) -> None:
        if self._search_source:
            GLib.source_remove(self._search_source)
        self._search_source = GLib.timeout_add(180, self._apply_filter_idle)

    def _on_search_activate(self, _entry: Gtk.SearchEntry) -> None:
        if self._search_source:
            GLib.source_remove(self._search_source)
            self._search_source = 0
        self._apply_filter()
        self._select_first_visible()

    def _clear_search(self) -> None:
        self._search.set_text("")
        self._apply_filter()
        self._search.grab_focus()

    def _game_visible(self, row: Gtk.ListBoxRow) -> bool:
        return isinstance(row, GameRow) and row.game.identifier in self._visible_ids

    def _select_first_visible(self) -> None:
        if self._sidebar_stack.get_visible_child_name() != "list":
            return
        for row in self._list.get_children():
            if isinstance(row, GameRow) and row.game.identifier in self._visible_ids:
                self._list.select_row(row)
                if row.get_mapped():
                    row.grab_focus()
                return

    def _restore_selection(self, identifier: str) -> None:
        for row in self._list.get_children():
            if isinstance(row, GameRow) and row.game.identifier == identifier:
                self._list.select_row(row)
                GLib.idle_add(self._scroll_row_into_view, row)
                return

    def _scroll_row_into_view(self, row: Gtk.ListBoxRow) -> bool:
        allocation = row.get_allocation()
        adjustment = self._sidebar_scroller.get_vadjustment()
        if allocation.height > 0 and adjustment is not None:
            top = allocation.y
            bottom = top + allocation.height
            value = adjustment.get_value()
            page = adjustment.get_page_size()
            if top < value:
                adjustment.set_value(max(0.0, top))
            elif bottom > value + page:
                adjustment.set_value(bottom - page)
        return False

    def _on_row_selected(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if not isinstance(row, GameRow):
            return
        if self._selection_source:
            GLib.source_remove(self._selection_source)
        self._selection_source = GLib.timeout_add(
            80, self._commit_selection, row.game.identifier
        )

    def _commit_selection(self, identifier: str) -> bool:
        self._selection_source = 0
        if self._closing:
            return False
        game = self._games_by_id.get(identifier)
        if game is not None:
            self._select_game(game)
        return False

    def _select_game(self, game: GameSummary) -> None:
        if (
            self._current_game is not None
            and self._current_game.identifier == game.identifier
            and self._current_item is not None
        ):
            self._main_stack.set_visible_child_name("game")
            return
        self._current_game = game
        self._current_item = None
        back = getattr(self, "_back_btn", None)
        if back is not None:
            back.set_sensitive(True)
        self._main_stack.set_visible_child_name("game")
        try:
            self._settings.set_last_game(game.identifier)
        except (OSError, ValueError):
            pass
        if game.is_catalog:
            self._show_catalog_game(game)
            return
        self._load_item(game)

    def _show_catalog_game(self, game: GameSummary) -> None:
        record = self._catalog_records.get(game.catalog_key)
        if record is None:
            # The record was removed while selected; fall back to an empty page.
            self._details.show_empty()
            return
        self._details.show_catalog(
            game,
            record,
            magnets=self.catalog_store.magnets_for(game.catalog_key),
            mirrors=self.catalog_store.mirrors_for(game.catalog_key),
        )

    def _load_item(self, game: GameSummary, force: bool = False) -> None:
        if game.is_catalog:
            self._show_catalog_game(game)
            return
        self._item_token += 1
        token = self._item_token
        cached = self._items.get(game.identifier)
        if cached is not None and not force:
            self._current_item = cached
            self._details.show_item(cached)
            self._refresh_local()
            return

        self._details.show_loading(game.title)

        def work() -> ArchiveItem:
            with self._archive_lock:
                return self._client.fetch_item(game.identifier, force=force)

        def success(item: object) -> None:
            self._on_item_loaded(token, game, item)

        def failure(exc: object) -> None:
            self._on_item_failed(token, game, exc)

        _spawn(work, success, failure)

    def _on_item_loaded(self, token: int, game: GameSummary, item: object) -> None:
        if self._closing or token != self._item_token or not isinstance(item, ArchiveItem):
            return
        self._items[item.identifier] = item
        self._current_item = item
        self._details.show_item(item)
        self._refresh_local()

    def _on_item_failed(self, token: int, game: GameSummary,
                        exc: BaseException) -> None:
        if self._closing or token != self._item_token:
            return
        message = _error_text(exc)
        self._details.show_error(
            message, lambda: self._load_item(game, force=True)
        )

    def image_hosts_for(self, source_id: str) -> tuple[str, ...]:
        """Hosts approved for this source: its adapter's, plus what it named.

        The argument may be the source id or its display name: catalog details
        pass the id, while Browse summaries carry ``source.name``.  Matching
        only the id left that allow-list empty, so every imported cover was
        refused and the grid fell back to letter placeholders.
        """
        record = self.catalog_store.get_source(source_id)
        if record is None and source_id:
            for candidate in self.catalog_store.sources.values():
                if candidate.name == source_id:
                    record = candidate
                    break
        if record is None:
            return ()
        hosts: list[str] = []
        for adapter in SOURCE_TYPES:
            if (
                adapter.__name__ == record.adapter
                or getattr(adapter, "id", "") == record.source_id
            ):
                hosts.extend(adapter.image_hosts)
        for host in record.image_hosts:
            if host not in hosts:
                hosts.append(host)
        return tuple(hosts)

    def open_magnet_uri(self, uri: str) -> None:
        """Hand a magnet URI to the desktop's magnet handler.

        The catalog details panel reaches this through the window, while the
        library view owns the desktop hand-off itself.  The Settings field is
        the aria2c engine path, so it is not used here.
        """
        view = getattr(self, "library_view", None)
        if view is None:
            self.toast("No magnet handler is available yet.", error=True)
            return
        view.open_magnet_uri(uri)

    def can_fetch_image(self, url: str, source_id: str) -> bool:
        """Allow archive artwork and hosts this source actually pointed at."""
        try:
            split = urllib.parse.urlsplit(url)
        except ValueError:
            return False
        host = (split.hostname or "").lower()
        if split.scheme != "https" or not host:
            return False
        if host == "archive.org" or host.endswith(".archive.org"):
            return True
        for allowed in self.image_hosts_for(source_id):
            if host == allowed or host.endswith("." + allowed):
                return True
        return False

    def fetch_poster(self, identifier: str, url: str,
                     on_success: Callable[[object], None],
                     on_error: Callable[[object], None],
                     allowed_hosts: Sequence[str] | None = None) -> None:
        """Fetch image bytes in a small pool; decode happens in the idle callback.

        Images never take the archive lock, so selecting a game, its poster and
        its screenshots all make progress at the same time.  Identical URLs
        requested twice (poster plus thumbnail, for example) share one fetch.
        """
        if not url:
            on_error(ArchiveError("No image URL."))
            return
        with self._image_lock:
            waiting = self._image_inflight.get(url)
            if waiting is not None:
                waiting.append((on_success, on_error))
                return
            self._image_inflight[url] = [(on_success, on_error)]

        def deliver(success: bool, payload: object) -> None:
            with self._image_lock:
                waiters = self._image_inflight.pop(url, [])
            for ok_cb, error_cb in waiters:
                try:
                    ok_cb(payload) if success else error_cb(payload)
                except Exception:
                    continue

        def runner() -> None:
            try:
                with self._image_slots:
                    data = self._client.fetch_image(
                        url, identifier, timeout=_IMAGE_TIMEOUT,
                        allowed_hosts=allowed_hosts,
                    )
            except BaseException as exc:  # noqa: BLE001 - reported to the UI
                GLib.idle_add(deliver, False, exc)
            else:
                GLib.idle_add(deliver, True, data)

        threading.Thread(target=runner, daemon=True, name="lgl-image").start()

    def _refresh_local(self) -> None:
        """Scan the downloaded folder in the background, then show its files."""
        game = self._current_game
        if game is None or self._current_item is None:
            return
        try:
            validate_identifier(game.identifier)
        except ValueError:
            return
        folder = self._download_dir / game.identifier
        self._local_token += 1
        token = self._local_token

        def work() -> GameFolder:
            try:
                return scan_game_folder(folder)
            except OSError:
                return GameFolder(path=folder, files=[], executables=[], documents=[])

        def success(scanned: object) -> None:
            if self._closing or token != self._local_token:
                return
            if not isinstance(scanned, GameFolder):
                return
            current = self._current_item
            if current is None or current.identifier != game.identifier:
                return
            self._details.set_local(scanned, folder)

        _spawn(work, success, lambda _exc: None)

    # -- downloads ----------------------------------------------------------

    def download_files(self, item: ArchiveItem, files: Sequence[ArchiveFile]) -> None:
        if not files:
            return
        if item.restricted:
            self.toast("This archive item is restricted; downloads are unavailable.", error=True)
            return
        try:
            download_root = check_download_dir(self._download_dir)
        except (OSError, ValueError) as exc:
            self.toast(f"Cannot use the download folder: {exc}", error=True)
            return

        added = 0
        last_task: str | None = None
        errors: list[str] = []
        for archive_file in files:
            if not archive_file.downloadable:
                continue
            try:
                task_id = self._manager.add(
                    identifier=item.identifier,
                    filename=archive_file.name,
                    url=item.file_url(archive_file),
                    download_dir=download_root,
                    total_size=archive_file.size,
                )
            except (AppError, ValueError, OSError) as exc:
                errors.append(f"{archive_file.name}: {_error_text(exc)}")
                continue
            added += 1
            last_task = task_id

        if added:
            try:
                write_manifest(game_folder(download_root, item.identifier), item)
            except (OSError, ValueError) as exc:
                errors.append(f"Game metadata was not saved: {_error_text(exc)}")
            self.set_downloads_visible(True)
            if last_task is not None:
                self.downloads_panel.select_task(last_task)
            self._refresh_local()

        if added and not errors:
            self.toast(
                f"Added {added} {_plural(added, 'file')} to downloads."
            )
        elif added:
            self.toast(
                f"Added {added} {_plural(added, 'file')}. "
                f"{len(errors)} could not be added: {errors[0]}",
                error=True,
            )
        elif errors:
            self.toast(errors[0], error=True)
        else:
            self.toast("No downloadable files were selected.", error=True)

    def update_downloads(self, snapshots: Sequence[TaskSnapshot]) -> None:
        self.downloads_panel.update(snapshots)

    # -- torrents -----------------------------------------------------------

    def _ensure_torrent_poll(self) -> None:
        if self._torrent_timer or self._closing:
            return
        self._torrent_timer = GLib.timeout_add(1000, self._torrent_tick)

    def _torrent_tick(self) -> bool:
        if self._closing:
            self._torrent_timer = 0
            return False
        visible = self._downloads_revealer.get_reveal_child()
        if not self.torrents.has_unfinished() and not visible:
            self._torrent_timer = 0
            return False
        self._refresh_torrents_async()
        return True

    def _refresh_torrents_async(self) -> None:
        if self._torrent_refreshing:
            return
        self._torrent_refreshing = True

        def work() -> list[TorrentTask]:
            return self.torrents.refresh()

        def success(_result: object) -> None:
            self._torrent_refreshing = False
            if self._closing:
                return
            self.downloads_panel.update_torrents(self.torrents.tasks())

        def failure(_exc: object) -> None:
            # A dead engine must not break the timer or the window.
            self._torrent_refreshing = False

        _spawn(work, success, failure)

    def torrents_changed(self) -> None:
        self.downloads_panel.update_torrents(self.torrents.tasks())
        self.downloads_panel.update_engine_hint()
        self._ensure_torrent_poll()

    def torrent_engine_changed(self, path: str) -> None:
        """Apply a new aria2c path immediately, with no restart."""
        try:
            self.torrents.set_engine_path(path)
        except (OSError, ValueError):
            pass
        self.downloads_panel.update_engine_hint()

    def _torrent_completed_from_thread(self, task: TorrentTask) -> None:
        GLib.idle_add(self._on_torrent_completed, task)

    def _on_torrent_completed(self, task: TorrentTask) -> bool:
        if self._closing or task.task_id in self._torrent_finished:
            return False
        self._torrent_finished.add(task.task_id)
        self.library_view.schedule_rescan()
        self.downloads_panel.update_torrents(self.torrents.tasks())
        title = task.title or f"{task.infohash[:12]}…"
        self.toast(f"Torrent finished: {title}.")
        folder = task.folder or task.save_dir
        self._notify_download(
            "Torrent finished",
            f"{title} was saved to {_shorten_home(Path(folder))}."
            if folder else f"{title} finished.",
        )
        return False

    def start_torrent_download(self, record: MagnetRecord, title: str) -> None:
        """Confirm and start one magnet from a catalog record."""
        info = self.torrents.engine_status()
        if not info.available:
            dialog = Gtk.MessageDialog(
                transient_for=self,
                modal=True,
                message_type=Gtk.MessageType.INFO,
                buttons=Gtk.ButtonsType.OK,
                text="Torrent engine not found",
            )
            dialog.format_secondary_text(
                f"{info.reason or info.summary}\n\n"
                "An aria2c path can be set in Settings → General. Leave it "
                "empty to use the built-in torrent engine."
            )
            dialog.run()
            dialog.destroy()
            return

        confirm = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.CANCEL,
            text="Download this game over BitTorrent?",
        )
        confirm.add_button("Download", Gtk.ResponseType.OK)
        confirm.format_secondary_text(
            "The magnet link comes from the source's metadata and is not "
            f"verified. The download is saved to {_shorten_home(self._download_dir)}, "
            "seeding is off, and only content you have the right to download "
            "should be fetched."
        )
        response = confirm.run()
        confirm.destroy()
        if response != Gtk.ResponseType.OK:
            return

        try:
            task = self.torrents.add(record.uri, title=title)
            self.torrents.start(task.task_id)
        except (AppError, OSError, ValueError) as exc:
            self.toast(_error_text(exc), error=True)
            self.torrents_changed()
            return

        self.set_downloads_visible(True)
        self.downloads_panel.select_torrent(task.task_id)
        self.torrents_changed()
        self.toast("Torrent download started.")

    def on_task_completed(self, snapshot: TaskSnapshot) -> None:
        if (
            self._current_game is not None
            and snapshot.identifier == self._current_game.identifier
        ):
            self._refresh_local()
        if self.active_view() == "library":
            self.library_view.schedule_rescan()
        self.toast(f"Downloaded {snapshot.filename}.")
        self._notify_download(
            "Download finished",
            f"{snapshot.filename} was saved to {_shorten_home(Path(snapshot.final_path))}.",
        )

    def on_task_failed(self, snapshot: TaskSnapshot) -> None:
        reason = snapshot.error or "The download did not finish."
        self.toast(f"{snapshot.filename}: {reason}", error=True)
        self._notify_download(
            "Download failed",
            f"{snapshot.filename}: {reason}",
            error=True,
        )

    def _notify_download(self, title: str, message: str,
                         error: bool = False) -> None:
        if not self._settings.get("notifications", True):
            return
        if platform_support.notification_backend() is None:
            return
        platform_support.notify(title, message, error=error)

    # -- folders ------------------------------------------------------------

    def choose_download_folder(self) -> None:
        dialog = Gtk.FileChooserDialog(
            title="Choose download folder",
            transient_for=self,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
        )
        dialog.add_buttons(
            "Cancel", Gtk.ResponseType.CANCEL,
            "Choose", Gtk.ResponseType.OK,
        )
        dialog.set_default_response(Gtk.ResponseType.OK)
        dialog.set_modal(True)
        dialog.set_current_name("")
        try:
            dialog.set_current_folder(str(self._download_dir))
        except GLib.Error:
            pass
        response = dialog.run()
        path = dialog.get_filename()
        dialog.destroy()
        if response != Gtk.ResponseType.OK or not path:
            return
        try:
            chosen = check_download_dir(path)
        except (OSError, ValueError) as exc:
            self._show_error("Cannot use that folder", _error_text(exc))
            return

        self._download_dir = chosen
        self._apply_download_dir_label()
        self.library_store.download_root = chosen
        self.library_view.download_root_changed()
        try:
            self._settings.set_download_dir(chosen)
        except (OSError, ValueError) as exc:
            self.toast(
                "The folder works for this session, but the choice could not be "
                f"saved: {_error_text(exc)}",
                error=True,
            )
        else:
            self.toast(f"Download folder set to {_shorten_home(chosen)}.")
        self._refresh_local()

    def open_download_folder(self) -> None:
        self.open_folder(self._download_dir)

    def open_folder(self, path: Path) -> None:
        path = Path(path)
        if not path.is_dir():
            self.toast("That folder does not exist yet.")
            return
        try:
            uri = GLib.filename_to_uri(str(path), None)
        except (GLib.Error, ValueError):
            uri = ""
        if uri and Gtk.show_uri_on_window(
            self, uri, Gtk.get_current_event_time()
        ):
            return
        try:
            subprocess.Popen(
                ["xdg-open", str(path)],
                shell=False,
                stdin=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True,
            )
        except OSError as exc:
            self.toast(f"Could not open the folder: {_error_text(exc)}", error=True)

    # -- launch review ------------------------------------------------------

    def review_launch(self, local_file: LocalFile) -> None:
        path = Path(local_file.path)
        if not path.is_file():
            self._show_error(
                "This file is no longer available",
                "The selected file was moved or deleted. Refresh the game selection.",
            )
            return
        needs_chmod = not is_shell(path.name) and not os.access(path, os.X_OK)

        dialog = Gtk.Dialog(title="Review downloaded file", transient_for=self, modal=True)
        dialog.set_default_size(580, -1)
        dialog.set_resizable(False)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        action = dialog.add_button(
            "Make executable…" if needs_chmod else "Launch", Gtk.ResponseType.OK
        )
        action.get_style_context().add_class("suggested-action")
        action.set_sensitive(False)
        dialog.set_default_response(Gtk.ResponseType.OK)

        content = dialog.get_content_area()
        content.set_spacing(14)
        content.set_margin_start(22)
        content.set_margin_end(22)
        content.set_margin_top(18)
        content.set_margin_bottom(16)

        warning = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        warning.get_style_context().add_class("warning-box")
        warning_icon = Gtk.Image.new_from_icon_name(
            "dialog-warning-symbolic", Gtk.IconSize.MENU
        )
        warning_icon.set_valign(Gtk.Align.START)
        warning_text = Gtk.Label(
            label=(
                "This file is third-party software downloaded from archive.org. "
                "Running it may modify files, install software, or start other "
                "programs. Only launch it if you have reviewed it and trust its source."
            )
        )
        warning_text.set_xalign(0)
        warning_text.set_line_wrap(True)
        warning_text.set_max_width_chars(68)
        warning_text.get_style_context().add_class("warning-text")
        warning.pack_start(warning_icon, False, False, 0)
        warning.pack_start(warning_text, True, True, 0)
        content.pack_start(warning, False, False, 0)

        details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        def add_field(caption: str, value: str, mono: bool = False,
                      selectable: bool = False) -> None:
            field = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            label = Gtk.Label(label=caption.upper(), xalign=0)
            label.get_style_context().add_class("section-label")
            text = Gtk.Label(label=value, xalign=0)
            text.set_line_wrap(True)
            text.set_selectable(selectable)
            text.set_max_width_chars(70)
            if mono:
                text.get_style_context().add_class("mono")
            else:
                text.get_style_context().add_class("small")
            field.pack_start(label, False, False, 0)
            field.pack_start(text, False, False, 0)
            details.pack_start(field, False, False, 0)

        add_field("File", path.name)
        add_field("Detected type", local_file.kind)
        add_field("Size", human_size(local_file.size))
        add_field("Absolute path", str(path), mono=True, selectable=True)
        content.pack_start(details, False, False, 0)

        if needs_chmod:
            hint = Gtk.Label(
                label=(
                    "This file is not marked executable. After you approve it, you "
                    "will be asked to confirm adding execute permission before it runs."
                )
            )
            hint.set_xalign(0)
            hint.set_line_wrap(True)
            hint.set_max_width_chars(68)
            hint.get_style_context().add_class("dim")
            hint.get_style_context().add_class("small")
            content.pack_start(hint, False, False, 0)

        check = Gtk.CheckButton(label="I have reviewed this file and trust it.")
        check.connect(
            "toggled", lambda button: action.set_sensitive(button.get_active())
        )
        content.pack_start(check, False, False, 0)

        content.show_all()
        response = dialog.run()
        approved = check.get_active()
        dialog.destroy()
        if response != Gtk.ResponseType.OK or not approved:
            return

        if needs_chmod:
            confirm = Gtk.MessageDialog(
                transient_for=self,
                modal=True,
                message_type=Gtk.MessageType.QUESTION,
                buttons=Gtk.ButtonsType.CANCEL,
                text="Add execute permission and launch?",
            )
            confirm.add_button("Make executable", Gtk.ResponseType.OK)
            confirm.format_secondary_text(
                "This sets the user execute bit on:\n"
                f"{path}\n\n"
                "Only continue if you have reviewed the file."
            )
            confirm.set_default_response(Gtk.ResponseType.CANCEL)
            ok_button = confirm.get_widget_for_response(Gtk.ResponseType.OK)
            if ok_button is not None:
                ok_button.get_style_context().add_class("suggested-action")
            confirmed = confirm.run()
            confirm.destroy()
            if confirmed != Gtk.ResponseType.OK:
                return
            try:
                make_executable(path, self._download_dir)
            except (LaunchError, OSError) as exc:
                self._show_error(
                    "Could not change permissions", _error_text(exc)
                )
                return

        try:
            pid, log_path = launch(path, self._download_dir)
        except (LaunchError, OSError) as exc:
            self._show_error("The file was not launched", _error_text(exc))
            return

        self._mark_played_for(path)
        message = f"Started {path.name} (pid {pid})."
        if log_path is not None:
            message += f" Log: {log_path}"
        self._details.show_launch_status(message)
        self.toast(f"Launched {path.name} (pid {pid}).")

    def _mark_played_for(self, path: Path) -> None:
        """Record a play timestamp for the library entry containing *path*."""
        try:
            target = path.resolve()
        except OSError:
            return
        with self.library_lock:
            for entry in self.library_store.entries():
                if not entry.folder:
                    continue
                try:
                    folder = Path(entry.folder).resolve()
                except OSError:
                    continue
                if target == folder or folder in target.parents:
                    self.library_store.mark_played(entry.identifier)
                    try:
                        self.library_store.save()
                    except OSError:
                        pass
                    return

    def _show_error(self, title: str, message: str) -> None:
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.CLOSE,
            text=title,
        )
        dialog.format_secondary_text(message)
        dialog.run()
        dialog.destroy()

    def show_website_sources(self) -> None:
        dialog = WebsiteSourcesDialog(self)
        dialog.run()
        dialog.destroy()

    def show_kryoto_catalog(self) -> None:
        dialog = KryotoCatalogDialog(self)
        dialog.run()
        dialog.destroy()

    def copy_text(self, text: str) -> None:
        """Put a link or checksum on the clipboard without launching anything."""
        if not text:
            return
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(text, -1)
        clipboard.store()
        self.toast("Copied to the clipboard.")

    def open_external_url(self, url: str) -> None:
        if not url or not platform_support.open_url(url):
            self.toast("No browser answered for this link.", error=True)

    def check_catalog_mirror(
        self, mirror: MirrorRecord, done: Callable[[object], None] | None = None
    ) -> None:
        """Probe one catalog link in the background and persist the result."""
        allow_external = not kryoto.host_allowed(mirror.url, kryoto.KRYOTO_HOSTS)

        def work():
            return kryoto.check_link(
                mirror.url, allow_external=allow_external
            )

        def success(result: object) -> None:
            if not isinstance(result, kryoto.LinkCheck):
                if done is not None:
                    done(result)
                return
            mirror.last_checked = time.time()
            mirror.check_status = result.status
            mirror.url_kind = result.kind or mirror.url_kind
            mirror.content_type = result.content_type
            mirror.notes = tuple(result.notes)
            if result.filename and not mirror.filename:
                mirror.filename = result.filename
            if result.size and not mirror.size:
                mirror.size = result.size
            try:
                self.catalog_store.save()
            except OSError as exc:
                self.toast(f"The catalog could not be saved: {exc}", error=True)
            if done is not None:
                done(result)
            else:
                self.toast(
                    f"Link check: {mirror.status_label()}."
                    if mirror.check_status != "verified" else
                    "The server confirmed a file response; the download button is now available."
                )

        def failure(exc: object) -> None:
            self.toast(_error_text(exc), error=True)
            if done is not None:
                done(exc)

        _spawn(work, success, failure)

    def send_links_to_jdownloader(
        self, mirrors: Sequence[MirrorRecord], title: str = ""
    ) -> None:
        """Hand a game's links to a running JDownloader 2 (Click'n'Load).

        The links are sent unchanged and downloaded by that application; this app
        neither downloads from a provider endpoint nor bypasses anything.  The
        call runs off the main loop so a slow or missing listener cannot freeze
        the window.
        """
        urls = [mirror.url for mirror in mirrors if mirror.url]
        if not urls:
            self.toast("This game has no source links to send.", error=True)
            return

        def work() -> object:
            return external_dm.send_to_jdownloader(urls)

        def success(result: object) -> None:
            count = result if isinstance(result, int) else len(urls)
            self.toast(
                f"Sent {count} link{'s' if count != 1 else ''} to JDownloader. "
                "That application does the downloading, not this one."
            )

        def failure(exc: object) -> None:
            self.toast(_error_text(exc), error=True)

        _spawn(work, success, failure)

    def send_links_to_ab_download_manager(
        self, mirrors: Sequence[MirrorRecord], title: str = ""
    ) -> None:
        """Start a game's links in AB Download Manager through its REST API.

        AB Download Manager does the transfer with its own engine (multi-
        connection ranges, retries, resume), saving into this app's download
        folder so the library still finds the game.  The links travel unchanged
        and nothing is sent until the button is pressed.
        """
        urls = [mirror.url for mirror in mirrors if mirror.url]
        if not urls:
            self.toast("This game has no source links to send.", error=True)
            return
        folder = str(self._download_dir)

        def work() -> object:
            return external_dm.send_to_ab_download_manager(
                urls, folder=folder, name=title
            )

        def success(result: object) -> None:
            count = result if isinstance(result, int) else len(urls)
            detail = (
                f"Sent {count} of {len(urls)} links to AB Download Manager."
                if count < len(urls)
                else f"Sent {count} link{'s' if count != 1 else ''} to AB Download Manager."
            )
            self.toast(f"{detail} It saves into {_shorten_home(folder)}.")

        def failure(exc: object) -> None:
            self.toast(_error_text(exc), error=True)

        _spawn(work, success, failure)

    def download_catalog_mirror(
        self,
        mirror: MirrorRecord,
        title: str,
        on_finished: Callable[[], None] | None = None,
    ) -> None:
        """The Download button on a source link.

        A link that has not been checked yet is probed first: only a response
        that actually returned a file reaches the download manager, so a page
        URL is never handed to aria2 on the strength of its shape.  A link that
        checks out is then queued exactly like an already-verified one.

        *on_finished* runs on the main loop once the outcome is known, so a
        dialog can redraw the link's now-known state.
        """
        if kryoto.is_downloadable_link(mirror.check_status, mirror.url_kind):
            self.start_catalog_mirror_download(mirror, title)
            if on_finished is not None:
                GLib.idle_add(on_finished)
            return
        self.toast(f"Checking {mirror.host or 'the link'} before downloading…")

        def done(result: object) -> None:
            GLib.idle_add(
                self._after_catalog_mirror_check, mirror, title, result, on_finished
            )

        self.check_catalog_mirror(mirror, done)

    def _after_catalog_mirror_check(
        self,
        mirror: MirrorRecord,
        title: str,
        _result: object,
        on_finished: Callable[[], None] | None = None,
    ) -> bool:
        """Decide what a just-finished link check allows."""
        if kryoto.is_downloadable_link(mirror.check_status, mirror.url_kind):
            self.start_catalog_mirror_download(mirror, title)
        elif mirror.check_status == "page":
            self.toast(
                "That link is a web page, not a file. Use Open in Browser instead.",
                error=True,
            )
        elif mirror.check_status == "external":
            self.toast(
                "That link is on an external host and could not be checked. "
                "Nothing was queued.",
                error=True,
            )
        else:
            self.toast(f"Nothing was queued: {mirror.status_label()}.", error=True)
        if on_finished is not None:
            GLib.idle_add(on_finished)
        return False

    def start_catalog_mirror_download(
        self, mirror: MirrorRecord, title: str
    ) -> None:
        """Queue a catalog link only after a probe confirmed a file response."""
        if not kryoto.is_downloadable_link(mirror.check_status, mirror.url_kind):
            self.toast(
                "This link has not been verified as a file. Open it in a browser "
                "instead, or check it first.",
                error=True,
            )
            return
        host = (urllib.parse.urlsplit(mirror.url).hostname or "").lower()
        if not host:
            self.toast("That link has no host.", error=True)
            return
        path_name = urllib.parse.unquote(
            urllib.parse.urlsplit(mirror.url).path.rsplit("/", 1)[-1]
        )
        filename = mirror.filename or path_name or f"{title or 'catalog-game'}"
        identifier = re.sub(r"[^A-Za-z0-9._-]+", "-", mirror.game_key or "catalog").strip("-.")
        if not identifier or not identifier[0].isalnum():
            identifier = "catalog-" + identifier.lstrip("-.")

        confirm = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.CANCEL,
            text="Download this catalog file?",
        )
        confirm.add_button("Download", Gtk.ResponseType.OK)
        confirm.format_secondary_text(
            f"{filename}\n\nThe link was checked and returned a file response. "
            f"It is saved to {_shorten_home(self._download_dir)} and never "
            "executed automatically."
        )
        response = confirm.run()
        confirm.destroy()
        if response != Gtk.ResponseType.OK:
            return
        try:
            self.manager.add(
                identifier=identifier[:120],
                filename=filename,
                url=mirror.url,
                download_dir=self._download_dir,
                total_size=mirror.size or None,
                folder_name=safe_filename(title or identifier, identifier),
                allowed_hosts=(host,),
            )
        except (AppError, OSError, ValueError) as exc:
            self.toast(_error_text(exc), error=True)
            return
        self.set_downloads_visible(True)
        self.toast("Added to the download queue.")

    def show_settings(self, shortcuts: bool = False, page: str | None = None) -> None:
        """Open Settings on a specific page.

        ``shortcuts`` is kept for existing callers; ``page`` names a tab
        directly (``"general"``, ``"locations"``, ``"providers"``,
        ``"shortcuts"``).  Unknown names fall back to General.
        """
        target = page or ("shortcuts" if shortcuts else "general")
        dialog = SettingsDialog(self, page=target)
        dialog.run()

    def show_movies(self) -> None:
        """Activate the in-app Movies & TV destination."""
        self._apply_view("media")


    def show_calendar(self) -> None:
        """Open the release calendar: what airs or releases on which day."""
        from . import gtk_calendar

        self._calendar_window = gtk_calendar.show_calendar(self, self.settings)

    def show_recent(self) -> None:
        """Open the shared Recently viewed list."""
        from . import gtk_recent

        self._recent_window = gtk_recent.show_recent(self, self.settings)

    def show_tools(self) -> None:
        """Open the tools window: providers, downloads, backup, profiles and more."""
        from . import gtk_tools

        window = gtk_tools.show_tools(self, self.settings)
        # The Interface page writes the preferences while the window is open,
        # so re-apply them when it closes rather than only on the next start.
        window.connect("destroy", lambda *_: self.apply_interface_prefs())
        self._tools_window = window

    def show_shortcuts(self) -> None:
        """Open the window that rebinds the keys this window obeys."""
        self._shortcuts_window = gtk_shortcuts.show_shortcuts(self, self.settings)

    def show_profiles(self) -> None:
        """Open the profiles window: choose, create, rename or delete one."""
        from . import gtk_profiles

        self._profiles_window = gtk_profiles.show_profiles(
            self, on_switch=self._profile_switched
        )

    def _profile_switched(self, profile: Any) -> None:
        """The choice is read at the next start, so say that instead of pretending."""
        self._set_profile_name(
            profile.name, was=getattr(self, "_session_profile", None)
        )
        self.toast(f"Restart Lumora to switch to {profile.name}.")

    def show_about(self) -> None:
        dialog = Gtk.AboutDialog(transient_for=self, modal=True)
        dialog.set_program_name(APP_NAME)
        dialog.set_version(__version__)
        dialog.set_comments(
            "Browse the public Internet Archive collection "
            "native-linux-games-collection, download files to your own folder, "
            "and review them before anything is launched."
        )
        dialog.set_website("https://archive.org/details/native-linux-games-collection")
        dialog.set_website_label("Collection on archive.org")
        dialog.set_logo_icon_name("applications-games")
        dialog.run()
        dialog.destroy()

    # -- keyboard and shutdown ---------------------------------------------

    def _on_key_press(self, _window: Gtk.Window, event: Gdk.EventKey) -> bool:
        keyval = event.keyval
        if keyval == Gdk.KEY_Escape:
            self._toast.dismiss()
            if self._queue_banner.get_reveal_child():
                self._queue_banner.set_reveal_child(False)
            if self.active_view() == "game":
                self.go_back()
                return True
            if self.active_view() == "archive" and self._search.get_text():
                self._clear_search()
                return True
            return False
        # The editable bindings decide here, so a key changed in the shortcuts
        # window works on the next press instead of on the next start.
        action = gtk_shortcuts.action_for(self._settings, keyval, event.state)
        if action == "refresh":
            self.refresh_active_view()
            return True
        if action == "nav.browse":
            self._apply_view("archive")
            return True
        if action == "nav.library":
            self._apply_view("library")
            return True
        if action == "downloads.toggle":
            self.set_downloads_visible(
                self._main_stack.get_visible_child_name() != "downloads"
            )
            return True
        if action == "palette":
            self.show_palette()
            return True
        if action == "search":
            self.focus_search()
            return True
        if action == "settings":
            self.show_settings()
            return True
        return False

    def prepare_shutdown(self) -> None:
        if self._shutdown_done:
            return
        self._shutdown_done = True
        self._closing = True
        if self._torrent_timer:
            GLib.source_remove(self._torrent_timer)
            self._torrent_timer = 0
        self.torrents.close()
        self._manager.shutdown()

    def _save_window_state(self) -> None:
        try:
            self._settings.set_maximized(self.is_maximized())
            if not self.is_maximized():
                width, height = self.get_size()
                if width > 0 and height > 0:
                    self._settings.set_window_size(width, height)
            self._settings.set_active_view(self.active_view())
        except (OSError, ValueError):
            pass

    def _on_delete_event(self, *_args) -> bool:
        self._save_window_state()
        self.prepare_shutdown()
        return False


class SettingsDialog(Gtk.Dialog):
    """Preferences, locations, and the shortcut reference."""

    def __init__(self, owner: "MainWindow", page: str = "general") -> None:
        super().__init__(title="Settings", transient_for=owner, modal=True)
        self._owner = owner
        self._settings = owner.settings
        self.set_default_size(640, 520)
        self.set_size_request(480, 360)
        self.set_resizable(True)
        self.add_button("Close", Gtk.ResponseType.CLOSE)

        content = self.get_content_area()
        content.set_spacing(0)
        content.set_margin_start(0)
        content.set_margin_end(0)
        content.set_margin_top(0)
        content.set_margin_bottom(0)

        # Force solid backgrounds on all dialog widgets to prevent
        # system-theme black/gray blocks from leaking through.
        from gi.repository import Gdk
        # Use @surface (lighter) instead of @bg (near-black) to eliminate
        # the "black block" effect in the settings dialog.
        css = b'''
            dialog, dialog *, notebook, notebook *, scrolledwindow, scrolledwindow *,
            viewport, viewport *, box, box * {
                background-color: @surface;
                background-image: none;
                border: none;
                box-shadow: none;
            }
            notebook > header {
                background-color: @surface;
            }
            notebook > header tab:checked {
                background-color: @bg;
            }
        '''
        prov = Gtk.CssProvider()
        prov.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 3
        )

        notebook = Gtk.Notebook()
        notebook.set_border_width(0)
        notebook.append_page(self._build_general(), Gtk.Label(label="General"))
        notebook.append_page(self._build_locations(), Gtk.Label(label="Locations"))
        notebook.append_page(self._build_providers(), Gtk.Label(label="Providers"))
        notebook.append_page(self._build_media(), Gtk.Label(label="Media"))
        notebook.append_page(self._build_shortcuts(), Gtk.Label(label="Shortcuts"))
        content.pack_start(notebook, True, True, 0)
        # Force solid backgrounds on all key containers to eliminate black blocks.
        for widget in (content, notebook):
            ctx = widget.get_style_context()
            ctx.add_class("settings-solid-bg")
        self._notebook = notebook
        self._pages = {"general": 0, "locations": 1, "providers": 2, "media": 3, "shortcuts": 4}

        self.connect("response", lambda dialog, _r: dialog.destroy())
        content.show_all()
        # Walk all children and force solid @bg backgrounds to eliminate
        # any system-theme black/gray blocks.
        def _solidify(widget):
            ctx = widget.get_style_context()
            ctx.add_class("settings-solid-bg")
            try:
                widget.override_background_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(0.063, 0.071, 0.086, 1.0))
            except (AttributeError, TypeError):
                pass
            for child in getattr(widget, "get_children", lambda: [])():
                _solidify(child)
        _solidify(self)
        notebook.set_current_page(self._pages.get(page, 0))

    # -- pages --------------------------------------------------------------

    def _page(self) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_start(16)
        box.set_margin_end(16)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        return box

    def _scrolled(self, box: Gtk.Box) -> Gtk.Widget:
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.get_style_context().add_class("settings-page")
        scroller.add(box)
        return scroller

    def _build_general(self) -> Gtk.Widget:
        box = self._page()

        heading = Gtk.Label(label="Downloads", xalign=0)
        heading.get_style_context().add_class("panel-title")
        box.pack_start(heading, False, False, 0)

        folder_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        folder_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        folder_caption = Gtk.Label(label="Download folder", xalign=0)
        folder_caption.get_style_context().add_class("fact-name")
        self._dir_value = Gtk.Label(label=_shorten_home(self._owner.download_dir), xalign=0)
        self._dir_value.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self._dir_value.set_selectable(True)
        self._dir_value.set_tooltip_text(str(self._owner.download_dir))
        self._dir_value.get_style_context().add_class("fact-value")
        folder_text.pack_start(folder_caption, False, False, 0)
        folder_text.pack_start(self._dir_value, False, False, 0)
        folder_row.pack_start(folder_text, True, True, 0)
        change = Gtk.Button(label="Browse…")
        change.set_tooltip_text("Choose where downloaded game folders are stored")
        change.connect("clicked", self._on_change_folder)
        folder_row.pack_end(change, False, False, 0)
        box.pack_start(folder_row, False, False, 0)

        self._notifications = Gtk.Switch()
        self._notifications.set_active(bool(self._settings.get("notifications", True)))
        self._notifications.set_valign(Gtk.Align.CENTER)
        self._notifications.connect("notify::active", self._on_notifications)
        box.pack_start(self._switch_row(
            "Desktop notifications",
            "Show a system notification when a download finishes or fails.",
            self._notifications,
        ), False, False, 0)

        self._rescan = Gtk.Switch()
        self._rescan.set_active(bool(self._settings.get("rescan_on_startup", True)))
        self._rescan.set_valign(Gtk.Align.CENTER)
        self._rescan.connect("notify::active", self._on_rescan)
        box.pack_start(self._switch_row(
            "Rescan library on startup",
            "Check the download folder for new game folders when the app opens.",
            self._rescan,
        ), False, False, 0)

        retries = Gtk.SpinButton.new_with_range(
            MIN_DOWNLOAD_RETRIES, MAX_DOWNLOAD_RETRIES, 1
        )
        retries.set_value(self._settings.download_retries)
        retries.set_valign(Gtk.Align.CENTER)
        retries.set_tooltip_text("How often a failed download may be retried")
        retries.connect("value-changed", self._on_retries)
        box.pack_start(self._switch_row(
            "Retry a failed download",
            "Times a failed or cancelled download can be put back in the queue.",
            retries,
        ), False, False, 0)

        timeout = Gtk.SpinButton.new_with_range(
            MIN_NETWORK_TIMEOUT, MAX_NETWORK_TIMEOUT, 5
        )
        timeout.set_value(self._settings.network_timeout)
        timeout.set_valign(Gtk.Align.CENTER)
        timeout.set_tooltip_text("Seconds to wait for a server before giving up")
        timeout.connect("value-changed", self._on_timeout)
        box.pack_start(self._switch_row(
            "Network timeout",
            "Seconds to wait for a server before giving up.",
            timeout,
        ), False, False, 0)

        self._torrent_client = Gtk.Entry()
        self._torrent_client.set_text(self._settings.torrent_client)
        self._torrent_client.set_placeholder_text("Built-in engine")
        self._torrent_client.set_width_chars(24)
        self._torrent_client.set_hexpand(True)
        self._torrent_client.set_valign(Gtk.Align.CENTER)
        self._torrent_client.set_tooltip_text(
            "Optional path to an aria2c executable. Leave empty to use the "
            "built-in torrent engine."
        )
        self._torrent_client.connect("activate", self._on_torrent_client)
        self._torrent_client.connect("focus-out-event", self._on_torrent_client)
        box.pack_start(self._switch_row(
            "aria2c path",
            "Optional aria2c executable. Leave empty to use the built-in "
            "torrent engine.",
            self._torrent_client,
        ), False, False, 0)

        appearance = Gtk.Label(label="Appearance", xalign=0)
        appearance.set_margin_top(8)
        appearance.get_style_context().add_class("panel-title")
        box.pack_start(appearance, False, False, 0)

        group: Gtk.RadioButton | None = None
        for value, label, detail in (
            ("system", "System",
             "Follow the desktop's light or dark preference."),
            ("light", "Light", "Use a light palette for this window."),
            ("dark", "Dark", "Use a dark palette for this window."),
        ):
            radio = Gtk.RadioButton(label="")
            if group is None:
                group = radio
            else:
                radio.join_group(group)
            radio.set_active(self._settings.theme == value)
            radio.connect("toggled", self._on_theme, value)
            box.pack_start(self._switch_row(label, detail, radio), False, False, 0)

        self._missing_switch = Gtk.Switch()
        self._missing_switch.set_active(bool(self._settings.library_show_missing))
        self._missing_switch.set_valign(Gtk.Align.CENTER)
        self._missing_switch.connect("notify::active", self._on_show_missing)
        box.pack_start(self._switch_row(
            "Show missing library entries",
            "Keep rows for game folders that are no longer on disk.",
            self._missing_switch,
        ), False, False, 0)

        note = Gtk.Label(
            label=(
                "Nothing is launched, downloaded, or deleted without your "
                "confirmation. The library index only records what is already on disk."
            )
        )
        note.set_xalign(0)
        note.set_line_wrap(True)
        note.set_max_width_chars(72)
        note.get_style_context().add_class("dim")
        note.get_style_context().add_class("small")
        box.pack_start(note, False, False, 0)

        return self._scrolled(box)

    def _switch_row(self, title: str, description: str,
                    widget: Gtk.Widget) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        label = Gtk.Label(label=title, xalign=0)
        label.get_style_context().add_class("fact-name")
        detail = Gtk.Label(label=description, xalign=0)
        detail.set_line_wrap(True)
        detail.set_max_width_chars(52)
        detail.get_style_context().add_class("dim")
        detail.get_style_context().add_class("small")
        text.pack_start(label, False, False, 0)
        text.pack_start(detail, False, False, 0)
        row.pack_start(text, True, True, 0)
        widget.set_valign(Gtk.Align.CENTER)
        row.pack_end(widget, False, False, 0)
        return row

    def _build_locations(self) -> Gtk.Widget:
        box = self._page()
        heading = Gtk.Label(label="Where things are kept", xalign=0)
        heading.get_style_context().add_class("panel-title")
        box.pack_start(heading, False, False, 0)

        for caption, value in (
            ("Settings file", str(self._settings.path)),
            ("Data folder", str(platform_support.data_dir())),
            ("Download folder", str(self._owner.download_dir)),
        ):
            field = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            label = Gtk.Label(label=caption, xalign=0)
            label.get_style_context().add_class("fact-name")
            path = Gtk.Label(label=value, xalign=0)
            path.set_selectable(True)
            path.set_line_wrap(True)
            path.set_max_width_chars(72)
            path.get_style_context().add_class("mono")
            field.pack_start(label, False, False, 0)
            field.pack_start(path, False, False, 0)
            box.pack_start(field, False, False, 0)

        note = Gtk.Label(
            label=(
                "These locations are read-only here. They follow your system's "
                "standard folders for application data and settings."
            )
        )
        note.set_xalign(0)
        note.set_line_wrap(True)
        note.set_max_width_chars(72)
        note.get_style_context().add_class("dim")
        note.get_style_context().add_class("small")
        box.pack_start(note, False, False, 0)
        return self._scrolled(box)

    def _build_providers(self) -> Gtk.Widget:
        box = self._page()
        heading = Gtk.Label(label="Metadata providers", xalign=0)
        heading.get_style_context().add_class("panel-title")
        box.pack_start(heading, False, False, 0)

        note = Gtk.Label(
            label=(
                "Keys are stored in the desktop keyring when one is available, "
                "otherwise in this user's settings file with owner-only permissions "
                "on POSIX. They are never placed in the catalog, source code, logs, or "
                "error messages. A key is reported as working only after a live "
                "connection test succeeds."
            )
        )
        note.set_xalign(0)
        note.set_line_wrap(True)
        note.set_max_width_chars(72)
        note.get_style_context().add_class("dim")
        note.get_style_context().add_class("small")
        box.pack_start(note, False, False, 0)

        # RAWG
        provider = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        title = Gtk.Label(label="RAWG — metadata only", xalign=0)
        title.get_style_context().add_class("fact-name")
        detail = Gtk.Label(
            label=(
                "RAWG supplies titles, release dates, genres, platforms, developers, "
                "publishers, descriptions, ratings, and cover URLs. It does not "
                "provide game files; store links are metadata, not downloads. "
                "Requires a free API key and RAWG attribution."
            )
        )
        detail.set_xalign(0)
        detail.set_line_wrap(True)
        detail.set_max_width_chars(72)
        detail.get_style_context().add_class("dim")
        detail.get_style_context().add_class("small")
        provider.pack_start(title, False, False, 0)
        provider.pack_start(detail, False, False, 0)

        self._rawg_key = Gtk.Entry()
        self._rawg_key.set_visibility(False)
        self._rawg_key.set_placeholder_text(
            "RAWG API key (blank = use the saved key)"
        )
        self._rawg_key.set_hexpand(True)
        provider.pack_start(self._rawg_key, False, False, 0)

        buttons = Gtk.FlowBox()
        buttons.set_selection_mode(Gtk.SelectionMode.NONE)
        buttons.set_homogeneous(False)
        buttons.set_column_spacing(8)
        buttons.set_row_spacing(8)
        self._rawg_test = Gtk.Button(label="Save & test")
        self._rawg_test.set_tooltip_text(
            "Verify the key with the documented RAWG API before saving it"
        )
        self._rawg_test.connect("clicked", self._on_test_rawg)
        clear = Gtk.Button(label="Clear")
        clear.connect("clicked", self._on_clear_rawg)
        buttons.add(self._rawg_test)
        buttons.add(clear)
        provider.pack_start(buttons, False, False, 0)

        self._rawg_status = Gtk.Label(
            label=(
                "A key is saved in the "
                + ("desktop keyring"
                   if self._settings.keyring_backend == "os-keyring"
                   else "owner-only settings file")
                + ". It has not been tested in this session."
                if self._settings.has_api_key("rawg") else
                "No RAWG key is configured."
            ),
            xalign=0,
        )
        self._rawg_status.set_line_wrap(True)
        self._rawg_status.set_max_width_chars(72)
        self._rawg_status.get_style_context().add_class("small")
        self._rawg_status.get_style_context().add_class("dim")
        provider.pack_start(self._rawg_status, False, False, 0)
        box.pack_start(provider, False, False, 0)

        # TMDB
        tmdb_provider = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        title = Gtk.Label(label="TMDB — movies & TV metadata", xalign=0)
        title.get_style_context().add_class("fact-name")
        detail = Gtk.Label(
            label=(
                "TMDB supplies movie and TV show titles, release dates, genres, "
                "descriptions, ratings, posters, and backdrops. It does not provide "
                "video files; playback is handled separately. Requires a free API key "
                "and TMDB attribution."
            )
        )
        detail.set_xalign(0)
        detail.set_line_wrap(True)
        detail.set_max_width_chars(72)
        detail.get_style_context().add_class("dim")
        detail.get_style_context().add_class("small")
        tmdb_provider.pack_start(title, False, False, 0)
        tmdb_provider.pack_start(detail, False, False, 0)

        self._tmdb_key = Gtk.Entry()
        self._tmdb_key.set_visibility(False)
        self._tmdb_key.set_placeholder_text(
            "TMDB API key (blank = use the saved key)"
        )
        self._tmdb_key.set_hexpand(True)
        tmdb_provider.pack_start(self._tmdb_key, False, False, 0)

        buttons = Gtk.FlowBox()
        buttons.set_selection_mode(Gtk.SelectionMode.NONE)
        buttons.set_homogeneous(False)
        buttons.set_column_spacing(8)
        buttons.set_row_spacing(8)
        self._tmdb_test = Gtk.Button(label="Save & test")
        self._tmdb_test.set_tooltip_text(
            "Verify the key with the documented TMDB API before saving it"
        )
        self._tmdb_test.connect("clicked", self._on_test_tmdb)
        clear = Gtk.Button(label="Clear")
        clear.connect("clicked", self._on_clear_tmdb)
        buttons.add(self._tmdb_test)
        buttons.add(clear)
        tmdb_provider.pack_start(buttons, False, False, 0)

        self._tmdb_status = Gtk.Label(
            label=(
                "A key is saved in the "
                + ("desktop keyring"
                   if self._settings.keyring_backend == "os-keyring"
                   else "owner-only settings file")
                + ". It has not been tested in this session."
                if self._settings.has_api_key("tmdb") else
                "No TMDB key is configured. Movies & TV will not work without it."
            ),
            xalign=0,
        )
        self._tmdb_status.set_line_wrap(True)
        self._tmdb_status.set_max_width_chars(72)
        self._tmdb_status.get_style_context().add_class("small")
        self._tmdb_status.get_style_context().add_class("dim")
        tmdb_provider.pack_start(self._tmdb_status, False, False, 0)
        box.pack_start(tmdb_provider, False, False, 0)

        # MAL
        mal_provider = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        title = Gtk.Label(label="MyAnimeList — anime metadata", xalign=0)
        title.get_style_context().add_class("fact-name")
        detail = Gtk.Label(
            label=(
                "MyAnimeList supplies anime titles, synopsis, episode counts, airing "
                "status, genres, scores, release dates, and cover artwork. Only the "
                "public read API is used, authenticated with your Client ID "
                "(X-MAL-CLIENT-ID); no account password or user login is requested. "
                "Metadata only: it does not provide video files."
            )
        )
        detail.set_xalign(0)
        detail.set_line_wrap(True)
        detail.set_max_width_chars(72)
        detail.get_style_context().add_class("dim")
        detail.get_style_context().add_class("small")
        mal_provider.pack_start(title, False, False, 0)
        mal_provider.pack_start(detail, False, False, 0)

        self._mal_key = Gtk.Entry()
        self._mal_key.set_visibility(False)
        self._mal_key.set_placeholder_text(
            "MAL Client ID (blank = use the saved id)"
        )
        self._mal_key.set_hexpand(True)
        mal_provider.pack_start(self._mal_key, False, False, 0)

        buttons = Gtk.FlowBox()
        buttons.set_selection_mode(Gtk.SelectionMode.NONE)
        buttons.set_homogeneous(False)
        buttons.set_column_spacing(8)
        buttons.set_row_spacing(8)
        self._mal_test = Gtk.Button(label="Save & test")
        self._mal_test.set_tooltip_text(
            "Verify the Client ID with the documented MAL API before saving it"
        )
        self._mal_test.connect("clicked", self._on_test_mal)
        clear = Gtk.Button(label="Clear")
        clear.connect("clicked", self._on_clear_mal)
        buttons.add(self._mal_test)
        buttons.add(clear)
        mal_provider.pack_start(buttons, False, False, 0)

        self._mal_status = Gtk.Label(
            label=(
                "A Client ID is saved in the "
                + ("desktop keyring"
                   if self._settings.keyring_backend == "os-keyring"
                   else "owner-only settings file")
                + ". It has not been tested in this session."
                if self._settings.has_api_key("mal") else
                "No MyAnimeList Client ID is configured. Anime will be empty."
            ),
            xalign=0,
        )
        self._mal_status.set_line_wrap(True)
        self._mal_status.set_max_width_chars(72)
        self._mal_status.get_style_context().add_class("small")
        self._mal_status.get_style_context().add_class("dim")
        mal_provider.pack_start(self._mal_status, False, False, 0)
        box.pack_start(mal_provider, False, False, 0)

        return self._scrolled(box)

    def _build_media(self) -> Gtk.Widget:
        box = self._page()

        heading = Gtk.Label(label="Media & playback", xalign=0)
        heading.get_style_context().add_class("panel-title")
        box.pack_start(heading, False, False, 0)

        providers_heading = Gtk.Label(label="Providers", xalign=0)
        providers_heading.set_margin_top(4)
        providers_heading.get_style_context().add_class("fact-name")
        box.pack_start(providers_heading, False, False, 0)

        self._media_provider = Gtk.ComboBoxText()
        from . import streaming
        for key in streaming.PROVIDERS:
            self._media_provider.append(key, streaming.LABELS.get(key, key))
        self._media_provider.set_active_id(
            streaming.clean_provider(self._settings.get("streaming_provider", streaming.DEFAULT_PROVIDER))
        )
        self._media_provider.connect("changed", self._on_media_provider_changed)
        self._media_provider.set_size_request(180, -1)
        box.pack_start(self._switch_row(
            "Streaming provider",
            "Provider used for movies and TV playback.",
            self._media_provider,
        ), False, False, 0)

        self._media_anime_provider = Gtk.ComboBoxText()
        for key in streaming.ANIME_PROVIDERS:
            self._media_anime_provider.append(key, streaming.LABELS.get(key, key))
        self._media_anime_provider.set_active_id(
            streaming.clean_anime_provider(self._settings.get("anime_streaming_provider",
                self._settings.get("streaming_provider", streaming.DEFAULT_ANIME_PROVIDER)))
        )
        self._media_anime_provider.connect("changed", self._on_media_anime_provider_changed)
        self._media_anime_provider.set_size_request(180, -1)
        box.pack_start(self._switch_row(
            "Anime provider",
            "Provider used for anime playback. ani.pm plays from an AniList "
            "or MyAnimeList id and needs no key (https://ani.pm/developers).",
            self._media_anime_provider,
        ), False, False, 0)

        playback_heading = Gtk.Label(label="Playback", xalign=0)
        playback_heading.set_margin_top(8)
        playback_heading.get_style_context().add_class("fact-name")
        box.pack_start(playback_heading, False, False, 0)

        self._media_audio = Gtk.ComboBoxText()
        for key in ("sub", "dub", "vostfr"):
            self._media_audio.append(key, streaming.LANGUAGE_LABELS.get(key, key))
        current = streaming.clean_language(
            "ani",
            self._settings.get("anime_language", "")
            or ("dub" if self._settings.get("anime_dub", False) else "sub"),
        )
        self._media_audio.set_active_id(current)
        self._media_audio.connect("changed", self._on_media_audio_changed)
        self._media_audio.set_size_request(180, -1)
        box.pack_start(self._switch_row(
            "Default audio",
            "Sub, dub or VOSTFR where the anime provider offers it.",
            self._media_audio,
        ), False, False, 0)

        self._media_autoplay = Gtk.Switch()
        self._media_autoplay.set_active(bool(self._settings.get("media_autoplay", True)))
        self._media_autoplay.connect("notify::active", self._on_media_autoplay_changed)
        box.pack_start(self._switch_row(
            "Autoplay",
            "Start playing as soon as an episode or movie opens.",
            self._media_autoplay,
        ), False, False, 0)

        return self._scrolled(box)

    def _build_shortcuts(self) -> Gtk.Widget:
        box = self._page()
        heading = Gtk.Label(label="Keyboard shortcuts", xalign=0)
        heading.get_style_context().add_class("panel-title")
        box.pack_start(heading, False, False, 0)

        grid = Gtk.Grid(column_spacing=16, row_spacing=8)
        grid.set_halign(Gtk.Align.START)
        shortcuts = (
            ("Ctrl+1", "Show the Archive view"),
            ("Ctrl+2", "Show the Library view"),
            ("Ctrl+F", "Focus the search field"),
            ("Ctrl+D", "Show or hide the download queue"),
            ("Ctrl+,", "Open Settings"),
            ("F5", "Reload the collection or rescan the library"),
            ("Escape", "Dismiss a notice or clear the search"),
        )
        for index, (keys, description) in enumerate(shortcuts):
            key_label = Gtk.Label(label=keys, xalign=0)
            key_label.get_style_context().add_class("shortcut-keycap")
            text = Gtk.Label(label=description, xalign=0)
            text.set_line_wrap(True)
            text.set_max_width_chars(48)
            text.get_style_context().add_class("small")
            grid.attach(key_label, 0, index, 1, 1)
            grid.attach(text, 1, index, 1, 1)
        box.pack_start(grid, False, False, 0)
        return self._scrolled(box)


    # -- handlers -----------------------------------------------------------

    def _on_change_folder(self, _button: Gtk.Button) -> None:
        self._owner.choose_download_folder()
        self._dir_value.set_text(_shorten_home(self._owner.download_dir))
        self._dir_value.set_tooltip_text(str(self._owner.download_dir))

    def _on_notifications(self, switch: Gtk.Switch, _param) -> None:
        self._settings.set("notifications", switch.get_active())

    def _on_rescan(self, switch: Gtk.Switch, _param) -> None:
        self._settings.set("rescan_on_startup", switch.get_active())

    def _on_theme(self, button: Gtk.RadioButton, value: str) -> None:
        if not button.get_active():
            return
        try:
            self._settings.set_theme(value)
        except (OSError, ValueError):
            pass
        self._owner.apply_theme(value)

    def _on_retries(self, spin: Gtk.SpinButton) -> None:
        try:
            self._settings.set_download_retries(spin.get_value_as_int())
            self._owner.manager.set_retry_limit(spin.get_value_as_int())
        except (OSError, ValueError):
            pass

    def _on_timeout(self, spin: Gtk.SpinButton) -> None:
        try:
            self._settings.set_network_timeout(spin.get_value_as_int())
        except (OSError, ValueError):
            pass

    def _on_torrent_client(self, widget: Gtk.Entry, _event=None) -> bool:
        try:
            self._settings.set_torrent_client(widget.get_text())
        except ValueError as exc:
            widget.set_tooltip_text(str(exc))
            widget.get_style_context().add_class("error-text")
            return False
        widget.get_style_context().remove_class("error-text")
        widget.set_tooltip_text(
            "Optional path to an aria2c executable. Leave empty to use the "
            "built-in torrent engine."
        )
        self._owner.torrent_engine_changed(self._settings.torrent_client)
        return False

    def _on_show_missing(self, switch: Gtk.Switch, _param) -> None:
        value = bool(switch.get_active())
        try:
            self._settings.set_library_show_missing(value)
        except (OSError, ValueError):
            pass
        view = getattr(self._owner, "library_view", None)
        if view is not None and view._show_missing.get_active() != value:
            view._show_missing.set_active(value)


    def _on_test_rawg(self, _button) -> None:
        typed = self._rawg_key.get_text().strip()
        key = typed or self._settings.api_key("rawg")
        if not key:
            self._rawg_status.set_text("Enter a RAWG API key first.")
            return
        self._rawg_test.set_sensitive(False)
        self._rawg_status.set_text("Testing the RAWG API…")

        def success(result: object) -> None:
            self._rawg_test.set_sensitive(True)
            if typed:
                try:
                    self._settings.set_api_key("rawg", typed)
                except (OSError, ValueError) as exc:
                    self._rawg_status.set_text(
                        f"The key worked but could not be saved: {exc}"
                    )
                    return
                self._rawg_key.set_text("")
            self._rawg_status.set_text(str(result))

        def failure(exc: object) -> None:
            self._rawg_test.set_sensitive(True)
            self._rawg_status.set_text(f"RAWG test failed: {_error_text(exc)}")

        _spawn(lambda: RawgSource(key).test_connection(), success, failure)


    def _on_clear_rawg(self, _button) -> None:
        try:
            self._settings.clear_api_key("rawg")
        except (OSError, ValueError):
            pass
        self._rawg_key.set_text("")
        self._rawg_status.set_text("The saved RAWG key was removed.")


    def _on_test_tmdb(self, _button) -> None:
        from .tmdb import TmdbClient, TmdbError

        typed = self._tmdb_key.get_text().strip()
        key = typed or self._settings.api_key("tmdb")
        if not key:
            self._tmdb_status.set_text("Enter a TMDB API key first.")
            return
        self._tmdb_test.set_sensitive(False)
        self._tmdb_status.set_text("Testing the TMDB API…")

        def success(result: object) -> None:
            self._tmdb_test.set_sensitive(True)
            if typed:
                try:
                    self._settings.set_api_key("tmdb", typed)
                except (OSError, ValueError) as exc:
                    self._tmdb_status.set_text(
                        f"The key worked but could not be saved: {exc}"
                    )
                    return
                self._tmdb_key.set_text("")
            self._tmdb_status.set_text(str(result))

        def failure(exc: object) -> None:
            self._tmdb_test.set_sensitive(True)
            self._tmdb_status.set_text(f"TMDB test failed: {_error_text(exc)}")

        _spawn(lambda: TmdbClient(key).test_connection(), success, failure)


    def _on_clear_tmdb(self, _button) -> None:
        try:
            self._settings.clear_api_key("tmdb")
        except (OSError, ValueError):
            pass
        self._tmdb_key.set_text("")
        self._tmdb_status.set_text("The saved TMDB key was removed.")


    def _on_test_mal(self, _button) -> None:
        from .mal import MalClient

        typed = self._mal_key.get_text().strip()
        key = typed or self._settings.api_key("mal")
        if not key:
            self._mal_status.set_text("Enter a MyAnimeList Client ID first.")
            return
        self._mal_test.set_sensitive(False)
        self._mal_status.set_text("Testing the MyAnimeList API…")

        def success(result: object) -> None:
            self._mal_test.set_sensitive(True)
            if typed:
                try:
                    self._settings.set_api_key("mal", typed)
                except (OSError, ValueError) as exc:
                    self._mal_status.set_text(
                        f"The Client ID worked but could not be saved: {exc}"
                    )
                    return
                self._mal_key.set_text("")
            self._mal_status.set_text(str(result))

        def failure(exc: object) -> None:
            self._mal_test.set_sensitive(True)
            self._mal_status.set_text(
                f"MyAnimeList test failed: {_error_text(exc)}"
            )

        _spawn(lambda: MalClient(key).test_connection(), success, failure)


    def _on_clear_mal(self, _button) -> None:
        try:
            self._settings.clear_api_key("mal")
        except (OSError, ValueError):
            pass
        self._mal_key.set_text("")
        self._mal_status.set_text("The saved MyAnimeList Client ID was removed.")


    def _on_media_provider_changed(self, combo) -> None:
        from . import streaming
        self._settings.set("streaming_provider", combo.get_active_id() or streaming.DEFAULT_PROVIDER)


    def _on_media_anime_provider_changed(self, combo) -> None:
        from . import streaming
        self._settings.set(
            "anime_streaming_provider",
            streaming.clean_anime_provider(combo.get_active_id() or streaming.DEFAULT_ANIME_PROVIDER),
        )


    def _on_media_audio_changed(self, combo) -> None:
        from . import streaming
        choice = streaming.clean_language(
            "ani", combo.get_active_id() or streaming.DEFAULT_LANGUAGE
        )
        self._settings.set("anime_language", choice)
        self._settings.set("anime_dub", choice == "dub")


    def _on_media_autoplay_changed(self, switch, _param) -> None:
        self._settings.set("media_autoplay", bool(switch.get_active()))


# -- application -------------------------------------------------------------


_CAPABILITY_ROWS = (
    "Catalog discovery",
    "Pagination",
    "Game details",
    "Artwork",
    "Magnet discovery",
    "Mirror discovery",
    "Authentication",
    "Download links",
)


def _provider_value(value: object) -> str:
    """Render one source-supplied field as plain text, never as a link."""
    if isinstance(value, bool):
        return "yes" if value else "no"
    text = str(value)
    return text if len(text) <= 80 else text[:77] + "…"


def _preview_sample(source, limit: int = 5) -> str:
    """Read a few catalog records so a large import can be judged first.

    This is one bounded request.  A failure is reported as text, never as a
    fabricated record, and it never blocks the import button.
    """
    try:
        page = source.page(None, limit)
    except Exception as exc:  # noqa: BLE001 - shown to the user
        return f"No preview records could be read: {_error_text(exc)}"
    titles = [game.title for game in page.games[:limit] if game.title]
    if not titles:
        return "The source returned no preview records for its first page."
    total = f" of {page.total}" if page.total is not None else ""
    return f"First{total} record(s): " + " · ".join(titles)


class ArtworkMatchDialog(Gtk.Dialog):
    """Choose which provider result a record's title actually refers to."""

    def __init__(self, owner: "MainWindow", title: str,
                 candidates: Sequence[ArtworkCandidate]) -> None:
        super().__init__(title="Match on RAWG", transient_for=owner, modal=True)
        self.set_default_size(520, 360)
        self._candidates = list(candidates)
        self._choose = self.add_button("Use selected", Gtk.ResponseType.OK)
        self._choose.get_style_context().add_class("suggested-action")
        self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        self.selected: ArtworkCandidate | None = None

        content = self.get_content_area()
        content.set_spacing(8)
        content.set_margin_start(16)
        content.set_margin_end(16)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        intro = Gtk.Label(
            label=(
                f"No result matched “{title}” exactly. "
                "Choose the game this record describes:"
            ),
            xalign=0,
        )
        intro.set_line_wrap(True)
        intro.get_style_context().add_class("small")
        content.pack_start(intro, False, False, 0)

        self._list = Gtk.ListBox()
        self._list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        for candidate in self._candidates:
            row = Gtk.ListBoxRow()
            label = Gtk.Label(label=candidate.describe(), xalign=0)
            label.set_line_wrap(True)
            label.set_margin_start(8)
            label.set_margin_end(8)
            label.set_margin_top(6)
            label.set_margin_bottom(6)
            row.add(label)
            self._list.add(row)
        self._list.connect("row-selected", self._on_row_selected)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.add(self._list)
        content.pack_start(scroller, True, True, 0)
        self._choose.set_sensitive(False)
        self.show_all()

    def _on_row_selected(self, _list: Gtk.ListBox,
                         row: Gtk.ListBoxRow | None) -> None:
        index = row.get_index() if row is not None else -1
        self.selected = (
            self._candidates[index]
            if 0 <= index < len(self._candidates) else None
        )
        self._choose.set_sensitive(self.selected is not None)


class WebsiteSourcesDialog(Gtk.Dialog):
    """Add a website source, inspect what it really offers, and import it.

    The dialog is deliberately explicit about capabilities: a row says
    "verified" only when a live response confirmed it, and "unsupported" with a
    reason when the adapter has no such operation.  Import runs in a worker
    thread and checkpoints every page, so the UI never blocks and an interrupted
    run can be resumed.
    """

    def __init__(self, owner: "MainWindow") -> None:
        super().__init__(title="Website Sources", transient_for=owner, modal=True)
        self._owner = owner
        self._store: CatalogStore = owner.catalog_store
        self._cancel = threading.Event()
        self._running = False
        self._fetch_available = False
        self._source = None
        self._report: CapabilityReport | None = None
        self.set_default_size(820, 620)
        self.set_resizable(True)
        self.add_button("Close", Gtk.ResponseType.CLOSE)

        content = self.get_content_area()
        content.set_spacing(0)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        box.set_margin_start(20)
        box.set_margin_end(20)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        scroller.add(box)
        content.pack_start(scroller, True, True, 0)

        heading = Gtk.Label(label="Add a website", xalign=0)
        heading.get_style_context().add_class("panel-title")
        box.pack_start(heading, False, False, 0)

        hint = Gtk.Label(
            label=(
                "A source is usable only when this build contains an adapter for it. "
                "Entering an arbitrary URL cannot invent an interface; the preview "
                "below reports exactly what was verified."
            )
        )
        hint.set_xalign(0)
        hint.set_line_wrap(True)
        hint.set_max_width_chars(92)
        hint.get_style_context().add_class("dim")
        hint.get_style_context().add_class("small")
        box.pack_start(hint, False, False, 0)

        url_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._url = Gtk.Entry()
        self._url.set_placeholder_text("https://archive.org/details/<collection>")
        self._url.set_text("https://archive.org/details/native-linux-games-collection")
        self._url.set_hexpand(True)
        self._url.connect("activate", lambda *_: self._on_inspect(None))
        self._inspect_btn = Gtk.Button(label="Inspect")
        self._inspect_btn.set_tooltip_text(
            "Check the source's public interfaces and report what actually works"
        )
        self._inspect_btn.connect("clicked", self._on_inspect)
        url_row.pack_start(self._url, True, True, 0)
        url_row.pack_start(self._inspect_btn, False, False, 0)
        box.pack_start(url_row, False, False, 0)

        self._structured_toggle = Gtk.CheckButton(
            label="Try structured HTML (JSON-LD / Open Graph) on this page"
        )
        self._structured_toggle.set_active(False)
        self._structured_toggle.set_tooltip_text(
            "Only explicit structured metadata on the entered page is read; no "
            "links are crawled. The page must permit automated access."
        )
        box.pack_start(self._structured_toggle, False, False, 0)

        # Configurable adapter: any public JSON endpoint whose shape the generic
        # reader cannot guess.  Metadata only; the mapping cannot name a file.
        self._mapping_expander = Gtk.Expander(label="Custom JSON mapping (advanced)")
        self._mapping_expander.set_tooltip_text(
            "For a public JSON endpoint whose record path, field names or "
            "pagination need to be stated explicitly."
        )
        mapping_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._mapping_entry = Gtk.Entry()
        self._mapping_entry.set_placeholder_text(
            '{"records": "games", "fields": {"id": "productId", '
            '"source_url": "pageUrl"}, "pagination": {"type": "page"}}'
        )
        mapping_hint = Gtk.Label(
            label=(
                "Keys: records (path to the array), fields (canonical field → "
                "source key), pagination {type: none|page|offset|cursor, param, "
                "size_param, start, total_path, next_path}. Saved with the source "
                "and reused on the next run. Metadata only: a record's download "
                "links are never derived from this mapping."
            )
        )
        mapping_hint.set_xalign(0)
        mapping_hint.set_line_wrap(True)
        mapping_hint.set_max_width_chars(92)
        mapping_hint.get_style_context().add_class("dim")
        mapping_hint.get_style_context().add_class("small")
        mapping_box.pack_start(self._mapping_entry, False, False, 0)
        mapping_box.pack_start(mapping_hint, False, False, 0)
        self._mapping_expander.add(mapping_box)
        box.pack_start(self._mapping_expander, False, False, 0)

        self._status = Gtk.Label(label="No source inspected yet.", xalign=0)
        self._status.set_line_wrap(True)
        self._status.set_max_width_chars(92)
        self._status.get_style_context().add_class("dim")
        self._status.get_style_context().add_class("small")
        box.pack_start(self._status, False, False, 0)

        preview = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        preview.get_style_context().add_class("start-frame")
        preview.set_margin_top(2)
        self._cap_grid = Gtk.Grid(column_spacing=14, row_spacing=6)
        self._cap_values: dict[str, Gtk.Label] = {}
        for row, label in enumerate(_CAPABILITY_ROWS):
            caption = Gtk.Label(label=label, xalign=0)
            caption.get_style_context().add_class("fact-name")
            state = Gtk.Label(label="unknown", xalign=0)
            state.set_size_request(96, -1)
            state.get_style_context().add_class("mono")
            reason = Gtk.Label(label="Not checked yet.", xalign=0)
            reason.set_line_wrap(True)
            reason.set_max_width_chars(56)
            reason.get_style_context().add_class("dim")
            reason.get_style_context().add_class("small")
            self._cap_grid.attach(caption, 0, row, 1, 1)
            self._cap_grid.attach(state, 1, row, 1, 1)
            self._cap_grid.attach(reason, 2, row, 1, 1)
            self._cap_values[label] = state
            setattr(self, "_reason_" + str(row), reason)
        self._sample = Gtk.Label(label="", xalign=0)
        self._sample.set_line_wrap(True)
        self._sample.set_max_width_chars(92)
        self._sample.set_selectable(True)
        self._sample.get_style_context().add_class("dim")
        self._sample.get_style_context().add_class("small")
        preview.pack_start(self._sample, False, False, 0)
        preview.pack_start(self._cap_grid, False, False, 0)
        self._cap_notes = Gtk.Label(label="", xalign=0)
        self._cap_notes.set_line_wrap(True)
        self._cap_notes.set_max_width_chars(92)
        self._cap_notes.get_style_context().add_class("small")
        preview.pack_start(self._cap_notes, False, False, 0)
        # Sources whose terms ask for attribution get a real link, never"
        # just a bare URL in a label.
        self._attribution_link = Gtk.LinkButton.new_with_label("", "")
        self._attribution_link.set_no_show_all(True)
        self._attribution_link.set_halign(Gtk.Align.START)
        self._attribution_link.get_style_context().add_class("small")
        preview.pack_start(self._attribution_link, False, False, 0)
        box.pack_start(preview, False, False, 0)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._import_btn = Gtk.Button(label="Import all")
        self._import_btn.set_tooltip_text("Import every page this source exposes")
        self._import_btn.set_sensitive(False)
        self._import_btn.connect("clicked", lambda *_: self._on_import(False))
        self._resume_btn = Gtk.Button(label="Resume")
        self._resume_btn.set_tooltip_text("Continue the last interrupted import")
        self._resume_btn.set_sensitive(False)
        self._resume_btn.connect("clicked", lambda *_: self._on_import(True))
        self._cancel_btn = Gtk.Button(label="Cancel import")
        self._cancel_btn.set_sensitive(False)
        self._cancel_btn.connect("clicked", lambda *_: self._cancel.set())
        self._remove_btn = Gtk.Button(label="Remove source")
        self._remove_btn.set_sensitive(False)
        self._remove_btn.connect("clicked", lambda *_: self._on_remove())
        self._file_btn = Gtk.Button(label="Import JSON/CSV…")
        self._file_btn.set_tooltip_text(
            "Import a local catalog file with title, metadata, and optional cover URLs"
        )
        self._file_btn.connect("clicked", self._on_import_file)
        self._fetch_magnets_btn = Gtk.Button(label="Fetch magnet links")
        self._fetch_magnets_btn.set_tooltip_text(
            "Read the .json link each already-imported record carries. "
            "One request per record; nothing is downloaded."
        )
        self._fetch_magnets_btn.set_sensitive(False)
        self._fetch_magnets_btn.set_no_show_all(True)
        self._fetch_magnets_btn.connect("clicked", lambda *_: self._on_fetch_magnets())
        self._enrich_toggle = Gtk.CheckButton(
            label="Also fetch details, artwork, mirrors, and magnets (slow)"
        )
        self._enrich_toggle.set_active(False)
        self._enrich_toggle.set_tooltip_text(
            "The catalog is imported first and in full. This optional second pass "
            "makes one request per game and can be resumed or cancelled separately."
        )
        box.pack_start(self._enrich_toggle, False, False, 0)

        sidecar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._sidecar_toggle = Gtk.CheckButton(
            label="Read each record's .json link for magnet or infohash"
        )
        self._sidecar_toggle.set_active(False)
        self._sidecar_toggle.set_tooltip_text(
            "A local catalog whose records name their own .json file can be read "
            "once per record. Anything found is unverified metadata."
        )
        sidecar_hint = Gtk.Label(
            label=(
                "One request per record. A magnet link is unverified metadata, "
                "not a download promise."
            ),
            xalign=0,
        )
        sidecar_hint.set_line_wrap(True)
        sidecar_hint.set_max_width_chars(92)
        sidecar_hint.get_style_context().add_class("dim")
        sidecar_hint.get_style_context().add_class("small")
        self._sidecar_report = Gtk.Label(label="", xalign=0)
        self._sidecar_report.set_line_wrap(True)
        self._sidecar_report.set_max_width_chars(92)
        self._sidecar_report.set_selectable(True)
        self._sidecar_report.get_style_context().add_class("small")
        self._sidecar_report.set_no_show_all(True)
        sidecar_box.pack_start(self._sidecar_toggle, False, False, 0)
        sidecar_box.pack_start(sidecar_hint, False, False, 0)
        sidecar_box.pack_start(self._sidecar_report, False, False, 0)
        box.pack_start(sidecar_box, False, False, 0)

        limit_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        limit_label = Gtk.Label(label="Record limit (0 = whole catalog)")
        limit_row.pack_start(limit_label, False, False, 0)
        self._limit_entry = Gtk.Entry()
        self._limit_entry.set_text("0")
        self._limit_entry.set_width_chars(8)
        self._limit_entry.set_tooltip_text(
            "Stop the import after this many games. Large catalogs such as RAWG "
            "report hundreds of thousands of records, so a limit is a safe first run. "
            "Values above 32768 are clamped to 32768."
        )
        limit_row.pack_start(self._limit_entry, False, False, 0)
        box.pack_start(limit_row, False, False, 0)
        actions.pack_start(self._import_btn, False, False, 0)
        actions.pack_start(self._resume_btn, False, False, 0)
        actions.pack_start(self._cancel_btn, False, False, 0)
        actions.pack_start(self._file_btn, False, False, 0)
        actions.pack_start(self._fetch_magnets_btn, False, False, 0)
        actions.pack_end(self._remove_btn, False, False, 0)
        box.pack_start(actions, False, False, 0)

        self._progress = Gtk.ProgressBar()
        self._progress.set_show_text(False)
        box.pack_start(self._progress, False, False, 0)
        self._counters = Gtk.Label(label="", xalign=0)
        self._counters.set_line_wrap(True)
        self._counters.set_max_width_chars(92)
        self._counters.get_style_context().add_class("dim")
        self._counters.get_style_context().add_class("small")
        box.pack_start(self._counters, False, False, 0)

        self._diagnostics = Gtk.Label(label="", xalign=0)
        self._diagnostics.set_line_wrap(True)
        self._diagnostics.set_max_width_chars(92)
        self._diagnostics.set_selectable(True)
        self._diagnostics.get_style_context().add_class("dim")
        self._diagnostics.get_style_context().add_class("small")
        box.pack_start(self._diagnostics, False, False, 0)

        self._error = Gtk.Label(label="")
        self._error.set_xalign(0)
        self._error.set_line_wrap(True)
        self._error.set_max_width_chars(92)
        self._error.get_style_context().add_class("error-text")
        self._error.set_no_show_all(True)
        box.pack_start(self._error, False, False, 0)

        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        box.pack_start(separator, False, False, 0)
        configured = Gtk.Label(label="Configured sources", xalign=0)
        configured.get_style_context().add_class("panel-title")
        box.pack_start(configured, False, False, 0)
        self._sources_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.pack_start(self._sources_box, False, False, 0)
        self._totals = Gtk.Label(label="", xalign=0)
        self._totals.set_line_wrap(True)
        self._totals.get_style_context().add_class("dim")
        self._totals.get_style_context().add_class("small")
        box.pack_start(self._totals, False, False, 0)

        self.connect("response", self._on_response)
        self.connect("destroy", lambda *_: self._cancel.set())
        content.show_all()
        self._refresh_sources()
        self._use_configured_source()

    # -- helpers -----------------------------------------------------------

    def _use_configured_source(self) -> None:
        """Select an already configured source so its URL need not be retyped."""
        records = sorted(
            self._store.sources.values(),
            key=lambda record: (record.last_sync, record.added_at),
            reverse=True,
        )
        for record in records:
            try:
                source = self._resolve(
                    record.url,
                    allow_structured=record.adapter == "StructuredHtmlSource",
                )
            except SourceError:
                continue
            self._source = source
            self._url.set_text(record.url)
            if record.config:
                self._mapping_entry.set_text(json.dumps(record.config, sort_keys=True))
                self._mapping_expander.set_expanded(True)
            if record.capabilities:
                report = CapabilityReport.from_dict(record.capabilities)
                self._report = report
                self._show_report(source, report, live=False)
                self._status.set_text(
                    f"Using configured source {record.name or record.source_id}. "
                    "Import all continues the whole catalog; Inspect refreshes the "
                    "capability report."
                )
            else:
                self._status.set_text(
                    f"Using configured source {record.name or record.source_id}. "
                    "Inspect it once to enable Import all."
                )
            return

    def _saved_config(self, url: str) -> dict:
        """The mapping this URL was last configured with, if any."""
        target = url.strip()
        for record in self._store.sources.values():
            if record.config and record.url.strip() == target:
                return dict(record.config)
        return {}

    def _resolve(self, url: str, allow_structured: bool | None = None):
        """Resolve a URL to an adapter. Raises SourceError with the reason."""
        if allow_structured is None:
            allow_structured = self._structured_toggle.get_active()
        typed = self._mapping_entry.get_text().strip()
        config = normalize_source_config(typed) if typed else self._saved_config(url)
        return source_for_url(
            url,
            api_keys=self._owner.settings.api_keys,
            allow_structured=allow_structured,
            config=config,
        )

    def _options(self) -> ImportOptions:
        try:
            limit = int(self._limit_entry.get_text().strip() or "0")
        except ValueError:
            limit = 0
        return ImportOptions(
            enrich=self._enrich_toggle.get_active(), max_records=max(0, limit)
        )

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._running = busy
        self._inspect_btn.set_sensitive(not busy)
        self._url.set_sensitive(not busy)
        self._import_btn.set_sensitive(
            not busy and self._report is not None
            and self._report.state("catalog") in (Capability.VERIFIED, Capability.PROBABLE)
        )
        self._cancel_btn.set_sensitive(busy)
        self._resume_btn.set_sensitive(not busy and self._has_resumable())
        self._fetch_magnets_btn.set_sensitive(not busy and self._fetch_available)
        if message:
            self._status.set_text(message)

    def _has_resumable(self):
        if self._source is None:
            return False
        job = self._store.latest_job(self._source.id)
        return bool(job and job.resumable)

    def _update_fetch_button(self) -> None:
        """Show the retroactive magnet action only for a chosen local catalog."""
        local = isinstance(self._source, LocalFileSource)
        self._fetch_available = bool(
            local and self._store.get_source(self._source.id) is not None
        )
        if local:
            self._fetch_magnets_btn.set_no_show_all(False)
            self._fetch_magnets_btn.show()
        else:
            self._fetch_magnets_btn.hide()
            self._fetch_magnets_btn.set_no_show_all(True)
        self._fetch_magnets_btn.set_sensitive(self._fetch_available and not self._running)

    def _select_local_record(self, record) -> None:
        """Select an already imported local catalog so its records can be filled in.

        The file is read once to rebuild the adapter; nothing is fetched.
        """
        if self._running:
            return
        try:
            path = _path_from_file_url(record.url)
            source = local_source(path, read_sidecars=False)
            report = source.inspect()
        except (FeedError, OSError, ValueError) as exc:
            self._show_error(str(exc))
            return
        self._source = source
        self._report = report
        self._url.set_text(record.url)
        self._show_report(source, report, live=False)
        # The report describes a fresh import.  The action below can still fill
        # in records that were imported before the sidecar switch existed.
        reason = str(report.reasons.get("magnets") or "")
        head, marker, _tail = reason.partition(", but reading them is off")
        if marker:
            self._cap_values["Magnet discovery"].set_text("probable")
            self._reason_4.set_text(
                head + "; already imported records can be filled in with "
                "Fetch magnet links."
            )
        self._status.set_text(
            f"Using local catalog {source.name}. Select the .json sidecar switch "
            "before importing a new file; already imported records can be filled "
            "in with Fetch magnet links."
        )
        self._update_fetch_button()

    def _refresh_sources(self) -> None:
        for child in list(self._sources_box.get_children()):
            self._sources_box.remove(child)
        records = sorted(self._store.sources.values(), key=lambda r: r.name.casefold())
        if not records:
            empty = Gtk.Label(label="No website sources configured yet.", xalign=0)
            empty.get_style_context().add_class("dim")
            empty.get_style_context().add_class("small")
            self._sources_box.pack_start(empty, False, False, 0)
        for record in records:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            name = Gtk.Label(label=record.name or record.source_id, xalign=0)
            name.get_style_context().add_class("fact-name")
            detail_bits = [record.url, f"{record.imported} imported"]
            if record.last_sync:
                detail_bits.append(
                    "last sync " + time.strftime("%Y-%m-%d %H:%M", time.localtime(record.last_sync))
                )
            if record.last_error:
                detail_bits.append("error: " + record.last_error)
            detail = Gtk.Label(label=" · ".join(detail_bits), xalign=0)
            detail.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            detail.set_max_width_chars(84)
            detail.get_style_context().add_class("dim")
            detail.get_style_context().add_class("small")
            text.pack_start(name, False, False, 0)
            text.pack_start(detail, False, False, 0)
            if record.url.startswith(("http://", "https://")):
                use = Gtk.Button(label="Use")
                use.set_tooltip_text("Inspect this source again")
                use.connect(
                    "clicked",
                    lambda _b, url=record.url: (
                        self._url.set_text(url),
                        self._on_inspect(None),
                    ),
                )
                row.pack_end(use, False, False, 0)
            elif record.url.startswith("file://"):
                use = Gtk.Button(label="Use")
                use.set_tooltip_text("Select this local catalog")
                use.connect(
                    "clicked",
                    lambda _b, rec=record: self._select_local_record(rec),
                )
                row.pack_end(use, False, False, 0)
            row.pack_start(text, True, True, 0)
            self._sources_box.pack_start(row, False, False, 0)
        stats = self._store.stats()
        self._totals.set_text(
            f"{stats['games']} catalog game(s), {stats['duplicates']} duplicate(s), "
            f"{stats['with_artwork']} with artwork, {stats['magnets']} magnet(s), "
            f"{stats['mirrors']} mirror record(s). Catalog only: nothing here is "
            "installed or downloaded."
        )
        self._sources_box.show_all()
        self._resume_btn.set_sensitive(not self._running and self._has_resumable())

    def _show_report(self, source, report: CapabilityReport, live: bool = True,
                     sample: str = "") -> None:
        rows = dict((label, (state, reason)) for label, state, reason in report.rows())
        rows["Authentication"] = (
            "API key" if getattr(source, "requires_auth", False) else "none",
            getattr(source, "auth_kind", "")
            or (
                "This source needs a configured credential."
                if getattr(source, "requires_auth", False) else
                "No credentials are needed for this source."
            ),
        )
        rows["Download links"] = (
            getattr(source, "download_capability", "none") or "none",
            {
                "store-links": "Store links are metadata; the source publishes no game files.",
                "public-files": "The source publishes file links; downloads use the task queue.",
                "authorized-files": "Files are offered through the source's official interface.",
            }.get(
                getattr(source, "download_capability", "none"),
                "This source provides no download mechanism.",
            ),
        )
        for label, widget in self._cap_values.items():
            widget.set_text(rows.get(label, ("unknown", ""))[0])
        # Reasons are attached in construction order.
        for index, label in enumerate(_CAPABILITY_ROWS):
            widget = getattr(self, "_reason_" + str(index))
            widget.set_text(rows.get(label, ("", ""))[1])
        estimate = (
            f" The source reports {report.total_estimate} record(s)."
            if report.total_estimate is not None else
            " No reliable total was reported."
        )
        self._cap_notes.set_text(
            f"{source.name} — {source.canonical_url}.{estimate}"
            + (f" {report.rate_limit}" if report.rate_limit else "")
        )
        self._sample.set_text(sample)
        self._status.set_text(
            f"{source.source_kind} source · pagination: {source.pagination_method} · "
            f"format: {source.response_format}. "
            + (
                "Capabilities above come from this session's live check."
                if live else
                "Capabilities above are the last saved inspection for this source."
            )
        )
        self._import_btn.set_sensitive(
            report.state("catalog") in (Capability.VERIFIED, Capability.PROBABLE)
        )
        self._remove_btn.set_sensitive(bool(self._store.get_source(source.id)))
        attribution = str(getattr(source, "attribution", "") or "")
        if attribution:
            label = str(getattr(source, "attribution_label", "") or "") or attribution
            self._attribution_link.set_uri(attribution)
            self._attribution_link.set_label(f"Game data from {label}")
            self._attribution_link.set_tooltip_text(attribution)
            self._attribution_link.set_no_show_all(False)
            self._attribution_link.show()
        else:
            self._attribution_link.hide()
        self._refresh_sources()

    def _show_error(self, message: str) -> None:
        self._error.set_text(message)
        self._error.set_no_show_all(False)
        self._error.show()

    # -- actions -----------------------------------------------------------

    def _on_import_file(self, _button) -> None:
        if self._running:
            return
        chooser = Gtk.FileChooserDialog(
            title="Import catalog file",
            transient_for=self,
            action=Gtk.FileChooserAction.OPEN,
        )
        chooser.add_buttons(
            "Cancel", Gtk.ResponseType.CANCEL,
            "Import", Gtk.ResponseType.ACCEPT,
        )
        filter_files = Gtk.FileFilter()
        filter_files.set_name("JSON or CSV catalog")
        for pattern in ("*.json", "*.jsonl", "*.ndjson", "*.csv"):
            filter_files.add_pattern(pattern)
        chooser.add_filter(filter_files)
        response = chooser.run()
        path = chooser.get_filename()
        chooser.destroy()
        if response != Gtk.ResponseType.ACCEPT or not path:
            return
        try:
            read_sidecars = self._sidecar_toggle.get_active()
            source = local_source(path, read_sidecars=read_sidecars)
            options = self._options()
            if read_sidecars:
                # Reading sidecars only makes sense with the enrichment pass
                # that follows each record's own link.
                options = ImportOptions(enrich=True, max_records=options.max_records)
            importer = CatalogImporter(self._store, source, options=options)
            report = importer.inspect()
            self._store.save()
        except (FeedError, OSError, ValueError) as exc:
            self._show_error(str(exc))
            return
        self._error.hide()
        self._source = source
        self._report = report
        self._url.set_text(source.canonical_url)
        if read_sidecars:
            reason = report.reasons.get("magnets", "")
            self._sidecar_report.set_text(reason)
            if reason:
                self._sidecar_report.set_no_show_all(False)
                self._sidecar_report.show()
        else:
            self._sidecar_report.set_text("")
            self._sidecar_report.hide()
        # A file whose records carry their own page is worth completing: read
        # each page's published metadata instead of leaving the rows thin.
        if report.state("details") in (Capability.PROBABLE, Capability.VERIFIED):
            self._enrich_toggle.set_active(True)
            self._status.set_text(
                "Each record names its own page, so the details pass will read "
                "that page's published metadata after the catalog is imported."
            )
        self._show_report(source, report, sample=_preview_sample(source))
        self._update_fetch_button()
        self._on_import(False)

    def _on_inspect(self, _button) -> None:
        if self._running:
            return
        url = self._url.get_text().strip()
        try:
            source = self._resolve(url)
        except SourceError as exc:
            self._show_error(str(exc))
            self._status.set_text("This URL cannot be inspected.")
            return
        self._error.hide()
        self._source = source
        if isinstance(source, ConfiguredJsonSource):
            overrides = len(source.config.get("fields", {}))
            self._status.set_text(
                f"Using the custom mapping for {source.canonical_url} "
                f"({overrides} field override(s), {source.pagination_method})."
            )
        options = self._options()
        self._set_busy(True, "Inspecting the source's public interfaces…")

        def work():
            importer = CatalogImporter(self._store, source, options=options)
            report = importer.inspect()
            try:
                self._store.save()
            except OSError:
                pass
            sample = _preview_sample(source)
            return report, sample

        def success(result: object) -> None:
            self._set_busy(False)
            report, sample = (result if isinstance(result, tuple) else (result, ""))
            if not isinstance(report, CapabilityReport):
                self._show_error("The source returned an unusable capability report.")
                return
            self._report = report
            self._show_report(source, report, sample=sample)

        def failure(exc: object) -> None:
            self._set_busy(False)
            self._status.set_text("The source could not be inspected.")
            self._show_error(_error_text(exc))
            self._show_report(
                source,
                CapabilityReport(
                    catalog=Capability.UNKNOWN,
                    reasons={"catalog": _error_text(exc)},
                ),
            )

        _spawn(work, success, failure)

    def _on_import(self, resume: bool) -> None:
        if self._running or self._source is None:
            return
        source = self._source
        job = None
        if resume:
            job = self._store.latest_job(source.id)
            if job is None or not job.resumable:
                self._show_error("There is no interrupted import to resume.")
                return
        self._cancel.clear()
        options = self._options()
        if getattr(source, "read_sidecars", False):
            options.enrich = True
        if job is not None and job.phase == "enrichment":
            # Resume the pass that was interrupted, even if the checkbox is off.
            options.enrich = True
        self._set_busy(True, "Importing catalog…")
        self._progress.set_fraction(0.0)

        def progress(snapshot) -> None:
            data = {
                "pages": snapshot.pages_processed,
                "games": snapshot.games_discovered,
                "imported": snapshot.games_imported,
                "duplicates": snapshot.duplicates,
                "skipped": snapshot.skipped,
                "retries": snapshot.retries,
                "magnets": snapshot.magnets_found,
                "mirrors": snapshot.mirrors_found,
                "total": snapshot.total_estimate,
                "status": snapshot.status,
                "phase": snapshot.phase,
                "format": snapshot.response_format,
                "pagination": snapshot.pagination,
                "last_error": snapshot.last_error,
                "errors": [dict(entry) for entry in snapshot.errors],
                "enriched": snapshot.games_enriched,
                "enrich_done": snapshot.enrich_done,
                "enrich_total": snapshot.enrich_total,
                "enrich_failures": snapshot.enrich_failures,
            }
            GLib.idle_add(self._apply_progress, data)

        def work() -> ImportOutcome:
            importer = CatalogImporter(self._store, source, options=options)
            return importer.run(job=job, cancel=self._cancel, progress=progress)

        def success(result: object) -> None:
            self._set_busy(False, "Import finished.")
            if isinstance(result, ImportOutcome):
                self._counters.set_text(result.summary)
                if result.status in ("failed", "blocked", "partial", "interrupted"):
                    self._show_error(
                        f"Import is {result.status}, not complete. {result.message}"
                    )
                self._refresh_sources()
                self._owner.reload_catalog(announce=True)
                self._owner.toast(result.summary)
                self._remove_btn.set_sensitive(True)

        def failure(exc: object) -> None:
            self._set_busy(False, "Import failed.")
            self._show_error(_error_text(exc))

        _spawn(work, success, failure)

    def _apply_progress(self, data: dict) -> bool:
        total = data.get("total")
        games = int(data.get("games") or 0)
        phase = str(data.get("phase") or "catalog")
        recorded = data.get("errors") or []
        error_count = len(recorded) if isinstance(recorded, list) else 0
        if phase == "enrichment" and int(data.get("enrich_total") or 0) > 0:
            done = int(data.get("enrich_done") or 0)
            self._progress.set_fraction(
                min(1.0, done / int(data.get("enrich_total") or 1))
            )
            self._counters.set_text(
                f"{data.get('status', '')}: enrichment {done} of "
                f"{data.get('enrich_total')} · {data.get('enriched', 0)} enriched · "
                f"{data.get('enrich_failures', 0)} failed · "
                f"{data.get('mirrors', 0)} mirror record(s) · "
                f"{data.get('magnets', 0)} magnet(s) · "
                f"{error_count} error(s)"
            )
        else:
            if isinstance(total, int) and total > 0:
                self._progress.set_fraction(min(1.0, games / total))
            else:
                self._progress.pulse()
            self._counters.set_text(
                f"{data.get('status', '')}: page {data.get('pages', 0)}"
                + (f" of ~{total}" if isinstance(total, int) and total > 0 else "")
                + f" · {games} discovered · {data.get('imported', 0)} imported · "
                f"{data.get('duplicates', 0)} duplicate(s) · "
                f"{data.get('skipped', 0)} skipped · "
                f"{data.get('retries', 0)} retry(ies) · "
                f"{error_count} error(s)"
            )
        reachable = (
            int(data.get("pages") or 0) > 0
            or int(data.get("enrich_done") or 0) > 0
        )
        last_message = ""
        if isinstance(recorded, list) and recorded:
            last_entry = recorded[-1]
            if isinstance(last_entry, dict):
                last_message = str(last_entry.get("message") or "")
        error_text = (
            str(data.get("last_error") or "") or last_message
            or (f"{len(recorded)} recorded" if isinstance(recorded, list) and recorded else "none")
        )
        bits = [
            f"connection: {'reachable' if reachable else 'not confirmed'}",
            f"format: {data.get('format') or 'not reported'}",
            f"pagination: {data.get('pagination') or 'not reported'}",
            f"state: {data.get('status') or 'not started'}",
            f"errors: {error_text}",
        ]
        self._diagnostics.set_text(" · ".join(bits))
        return False

    def _on_fetch_magnets(self) -> None:
        """Fill in magnets for records imported before the sidecar option existed."""
        if self._running:
            return
        source = self._source
        if not isinstance(source, LocalFileSource):
            return
        if self._store.get_source(source.id) is None:
            self._show_error("Import this catalog before fetching its magnet links.")
            return
        confirm = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.CANCEL,
            text="Read the .json links of the records already imported from this catalog?",
        )
        confirm.add_button("Read links", Gtk.ResponseType.OK)
        confirm.format_secondary_text(
            "Each link is requested once; records that already have an answer are "
            "skipped. No files are downloaded."
        )
        response = confirm.run()
        confirm.destroy()
        if response != Gtk.ResponseType.OK:
            return

        try:
            stored = self._store.get_source(source.id)
            path = _path_from_file_url(stored.url if stored else source.canonical_url)
            sidecar_source = local_source(path, read_sidecars=True)
        except (FeedError, OSError, ValueError) as exc:
            self._show_error(str(exc))
            return

        self._cancel.clear()
        self._set_busy(True, "Reading .json links…")
        self._cancel_btn.set_label("Cancel reading")
        self._progress.set_fraction(0.0)

        def progress(done: int, total: int) -> None:
            GLib.idle_add(self._apply_sidecar_progress, done, total)

        def work() -> SidecarRefreshReport:
            return refresh_sidecar_magnets(
                self._store,
                sidecar_source,
                cancel=self._cancel,
                progress=progress,
            )

        def success(result: object) -> None:
            self._cancel_btn.set_label("Cancel import")
            self._set_busy(False)
            if not isinstance(result, SidecarRefreshReport):
                self._show_error("The magnet pass returned no usable report.")
                return
            self._progress.set_fraction(
                0.0 if result.cancelled else
                (1.0 if result.requested else 0.0)
            )
            self._status.set_text(result.summary())
            try:
                self._store.save()
            except OSError as exc:
                self._show_error(f"The magnets were read but could not be saved: {exc}")
            self._refresh_sources()
            self._owner.reload_catalog(announce=True)
            if result.found > 0:
                self._owner.toast(
                    f"{result.found} magnet link(s) are now available on those records."
                )

        def failure(exc: object) -> None:
            self._cancel_btn.set_label("Cancel import")
            self._set_busy(False, "Reading .json links failed.")
            self._show_error(_error_text(exc))

        _spawn(work, success, failure)

    def _apply_sidecar_progress(self, done: int, total: int) -> bool:
        self._status.set_text(f"Read {done} of {total}…")
        if total > 0:
            self._progress.set_fraction(min(1.0, done / total))
        else:
            self._progress.pulse()
        return False

    def _on_remove(self) -> None:
        if self._running:
            return
        source_id = self._source.id if self._source is not None else ""
        if not source_id and self._store.sources:
            source_id = sorted(self._store.sources)[0]
        if not source_id or self._store.get_source(source_id) is None:
            self._show_error("Select a configured source first.")
            return
        confirm = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.CANCEL,
            text="Remove this source and its catalog records?",
        )
        confirm.add_button("Remove", Gtk.ResponseType.OK)
        confirm.format_secondary_text(
            "Imported catalog records, magnets, and mirror records for this source are "
            "forgotten. Files on disk and your local library are not touched."
        )
        response = confirm.run()
        confirm.destroy()
        if response != Gtk.ResponseType.OK:
            return
        games, jobs = self._store.remove_source(source_id)
        try:
            self._store.save()
        except OSError as exc:
            self._show_error(f"The source was removed but could not be saved: {exc}")
        self._report = None
        self._source = None
        self._import_btn.set_sensitive(False)
        self._update_fetch_button()
        self._status.set_text(f"Removed {source_id} ({games} game(s), {jobs} job(s)).")
        self._refresh_sources()
        self._owner.reload_catalog()

    def _on_response(self, dialog: Gtk.Dialog, response: int) -> None:
        # Closing the dialog must stop a running import at its next checkpoint.
        if self._running:
            self._cancel.set()


class KryotoCatalogDialog(Gtk.Dialog):
    """Discover, inspect and browse the Kryoto catalog in one place.

    The dialog never equates "listed" with "downloadable": a row can be in the
    catalog with no links, with unchecked links, or with a link a server check
    actually confirmed as a file.  Catalog work runs in a worker thread and is
    checkpointed by the existing importer, so closing the dialog cancels at the
    next page boundary and Resume continues from the saved cursor.
    """

    def __init__(self, owner: "MainWindow") -> None:
        super().__init__(title="Kryoto Catalog", transient_for=owner, modal=True)
        self._owner = owner
        self._store: CatalogStore = owner.catalog_store
        self._closing = False
        self._running = False
        self._cancel = threading.Event()
        self._cover_token = 0
        self._selected_key = ""
        self.set_default_size(1080, 760)
        self.set_resizable(True)
        self.add_button("Close", Gtk.ResponseType.CLOSE)

        content = self.get_content_area()
        content.set_spacing(0)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        outer.set_margin_start(18)
        outer.set_margin_end(18)
        outer.set_margin_top(14)
        outer.set_margin_bottom(14)
        content.pack_start(outer, True, True, 0)

        heading = Gtk.Label(label="Kryoto catalog", xalign=0)
        heading.get_style_context().add_class("panel-title")
        outer.pack_start(heading, False, False, 0)
        hint = Gtk.Label(
            label=(
                "Discovery uses the site's public API only. A recorded game, a "
                "record with source links, and a verified file are different "
                "states; only a check that returned a file response enables the "
                "download button."
            ),
            xalign=0,
        )
        hint.set_line_wrap(True)
        hint.set_max_width_chars(118)
        hint.get_style_context().add_class("dim")
        hint.get_style_context().add_class("small")
        outer.pack_start(hint, False, False, 0)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._refresh_btn = Gtk.Button(label="Refresh Kryoto Catalog")
        self._refresh_btn.get_style_context().add_class("suggested-action")
        self._refresh_btn.set_tooltip_text(
            "Walk every catalog page with offset pagination, then fetch details "
            "and source links for records that have not been read yet"
        )
        self._refresh_btn.connect("clicked", lambda *_: self._on_refresh(False))
        self._resume_btn = Gtk.Button(label="Resume")
        self._resume_btn.set_tooltip_text("Continue the last interrupted Kryoto scan")
        self._resume_btn.connect("clicked", lambda *_: self._on_refresh(True))
        self._cancel_btn = Gtk.Button(label="Cancel")
        self._cancel_btn.connect("clicked", lambda *_: self._on_cancel())
        self._check_btn = Gtk.Button(label="Check Kryoto links")
        self._check_btn.set_tooltip_text(
            "Probe Kryoto-hosted links only. External mirrors are left for the "
            "per-link Check action; nothing is downloaded."
        )
        self._check_btn.connect("clicked", lambda *_: self._on_check_links())
        self._sources_btn = Gtk.Button(label="Import another source…")
        self._sources_btn.set_tooltip_text(
            "Open the existing Website Sources dialog for a JSON catalog or another adapter"
        )
        self._sources_btn.connect("clicked", lambda *_: self._on_other_sources())
        for button in (
            self._refresh_btn, self._resume_btn, self._cancel_btn,
            self._check_btn,
        ):
            actions.pack_start(button, False, False, 0)
        actions.pack_end(self._sources_btn, False, False, 0)
        outer.pack_start(actions, False, False, 0)

        self._progress = Gtk.ProgressBar()
        self._progress.set_show_text(False)
        outer.pack_start(self._progress, False, False, 0)
        self._status = Gtk.Label(label="Ready.", xalign=0)
        self._status.set_line_wrap(True)
        self._status.set_max_width_chars(118)
        self._status.get_style_context().add_class("dim")
        self._status.get_style_context().add_class("small")
        outer.pack_start(self._status, False, False, 0)

        stats_frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        stats_frame.get_style_context().add_class("start-frame")
        stats_frame.set_margin_top(2)
        self._stats: dict[str, Gtk.Label] = {}
        stats_grid = Gtk.Grid(column_spacing=16, row_spacing=5)
        for index, name in enumerate(
            (
                "Discovered", "Metadata", "With links", "No links",
                "Links listed", "Checked", "Verified files", "Failed requests",
                "Last refresh", "Coverage",
            )
        ):
            caption = Gtk.Label(label=name, xalign=0)
            caption.get_style_context().add_class("fact-name")
            value = Gtk.Label(label="—", xalign=0)
            value.set_selectable(True)
            value.get_style_context().add_class("mono")
            stats_grid.attach(caption, (index % 2) * 2, index // 2, 1, 1)
            stats_grid.attach(value, (index % 2) * 2 + 1, index // 2, 1, 1)
            self._stats[name] = value
        stats_frame.pack_start(stats_grid, False, False, 0)
        outer.pack_start(stats_frame, False, False, 0)

        filters = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._search = Gtk.Entry()
        self._search.set_placeholder_text("Search title, developer, year, or slug")
        self._search.set_hexpand(True)
        self._search.connect("changed", lambda *_: self._rebuild())
        self._provider = Gtk.ComboBoxText()
        self._provider.append("all", "All providers")
        self._provider.set_active_id("all")
        self._provider.connect("changed", lambda *_: self._rebuild())
        self._filter = Gtk.ComboBoxText()
        for key, label in (
            ("all", "All records"),
            ("links", "With source links"),
            ("no-links", "No source links"),
            ("verified", "Verified file"),
            ("unchecked", "Not checked"),
        ):
            self._filter.append(key, label)
        self._filter.set_active_id("all")
        self._filter.connect("changed", lambda *_: self._rebuild())
        self._sort = Gtk.ComboBoxText()
        for key, label in (
            ("title", "Sort: title"),
            ("year", "Sort: year"),
            ("provider", "Sort: provider"),
            ("links", "Sort: links"),
            ("status", "Sort: link state"),
        ):
            self._sort.append(key, label)
        self._sort.set_active_id("title")
        self._sort.connect("changed", lambda *_: self._rebuild())
        filters.pack_start(self._search, True, True, 0)
        filters.pack_start(self._provider, False, False, 0)
        filters.pack_start(self._filter, False, False, 0)
        filters.pack_start(self._sort, False, False, 0)
        outer.pack_start(filters, False, False, 0)

        paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        paned.set_position(310)
        self._tree_store = Gtk.ListStore(str, str, str, str, str, str, object)
        self._tree = Gtk.TreeView(model=self._tree_store)
        self._tree.set_headers_visible(True)
        for index, title, width in (
            (0, "Title", 300), (1, "Year", 64), (2, "Developer", 180),
            (3, "Provider", 120), (4, "Links", 60), (5, "Link state", 190),
        ):
            renderer = Gtk.CellRendererText()
            renderer.set_property("ellipsize", Pango.EllipsizeMode.END)
            column = Gtk.TreeViewColumn(title, renderer, text=index)
            column.set_resizable(True)
            column.set_min_width(width)
            self._tree.append_column(column)
        selection = self._tree.get_selection()
        selection.connect("changed", self._on_select)
        list_scroller = Gtk.ScrolledWindow()
        list_scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        list_scroller.set_min_content_height(210)
        list_scroller.add(self._tree)
        paned.pack1(list_scroller, True, False)

        detail_scroller = Gtk.ScrolledWindow()
        detail_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._detail.set_margin_start(6)
        self._detail.set_margin_end(6)
        self._detail.set_margin_top(8)
        self._detail.set_margin_bottom(8)
        detail_scroller.add(self._detail)
        paned.pack2(detail_scroller, True, False)
        outer.pack_start(paned, True, True, 0)

        self._detail_title = Gtk.Label(label="Select a catalog record", xalign=0)
        self._detail_title.set_line_wrap(True)
        self._detail_title.set_max_width_chars(94)
        self._detail_title.get_style_context().add_class("panel-title")
        self._detail.pack_start(self._detail_title, False, False, 0)
        self._detail_meta = Gtk.Label(xalign=0)
        self._detail_meta.set_line_wrap(True)
        self._detail_meta.set_max_width_chars(100)
        self._detail_meta.set_selectable(True)
        self._detail_meta.get_style_context().add_class("dim")
        self._detail_meta.get_style_context().add_class("small")
        self._detail.pack_start(self._detail_meta, False, False, 0)
        self._detail_description = Gtk.Label(xalign=0)
        self._detail_description.set_line_wrap(True)
        self._detail_description.set_max_width_chars(100)
        self._detail_description.set_selectable(True)
        self._detail.pack_start(self._detail_description, False, False, 0)
        cover_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self._cover = Gtk.Image.new_from_icon_name(
            "applications-games-symbolic", Gtk.IconSize.DIALOG
        )
        self._cover.set_size_request(120, 160)
        self._cover.set_valign(Gtk.Align.START)
        self._cover.get_style_context().add_class("poster-frame")
        cover_row.pack_start(self._cover, False, False, 0)
        self._detail_side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        cover_row.pack_start(self._detail_side, True, True, 0)
        self._detail.pack_start(cover_row, False, False, 0)
        self._detail.pack_start(_section_label("Source links"), False, False, 0)
        self._links_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._detail_links: list[MirrorRecord] = []
        self._detail.pack_start(self._links_box, False, False, 0)
        link_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._send_links = Gtk.Button(label="Send to JDownloader")
        self._send_links.set_tooltip_text(
            "Hand this game's links to a running JDownloader 2 "
            "(Click'n'Load on 127.0.0.1:9666). Large or unstable downloads "
            "resume better there."
        )
        self._send_links.set_sensitive(False)
        self._send_links.connect("clicked", self._on_send_links)
        self._send_abdm = Gtk.Button(label="Send to AB Download Manager")
        self._send_abdm.set_tooltip_text(
            "Hand this game's links to a running AB Download Manager (REST API "
            "on 127.0.0.1:15151). It downloads them itself, into this app's "
            "download folder."
        )
        self._send_abdm.set_sensitive(False)
        self._send_abdm.connect("clicked", self._on_send_abdm)
        hint = Gtk.Label(label=external_dm.NOT_AFFILIATED, xalign=0)
        hint.set_line_wrap(True)
        hint.set_max_width_chars(74)
        hint.get_style_context().add_class("dim")
        hint.get_style_context().add_class("small")
        link_actions.pack_start(self._send_links, False, False, 0)
        link_actions.pack_start(self._send_abdm, False, False, 0)
        link_actions.pack_start(hint, True, True, 0)
        self._detail.pack_start(link_actions, False, False, 0)

        self.connect("response", self._on_response)
        self.connect("destroy", self._on_destroy)
        content.show_all()
        self._refresh_view()

    # -- state --------------------------------------------------------------

    def _source_name(self, source_id: str) -> str:
        record = self._store.get_source(source_id)
        if record is not None and record.name:
            return record.name
        return source_id or "unknown"

    def _on_response(self, _dialog, _response) -> None:
        self._on_destroy()

    def _on_destroy(self, *_args) -> None:
        self._closing = True
        self._cancel.set()

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._running = busy
        self._refresh_btn.set_sensitive(not busy)
        self._resume_btn.set_sensitive(not busy and self._has_resumable())
        self._cancel_btn.set_sensitive(busy)
        self._check_btn.set_sensitive(not busy)
        self._sources_btn.set_sensitive(not busy)
        if message:
            self._status.set_text(message)

    def _has_resumable(self) -> bool:
        job = self._store.latest_job("kryoto")
        return bool(job and job.resumable)

    def _on_other_sources(self) -> None:
        dialog = WebsiteSourcesDialog(self._owner)
        dialog.run()
        dialog.destroy()
        self._refresh_view()

    # -- refresh ------------------------------------------------------------

    def _on_refresh(self, resume: bool) -> None:
        if self._running:
            return
        source = KryotoSource()
        job = None
        if resume:
            job = self._store.latest_job("kryoto")
            if job is None or not job.resumable:
                self._status.set_text("There is no interrupted Kryoto scan to resume.")
                return
        self._cancel.clear()
        self._set_busy(True, "Resuming the Kryoto scan…" if resume else "Refreshing the Kryoto catalog…")
        self._progress.set_fraction(0.0)
        # 25 is the API's largest useful page; larger requests are ignored and
        # make the server spend more time serialising the same records.
        options = ImportOptions(page_size=25, enrich=True, workers=4)

        def progress(snapshot) -> None:
            GLib.idle_add(self._on_progress, snapshot)

        def work():
            importer = CatalogImporter(self._store, source, options=options)
            report = None
            if not resume:
                report = importer.inspect()
            outcome = importer.run(
                job=job if resume else None,
                cancel=self._cancel,
                progress=progress,
                enrich=True,
            )
            try:
                self._store.save()
            except OSError:
                pass
            return outcome, report

        def success(result) -> None:
            outcome = result[0] if isinstance(result, tuple) else result
            self._set_busy(False)
            if isinstance(outcome, ImportOutcome):
                self._status.set_text(outcome.summary)
            self._refresh_view()

        def failure(exc) -> None:
            self._set_busy(False)
            self._status.set_text(f"The refresh failed: {_error_text(exc)}")
            self._refresh_view()

        _spawn(work, success, failure)

    def _on_progress(self, snapshot) -> bool:
        if self._closing:
            return False
        pages = getattr(snapshot, "pages_processed", 0)
        enriched = getattr(snapshot, "enrich_done", 0)
        total = getattr(snapshot, "enrich_total", 0)
        if getattr(snapshot, "phase", "") == "enrichment" and total:
            self._progress.set_fraction(min(1.0, enriched / total))
            self._status.set_text(
                f"Details and source links: {enriched}/{total} · "
                f"{getattr(snapshot, 'mirrors_found', 0)} link(s) recorded"
            )
        else:
            self._progress.pulse()
            self._status.set_text(
                f"Catalog page {pages} · "
                f"{getattr(snapshot, 'games_discovered', 0)} game(s) discovered"
            )
        return False

    def _on_cancel(self) -> None:
        self._cancel.set()
        self._status.set_text(
            "Cancelling at the next page boundary; the checkpoint will be kept."
        )

    def _on_check_links(self) -> None:
        if self._running:
            return
        targets = []
        for game in self._store.iter_games():
            for mirror in self._store.mirrors_for(game.key):
                if kryoto.host_allowed(mirror.url, kryoto.KRYOTO_HOSTS):
                    targets.append(mirror)
        if not targets:
            self._status.set_text(
                "No Kryoto-hosted links are recorded yet. Refresh the catalog first."
            )
            return
        self._cancel.clear()
        self._set_busy(True, f"Checking {len(targets)} Kryoto-hosted link(s)…")

        def work():
            outcomes = []
            for index, mirror in enumerate(targets, start=1):
                if self._cancel.is_set():
                    break
                result = kryoto.check_link(mirror.url, allow_external=False)
                outcomes.append((mirror, result))
                GLib.idle_add(
                    self._on_check_progress, index, len(targets), mirror.label or mirror.host
                )
            return outcomes

        def success(result) -> None:
            if isinstance(result, list):
                for mirror, check in result:
                    mirror.last_checked = check.checked_at
                    mirror.check_status = check.status
                    mirror.url_kind = check.kind or mirror.url_kind
                    mirror.content_type = check.content_type
                    mirror.notes = tuple(check.notes)
                    if check.filename and not mirror.filename:
                        mirror.filename = check.filename
                    if check.size and not mirror.size:
                        mirror.size = check.size
                try:
                    self._store.save()
                except OSError as exc:
                    self._status.set_text(f"Checks finished, but saving failed: {exc}")
                else:
                    self._status.set_text(
                        f"Checked {len(result)} link(s). A link is only marked "
                        "verified when the server returned a file response."
                    )
            self._set_busy(False)
            self._refresh_view()

        def failure(exc) -> None:
            self._set_busy(False)
            self._status.set_text(f"Link checks failed: {_error_text(exc)}")

        _spawn(work, success, failure)

    def _on_check_progress(self, index: int, total: int, label: str) -> bool:
        if self._closing:
            return False
        self._progress.set_fraction(index / max(1, total))
        self._status.set_text(f"Checking {index}/{total}: {label or 'link'}")
        return False

    # -- view ---------------------------------------------------------------

    def _refresh_view(self) -> None:
        if self._closing:
            return
        summary = self._store.source_summary("kryoto")
        self._stats["Discovered"].set_text(str(summary["games_discovered"]))
        self._stats["Metadata"].set_text(str(summary["metadata_retrieved"]))
        self._stats["With links"].set_text(str(summary["games_with_links"]))
        self._stats["No links"].set_text(str(summary["games_without_links"]))
        self._stats["Links listed"].set_text(str(summary["links_listed"]))
        self._stats["Checked"].set_text(str(summary["links_checked"]))
        self._stats["Verified files"].set_text(str(summary["links_verified"]))
        self._stats["Failed requests"].set_text(str(summary["failed_requests"]))
        refreshed = summary["last_refresh"]
        self._stats["Last refresh"].set_text(
            time.strftime("%Y-%m-%d %H:%M", time.localtime(refreshed))
            if refreshed else "never"
        )
        self._stats["Coverage"].set_text(
            "scan complete" if summary["pagination_complete"]
            else f"not complete ({summary['job_status']})"
        )
        self._resume_btn.set_sensitive(not self._running and self._has_resumable())
        self._sync_providers()
        self._rebuild()

    def _sync_providers(self) -> None:
        active = self._provider.get_active_id() or "all"
        self._provider.remove_all()
        self._provider.append("all", "All providers")
        for source_id in sorted(self._store.sources, key=self._source_name):
            self._provider.append(source_id, self._source_name(source_id))
        if not self._provider.set_active_id(active):
            self._provider.set_active_id("all")

    def _visible_games(self) -> list[CatalogGame]:
        query = self._search.get_text().strip().casefold()
        provider = self._provider.get_active_id() or "all"
        kind = self._filter.get_active_id() or "all"
        games: list[CatalogGame] = []
        for game in self._store.iter_games():
            if provider != "all" and game.source_id != provider:
                continue
            if query:
                haystack = " ".join(
                    (game.title, game.developer, game.year, game.game_id)
                ).casefold()
                if query not in haystack:
                    continue
            mirrors = self._store.mirrors_for(game.key)
            state = self._store.download_state(game.key)
            if kind == "links" and not mirrors:
                continue
            if kind == "no-links" and mirrors:
                continue
            if kind == "verified" and state != "ready":
                continue
            if kind == "unchecked" and state != "unverified":
                continue
            games.append(game)
        key = self._sort.get_active_id() or "title"
        if key == "year":
            games.sort(key=lambda game: (game.year or "9999", game.title.casefold()))
        elif key == "provider":
            games.sort(key=lambda game: (self._source_name(game.source_id), game.title.casefold()))
        elif key == "links":
            games.sort(key=lambda game: (-len(self._store.mirrors_for(game.key)), game.title.casefold()))
        elif key == "status":
            games.sort(key=lambda game: (
                DOWNLOAD_STATE_RANK.get(self._store.download_state(game.key), 1),
                game.title.casefold(),
            ))
        else:
            games.sort(key=lambda game: (game.title.casefold(), game.year))
        return games

    def _rebuild(self) -> None:
        if self._closing:
            return
        previous = self._selected_key
        self._tree_store.clear()
        for game in self._visible_games():
            mirrors = self._store.mirrors_for(game.key)
            state = self._store.download_state(game.key)
            self._tree_store.append([
                game.title,
                game.year,
                game.developer,
                self._source_name(game.source_id),
                str(len(mirrors)),
                DOWNLOAD_STATE_LABELS.get(state, state),
                game,
            ])
        if len(self._tree_store):
            target = 0
            for index, row in enumerate(self._tree_store):
                if row[6] is not None and row[6].key == previous:
                    target = index
                    break
            self._tree.get_selection().select_path(Gtk.TreePath.new_from_indices([target]))
        else:
            self._show_detail(None)

    def _on_select(self, selection: Gtk.TreeSelection) -> None:
        model, tree_iter = selection.get_selected()
        if tree_iter is None:
            self._show_detail(None)
            return
        game = model[tree_iter][6]
        if isinstance(game, CatalogGame):
            self._selected_key = game.key
            self._show_detail(game)

    def _show_detail(self, game: CatalogGame | None) -> None:
        self._cover_token += 1
        token = self._cover_token
        for child in list(self._links_box.get_children()):
            self._links_box.remove(child)
        for child in list(self._detail_side.get_children()):
            self._detail_side.remove(child)
        self._cover.set_from_icon_name(
            "applications-games-symbolic", Gtk.IconSize.DIALOG
        )
        if game is None:
            self._detail_title.set_text("No catalog records to show")
            self._detail_meta.set_text("")
            self._detail_description.set_text("")
            empty = Gtk.Label(
                label="Refresh the Kryoto catalog, import a JSON catalog, or clear the filters.",
                xalign=0,
            )
            empty.set_line_wrap(True)
            empty.get_style_context().add_class("dim")
            self._links_box.pack_start(empty, False, False, 0)
            self._links_box.show_all()
            return
        self._detail_title.set_text(game.title or game.game_id)
        mirrors = self._store.mirrors_for(game.key)
        provider_fields = (
            game.extra.get("source_fields")
            if isinstance(game.extra, dict) else None
        )
        provider_fields = provider_fields if isinstance(provider_fields, dict) else {}
        version = game.extra.get("version") if isinstance(game.extra, dict) else ""
        version = version or provider_fields.get("version") or ""
        sizes = []
        if provider_fields.get("size"):
            sizes.append(f"install {provider_fields['size']}")
        if provider_fields.get("download_size"):
            sizes.append(f"download {provider_fields['download_size']}")
        bits = [
            value for value in (
                game.year, game.developer or game.creator,
                f"v{version}" if version else "",
                " · ".join(sizes),
                self._source_name(game.source_id),
                f"{len(mirrors)} source link(s)",
            ) if value
        ]
        self._detail_meta.set_text(" · ".join(bits) or "No further metadata was reported.")
        self._detail_description.set_text(game.description or "")
        self._detail_description.set_visible(bool(game.description))

        source_btn = Gtk.Button(label="Open source page")
        source_btn.set_halign(Gtk.Align.START)
        source_btn.set_sensitive(bool(game.source_url))
        source_btn.connect(
            "clicked", lambda _b, url=game.source_url: self._owner.open_external_url(url)
        )
        copy_source = Gtk.Button(label="Copy source URL")
        copy_source.set_sensitive(bool(game.source_url))
        copy_source.connect(
            "clicked", lambda _b, url=game.source_url: self._owner.copy_text(url)
        )
        self._detail_side.pack_start(source_btn, False, False, 0)
        self._detail_side.pack_start(copy_source, False, False, 0)
        field_bits = " · ".join(
            f"{name}: {_provider_value(value)}"
            for name, value in list(provider_fields.items())[:10]
        )
        if field_bits:
            fields = Gtk.Label(
                label="Reported by the provider: " + field_bits, xalign=0
            )
            fields.set_line_wrap(True)
            fields.set_max_width_chars(80)
            fields.set_selectable(True)
            fields.get_style_context().add_class("dim")
            fields.get_style_context().add_class("small")
            self._detail_side.pack_start(fields, False, False, 0)

        if not mirrors:
            empty = Gtk.Label(
                label=(
                    "This game is listed in the catalog but the source reported "
                    "no download links for it."
                ),
                xalign=0,
            )
            empty.set_line_wrap(True)
            empty.get_style_context().add_class("dim")
            empty.get_style_context().add_class("small")
            self._links_box.pack_start(empty, False, False, 0)
        self._detail_links = list(mirrors)
        self._send_links.set_sensitive(bool(mirrors))
        self._send_abdm.set_sensitive(bool(mirrors))
        for mirror in mirrors:
            self._add_link_row(mirror)

        if game.cover_url and self._owner.can_fetch_image(
            game.cover_url, game.source_id
        ):
            self._owner.fetch_poster(
                f"kryoto-dialog-{game.key}",
                game.cover_url,
                lambda data, t=token: self._on_cover(t, data),
                lambda _exc: None,
                allowed_hosts=self._owner.image_hosts_for(game.source_id),
            )
        self._links_box.show_all()
        self._detail_side.show_all()

    def _on_cover(self, token: int, data: object) -> None:
        if self._closing or token != self._cover_token:
            return
        if not isinstance(data, (bytes, bytearray)):
            return
        try:
            loader = GdkPixbuf.PixbufLoader()
            loader.write(data)
            loader.close()
            pixbuf = loader.get_pixbuf()
        except (GLib.Error, TypeError, ValueError):
            return
        if pixbuf is None:
            return
        width, height = pixbuf.get_width(), pixbuf.get_height()
        scale = min(120 / max(1, width), 160 / max(1, height))
        scaled = pixbuf.scale_simple(
            max(1, int(width * scale)), max(1, int(height * scale)),
            GdkPixbuf.InterpType.BILINEAR,
        )
        if scaled is not None:
            self._cover.set_from_pixbuf(scaled)

    def _on_send_links(self, _button: Gtk.Button) -> None:
        """Hand every link of the selected game to the user's download manager."""
        self._owner.send_links_to_jdownloader(
            self._detail_links, self._detail_title.get_text()
        )

    def _on_send_abdm(self, _button: Gtk.Button) -> None:
        """Start the selected game's links in AB Download Manager instead."""
        self._owner.send_links_to_ab_download_manager(
            self._detail_links, self._detail_title.get_text()
        )

    def _add_link_row(self, mirror: MirrorRecord) -> None:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        label = mirror.label or mirror.filename or mirror.host or mirror.url
        name = Gtk.Label(label=label, xalign=0)
        name.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        name.set_max_width_chars(62)
        name.get_style_context().add_class("fact-name")
        bits = [value for value in (
            mirror.host,
            mirror.url_kind.replace("-", " ") if mirror.url_kind else "",
            human_size(mirror.size) if mirror.size else "",
            mirror.status_label(),
        ) if value]
        detail = Gtk.Label(label=" · ".join(bits), xalign=0)
        detail.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        detail.set_max_width_chars(80)
        detail.get_style_context().add_class("dim")
        detail.get_style_context().add_class("small")
        url = Gtk.Label(label=mirror.url, xalign=0)
        url.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        url.set_max_width_chars(80)
        url.set_selectable(True)
        url.set_tooltip_text(mirror.url)
        url.get_style_context().add_class("mono")
        url.get_style_context().add_class("small")
        text.pack_start(name, False, False, 0)
        text.pack_start(detail, False, False, 0)
        text.pack_start(url, False, False, 0)
        row.pack_start(text, True, True, 0)
        copy = Gtk.Button(label="Copy Link")
        copy.connect("clicked", lambda _b, value=mirror.url: self._owner.copy_text(value))
        open_btn = Gtk.Button(label="Open in Browser")
        open_btn.connect(
            "clicked", lambda _b, value=mirror.url: self._owner.open_external_url(value)
        )
        check = Gtk.Button(label="Check")
        check.connect(
            "clicked",
            lambda _b, item=mirror: self._owner.check_catalog_mirror(
                item, lambda _r: GLib.idle_add(self._refresh_view)
            ),
        )
        row.pack_end(copy, False, False, 0)
        row.pack_end(open_btn, False, False, 0)
        row.pack_end(check, False, False, 0)
        download = Gtk.Button(label="Download")
        if kryoto.is_downloadable_link(mirror.check_status, mirror.url_kind):
            download.set_tooltip_text(
                "Queue this verified file with the existing download manager"
            )
            download.get_style_context().add_class("suggested-action")
        else:
            download.set_tooltip_text(
                "Checks this link first and queues it only if the server answers "
                "with a file. A web page is never downloaded."
            )
        download.connect(
            "clicked",
            lambda _b, item=mirror: self._owner.download_catalog_mirror(
                item, self._detail_title.get_text(), self._refresh_view
            ),
        )
        row.pack_end(download, False, False, 0)
        self._links_box.pack_start(row, False, False, 0)


class Application(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID)
        self._window: MainWindow | None = None
        self._dl_pending = False
        self._dl_lock = threading.Lock()
        self._shutdown_done = False
        self._startup_warning: str | None = None

    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        GLib.set_prgname("lumora")
        if hasattr(Gdk, "set_program_class"):
            Gdk.set_program_class("Lumora")
        GLib.set_application_name(APP_NAME)
        Gtk.Window.set_default_icon_name("applications-games")
        load_css()

        # Copy pre-0.6.0 folders across before anything reads or writes
        # the config, so a first-launch save cannot overwrite it.
        platform_support.ensure_app_dirs()
        # Pin the profile folder for the whole run: a switch only takes effect
        # at the next start, so no store may open the other profile's files now.
        profiles.start_session()
        # The profile in use owns the settings and the library index.  The
        # Default profile keeps the folder this install already uses, so an
        # existing library is never moved or copied.
        self.profiles = profiles.load()
        self.profile_data_dir = profiles.active_data_dir()
        self.settings = Settings(profiles.active_settings_path())
        self.client = ArchiveClient()
        self.download_dir = self._initial_download_dir()
        self.library_store = LibraryStore(
            path=self.profile_data_dir / "library.json",
            download_root=self.download_dir,
        )
        self.manager = DownloadManager(
            observer=self._on_download_change,
            queue_path=platform_support.data_dir() / "downloads.json",
            timeout=float(self.settings.network_timeout),
            retry_limit=self.settings.download_retries,
        )
        self.catalog_store = CatalogStore(
            path=platform_support.data_dir() / "catalog.json"
        )

        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, self._on_signal)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self._on_signal)

    def _initial_download_dir(self) -> Path:
        saved = self.settings.download_dir
        if saved:
            try:
                return check_download_dir(saved)
            except (OSError, ValueError) as exc:
                self._startup_warning = (
                    "The saved download folder is unavailable, so the default is "
                    f"being used. {_error_text(exc)}"
                )
        try:
            return check_download_dir(default_download_dir())
        except (OSError, ValueError):
            return Path.home()

    def do_activate(self) -> None:
        if self._window is None:
            self._window = MainWindow(
                self,
                self.client,
                self.manager,
                self.settings,
                self.download_dir,
                self.library_store,
                self._startup_warning,
                self.catalog_store,
            )
            self._window.show_all()
        self._window.present()

    def _on_signal(self) -> bool:
        self.quit()
        return GLib.SOURCE_REMOVE

    # -- downloader observer (called from the worker thread) ----------------

    def _on_download_change(self) -> None:
        with self._dl_lock:
            if self._dl_pending:
                return
            self._dl_pending = True
        GLib.idle_add(self._flush_downloads)

    def _flush_downloads(self) -> bool:
        with self._dl_lock:
            self._dl_pending = False
        window = self._window
        if window is None or self._shutdown_done:
            return False
        window.update_downloads(self.manager.snapshots())
        return False

    def do_shutdown(self) -> None:
        if not self._shutdown_done:
            self._shutdown_done = True
            if self._window is not None:
                self._window.prepare_shutdown()
            else:
                self.manager.shutdown()
        Gtk.Application.do_shutdown(self)


def main() -> int:
    application = Application()
    return application.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
