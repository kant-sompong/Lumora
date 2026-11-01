"""Windows/Tk media tab — browse, search, and launch titles via browser fallback.

This tab mirrors the *structure* of the GTK Movies & TV page but does not embed
a WebKit player.  When the user presses "Watch", the streaming provider's URL
opens in the system browser instead.  All provider settings (RAWG, TMDB, MAL,
streaming provider, anime provider, audio language, autoplay) are edited in
Settings → Providers / Settings → Media so the Windows UI stays in sync with
the GTK configuration.
"""

from __future__ import annotations

import io
import os
import queue
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk
from typing import Any, Callable, Sequence, TYPE_CHECKING

if TYPE_CHECKING:
    from .windows_app import WindowsApp

from . import streaming
from . import tk_style
from .platform_support import open_url


@dataclass
class MediaItem:
    title: str
    year: str
    overview: str
    poster_url: str
    backdrop_url: str
    tmdb_id: int
    media_type: str  # "movie" or "tv"
    # For anime
    mal_id: int | None = None
    anilist_id: int | None = None
    episodes: int | None = None
    rating: float | None = None
    genres: tuple[str, ...] = ()
    status: str = ""


class MediaTab:
    """Media browse/playback surface for the Tk frontend."""

    POSTER_SIZE = (96, 144)
    THUMB_SIZE = (160, 90)

    def __init__(self, app: "WindowsApp", tab: ttk.Frame) -> None:
        self.app = app
        self.tab = tab
        self._ui_queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._current_items: list[MediaItem] = []
        self._selected_index: int | None = None

        self._build_ui()
        self._load_home()

    # --------------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        # Toolbar
        toolbar = ttk.Frame(self.tab, style="Toolbar.TFrame", padding=(14, 10))
        toolbar.pack(fill="x")
        toolbar.columnconfigure(1, weight=1)

        ttk.Label(toolbar, text="Search", style="FieldLabel.TLabel").grid(
            row=0, column=0, padx=(0, 10), sticky="w"
        )
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(toolbar, textvariable=self.search_var)
        self.search_entry.grid(row=0, column=1, sticky="ew")
        self.search_entry.bind("<Return>", lambda _e: self._search())
        self.app._add_placeholder(
            self.search_entry, self.search_var, "Movie, TV show, or anime"
        )

        ttk.Button(toolbar, text="Search", command=self._search).grid(
            row=0, column=2, padx=(10, 0)
        )
        ttk.Button(toolbar, text="Home", command=self._load_home).grid(
            row=0, column=3, padx=(8, 0)
        )
        ttk.Button(toolbar, text="Movies", command=lambda: self._load_category("movie")).grid(
            row=0, column=4, padx=(8, 0)
        )
        ttk.Button(toolbar, text="TV", command=lambda: self._load_category("tv")).grid(
            row=0, column=5, padx=(8, 0)
        )
        ttk.Button(toolbar, text="Anime", command=self._load_anime).grid(
            row=0, column=6, padx=(8, 0)
        )

        ttk.Separator(self.tab).pack(fill="x")

        # Paned window: left list, right detail
        self.pane = ttk.Panedwindow(self.tab, orient="horizontal")
        self.pane.pack(fill="both", expand=True)

        left = ttk.Frame(self.pane, style="Surface.TFrame")
        right = ttk.Frame(self.pane, style="Surface.TFrame")
        self.pane.add(left, weight=2)
        self.pane.add(right, weight=5)

        # Left: list of items
        left_head = ttk.Frame(left, style="Surface.TFrame", padding=(12, 10))
        left_head.pack(fill="x")
        ttk.Label(left_head, text="Results", style="Heading.TLabel").pack(anchor="w")
        self.count_var = tk.StringVar(value="Loading…")
        ttk.Label(left_head, textvariable=self.count_var, style="Small.TLabel").pack(
            anchor="w", pady=(2, 0)
        )

        self.list_frame = ttk.Frame(left, style="Surface.TFrame")
        self.list_frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.list_canvas = tk.Canvas(
            self.list_frame,
            background=tk_style.SURFACE,
            highlightthickness=0,
        )
        self.list_scroll = ttk.Scrollbar(
            self.list_frame, orient="vertical", command=self.list_canvas.yview
        )
        self.list_canvas.configure(yscrollcommand=self.list_scroll.set)
        self.list_scroll.pack(side="right", fill="y")
        self.list_canvas.pack(side="left", fill="both", expand=True)

        self.list_inner = ttk.Frame(self.list_canvas, style="Surface.TFrame")
        self.list_canvas.create_window((0, 0), window=self.list_inner, anchor="nw")
        self.list_inner.bind(
            "<Configure>",
            lambda _e: self.list_canvas.configure(scrollregion=self.list_canvas.bbox("all")),
        )
        self.list_canvas.bind("<MouseWheel>", self._on_mousewheel)

        # Right: detail
        right_head = ttk.Frame(right, style="Surface.TFrame", padding=(14, 10))
        right_head.pack(fill="x")
        self.detail_title = ttk.Label(right_head, text="", style="Heading.TLabel")
        self.detail_title.pack(anchor="w")
        self.detail_meta = ttk.Label(right_head, text="", style="Small.TLabel")
        self.detail_meta.pack(anchor="w", pady=(4, 0))

        ttk.Separator(right).pack(fill="x", padx=14)

        self.detail_overview = tk.Text(
            right,
            height=8,
            wrap="word",
            bd=0,
            relief="flat",
            background=tk_style.SURFACE_ALT,
            foreground=tk_style.INK,
            font=self.app.fonts.small,
            padx=10,
            pady=8,
        )
        self.detail_overview.pack(fill="x", padx=14, pady=(0, 8))
        self.detail_overview.configure(state="disabled")

        ttk.Separator(right).pack(fill="x", padx=14)

        # Action buttons
        action_frame = ttk.Frame(right, style="Surface.TFrame", padding=(14, 10))
        action_frame.pack(fill="x")
        self.watch_btn = ttk.Button(
            action_frame, text="Watch", style="Accent.TButton", state="disabled",
            command=self._watch_selected
        )
        self.watch_btn.pack(side="left")
        ttk.Button(
            action_frame, text="Open in browser", command=self._open_in_browser
        ).pack(side="left", padx=(8, 0))
        self.provider_var = tk.StringVar(value="")
        self.provider_combo = ttk.Combobox(
            action_frame, state="readonly", textvariable=self.provider_var,
            width=18,
        )
        self.provider_combo.pack(side="right")

    # ------------------------------------------------------------------ Data
    def _load_home(self) -> None:
        self.count_var.set("Loading trending…")
        self._run_worker(self._fetch_trending)

    def _load_category(self, media_type: str) -> None:
        self.count_var.set(f"Loading {media_type}s…")
        self._run_worker(lambda: self._fetch_category(media_type))

    def _load_anime(self) -> None:
        self.count_var.set("Loading popular anime…")
        self._run_worker(self._fetch_anime)

    def _search(self) -> None:
        query = self.search_var.get().strip()
        if not query:
            return
        self.count_var.set(f"Searching for “{query}”…")
        self._run_worker(lambda: self._fetch_search(query))

    def _run_worker(self, target: Callable[[], None]) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._worker = threading.Thread(target=target, daemon=True)
        self._worker.start()
        self.app.root.after(100, self._drain_queue)

    def _drain_queue(self) -> None:
        try:
            while True:
                fn = self._ui_queue.get_nowait()
                fn()
        except queue.Empty:
            pass
        if self._worker and self._worker.is_alive():
            self.app.root.after(100, self._drain_queue)

    def _post(self, fn: Callable[[], None]) -> None:
        self._ui_queue.put(fn)

    # ----------------------------------------------------------------- Network
    def _fetch_trending(self) -> None:
        from .tmdb import TmdbClient, TmdbError
        try:
            client = self._tmdb_client()
            items = client.trending(kind="movie", window="week")
            self._post(lambda: self._apply_items(items))
        except TmdbError as exc:
            self._post(lambda e=exc: self._show_error(f"Could not load trending: {e}"))

    def _fetch_category(self, media_type: str) -> None:
        from .tmdb import TmdbClient, TmdbError
        try:
            client = self._tmdb_client()
            if media_type == "movie":
                items = client.popular(kind="movie", page=1)
            else:
                items = client.popular(kind="tv", page=1)
            self._post(lambda: self._apply_items(items))
        except TmdbError as exc:
            self._post(lambda e=exc: self._show_error(f"Could not load {media_type}s: {e}"))

    def _fetch_search(self, query: str) -> None:
        from .tmdb import TmdbClient, TmdbError
        try:
            client = self._tmdb_client()
            items = client.search(query)
            self._post(lambda: self._apply_items(items))
        except TmdbError as exc:
            self._post(lambda e=exc: self._show_error(f"Search failed: {e}"))

    def _fetch_anime(self) -> None:
        from .mal import MalClient, MalError
        try:
            client = MalClient(self.app.settings.api_key("mal"))
            items = client.ranking("bypopularity", limit=20)
            self._post(lambda: self._apply_anime(items))
        except MalError as exc:
            self._post(lambda e=exc: self._show_error(f"Could not load anime: {e}"))

    def _tmdb_client(self):
        from .tmdb import TmdbClient
        api_key = self.app.settings.api_key("tmdb")
        return TmdbClient(api_key or None)

    # ----------------------------------------------------------------- Render
    def _apply_items(self, items) -> None:
        # Convert TMDB items to MediaItem
        self._current_items = [
            MediaItem(
                title=it.title,
                year=it.year,
                overview=it.overview,
                poster_url=it.poster_url,
                backdrop_url=it.backdrop_url,
                tmdb_id=it.tmdb_id,
                media_type=it.media_type,
            )
            for it in items
        ]
        self._render_list()
        self.count_var.set(f"{len(self._current_items)} titles")
        self._clear_detail()

    def _apply_anime(self, items) -> None:
        self._current_items = [
            MediaItem(
                title=it.title,
                year=it.year,
                overview=it.synopsis,
                poster_url=it.poster_url,
                backdrop_url=it.poster_url,  # MAL doesn't provide backdrops
                tmdb_id=0,
                media_type="anime",
                mal_id=it.id,
                episodes=it.episodes,
                rating=it.rating,
            )
            for it in items
        ]
        self._render_list()
        self.count_var.set(f"{len(self._current_items)} anime")
        self._clear_detail()

    def _render_list(self) -> None:
        for child in self.list_inner.winfo_children():
            child.destroy()

        for idx, item in enumerate(self._current_items):
            row = ttk.Frame(self.list_inner, style="Surface.TFrame")
            row.pack(fill="x", pady=4)
            row.bind("<Button-1>", lambda _e, i=idx: self._select_item(i))
            row.bind("<Enter>", lambda _e, r=row: r.configure(style="Hover.TFrame"))
            row.bind("<Leave>", lambda _e, r=row: r.configure(style="Surface.TFrame"))

            # Title + year
            title_text = item.title
            if item.year:
                title_text += f" ({item.year})"
            if item.media_type == "anime":
                title_text += " 🎌"

            ttk.Label(row, text=title_text, style="Small.TLabel", anchor="w").pack(
                fill="x", padx=8, pady=4
            )

    def _select_item(self, idx: int) -> None:
        if idx >= len(self._current_items):
            return
        self._selected_index = idx
        item = self._current_items[idx]
        self.detail_title.configure(text=item.title)
        meta_parts = []
        if item.year:
            meta_parts.append(item.year)
        if item.media_type == "tv" and item.episodes:
            meta_parts.append(f"{item.episodes} eps")
        if item.rating:
            meta_parts.append(f"★ {item.rating:.1f}")
        if item.genres:
            meta_parts.append(" · ".join(item.genres[:3]))
        self.detail_meta.configure(text=" | ".join(meta_parts))

        self.detail_overview.configure(state="normal")
        self.detail_overview.delete("1.0", "end")
        self.detail_overview.insert("1.0", item.overview or "No description available.")
        self.detail_overview.configure(state="disabled")

        # Update provider combo
        providers = self._available_providers(item)
        self.provider_combo.configure(values=providers)
        if providers:
            self.provider_var.set(providers[0])
            self.watch_btn.configure(state="normal")
        else:
            self.provider_var.set("")
            self.watch_btn.configure(state="disabled")

    def _clear_detail(self) -> None:
        self.detail_title.configure(text="")
        self.detail_meta.configure(text="")
        self.detail_overview.configure(state="normal")
        self.detail_overview.delete("1.0", "end")
        self.detail_overview.configure(state="disabled")
        self.provider_combo.configure(values=[])
        self.provider_var.set("")
        self.watch_btn.configure(state="disabled")
        self._selected_index = None

    def _available_providers(self, item: MediaItem) -> list[str]:
        """Return the list of providers that can play this item."""
        provider = self.app.settings.get("streaming_provider", streaming.DEFAULT_PROVIDER)
        anime_provider = self.app.settings.get("anime_streaming_provider", streaming.DEFAULT_ANIME_PROVIDER)
        result = []

        if item.media_type == "anime":
            if streaming.has_anime_route(anime_provider):
                result.append(anime_provider)
            # Fallback to TMDB providers if anime provider can't play
            for p in streaming.fallback_providers(provider):
                if p not in result:
                    result.append(p)
        else:
            result.append(provider)
            for p in streaming.fallback_providers(provider):
                if p not in result:
                    result.append(p)
        return result

    def _watch_selected(self) -> None:
        if self._selected_index is None:
            return
        item = self._current_items[self._selected_index]
        provider = self.provider_var.get() or self._available_providers(item)[0]
        url = self._build_playback_url(item, provider)
        if not url:
            self.app._status("Could not build playback URL for this title.", error=True)
            return
        if open_url(url):
            self.app._status(f"Opened {streaming.LABELS.get(provider, provider)} in browser.")
        else:
            self.app._status("Could not open browser.", error=True)

    def _open_in_browser(self) -> None:
        self._watch_selected()

    def _build_playback_url(self, item: MediaItem, provider: str) -> str | None:
        try:
            if item.media_type == "anime":
                if provider == streaming.ANI_PROVIDER:
                    if item.mal_id:
                        lang = self.app.settings.get("anime_language", "sub")
                        return streaming.ani_mal_url(item.mal_id, 1, language=lang)
                    return None
                # Fallback: try TMDB providers via TMDB search
                if item.tmdb_id == 0:
                    return None
                return streaming.anime_url(provider, item.tmdb_id, 1)  # best effort
            elif item.media_type == "tv":
                return streaming.episode_url(provider, item.tmdb_id, 1, 1, dub=False)
            else:
                return streaming.movie_url(provider, item.tmdb_id, dub=False)
        except Exception:
            return None

    def _show_error(self, message: str) -> None:
        self.count_var.set("Error")
        self.app._status(message, error=True)

    def _on_mousewheel(self, event):
        self.list_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")


# -----------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------

def build_media_tab(app: "WindowsApp", tab: ttk.Frame) -> MediaTab:
    """Factory used by WindowsApp._build_media_tab."""
    return MediaTab(app, tab)