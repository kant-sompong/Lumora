"""Settings and About dialogs for the Qt (Windows) frontend.

The layout follows the GTK settings sheet: a tab strip across the top, rows with
a bold label and a muted explanation on the left and the control on the right,
and a single Close button in the corner.  Nothing is written until a control
changes, exactly like the Linux build.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import __version__, mal, platform_support, streaming, theme, tmdb
from .qt_widgets import Toast, label, run_async
from .settings import Settings
from .sources import RawgSource

TABS = (("general", "General"), ("locations", "Locations"),
        ("providers", "Providers"), ("media", "Media"), ("shortcuts", "Shortcuts"))


class SettingRow(QWidget):
    """Bold title, muted explanation, control on the right."""

    def __init__(self, title: str, description: str = "", control: QWidget | None = None) -> None:
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 6, 0, 6)
        row.setSpacing(16)
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(label(title, "settLabel"))
        if description:
            text.addWidget(label(description, "settDesc", wrap=True))
        row.addLayout(text, 1)
        if control is not None:
            row.addWidget(control, 0, Qt.AlignVCenter)


class SettingsDialog(QDialog):
    """The five-tab preferences sheet."""

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setObjectName("sheet")
        self.setWindowTitle("Settings")
        self.resize(700, 590)
        self._stack = QStackedWidget()
        self._tabs: dict[str, QPushButton] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QFrame()
        header.setObjectName("sheetHeader")
        head = QHBoxLayout(header)
        head.setContentsMargins(16, 10, 16, 10)
        head.setSpacing(2)
        for key, text in TABS:
            button = QPushButton(text)
            button.setObjectName("sheetTab")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _=False, k=key: self.show_tab(k))
            head.addWidget(button)
            self._tabs[key] = button
        head.addStretch(1)
        outer.addWidget(header)
        outer.addWidget(self._stack, 1)

        self._stack.addWidget(self._scrolled(self._build_general()))
        self._stack.addWidget(self._scrolled(self._build_locations()))
        self._stack.addWidget(self._scrolled(self._build_providers()))
        self._stack.addWidget(self._scrolled(self._build_media()))
        self._stack.addWidget(self._scrolled(self._build_shortcuts()))

        footer = QWidget()
        foot = QHBoxLayout(footer)
        foot.setContentsMargins(20, 10, 20, 14)
        foot.addStretch(1)
        close = QPushButton("Close")
        close.setMinimumWidth(90)
        close.clicked.connect(self.accept)
        foot.addWidget(close)
        outer.addWidget(footer)
        self.show_tab("general")

    # scaffolding -----------------------------------------------------------
    def _scrolled(self, body: QWidget) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body.setObjectName("pageBody")
        scroll.setWidget(body)
        return scroll

    def _page(self) -> tuple[QWidget, QVBoxLayout]:
        body = QWidget()
        column = QVBoxLayout(body)
        column.setContentsMargins(28, 24, 28, 24)
        column.setSpacing(8)
        return body, column

    def show_tab(self, key: str) -> None:
        order = [name for name, _text in TABS]
        for name, button in self._tabs.items():
            button.setChecked(name == key)
        if key in order:
            self._stack.setCurrentIndex(order.index(key))

    def _heading(self, column: QVBoxLayout, text: str) -> None:
        head = label(text, "pageTitle")
        head.setContentsMargins(0, 0, 0, 4)
        column.addWidget(head)

    def _divider(self, column: QVBoxLayout) -> QWidget:
        line = QFrame()
        line.setObjectName("settDivider")
        line.setFixedHeight(1)
        line.setContentsMargins(0, 6, 0, 6)
        column.addWidget(line)
        return line

    # general ---------------------------------------------------------------
    def _build_general(self) -> QWidget:
        body, column = self._page()
        self._heading(column, "Downloads")

        folder_row = QWidget()
        folder = QHBoxLayout(folder_row)
        folder.setContentsMargins(0, 0, 0, 0)
        folder.setSpacing(8)
        self._folder_value = label(
            str(self.settings.download_dir or platform_support.default_download_dir()),
            "settPath",
        )
        folder.addWidget(self._folder_value)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._pick_folder)
        folder.addWidget(browse)
        column.addWidget(SettingRow(
            "Download folder", "Where new games are saved.", folder_row
        ))

        if platform_support.notification_backend():
            notify = QCheckBox()
            notify.setChecked(bool(self.settings.get("desktop_notifications", True)))
            notify.toggled.connect(
                lambda on: self.settings.set("desktop_notifications", bool(on))
            )
            column.addWidget(SettingRow(
                "Desktop notifications",
                "Show a system notification when a download finishes or fails.",
                notify,
            ))

        rescan = QCheckBox()
        rescan.setChecked(bool(self.settings.get("rescan_on_startup", True)))
        rescan.toggled.connect(lambda on: self.settings.set("rescan_on_startup", bool(on)))
        column.addWidget(SettingRow(
            "Rescan library on startup",
            "Check the download folder for new game folders when the app opens.",
            rescan,
        ))

        retries = self._spin(0, 10, self.settings.download_retries)
        retries.valueChanged.connect(self.settings.set_download_retries)
        column.addWidget(SettingRow(
            "Retry a failed download",
            "Times a failed or cancelled download can be put back in the queue.",
            retries,
        ))

        timeout = self._spin(5, 300, self.settings.network_timeout)
        timeout.setSingleStep(5)
        timeout.valueChanged.connect(self.settings.set_network_timeout)
        column.addWidget(SettingRow(
            "Network timeout",
            "Seconds to wait for a server before giving up.",
            timeout,
        ))

        engine = QWidget()
        engine_row = QHBoxLayout(engine)
        engine_row.setContentsMargins(0, 0, 0, 0)
        engine_row.setSpacing(8)
        self._torrent_path = QLineEdit(self.settings.torrent_client)
        self._torrent_path.setPlaceholderText("Built-in engine")
        self._torrent_path.setFixedWidth(220)
        self._torrent_path.editingFinished.connect(self._save_torrent_path)
        engine_row.addWidget(self._torrent_path)
        pick = QPushButton("Browse…")
        pick.clicked.connect(self._pick_engine)
        engine_row.addWidget(pick)
        column.addWidget(SettingRow(
            "aria2c path",
            "Optional external engine. Leave empty to use the built-in BitTorrent engine.",
            engine,
        ))

        self._divider(column)
        self._heading(column, "Appearance")
        self._theme_buttons: dict[str, QRadioButton] = {}
        for key, text, description in (
            ("system", "System", "Follow the desktop's light or dark preference."),
            ("light", "Light", "Use a light palette for this window."),
            ("dark", "Dark", "Use a dark palette for this window."),
        ):
            radio = QRadioButton()
            radio.setChecked(theme.normalize_mode(self.settings.theme) == key)
            radio.toggled.connect(lambda on, k=key: self._set_theme(k) if on else None)
            self._theme_buttons[key] = radio
            column.addWidget(SettingRow(text, description, radio))

        missing = QCheckBox()
        missing.setChecked(self.settings.library_show_missing)
        missing.toggled.connect(self.settings.set_library_show_missing)
        column.addWidget(SettingRow(
            "Show missing library entries",
            "Keep rows for game folders that are no longer on disk.",
            missing,
        ))
        column.addWidget(label(
            "Nothing is launched, downloaded, or deleted without your confirmation. "
            "The library index only records what is already on disk.",
            "settDesc", wrap=True,
        ))
        column.addStretch(1)
        return body

    def _spin(self, low: int, high: int, value: int) -> QSpinBox:
        box = QSpinBox()
        box.setRange(low, high)
        box.setValue(int(value))
        box.setFixedWidth(84)
        box.setButtonSymbols(QSpinBox.UpDownArrows)
        return box

    def _set_theme(self, mode: str) -> None:
        self.settings.set_theme(mode)
        app = self.parent().window().windowHandle() if self.parent() else None
        window = self.parent()
        apply = getattr(window, "apply_theme", None)
        if callable(apply):
            apply(mode)

    def _pick_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Choose the download folder",
            str(self.settings.download_dir or platform_support.default_download_dir()),
        )
        if path:
            self.settings.set_download_dir(path)
            self._folder_value.setText(path)
            window = self.parent()
            update = getattr(window, "download_dir_changed", None)
            if callable(update):
                update(Path(path))

    def _save_torrent_path(self) -> None:
        try:
            self.settings.set_torrent_client(self._torrent_path.text())
        except ValueError:
            self._torrent_path.setText(self.settings.torrent_client)

    def _pick_engine(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "Choose an aria2c executable", "", "Executables (*.exe);;All files (*)"
        )
        if path:
            self._torrent_path.setText(path)
            self._save_torrent_path()

    # locations -------------------------------------------------------------
    def _build_locations(self) -> QWidget:
        body, column = self._page()
        self._heading(column, "Where things are kept")
        rows = (
            ("Settings file", str(platform_support.config_dir() / "config.json")),
            ("Data folder", str(platform_support.data_dir())),
            ("Cache", str(platform_support.cache_dir())),
            ("Download folder", str(self.settings.download_dir or platform_support.default_download_dir())),
        )
        for title, value in rows:
            column.addWidget(SettingRow(title, value))
        column.addWidget(label(
            "These locations are read-only here. They follow the system's standard "
            "folders for application data and settings.", "settDesc", wrap=True,
        ))
        column.addStretch(1)
        return body

    # providers -------------------------------------------------------------
    def _build_providers(self) -> QWidget:
        body, column = self._page()
        self._heading(column, "Metadata providers")
        column.addWidget(label(
            "Keys are stored in the Windows Credential Manager when it is available, "
            "otherwise in this user's settings file with owner permissions. They are "
            "never placed in the catalog, source code, logs, or error messages. A key "
            "reports as working only after a live connection test succeeds.",
            "settDesc", wrap=True,
        ))
        self._provider_status: dict[str, QLabel] = {}
        self._provider_fields: dict[str, QLineEdit] = {}
        specs = (
            ("rawg", "RAWG — metadata only",
             "RAWG supplies titles, release dates, genres, platforms, developers, "
             "publishers, descriptions, ratings, and cover URLs. It does not provide "
             "game files; store links are metadata, not downloads.",
             "RAWG API key (blank = use the saved key)"),
            ("tmdb", "TMDB — movies & TV metadata",
             "TMDB supplies movie and TV show titles, release dates, genres, "
             "descriptions, ratings, posters, and backdrops. It does not provide video "
             "files; playback is handled separately.",
             "TMDB API key (blank = use the saved key)"),
            ("mal", "MyAnimeList — anime metadata",
             "MyAnimeList supplies anime titles, synopsis, episode counts, airing "
             "status, genres, scores, release dates, and cover artwork. Only the public "
             "read API is used, authenticated with your Client ID.",
             "MAL Client ID (blank = use the saved key)"),
        )
        for key, title, description, placeholder in specs:
            self._divider(column)
            head = label(title, "settLabel")
            column.addWidget(head)
            column.addWidget(label(description, "settDesc", wrap=True))
            if key == "rawg":
                helper = QHBoxLayout()
                helper.setSpacing(8)
                helper.addWidget(label(
                    "A free key is enough: register at rawg.io/apidocs and copy the "
                    "API key from the dashboard.",
                    "settDesc", wrap=True,
                ), 1)
                get = QPushButton("Get a RAWG key")
                get.setCursor(Qt.PointingHandCursor)
                get.clicked.connect(
                    lambda: platform_support.open_url("https://rawg.io/apidocs")
                )
                helper.addWidget(get)
                column.addLayout(helper)
            field = QLineEdit()
            field.setPlaceholderText(placeholder)
            field.setEchoMode(QLineEdit.Password)
            self._provider_fields[key] = field
            column.addWidget(field)
            buttons = QWidget()
            button_row = QHBoxLayout(buttons)
            button_row.setContentsMargins(0, 0, 0, 0)
            button_row.setSpacing(8)
            save = QPushButton("Save & test")
            save.clicked.connect(lambda _=False, k=key: self._save_provider(k))
            clear = QPushButton("Clear")
            clear.clicked.connect(lambda _=False, k=key: self._clear_provider(k))
            button_row.addWidget(save)
            button_row.addWidget(clear)
            button_row.addStretch(1)
            column.addWidget(buttons)
            status = label(
                "A key is saved in the owner-only settings file. It has not been tested "
                "in this session." if self.settings.has_api_key(key)
                else "No key is saved yet.",
                "settDesc", wrap=True,
            )
            self._provider_status[key] = status
            column.addWidget(status)
        column.addStretch(1)
        return body

    def _save_provider(self, key: str) -> None:
        field = self._provider_fields[key]
        value = field.text().strip()
        status = self._provider_status[key]
        if not value:
            status.setText("Nothing was typed, so the saved key was left unchanged.")
            return
        try:
            self.settings.set_api_key(key, value)
        except Exception as exc:  # noqa: BLE001 - reported in the row
            status.setText(f"The key was not saved: {exc}")
            return
        field.clear()
        status.setText("Saved. Testing the connection…")
        timeout = float(self.settings.network_timeout)

        def work() -> str:
            if key == "tmdb":
                return tmdb.TmdbClient(value, timeout=timeout).test_connection()
            if key == "mal":
                return mal.MalClient(value, timeout=timeout).test_connection()
            if key == "rawg":
                return RawgSource(value, timeout=timeout).test_connection()
            return "The key was saved to this user's settings file."

        def done(message: object) -> None:
            status.setText(str(message))

        def failed(exc: object) -> None:
            status.setText(f"Saved, but the test failed: {exc}")

        run_async(work, done, failed)

    def _clear_provider(self, key: str) -> None:
        self.settings.clear_api_key(key)
        self._provider_fields[key].clear()
        self._provider_status[key].setText("The saved key was removed.")

    # media -----------------------------------------------------------------
    def _build_media(self) -> QWidget:
        body, column = self._page()
        self._heading(column, "Media & playback")
        column.addWidget(label("Providers", "settLabel"))

        self._streaming = QComboBox()
        for key in streaming.PROVIDERS:
            self._streaming.addItem(streaming.LABELS.get(key, key), key)
        current = streaming.clean_provider(
            self.settings.get("streaming_provider", streaming.DEFAULT_PROVIDER)
        )
        self._streaming.setCurrentIndex(max(0, self._streaming.findData(current)))
        self._streaming.setFixedWidth(190)
        self._streaming.currentIndexChanged.connect(
            lambda: self.settings.set("streaming_provider", self._streaming.currentData())
        )
        column.addWidget(SettingRow(
            "Streaming provider", "Provider used for movies and TV playback.",
            self._streaming,
        ))

        self._anime_provider = QComboBox()
        for key in streaming.ANIME_PROVIDERS:
            self._anime_provider.addItem(streaming.LABELS.get(key, key), key)
        anime_current = streaming.clean_anime_provider(
            self.settings.get("anime_streaming_provider",
                              self.settings.get("streaming_provider",
                                                streaming.DEFAULT_ANIME_PROVIDER))
        )
        self._anime_provider.setCurrentIndex(
            max(0, self._anime_provider.findData(anime_current))
        )
        self._anime_provider.setFixedWidth(190)
        self._anime_provider.currentIndexChanged.connect(
            lambda: self.settings.set(
                "anime_streaming_provider", self._anime_provider.currentData()
            )
        )
        column.addWidget(SettingRow(
            "Anime provider",
            "Provider used for anime playback. ani.pm plays from an AniList or "
            "MyAnimeList id and needs no key (https://ani.pm/developers).",
            self._anime_provider,
        ))

        column.addWidget(label("Playback", "settLabel"))
        self._audio = QComboBox()
        for key in ("sub", "dub", "vostfr"):
            self._audio.addItem(streaming.LANGUAGE_LABELS.get(key, key), key)
        language = streaming.clean_language(
            "ani",
            self.settings.get("anime_language", "")
            or ("dub" if self.settings.get("anime_dub", False) else "sub"),
        )
        self._audio.setCurrentIndex(max(0, self._audio.findData(language)))
        self._audio.setFixedWidth(190)
        self._audio.currentIndexChanged.connect(self._save_audio)
        column.addWidget(SettingRow(
            "Default audio", "Sub, dub or VOSTFR where the anime provider offers it.",
            self._audio,
        ))

        autoplay = QCheckBox()
        autoplay.setChecked(bool(self.settings.get("media_autoplay", True)))
        autoplay.toggled.connect(lambda on: self.settings.set("media_autoplay", bool(on)))
        column.addWidget(SettingRow(
            "Autoplay", "Start playing as soon as an episode or movie opens.", autoplay
        ))
        column.addStretch(1)
        return body

    def _save_audio(self) -> None:
        choice = str(self._audio.currentData())
        self.settings.set("anime_language", choice)
        self.settings.set("anime_dub", choice == "dub")

    # shortcuts -------------------------------------------------------------
    def _build_shortcuts(self) -> QWidget:
        body, column = self._page()
        self._heading(column, "Keyboard shortcuts")
        grid = QWidget()
        rows = QVBoxLayout(grid)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(8)
        for keys, description in (
            ("Ctrl+1", "Show the Browse view"),
            ("Ctrl+2", "Show the Library view"),
            ("Ctrl+3", "Open Downloads"),
            ("Ctrl+4", "Open Movies & TV"),
            ("Ctrl+F", "Focus the search field"),
            ("Ctrl+R / F5", "Reload the collection or rescan the library"),
            ("Ctrl+,", "Open Settings"),
            ("Escape", "Close the open game, filter sheet, or search"),
        ):
            row = QHBoxLayout()
            key_label = label(keys, "settLabel")
            key_label.setFixedWidth(120)
            row.addWidget(key_label)
            row.addWidget(label(description, "settDesc"), 1)
            rows.addLayout(row)
        column.addWidget(grid)
        column.addStretch(1)
        return body


class AboutDialog(QDialog):
    """App name, version, what it does, and where to find things."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sheet")
        self.setWindowTitle("About Lumora")
        self.setMinimumWidth(520)
        column = QVBoxLayout(self)
        column.setContentsMargins(28, 26, 28, 20)
        column.setSpacing(8)
        column.addWidget(label("Lumora", "pageTitle"))
        column.addWidget(label(f"Version {__version__}", "settDesc"))
        column.addSpacing(6)
        column.addWidget(label(
            "A desktop library for playing, downloading and organising games, "
            "with a Movies & TV section for everything else. Linux runs the GTK "
            "frontend; Windows runs this one and shares the same core.",
            "settDesc", wrap=True,
        ))
        column.addSpacing(8)
        for title, value in (
            ("Collection", "archive.org · native-linux-games-collection"),
            ("Metadata", "RAWG · TMDB · MyAnimeList"),
            ("License", "MIT (third-party components listed in the README)"),
        ):
            row = QHBoxLayout()
            head = label(title, "settLabel")
            head.setFixedWidth(110)
            row.addWidget(head, 0, Qt.AlignTop)
            row.addWidget(label(value, "settDesc", wrap=True), 1)
            column.addLayout(row)
        column.addSpacing(8)
        column.addWidget(label(
            "Nothing is launched, downloaded or deleted without your confirmation.",
            "settDesc", wrap=True,
        ))
        row = QHBoxLayout()
        row.addStretch(1)
        git = QPushButton("Open project page")
        git.clicked.connect(
            lambda: platform_support.open_url("https://github.com/kant-sompong/Lumora")
        )
        row.addWidget(git)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        column.addSpacing(10)
        column.addLayout(row)


__all__ = ["AboutDialog", "SettingsDialog"]
