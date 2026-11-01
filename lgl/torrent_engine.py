"""One interface for the built-in torrent engine and the aria2c fallback.

The Downloads panel should not care which engine is running.  This module
defines that contract once (``add_magnet`` … ``shutdown``), the two backends
that satisfy it, the choice between them, and - most importantly - the pure
logic around them: state mapping, file selection, resume bookkeeping, the
"Max speed" settings profile, diagnostics and HTTP segment splitting.  All of
that is testable without a running session, a network or a licensed client.

``libtorrent`` is imported lazily and never at module import time: a machine
without it must still start the app and fall back to aria2c.
"""

from __future__ import annotations

import importlib
import shutil
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Sequence

#: Values written to ``settings.torrent_engine``.
ENGINE_AUTO = "auto"
ENGINE_BUILTIN = "builtin"
ENGINE_ARIA2C = "aria2c"
ENGINE_CUSTOM = "custom"
ENGINE_CHOICES = (ENGINE_AUTO, ENGINE_BUILTIN, ENGINE_ARIA2C, ENGINE_CUSTOM)

#: KiB/s sanity threshold below which an upload cap starts hurting downloads.
LOW_UPLOAD_BYTES = 32 * 1024
#: A write queue this deep usually means the disk, not the network, is the limit.
DISK_QUEUE_PRESSURE_BYTES = 64 * 1024 * 1024


class TorrentState(str, Enum):
    QUEUED = "queued"
    CHECKING = "checking"
    FETCHING_METADATA = "fetching-metadata"
    DOWNLOADING = "downloading"
    SEEDING = "seeding"
    PAUSED = "paused"
    FINISHED = "finished"
    ERROR = "error"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        return {
            TorrentState.QUEUED: "Queued",
            TorrentState.CHECKING: "Checking files",
            TorrentState.FETCHING_METADATA: "Fetching metadata",
            TorrentState.DOWNLOADING: "Downloading",
            TorrentState.SEEDING: "Seeding",
            TorrentState.PAUSED: "Paused",
            TorrentState.FINISHED: "Finished",
            TorrentState.ERROR: "Error",
            TorrentState.UNKNOWN: "Unknown",
        }[self]


@dataclass(frozen=True)
class TorrentStatus:
    """One torrent, in the terms the panel shows."""

    info_hash: str
    name: str = ""
    state: TorrentState = TorrentState.UNKNOWN
    progress: float = 0.0
    download_rate: int = 0
    upload_rate: int = 0
    peers: int = 0
    seeds: int = 0
    total_bytes: int = 0
    downloaded_bytes: int = 0
    uploaded_bytes: int = 0
    error: str = ""
    save_path: str = ""

    @property
    def percent(self) -> int:
        return max(0, min(100, int(round(self.progress * 100))))

    @property
    def remaining_bytes(self) -> int:
        return max(0, self.total_bytes - self.downloaded_bytes)

    @property
    def eta(self) -> int:
        """Seconds left at the current rate, ``-1`` when it cannot be known."""
        if self.state is not TorrentState.DOWNLOADING or self.download_rate <= 0:
            return -1
        return int(self.remaining_bytes / self.download_rate)

    @property
    def ratio(self) -> float:
        if self.downloaded_bytes <= 0:
            return 0.0
        return round(self.uploaded_bytes / self.downloaded_bytes, 3)

    @property
    def is_active(self) -> bool:
        return self.state in (
            TorrentState.DOWNLOADING,
            TorrentState.SEEDING,
            TorrentState.CHECKING,
            TorrentState.FETCHING_METADATA,
        )


#: libtorrent's ``torrent_status.state`` values (2.x), by number.
LIBTORRENT_STATES: dict[int, TorrentState] = {
    1: TorrentState.CHECKING,
    2: TorrentState.FETCHING_METADATA,
    3: TorrentState.DOWNLOADING,
    4: TorrentState.FINISHED,
    5: TorrentState.SEEDING,
    6: TorrentState.QUEUED,
    7: TorrentState.CHECKING,
}

ARIA2_STATES: dict[str, TorrentState] = {
    "active": TorrentState.DOWNLOADING,
    "waiting": TorrentState.QUEUED,
    "paused": TorrentState.PAUSED,
    "error": TorrentState.ERROR,
    "complete": TorrentState.SEEDING,
    "removed": TorrentState.FINISHED,
}


def state_from_libtorrent(value: Any) -> TorrentState:
    """Map a libtorrent state (int or enum) to a panel state."""
    if isinstance(value, TorrentState):
        return value
    name = str(getattr(value, "name", "") or "").casefold()
    if name:
        for candidate, mapped in (
            ("checking", TorrentState.CHECKING),
            ("downloading_metadata", TorrentState.FETCHING_METADATA),
            ("downloading", TorrentState.DOWNLOADING),
            ("finished", TorrentState.FINISHED),
            ("seeding", TorrentState.SEEDING),
            ("allocating", TorrentState.QUEUED),
        ):
            if candidate in name:
                return mapped
    try:
        return LIBTORRENT_STATES.get(int(value), TorrentState.UNKNOWN)
    except (TypeError, ValueError):
        return TorrentState.UNKNOWN


def state_from_aria2(value: Any) -> TorrentState:
    return ARIA2_STATES.get(str(value or "").strip().casefold(), TorrentState.UNKNOWN)


def status_from_libtorrent(status: Any, info_hash: str = "") -> TorrentStatus:
    """One ``libtorrent.torrent_status`` as a :class:`TorrentStatus`."""
    total = int(getattr(status, "total_wanted", 0) or getattr(status, "total", 0) or 0)
    done = int(getattr(status, "total_wanted_done", 0) or getattr(status, "total_done", 0) or 0)
    progress = float(getattr(status, "progress", 0.0) or 0.0)
    if not progress and total:
        progress = done / total
    return TorrentStatus(
        info_hash=str(info_hash or ""),
        name=str(getattr(status, "name", "") or ""),
        state=state_from_libtorrent(getattr(status, "state", None)),
        progress=progress,
        download_rate=int(getattr(status, "download_rate", 0) or 0),
        upload_rate=int(getattr(status, "upload_rate", 0) or 0),
        peers=int(getattr(status, "num_peers", 0) or 0),
        seeds=int(getattr(status, "num_seeds", 0) or 0),
        total_bytes=max(0, total),
        downloaded_bytes=max(0, done),
        uploaded_bytes=int(getattr(status, "all_time_upload", 0) or 0),
        error=str(getattr(status, "error", "") or ""),
    )


def aria2_status(payload: Mapping[str, Any]) -> TorrentStatus:
    """One ``aria2.tellStatus`` result as a :class:`TorrentStatus`."""
    files = payload.get("files")
    name = ""
    if isinstance(files, Sequence):
        for entry in files:
            if isinstance(entry, Mapping) and entry.get("path"):
                name = str(entry["path"]).rsplit("/", 1)[-1]
                break
    total = int(payload.get("totalLength") or 0)
    done = int(payload.get("completedLength") or 0)
    return TorrentStatus(
        info_hash=str(payload.get("infoHash") or payload.get("gid") or ""),
        name=name,
        state=state_from_aria2(payload.get("status")),
        progress=(done / total) if total else 0.0,
        download_rate=int(payload.get("downloadSpeed") or 0),
        upload_rate=int(payload.get("uploadSpeed") or 0),
        peers=int(payload.get("connections") or 0),
        seeds=int(payload.get("numSeeders") or 0),
        total_bytes=max(0, total),
        downloaded_bytes=max(0, done),
        error=str(payload.get("errorMessage") or ""),
    )


#: The "Max speed" profile.  Plain data: this module never imports libtorrent
#: to build it, and every name is checked against the installed build before
#: it is applied.
MAX_SPEED_PROFILE: dict[str, Any] = {
    "connections_limit": 800,
    "active_downloads": 12,
    "active_limit": 24,
    "download_rate_limit": 0,
    "upload_rate_limit": 0,
    "max_queued_disk_bytes": 512 * 1024 * 1024,
    "aio_threads": 32,
    "max_out_request_queue": 8000,
    "enable_dht": True,
    "enable_lsd": True,
    "enable_upnp": True,
    "enable_natpmp": True,
    "mixed_mode_algorithm": "prefer_tcp",
}


def max_speed_profile(overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The profile as a fresh dict, with any advanced-user overrides applied."""
    profile = dict(MAX_SPEED_PROFILE)
    for name, value in (overrides or {}).items():
        if name in profile:
            profile[name] = value
    return profile


def apply_settings(
    session: Any,
    values: Mapping[str, Any],
    *,
    known: Iterable[str] | None = None,
    on_skip: Callable[[str], None] | None = None,
) -> list[str]:
    """Apply settings one at a time; skip the ones this build does not know.

    libtorrent renames settings between minor versions, and a single unknown
    name in a dict can reject the whole call.  Applying them individually (and
    checking ``known`` first when the build can list its settings) means an
    old build loses one tuning knob instead of the entire profile.
    """
    allowed = None if known is None else set(known)
    applied: list[str] = []
    setter = getattr(session, "apply_settings", None) or getattr(
        session, "set_settings", None
    )
    if setter is None:
        for name in values:
            if on_skip is not None:
                on_skip(name)
        return applied
    for name, value in values.items():
        if allowed is not None and name not in allowed:
            if on_skip is not None:
                on_skip(name)
            continue
        try:
            setter({name: value})
        except (RuntimeError, TypeError, ValueError, AttributeError):
            if on_skip is not None:
                on_skip(name)
            continue
        applied.append(name)
    return applied


# -- engine selection -------------------------------------------------------


def libtorrent_available(module: Any = None) -> bool:
    """True when the built-in engine can be imported."""
    if module is not None:
        return True
    try:
        importlib.import_module("libtorrent")
    except (ImportError, OSError, ValueError):
        return False
    return True


def select_backend(
    preference: str = ENGINE_AUTO,
    *,
    builtin: bool | None = None,
    aria2c_path: str = "",
    custom_path: str = "",
    which: Callable[[str], str | None] = shutil.which,
) -> str:
    """Which engine to run: ``"libtorrent"``, ``"aria2c"`` or ``"none"``."""
    choice = str(preference or ENGINE_AUTO).strip().lower()
    if choice not in ENGINE_CHOICES:
        choice = ENGINE_AUTO
    has_builtin = libtorrent_available() if builtin is None else bool(builtin)
    if choice == ENGINE_BUILTIN:
        return "libtorrent" if has_builtin else "none"
    if choice == ENGINE_ARIA2C:
        return "aria2c" if (aria2c_path or which("aria2c")) else "none"
    if choice == ENGINE_CUSTOM:
        return "aria2c" if (custom_path and which(custom_path)) else "none"
    if has_builtin:
        return "libtorrent"
    return "aria2c" if (aria2c_path or which("aria2c")) else "none"


# -- magnet files -----------------------------------------------------------


@dataclass(frozen=True)
class TorrentFile:
    index: int
    path: str
    size: int

    @property
    def display_name(self) -> str:
        return self.path.rsplit("/", 1)[-1]


def select_files(
    files: Sequence[TorrentFile], wanted: Sequence[int] | None = None
) -> tuple[int, ...]:
    """Indices to download: every file, or exactly the ones asked for."""
    available = {file.index for file in files}
    if wanted is None:
        return tuple(sorted(available))
    return tuple(sorted({int(index) for index in wanted if int(index) in available}))


def selection_total(
    files: Sequence[TorrentFile], wanted: Sequence[int] | None = None
) -> int:
    chosen = set(select_files(files, wanted))
    return sum(file.size for file in files if file.index in chosen)


# -- resume bookkeeping -----------------------------------------------------


@dataclass(frozen=True)
class ResumeRecord:
    """What has to survive a restart for a torrent to continue, not recheck."""

    info_hash: str
    magnet: str = ""
    save_path: str = ""
    files: tuple[int, ...] = ()
    added_at: float = 0.0
    paused: bool = False


def resume_payload(records: Iterable[ResumeRecord]) -> list[dict[str, Any]]:
    """JSON-ready form, stable key order so the file does not churn."""
    return [
        {
            "info_hash": record.info_hash,
            "magnet": record.magnet,
            "save_path": record.save_path,
            "files": list(record.files),
            "added_at": record.added_at,
            "paused": record.paused,
        }
        for record in records
        if record.info_hash
    ]


def plan_restore(
    records: Iterable[ResumeRecord],
    *,
    exists: Callable[[str], bool],
    magnet_is_usable: Callable[[str], bool] | None = None,
) -> tuple[list[ResumeRecord], list[ResumeRecord]]:
    """Split records into *(restorable, orphaned)*.

    A torrent whose folder was deleted or moved cannot be restored without a
    recheck of nothing, so it is reported instead of silently dropped.
    """
    restorable: list[ResumeRecord] = []
    orphaned: list[ResumeRecord] = []
    for record in records:
        if not record.info_hash:
            continue
        if not record.save_path or not exists(record.save_path):
            orphaned.append(record)
            continue
        if not record.magnet and magnet_is_usable is not None:
            orphaned.append(record)
            continue
        restorable.append(record)
    return restorable, orphaned


# -- diagnostics ------------------------------------------------------------


def diagnostics(
    status: TorrentStatus,
    *,
    port_reachable: bool | None = None,
    disk_queue_bytes: int = 0,
    upload_limit: int = 0,
    available_seeds: int | None = None,
    dht_enabled: bool = True,
) -> list[str]:
    """Plain-language reasons a torrent is slower than it could be."""
    hints: list[str] = []
    if status.state is TorrentState.ERROR and status.error:
        return [f"The engine reported: {status.error}"]
    if status.state in (TorrentState.PAUSED, TorrentState.FINISHED):
        return ["Nothing is running, so there is no speed to explain."]
    if status.state is TorrentState.SEEDING:
        return ["This torrent is finished and only uploading."]
    if status.state is TorrentState.FETCHING_METADATA:
        return ["Still fetching metadata, so no peers have the info yet."]
    if status.state is TorrentState.CHECKING:
        return ["Checking existing files; speed starts after this."]

    if status.seeds <= 0:
        if not available_seeds:
            hints.append(
                "No seeds are connected and none were reported; the swarm may be empty "
                "or the tracker unreachable."
            )
        else:
            hints.append(
                f"{available_seeds} seed(s) exist but none are connected yet."
            )
    elif status.seeds <= 2 and status.download_rate < 1024 * 1024:
        hints.append(
            f"Only {status.seeds} seed(s) connected, which caps the speed."
        )
    if port_reachable is False:
        hints.append(
            "The listening port looks closed; incoming peers cannot connect, so "
            "enable UPnP or forward the port."
        )
    if disk_queue_bytes >= DISK_QUEUE_PRESSURE_BYTES:
        hints.append(
            "The disk write queue is backed up, so the disk is the bottleneck here, "
            "not the network."
        )
    if upload_limit and upload_limit < LOW_UPLOAD_BYTES:
        hints.append(
            "Your upload limit is very low; peers throttle downloaders that do not "
            "reciprocate."
        )
    if not dht_enabled:
        hints.append("DHT is off, so peers can only come from trackers.")
    if not hints:
        hints.append("Nothing in these numbers explains a slow speed.")
    return hints


# -- segmented HTTP downloads ----------------------------------------------


def split_segments(
    total_bytes: int, workers: int = 8, *, min_chunk: int = 1024 * 1024
) -> list[tuple[int, int]]:
    """Split ``[0, total)`` into contiguous ``(start, end)`` range pairs.

    Used only when a server advertises ``Accept-Ranges``; a caller that gets no
    ranges falls back to a single connection.  ``end`` is exclusive, so the
    pieces tile the file exactly and a resume can store the same numbers.
    """
    total = int(total_bytes or 0)
    if total <= 0:
        return []
    count = max(1, min(int(workers or 1), 16))
    if min_chunk > 0:
        count = max(1, min(count, max(1, total // int(min_chunk))))
    chunk = total // count
    segments: list[tuple[int, int]] = []
    start = 0
    for index in range(count):
        end = total if index == count - 1 else start + chunk
        segments.append((start, end))
        start = end
    return [segment for segment in segments if segment[1] > segment[0]]


# -- backends ---------------------------------------------------------------


class TorrentBackend:
    """The contract the Downloads panel drives."""

    name = "none"
    version = ""

    def __init__(self, *, save_path: str = "") -> None:
        self.save_path = str(save_path or "")
        self.last_error = ""

    def add_magnet(
        self, uri: str, *, save_path: str = "", files: Sequence[int] | None = None
    ) -> str:
        raise NotImplementedError

    def add_torrent_file(
        self, path: str, *, save_path: str = "", files: Sequence[int] | None = None
    ) -> str:
        raise NotImplementedError

    def pause(self, info_hash: str) -> bool:
        raise NotImplementedError

    def resume(self, info_hash: str) -> bool:
        raise NotImplementedError

    def remove(self, info_hash: str, *, delete_files: bool = False) -> bool:
        raise NotImplementedError

    def set_limits(self, *, download: int = 0, upload: int = 0) -> bool:
        raise NotImplementedError

    def list_status(self) -> list[TorrentStatus]:
        raise NotImplementedError

    def shutdown(self) -> None:
        raise NotImplementedError


class Aria2Backend(TorrentBackend):
    """aria2c through the existing JSON-RPC helper, as a fallback.

    ``call(method, params)`` is supplied by the caller (the app already owns an
    aria2c process and its RPC plumbing), which keeps this class testable
    without a subprocess.
    """

    name = "aria2c"

    def __init__(
        self,
        call: Callable[[str, Sequence[Any]], Any],
        *,
        save_path: str = "",
        path: str = "",
    ) -> None:
        super().__init__(save_path=save_path)
        self._call = call
        self.path = path

    def add_magnet(self, uri, *, save_path="", files=None) -> str:
        params: list[Any] = [uri]
        if save_path or self.save_path:
            params.append({"dir": save_path or self.save_path})
        if files is not None:
            params.append([f"select-file={','.join(str(i + 1) for i in files)}"])
        return str(self._call("aria2.addUri", params) or "")

    def add_torrent_file(self, path, *, save_path="", files=None) -> str:
        params: list[Any] = [path]
        if save_path or self.save_path:
            params.append({"dir": save_path or self.save_path})
        return str(self._call("aria2.addTorrent", params) or "")

    def pause(self, info_hash: str) -> bool:
        return bool(self._call("aria2.pause", [info_hash]))

    def resume(self, info_hash: str) -> bool:
        return bool(self._call("aria2.unpause", [info_hash]))

    def remove(self, info_hash: str, *, delete_files: bool = False) -> bool:
        method = "aria2.removeDownloadResult" if not delete_files else "aria2.forceRemove"
        return bool(self._call(method, [info_hash]))

    def set_limits(self, *, download: int = 0, upload: int = 0) -> bool:
        options = {
            "max-overall-download-limit": f"{int(download)}",
            "max-overall-upload-limit": f"{int(upload)}",
        }
        return bool(self._call("aria2.changeGlobalOption", [options]))

    def list_status(self) -> list[TorrentStatus]:
        statuses: list[TorrentStatus] = []
        for method in ("aria2.tellActive", "aria2.tellWaiting", "aria2.tellStopped"):
            try:
                payload = self._call(method, [0, 1000])
            except (RuntimeError, OSError, ValueError):
                continue
            if isinstance(payload, Sequence):
                for item in payload:
                    if isinstance(item, Mapping):
                        statuses.append(aria2_status(item))
        return statuses

    def shutdown(self) -> None:
        try:
            self._call("aria2.shutdown", [])
        except (RuntimeError, OSError, ValueError):
            pass


class LibtorrentBackend(TorrentBackend):
    """The bundled engine: one session, DHT/LSD/UPnP on, Max speed profile."""

    name = "libtorrent"

    def __init__(
        self,
        *,
        save_path: str = "",
        listen_port: int = 0,
        profile: Mapping[str, Any] | None = None,
        module: Any = None,
        on_skip: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(save_path=save_path)
        if module is None:
            module = importlib.import_module("libtorrent")
        self._lt = module
        self.version = str(getattr(module, "__version__", "") or "")
        settings: dict[str, Any] = {}
        if listen_port:
            settings["listen_interfaces"] = f"0.0.0.0:{int(listen_port)},[::]:{int(listen_port)}"
        self.session = module.session(settings) if settings else module.session()
        self.skipped: list[str] = []
        apply_settings(
            self.session,
            max_speed_profile(profile),
            known=_known_settings(self.session),
            on_skip=lambda name: self.skipped.append(name)
            if on_skip is None
            else on_skip(name),
        )

    def add_magnet(self, uri, *, save_path="", files=None) -> str:
        params = self._params(uri, save_path=save_path, files=files)
        handle = self.session.add_torrent(params) if hasattr(self.session, "add_torrent") else None
        return self._hash(handle)

    def add_torrent_file(self, path, *, save_path="", files=None) -> str:
        return self.add_magnet(path, save_path=save_path, files=files)

    def _params(self, uri: str, *, save_path: str, files: Sequence[int] | None) -> dict[str, Any]:
        params: dict[str, Any] = {"save_path": save_path or self.save_path}
        if files is None:
            params["url"] = uri
            return params
        if hasattr(self._lt, "parse_magnet_uri"):
            try:
                handle = self._lt.add_torrent_params()
                self._lt.parse_magnet_uri(uri, handle)
                flags = list(getattr(handle, "file_priorities", []) or [])
                if not flags:
                    flags = [1] * (max(files) + 1 if files else 0)
                for index in range(len(flags)):
                    flags[index] = 4 if index in set(files) else 0
                handle.file_priorities = flags
                params["ti"] = handle
                return params
            except (RuntimeError, ValueError, AttributeError):
                pass
        params["url"] = uri
        return params

    @staticmethod
    def _hash(handle: Any) -> str:
        try:
            return str(handle.info_hash().to_string() if hasattr(handle.info_hash(), "to_string") else handle.info_hash())
        except (AttributeError, RuntimeError, TypeError):
            return ""

    def pause(self, info_hash: str) -> bool:
        return self._apply(info_hash, lambda handle: handle.pause())

    def resume(self, info_hash: str) -> bool:
        return self._apply(info_hash, lambda handle: handle.resume())

    def remove(self, info_hash: str, *, delete_files: bool = False) -> bool:
        flags = 1 if delete_files else 0
        try:
            self.session.remove_torrent(self._find(info_hash), flags)
            return True
        except (AttributeError, RuntimeError, TypeError):
            return False

    def _find(self, info_hash: str) -> Any:
        for handle in getattr(self.session, "get_torrents", lambda: [])():
            if self._hash(handle) == str(info_hash):
                return handle
        raise RuntimeError(f"{info_hash} is not in this session")

    def _apply(self, info_hash: str, action: Callable[[Any], Any]) -> bool:
        try:
            action(self._find(info_hash))
            return True
        except (RuntimeError, AttributeError, TypeError):
            return False

    def set_limits(self, *, download: int = 0, upload: int = 0) -> bool:
        applied = apply_settings(
            self.session,
            {"download_rate_limit": int(download), "upload_rate_limit": int(upload)},
            known=_known_settings(self.session),
        )
        return bool(applied)

    def list_status(self) -> list[TorrentStatus]:
        statuses: list[TorrentStatus] = []
        for handle in getattr(self.session, "get_torrents", lambda: [])():
            try:
                statuses.append(status_from_libtorrent(handle.status(), self._hash(handle)))
            except (RuntimeError, AttributeError):
                continue
        return statuses

    def shutdown(self) -> None:
        save = getattr(self.session, "save_state", None)
        if save is None:
            return
        try:
            save({"save_dht_state": True}, getattr(self._lt, "save_state_flags", lambda: 0)())
        except (RuntimeError, TypeError, AttributeError):
            try:
                save()
            except (RuntimeError, TypeError, AttributeError):
                pass


def _known_settings(session: Any) -> set[str] | None:
    """Setting names this build accepts, when it can say so."""
    try:
        current = session.settings()
    except (AttributeError, RuntimeError, TypeError):
        return None
    items = getattr(current, "items", None)
    if callable(items):
        try:
            return {str(name) for name, _value in items()}
        except (TypeError, ValueError):
            return None
    return None


class TorrentEngine:
    """The facade the UI holds: one backend, chosen at start-up."""

    def __init__(self, backend: TorrentBackend | None = None, *, save_path: str = "") -> None:
        self.backend = backend
        self.save_path = str(save_path or "")

    @property
    def backend_name(self) -> str:
        return getattr(self.backend, "name", "none")

    @property
    def version(self) -> str:
        return str(getattr(self.backend, "version", "") or "")

    @property
    def available(self) -> bool:
        return self.backend is not None

    def add_magnet(self, uri: str, *, files: Sequence[int] | None = None) -> str:
        if self.backend is None:
            raise RuntimeError("No torrent engine is available.")
        return self.backend.add_magnet(uri, save_path=self.save_path, files=files)

    def add_torrent_file(self, path: str, *, files: Sequence[int] | None = None) -> str:
        if self.backend is None:
            raise RuntimeError("No torrent engine is available.")
        return self.backend.add_torrent_file(path, save_path=self.save_path, files=files)

    def pause(self, info_hash: str) -> bool:
        return bool(self.backend and self.backend.pause(info_hash))

    def resume(self, info_hash: str) -> bool:
        return bool(self.backend and self.backend.resume(info_hash))

    def remove(self, info_hash: str, *, delete_files: bool = False) -> bool:
        return bool(self.backend and self.backend.remove(info_hash, delete_files=delete_files))

    def set_limits(self, *, download: int = 0, upload: int = 0) -> bool:
        return bool(self.backend and self.backend.set_limits(download=download, upload=upload))

    def list_status(self) -> list[TorrentStatus]:
        return list(self.backend.list_status()) if self.backend else []

    def shutdown(self) -> None:
        if self.backend is not None:
            self.backend.shutdown()
            self.backend = None


def create_engine(
    *,
    preference: str = ENGINE_AUTO,
    save_path: str = "",
    aria2c_path: str = "",
    custom_path: str = "",
    aria2_call: Callable[[str, Sequence[Any]], Any] | None = None,
    libtorrent_backend: TorrentBackend | None = None,
) -> TorrentEngine:
    """Build the engine the settings ask for, falling back when they cannot."""
    if libtorrent_backend is not None:
        return TorrentEngine(libtorrent_backend, save_path=save_path)
    choice = select_backend(
        preference,
        aria2c_path=aria2c_path,
        custom_path=custom_path,
    )
    if choice == "libtorrent":
        try:
            return TorrentEngine(LibtorrentBackend(save_path=save_path), save_path=save_path)
        except (ImportError, OSError, RuntimeError, ValueError):
            choice = "aria2c" if (aria2c_path or custom_path) else "none"
    if choice == "aria2c" and aria2_call is not None:
        return TorrentEngine(
            Aria2Backend(aria2_call, save_path=save_path, path=custom_path or aria2c_path),
            save_path=save_path,
        )
    return TorrentEngine(None, save_path=save_path)


__all__ = [
    "ARIA2_STATES",
    "DISK_QUEUE_PRESSURE_BYTES",
    "ENGINE_ARIA2C",
    "ENGINE_AUTO",
    "ENGINE_BUILTIN",
    "ENGINE_CHOICES",
    "ENGINE_CUSTOM",
    "LIBTORRENT_STATES",
    "LOW_UPLOAD_BYTES",
    "MAX_SPEED_PROFILE",
    "Aria2Backend",
    "LibtorrentBackend",
    "ResumeRecord",
    "TorrentBackend",
    "TorrentEngine",
    "TorrentFile",
    "TorrentState",
    "TorrentStatus",
    "apply_settings",
    "aria2_status",
    "create_engine",
    "diagnostics",
    "libtorrent_available",
    "max_speed_profile",
    "plan_restore",
    "resume_payload",
    "select_backend",
    "select_files",
    "selection_total",
    "split_segments",
    "state_from_aria2",
    "state_from_libtorrent",
    "status_from_libtorrent",
]
