"""Native Tkinter frontend for Lumora.

A second, native-feeling frontend over the same platform-neutral core used by
the GTK application.  It targets Windows 10/11 (bundled with PyInstaller) but
runs on any machine with Tk 8.6 so it can be developed and reviewed on Linux.

Rules used throughout:

* No network or heavy filesystem work happens on the Tk thread.  Short-lived
  daemon threads do the work and results are applied through a queue drained
  by ``root.after``.
* Downloaded files are never executed automatically, overwritten, or deleted.
  Launching always goes through a review dialog and an explicit confirmation.
* Routine failures are shown inline in the status bar or the relevant pane,
  not in modal dialogs.
"""

from __future__ import annotations

import io
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk
from typing import Any, Callable, Sequence

from . import __version__, platform_support, tk_style
from . import streaming
from .windows_media import build_media_tab
from .archive import (
    ArchiveClient,
    ArchiveFile,
    ArchiveItem,
    CollectionResult,
    GameSummary,
    build_download_url,
)
from .catalog_store import DOWNLOAD_STATE_LABELS, DOWNLOAD_STATE_RANK
from .compatibility import STATUSES as COMPATIBILITY_STATUSES
from .downloader import DownloadManager, DownloadStatus, TaskSnapshot
from .filetypes import is_shell
from .integrity import IntegrityResult, IntegrityStatus, verify_file
from .launch import open_launch_log, plan_launch
from .launch import launch as launch_reviewed_file
from .library import MANIFEST_NAME, LocalFile, game_folder, scan_game_folder, write_manifest
from .library_store import (
    STATUSES as LIBRARY_STATUSES,
    DuplicateGroup,
    LibraryEntry,
    LibraryStore,
)
from .settings import MIN_WINDOW_SIZE, Settings
from .torrent import (
    STATUS_COMPLETED as TORRENT_COMPLETED,
    STATUS_DOWNLOADING as TORRENT_DOWNLOADING,
    STATUS_FAILED as TORRENT_FAILED,
    STATUS_PAUSED as TORRENT_PAUSED,
    STATUS_QUEUED as TORRENT_QUEUED,
    EngineUnavailable,
    TorrentError,
    TorrentManager,
    TorrentTask,
)
from .util import (
    AppError,
    LaunchError,
    check_download_dir,
    human_eta,
    human_size,
    human_speed,
    is_within,
    short,
)

APP_TITLE = "Lumora"
COLLECTION_URL = "https://archive.org/details/native-linux-games-collection"

try:  # Pillow is optional; the frozen Windows build excludes it on purpose.
    from PIL import Image, ImageTk

    PIL_AVAILABLE = True
except Exception:  # pragma: no cover - depends on the machine
    Image = None  # type: ignore[assignment]
    ImageTk = None  # type: ignore[assignment]
    PIL_AVAILABLE = False


STATUS_LABELS = {
    DownloadStatus.QUEUED: "Queued",
    DownloadStatus.RUNNING: "Running",
    DownloadStatus.PAUSED: "Paused",
    DownloadStatus.COMPLETED: "Completed",
    DownloadStatus.FAILED: "Failed",
    DownloadStatus.CANCELLED: "Cancelled",
}

INTEGRITY_LABELS = {
    "": "Not checked",
    "verified_sha256": "SHA-256 verified",
    "verified_legacy": "MD5/SHA-1 verified (not SHA-256)",
    "mismatch": "Checksum mismatch",
    "unavailable": "No published checksum",
    "error": "Check failed",
}

WINDOWS_LAUNCH_SUFFIXES = (".exe", ".com", ".bat", ".cmd")


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    if count == 1:
        return singular
    return plural or singular + "s"


def _error_text(exc: BaseException) -> str:
    text = str(exc).strip()
    if isinstance(exc, (AppError, OSError, ValueError)):
        return text or exc.__class__.__name__
    return text or exc.__class__.__name__


def _shorten_home(path: Path | str) -> str:
    text = str(path)
    home = str(Path.home())
    if text == home:
        return "~"
    if text.startswith(home + os.sep):
        return "~" + text[len(home):]
    return text


def _capitalise(value: str) -> str:
    return value[:1].upper() + value[1:] if value else value


def _local_launchable(local: LocalFile) -> bool:
    if local.executable_candidate:
        return True
    if platform_support.IS_WINDOWS:
        return local.path.suffix.lower() in WINDOWS_LAUNCH_SUFFIXES
    return False


class WindowsApp:
    """The whole application: three tabs plus a settings dialog."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self._closing = False
        self._ui_queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self._downloads_dirty = threading.Event()
        self._status_after: str | None = None

        self.settings = Settings()
        self.theme = tk_style.setup(root, theme=self.settings.theme)
        self.fonts = self.theme.fonts
        self.px = self.theme.px

        self.client = ArchiveClient(cache_root=platform_support.cache_dir())
        self._startup_warning: str | None = None
        self.download_dir = self._initial_download_dir()
        self.library = LibraryStore(
            platform_support.data_dir() / "library.json", self.download_dir
        )
        self.manager = DownloadManager(
            observer=self._on_download_change,
            queue_path=platform_support.data_dir() / "downloads.json",
            url_resolver=self._resolve_task_url,
            timeout=float(self.settings.network_timeout),
            retry_limit=self.settings.download_retries,
        )
        self._restored = self.manager.has_unfinished()
        self._restored_visible = self._restored

        self.torrents = TorrentManager(
            platform_support.data_dir() / "torrents.json",
            self.download_dir,
            engine_path=self.settings.torrent_client,
            on_complete=self._on_torrent_complete,
        )
        self._selected_torrent_id: str | None = None
        self._torrent_updating = False
        self._torrent_refreshing = False
        self._torrent_after: str | None = None
        self._torrent_known_status: dict[str, str] = {}

        # Browse state
        self._games: list[GameSummary] = []
        self._games_by_id: dict[str, GameSummary] = {}
        self._visible_games: list[GameSummary] = []
        self._selected_identifier: str | None = None
        self._current_item: ArchiveItem | None = None
        self._item_token = 0
        self._game_render_token = 0
        self._collection_loading = False
        self._item_files: dict[str, ArchiveFile] = {}
        self._browse_photo: Any = None
        self._browse_source_image: Any = None
        self._browse_poster_after: str | None = None
        self._browse_poster_width = 0
        self._screenshots: list[Any] = []
        self._shot_photos: list[Any] = []
        self._screenshot_token = 0

        # Downloads state
        self._dl_known_status: dict[str, DownloadStatus] = {}
        self._dl_progress_mode = "determinate"
        self._dl_updating = False
        self._selected_task_id: str | None = None

        # Library state
        self._library_selected: str | None = None
        self._library_entry: LibraryEntry | None = None
        self._library_token = 0
        self._library_files: dict[str, LocalFile] = {}
        self._library_cover_photo: Any = None
        self._rescanning = False
        self._lib_status_after: str | None = None
        self._lib_launch_files: dict[str, LocalFile] = {}
        self._lib_launch_radios: list[ttk.Radiobutton] = []

        self._settings_window: tk.Toplevel | None = None
        self._duplicates_window: tk.Toplevel | None = None

        self._configure_window()
        self._build_menu()
        self._build_statusbar()
        self._build_notebook()
        self._bind_shortcuts()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._refresh_downloads()
        self._refresh_library_table()
        self.root.after(60, self._startup)
        self.root.after(100, self._drain_ui)
        self.root.after(250, self._refresh_torrents)
        self._schedule_torrent_refresh()

    # -- construction helpers ---------------------------------------------

    def _w(self, pixels: float) -> int:
        return int(round(pixels * self.px))

    def _initial_download_dir(self) -> Path:
        saved = self.settings.download_dir
        if saved:
            try:
                return check_download_dir(saved)
            except (OSError, ValueError) as exc:
                self._startup_warning = (
                    "The saved download folder is unavailable, so the default "
                    f"was used. {_error_text(exc)}"
                )
        try:
            return check_download_dir(platform_support.default_download_dir())
        except (OSError, ValueError):
            return Path.home()

    def _configure_window(self) -> None:
        self.root.title(APP_TITLE)
        self.root.configure(background=tk_style.BG)
        self.root.minsize(*MIN_WINDOW_SIZE)
        width, height = self.settings.window_size
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 3)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        if self.settings.maximized and platform_support.IS_WINDOWS:
            try:
                self.root.state("zoomed")
            except tk.TclError:
                pass

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(
            label="Refresh", underline=0, accelerator="F5", command=self._on_f5
        )
        file_menu.add_command(
            label="Settings…", underline=0, accelerator="Ctrl+,", command=self._open_settings
        )
        file_menu.add_separator()
        file_menu.add_command(label="Exit", underline=1, accelerator="Alt+F4",
                              command=self._on_close)
        menubar.add_cascade(label="File", underline=0, menu=file_menu)

        view_menu = tk.Menu(menubar, tearoff=False)
        view_menu.add_command(label="Browse", underline=0, accelerator="Ctrl+1",
                              command=lambda: self._focus_tab(0))
        view_menu.add_command(label="Downloads", underline=0, accelerator="Ctrl+2",
                              command=lambda: self._focus_tab(1))
        view_menu.add_command(label="Library", underline=0, accelerator="Ctrl+3",
                              command=lambda: self._focus_tab(2))
        menubar.add_cascade(label="View", underline=0, menu=view_menu)

        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="Open collection on archive.org", underline=0,
                              command=self._open_collection_page)
        help_menu.add_command(label="About Lumora", underline=0,
                              command=self._show_about)
        menubar.add_cascade(label="Help", underline=0, menu=help_menu)
        self.root.configure(menu=menubar)

    def _build_statusbar(self) -> None:
        bar = tk.Frame(self.root, background=tk_style.SURFACE_ALT,
                       highlightthickness=1, highlightbackground=tk_style.BORDER,
                       bd=0)
        bar.pack(side="bottom", fill="x")
        self.status_var = tk.StringVar(value="Ready")
        self.status_label = ttk.Label(
            bar, textvariable=self.status_var, style="Status.TLabel", anchor="w"
        )
        self.status_label.pack(side="left", fill="x", expand=True)
        hint = ttk.Label(
            bar,
            text="Ctrl+1 Browse   Ctrl+2 Downloads   Ctrl+3 Library   Ctrl+4 Media",
            style="Status.TLabel",
            anchor="e",
        )
        hint.pack(side="right")

    def _build_notebook(self) -> None:
        outer = ttk.Frame(self.root, style="App.TFrame", padding=(12, 8, 12, 6))
        outer.pack(fill="both", expand=True)
        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill="both", expand=True)
        self.browse_tab = ttk.Frame(self.notebook, style="App.TFrame", padding=(0, 10, 0, 0))
        self.downloads_tab = ttk.Frame(self.notebook, style="App.TFrame", padding=(0, 10, 0, 0))
        self.library_tab = ttk.Frame(self.notebook, style="App.TFrame", padding=(0, 10, 0, 0))
        self.media_tab = ttk.Frame(self.notebook, style="App.TFrame", padding=(0, 10, 0, 0))
        self.notebook.add(self.browse_tab, text="Browse")
        self.notebook.add(self.downloads_tab, text="Downloads")
        self.notebook.add(self.library_tab, text="Library")
        self.notebook.add(self.media_tab, text="Media")
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        self._build_browse_tab()
        self._build_downloads_tab()
        self._build_library_tab()
        self._build_media_tab()

    def _build_media_tab(self) -> None:
        build_media_tab(self, self.media_tab)

    # -- browse ------------------------------------------------------------

    def _build_browse_tab(self) -> None:
        tab = self.browse_tab

        toolbar = ttk.Frame(tab, style="Toolbar.TFrame", padding=(14, 10))
        toolbar.pack(fill="x")
        toolbar.columnconfigure(1, weight=1)
        ttk.Label(toolbar, text="Search", style="FieldLabel.TLabel").grid(
            row=0, column=0, padx=(0, 10), sticky="w"
        )
        self.browse_search_var = tk.StringVar()
        self.browse_search = ttk.Entry(toolbar, textvariable=self.browse_search_var)
        self.browse_search.grid(row=0, column=1, sticky="ew")
        tk_style.tooltip(
            self.browse_search,
            "Search titles, identifiers, years, creators, and subjects (Ctrl+F)",
        )
        self._add_placeholder(
            self.browse_search, self.browse_search_var,
            "Title, creator, year, or subject",
        )
        self.browse_search_var.trace_add("write", self._schedule_browse_filter)
        self.browse_search.bind("<Return>", lambda _e: self._apply_browse_filter())
        self.browse_search.bind("<Escape>", lambda _e: self._clear_browse_search())

        refresh = ttk.Button(toolbar, text="Refresh", command=lambda: self._load_collection(True))
        refresh.grid(row=0, column=5, padx=(10, 0))
        tk_style.tooltip(refresh, "Re-download the collection list (F5)")
        settings_btn = ttk.Button(toolbar, text="Settings…", command=self._open_settings)
        settings_btn.grid(row=0, column=6, padx=(8, 0))
        tk_style.tooltip(settings_btn, "Download folder, appearance, startup behaviour, and shortcuts (Ctrl+,)")

        ttk.Label(toolbar, text="Sort", style="FieldLabel.TLabel").grid(
            row=0, column=2, padx=(14, 6), sticky="w"
        )
        self.browse_sort_var = tk.StringVar(value=self._browse_sort_label())
        self.browse_sort = ttk.Combobox(
            toolbar, state="readonly", width=11,
            values=tuple(self._browse_sort_map()),
            textvariable=self.browse_sort_var,
        )
        self.browse_sort.grid(row=0, column=3, sticky="w")
        self.browse_sort.bind("<<ComboboxSelected>>", self._on_browse_sort_change)
        tk_style.tooltip(self.browse_sort, "Sort the game list. Missing values always sort last.")
        self.browse_dir_btn = ttk.Button(
            toolbar, text=self._browse_direction_label(), width=6,
            command=self._toggle_browse_direction,
        )
        self.browse_dir_btn.grid(row=0, column=4, padx=(6, 0))
        tk_style.tooltip(self.browse_dir_btn, "Reverse the sort direction")

        ttk.Separator(tab).pack(fill="x")

        pane = ttk.Panedwindow(tab, orient="horizontal")
        pane.pack(fill="both", expand=True)
        left = ttk.Frame(pane, style="Surface.TFrame")
        right = ttk.Frame(pane, style="Surface.TFrame")
        pane.add(left, weight=3)
        pane.add(right, weight=7)

        head = ttk.Frame(left, style="Surface.TFrame", padding=(14, 12, 14, 6))
        head.pack(fill="x")
        ttk.Label(head, text="Collection", style="Heading.TLabel").pack(anchor="w")
        self.browse_count_var = tk.StringVar(value="Loading…")
        ttk.Label(head, textvariable=self.browse_count_var, style="Small.TLabel").pack(
            anchor="w", pady=(2, 0)
        )

        tree_frame = ttk.Frame(left, style="Surface.TFrame", padding=(8, 0, 8, 6))
        tree_frame.pack(fill="both", expand=True)
        self.game_tree = ttk.Treeview(
            tree_frame,
            columns=("title", "year", "size", "downloads"),
            show="headings",
            selectmode="browse",
        )
        self.game_tree.heading("title", text="Title", anchor="w")
        self.game_tree.heading("year", text="Year", anchor="w")
        self.game_tree.heading("size", text="Size", anchor="e")
        self.game_tree.heading("downloads", text="Downloads", anchor="e")
        self.game_tree.column("title", width=self._w(220), minwidth=self._w(140), stretch=True)
        self.game_tree.column("year", width=self._w(54), minwidth=self._w(44), stretch=False, anchor="w")
        self.game_tree.column("size", width=self._w(84), minwidth=self._w(64), stretch=False, anchor="e")
        self.game_tree.column("downloads", width=self._w(80), minwidth=self._w(62), stretch=False, anchor="e")
        self.game_tree.tag_configure("odd", background=tk_style.STRIPE)
        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.game_tree.yview)
        self.game_tree.configure(yscrollcommand=scroll.set)
        self.game_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.game_tree.bind("<<TreeviewSelect>>", self._on_game_selected)

        self.browse_footer_var = tk.StringVar(value="Loading collection…")
        self.browse_footer = ttk.Label(
            left, textvariable=self.browse_footer_var, style="Small.TLabel",
            padding=(14, 4, 14, 10), wraplength=self._w(360), justify="left",
        )
        self.browse_footer.pack(fill="x")

        # Message area (empty, loading, or error) and the real details content.
        self.browse_message_frame = ttk.Frame(right, style="Surface.TFrame")
        self.browse_message_label = ttk.Label(
            self.browse_message_frame,
            text="Select a game to see its details.",
            style="EmptyText.TLabel",
            anchor="center",
            justify="center",
            wraplength=self._w(440),
        )
        self.browse_message_label.pack(fill="both", expand=True, padx=24, pady=24)
        self.browse_retry_button = ttk.Button(
            self.browse_message_frame, text="Retry details", command=self._retry_selected_item
        )
        self.browse_message_frame.pack(fill="both", expand=True)

        self.browse_content = ttk.Frame(right, style="Surface.TFrame", padding=(18, 16, 18, 14))
        self.browse_content.columnconfigure(1, weight=1)
        self.browse_content.rowconfigure(3, weight=1)

        self.poster_canvas = tk.Canvas(
            self.browse_content,
            width=self._w(184),
            height=self._w(256),
            background=tk_style.SURFACE_ALT,
            highlightthickness=1,
            highlightbackground=tk_style.BORDER,
            bd=0,
        )
        self.poster_canvas.grid(row=0, column=0, rowspan=2, sticky="n", padx=(0, 18), pady=(0, 12))
        self._draw_cover_placeholder(self.poster_canvas, "No cover preview available")

        self.shot_frame = ttk.Frame(self.browse_content, style="Surface.TFrame")
        self.shot_frame.grid(row=2, column=0, sticky="nw", padx=(0, 18), pady=(0, 10))
        self.shot_frame.grid_remove()

        info = ttk.Frame(self.browse_content, style="Surface.TFrame")
        info.grid(row=0, column=1, sticky="new")
        info.columnconfigure(0, weight=1)
        self.item_title_var = tk.StringVar(value="")
        info_title = ttk.Label(
            info, textvariable=self.item_title_var, style="Title.TLabel",
            wraplength=self._w(520), justify="left", anchor="w",
        )
        info_title.grid(row=0, column=0, sticky="ew")
        self._follow_width(info_title, info, 12)
        self.item_meta_var = tk.StringVar(value="")
        info_meta = ttk.Label(
            info, textvariable=self.item_meta_var, style="Meta.TLabel",
            wraplength=self._w(520), justify="left", anchor="w",
        )
        info_meta.grid(row=1, column=0, sticky="ew", pady=(5, 0))
        self._follow_width(info_meta, info, 12)
        links = ttk.Frame(info, style="Surface.TFrame")
        links.grid(row=2, column=0, sticky="w", pady=(10, 0))
        self.item_license_label = ttk.Label(
            links, text="License terms", style="Link.TLabel", cursor="hand2"
        )
        self.item_license_label.pack(side="left", padx=(0, 18))
        self.item_license_label.bind("<Button-1>", lambda _e: self._open_item_license())
        self.item_archive_label = ttk.Label(
            links, text="View on archive.org", style="Link.TLabel", cursor="hand2"
        )
        self.item_archive_label.pack(side="left")
        self.item_archive_label.bind("<Button-1>", lambda _e: self._open_archive_page())
        self.item_restricted_label = ttk.Label(
            info,
            text="This item is restricted and cannot be downloaded here.",
            style="Error.TLabel",
            wraplength=self._w(500),
            justify="left",
        )
        self.item_restricted_label.grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.item_restricted_label.grid_remove()

        desc_head = ttk.Frame(self.browse_content, style="Surface.TFrame")
        desc_head.grid(row=1, column=1, sticky="new")
        ttk.Label(desc_head, text="Description", style="Caption.TLabel").pack(anchor="w")
        desc_border = tk.Frame(self.browse_content, background=tk_style.BORDER, bd=0)
        desc_border.grid(row=2, column=1, sticky="new", pady=(5, 0))
        desc_inner = tk.Frame(desc_border, background=tk_style.SURFACE, bd=0)
        desc_inner.pack(fill="both", expand=True, padx=1, pady=1)
        self.item_description = tk.Text(
            desc_inner,
            height=7,
            wrap="word",
            bd=0,
            relief="flat",
            background=tk_style.SURFACE,
            foreground=tk_style.INK,
            insertbackground=tk_style.INK,
            padx=10,
            pady=8,
            spacing1=1,
            spacing3=3,
            font=self.fonts.base,
            state="disabled",
            takefocus=True,
        )
        desc_scroll = ttk.Scrollbar(desc_inner, orient="vertical", command=self.item_description.yview)
        self.item_description.configure(yscrollcommand=desc_scroll.set)
        self.item_description.pack(side="left", fill="both", expand=True)
        desc_scroll.pack(side="right", fill="y")
        self.item_description.tag_configure("muted", foreground=tk_style.MUTED)

        files_head = ttk.Frame(self.browse_content, style="Surface.TFrame")
        files_head.grid(row=3, column=0, columnspan=2, sticky="new", pady=(12, 0))
        ttk.Label(files_head, text="Files", style="Caption.TLabel").pack(side="left")
        self.item_files_count_var = tk.StringVar(value="")
        ttk.Label(files_head, textvariable=self.item_files_count_var,
                  style="Small.TLabel").pack(side="left", padx=(10, 0))

        files_frame = ttk.Frame(self.browse_content, style="Surface.TFrame")
        files_frame.grid(row=4, column=0, columnspan=2, sticky="nsew", pady=(5, 0))
        files_border = tk.Frame(files_frame, background=tk_style.BORDER, bd=0)
        files_border.pack(fill="both", expand=True)
        files_inner = tk.Frame(files_border, background=tk_style.SURFACE, bd=0)
        files_inner.pack(fill="both", expand=True, padx=1, pady=1)
        self.file_tree = ttk.Treeview(
            files_inner,
            columns=("name", "type", "size"),
            show="headings",
            selectmode="extended",
            height=6,
        )
        self.file_tree.heading("name", text="Name", anchor="w")
        self.file_tree.heading("type", text="Type", anchor="w")
        self.file_tree.heading("size", text="Size", anchor="e")
        self.file_tree.column("name", width=self._w(300), minwidth=self._w(160), stretch=True)
        self.file_tree.column("type", width=self._w(150), minwidth=self._w(100), stretch=False)
        self.file_tree.column("size", width=self._w(84), minwidth=self._w(64), stretch=False, anchor="e")
        self.file_tree.tag_configure("odd", background=tk_style.STRIPE)
        self.file_tree.tag_configure("private", foreground=tk_style.FAINT)
        file_scroll = ttk.Scrollbar(files_inner, orient="vertical", command=self.file_tree.yview)
        self.file_tree.configure(yscrollcommand=file_scroll.set)
        self.file_tree.pack(side="left", fill="both", expand=True)
        file_scroll.pack(side="right", fill="y")
        self.file_tree.bind("<<TreeviewSelect>>", self._on_file_selection)
        self.file_tree.bind("<Return>", lambda _e: self._download_selected())
        self.browse_content.rowconfigure(4, weight=1)

        actions = ttk.Frame(self.browse_content, style="Surface.TFrame")
        actions.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        actions.columnconfigure(0, weight=1)
        self.file_selection_var = tk.StringVar(value="No files selected")
        ttk.Label(actions, textvariable=self.file_selection_var,
                  style="Small.TLabel").grid(row=0, column=0, sticky="w")
        self.download_button = ttk.Button(
            actions, text="Download selected", style="Accent.TButton",
            command=self._download_selected,
        )
        self.download_button.grid(row=0, column=1, padx=(10, 0))
        tk_style.tooltip(
            self.download_button,
            "Add the selected files to Downloads. Existing files are never overwritten.",
        )
        archive_button = ttk.Button(
            actions, text="Open archive.org page", command=self._open_archive_page
        )
        archive_button.grid(row=0, column=2, padx=(8, 0))

        self.browse_content.bind("<Configure>", self._on_browse_details_resize, add="+")

    # -- shared helpers ----------------------------------------------------

    def _add_placeholder(
        self, entry: ttk.Entry, variable: tk.StringVar, text: str
    ) -> None:
        hint = ttk.Label(entry.master, text=text, style="Hint.TLabel")
        hint.place(in_=entry, x=10, rely=0.5, anchor="w")
        hint.bind("<Button-1>", lambda _e: entry.focus_set())

        def refresh(*_args: Any) -> None:
            try:
                if variable.get():
                    hint.place_forget()
                else:
                    hint.place(in_=entry, x=10, rely=0.5, anchor="w")
                    hint.lift()
            except tk.TclError:
                pass

        entry.bind("<FocusIn>", lambda _e: hint.place_forget(), add="+")
        entry.bind("<FocusOut>", lambda _e: refresh(), add="+")
        variable.trace_add("write", refresh)
        self.root.after(10, refresh)

    def _follow_width(self, label: ttk.Label, container: tk.Misc, padding: int) -> None:
        def on_configure(event: tk.Event) -> None:
            try:
                label.configure(wraplength=max(self._w(180), event.width - padding))
            except tk.TclError:
                pass

        container.bind("<Configure>", on_configure, add="+")

    def _draw_cover_placeholder(self, canvas: tk.Canvas, text: str) -> None:
        canvas.delete("all")
        width = int(canvas.cget("width"))
        height = int(canvas.cget("height"))
        canvas.create_text(
            width // 2,
            height // 2,
            text=text,
            fill=tk_style.MUTED,
            font=self.fonts.small,
            justify="center",
            width=max(80, width - 22),
            anchor="center",
        )

    def _post(self, callback: Callable[[], None]) -> None:
        self._ui_queue.put(callback)

    def _run_async(
        self,
        work: Callable[[], Any],
        on_done: Callable[[Any], None] | None = None,
        on_error: Callable[[BaseException], None] | None = None,
    ) -> None:
        def runner() -> None:
            try:
                result = work()
            except BaseException as exc:  # keep the UI thread alive
                if on_error is not None:
                    self._post(lambda error=exc: on_error(error))
                else:
                    self._post(lambda error=exc: self._async_failed(error))
                return
            if on_done is not None:
                self._post(lambda value=result: on_done(value))

        threading.Thread(target=runner, name="lgl-ui-worker", daemon=True).start()

    def _async_failed(self, exc: BaseException) -> None:
        traceback.print_exception(type(exc), exc, exc.__traceback__)
        self._status(f"Something went wrong: {_error_text(exc)}", error=True)

    def _drain_ui(self) -> None:
        if self._closing:
            return
        try:
            for _ in range(200):
                try:
                    callback = self._ui_queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    callback()
                except Exception as exc:  # a UI callback must not stop the loop
                    traceback.print_exception(type(exc), exc, exc.__traceback__)
                    self._status(f"Something went wrong: {_error_text(exc)}", error=True)
        finally:
            if self._downloads_dirty.is_set() and not self._closing:
                self._downloads_dirty.clear()
                self._refresh_downloads()
            if not self._closing:
                self.root.after(100, self._drain_ui)

    def _status(self, text: str, error: bool = False) -> None:
        if self._closing:
            return
        if self._status_after is not None:
            try:
                self.root.after_cancel(self._status_after)
            except tk.TclError:
                pass
            self._status_after = None
        self.status_var.set(short(text, 180))
        self.status_label.configure(style="StatusError.TLabel" if error else "Status.TLabel")

        def clear() -> None:
            if not self._closing:
                self.status_var.set("Ready")
                self.status_label.configure(style="Status.TLabel")

        self._status_after = self.root.after(12000 if error else 8000, clear)

    def _startup(self) -> None:
        self.browse_search.focus_set()
        if self._startup_warning:
            self._status(self._startup_warning, error=True)
        self._load_collection(False)
        if bool(self.settings.get("rescan_on_startup", True)):
            self._rescan_library(startup=True)

    def _focus_tab(self, index: int) -> None:
        try:
            self.notebook.select(index)
        except tk.TclError:
            return
        if index == 0:
            self.browse_search.focus_set()
        elif index == 2:
            self.lib_search.focus_set()

    def _on_tab_changed(self, _event: tk.Event | None = None) -> None:
        try:
            index = self.notebook.index(self.notebook.select())
        except tk.TclError:
            return
        if index == 1:
            self._refresh_downloads()
            self._refresh_torrents()
        elif index == 2:
            self._refresh_library_table()

    # -- browse: collection list -------------------------------------------

    def _schedule_browse_filter(self, *_args: Any) -> None:
        if getattr(self, "_browse_filter_after", None) is not None:
            try:
                self.root.after_cancel(self._browse_filter_after)
            except tk.TclError:
                pass
        self._browse_filter_after = self.root.after(140, self._apply_browse_filter)

    def _browse_sort_map(self) -> dict[str, str]:
        return {
            "Title": "title",
            "Year": "year",
            "Size": "size",
            "Downloads": "downloads",
            "Download": "downloadable",
        }

    def _browse_sort_label(self) -> str:
        key = self.settings.browse_sort
        for label, value in self._browse_sort_map().items():
            if value == key:
                return label
        return "Title"

    def _browse_direction_label(self) -> str:
        return "Z → A" if self.settings.browse_descending else "A → Z"

    def _on_browse_sort_change(self, _event: tk.Event | None = None) -> None:
        key = self._browse_sort_map().get(self.browse_sort_var.get(), "title")
        try:
            self.settings.set_browse_sort(key)
        except OSError:
            pass
        self._apply_browse_filter()

    def _toggle_browse_direction(self) -> None:
        descending = not self.settings.browse_descending
        try:
            self.settings.set_browse_descending(descending)
        except OSError:
            pass
        self.browse_dir_btn.configure(text=self._browse_direction_label())
        self._apply_browse_filter()

    def _sorted_games(self, games: Sequence[GameSummary]) -> list[GameSummary]:
        """Sort by the chosen field, keeping empty values last both ways."""
        key = self.settings.browse_sort
        descending = self.settings.browse_descending
        if key == "downloadable":
            ordered = sorted(
                games,
                key=lambda g: (
                    DOWNLOAD_STATE_RANK.get(g.download_state, 1),
                    g.title.casefold(),
                    g.identifier,
                ),
            )
            if descending:
                ordered.reverse()
            return ordered
        if key == "title":
            ordered = sorted(games, key=lambda g: (g.title.casefold(), g.identifier))
            if descending:
                ordered.reverse()
            return ordered

        def value(game: GameSummary) -> float | None:
            if key == "year":
                try:
                    return float(int(str(game.year).strip()[:4]))
                except (TypeError, ValueError):
                    return None
            if key == "size":
                return float(game.item_size) if game.item_size and game.item_size > 0 else None
            return float(game.downloads) if game.downloads and game.downloads > 0 else None

        present: list[tuple[float, GameSummary]] = []
        missing: list[GameSummary] = []
        for game in games:
            score = value(game)
            if score is None:
                missing.append(game)
            else:
                present.append((score, game))
        present.sort(
            key=lambda pair: (pair[0], pair[1].title.casefold(), pair[1].identifier),
            reverse=descending,
        )
        missing.sort(key=lambda g: (g.title.casefold(), g.identifier))
        return [game for _score, game in present] + missing

    def _apply_browse_filter(self) -> None:
        self._browse_filter_after = None
        query = self.browse_search_var.get().strip()
        if not self._games:
            self._visible_games = []
            self._render_games([])
            if self._collection_loading:
                self.browse_footer_var.set("Loading collection…")
            return
        matched = ArchiveClient.search(self._games, query) if query else list(self._games)
        self._visible_games = self._sorted_games(matched)
        self._render_games(self._visible_games)
        if query and not self._visible_games:
            self.browse_footer_var.set("No games match this search. Press Escape to clear it.")
        elif query:
            self.browse_footer_var.set(
                f"{len(self._visible_games)} of {len(self._games)} games match “{short(query, 40)}”."
            )
        elif self.settings.browse_sort != "title" or self.settings.browse_descending:
            self.browse_footer_var.set(
                f"{len(self._visible_games)} {_plural(len(self._visible_games), 'game')} · "
                f"sorted by {self.settings.browse_sort} "
                f"({'descending' if self.settings.browse_descending else 'ascending'})"
            )
        else:
            self.browse_footer_var.set(
                f"{len(self._visible_games)} {_plural(len(self._visible_games), 'game')}"
            )

    def _clear_browse_search(self) -> None:
        if self.browse_search_var.get():
            self.browse_search_var.set("")
        self._apply_browse_filter()
        self.browse_search.focus_set()

    def _load_collection(self, force: bool = False) -> None:
        if self._collection_loading:
            return
        self._collection_loading = True
        self.browse_footer.configure(style="Small.TLabel")
        self.browse_footer_var.set("Refreshing the collection…" if force else "Loading collection…")
        self.browse_count_var.set("Loading…")
        if not self._games:
            self._show_browse_message("Loading the collection from archive.org…")

        def work() -> CollectionResult:
            return self.client.fetch_collection(force=force)

        self._run_async(work, self._on_collection_loaded, self._on_collection_failed)

    def _on_collection_loaded(self, result: CollectionResult) -> None:
        self._collection_loading = False
        self._games = list(result.games)
        self._games_by_id = {game.identifier: game for game in self._games}
        if result.warning:
            self._status(result.warning, error=True)
        if not self._games:
            self.browse_count_var.set("No games")
            self.browse_footer_var.set("The collection list is empty.")
            self._show_browse_message(
                "The collection is empty.\n\nPress Refresh to try again later."
            )
            self._render_games([])
            return
        self._apply_browse_filter()
        source = "saved copy" if result.from_cache else "archive.org"
        self.browse_footer_var.set(
            f"{len(self._games)} {_plural(len(self._games), 'game')} · {source}"
        )
        self.browse_count_var.set(f"{len(self._games)} {_plural(len(self._games), 'game')}")

    def _on_collection_failed(self, exc: BaseException) -> None:
        self._collection_loading = False
        message = _error_text(exc)
        self.browse_footer.configure(style="Error.TLabel")
        self.browse_footer_var.set(
            "Could not load the collection. Check your connection, then press Refresh."
        )
        self.browse_count_var.set("Unavailable")
        self._status(f"Could not load the collection: {message}", error=True)
        if not self._games:
            self._show_browse_message(
                "The collection could not be loaded.\n\n"
                f"{message}\n\n"
                "Check your network connection, then press Refresh."
            )

    def _render_games(self, games: Sequence[GameSummary]) -> None:
        self._game_render_token += 1
        token = self._game_render_token
        try:
            self.game_tree.delete(*self.game_tree.get_children())
        except tk.TclError:
            return
        if not games:
            self.browse_count_var.set("0 games")
            return
        self.browse_count_var.set(f"{len(games)} {_plural(len(games), 'game')}")
        self._insert_game_chunk(token, list(games), 0)

    def _insert_game_chunk(
        self, token: int, games: list[GameSummary], start: int
    ) -> None:
        if token != self._game_render_token or self._closing:
            return
        end = min(len(games), start + 160)
        try:
            for index in range(start, end):
                game = games[index]
                self.game_tree.insert(
                    "",
                    "end",
                    iid=game.identifier,
                    values=(
                        game.title,
                        game.year or "—",
                        human_size(game.item_size),
                        (
                            DOWNLOAD_STATE_LABELS.get(game.download_state, "—")
                            if game.is_catalog and game.download_state
                            else (f"{game.downloads:,}" if game.downloads else "—")
                        ),
                    ),
                    tags=("odd",) if index % 2 else (),
                )
        except tk.TclError:
            return
        if end < len(games):
            self.root.after_idle(lambda: self._insert_game_chunk(token, games, end))
            return

        desired = self._selected_identifier
        candidates = [g.identifier for g in games]
        if desired not in candidates:
            desired = candidates[0]
        if desired:
            self._select_game(desired)

    def _select_game(self, identifier: str) -> None:
        try:
            children = self.game_tree.get_children()
            if identifier not in children:
                return
            self.game_tree.selection_set(identifier)
            self.game_tree.focus(identifier)
            self.game_tree.see(identifier)
        except tk.TclError:
            return

    def _on_game_selected(self, _event: tk.Event | None = None) -> None:
        selection = self.game_tree.selection()
        if not selection:
            return
        identifier = selection[0]
        if identifier == self._selected_identifier and (
            self._current_item is not None
            or identifier == getattr(self, "_item_loading_identifier", None)
        ):
            return
        self._selected_identifier = identifier
        game = self._games_by_id.get(identifier)
        if game is not None:
            self._load_game_details(identifier)

    # -- browse: details pane ----------------------------------------------

    def _retry_selected_item(self) -> None:
        if self._selected_identifier:
            self._load_game_details(self._selected_identifier, force=True)

    def _show_browse_message(self, text: str, retry: bool = False) -> None:
        self.browse_content.pack_forget()
        self.browse_message_label.configure(text=text)
        self.browse_message_frame.pack(fill="both", expand=True)
        if retry:
            self.browse_retry_button.pack(pady=(0, 24))
        else:
            self.browse_retry_button.pack_forget()

    def _show_browse_content(self) -> None:
        self.browse_message_frame.pack_forget()
        self.browse_content.pack(fill="both", expand=True)

    def _load_game_details(self, identifier: str, force: bool = False) -> None:
        game = self._games_by_id.get(identifier)
        if game is None:
            return
        if (
            not force
            and identifier == self._selected_identifier
            and self._current_item is not None
        ):
            return
        if identifier == getattr(self, "_item_loading_identifier", None) and not force:
            return
        self._selected_identifier = identifier
        self._item_loading_identifier = identifier
        self._item_token += 1
        token = self._item_token
        self._current_item = None
        self._clear_browse_media()
        self._show_browse_message(f"Loading details for {game.title}…")
        self._status(f"Loading {game.title}…")

        def work() -> ArchiveItem:
            return self.client.fetch_item(identifier, force=force)

        self._run_async(
            work,
            lambda item: self._on_item_loaded(token, identifier, item),
            lambda exc: self._on_item_failed(token, identifier, exc),
        )

    def _on_item_loaded(self, token: int, identifier: str, item: ArchiveItem) -> None:
        if token != self._item_token or self._closing:
            return
        self._item_loading_identifier = None
        self._current_item = item
        self.item_title_var.set(item.title)
        bits: list[str] = []
        if item.creator:
            bits.append(item.creator)
        if item.date:
            bits.append(item.date)
        elif item.year:
            bits.append(item.year)
        if item.item_size:
            bits.append(human_size(item.item_size))
        bits.append(item.identifier)
        self.item_meta_var.set(" · ".join(bits))

        has_license = bool(item.licenseurl and item.licenseurl.startswith(("http://", "https://")))
        if has_license:
            self.item_license_label.pack(side="left", padx=(0, 18))
        else:
            self.item_license_label.pack_forget()
        if item.restricted:
            self.item_restricted_label.grid()
        else:
            self.item_restricted_label.grid_remove()

        self._set_description(item.description)
        self._populate_item_files(item)
        self._update_browse_actions()
        self._show_browse_content()
        self._load_browse_poster(token, item)
        self._load_browse_screenshots(token, item)
        self._status(f"Loaded {item.title}.")

    def _on_item_failed(self, token: int, identifier: str, exc: BaseException) -> None:
        if token != self._item_token or self._closing:
            return
        self._item_loading_identifier = None
        self._current_item = None
        game = self._games_by_id.get(identifier)
        title = game.title if game else identifier
        message = _error_text(exc)
        self._show_browse_message(
            f"Could not load details for {title}.\n\n{message}\n\n"
            "Press Retry details to try again.",
            retry=True,
        )
        self._status(f"Could not load details for {title}: {message}", error=True)

    def _set_description(self, text: str) -> None:
        widget = self.item_description
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        if text:
            widget.insert("1.0", text)
        else:
            widget.insert("1.0", "No description was provided for this item.")
            widget.tag_add("muted", "1.0", "end")
        widget.configure(state="disabled")
        widget.yview_moveto(0.0)

    def _populate_item_files(self, item: ArchiveItem) -> None:
        try:
            self.file_tree.delete(*self.file_tree.get_children())
        except tk.TclError:
            return
        self._item_files = {}
        for index, archive_file in enumerate(item.files):
            iid = str(index)
            self._item_files[iid] = archive_file
            kind = archive_file.kind
            if not archive_file.downloadable:
                kind += " · private"
            tags: tuple[str, ...] = ()
            if not archive_file.downloadable:
                tags = ("private",)
            elif archive_file.auxiliary:
                tags = ("odd",)
            elif index % 2:
                tags = ("odd",)
            self.file_tree.insert(
                "", "end", iid=iid,
                values=(archive_file.name, kind, human_size(archive_file.size)),
                tags=tags,
            )
        count = len(item.files)
        self.item_files_count_var.set(f"{count} {_plural(count, 'file')}")
        self._update_browse_actions()

    def _on_file_selection(self, _event: tk.Event | None = None) -> None:
        self._update_browse_actions()

    def _selected_archive_files(self) -> list[ArchiveFile]:
        item = self._current_item
        if item is None or item.restricted:
            return []
        files: list[ArchiveFile] = []
        for iid in self.file_tree.selection():
            archive_file = self._item_files.get(iid)
            if archive_file is not None and archive_file.downloadable:
                files.append(archive_file)
        return files

    def _update_browse_actions(self) -> None:
        if not hasattr(self, "download_button"):
            return
        item = self._current_item
        files = self._selected_archive_files()
        count = len(files)
        if item is None:
            self.file_selection_var.set("No files selected")
        elif count:
            self.file_selection_var.set(f"{count} {_plural(count, 'file')} selected")
        else:
            self.file_selection_var.set("No files selected")
        enabled = bool(item is not None and not item.restricted and count)
        self.download_button.state(["!disabled"] if enabled else ["disabled"])

    def _download_selected(self) -> None:
        item = self._current_item
        if item is None:
            return
        if item.restricted:
            self._status("This archive item is restricted; downloads are unavailable.", error=True)
            return
        files = self._selected_archive_files()
        if not files:
            self._status("Select one or more downloadable files first.", error=True)
            return
        download_dir = Path(self.download_dir)
        manager = self.manager
        selected = list(files)

        def work() -> tuple[int, list[str], str | None]:
            root = check_download_dir(download_dir)
            added = 0
            errors: list[str] = []
            for archive_file in selected:
                try:
                    manager.add(
                        identifier=item.identifier,
                        filename=archive_file.name,
                        url=item.file_url(archive_file),
                        download_dir=root,
                        total_size=archive_file.size,
                    )
                    added += 1
                except (AppError, ValueError, OSError) as exc:
                    errors.append(f"{archive_file.name}: {_error_text(exc)}")
            manifest_note: str | None = None
            if added:
                try:
                    write_manifest(game_folder(root, item.identifier), item)
                except (OSError, ValueError) as exc:
                    manifest_note = f"Game metadata was not saved: {_error_text(exc)}"
            return added, errors, manifest_note

        def done(result: tuple[int, list[str], str | None]) -> None:
            added, errors, manifest_note = result
            self._refresh_downloads()
            if added and not errors and not manifest_note:
                self._status(f"Added {added} {_plural(added, 'file')} to Downloads.")
            elif added:
                detail = errors[0] if errors else (manifest_note or "")
                self._status(
                    f"Added {added} {_plural(added, 'file')}. "
                    f"{len(errors)} could not be added: {detail}",
                    error=True,
                )
            elif errors:
                self._status(errors[0], error=True)
            else:
                self._status("No downloadable files were selected.", error=True)

        self._run_async(work, done, lambda exc: self._status(
            f"Could not queue the download: {_error_text(exc)}", error=True
        ))

    def _open_archive_page(self) -> None:
        item = self._current_item
        identifier = item.identifier if item else self._selected_identifier
        if not identifier:
            return
        url = item.download_url if item else f"https://archive.org/details/{identifier}"
        if not platform_support.open_url(url):
            self._status("Could not open the archive.org page in a browser.", error=True)

    def _open_item_license(self) -> None:
        item = self._current_item
        if item is None or not item.licenseurl:
            return
        if not platform_support.open_url(item.licenseurl):
            self._status("Could not open the license link in a browser.", error=True)

    # -- browse: poster and screenshots ------------------------------------

    def _on_browse_details_resize(self, event: tk.Event) -> None:
        if self._closing:
            return
        available = max(0, int(event.width) - self._w(36))
        if not available:
            return
        # Roughly 30% of the details width, bounded to 22-38%, capped at 360 px.
        box_w = int(available * 0.30)
        box_w = max(box_w, int(available * 0.22), self._w(140))
        box_w = min(box_w, int(available * 0.38), self._w(360))
        if abs(box_w - self._browse_poster_width) < 4:
            return
        self._browse_poster_width = box_w
        box_h = int(box_w * 1.34)
        try:
            self.poster_canvas.configure(width=box_w, height=box_h)
        except tk.TclError:
            return
        if self._browse_source_image is not None:
            self._render_browse_poster()
        else:
            self._draw_cover_placeholder(self.poster_canvas, "No cover preview available")

    def _fit_image(self, image: Any, box_w: int, box_h: int) -> Any:
        """Scale an image to fit a box without cropping or upscaling."""
        box_w = max(1, int(box_w))
        box_h = max(1, int(box_h))
        iw, ih = image.size
        if iw <= 0 or ih <= 0:
            return image
        scale = min(box_w / iw, box_h / ih, 1.0)
        size = (max(1, int(round(iw * scale))), max(1, int(round(ih * scale))))
        if size == image.size:
            return image
        resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS", 1)
        return image.resize(size, resample)

    def _render_browse_poster(self) -> None:
        image = self._browse_source_image
        if image is None or not PIL_AVAILABLE:
            return
        canvas = self.poster_canvas
        try:
            width = int(canvas.cget("width"))
            height = int(canvas.cget("height"))
            fitted = self._fit_image(image, width - 2, height - 2)
            photo = ImageTk.PhotoImage(fitted)
        except Exception:
            return
        self._browse_photo = photo
        canvas.delete("all")
        canvas.create_image(width // 2, height // 2, image=photo, anchor="center")

    def _clear_browse_media(self) -> None:
        self._browse_source_image = None
        self._browse_photo = None
        self._screenshot_token += 1
        self._screenshots = []
        self._shot_photos = []
        if hasattr(self, "shot_frame"):
            for child in self.shot_frame.winfo_children():
                child.destroy()
            self.shot_frame.grid_remove()

    def _load_browse_poster(self, token: int, item: ArchiveItem) -> None:
        if token != self._item_token:
            return
        self._browse_source_image = None
        if not PIL_AVAILABLE:
            self._draw_cover_placeholder(self.poster_canvas, "No cover preview available")
            return
        self._draw_cover_placeholder(self.poster_canvas, "Loading cover…")
        url = item.poster_url

        def work() -> Any:
            data = self.client.fetch_image(url, item.identifier)
            image = Image.open(io.BytesIO(data))
            image.load()
            return image.convert("RGB")

        self._run_async(
            work,
            lambda image: self._on_poster_loaded(token, image),
            lambda _exc: self._on_poster_failed(token),
        )

    def _on_poster_loaded(self, token: int, image: Any) -> None:
        if token != self._item_token or self._closing or not PIL_AVAILABLE:
            return
        self._browse_source_image = image
        self._render_browse_poster()

    def _on_poster_failed(self, token: int) -> None:
        if token != self._item_token or self._closing:
            return
        self._browse_source_image = None
        self._draw_cover_placeholder(self.poster_canvas, "No cover preview available")

    def _load_browse_screenshots(self, token: int, item: ArchiveItem) -> None:
        self._screenshot_token += 1
        shot_token = self._screenshot_token
        self._screenshots = []
        self._shot_photos = []
        for child in self.shot_frame.winfo_children():
            child.destroy()
        self.shot_frame.grid_remove()
        if not PIL_AVAILABLE:
            return
        files = item.screenshot_files[:4]
        if not files:
            return

        def work() -> list[Any]:
            images: list[Any] = []
            for index, shot in enumerate(files):
                try:
                    data = self.client.fetch_image(
                        item.file_url(shot), f"{item.identifier}-shot-{index}"
                    )
                    image = Image.open(io.BytesIO(data))
                    image.load()
                    images.append(image.convert("RGB"))
                except Exception:
                    continue
            return images

        self._run_async(
            work,
            lambda images: self._on_screenshots_loaded(token, shot_token, images),
            lambda _exc: None,
        )

    def _on_screenshots_loaded(self, token: int, shot_token: int,
                               images: list[Any]) -> None:
        if (
            token != self._item_token
            or shot_token != self._screenshot_token
            or self._closing
            or not images
        ):
            return
        self._screenshots = images
        size = self._w(68)
        for index, image in enumerate(images):
            try:
                fitted = self._fit_image(image, size, size)
                photo = ImageTk.PhotoImage(fitted)
            except Exception:
                continue
            button = tk.Button(
                self.shot_frame, image=photo, bd=0, relief="flat",
                background=tk_style.SURFACE, activebackground=tk_style.SURFACE_ALT,
                highlightthickness=1, highlightbackground=tk_style.BORDER,
                cursor="hand2", padx=0, pady=0,
                command=lambda i=index: self._open_screenshot_viewer(i),
            )
            button.grid(row=0, column=index, padx=(0, 6), pady=(0, 2))
            self._shot_photos.append(photo)
        if self._shot_photos:
            self.shot_frame.grid()

    def _open_screenshot_viewer(self, index: int) -> None:
        if not self._screenshots:
            return
        window = tk.Toplevel(self.root)
        window.title(f"Screenshot {index + 1} of {len(self._screenshots)}")
        window.configure(background=tk_style.BG)
        window.transient(self.root)
        state = {"index": index % len(self._screenshots)}

        canvas = tk.Canvas(
            window, background=tk_style.BG, bd=0, highlightthickness=0,
            width=self._w(760), height=self._w(520),
        )
        canvas.pack(fill="both", expand=True, padx=12, pady=(12, 6))
        photo_ref = {"photo": None}

        def render() -> None:
            if not window.winfo_exists():
                return
            image = self._screenshots[state["index"]]
            try:
                max_w = max(200, window.winfo_width() - 24)
                max_h = max(160, window.winfo_height() - 80)
                fitted = self._fit_image(image, max_w, max_h)
                photo = ImageTk.PhotoImage(fitted)
            except Exception:
                return
            photo_ref["photo"] = photo
            canvas.delete("all")
            canvas.create_image(
                max(1, window.winfo_width() // 2),
                max(1, (window.winfo_height() - 60) // 2),
                image=photo, anchor="center",
            )
            window.title(f"Screenshot {state['index'] + 1} of {len(self._screenshots)}")

        def move(step: int) -> None:
            state["index"] = (state["index"] + step) % len(self._screenshots)
            render()

        buttons = ttk.Frame(window, style="App.TFrame", padding=(12, 0, 12, 10))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="← Previous",
                   command=lambda: move(-1)).pack(side="left")
        ttk.Button(buttons, text="Next →",
                   command=lambda: move(1)).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Close", command=window.destroy).pack(side="right")
        window.bind("<Left>", lambda _e: move(-1))
        window.bind("<Right>", lambda _e: move(1))
        window.bind("<Escape>", lambda _e: window.destroy())
        window.protocol("WM_DELETE_WINDOW", window.destroy)
        window.bind("<Configure>", lambda _e: render(), add="+")
        window.geometry(f"{self._w(820)}x{self._w(600)}")
        window.update_idletasks()
        render()
        window.focus_set()

    # -- downloads: wiring -------------------------------------------------

    def _title_for_identifier(self, identifier: str) -> str:
        game = self._games_by_id.get(identifier)
        if game is not None and game.title:
            return game.title
        entry = self.library.get(identifier)
        if entry is not None and entry.title:
            return entry.title
        return identifier

    def _resolve_task_url(self, snapshot: TaskSnapshot) -> str | None:
        try:
            return build_download_url(snapshot.identifier, snapshot.filename)
        except Exception:
            return None

    def _on_download_change(self) -> None:
        # Called from the downloader worker thread.
        self._downloads_dirty.set()

    # -- downloads tab -----------------------------------------------------

    def _build_downloads_tab(self) -> None:
        tab = self.downloads_tab

        head = ttk.Frame(tab, style="Surface.TFrame", padding=(14, 12, 14, 8))
        head.pack(fill="x")
        ttk.Label(head, text="Downloads", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(
            head,
            text="One download runs at a time. Queued and paused items are kept between sessions.",
            style="Small.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        folder_row = ttk.Frame(head, style="Surface.TFrame")
        folder_row.pack(fill="x", pady=(10, 0))
        folder_row.columnconfigure(1, weight=1)
        ttk.Label(folder_row, text="Save to", style="FieldLabel.TLabel").grid(
            row=0, column=0, padx=(0, 10), sticky="w"
        )
        self.dl_folder_var = tk.StringVar(value=_shorten_home(self.download_dir))
        self.dl_folder_label = ttk.Label(
            folder_row, textvariable=self.dl_folder_var, style="Small.TLabel",
            anchor="w",
        )
        self.dl_folder_label.grid(row=0, column=1, sticky="ew")
        choose = ttk.Button(folder_row, text="Choose folder…", command=self._choose_download_dir)
        choose.grid(row=0, column=2, padx=(10, 0))
        open_btn = ttk.Button(folder_row, text="Open folder", command=self._open_download_dir)
        open_btn.grid(row=0, column=3, padx=(8, 0))
        tk_style.tooltip(choose, "Choose where downloaded game folders are stored")
        tk_style.tooltip(open_btn, "Open the download folder in File Explorer")

        ttk.Separator(tab).pack(fill="x")

        if self._restored:
            banner = tk.Frame(
                tab, background=tk_style.WARNING_BG, bd=0,
                highlightthickness=1, highlightbackground=tk_style.WARNING_BORDER,
            )
            banner.pack(fill="x", padx=0, pady=(10, 0))
            inner = ttk.Frame(banner, style="Banner.TFrame", padding=(14, 9))
            inner.pack(fill="x")
            text_box = ttk.Frame(inner, style="Banner.TFrame")
            text_box.pack(side="left", fill="x", expand=True)
            ttk.Label(
                text_box, text="Unfinished downloads restored", style="WarningBold.TLabel"
            ).pack(anchor="w")
            ttk.Label(
                text_box,
                text="Nothing starts automatically. Use Resume all or select a download and press Resume.",
                style="Warning.TLabel",
                wraplength=self._w(680),
                justify="left",
            ).pack(anchor="w", pady=(1, 0))
            ttk.Button(inner, text="Dismiss", style="Quiet.TButton",
                       command=self._dismiss_restored).pack(side="right", padx=(12, 0))
            self.dl_banner = banner
        else:
            self.dl_banner = None

        controls = ttk.Frame(tab, style="Surface.TFrame", padding=(14, 8))
        controls.pack(fill="x")
        self.dl_pause_btn = ttk.Button(controls, text="Pause", command=self._pause_selected)
        self.dl_resume_btn = ttk.Button(controls, text="Resume", command=self._resume_selected)
        self.dl_cancel_btn = ttk.Button(controls, text="Cancel", command=self._cancel_selected)
        self.dl_retry_btn = ttk.Button(controls, text="Retry", command=self._retry_selected)
        self.dl_remove_btn = ttk.Button(controls, text="Remove", command=self._remove_selected)
        self.dl_clear_btn = ttk.Button(controls, text="Clear finished", command=self._clear_finished)
        self.dl_resume_all_btn = ttk.Button(controls, text="Resume all", command=self._resume_all)
        for button in (
            self.dl_pause_btn, self.dl_resume_btn, self.dl_cancel_btn,
            self.dl_retry_btn, self.dl_remove_btn, self.dl_clear_btn,
            self.dl_resume_all_btn,
        ):
            button.pack(side="left", padx=(0, 6))
        tk_style.tooltip(self.dl_pause_btn, "Pause the selected download and keep its partial file")
        tk_style.tooltip(self.dl_resume_btn, "Continue the selected paused download")
        tk_style.tooltip(self.dl_cancel_btn, "Stop the selected download and keep its partial file")
        tk_style.tooltip(self.dl_retry_btn, "Retry the selected failed or cancelled download")
        tk_style.tooltip(self.dl_remove_btn, "Remove a finished task from this list. Files are kept.")
        tk_style.tooltip(self.dl_clear_btn, "Remove all finished tasks. Files are kept.")
        tk_style.tooltip(self.dl_resume_all_btn, "Resume every paused download")

        table = ttk.Frame(tab, style="Surface.TFrame", padding=(14, 0, 14, 0))
        table.pack(fill="both", expand=True)
        tree_border = tk.Frame(table, background=tk_style.BORDER, bd=0)
        tree_border.pack(fill="both", expand=True)
        tree_inner = tk.Frame(tree_border, background=tk_style.SURFACE, bd=0)
        tree_inner.pack(fill="both", expand=True, padx=1, pady=1)
        columns = ("file", "game", "status", "progress", "size", "speed", "eta", "error")
        self.dl_tree = ttk.Treeview(
            tree_inner, columns=columns, show="headings", selectmode="browse"
        )
        headings = {
            "file": ("File", "w", 220),
            "game": ("Game", "w", 150),
            "status": ("Status", "w", 82),
            "progress": ("Progress", "e", 70),
            "size": ("Downloaded", "e", 140),
            "speed": ("Speed", "e", 82),
            "eta": ("ETA", "e", 76),
            "error": ("Error", "w", 220),
        }
        for key, (label, anchor, width) in headings.items():
            self.dl_tree.heading(key, text=label, anchor=anchor)
            stretch = key in ("file", "game", "error")
            self.dl_tree.column(
                key,
                width=self._w(width),
                minwidth=self._w(50),
                stretch=stretch,
                anchor=anchor,
            )
        self.dl_tree.tag_configure("odd", background=tk_style.STRIPE)
        self.dl_tree.tag_configure("failed", foreground=tk_style.DANGER)
        self.dl_tree.tag_configure("completed", foreground=tk_style.FAINT)
        self.dl_tree.tag_configure("paused", foreground=tk_style.MUTED)
        self.dl_tree.tag_configure("running", font=self.fonts.base_bold)
        dl_scroll = ttk.Scrollbar(tree_inner, orient="vertical", command=self.dl_tree.yview)
        self.dl_tree.configure(yscrollcommand=dl_scroll.set)
        self.dl_tree.pack(side="left", fill="both", expand=True)
        dl_scroll.pack(side="right", fill="y")
        self.dl_tree.bind("<<TreeviewSelect>>", self._on_dl_select)

        detail = ttk.Frame(tab, style="Surface.TFrame", padding=(14, 8, 14, 4))
        detail.pack(fill="x")
        detail.columnconfigure(0, weight=1)
        self.dl_selected_var = tk.StringVar(value="No download selected")
        ttk.Label(detail, textvariable=self.dl_selected_var,
                  style="SmallBold.TLabel").grid(row=0, column=0, sticky="w")
        self.dl_detail_var = tk.StringVar(value="")
        ttk.Label(detail, textvariable=self.dl_detail_var, style="Small.TLabel",
                  anchor="w", justify="left").grid(row=1, column=0, sticky="ew", pady=(1, 4))
        self.dl_progress = ttk.Progressbar(detail, mode="determinate", maximum=100, value=0)
        self.dl_progress.grid(row=2, column=0, sticky="ew")
        self.dl_error_var = tk.StringVar(value="")
        self.dl_error_label = ttk.Label(
            detail, textvariable=self.dl_error_var, style="Error.TLabel",
            justify="left", wraplength=self._w(900),
        )
        self.dl_error_label.grid(row=3, column=0, sticky="ew", pady=(4, 0))
        self._follow_width(self.dl_error_label, detail, 12)

        torrents = ttk.Frame(tab, style="Surface.TFrame", padding=(14, 6, 14, 4))
        torrents.pack(fill="x")
        torrent_head = ttk.Frame(torrents, style="Surface.TFrame")
        torrent_head.pack(fill="x")
        ttk.Label(torrent_head, text="Torrents", style="SmallBold.TLabel").pack(
            side="left"
        )
        self.torrent_engine_var = tk.StringVar(value="Checking torrent engine…")
        ttk.Label(
            torrent_head, textvariable=self.torrent_engine_var, style="Small.TLabel"
        ).pack(side="right")

        torrent_controls = ttk.Frame(torrents, style="Surface.TFrame")
        torrent_controls.pack(fill="x", pady=(4, 4))
        self.torrent_add_btn = ttk.Button(
            torrent_controls, text="Add magnet…", command=self._add_magnet_dialog
        )
        self.torrent_start_btn = ttk.Button(
            torrent_controls, text="Start/Resume", command=self._torrent_start_selected
        )
        self.torrent_pause_btn = ttk.Button(
            torrent_controls, text="Pause", command=self._torrent_pause_selected
        )
        self.torrent_remove_btn = ttk.Button(
            torrent_controls, text="Remove", command=self._torrent_remove_selected
        )
        self.torrent_clear_btn = ttk.Button(
            torrent_controls, text="Clear finished",
            command=self._torrent_clear_finished,
        )
        for button in (
            self.torrent_add_btn, self.torrent_start_btn, self.torrent_pause_btn,
            self.torrent_remove_btn, self.torrent_clear_btn,
        ):
            button.pack(side="left", padx=(0, 6))
        tk_style.tooltip(
            self.torrent_add_btn,
            "Queue a magnet link. Nothing starts until you press Start/Resume.",
        )
        tk_style.tooltip(
            self.torrent_start_btn,
            "Start or resume the selected torrent. Seeding is disabled.",
        )
        tk_style.tooltip(self.torrent_pause_btn, "Pause the selected torrent")
        tk_style.tooltip(
            self.torrent_remove_btn,
            "Remove a torrent from this queue. Downloaded files stay on disk.",
        )
        tk_style.tooltip(
            self.torrent_clear_btn, "Remove all finished torrents. Files are kept."
        )

        torrent_table = tk.Frame(torrents, background=tk_style.BORDER, bd=0)
        torrent_table.pack(fill="x")
        torrent_inner = tk.Frame(torrent_table, background=tk_style.SURFACE, bd=0)
        torrent_inner.pack(fill="both", expand=True, padx=1, pady=1)
        torrent_columns = ("title", "status", "progress", "speed", "peers", "error")
        self.torrent_tree = ttk.Treeview(
            torrent_inner,
            columns=torrent_columns,
            show="headings",
            selectmode="browse",
            height=5,
        )
        torrent_headings = {
            "title": ("Torrent", "w", 260),
            "status": ("Status", "w", 104),
            "progress": ("Progress", "e", 76),
            "speed": ("Speed", "e", 82),
            "peers": ("Peers", "e", 58),
            "error": ("Error", "w", 240),
        }
        for key, (label, anchor, width) in torrent_headings.items():
            self.torrent_tree.heading(key, text=label, anchor=anchor)
            self.torrent_tree.column(
                key,
                width=self._w(width),
                minwidth=self._w(46),
                stretch=key in ("title", "error"),
                anchor=anchor,
            )
        self.torrent_tree.tag_configure("odd", background=tk_style.STRIPE)
        self.torrent_tree.tag_configure("failed", foreground=tk_style.DANGER)
        self.torrent_tree.tag_configure("completed", foreground=tk_style.FAINT)
        self.torrent_tree.tag_configure("paused", foreground=tk_style.MUTED)
        self.torrent_tree.tag_configure("queued", foreground=tk_style.MUTED)
        self.torrent_tree.tag_configure("running", font=self.fonts.base_bold)
        torrent_scroll = ttk.Scrollbar(
            torrent_inner, orient="vertical", command=self.torrent_tree.yview
        )
        self.torrent_tree.configure(yscrollcommand=torrent_scroll.set)
        self.torrent_tree.pack(side="left", fill="both", expand=True)
        torrent_scroll.pack(side="right", fill="y")
        self.torrent_tree.bind("<<TreeviewSelect>>", self._on_torrent_select)

        self.dl_status_var = tk.StringVar(value="")
        ttk.Label(tab, textvariable=self.dl_status_var, style="Small.TLabel",
                  padding=(14, 2, 14, 10), wraplength=self._w(900),
                  justify="left").pack(fill="x")
        self._update_torrent_buttons()

    def _on_dl_select(self, _event: tk.Event | None = None) -> None:
        if self._dl_updating:
            return
        selection = self.dl_tree.selection()
        self._selected_task_id = selection[0] if selection else None
        self._update_download_detail()

    def _selected_download(self) -> TaskSnapshot | None:
        if not self._selected_task_id:
            return None
        return self.manager.get(self._selected_task_id)

    def _refresh_downloads(self) -> None:
        if self._closing or not hasattr(self, "dl_tree"):
            return
        snapshots = self.manager.snapshots()
        self._dl_updating = True
        try:
            selected = self._selected_task_id
            existing = set(self.dl_tree.get_children())
            seen: set[str] = set()

            for index, snapshot in enumerate(snapshots):
                seen.add(snapshot.id)
                previous = self._dl_known_status.get(snapshot.id)
                if previous is not None and previous != snapshot.status:
                    self._on_task_status_changed(snapshot, previous)
                self._dl_known_status[snapshot.id] = snapshot.status

                state_tag = {
                    DownloadStatus.RUNNING: "running",
                    DownloadStatus.FAILED: "failed",
                    DownloadStatus.COMPLETED: "completed",
                    DownloadStatus.PAUSED: "paused",
                }.get(snapshot.status, "")
                tags: tuple[str, ...] = ()
                if state_tag:
                    tags += (state_tag,)
                if index % 2:
                    tags += ("odd",)

                values = (
                    snapshot.filename,
                    self._title_for_identifier(snapshot.identifier),
                    STATUS_LABELS.get(snapshot.status, _capitalise(snapshot.status.value)),
                    self._download_progress_text(snapshot),
                    self._download_size_text(snapshot),
                    human_speed(snapshot.speed) if snapshot.status == DownloadStatus.RUNNING else "—",
                    human_eta(snapshot.eta) if snapshot.status == DownloadStatus.RUNNING else "—",
                    short(snapshot.error, 70) if snapshot.error else "",
                )
                if snapshot.id in existing:
                    self.dl_tree.item(snapshot.id, values=values, tags=tags)
                else:
                    self.dl_tree.insert("", "end", iid=snapshot.id, values=values, tags=tags)

            for task_id in list(existing - seen):
                try:
                    self.dl_tree.delete(task_id)
                except tk.TclError:
                    pass
            for task_id in list(self._dl_known_status):
                if task_id not in seen:
                    self._dl_known_status.pop(task_id, None)

            if selected and selected not in seen:
                selected = None
            self._selected_task_id = selected
            if selected:
                try:
                    self.dl_tree.selection_set(selected)
                except tk.TclError:
                    self._selected_task_id = None
        finally:
            self._dl_updating = False

        active = sum(1 for snap in snapshots if not snap.status.terminal)
        terminal = len(snapshots) - active
        if snapshots:
            self.dl_status_var.set(
                f"{active} active · {len(snapshots)} {_plural(len(snapshots), 'task')}"
                + (f" · {terminal} finished" if terminal else "")
            )
        else:
            self.dl_status_var.set("No downloads yet. Pick files in Browse.")

        if self.dl_banner is not None and not self._restored_visible:
            self.dl_banner.pack_forget()
        elif self.dl_banner is not None and not any(
            not snap.status.terminal for snap in snapshots
        ) and snapshots:
            self._restored_visible = False
            self.dl_banner.pack_forget()

        self._update_download_detail()
        self._update_download_buttons()
        self._refresh_download_tab_title(active)

    def _on_task_status_changed(
        self, snapshot: TaskSnapshot, previous: DownloadStatus
    ) -> None:
        if snapshot.status == DownloadStatus.COMPLETED:
            self._status(
                f"Downloaded {snapshot.filename}. Rescan the Library to index it."
            )
            if bool(self.settings.get("notifications", True)):
                platform_support.notify("Download complete", snapshot.filename)
        elif snapshot.status == DownloadStatus.FAILED:
            self._status(
                f"Download failed: {snapshot.filename}. See the Downloads tab for details.",
                error=True,
            )

    def _download_progress_text(self, snapshot: TaskSnapshot) -> str:
        progress = snapshot.progress
        if progress is not None:
            return f"{int(progress * 100)}%"
        if snapshot.status == DownloadStatus.RUNNING:
            return "working"
        return "—"

    def _download_size_text(self, snapshot: TaskSnapshot) -> str:
        downloaded = human_size(snapshot.downloaded)
        if snapshot.total:
            return f"{downloaded} of {human_size(snapshot.total)}"
        return f"{downloaded} of unknown size"

    def _update_download_detail(self) -> None:
        snapshot = self._selected_download()
        if snapshot is None:
            running = next(
                (s for s in self.manager.snapshots() if s.status == DownloadStatus.RUNNING),
                None,
            )
            snapshot = running
            if running is None:
                self.dl_selected_var.set("No download selected")
                self.dl_detail_var.set("")
                self.dl_error_var.set("")
                self._set_download_progress(None)
                return
            self.dl_selected_var.set(f"Active: {snapshot.filename}")
        else:
            self.dl_selected_var.set(snapshot.filename)

        status = STATUS_LABELS.get(snapshot.status, _capitalise(snapshot.status.value))
        self.dl_detail_var.set(
            f"{status} · {self._download_size_text(snapshot)} · {snapshot.final_path}"
        )
        self.dl_error_var.set(snapshot.error or "")
        self._set_download_progress(snapshot)

    def _set_download_progress(self, snapshot: TaskSnapshot | None) -> None:
        bar = self.dl_progress
        if snapshot is None:
            if self._dl_progress_mode != "determinate":
                bar.stop()
                bar.configure(mode="determinate")
                self._dl_progress_mode = "determinate"
            bar.configure(value=0)
            return
        progress = snapshot.progress
        if snapshot.status == DownloadStatus.RUNNING and progress is None:
            if self._dl_progress_mode != "indeterminate":
                bar.configure(mode="indeterminate")
                bar.start(14)
                self._dl_progress_mode = "indeterminate"
            return
        if self._dl_progress_mode != "determinate":
            bar.stop()
            bar.configure(mode="determinate")
            self._dl_progress_mode = "determinate"
        if progress is not None:
            bar.configure(value=progress * 100)
        elif snapshot.status == DownloadStatus.COMPLETED:
            bar.configure(value=100)
        else:
            bar.configure(value=0)

    def _update_download_buttons(self) -> None:
        snapshot = self._selected_download()
        status = snapshot.status if snapshot else None
        terminal = status.terminal if status else False

        def set_state(button: ttk.Button, enabled: bool) -> None:
            button.state(["!disabled"] if enabled else ["disabled"])

        set_state(self.dl_pause_btn, status == DownloadStatus.RUNNING)
        set_state(self.dl_resume_btn, status == DownloadStatus.PAUSED)
        set_state(self.dl_cancel_btn, status is not None and not terminal)
        set_state(self.dl_retry_btn,
                  status in (DownloadStatus.FAILED, DownloadStatus.CANCELLED))
        set_state(self.dl_remove_btn, bool(terminal))
        snapshots = self.manager.snapshots()
        set_state(self.dl_clear_btn, any(s.status.terminal for s in snapshots))
        set_state(self.dl_resume_all_btn,
                  any(s.status == DownloadStatus.PAUSED for s in snapshots))

    def _refresh_download_tab_title(self, active: int) -> None:
        text = f"Downloads ({active})" if active else "Downloads"
        try:
            self.notebook.tab(1, text=text)
        except tk.TclError:
            pass

    def _download_action(self, action: Callable[[str], Any], task_id: str,
                         message: str) -> None:
        def work() -> Any:
            return action(task_id)

        self._run_async(
            work,
            lambda _result: self._status(message),
            lambda exc: self._status(
                f"Could not change the download: {_error_text(exc)}", error=True
            ),
        )

    def _pause_selected(self) -> None:
        snapshot = self._selected_download()
        if snapshot is not None:
            self._download_action(self.manager.pause, snapshot.id,
                                  f"Paused {snapshot.filename}.")

    def _resume_selected(self) -> None:
        snapshot = self._selected_download()
        if snapshot is not None:
            self._download_action(self.manager.resume, snapshot.id,
                                  f"Resumed {snapshot.filename}.")
            self._restored_visible = False
            if self.dl_banner is not None:
                self.dl_banner.pack_forget()

    def _cancel_selected(self) -> None:
        snapshot = self._selected_download()
        if snapshot is not None:
            self._download_action(self.manager.cancel, snapshot.id,
                                  f"Cancelled {snapshot.filename}. The partial file was kept.")

    def _retry_selected(self) -> None:
        snapshot = self._selected_download()
        if snapshot is not None:
            self._download_action(self.manager.retry, snapshot.id,
                                  f"Retrying {snapshot.filename}.")

    def _remove_selected(self) -> None:
        snapshot = self._selected_download()
        if snapshot is not None and snapshot.status.terminal:
            self._download_action(self.manager.remove, snapshot.id,
                                  f"Removed the finished task for {snapshot.filename}.")

    def _clear_finished(self) -> None:
        def work() -> int:
            return self.manager.clear_finished()

        def done(count: int) -> None:
            self._status(
                f"Cleared {count} finished {_plural(count, 'task')}."
                if count else "There were no finished tasks to clear."
            )
            self._refresh_downloads()

        self._run_async(work, done, lambda exc: self._status(
            f"Could not clear finished tasks: {_error_text(exc)}", error=True
        ))

    def _resume_all(self) -> None:
        paused = [s.id for s in self.manager.snapshots()
                  if s.status == DownloadStatus.PAUSED]
        if not paused:
            self._status("There are no paused downloads to resume.")
            return

        def work() -> int:
            for task_id in paused:
                self.manager.resume(task_id)
            return len(paused)

        def done(count: int) -> None:
            self._status(f"Resumed {count} {_plural(count, 'download')}.")
            self._restored_visible = False
            if self.dl_banner is not None:
                self.dl_banner.pack_forget()
            self._refresh_downloads()

        self._run_async(work, done, lambda exc: self._status(
            f"Could not resume downloads: {_error_text(exc)}", error=True
        ))

    def _dismiss_restored(self) -> None:
        self._restored_visible = False
        if self.dl_banner is not None:
            self.dl_banner.pack_forget()

    # -- downloads: torrents ----------------------------------------------

    def _schedule_torrent_refresh(self) -> None:
        if self._closing:
            return
        self._torrent_after = self.root.after(1000, self._torrent_tick)

    def _torrent_tick(self) -> None:
        self._torrent_after = None
        if self._closing:
            return
        self._refresh_torrents()
        self._schedule_torrent_refresh()

    def _refresh_torrents(self) -> None:
        if (
            self._closing
            or self._torrent_refreshing
            or not hasattr(self, "torrent_tree")
        ):
            return
        self._torrent_refreshing = True

        def work() -> Any:
            tasks = self.torrents.refresh()
            return tasks, self.torrents.engine_status()

        def done(result: Any) -> None:
            self._torrent_refreshing = False
            if self._closing:
                return
            tasks, info = result
            self._apply_torrent_rows(tasks, info)

        def failed(exc: BaseException) -> None:
            self._torrent_refreshing = False
            if self._closing:
                return
            self.torrent_engine_var.set("Torrent engine status unavailable")
            self._status(
                f"Could not refresh the torrent queue: {_error_text(exc)}",
                error=True,
            )

        self._run_async(work, done, failed)

    def _torrent_progress_text(self, task: TorrentTask) -> str:
        if task.status == TORRENT_COMPLETED:
            return "100%"
        if task.total:
            return f"{int(task.fraction * 100)}%"
        if task.status == TORRENT_DOWNLOADING:
            return "metadata"
        return "—"

    def _apply_torrent_rows(self, tasks: Sequence[TorrentTask], info: Any) -> None:
        if self._closing or not hasattr(self, "torrent_tree"):
            return
        self._torrent_updating = True
        try:
            selected = self._selected_torrent_id
            existing = set(self.torrent_tree.get_children())
            seen: set[str] = set()

            for index, task in enumerate(tasks):
                seen.add(task.task_id)
                previous = self._torrent_known_status.get(task.task_id)
                if previous is not None and previous != task.status:
                    self._on_torrent_status_changed(task, previous)
                self._torrent_known_status[task.task_id] = task.status

                state_tag = {
                    TORRENT_DOWNLOADING: "running",
                    TORRENT_FAILED: "failed",
                    TORRENT_COMPLETED: "completed",
                    TORRENT_PAUSED: "paused",
                    TORRENT_QUEUED: "queued",
                }.get(task.status, "")
                tags: tuple[str, ...] = ()
                if state_tag:
                    tags += (state_tag,)
                if index % 2:
                    tags += ("odd",)

                values = (
                    short(task.title or task.infohash[:12], 60),
                    _capitalise(task.status),
                    self._torrent_progress_text(task),
                    (human_speed(task.speed)
                     if task.status == TORRENT_DOWNLOADING else "—"),
                    str(task.peers) if task.status == TORRENT_DOWNLOADING else "—",
                    short(task.error, 60) if task.error else "",
                )
                if task.task_id in existing:
                    self.torrent_tree.item(task.task_id, values=values, tags=tags)
                else:
                    self.torrent_tree.insert(
                        "", "end", iid=task.task_id, values=values, tags=tags
                    )

            for task_id in list(existing - seen):
                try:
                    self.torrent_tree.delete(task_id)
                except tk.TclError:
                    pass
            for task_id in list(self._torrent_known_status):
                if task_id not in seen:
                    self._torrent_known_status.pop(task_id, None)

            if selected and selected not in seen:
                selected = None
            self._selected_torrent_id = selected
            if selected:
                try:
                    self.torrent_tree.selection_set(selected)
                except tk.TclError:
                    self._selected_torrent_id = None
        finally:
            self._torrent_updating = False

        if info.available:
            self.torrent_engine_var.set(info.summary)
        else:
            self.torrent_engine_var.set(info.reason or "No torrent engine found")
        self._update_torrent_buttons(tasks)

    def _on_torrent_status_changed(self, task: TorrentTask, _previous: str) -> None:
        if task.status == TORRENT_FAILED:
            self._status(
                f"Torrent failed: {task.title or task.infohash[:12]}. "
                "See the torrent row for details.",
                error=True,
            )

    def _on_torrent_complete(self, task: TorrentTask) -> None:
        # Called from the polling worker; hop back to the Tk thread through the
        # UI queue that root.after drains.
        def apply() -> None:
            if self._closing:
                return
            name = task.title or task.infohash[:12]
            self._status(f"Torrent finished: {name}. Rescanning the Library…")
            if bool(self.settings.get("notifications", True)):
                platform_support.notify("Torrent complete", name)
            self._refresh_torrents()
            self._rescan_library()

        self._post(apply)

    def _on_torrent_select(self, _event: tk.Event | None = None) -> None:
        if self._torrent_updating:
            return
        selection = self.torrent_tree.selection()
        self._selected_torrent_id = selection[0] if selection else None
        self._update_torrent_buttons()

    def _selected_torrent(self) -> TorrentTask | None:
        if not self._selected_torrent_id:
            return None
        return self.torrents.get(self._selected_torrent_id)

    def _update_torrent_buttons(
        self, tasks: Sequence[TorrentTask] | None = None
    ) -> None:
        if not hasattr(self, "torrent_start_btn"):
            return
        task = self._selected_torrent()
        status = task.status if task else ""

        def set_state(button: ttk.Button, enabled: bool) -> None:
            button.state(["!disabled"] if enabled else ["disabled"])

        set_state(
            self.torrent_start_btn,
            status in (TORRENT_PAUSED, TORRENT_QUEUED, TORRENT_FAILED),
        )
        set_state(self.torrent_pause_btn, status == TORRENT_DOWNLOADING)
        set_state(self.torrent_remove_btn, task is not None)
        if tasks is None:
            tasks = self.torrents.tasks()
        set_state(self.torrent_clear_btn, any(t.finished for t in tasks))

    def _show_torrent_error(self, title: str, exc: BaseException) -> None:
        if isinstance(exc, EngineUnavailable):
            reason = str(exc) or "No BitTorrent engine was found."
            messagebox.showerror(
                "Torrent engine not found",
                f"{reason}\n\nThe aria2c path can be set in Settings.",
                parent=self.root,
            )
        elif isinstance(exc, TorrentError):
            messagebox.showerror(title, _error_text(exc), parent=self.root)
            self._refresh_torrents()
        else:
            self._status(f"{title}: {_error_text(exc)}", error=True)

    def _torrent_action(
        self, action: Callable[[str], Any], task: TorrentTask, message: str
    ) -> None:
        def work() -> Any:
            return action(task.task_id)

        def done(_result: Any) -> None:
            self._status(message)
            self._refresh_torrents()

        def failed(exc: BaseException) -> None:
            self._show_torrent_error("Torrent action failed", exc)

        self._run_async(work, done, failed)

    def _torrent_start_selected(self) -> None:
        task = self._selected_torrent()
        if task is None:
            self._status("Select a torrent first.", error=True)
            return
        if task.status == TORRENT_DOWNLOADING:
            self._status("That torrent is already downloading.")
            return
        name = task.title or task.infohash[:12]

        def work() -> Any:
            info = self.torrents.engine_status()
            if not info.available:
                raise EngineUnavailable(info.reason or info.summary)
            if task.status == TORRENT_PAUSED:
                return self.torrents.resume(task.task_id)
            return self.torrents.start(task.task_id)

        def done(_result: Any) -> None:
            self._status(f"Started {name}.")
            self._refresh_torrents()

        def failed(exc: BaseException) -> None:
            if isinstance(exc, EngineUnavailable):
                self._show_torrent_error("Torrent engine not found", exc)
            else:
                self._show_torrent_error("Torrent could not start", exc)
            self._refresh_torrents()

        self._run_async(work, done, failed)

    def _torrent_pause_selected(self) -> None:
        task = self._selected_torrent()
        if task is None:
            self._status("Select a torrent first.", error=True)
            return
        name = task.title or task.infohash[:12]
        self._torrent_action(self.torrents.pause, task, f"Paused {name}.")

    def _torrent_remove_selected(self) -> None:
        task = self._selected_torrent()
        if task is None:
            self._status("Select a torrent first.", error=True)
            return
        name = task.title or task.infohash[:12]
        confirmed = messagebox.askyesno(
            "Remove torrent",
            "Remove this torrent from the queue? Downloaded files stay on disk.",
            parent=self.root,
        )
        if not confirmed:
            return
        self._torrent_action(
            self.torrents.remove, task,
            f"Removed {name} from the torrent queue. Files stay on disk.",
        )

    def _torrent_clear_finished(self) -> None:
        def work() -> int:
            return self.torrents.clear_finished()

        def done(count: int) -> None:
            self._status(
                f"Cleared {count} finished {_plural(count, 'torrent')}."
                if count else "There were no finished torrents to clear."
            )
            self._refresh_torrents()

        def failed(exc: BaseException) -> None:
            self._show_torrent_error("Could not clear torrents", exc)

        self._run_async(work, done, failed)

    def _add_magnet_dialog(self) -> None:
        window = tk.Toplevel(self.root)
        window.title("Add magnet link")
        window.configure(background=tk_style.BG)
        window.transient(self.root)
        window.resizable(False, False)

        outer = ttk.Frame(window, style="App.TFrame", padding=16)
        outer.pack(fill="both", expand=True)
        card = tk.Frame(
            outer, background=tk_style.SURFACE, bd=0,
            highlightthickness=1, highlightbackground=tk_style.BORDER,
        )
        card.pack(fill="x")
        body = ttk.Frame(card, style="Surface.TFrame", padding=12)
        body.pack(fill="x")

        ttk.Label(body, text="Magnet link", style="FieldLabel.TLabel").pack(anchor="w")
        magnet_var = tk.StringVar()
        magnet_row = ttk.Frame(body, style="Surface.TFrame")
        magnet_row.pack(fill="x", pady=(3, 10))
        magnet_entry = ttk.Entry(magnet_row, textvariable=magnet_var, width=58)
        magnet_entry.pack(side="left", fill="x", expand=True)

        error_var = tk.StringVar(value="")

        def paste() -> None:
            try:
                text = self.root.clipboard_get()
            except tk.TclError:
                error_var.set("The clipboard is empty or unavailable.")
                return
            magnet_var.set(text.strip())
            error_var.set("")
            magnet_entry.focus_set()

        paste_btn = ttk.Button(magnet_row, text="Paste", command=paste)
        paste_btn.pack(side="left", padx=(8, 0))
        self._add_placeholder(magnet_entry, magnet_var, "magnet:?xt=urn:btih:…")

        ttk.Label(
            body, text="Title (optional)", style="FieldLabel.TLabel"
        ).pack(anchor="w")
        title_var = tk.StringVar()
        title_entry = ttk.Entry(body, textvariable=title_var)
        title_entry.pack(fill="x", pady=(3, 8))
        ttk.Label(
            body,
            text="The magnet is queued only. Press Start/Resume to begin. "
                 "Seeding is disabled by the engine.",
            style="Small.TLabel",
            wraplength=self._w(460),
            justify="left",
        ).pack(anchor="w")

        error_label = ttk.Label(
            outer, textvariable=error_var, style="Error.TLabel",
            wraplength=self._w(480), justify="left",
        )
        error_label.pack(anchor="w", pady=(8, 4))

        buttons = ttk.Frame(outer, style="App.TFrame")
        buttons.pack(fill="x")
        add_btn = ttk.Button(buttons, text="Add", style="Accent.TButton")
        add_btn.pack(side="right")
        ttk.Button(buttons, text="Cancel", command=window.destroy).pack(
            side="right", padx=(0, 8)
        )

        def add() -> None:
            try:
                task = self.torrents.add(
                    magnet_var.get(), title=title_var.get().strip()
                )
            except Exception as exc:  # never crash the dialog
                error_var.set(_error_text(exc))
                return
            self._selected_torrent_id = task.task_id
            name = task.title or task.infohash[:12]
            window.destroy()
            self._status(f"Added {name}. Press Start/Resume to begin.")
            self._refresh_torrents()

            def select() -> None:
                if self._closing or not hasattr(self, "torrent_tree"):
                    return
                try:
                    if self.torrent_tree.exists(task.task_id):
                        self.torrent_tree.selection_set(task.task_id)
                        self.torrent_tree.see(task.task_id)
                        self._update_torrent_buttons()
                except tk.TclError:
                    pass

            self.root.after(300, select)

        add_btn.configure(command=add)
        magnet_entry.bind("<Return>", lambda _e: add())
        title_entry.bind("<Return>", lambda _e: add())
        window.bind("<Escape>", lambda _e: window.destroy())
        window.protocol("WM_DELETE_WINDOW", window.destroy)
        window.update_idletasks()
        width = window.winfo_width()
        height = window.winfo_height()
        x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - width) // 2)
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - height) // 4)
        window.geometry(f"+{x}+{y}")
        window.grab_set()
        magnet_entry.focus_set()

    def _choose_download_dir(self) -> None:
        initial = str(self.download_dir)
        chosen = filedialog.askdirectory(
            parent=self.root, title="Choose download folder", initialdir=initial,
            mustexist=True,
        )
        if not chosen:
            return
        try:
            folder = check_download_dir(chosen)
        except (OSError, ValueError) as exc:
            self._status(f"Cannot use that folder: {_error_text(exc)}", error=True)
            return
        self._apply_download_dir(folder)
        try:
            self.settings.set_download_dir(folder)
        except OSError as exc:
            self._status(
                f"The folder is in use for this session, but the choice could not be saved: {_error_text(exc)}",
                error=True,
            )
            return
        self._status(f"Download folder set to {_shorten_home(folder)}. Rescan the Library to index it.")

    def _apply_download_dir(self, folder: Path) -> None:
        self.download_dir = folder
        self.library.download_root = folder
        if hasattr(self, "torrents"):
            self.torrents.download_root = folder
        self._update_download_dir_labels()

    def _update_download_dir_labels(self) -> None:
        text = _shorten_home(self.download_dir)
        if hasattr(self, "dl_folder_var"):
            self.dl_folder_var.set(text)
        if hasattr(self, "dl_folder_label"):
            try:
                self.dl_folder_label.configure(text=text)
            except tk.TclError:
                pass

    def _open_download_dir(self) -> None:
        if not platform_support.open_folder(self.download_dir):
            self._status("Could not open the download folder.", error=True)

    # -- library tab -------------------------------------------------------

    def _build_library_tab(self) -> None:
        tab = self.library_tab

        toolbar = ttk.Frame(tab, style="Toolbar.TFrame", padding=(14, 10))
        toolbar.pack(fill="x")
        toolbar.columnconfigure(1, weight=1)
        ttk.Label(toolbar, text="Search", style="FieldLabel.TLabel").grid(
            row=0, column=0, padx=(0, 10), sticky="w"
        )
        self.lib_search_var = tk.StringVar()
        self.lib_search = ttk.Entry(toolbar, textvariable=self.lib_search_var)
        self.lib_search.grid(row=0, column=1, sticky="ew")
        self._add_placeholder(self.lib_search, self.lib_search_var,
                              "Title, creator, or identifier")
        tk_style.tooltip(self.lib_search, "Filter the local library (Ctrl+F)")
        self.lib_search_var.trace_add("write", self._schedule_library_filter)
        self.lib_search.bind("<Escape>", lambda _e: self._clear_library_search())

        rescan = ttk.Button(toolbar, text="Rescan", command=self._rescan_library)
        rescan.grid(row=0, column=2, padx=(10, 0))
        tk_style.tooltip(rescan, "Look for games in the download folder (F5)")
        export = ttk.Button(toolbar, text="Export…", command=self._export_library)
        export.grid(row=0, column=3, padx=(8, 0))
        tk_style.tooltip(export, "Save a JSON snapshot of the library index")
        import_btn = ttk.Button(toolbar, text="Import…", command=self._import_library)
        import_btn.grid(row=0, column=4, padx=(8, 0))
        tk_style.tooltip(import_btn, "Merge a previously exported library index")
        duplicates = ttk.Button(toolbar, text="Find duplicates…",
                                command=self._open_duplicates)
        duplicates.grid(row=0, column=5, padx=(8, 0))
        tk_style.tooltip(duplicates, "List probable duplicate files without deleting anything")

        filters = ttk.Frame(tab, style="Surface.TFrame", padding=(14, 0, 14, 8))
        filters.pack(fill="x")
        ttk.Label(filters, text="Status", style="FieldLabel.TLabel").pack(side="left")
        self.lib_status_filter = ttk.Combobox(
            filters, state="readonly", width=14,
            values=("Any status",) + tuple(_capitalise(s) for s in LIBRARY_STATUSES),
        )
        self.lib_status_filter.set("Any status")
        self.lib_status_filter.pack(side="left", padx=(6, 14))
        self.lib_status_filter.bind("<<ComboboxSelected>>", self._on_library_filter_change)

        ttk.Label(filters, text="Favorites", style="FieldLabel.TLabel").pack(side="left")
        self.lib_fav_filter = ttk.Combobox(
            filters, state="readonly", width=14, values=("All games", "Favorites only")
        )
        self.lib_fav_filter.set("All games")
        self.lib_fav_filter.pack(side="left", padx=(6, 14))
        self.lib_fav_filter.bind("<<ComboboxSelected>>", self._on_library_filter_change)

        ttk.Label(filters, text="Platform", style="FieldLabel.TLabel").pack(side="left")
        self.lib_compat_filter = ttk.Combobox(
            filters, state="readonly", width=15, values=("Any platform",)
        )
        self.lib_compat_filter.set("Any platform")
        self.lib_compat_filter.pack(side="left", padx=(6, 14))
        self.lib_compat_filter.bind("<<ComboboxSelected>>", self._on_library_filter_change)

        ttk.Label(filters, text="Sort", style="FieldLabel.TLabel").pack(side="left")
        self.lib_sort_var = tk.StringVar(value="Title")
        self.lib_sort = ttk.Combobox(
            filters, state="readonly", width=14,
            values=("Title", "Year", "Size", "Recently seen"),
            textvariable=self.lib_sort_var,
        )
        self.lib_sort.pack(side="left", padx=(6, 0))
        self.lib_sort.bind("<<ComboboxSelected>>", self._on_library_sort_change)
        saved_sort = str(self.settings.get("windows_library_sort", "title"))
        for label, key in self._sort_map().items():
            if key == saved_sort:
                self.lib_sort_var.set(label)
                break

        self.lib_count_var = tk.StringVar(value="0 games")
        ttk.Label(filters, textvariable=self.lib_count_var,
                  style="Small.TLabel").pack(side="right")

        visibility = ttk.Frame(tab, style="Surface.TFrame", padding=(14, 0, 14, 8))
        visibility.pack(fill="x")
        self.lib_games_only_var = tk.BooleanVar(
            value=not self.settings.library_games_only
        )
        self.lib_show_all_check = ttk.Checkbutton(
            visibility, text="Show all folders",
            variable=self.lib_games_only_var,
            command=self._on_library_visibility_change,
        )
        self.lib_show_all_check.pack(side="left")
        tk_style.tooltip(
            self.lib_show_all_check,
            "Include folders that do not look like games, such as documentation or tools",
        )
        self.lib_show_missing_var = tk.BooleanVar(
            value=self.settings.library_show_missing
        )
        self.lib_show_missing_check = ttk.Checkbutton(
            visibility, text="Show missing",
            variable=self.lib_show_missing_var,
            command=self._on_library_visibility_change,
        )
        self.lib_show_missing_check.pack(side="left", padx=(14, 0))
        tk_style.tooltip(
            self.lib_show_missing_check,
            "Keep entries whose folder has disappeared from the download folder",
        )
        self.lib_stats_var = tk.StringVar(value="")
        ttk.Label(visibility, textvariable=self.lib_stats_var,
                  style="Small.TLabel").pack(side="left", padx=(16, 0))
        self.lib_forget_btn = ttk.Button(
            visibility, text="Forget missing entries",
            command=self._forget_missing_entries,
        )
        self.lib_forget_btn.pack(side="right")
        self.lib_forget_btn.state(["disabled"])
        tk_style.tooltip(
            self.lib_forget_btn,
            "Remove entries whose folder is gone from the index. Files are never deleted.",
        )

        ttk.Separator(tab).pack(fill="x")

        body = ttk.Frame(tab, style="Surface.TFrame")
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=2, minsize=self._w(320))
        body.columnconfigure(1, weight=3)
        body.rowconfigure(0, weight=1)
        left = ttk.Frame(body, style="Surface.TFrame")
        right = ttk.Frame(body, style="Surface.TFrame")
        left.grid(row=0, column=0, sticky="nsew")
        right.grid(row=0, column=1, sticky="nsew")

        head = ttk.Frame(left, style="Surface.TFrame", padding=(14, 12, 14, 6))
        head.pack(fill="x")
        ttk.Label(head, text="Library", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(
            head, text="Games found in your download folder.",
            style="Small.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        tree_frame = ttk.Frame(left, style="Surface.TFrame", padding=(8, 0, 8, 8))
        tree_frame.pack(fill="both", expand=True)
        columns = ("title", "year", "size", "status", "compatibility", "favorite")
        self.library_tree = ttk.Treeview(
            tree_frame, columns=columns, show="headings", selectmode="browse"
        )
        headings = {
            "title": ("Title", "w", 190),
            "year": ("Year", "w", 50),
            "size": ("Size", "e", 78),
            "status": ("Status", "w", 104),
            "compatibility": ("Platform", "w", 112),
            "favorite": ("Favorite", "w", 62),
        }
        for key, (label, anchor, width) in headings.items():
            self.library_tree.heading(key, text=label, anchor=anchor)
            self.library_tree.column(
                key,
                width=self._w(width),
                minwidth=self._w(40),
                stretch=key == "title",
                anchor=anchor,
            )
        self.library_tree.tag_configure("odd", background=tk_style.STRIPE)
        self.library_tree.tag_configure("missing", foreground=tk_style.FAINT)
        self.library_tree.tag_configure("failed", foreground=tk_style.DANGER)
        self.library_tree.tag_configure("incomplete", foreground=tk_style.WARNING_INK)
        lib_scroll = ttk.Scrollbar(tree_frame, orient="vertical",
                                   command=self.library_tree.yview)
        self.library_tree.configure(yscrollcommand=lib_scroll.set)
        self.library_tree.pack(side="left", fill="both", expand=True)
        lib_scroll.pack(side="right", fill="y")
        self.library_tree.bind("<<TreeviewSelect>>", self._on_library_select)
        self.library_tree.bind("<Double-1>", lambda _e: self._open_library_folder())

        self.lib_message_frame = ttk.Frame(right, style="Surface.TFrame")
        self.lib_message_label = ttk.Label(
            self.lib_message_frame,
            text="No games in the library yet.\n\nDownload a game, then press Rescan.",
            style="EmptyText.TLabel",
            anchor="center",
            justify="center",
            wraplength=self._w(360),
        )
        self.lib_message_label.pack(fill="both", expand=True, padx=22, pady=22)
        self.lib_message_frame.pack(fill="both", expand=True)

        self.lib_content = ttk.Frame(right, style="Surface.TFrame", padding=(16, 14, 16, 12))
        self.lib_content.columnconfigure(1, weight=1)
        self.lib_content.rowconfigure(4, weight=1)

        self.lib_cover_canvas = tk.Canvas(
            self.lib_content,
            width=self._w(136),
            height=self._w(136),
            background=tk_style.SURFACE_ALT,
            highlightthickness=1,
            highlightbackground=tk_style.BORDER,
            bd=0,
        )
        self.lib_cover_canvas.grid(row=0, column=0, rowspan=2, sticky="n",
                                   padx=(0, 16), pady=(0, 10))
        self._draw_cover_placeholder(self.lib_cover_canvas, "No cover preview available")

        info = ttk.Frame(self.lib_content, style="Surface.TFrame")
        info.grid(row=0, column=1, sticky="new")
        info.columnconfigure(0, weight=1)
        self.lib_title_var = tk.StringVar(value="")
        lib_title = ttk.Label(
            info, textvariable=self.lib_title_var, style="Heading.TLabel",
            wraplength=self._w(330), justify="left", anchor="w",
        )
        lib_title.grid(row=0, column=0, sticky="ew")
        self._follow_width(lib_title, info, 12)
        self.lib_meta_var = tk.StringVar(value="")
        lib_meta = ttk.Label(
            info, textvariable=self.lib_meta_var, style="Small.TLabel",
            wraplength=self._w(330), justify="left", anchor="w",
        )
        lib_meta.grid(row=1, column=0, sticky="ew", pady=(3, 0))
        self._follow_width(lib_meta, info, 12)

        fields = ttk.Frame(info, style="Surface.TFrame")
        fields.grid(row=2, column=0, sticky="ew", pady=(9, 0))
        fields.columnconfigure(1, weight=1)
        self.lib_status_var = tk.StringVar(value="—")
        self.lib_integrity_var = tk.StringVar(value=INTEGRITY_LABELS[""])
        self.lib_compat_var = tk.StringVar(value="Unknown")
        self.lib_files_var = tk.StringVar(value="0 files")
        for row, (label, variable) in enumerate((
            ("Status", self.lib_status_var),
            ("Integrity", self.lib_integrity_var),
            ("Platform", self.lib_compat_var),
            ("Contents", self.lib_files_var),
        )):
            ttk.Label(fields, text=label, style="Caption.TLabel").grid(
                row=row, column=0, sticky="nw", padx=(0, 10), pady=(0, 3)
            )
            ttk.Label(fields, textvariable=variable, style="Small.TLabel",
                      anchor="w", justify="left",
                      wraplength=self._w(260)).grid(
                row=row, column=1, sticky="nw", pady=(0, 3)
            )

        self.lib_source_label = ttk.Label(
            info, text="View on archive.org", style="Link.TLabel", cursor="hand2"
        )
        self.lib_source_label.grid(row=3, column=0, sticky="w", pady=(5, 0))
        self.lib_source_label.bind("<Button-1>", lambda _e: self._open_library_source())

        self.lib_notice_var = tk.StringVar(value="")
        self.lib_notice_label = ttk.Label(
            info, textvariable=self.lib_notice_var, style="Meta.TLabel",
            anchor="w", justify="left", wraplength=self._w(260),
        )
        self.lib_notice_label.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        self.lib_notice_label.grid_remove()

        folder_head = ttk.Frame(self.lib_content, style="Surface.TFrame")
        folder_head.grid(row=1, column=1, sticky="sew", pady=(8, 0))
        folder_head.columnconfigure(1, weight=1)
        ttk.Label(folder_head, text="Folder", style="Caption.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        self.lib_folder_var = tk.StringVar(value="")
        self.lib_folder_entry = tk.Entry(
            folder_head,
            textvariable=self.lib_folder_var,
            state="readonly",
            readonlybackground=tk_style.SURFACE_ALT,
            foreground=tk_style.MUTED,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=tk_style.BORDER,
            font=self.fonts.small,
        )
        self.lib_folder_entry.grid(row=0, column=1, sticky="ew", ipady=3)

        start_box = ttk.Frame(self.lib_content, style="Surface.TFrame")
        start_box.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        start_box.columnconfigure(0, weight=1)
        start_head = ttk.Frame(start_box, style="Surface.TFrame")
        start_head.grid(row=0, column=0, sticky="ew")
        ttk.Label(start_head, text="Start with", style="Caption.TLabel").pack(side="left")
        self.lib_launch_hint_var = tk.StringVar(value="")
        ttk.Label(start_head, textvariable=self.lib_launch_hint_var,
                  style="Small.TLabel").pack(side="left", padx=(10, 0))
        self.lib_launch_choice_var = tk.StringVar(value="")
        self.lib_launch_box = tk.Frame(
            start_box, background=tk_style.SURFACE, bd=0,
            highlightthickness=1, highlightbackground=tk_style.BORDER,
        )
        self.lib_launch_box.grid(row=1, column=0, sticky="ew", pady=(3, 0))

        files_head = ttk.Frame(self.lib_content, style="Surface.TFrame")
        files_head.grid(row=3, column=0, columnspan=2, sticky="new", pady=(8, 0))
        ttk.Label(files_head, text="Files", style="Caption.TLabel").pack(side="left")
        self.lib_files_count_var = tk.StringVar(value="")
        ttk.Label(files_head, textvariable=self.lib_files_count_var,
                  style="Small.TLabel").pack(side="left", padx=(10, 0))

        files_border = tk.Frame(self.lib_content, background=tk_style.BORDER, bd=0)
        files_border.grid(row=4, column=0, columnspan=2, sticky="nsew", pady=(4, 0))
        files_inner = tk.Frame(files_border, background=tk_style.SURFACE, bd=0)
        files_inner.pack(fill="both", expand=True, padx=1, pady=1)
        self.lib_file_tree = ttk.Treeview(
            files_inner, columns=("name", "type", "size"), show="headings",
            selectmode="browse", height=4,
        )
        self.lib_file_tree.heading("name", text="Name", anchor="w")
        self.lib_file_tree.heading("type", text="Type", anchor="w")
        self.lib_file_tree.heading("size", text="Size", anchor="e")
        self.lib_file_tree.column("name", width=self._w(190), minwidth=self._w(120), stretch=True)
        self.lib_file_tree.column("type", width=self._w(120), minwidth=self._w(80), stretch=False)
        self.lib_file_tree.column("size", width=self._w(76), minwidth=self._w(58), stretch=False, anchor="e")
        self.lib_file_tree.tag_configure("odd", background=tk_style.STRIPE)
        lib_file_scroll = ttk.Scrollbar(files_inner, orient="vertical",
                                        command=self.lib_file_tree.yview)
        self.lib_file_tree.configure(yscrollcommand=lib_file_scroll.set)
        self.lib_file_tree.pack(side="left", fill="both", expand=True)
        lib_file_scroll.pack(side="right", fill="y")
        self.lib_file_tree.bind("<<TreeviewSelect>>", self._on_library_file_select)

        self.lib_action_status_var = tk.StringVar(value="")
        self.lib_action_status_label = ttk.Label(
            self.lib_content, textvariable=self.lib_action_status_var,
            style="Small.TLabel", wraplength=self._w(360), justify="left",
        )
        self.lib_action_status_label.grid(row=5, column=0, columnspan=2,
                                          sticky="ew", pady=(8, 0))
        self._follow_width(self.lib_action_status_label, self.lib_content, 24)
        self.lib_verify_bar = ttk.Progressbar(
            self.lib_content, style="Verify.TProgressbar",
            mode="determinate", maximum=100, value=0,
        )
        self.lib_verify_bar.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self.lib_verify_bar.grid_remove()

        actions = ttk.Frame(self.lib_content, style="Surface.TFrame")
        actions.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        actions.columnconfigure(2, weight=1)
        self.lib_open_btn = ttk.Button(actions, text="Open folder",
                                       command=self._open_library_folder)
        self.lib_open_btn.grid(row=0, column=0, sticky="w")
        self.lib_verify_btn = ttk.Button(actions, text="Verify integrity",
                                         command=self._verify_integrity)
        self.lib_verify_btn.grid(row=0, column=1, padx=(8, 0), sticky="w")
        self.lib_fav_btn = ttk.Button(actions, text="Add to favorites",
                                      command=self._toggle_favorite)
        self.lib_fav_btn.grid(row=1, column=0, pady=(8, 0), sticky="w")
        self.lib_launch_btn = ttk.Button(actions, text="Launch…", style="Accent.TButton",
                                         command=self._review_and_launch)
        self.lib_launch_btn.grid(row=1, column=1, padx=(8, 0), pady=(8, 0), sticky="w")
        self.lib_remove_btn = ttk.Button(
            actions, text="Remove from library", command=self._remove_from_library
        )
        self.lib_remove_btn.grid(row=1, column=2, pady=(8, 0), sticky="e")
        tk_style.tooltip(self.lib_verify_btn,
                         "Compare the selected file with archive.org checksums")
        tk_style.tooltip(self.lib_launch_btn,
                         "Review the chosen file, then optionally launch it")
        tk_style.tooltip(self.lib_fav_btn, "Mark or unmark this game as a favorite")
        tk_style.tooltip(
            self.lib_remove_btn,
            "Remove this entry from the library index. Files on disk are kept.",
        )

        self._refresh_compat_filter()
        self._update_library_actions()

    # -- library: table ----------------------------------------------------

    def _sort_map(self) -> dict[str, str]:
        return {"Title": "title", "Year": "year", "Size": "size",
                "Recently seen": "recent"}

    def _schedule_library_filter(self, *_args: Any) -> None:
        if getattr(self, "_library_filter_after", None) is not None:
            try:
                self.root.after_cancel(self._library_filter_after)
            except tk.TclError:
                pass
        self._library_filter_after = self.root.after(140, self._refresh_library_table)

    def _on_library_filter_change(self, _event: tk.Event | None = None) -> None:
        self._refresh_library_table()

    def _on_library_sort_change(self, _event: tk.Event | None = None) -> None:
        key = self._sort_map().get(self.lib_sort_var.get(), "title")
        try:
            self.settings.set("windows_library_sort", key)
        except OSError:
            pass
        self._refresh_library_table()

    def _on_library_visibility_change(self) -> None:
        try:
            self.settings.set_library_games_only(not self.lib_games_only_var.get())
            self.settings.set_library_show_missing(bool(self.lib_show_missing_var.get()))
        except OSError as exc:
            self._status(f"Could not save the filter: {_error_text(exc)}", error=True)
        self._refresh_library_table()

    def _hidden_entry_count(self, entries: Sequence[LibraryEntry]) -> int:
        games_only = self.settings.library_games_only
        show_missing = self.settings.library_show_missing
        hidden = 0
        for entry in entries:
            if games_only and not entry.is_game:
                hidden += 1
            elif not show_missing and entry.status == "missing":
                hidden += 1
        return hidden

    def _missing_entries(self) -> list[LibraryEntry]:
        missing: list[LibraryEntry] = []
        for entry in self.library.entries():
            if not entry.folder:
                missing.append(entry)
                continue
            try:
                if not Path(entry.folder).is_dir():
                    missing.append(entry)
            except OSError:
                missing.append(entry)
        return missing

    def _update_forget_button(self, entries: Sequence[LibraryEntry] | None = None) -> None:
        if not hasattr(self, "lib_forget_btn"):
            return
        if entries is None:
            entries = self.library.entries()
        has_missing = any(
            not entry.folder or not Path(entry.folder).is_dir()
            for entry in entries
        )
        self.lib_forget_btn.state(["!disabled"] if has_missing else ["disabled"])

    def _forget_missing_entries(self) -> None:
        missing = self._missing_entries()
        if not missing:
            self._status("There are no missing entries to forget.")
            self._update_forget_button()
            return
        count = len(missing)
        confirmed = messagebox.askyesno(
            "Forget missing entries",
            f"Remove {count} missing {_plural(count, 'entry', 'entries')} "
            "from the library index?\n\n"
            "No files are deleted. Only entries whose folder is gone are removed.",
            parent=self.root,
            default="no",
            icon="warning",
        )
        if not confirmed:
            return
        removed = self.library.forget_missing()
        note = ""
        try:
            self.library.save()
        except OSError as exc:
            note = f" The index could not be saved: {_error_text(exc)}"
        self._library_selected = None
        self._library_entry = None
        self._clear_library_details()
        self._refresh_library_table()
        self._status(
            f"Forgot {removed} missing {_plural(removed, 'entry', 'entries')}. "
            f"Files on disk were not touched.{note}",
            error=bool(note),
        )

    def _remove_from_library(self) -> None:
        entry = self._library_entry
        if entry is None:
            self._status("Select a library entry first.", error=True)
            return
        title = entry.title or entry.identifier
        confirmed = messagebox.askyesno(
            "Remove from library",
            f"Remove “{title}” from the library?\n\n"
            "The files stay on disk. Only the library entry is removed; "
            "nothing is deleted.",
            parent=self.root,
            default="no",
            icon="warning",
        )
        if not confirmed:
            return
        removed = self.library.remove(entry.identifier)
        if not removed:
            self._status("That library entry no longer exists.", error=True)
            self._refresh_library_table()
            return
        note = ""
        try:
            self.library.save()
        except OSError as exc:
            note = f" The index could not be saved: {_error_text(exc)}"
        self._library_selected = None
        self._library_entry = None
        self._clear_library_details()
        self._refresh_library_table()
        self._status(
            f"Removed {title} from the library. Files on disk were kept.{note}",
            error=bool(note),
        )

    def _clear_library_search(self) -> None:
        if self.lib_search_var.get():
            self.lib_search_var.set("")
        self._refresh_library_table()
        self.lib_search.focus_set()

    def _refresh_compat_filter(self) -> None:
        if not hasattr(self, "lib_compat_filter"):
            return
        current = self.lib_compat_filter.get()
        values: list[str] = ["Any platform"]
        for status in COMPATIBILITY_STATUSES:
            if status not in values:
                values.append(status)
        for entry in self.library.entries():
            if entry.compatibility and entry.compatibility not in values:
                values.append(entry.compatibility)
        self.lib_compat_filter.configure(values=tuple(values))
        if current not in values:
            current = "Any platform"
        self.lib_compat_filter.set(current)

    def _library_filter_args(self) -> dict[str, Any]:
        status_label = self.lib_status_filter.get()
        status = None if status_label == "Any status" else status_label.lower()
        fav_label = self.lib_fav_filter.get()
        favorite = None if fav_label == "All games" else True
        compat_label = self.lib_compat_filter.get()
        compatibility = None if compat_label == "Any platform" else compat_label
        sort_key = self._sort_map().get(self.lib_sort_var.get(), "title")
        return {
            "query": self.lib_search_var.get().strip(),
            "status": status,
            "favorite": favorite,
            "compatibility": compatibility,
            "sort": sort_key,
            "games_only": self.settings.library_games_only,
            "include_missing": self.settings.library_show_missing,
        }

    def _refresh_library_table(self) -> None:
        if self._closing or not hasattr(self, "library_tree"):
            return
        try:
            entries = self.library.entries()
        except RuntimeError:
            self.root.after(150, self._refresh_library_table)
            return
        filters = self._library_filter_args()
        try:
            visible = LibraryStore.filter_entries(
                entries,
                query=filters["query"],
                status=filters["status"],
                favorite=filters["favorite"],
                compatibility=filters["compatibility"],
                games_only=filters["games_only"],
                include_missing=filters["include_missing"],
            )
            visible = LibraryStore.sort_entries(visible, filters["sort"])
        except Exception as exc:
            self._status(f"Could not filter the library: {_error_text(exc)}", error=True)
            return

        selected = self._library_selected
        self.library_tree.delete(*self.library_tree.get_children())
        for index, entry in enumerate(visible):
            state = entry.status if entry.status in ("missing", "failed", "incomplete") else ""
            tags: tuple[str, ...] = ()
            if state:
                tags += (state,)
            if index % 2:
                tags += ("odd",)
            self.library_tree.insert(
                "",
                "end",
                iid=entry.identifier,
                values=(
                    entry.title or entry.identifier,
                    entry.year or "—",
                    human_size(entry.size) if entry.size else "—",
                    _capitalise(entry.status),
                    entry.compatibility or "Unknown",
                    "Yes" if entry.favorite else "No",
                ),
                tags=tags,
            )

        total = len(entries)
        shown = len(visible)
        filtering = bool(
            filters["query"] or filters["status"] or filters["favorite"]
            or filters["compatibility"]
        )
        self.lib_count_var.set(
            f"{shown} of {total} {_plural(total, 'game')}" if filtering
            else f"{total} {_plural(total, 'game')}"
        )
        hidden = self._hidden_entry_count(entries)
        games = sum(1 for entry in visible if entry.is_game)
        self.lib_stats_var.set(
            f"{shown} shown · {games} {_plural(games, 'game')} · {hidden} hidden"
        )
        self._update_forget_button(entries)

        if selected and selected in self.library_tree.get_children():
            try:
                self.library_tree.selection_set(selected)
                self.library_tree.see(selected)
            except tk.TclError:
                self._library_selected = None
        else:
            self._library_selected = None
            if visible:
                self._show_library_content()
                self._clear_library_details()
                self.lib_message_label.configure(
                    text="Select a game to see its details."
                )
                self.lib_message_frame.pack(fill="both", expand=True)
                self.lib_content.pack_forget()
            else:
                self._update_library_empty_state(total, filtering)
        self._update_library_actions()

    def _update_library_empty_state(self, total: int, filtering: bool) -> None:
        self._show_library_message_only()
        if total == 0:
            self.lib_message_label.configure(
                text="No games in the library yet.\n\n"
                     "Download a game, then press Rescan."
            )
        elif filtering:
            self.lib_message_label.configure(
                text="No games match the current search or filters."
            )
        else:
            self.lib_message_label.configure(
                text="No games in the library yet."
            )

    def _show_library_message_only(self) -> None:
        self.lib_content.pack_forget()
        self.lib_message_frame.pack(fill="both", expand=True)

    def _show_library_content(self) -> None:
        self.lib_message_frame.pack_forget()
        self.lib_content.pack(fill="both", expand=True)

    def _clear_library_details(self) -> None:
        self._library_entry = None
        self._library_files = {}
        try:
            self.lib_file_tree.delete(*self.lib_file_tree.get_children())
        except tk.TclError:
            pass
        self.lib_title_var.set("")
        self.lib_meta_var.set("")
        self.lib_status_var.set("—")
        self.lib_integrity_var.set(INTEGRITY_LABELS[""])
        self.lib_compat_var.set("Unknown")
        self.lib_files_var.set("0 files")
        self.lib_files_count_var.set("")
        self.lib_folder_var.set("")
        self.lib_action_status_var.set("")
        self.lib_notice_var.set("")
        self.lib_notice_label.grid_remove()
        self.lib_launch_hint_var.set("")
        self.lib_launch_choice_var.set("")
        self._lib_launch_files = {}
        for radio in self._lib_launch_radios:
            try:
                radio.destroy()
            except tk.TclError:
                pass
        self._lib_launch_radios = []
        self._draw_cover_placeholder(self.lib_cover_canvas, "No cover preview available")
        self.lib_verify_bar.grid_remove()

    def _on_library_select(self, _event: tk.Event | None = None) -> None:
        selection = self.library_tree.selection()
        if not selection:
            return
        identifier = selection[0]
        if identifier == self._library_selected and self._library_entry is not None:
            return
        entry = self.library.get(identifier)
        if entry is None:
            return
        self._library_selected = identifier
        self._show_library_entry(entry, reload_files=True)

    # -- library: details --------------------------------------------------

    def _show_library_entry(self, entry: LibraryEntry, reload_files: bool) -> None:
        self._library_entry = entry
        self.lib_title_var.set(entry.title or entry.identifier)
        bits: list[str] = []
        if entry.creator:
            bits.append(entry.creator)
        if entry.year:
            bits.append(entry.year)
        if entry.size:
            bits.append(human_size(entry.size))
        if entry.file_count:
            bits.append(f"{entry.file_count} {_plural(entry.file_count, 'file')}")
        self.lib_meta_var.set(" · ".join(bits) or "No additional metadata.")
        self.lib_status_var.set(_capitalise(entry.status))
        self.lib_integrity_var.set(
            INTEGRITY_LABELS.get(entry.integrity, entry.integrity or INTEGRITY_LABELS[""])
        )
        self.lib_compat_var.set(entry.compatibility or "Unknown")
        self.lib_files_var.set(
            f"{entry.file_count} {_plural(entry.file_count, 'file')} · {human_size(entry.size)}"
            if entry.size else f"{entry.file_count} {_plural(entry.file_count, 'file')}"
        )
        self.lib_folder_var.set(entry.folder)
        self.lib_fav_btn.configure(
            text="Remove from favorites" if entry.favorite else "Add to favorites"
        )
        self.lib_action_status_var.set("")
        self.lib_action_status_label.configure(style="Small.TLabel")
        self.lib_verify_bar.grid_remove()
        if entry.is_game:
            self.lib_notice_label.grid_remove()
        else:
            self.lib_notice_var.set(
                "This folder has no game manifest, executable, or archive."
            )
            self.lib_notice_label.grid()

        folder = Path(entry.folder) if entry.folder else None
        if folder is not None and folder.is_dir():
            self.lib_action_status_var.set("")
        else:
            self.lib_action_status_var.set(
                "This game folder is missing. Restore it, then press Rescan."
            )
            self.lib_action_status_label.configure(style="Error.TLabel")

        self._show_library_content()
        self._update_library_actions()
        self._load_library_cover(entry)
        if reload_files:
            self._load_library_files(entry)

    def _load_library_cover(self, entry: LibraryEntry) -> None:
        self._lib_cover_token = getattr(self, "_lib_cover_token", 0) + 1
        token = self._lib_cover_token
        self._library_cover_photo = None
        self._draw_cover_placeholder(self.lib_cover_canvas, "No cover preview available")
        if not PIL_AVAILABLE or not entry.cover_path:
            return
        path = Path(entry.cover_path)
        if not path.is_file():
            return
        width = max(48, int(self.lib_cover_canvas.cget("width")) - 8)
        height = max(48, int(self.lib_cover_canvas.cget("height")) - 8)

        def work() -> Any:
            image = Image.open(path)
            image.load()
            image = image.convert("RGB")
            return self._fit_image(image, width, height)

        self._run_async(
            work,
            lambda image: self._on_library_cover_loaded(token, entry.identifier, image),
            lambda _exc: None,
        )

    def _on_library_cover_loaded(self, token: int, identifier: str, image: Any) -> None:
        if token != getattr(self, "_lib_cover_token", -1) or self._closing:
            return
        if self._library_entry is None or self._library_entry.identifier != identifier:
            return
        try:
            photo = ImageTk.PhotoImage(image)
        except Exception:
            return
        self._library_cover_photo = photo
        canvas = self.lib_cover_canvas
        canvas.delete("all")
        canvas.create_image(
            int(canvas.cget("width")) // 2,
            int(canvas.cget("height")) // 2,
            image=photo,
            anchor="center",
        )

    def _load_library_files(self, entry: LibraryEntry) -> None:
        self._library_token += 1
        token = self._library_token
        try:
            self.lib_file_tree.delete(*self.lib_file_tree.get_children())
        except tk.TclError:
            pass
        self._library_files = {}
        self.lib_file_tree.tag_configure("odd", background=tk_style.STRIPE)
        self.lib_files_count_var.set("Loading…")
        if not entry.folder:
            self.lib_files_count_var.set("No folder")
            return
        folder = Path(entry.folder)

        def work() -> Any:
            return scan_game_folder(folder)

        self._run_async(
            work,
            lambda info: self._on_library_files_loaded(token, entry.identifier, info),
            lambda exc: self._on_library_files_failed(token, entry.identifier, exc),
        )

    def _on_library_files_loaded(self, token: int, identifier: str,
                                 info: Any) -> None:
        if token != self._library_token or self._closing:
            return
        if self._library_entry is None or self._library_entry.identifier != identifier:
            return
        try:
            self.lib_file_tree.delete(*self.lib_file_tree.get_children())
        except tk.TclError:
            return
        self._library_files = {}
        for index, local in enumerate(info.files):
            iid = str(index)
            self._library_files[iid] = local
            tags = ("odd",) if index % 2 else ()
            self.lib_file_tree.insert(
                "", "end", iid=iid,
                values=(local.path.name, local.kind, human_size(local.size)),
                tags=tags,
            )
        count = len(info.files)
        self.lib_files_count_var.set(f"{count} {_plural(count, 'file')}")
        self._populate_launch_choices(info.files)
        if count == 0:
            self.lib_action_status_var.set(
                "No files were found in this game folder. It may still be downloading."
            )
        self._update_library_actions()

    def _on_library_files_failed(self, token: int, identifier: str,
                                 exc: BaseException) -> None:
        if token != self._library_token or self._closing:
            return
        self.lib_files_count_var.set("Unavailable")
        self.lib_action_status_var.set(
            f"Could not read the game folder: {_error_text(exc)}"
        )
        self.lib_action_status_label.configure(style="Error.TLabel")
        self._update_library_actions()

    def _on_library_file_select(self, _event: tk.Event | None = None) -> None:
        local = self._selected_library_file()
        if local is not None and _local_launchable(local):
            iid = self._launch_iid_for(local)
            if iid:
                self.lib_launch_choice_var.set(iid)
        self._update_library_actions()

    def _selected_library_file(self) -> LocalFile | None:
        selection = self.lib_file_tree.selection()
        if not selection:
            return None
        return self._library_files.get(selection[0])

    def _launch_sort_key(self, local: LocalFile) -> tuple[int, str]:
        name = local.path.name.casefold()
        if not _local_launchable(local):
            return (3, name)
        kind = local.kind.casefold()
        if kind in ("elf executable", "appimage", "windows executable"):
            return (0, name)
        if kind == "shell installer":
            return (1, name)
        return (2, name)

    def _launch_reason(self, local: LocalFile) -> str:
        kind = local.kind
        if kind == "Document":
            return "a document, not a game program"
        if kind == "Archive":
            return "an archive; unpack it first"
        if kind == "Image":
            return "an image"
        if kind == "Disc image":
            return "a disc image"
        if kind == "Windows executable":
            return "a Windows program"
        if kind in ("Debian package", "RPM package"):
            return "a package; install it first"
        if kind == "Desktop entry":
            return "a desktop shortcut"
        return "not an executable, AppImage, or script"

    def _launch_iid_for(self, local: LocalFile) -> str | None:
        for iid, candidate in self._lib_launch_files.items():
            if candidate.path == local.path:
                return iid
        return None

    def _populate_launch_choices(self, files: Sequence[LocalFile]) -> None:
        for radio in self._lib_launch_radios:
            try:
                radio.destroy()
            except tk.TclError:
                pass
        self._lib_launch_radios = []
        self._lib_launch_files = {}
        self.lib_launch_choice_var.set("")
        if not files:
            self.lib_launch_hint_var.set("No files to start.")
            return

        ranked = sorted(files, key=self._launch_sort_key)
        shown = ranked[:20]
        for index, local in enumerate(shown):
            iid = f"launch-{index}"
            self._lib_launch_files[iid] = local
            launchable = _local_launchable(local)
            text = local.path.name
            if not launchable:
                text = f"{text}  —  {self._launch_reason(local)}"
            radio = ttk.Radiobutton(
                self.lib_launch_box,
                text=text,
                value=iid,
                variable=self.lib_launch_choice_var,
                command=self._on_launch_choice_change,
            )
            radio.grid(row=index, column=0, sticky="w", padx=(8, 6), pady=1)
            if not launchable:
                radio.state(["disabled"])
            self._lib_launch_radios.append(radio)

        best = next(
            (
                iid for iid, local in self._lib_launch_files.items()
                if _local_launchable(local)
            ),
            None,
        )
        if best is not None:
            self.lib_launch_choice_var.set(best)
            self._on_launch_choice_change()
        extra = len(files) - len(shown)
        if best is None:
            self.lib_launch_hint_var.set(
                "No file here can start the game. See the reasons below."
            )
        elif extra > 0:
            self.lib_launch_hint_var.set(
                f"First {len(shown)} of {len(files)} files; the rest are listed below."
            )
        else:
            self.lib_launch_hint_var.set("The chosen file is reviewed before it starts.")

    def _on_launch_choice_change(self) -> None:
        local = self._lib_launch_files.get(self.lib_launch_choice_var.get())
        if local is not None:
            for iid, candidate in self._library_files.items():
                if candidate.path == local.path:
                    try:
                        self.lib_file_tree.selection_set(iid)
                        self.lib_file_tree.focus(iid)
                        self.lib_file_tree.see(iid)
                    except tk.TclError:
                        pass
                    break
        self._update_library_actions()

    def _chosen_launch_file(self) -> LocalFile | None:
        local = self._lib_launch_files.get(self.lib_launch_choice_var.get())
        if local is not None:
            return local
        return self._selected_library_file()

    def _update_library_actions(self) -> None:
        if not hasattr(self, "lib_open_btn"):
            return
        entry = self._library_entry
        folder = Path(entry.folder) if entry and entry.folder else None
        folder_ok = bool(folder is not None and folder.is_dir())
        local = self._selected_library_file()
        chosen = self._chosen_launch_file()

        def set_state(button: ttk.Button, enabled: bool) -> None:
            button.state(["!disabled"] if enabled else ["disabled"])

        set_state(self.lib_open_btn, folder_ok)
        set_state(self.lib_verify_btn, bool(local is not None and folder_ok))
        set_state(self.lib_fav_btn, entry is not None)
        set_state(self.lib_remove_btn, entry is not None)
        set_state(
            self.lib_launch_btn,
            bool(chosen is not None and folder_ok and _local_launchable(chosen)),
        )

    def _open_library_folder(self) -> None:
        entry = self._library_entry
        if entry is None or not entry.folder:
            return
        folder = Path(entry.folder)
        if not folder.is_dir():
            self._status("That game folder does not exist. Rescan after restoring it.", error=True)
            return
        if not platform_support.open_folder(folder):
            self._status("Could not open the folder.", error=True)

    def _open_library_source(self) -> None:
        entry = self._library_entry
        if entry is None:
            return
        url = entry.source_url
        if not url.startswith(("http://", "https://")) and entry.identifier:
            url = f"https://archive.org/details/{entry.identifier}"
        if not platform_support.open_url(url):
            self._status("Could not open the archive.org page in a browser.", error=True)

    # -- library: actions --------------------------------------------------

    def _toggle_favorite(self) -> None:
        entry = self._library_entry
        if entry is None:
            return
        new_value = self.library.toggle_favorite(entry.identifier)
        if new_value is None:
            self._status("That library entry no longer exists.", error=True)
            return
        self.lib_fav_btn.configure(
            text="Remove from favorites" if new_value else "Add to favorites"
        )
        self._refresh_library_table()
        self._status(
            f"{'Added' if new_value else 'Removed'} {entry.title or entry.identifier} "
            f"{'to' if new_value else 'from'} favorites."
        )

        def work() -> bool:
            return self.library.save()

        self._run_async(work, None, lambda exc: self._status(
            f"The favorite changed for this session, but saving failed: {_error_text(exc)}",
            error=True,
        ))

    def _rescan_library(self, startup: bool = False) -> None:
        if self._rescanning:
            self._status("A library scan is already running.")
            return
        self._rescanning = True
        self.lib_action_status_var.set("Scanning the download folder…")
        self.lib_action_status_label.configure(style="Small.TLabel")
        if not startup:
            self._status("Scanning the download folder…")

        def work() -> Any:
            return self.library.scan()

        def done(report: Any) -> None:
            self._rescanning = False
            self._refresh_compat_filter()
            if self._library_selected:
                self._library_entry = None
            self._refresh_library_table()
            bits: list[str] = []
            if report.scanned:
                bits.append(f"scanned {report.scanned} {_plural(report.scanned, 'folder')}")
            if report.added:
                bits.append(f"{report.added} added")
            if report.updated:
                bits.append(f"{report.updated} updated")
            if report.missing:
                bits.append(f"{report.missing} missing")
            if report.errors:
                bits.append(f"{len(report.errors)} {_plural(len(report.errors), 'error')}")
            if report.scanned == 0:
                message = (
                    "No game folders were found in the download folder."
                    if not report.errors else report.errors[0]
                )
            else:
                message = "Library scan finished: " + ", ".join(bits) + "."
            self._status(message, error=bool(report.errors))
            if self._library_entry is not None:
                self.lib_action_status_var.set(message)
                self.lib_action_status_label.configure(
                    style="Error.TLabel" if report.errors else "Small.TLabel"
                )

        def failed(exc: BaseException) -> None:
            self._rescanning = False
            self._status(f"Library scan failed: {_error_text(exc)}", error=True)
            if self._library_entry is not None:
                self.lib_action_status_var.set(
                    f"Library scan failed: {_error_text(exc)}"
                )
                self.lib_action_status_label.configure(style="Error.TLabel")

        self._run_async(work, done, failed)

    def _verify_integrity(self) -> None:
        if getattr(self, "_verifying", False):
            self._status("An integrity check is already running.")
            return
        entry = self._library_entry
        local = self._selected_library_file()
        if entry is None or local is None:
            self._status("Select a file in the game folder first.", error=True)
            return
        folder = Path(entry.folder)
        if not folder.is_dir():
            self._status("The game folder is missing, so nothing could be checked.", error=True)
            return

        self._verifying = True
        self.lib_verify_bar.configure(value=0)
        self.lib_verify_bar.grid()
        self.lib_action_status_var.set(f"Checking {local.path.name}…")
        self.lib_action_status_label.configure(style="Small.TLabel")
        self._status(f"Checking the integrity of {local.path.name}…")
        self._update_library_actions()

        identifier = entry.identifier
        local_path = local.path
        folder_path = folder
        client = self.client
        library = self.library

        def progress(done_bytes: int, total_bytes: int) -> None:
            self._post(lambda: self._verify_progress(done_bytes, total_bytes))

        def work() -> tuple[IntegrityResult, str | None, bool]:
            item = None
            metadata_error: str | None = None
            try:
                item = client.fetch_item(identifier)
            except Exception as exc:
                metadata_error = _error_text(exc)
            archive_file = None
            if item is not None:
                try:
                    relpath = local_path.resolve().relative_to(folder_path.resolve()).as_posix()
                except (OSError, ValueError):
                    relpath = local_path.name
                for candidate in item.files:
                    if candidate.name == relpath:
                        archive_file = candidate
                        break
                if archive_file is None:
                    for candidate in item.files:
                        if candidate.name.replace("\\", "/").rsplit("/", 1)[-1] == local_path.name:
                            archive_file = candidate
                            break
            result = verify_file(local_path, archive_file, progress=progress)
            if metadata_error is not None and archive_file is None:
                result = replace(
                    result,
                    status=IntegrityStatus.UNAVAILABLE,
                    message=(
                        "Published checksums could not be loaded, so no verification "
                        f"was performed. {short(metadata_error, 160)}"
                    ),
                )
            note: str | None = None
            current = library.get(identifier)
            if current is not None:
                current.integrity = result.status.value
                try:
                    library.save()
                except OSError as exc:
                    note = f"The result could not be saved: {_error_text(exc)}"
            return result, note, archive_file is not None

        def done(result_tuple: tuple[IntegrityResult, str | None, bool]) -> None:
            self._verifying = False
            self.lib_verify_bar.grid_remove()
            result, note, matched = result_tuple
            self.lib_integrity_var.set(
                INTEGRITY_LABELS.get(result.status.value, _capitalise(result.status.value))
            )
            message = result.message
            if not matched and result.status == IntegrityStatus.UNAVAILABLE:
                message = "No published checksum was available for this file. No claim of integrity is made."
            if note:
                message = f"{message} {note}"
            self.lib_action_status_var.set(message)
            is_bad = result.status in (IntegrityStatus.MISMATCH, IntegrityStatus.ERROR)
            self.lib_action_status_label.configure(
                style="Error.TLabel" if is_bad else "Small.TLabel"
            )
            self._status(short(message, 170), error=is_bad)
            self._update_library_actions()

        def failed(exc: BaseException) -> None:
            self._verifying = False
            self.lib_verify_bar.grid_remove()
            self.lib_action_status_var.set(
                f"Could not check this file: {_error_text(exc)}"
            )
            self.lib_action_status_label.configure(style="Error.TLabel")
            self._status(f"Could not check this file: {_error_text(exc)}", error=True)
            self._update_library_actions()

        self._run_async(work, done, failed)

    def _verify_progress(self, done_bytes: int, total_bytes: int) -> None:
        if not getattr(self, "_verifying", False):
            return
        if total_bytes > 0:
            self.lib_verify_bar.configure(value=min(100, done_bytes * 100 / total_bytes))
            self.lib_action_status_var.set(
                f"Checking… {human_size(done_bytes)} of {human_size(total_bytes)}"
            )

    def _export_library(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Export library",
            defaultextension=".json",
            initialfile="library-export.json",
            filetypes=(("JSON files", "*.json"), ("All files", "*.*")),
        )
        if not path:
            return
        target = Path(path)

        def work() -> int:
            return self.library.export_json(target)

        def done(count: int) -> None:
            self._status(f"Exported {count} {_plural(count, 'entry', 'entries')} to {target}.")
            if self._library_entry is not None:
                self.lib_action_status_var.set(
                    f"Exported {count} {_plural(count, 'entry', 'entries')} to {target}."
                )

        self._run_async(work, done, lambda exc: self._status(
            f"Could not export the library: {_error_text(exc)}", error=True
        ))

    def _import_library(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            title="Import library",
            filetypes=(("JSON files", "*.json"), ("All files", "*.*")),
        )
        if not path:
            return
        source = Path(path)
        self._status("Importing the library index…")

        def work() -> Any:
            return self.library.import_json(source, merge=True)

        def done(report: Any) -> None:
            self._library_entry = None
            self._refresh_compat_filter()
            self._refresh_library_table()
            message = (
                f"Imported {report.imported} {_plural(report.imported, 'entry', 'entries')}"
                f" · {report.skipped} skipped."
            )
            if report.errors:
                message += f" {report.errors[0]}"
            self._status(message, error=bool(report.errors))

        self._run_async(work, done, lambda exc: self._status(
            f"Could not import the library: {_error_text(exc)}", error=True
        ))

    # -- launching ---------------------------------------------------------

    def _review_and_launch(self) -> None:
        entry = self._library_entry
        local = self._chosen_launch_file()
        if entry is None or local is None:
            self._status("Choose a file to start the game first.", error=True)
            return
        if not _local_launchable(local):
            self._status("That file cannot start the game. Choose an executable or script.", error=True)
            return
        path = local.path
        if not path.is_file():
            self._status("That file no longer exists.", error=True)
            return
        if platform_support.is_admin():
            self._status(
                "Refusing to launch games while running as administrator. "
                "Start the app normally to launch them.",
                error=True,
            )
            return
        needs_chmod = (
            not platform_support.IS_WINDOWS
            and not is_shell(path.name)
            and not os.access(path, os.X_OK)
        )
        if not self._review_launch_dialog(path, local.kind, local.size, needs_chmod):
            self._status("The file was not launched.")
            return

        if needs_chmod:
            confirmed = messagebox.askyesno(
                "Add execute permission and launch?",
                "This sets the user execute bit on:\n\n"
                f"{path}\n\n"
                "Only continue if you have reviewed the file.",
                parent=self.root,
                default="no",
            )
            if not confirmed:
                self._status("The file was not launched.")
                return
            try:
                platform_support.make_executable(path)
            except OSError as exc:
                self._status(f"Could not change permissions: {_error_text(exc)}", error=True)
                return

        try:
            if platform_support.IS_WINDOWS:
                pid, log_path = self._windows_launch(path)
            else:
                pid, log_path = launch_reviewed_file(path, self.download_dir)
        except (LaunchError, OSError, ValueError) as exc:
            self._status(f"The file was not launched: {_error_text(exc)}", error=True)
            return

        message = f"Started {path.name} (pid {pid})."
        if log_path is not None:
            message += f" Log: {log_path}"
        self.lib_action_status_var.set(message)
        self.lib_action_status_label.configure(style="Small.TLabel")
        self._status(f"Launched {path.name} (pid {pid}).")

    def _review_launch_dialog(self, path: Path, kind: str, size: int,
                              needs_chmod: bool) -> bool:
        dialog = tk.Toplevel(self.root)
        dialog.title("Review downloaded file")
        dialog.configure(background=tk_style.BG)
        dialog.transient(self.root)
        dialog.resizable(False, False)
        state = {"approved": False}

        outer = ttk.Frame(dialog, style="App.TFrame", padding=18)
        outer.pack(fill="both", expand=True)

        warning = tk.Frame(
            outer, background=tk_style.WARNING_BG, highlightthickness=1,
            highlightbackground=tk_style.WARNING_BORDER, bd=0,
        )
        warning.pack(fill="x")
        warning_inner = ttk.Frame(warning, style="Banner.TFrame", padding=(12, 10))
        warning_inner.pack(fill="x")
        ttk.Label(
            warning_inner,
            text="This file is third-party software downloaded from archive.org. "
                 "Running it may modify files, install software, or start other "
                 "programs. Only launch it if you have reviewed it and trust its source.",
            style="Warning.TLabel",
            wraplength=self._w(470),
            justify="left",
        ).pack(anchor="w")

        card = tk.Frame(outer, background=tk_style.SURFACE, bd=0,
                        highlightthickness=1, highlightbackground=tk_style.BORDER)
        card.pack(fill="x", pady=(12, 0))
        body = ttk.Frame(card, style="Surface.TFrame", padding=14)
        body.pack(fill="x")
        body.columnconfigure(1, weight=1)
        fields = (
            ("File", path.name),
            ("Detected type", kind),
            ("Size", human_size(size)),
            ("Location", str(path)),
        )
        for row, (label, value) in enumerate(fields):
            ttk.Label(body, text=label, style="Caption.TLabel").grid(
                row=row, column=0, sticky="nw", padx=(0, 12), pady=(0, 5)
            )
            ttk.Label(
                body, text=value, style="Small.TLabel", anchor="w",
                justify="left", wraplength=self._w(400),
            ).grid(row=row, column=1, sticky="ew", pady=(0, 5))

        if needs_chmod:
            ttk.Label(
                outer,
                text="This file is not marked executable. After you continue, you "
                     "will be asked to confirm adding execute permission.",
                style="Small.TLabel",
                wraplength=self._w(500),
                justify="left",
            ).pack(anchor="w", pady=(10, 0))

        approved = tk.BooleanVar(value=False)
        check = ttk.Checkbutton(
            outer,
            text="I have reviewed this file and trust it.",
            variable=approved,
        )
        check.pack(anchor="w", pady=(12, 0))

        buttons = ttk.Frame(outer, style="App.TFrame")
        buttons.pack(fill="x", pady=(16, 0))
        cancel = ttk.Button(buttons, text="Cancel", command=dialog.destroy)
        cancel.pack(side="right")
        launch_btn = ttk.Button(buttons, text="Launch", style="Accent.TButton")
        launch_btn.pack(side="right", padx=(0, 8))

        def do_launch() -> None:
            if approved.get():
                state["approved"] = True
                dialog.destroy()

        launch_btn.configure(command=do_launch)
        launch_btn.state(["disabled"])
        approved.trace_add(
            "write",
            lambda *_a: launch_btn.state(
                ["!disabled"] if approved.get() else ["disabled"]
            ),
        )
        dialog.bind("<Escape>", lambda _e: dialog.destroy())
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.update_idletasks()
        width = dialog.winfo_reqwidth()
        height = dialog.winfo_reqheight()
        x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - width) // 2)
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - height) // 3)
        dialog.geometry(f"+{x}+{y}")
        check.focus_set()
        dialog.grab_set()
        dialog.wait_window()
        return state["approved"]

    def _windows_launch(self, path: Path) -> tuple[int, Path | None]:
        if not is_within(path, self.download_dir):
            raise LaunchError(
                "The file is outside the selected download folder. It was not launched."
            )
        if not path.is_file():
            raise LaunchError("The selected file no longer exists.")
        suffix = path.suffix.lower()
        if suffix in (".exe", ".com"):
            command = [str(path)]
            kind = "Windows executable"
        elif suffix in (".bat", ".cmd"):
            interpreter = os.environ.get("COMSPEC", "cmd.exe")
            command = [interpreter, "/c", str(path)]
            kind = "Windows batch file"
        elif suffix == ".msi":
            raise LaunchError(
                "Windows Installer packages are not launched from this app. "
                "Open the folder and review the installer yourself."
            )
        else:
            raise LaunchError(
                "Only .exe, .com, .bat, and .cmd files are launched from this app. "
                "The file was not started."
            )
        log_file, log_path = open_launch_log(path.parent)
        try:
            if log_file is not None:
                log_file.write(
                    f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} launching "
                    f"{path.name} ({kind}) ===\n"
                )
                log_file.write("command: " + " ".join(command) + "\n")
                log_file.flush()
            process = subprocess.Popen(
                command,
                cwd=str(path.parent),
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=log_file if log_file is not None else subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
                **platform_support.detached_kwargs(),
            )
        except OSError as exc:
            raise LaunchError(f"Could not start the program: {exc}") from exc
        finally:
            if log_file is not None:
                log_file.close()
        return process.pid, log_path

    # -- duplicates --------------------------------------------------------

    def _open_duplicates(self) -> None:
        window = self._duplicates_window
        if window is not None and window.winfo_exists():
            window.lift()
            window.focus_set()
            return

        window = tk.Toplevel(self.root)
        self._duplicates_window = window
        window.title("Duplicate files")
        window.configure(background=tk_style.BG)
        window.transient(self.root)
        window.geometry(f"{self._w(820)}x{self._w(520)}")
        window.minsize(self._w(640), self._w(420))

        outer = ttk.Frame(window, style="App.TFrame", padding=16)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Duplicate files", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(
            outer,
            text="Read-only check. Nothing is deleted or moved. Open a folder to "
                 "inspect the files yourself.",
            style="Small.TLabel",
            wraplength=self._w(760),
            justify="left",
        ).pack(anchor="w", pady=(2, 8))

        status_var = tk.StringVar(value="Scanning the library for duplicates…")
        ttk.Label(outer, textvariable=status_var, style="Small.TLabel",
                  wraplength=self._w(760), justify="left").pack(anchor="w", pady=(0, 8))

        table = tk.Frame(outer, background=tk_style.BORDER, bd=0)
        table.pack(fill="both", expand=True)
        inner = tk.Frame(table, background=tk_style.SURFACE, bd=0)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        tree = ttk.Treeview(
            inner, columns=("kind", "copies", "size", "example"),
            show="headings", selectmode="browse",
        )
        tree.heading("kind", text="Match", anchor="w")
        tree.heading("copies", text="Copies", anchor="e")
        tree.heading("size", text="Size", anchor="e")
        tree.heading("example", text="Example path", anchor="w")
        tree.column("kind", width=self._w(150), stretch=False)
        tree.column("copies", width=self._w(60), stretch=False, anchor="e")
        tree.column("size", width=self._w(82), stretch=False, anchor="e")
        tree.column("example", width=self._w(480), stretch=True)
        tree.tag_configure("odd", background=tk_style.STRIPE)
        scroll = ttk.Scrollbar(inner, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        paths_box = tk.Frame(outer, background=tk_style.BORDER, bd=0)
        paths_box.pack(fill="x", pady=(8, 0))
        paths_inner = tk.Frame(paths_box, background=tk_style.SURFACE, bd=0)
        paths_inner.pack(fill="both", expand=True, padx=1, pady=1)
        paths_text = tk.Text(
            paths_inner, height=5, wrap="none", bd=0, relief="flat",
            background=tk_style.SURFACE, foreground=tk_style.INK,
            font=self.fonts.small, padx=8, pady=6, state="disabled",
        )
        paths_text.pack(fill="x")

        buttons = ttk.Frame(outer, style="App.TFrame")
        buttons.pack(fill="x", pady=(10, 0))
        open_btn = ttk.Button(buttons, text="Open folder")
        open_btn.pack(side="left")
        open_btn.state(["disabled"])
        close_btn = ttk.Button(buttons, text="Close", command=window.destroy)
        close_btn.pack(side="right")
        groups: list[DuplicateGroup] = []

        def selected_group() -> DuplicateGroup | None:
            selection = tree.selection()
            if not selection:
                return None
            try:
                return groups[int(selection[0])]
            except (ValueError, IndexError):
                return None

        def show_group(_event: tk.Event | None = None) -> None:
            group = selected_group()
            open_btn.state(["!disabled"] if group else ["disabled"])
            paths_text.configure(state="normal")
            paths_text.delete("1.0", "end")
            if group:
                paths_text.insert("1.0", "\n".join(group.paths))
            paths_text.configure(state="disabled")

        def open_folder() -> None:
            group = selected_group()
            if group is None or not group.paths:
                return
            folder = Path(group.paths[0]).parent
            if not platform_support.open_folder(folder):
                status_var.set("Could not open that folder.")

        def done(result: list[DuplicateGroup]) -> None:
            if not window.winfo_exists():
                return
            groups.extend(result)
            for index, group in enumerate(result):
                kind = "Same name and size" if group.kind == "name-size" else "Same content (SHA-256)"
                example = group.paths[0] if group.paths else ""
                tree.insert(
                    "", "end", iid=str(index),
                    values=(kind, len(group.paths), human_size(group.size), example),
                    tags=("odd",) if index % 2 else (),
                )
            if result:
                status_var.set(f"{len(result)} {_plural(len(result), 'group')} found.")
            else:
                status_var.set("No duplicate files were found.")

        def failed(exc: BaseException) -> None:
            if window.winfo_exists():
                status_var.set(f"Could not scan for duplicates: {_error_text(exc)}")

        tree.bind("<<TreeviewSelect>>", show_group)
        open_btn.configure(command=open_folder)
        window.bind("<Escape>", lambda _e: window.destroy())
        window.protocol("WM_DELETE_WINDOW", window.destroy)
        self._run_async(self.library.find_duplicates, done, failed)

    # -- settings dialog ---------------------------------------------------

    def _open_settings(self) -> None:
        window = self._settings_window
        if window is not None and window.winfo_exists():
            window.lift()
            window.focus_set()
            return

        window = tk.Toplevel(self.root)
        self._settings_window = window
        window.title("Settings")
        window.configure(background=tk_style.BG)
        window.transient(self.root)
        window.resizable(False, True)
        window.geometry(f"{self._w(570)}x{self._w(880)}")
        window.minsize(self._w(520), self._w(520))

        outer = ttk.Frame(window, style="App.TFrame", padding=16)
        outer.pack(fill="both", expand=True)

        def card(title: str) -> ttk.Frame:
            frame = tk.Frame(outer, background=tk_style.SURFACE, bd=0,
                             highlightthickness=1, highlightbackground=tk_style.BORDER)
            frame.pack(fill="x", pady=(0, 10))
            body = ttk.Frame(frame, style="Surface.TFrame", padding=12)
            body.pack(fill="x")
            ttk.Label(body, text=title, style="Heading.TLabel").pack(anchor="w", pady=(0, 8))
            return body

        files_body = card("Download folder")
        folder_var = tk.StringVar(value=str(self.download_dir))
        folder_row = ttk.Frame(files_body, style="Surface.TFrame")
        folder_row.pack(fill="x")
        folder_row.columnconfigure(0, weight=1)
        folder_entry = tk.Entry(
            folder_row, textvariable=folder_var, state="readonly",
            readonlybackground=tk_style.SURFACE_ALT, foreground=tk_style.INK,
            relief="flat", bd=0, highlightthickness=1,
            highlightbackground=tk_style.BORDER, font=self.fonts.small,
        )
        folder_entry.grid(row=0, column=0, sticky="ew", ipady=4)
        chosen_folder = {"path": Path(self.download_dir)}

        def browse() -> None:
            initial = str(chosen_folder["path"])
            path = filedialog.askdirectory(
                parent=window, title="Choose download folder", initialdir=initial,
                mustexist=True,
            )
            if not path:
                return
            try:
                folder = check_download_dir(path)
            except (OSError, ValueError) as exc:
                error_var.set(f"Cannot use that folder: {_error_text(exc)}")
                return
            chosen_folder["path"] = folder
            folder_var.set(str(folder))
            error_var.set("")

        browse_btn = ttk.Button(folder_row, text="Browse…", command=browse)
        browse_btn.grid(row=0, column=1, padx=(8, 0))
        ttk.Label(
            files_body,
            text="Each game is saved in a folder named after its archive.org identifier.",
            style="Small.TLabel",
            wraplength=self._w(480),
            justify="left",
        ).pack(anchor="w", pady=(6, 0))

        ttk.Separator(files_body).pack(fill="x", pady=(10, 8))
        ttk.Label(files_body, text="aria2c path", style="FieldLabel.TLabel").pack(
            anchor="w"
        )
        aria2_var = tk.StringVar(value=self.settings.torrent_client)
        aria2_entry = ttk.Entry(files_body, textvariable=aria2_var)
        aria2_entry.pack(fill="x", pady=(3, 0))
        self._add_placeholder(aria2_entry, aria2_var, "Search automatically")
        ttk.Label(
            files_body,
            text="Path to aria2c. Leave empty to search PATH and the app folder. "
                 "Takes effect immediately when you save.",
            style="Small.TLabel",
            wraplength=self._w(480),
            justify="left",
        ).pack(anchor="w", pady=(6, 0))

        behaviour = card("Behaviour")
        notifications_var = tk.BooleanVar(
            value=bool(self.settings.get("notifications", True))
        )
        rescan_var = tk.BooleanVar(
            value=bool(self.settings.get("rescan_on_startup", True))
        )
        ttk.Checkbutton(
            behaviour, text="Show a notification when a download finishes",
            variable=notifications_var,
        ).pack(anchor="w", pady=(0, 4))
        ttk.Checkbutton(
            behaviour, text="Scan the download folder when the app starts",
            variable=rescan_var,
        ).pack(anchor="w")
        if platform_support.IS_WINDOWS:
            ttk.Label(
                behaviour,
                text="Windows notifications are not wired up in this build; the "
                     "setting is kept for compatibility.",
                style="Small.TLabel",
                wraplength=self._w(470),
                justify="left",
            ).pack(anchor="w", pady=(6, 0))

        appearance = card("Appearance")
        theme_var = tk.StringVar(value=self.settings.theme)
        theme_row = ttk.Frame(appearance, style="Surface.TFrame")
        theme_row.pack(anchor="w")
        for value, label in (("system", "System"), ("light", "Light"), ("dark", "Dark")):
            ttk.Radiobutton(
                theme_row, text=label, value=value, variable=theme_var,
            ).pack(side="left", padx=(0, 16))
        ttk.Label(
            appearance,
            text="Applies after restart.",
            style="Small.TLabel",
        ).pack(anchor="w", pady=(6, 0))

        locations = card("Locations")
        location_text = tk.Text(
            locations, height=5, wrap="none", bd=0, relief="flat",
            background=tk_style.SURFACE_ALT, foreground=tk_style.INK,
            font=self.fonts.mono, padx=8, pady=6,
            highlightthickness=1, highlightbackground=tk_style.BORDER,
        )
        location_text.pack(fill="x")
        location_text.insert(
            "1.0",
            "Config        " + str(platform_support.config_dir()) + "\n"
            "Data folder   " + str(platform_support.data_dir()) + "\n"
            "Library index " + str(platform_support.data_dir() / "library.json") + "\n"
            "Downloads     " + str(self.download_dir) + "\n"
            "Cache         " + str(platform_support.cache_dir()),
        )
        location_text.configure(state="disabled")
        ttk.Label(
            locations,
            text="These locations are read-only here. Select the text to copy a path.",
            style="Small.TLabel",
            wraplength=self._w(480),
            justify="left",
        ).pack(anchor="w", pady=(4, 0))

        shortcuts = card("Keyboard shortcuts")
        shortcut_rows = (
            ("Ctrl+1 / 2 / 3 / 4", "Switch to Browse, Downloads, Library, or Media"),
            ("Ctrl+F", "Focus the search box"),
            ("F5", "Refresh the collection, or rescan the library"),
            ("Ctrl+,", "Open Settings"),
            ("Escape", "Clear the search in the current tab"),
        )
        grid = ttk.Frame(shortcuts, style="Surface.TFrame")
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)
        for row, (keys, description) in enumerate(shortcut_rows):
            ttk.Label(grid, text=keys, style="Surface.TLabel",
                      font=self.fonts.mono).grid(
                row=row, column=0, sticky="nw", padx=(0, 14), pady=(0, 3)
            )
            ttk.Label(grid, text=description, style="Small.TLabel",
                      wraplength=self._w(380), justify="left").grid(
                row=row, column=1, sticky="nw", pady=(0, 3)
            )

        # Providers card (RAWG / TMDB / MAL API keys)
        providers = card("Providers")
        ttk.Label(
            providers,
            text="API keys are stored in the OS keyring. The plain text value is only "
                 "kept when the keyring is unavailable.",
            style="Small.TLabel",
            wraplength=self._w(480),
            justify="left",
        ).pack(anchor="w", pady=(0, 8))

        def provider_row(name: str, key: str) -> None:
            row = ttk.Frame(providers, style="Surface.TFrame")
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=name, style="FieldLabel.TLabel", width=14).pack(side="left")
            var = tk.StringVar(value=self.settings.api_key(key))
            entry = ttk.Entry(row, textvariable=var, show="•")
            entry.pack(side="left", fill="x", expand=True, padx=(6, 6))
            ttk.Button(
                row, text="Save", width=8,
                command=lambda k=key, v=var: self._save_provider_key(k, v, window)
            ).pack(side="left")
            ttk.Button(
                row, text="Clear", width=8,
                command=lambda k=key, v=var: self._clear_provider_key(k, v, window)
            ).pack(side="left", padx=(4, 0))

        provider_row("RAWG", "rawg")
        provider_row("TMDB", "tmdb")
        provider_row("MyAnimeList", "mal")

        # Media card (streaming provider, anime provider, audio, autoplay)
        media = card("Media")
        ttk.Label(media, text="Streaming provider", style="FieldLabel.TLabel").pack(anchor="w")
        streaming_provider_var = tk.StringVar(
            value=self.settings.get("streaming_provider", streaming.DEFAULT_PROVIDER)
        )
        streaming_combo = ttk.Combobox(
            media, state="readonly", textvariable=streaming_provider_var,
            values=list(streaming.PROVIDERS),
        )
        streaming_combo.pack(fill="x", pady=(2, 8))

        ttk.Label(media, text="Anime streaming provider", style="FieldLabel.TLabel").pack(anchor="w")
        anime_provider_var = tk.StringVar(
            value=self.settings.get("anime_streaming_provider", streaming.DEFAULT_ANIME_PROVIDER)
        )
        anime_combo = ttk.Combobox(
            media, state="readonly", textvariable=anime_provider_var,
            values=list(streaming.ANIME_PROVIDERS),
        )
        anime_combo.pack(fill="x", pady=(2, 8))

        ttk.Label(media, text="Default anime audio", style="FieldLabel.TLabel").pack(anchor="w")
        anime_lang_var = tk.StringVar(
            value=self.settings.get("anime_language", streaming.DEFAULT_LANGUAGE)
        )
        lang_combo = ttk.Combobox(
            media, state="readonly", textvariable=anime_lang_var,
            values=list(streaming.LANGUAGES.get(
                self.settings.get("anime_streaming_provider", streaming.DEFAULT_ANIME_PROVIDER),
                ("sub", "dub")
            )),
        )
        lang_combo.pack(fill="x", pady=(2, 8))

        autoplay_var = tk.BooleanVar(
            value=bool(self.settings.get("movie_autoplay", False))
        )
        ttk.Checkbutton(
            media, text="Autoplay when opening a title",
            variable=autoplay_var,
        ).pack(anchor="w", pady=(4, 0))

        error_var = tk.StringVar(value="")
        error_label = ttk.Label(
            outer, textvariable=error_var, style="Error.TLabel",
            wraplength=self._w(500), justify="left",
        )
        error_label.pack(anchor="w", pady=(0, 4))

        buttons = ttk.Frame(outer, style="App.TFrame")
        buttons.pack(fill="x", pady=(4, 0))
        save_btn = ttk.Button(buttons, text="Save changes", style="Accent.TButton")
        save_btn.pack(side="right")
        cancel_btn = ttk.Button(buttons, text="Cancel", command=window.destroy)
        cancel_btn.pack(side="right", padx=(0, 8))
        # Keep the reference above so the local function can use it.
        error_var_ref = error_var

        def save() -> None:
            folder = chosen_folder["path"]
            previous_theme = self.settings.theme
            try:
                folder = check_download_dir(folder)
            except (OSError, ValueError) as exc:
                error_var_ref.set(f"Cannot use that folder: {_error_text(exc)}")
                return
            try:
                self.settings.set_torrent_client(aria2_var.get())
                self.settings.update(
                    download_dir=str(folder),
                    notifications=bool(notifications_var.get()),
                    rescan_on_startup=bool(rescan_var.get()),
                    streaming_provider=streaming_provider_var.get(),
                    anime_streaming_provider=anime_provider_var.get(),
                    anime_language=anime_lang_var.get(),
                    movie_autoplay=bool(autoplay_var.get()),
                )
                self.settings.set_theme(theme_var.get())
            except ValueError as exc:
                error_var_ref.set(_error_text(exc))
                return
            except OSError as exc:
                error_var_ref.set(f"Could not save settings: {_error_text(exc)}")
                return
            self._apply_download_dir(folder)
            engine_note = ""
            try:
                info = self.torrents.set_engine_path(self.settings.torrent_client)
                if not info.available:
                    engine_note = (
                        " The torrent engine was not found, so magnet downloads "
                        "are unavailable until a path is set."
                    )
            except Exception:
                engine_note = ""
            window.destroy()
            self._refresh_torrents()
            message = "Settings saved." + engine_note
            if self.settings.theme != previous_theme:
                message = "Settings saved. The theme applies after restart." + engine_note
            self._status(message, error=bool(engine_note))

        save_btn.configure(command=save)

        def show_error(message: str) -> None:
            error_var_ref.set(message)

        # Wire the folder validation error path to the shared label.
        browse = browse  # keep flake-style linters quiet about the closure
        window.bind("<Escape>", lambda _e: window.destroy())
        window.protocol("WM_DELETE_WINDOW", window.destroy)
        window.update_idletasks()
        needed = window.winfo_reqheight()
        limit = max(self._w(520), window.winfo_screenheight() - self._w(56))
        if needed > window.winfo_height():
            window.geometry(f"{self._w(570)}x{min(needed, limit)}")
            window.update_idletasks()
        width = window.winfo_width()
        height = window.winfo_height()
        x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - width) // 2)
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - height) // 4)
        window.geometry(f"+{x}+{y}")
        window.grab_set()
        folder_entry.focus_set()

    # -- menus and keyboard ------------------------------------------------

    def _save_provider_key(self, provider: str, var: tk.StringVar, window: tk.Toplevel) -> None:
        value = var.get().strip()
        try:
            self.settings.set_api_key(provider, value)
            var.set(self.settings.api_key(provider))
            self._status(f"{provider} API key saved.")
        except Exception as exc:
            self._status(f"Could not save {provider} key: {exc}", error=True)

    def _clear_provider_key(self, provider: str, var: tk.StringVar, window: tk.Toplevel) -> None:
        try:
            self.settings.clear_api_key(provider)
            var.set("")
            self._status(f"{provider} API key cleared.")
        except Exception as exc:
            self._status(f"Could not clear {provider} key: {exc}", error=True)

    def _open_collection_page(self) -> None:
        if not platform_support.open_url(COLLECTION_URL):
            self._status("Could not open the collection page in a browser.", error=True)

    def _show_about(self) -> None:
        messagebox.showinfo(
            "About Lumora",
            f"Lumora {__version__}\n\n"
            "Browse the public Internet Archive collection "
            "native-linux-games-collection, download files to your own folder, "
            "and review them before anything is launched.\n\n"
            "This is the Windows frontend. The GTK application remains the Linux UI.",
            parent=self.root,
        )

    def _bind_shortcuts(self) -> None:
        self.root.bind("<Control-Key-1>", lambda _e: self._shortcut_tab(0))
        self.root.bind("<Control-Key-2>", lambda _e: self._shortcut_tab(1))
        self.root.bind("<Control-Key-3>", lambda _e: self._shortcut_tab(2))
        self.root.bind("<Control-Key-4>", lambda _e: self._shortcut_tab(3))
        self.root.bind("<Control-f>", self._shortcut_search)
        self.root.bind("<Control-F>", self._shortcut_search)
        self.root.bind("<Control-comma>", lambda _e: self._shortcut_settings())
        self.root.bind("<F5>", lambda _e: self._shortcut_refresh())
        self.root.bind("<Escape>", lambda _e: self._shortcut_escape())

    def _shortcut_tab(self, index: int) -> str:
        self._focus_tab(index)
        return "break"

    def _shortcut_search(self, _event: tk.Event | None = None) -> str:
        try:
            index = self.notebook.index(self.notebook.select())
        except tk.TclError:
            index = 0
        if index == 2:
            self.lib_search.focus_set()
            self.lib_search.select_range(0, "end")
        else:
            if index == 1:
                self._focus_tab(0)
            self.browse_search.focus_set()
            self.browse_search.select_range(0, "end")
        return "break"

    def _shortcut_settings(self) -> str:
        self._open_settings()
        return "break"

    def _on_f5(self) -> None:
        """Menu entry for Refresh; behaves like the F5 shortcut."""
        self._shortcut_refresh()

    def _shortcut_refresh(self) -> str:
        try:
            index = self.notebook.index(self.notebook.select())
        except tk.TclError:
            index = 0
        if index == 1:
            self._refresh_downloads()
            self._refresh_torrents()
        elif index == 2:
            self._rescan_library()
        else:
            self._load_collection(True)
        return "break"

    def _shortcut_escape(self) -> str:
        try:
            index = self.notebook.index(self.notebook.select())
        except tk.TclError:
            index = 0
        if index == 0 and self.browse_search_var.get():
            self._clear_browse_search()
            return "break"
        if index == 2 and self.lib_search_var.get():
            self._clear_library_search()
            return "break"
        return ""

    # -- shutdown ----------------------------------------------------------

    def _save_window_state(self) -> None:
        try:
            zoomed = self.root.state() == "zoomed"
        except tk.TclError:
            zoomed = False
        try:
            if zoomed:
                self.settings.set_maximized(True)
            else:
                self.settings.set_maximized(False)
                self.settings.set_window_size(
                    self.root.winfo_width(), self.root.winfo_height()
                )
        except (OSError, tk.TclError):
            pass

    def _on_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._downloads_dirty.clear()
        if self._torrent_after is not None:
            try:
                self.root.after_cancel(self._torrent_after)
            except tk.TclError:
                pass
            self._torrent_after = None
        self._save_window_state()
        try:
            self.torrents.close()
        except Exception:
            traceback.print_exc()
        try:
            self.manager.shutdown()
        except Exception:
            traceback.print_exc()
        try:
            self.root.destroy()
        except tk.TclError:
            pass


def main() -> int:
    platform_support.ensure_app_dirs()
    tk_style.enable_native_dpi_awareness()
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"Could not start the Tk interface: {exc}", file=sys.stderr)
        return 1
    WindowsApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
