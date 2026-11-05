"""Declarative panels for the features added in 0.6.6.

The GTK and Qt windows are very different codebases, so everything new is exposed
through one model: a list of sections, each with rows that either report state or
carry an action.  Each frontend only has to render :class:`Row` and forward the
action id to :func:`apply`, which keeps the behaviour identical on both platforms.

Nothing here touches a toolkit, and nothing here performs network or file work
beyond what the settings need.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from . import (
    anime_calendar,
    backup,
    download_dashboard,
    game_sources,
    history_sync,
    home_layout,
    local_media,
    notifications,
    playback,
    plugins as plugins_mod,
    profiles as profiles_mod,
    providers,
    updates,
    view_prefs,
)

INFO = "info"
TOGGLE = "toggle"
BUTTON = "button"
CHOICE = "choice"
LIST = "list"


@dataclass
class Row:
    label: str
    kind: str = INFO
    value: str = ""
    description: str = ""
    action: str = ""
    options: tuple[tuple[str, str], ...] = ()
    state: bool | None = None


@dataclass
class Section:
    key: str
    title: str
    description: str = ""
    rows: list[Row] = field(default_factory=list)


@dataclass
class Panel:
    sections: list[Section] = field(default_factory=list)

    def section(self, key: str) -> Section | None:
        for item in self.sections:
            if item.key == key:
                return item
        return None

    def action(self, action: str) -> Row | None:
        for section in self.sections:
            for row in section.rows:
                if row.action == action:
                    return row
        return None

    def actions(self) -> list[str]:
        return [row.action for section in self.sections for row in section.rows if row.action]


def _bucket(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def build(
    settings: Any,
    *,
    plugins: Sequence[plugins_mod.Plugin] | None = None,
    rejected_plugins: Sequence[plugins_mod.Rejected] = (),
    upcoming: Sequence[anime_calendar.Entry] = (),
    media_roots: Iterable[Path | str] = (),
    profile_store: profiles_mod.Profiles | None = None,
    backup_dir: Path | str | None = None,
    diagnostics_path: Path | str | None = None,
) -> Panel:
    panel = Panel()
    panel.sections.append(_providers(settings))
    panel.sections.append(_downloads(settings))
    panel.sections.append(_notifications(settings))
    panel.sections.append(_home(settings))
    panel.sections.append(_interface(settings))
    panel.sections.append(_profiles(settings, profile_store))
    panel.sections.append(_library(settings, media_roots))
    panel.sections.append(_calendar(settings, upcoming))
    panel.sections.append(_sync(settings))
    panel.sections.append(_plugins(plugins, rejected_plugins))
    panel.sections.append(_updates(settings, backup_dir, diagnostics_path))
    return panel


def _providers(settings: Any) -> Section:
    section = Section("providers", "Providers", "Metadata and playback sources, with what each one needs.")
    for item in providers.status(settings):
        section.rows.append(
            Row(
                label=f"{item.label} ({item.kind})",
                kind=TOGGLE,
                state=item.enabled,
                value=item.state,
                description=item.detail + (" " + item.limitation if item.limitation else ""),
                action=f"provider.toggle.{item.key}",
            )
        )
    return section


def _downloads(settings: Any) -> Section:
    section = Section("downloads", "Downloads", "How many transfers run at once.")
    current = download_dashboard.limit(settings)
    section.rows.append(
        Row(
            label="Simultaneous downloads",
            kind=CHOICE,
            value=str(current),
            options=tuple((str(n), str(n)) for n in range(download_dashboard.MIN_LIMIT, download_dashboard.MAX_LIMIT + 1)),
            description="Starts fewer transfers on a slow connection, more on a fast one.",
            action="downloads.limit",
        )
    )
    return section


def _notifications(settings: Any) -> Section:
    section = Section("notifications", "Notifications", "Desktop notices, one switch per category.")
    section.rows.append(
        Row(
            label="Finished and failed downloads",
            kind=TOGGLE,
            state=notifications.enabled(settings, notifications.NOTIFY_DOWNLOADS),
            description="A single notice when a download completes or gives up.",
            action="notify.downloads",
        )
    )
    section.rows.append(
        Row(
            label="Anime episode releases",
            kind=TOGGLE,
            state=notifications.enabled(settings, notifications.NOTIFY_EPISODES),
            description="Off by default; only for titles you follow in the calendar.",
            action="notify.episodes",
        )
    )
    backend = notifications.notification_backend() if hasattr(notifications, "notification_backend") else None
    if backend:
        section.rows.append(Row(label="Desktop mechanism", value=str(backend)))
    return section


def _home(settings: Any) -> Section:
    layout = home_layout.HomeLayout(settings)
    section = Section("home", "Home screen", "Reorder or hide shelves, and choose how large cards are.")
    section.rows.append(
        Row(
            label="Card layout",
            kind=CHOICE,
            value=layout.layout(),
            options=((home_layout.POSTER, "Poster"), (home_layout.COMPACT, "Compact")),
            action="home.layout",
        )
    )
    visible = set(layout.order())
    for shelf in home_layout.SHELVES:
        section.rows.append(
            Row(
                label=shelf[1],
                kind=TOGGLE,
                state=shelf[0] in visible,
                action=f"home.shelf.{shelf[0]}",
            )
        )
    section.rows.append(Row(label="Restore defaults", kind=BUTTON, action="home.reset"))
    return section


def _interface(settings: Any) -> Section:
    section = Section("interface", "Interface", "Density, accent, motion and what a title page shows.")
    section.rows.append(
        Row(
            label="Compact layout",
            kind=TOGGLE,
            state=view_prefs.compact(settings),
            description="Denser rows and smaller covers, for long lists.",
            action="view.compact",
        )
    )
    section.rows.append(
        Row(
            label="Accent colour",
            kind=CHOICE,
            value=view_prefs.accent(settings),
            options=view_prefs.ACCENT_CHOICES,
            description="Used for highlights, links and selection. Labels switch to dark text when white would not read.",
            action="view.accent",
        )
    )
    section.rows.append(
        Row(
            label="Page transitions",
            kind=CHOICE,
            value=view_prefs.transitions(settings),
            options=tuple((choice, choice.capitalize()) for choice in view_prefs.TRANSITION_CHOICES),
            description="Off also removes the fade when a cover loads.",
            action="view.transitions",
        )
    )
    section.rows.append(
        Row(
            label="Artwork backgrounds",
            kind=TOGGLE,
            state=view_prefs.artwork_backgrounds(settings),
            description="Blurred poster artwork behind a title page.",
            action="view.artwork",
        )
    )
    section.rows.append(
        Row(
            label="Spoiler-free mode",
            kind=TOGGLE,
            state=view_prefs.spoiler_free(settings),
            description="Hides episode descriptions and anything that gives a plot away.",
            action="view.spoilers",
        )
    )
    section.rows.append(
        Row(
            label="Quick preview",
            kind=TOGGLE,
            state=view_prefs.quick_preview(settings),
            description="A small popover with rating and synopsis on selection.",
            action="view.preview",
        )
    )
    return section


def _profiles(settings: Any, store: profiles_mod.Profiles | None) -> Section:
    section = Section("profiles", "Profiles", profiles_mod.SECURITY_NOTE)
    if store is None:
        section.rows.append(Row(label="Profiles unavailable", value="no store"))
        return section
    for profile in store.list():
        active = profile.id == store.active_id
        section.rows.append(
            Row(
                label=profile.name,
                kind=BUTTON,
                value="Active" if active else "Switch",
                description="" if active else "Preferences, favourites and history are kept per profile.",
                action=f"profile.activate.{profile.id}",
            )
        )
    return section


def _library(settings: Any, roots: Iterable[Path | str]) -> Section:
    section = Section("library", "Local files and game sources", "Folders Lumora is allowed to read.")
    for root in roots:
        section.rows.append(
            Row(label=Path(root).name or str(root), value=str(root), action=f"media.remove.{root}")
        )
    if not list(roots):
        section.rows.append(
            Row(label="No media folders", description="Add a folder of films or shows to index it.")
        )
    for state in game_sources.setup_state(settings):
        section.rows.append(
            Row(
                label=state.label,
                value=state.state,
                description=state.limitation,
                action=f"games.toggle.{state.key}" if state.state in ("off", "needs-folder", "ready", "missing-folder") else "",
            )
        )
    return section


def _calendar(settings: Any, upcoming: Sequence[anime_calendar.Entry]) -> Section:
    section = Section("calendar", "Release calendar", "Followed titles and when the next episode airs.")
    followed = anime_calendar.follows(settings)
    section.rows.append(Row(label="Following", value=str(len(followed))))
    if not upcoming:
        section.rows.append(
            Row(
                label="Nothing scheduled",
                description="Add titles from the anime pages once provider data is available.",
            )
        )
    for entry in upcoming[:12]:
        section.rows.append(
            Row(
                label=entry.title,
                value=anime_calendar.format_when(entry),
                description=anime_calendar.describe(entry),
                action=f"anime.unfollow.{entry.key}" if entry.key in followed else f"anime.follow.{entry.key}",
            )
        )
    return section


def _sync(settings: Any) -> Section:
    cfg = history_sync.config(settings)
    section = Section("sync", "Watch-history sync", history_sync.status_line(cfg))
    section.rows.append(
        Row(
            label="Synchronise watch history",
            kind=TOGGLE,
            state=cfg.enabled,
            description="Opt in explicitly. Nothing is uploaded until you turn this on.",
            action="sync.toggle",
        )
    )
    section.rows.append(
        Row(
            label="Service",
            value=cfg.service or "none chosen",
            description="Lumora ships a local-file adapter; other services need their own adapter.",
            action="sync.service",
        )
    )
    return section


def _plugins(plugins: Sequence[plugins_mod.Plugin] | None, rejected: Sequence[plugins_mod.Rejected]) -> Section:
    section = Section(
        "plugins",
        "Plugins",
        "Declarative manifests only — Lumora makes the requests, so no plugin code runs.",
    )
    for plugin in plugins or ():
        section.rows.append(
            Row(
                label=plugin.name,
                value=f"{plugin.version} · {plugin.kind}",
                description=plugins_mod.permission_summary(plugin),
            )
        )
    for item in rejected:
        section.rows.append(
            Row(
                label=Path(item.path).parent.name if item.path else "Rejected manifest",
                value="refused",
                description=item.reason,
            )
        )
    if not plugins and not rejected:
        section.rows.append(Row(label="No plugins installed", description="Drop a manifest in the plugins folder."))
    return section


def _updates(settings: Any, backup_dir: Path | str | None, diagnostics_path: Path | str | None) -> Section:
    section = Section("updates", "Updates, backup and support", "Everything that leaves the machine does so because you asked.")
    section.rows.append(Row(label="This version", value=updates.version_line()))
    section.rows.append(
        Row(
            label="Check for updates automatically",
            kind=TOGGLE,
            state=updates.check_enabled(settings),
            description=f"At most once every {updates.interval_days(settings)} days, and only when online.",
            action="updates.toggle",
        )
    )
    section.rows.append(
        Row(
            label="Check now",
            kind=BUTTON,
            action="updates.check",
            description=updates.manual_instructions(),
        )
    )
    if backup_dir:
        section.rows.append(
            Row(label="Back up library and settings", kind=BUTTON, value=str(backup_dir), action="backup.create")
        )
    if diagnostics_path:
        section.rows.append(
            Row(label="Save support report", kind=BUTTON, value=str(diagnostics_path), action="diagnostics.create")
        )
    section.rows.append(
        Row(label="Backup format", value="zip + manifest.json", description=backup.describe_format())
    )
    return section


def apply(
    action: str,
    settings: Any,
    value: Any = None,
    *,
    profile_store: profiles_mod.Profiles | None = None,
    media_roots: list[str] | None = None,
) -> str:
    """Perform one panel action and return a short, user-facing result."""
    action = str(action or "")
    if not action:
        return "Nothing to do."
    try:
        if action.startswith("provider.toggle."):
            key = action.rsplit(".", 1)[1]
            wanted = bool(value) if value is not None else not providers.is_enabled(settings, key)
            providers.set_enabled(settings, key, wanted)
            return f"{key} {'enabled' if wanted else 'disabled'}."
        if action == "downloads.limit":
            limit = download_dashboard.set_limit(settings, value)
            return f"Downloads will run {limit} at a time."
        if action == "notify.downloads":
            wanted = bool(value)
            notifications.set_enabled(settings, notifications.NOTIFY_DOWNLOADS, wanted)
            return f"Download notifications {'on' if wanted else 'off'}."
        if action == "notify.episodes":
            wanted = bool(value)
            notifications.set_enabled(settings, notifications.NOTIFY_EPISODES, wanted)
            return f"Episode notifications {'on' if wanted else 'off'}."
        if action == "home.layout":
            chosen = home_layout.HomeLayout(settings).set_layout(str(value))
            return f"Home cards are now {chosen}."
        if action.startswith("home.shelf."):
            shelf = action.rsplit(".", 1)[1]
            layout = home_layout.HomeLayout(settings)
            wanted = bool(value) if value is not None else shelf not in layout.order()
            ok = layout.toggle(shelf, wanted)
            if not ok:
                return "At least one shelf has to stay visible."
            return f"{home_layout.label(shelf)} {'shown' if wanted else 'hidden'}."
        if action == "view.compact":
            wanted = bool(value)
            view_prefs.set_compact(settings, wanted)
            return f"Compact layout {'on' if wanted else 'off'}."
        if action == "view.accent":
            chosen = view_prefs.set_accent(settings, value)
            return f"Accent set to {chosen}."
        if action == "view.transitions":
            chosen = view_prefs.set_transitions(settings, value)
            return f"Page transitions {chosen}."
        if action == "view.artwork":
            wanted = bool(value)
            view_prefs.set_artwork(settings, wanted)
            return f"Artwork backgrounds {'on' if wanted else 'off'}."
        if action == "view.spoilers":
            wanted = bool(value)
            view_prefs.set_spoilers(settings, wanted)
            return f"Spoiler-free mode {'on' if wanted else 'off'}."
        if action == "view.preview":
            wanted = bool(value)
            view_prefs.set_preview(settings, wanted)
            return f"Quick preview {'on' if wanted else 'off'}."
        if action == "home.reset":
            home_layout.HomeLayout(settings).reset()
            return "Home screen restored to defaults."
        if action.startswith("profile.activate."):
            ident = action.rsplit(".", 1)[1]
            if profile_store is None:
                return "Profiles are unavailable."
            return "Switched profile." if profile_store.activate(ident) else "That profile no longer exists."
        if action.startswith("media.remove."):
            path = action[len("media.remove.") :]
            if media_roots is not None and path in media_roots:
                media_roots.remove(path)
                return "Folder removed from the index."
            return "That folder was not in the list."
        if action.startswith("games.toggle."):
            key = action.rsplit(".", 1)[1]
            source = game_sources.get(key)
            if source is None:
                return "Unknown source."
            wanted = bool(value) if value is not None else not game_sources.is_enabled(settings, key)
            if not game_sources.set_enabled(settings, key, wanted):
                return "That source cannot be switched off."
            return f"{source.label} {'enabled' if wanted else 'disabled'}."
        if action.startswith("anime.follow."):
            key = action[len("anime.follow.") :]
            return "Following." if anime_calendar.follow(settings, key) else "Could not follow that title."
        if action.startswith("anime.unfollow."):
            key = action[len("anime.unfollow.") :]
            return "No longer following." if anime_calendar.unfollow(settings, key) else "Was not being followed."
        if action == "sync.toggle":
            if value:
                return (
                    "Sync stays off until a service is chosen; pick one below."
                    if not history_sync.enable(settings, "local-file", consent=True)
                    else "Sync on for the local-file adapter."
                )
            history_sync.disable(settings)
            return "History sync is off."
        if action.startswith("updates.toggle"):
            wanted = bool(value)
            updates.set_check_enabled(settings, wanted)
            return f"Update checks {'on' if wanted else 'off'}."
    except Exception as exc:  # a panel action must never take the window down
        return f"That did not work: {exc}"
    return "Nothing to do."


__all__ = ["BUTTON", "CHOICE", "INFO", "LIST", "Panel", "Row", "Section", "TOGGLE", "apply", "build"]
