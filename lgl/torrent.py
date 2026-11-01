"""Magnet-link downloads driven by an external BitTorrent engine.

The application does not implement BitTorrent itself.  Magnet links are handed
to ``aria2c``, which runs on Windows and Linux, understands magnet URIs (DHT and
trackers), and exposes a small JSON-RPC interface.  That interface is what makes
real progress reporting possible: the engine reports bytes, speed, peers, and a
terminal state, and this module never guesses.

Things this module deliberately does **not** do:

* It never starts a download on its own.  A task exists only because the user
  asked for it, and nothing is resumed after a restart (unfinished work is
  reported as paused).
* It never deletes files.  Removing a task forgets the queue entry only.
* It never claims a magnet is healthy.  A parsed URI proves nothing about
  availability, completeness, or licensing.

If no engine is installed, :func:`find_engine` says so with a platform-specific
hint instead of failing later with a confusing network error.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import magnet as magnetlib
from .util import (
    AppError,
    atomic_write_json,
    check_download_dir,
    ensure_directory,
    read_json,
)

#: Queue file format.  Bumped only for incompatible changes.
QUEUE_VERSION = 1
MAX_TASKS = 500
RPC_TIMEOUT = 5.0
ENGINE_START_TIMEOUT = 15.0
POLL_INTERVAL = 1.0
#: How long a task may hold the engine with zero peers before it is reported as
#: unseeded.  aria2 keeps a torrent it cannot find peers for in the ``active``
#: state indefinitely, which makes a dead swarm look like a slow download.
NO_PEER_TIMEOUT = 180.0

STATUS_QUEUED = "queued"
STATUS_DOWNLOADING = "downloading"
STATUS_PAUSED = "paused"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUSES = (
    STATUS_QUEUED,
    STATUS_DOWNLOADING,
    STATUS_PAUSED,
    STATUS_COMPLETED,
    STATUS_FAILED,
)

_TERMINAL = (STATUS_COMPLETED, STATUS_FAILED)

#: aria2 states that mean "this download will not continue by itself".
_ARIA2_ACTIVE = ("active",)


class TorrentError(AppError):
    """A torrent task or the engine could not be used."""


class EngineUnavailable(TorrentError):
    """No usable BitTorrent engine is installed."""


# --------------------------------------------------------------------------
# Engine discovery
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class EngineInfo:
    """What was found when looking for a torrent engine.

    ``kind`` says which backend the manager should start.  ``"native"`` is the
    BitTorrent client built into this application; ``"aria2"`` is the external
    binary, used only when a path is configured explicitly.
    """

    available: bool
    path: str = ""
    version: str = ""
    reason: str = ""
    kind: str = "aria2"

    @property
    def summary(self) -> str:
        if not self.available:
            return self.reason or "No BitTorrent engine was found."
        if self.kind == "native":
            return f"Built-in torrent engine {self.version}".strip()
        return f"aria2c {self.version}".strip() if self.version else "aria2c"


def _install_hint() -> str:
    """Only reached when the built-in engine cannot be imported at all."""
    if os.name == "nt":
        return (
            "The built-in torrent engine could not be loaded. Reinstall the "
            "application, or put aria2c.exe next to Lumora.exe and "
            "set its path in Settings."
        )
    if sys.platform == "darwin":
        return (
            "The built-in torrent engine could not be loaded. Reinstall the "
            "application, or install aria2 with `brew install aria2`."
        )
    return (
        "The built-in torrent engine could not be loaded. Reinstall the "
        "application, or install aria2 with your package manager and set its "
        "path in Settings."
    )


def _native_engine() -> EngineInfo:
    """The in-process engine, which needs no external program."""
    try:
        from . import bittorrent
    except Exception as exc:  # pragma: no cover - import failure is the only path
        return EngineInfo(False, "", "", f"{_install_hint()} ({exc})")
    return EngineInfo(True, "", str(getattr(bittorrent, "__version__", "1")), kind="native")


def _bundled_candidates() -> list[Path]:
    """Places a packaged build may keep the engine."""
    roots: list[Path] = []
    frozen = getattr(sys, "_MEIPASS", None)
    if frozen:
        roots.append(Path(frozen))
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)
    here = Path(__file__).resolve()
    roots.extend([here.parent.parent / "tools", here.parent.parent / "vendor"])
    names = ("aria2c.exe", "aria2c") if os.name == "nt" else ("aria2c",)
    return [root / name for root in roots for name in names]


def _engine_version(path: str) -> str:
    """Return the aria2 version, or ``""`` when this is not aria2."""
    try:
        result = subprocess.run(
            [path, "--version"],
            capture_output=True,
            timeout=6,
            text=True,
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    text = f"{result.stdout or ''}\n{result.stderr or ''}"
    if "aria2" not in text.casefold():
        return ""
    match = re.search(r"version\s+([0-9][0-9A-Za-z.\-]*)", text, re.IGNORECASE)
    return match.group(1) if match else "installed"


def find_engine(configured: str = "") -> EngineInfo:
    """Locate aria2c, preferring an explicit path from settings.

    A configured path that exists but is not aria2 is reported as unusable
    rather than silently ignored: if the user typed a path, they deserve to know
    why it cannot be used instead of watching a generic failure later.
    """
    text = (configured or "").strip().strip('"')
    if text:
        candidate = Path(text).expanduser()
        if candidate.is_file():
            version = _engine_version(str(candidate))
            if version:
                return EngineInfo(True, str(candidate), version)
            return EngineInfo(
                False,
                str(candidate),
                "",
                "The configured program is not aria2c, so magnet links cannot be "
                "handed to it. Clear the setting to let the app search for aria2c.",
            )
        return EngineInfo(
            False, text, "", f"The configured engine path does not exist: {text}"
        )

    # No path configured: use the engine that ships with the application.  The
    # external binary is only a fallback for builds where the module is missing.
    native = _native_engine()
    if native.available:
        return native

    found = shutil.which("aria2c")
    if found:
        version = _engine_version(found)
        if version:
            return EngineInfo(True, found, version)

    for candidate in _bundled_candidates():
        try:
            if candidate.is_file():
                version = _engine_version(str(candidate))
                if version:
                    return EngineInfo(True, str(candidate), version)
        except OSError:
            continue

    return EngineInfo(False, "", "", _install_hint())


# --------------------------------------------------------------------------
# aria2 JSON-RPC
# --------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class Aria2Client:
    """Minimal JSON-RPC client for one local aria2 process.

    Only the calls this app needs are implemented, and the RPC endpoint is bound
    to the loopback interface with a per-session secret.
    """

    def __init__(self, port: int, secret: str, *, timeout: float = RPC_TIMEOUT) -> None:
        self.port = int(port)
        self.secret = secret
        self.timeout = timeout
        self._url = f"http://127.0.0.1:{self.port}/jsonrpc"
        self._ids = 0

    def _token(self) -> str:
        return f"token:{self.secret}"

    def call(self, method: str, params: Sequence[Any] = ()) -> Any:
        self._ids += 1
        payload = {
            "jsonrpc": "2.0",
            "id": f"ngl-{self._ids}",
            "method": method,
            "params": [self._token(), *params],
        }
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self._url,
            data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")
            except OSError:
                detail = ""
            raise TorrentError(
                f"The torrent engine rejected {method} (HTTP {exc.code}). {detail}".strip()
            ) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise TorrentError(f"The torrent engine is not reachable: {exc}") from exc
        try:
            document = json.loads(raw.decode("utf-8", "replace"))
        except ValueError as exc:
            raise TorrentError("The torrent engine answered with invalid JSON.") from exc
        if not isinstance(document, Mapping):
            raise TorrentError("The torrent engine answered with an unexpected payload.")
        error = document.get("error")
        if isinstance(error, Mapping):
            message = str(error.get("message") or "The engine refused the request.")
            raise TorrentError(message)
        return document.get("result")

    # -- typed helpers ------------------------------------------------------

    def version(self) -> dict[str, Any]:
        result = self.call("aria2.getVersion")
        return dict(result) if isinstance(result, Mapping) else {}

    def add_uri(self, uri: str, directory: str) -> str:
        result = self.call("aria2.addUri", [[uri], {"dir": directory, "pause": "false"}])
        if not isinstance(result, str) or not result:
            raise TorrentError("The torrent engine did not return a download id.")
        return result

    def tell(self, gid: str) -> dict[str, Any]:
        result = self.call(
            "aria2.tellStatus",
            [
                gid,
                [
                    "gid",
                    "status",
                    "totalLength",
                    "completedLength",
                    "downloadSpeed",
                    "uploadSpeed",
                    "connections",
                    "numSeeders",
                    "errorMessage",
                    "errorCode",
                    "followedBy",
                    "following",
                    "belongsTo",
                    "files",
                    "bittorrent",
                ],
            ],
        )
        return dict(result) if isinstance(result, Mapping) else {}

    def pause(self, gid: str) -> None:
        self.call("aria2.forcePause", [gid])

    def unpause(self, gid: str) -> None:
        self.call("aria2.unpause", [gid])

    def remove(self, gid: str) -> None:
        self.call("aria2.forceRemove", [gid])

    def shutdown(self) -> None:
        self.call("aria2.shutdown")


class Aria2Engine:
    """One aria2 process owned by this application."""

    def __init__(
        self,
        path: str,
        directory: Path | str,
        *,
        port: int | None = None,
        secret: str | None = None,
        spawn: Callable[..., Any] = subprocess.Popen,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.path = path
        self.directory = Path(directory).expanduser()
        self.port = int(port) if port else _free_port()
        self.secret = secret or secrets.token_urlsafe(24).replace("-", "").replace("_", "")
        self._spawn = spawn
        self._clock = clock
        self.process: Any = None
        self.client = Aria2Client(self.port, self.secret)

    # -- lifecycle ----------------------------------------------------------

    def command(self) -> list[str]:
        return [
            self.path,
            "--enable-rpc=true",
            "--rpc-listen-all=false",
            f"--rpc-listen-port={self.port}",
            f"--rpc-secret={self.secret}",
            f"--dir={self.directory}",
            "--max-concurrent-downloads=1",
            # Peer discovery.  aria2 runs with --no-conf=true, and in that mode
            # --enable-dht and --bt-enable-lpd default to **off**.  A magnet then
            # has only its own tr= trackers to find peers, so a link without a
            # live tracker sits at 0 B/s with 0 peers and looks "slow" instead
            # of failing.  DHT, local peer discovery and PEX are what let a
            # magnet bootstrap without a tracker.
            "--enable-dht=true",
            "--enable-dht6=true",
            "--bt-enable-lpd=true",
            "--enable-peer-exchange=true",
            # 100 peers instead of the default 55, and keep asking for more until
            # the swarm delivers ~20 MiB/s instead of the default 50 KiB/s
            # threshold, so a fast link is not throttled by too few connections.
            # 1M was too low: on a 625 Mbit line aria2 stopped expanding the peer
            # set as soon as it passed 1 MiB/s and then sat there.
            "--bt-max-peers=100",
            "--bt-request-peer-speed-limit=20M",
            # Torrents are written as scattered pieces, so without a write-behind
            # buffer each piece is a separate small write.  The cache absorbs
            # that; it is memory, not a bandwidth cap.  64 MiB keeps the app
            # usable on a machine with little free RAM.
            "--disk-cache=64M",
            # Multi-file torrents (game dumps with thousands of files) hit
            # aria2's 100-file default and stall on reopening them.
            "--bt-max-open-files=200",
            # A fixed port range instead of aria2's random pick inside
            # 6881-6999, so one router forward can be set up once and keep
            # working.  Inbound peers are what a swarm's seeder slots need; this
            # build of aria2 has no UPnP/NAT-PMP, so the forward is manual.
            "--listen-port=6881-6889",
            "--dht-listen-port=6881-6889",
            # Drop a dead tracker quickly (defaults are 60s each) so the healthy
            # ones are used for discovery instead of being waited on.
            "--bt-tracker-connect-timeout=10",
            "--bt-tracker-timeout=10",
            "--seed-time=0",
            "--file-allocation=none",
            "--auto-file-renaming=false",
            "--allow-overwrite=false",
            "--continue=true",
            # Fail fast on a dead peer, then retry, instead of hanging on one
            # socket for aria2's 60s default.
            "--connect-timeout=10",
            "--timeout=30",
            "--max-tries=5",
            "--retry-wait=3",
            "--summary-interval=0",
            "--console-log-level=warn",
            # The engine exits with us, so a crash cannot leave an orphan
            # seeding in the background.
            f"--stop-with-process={os.getpid()}",
            "--no-conf=true",
        ]

    def _popen_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return kwargs

    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return
        ensure_directory(self.directory)
        try:
            self.process = self._spawn(self.command(), **self._popen_kwargs())
        except OSError as exc:
            raise EngineUnavailable(f"The torrent engine could not start: {exc}") from exc

        deadline = self._clock() + ENGINE_START_TIMEOUT
        last: Exception | None = None
        while self._clock() < deadline:
            if self.process.poll() is not None:
                raise TorrentError(
                    "The torrent engine exited immediately after starting. "
                    "Check that it can write to the download folder."
                )
            try:
                self.client.version()
                return
            except TorrentError as exc:
                last = exc
                time.sleep(0.15)
        self.stop()
        raise TorrentError(
            "The torrent engine did not become ready in time"
            + (f": {last}" if last else ".")
        )

    def stop(self) -> None:
        process, self.process = self.process, None
        if process is None:
            return
        try:
            if process.poll() is None:
                try:
                    self.client.shutdown()
                except TorrentError:
                    pass
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
        finally:
            try:
                process.wait(timeout=1)
            except (subprocess.TimeoutExpired, OSError):
                pass

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None


# --------------------------------------------------------------------------
# Progress and queue records
# --------------------------------------------------------------------------


def _as_int(value: Any) -> int:
    try:
        return max(0, int(str(value)))
    except (TypeError, ValueError):
        return 0


@dataclass
class TorrentProgress:
    status: str = STATUS_QUEUED
    total: int = 0
    completed: int = 0
    speed: float = 0.0
    peers: int = 0
    name: str = ""
    error: str = ""

    @property
    def fraction(self) -> float:
        if self.total <= 0:
            return 1.0 if self.status == STATUS_COMPLETED else 0.0
        return max(0.0, min(1.0, self.completed / self.total))


def progress_from_status(raw: Mapping[str, Any], *, default_status: str) -> TorrentProgress:
    """Translate an aria2 status document into this app's vocabulary.

    aria2 reports ``waiting``/``active``/``paused``/``complete``/``error``/
    ``removed``.  Anything unknown keeps the previous status so a transient
    reply cannot make a running download look finished.
    """
    state = str(raw.get("status") or "").strip().lower()
    mapping = {
        "active": STATUS_DOWNLOADING,
        "waiting": STATUS_QUEUED,
        "paused": STATUS_PAUSED,
        "complete": STATUS_COMPLETED,
        "error": STATUS_FAILED,
        "removed": STATUS_FAILED,
    }
    status = mapping.get(state, default_status)
    name = ""
    bittorrent = raw.get("bittorrent")
    if isinstance(bittorrent, Mapping):
        info = bittorrent.get("info")
        if isinstance(info, Mapping):
            name = str(info.get("name") or "")
    if not name:
        files = raw.get("files")
        if isinstance(files, Sequence) and files and isinstance(files[0], Mapping):
            path = str(files[0].get("path") or "")
            if path:
                name = path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    error = str(raw.get("errorMessage") or "")
    if status == STATUS_FAILED and not error:
        code = raw.get("errorCode")
        error = f"The engine stopped with code {code}." if code else "The download stopped."
    return TorrentProgress(
        status=status,
        total=_as_int(raw.get("totalLength")),
        completed=_as_int(raw.get("completedLength")),
        speed=float(_as_int(raw.get("downloadSpeed"))),
        peers=_as_int(raw.get("connections")),
        name=name,
        error=error,
    )


@dataclass
class TorrentTask:
    """One queued magnet download.  Persisted without secrets or GIDs."""

    task_id: str
    magnet: str
    infohash: str
    title: str = ""
    save_dir: str = ""
    status: str = STATUS_QUEUED
    added_at: float = 0.0
    updated_at: float = 0.0
    total: int = 0
    completed: int = 0
    speed: float = 0.0
    peers: int = 0
    error: str = ""
    gid: str = ""
    folder: str = ""

    @property
    def fraction(self) -> float:
        if self.total <= 0:
            return 1.0 if self.status == STATUS_COMPLETED else 0.0
        return max(0.0, min(1.0, self.completed / self.total))

    @property
    def finished(self) -> bool:
        return self.status in _TERMINAL

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "magnet": self.magnet,
            "infohash": self.infohash,
            "title": self.title,
            "save_dir": self.save_dir,
            "status": self.status,
            "added_at": self.added_at,
            "updated_at": self.updated_at,
            "total": self.total,
            "completed": self.completed,
            "speed": self.speed,
            "peers": self.peers,
            "error": self.error,
            "folder": self.folder,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "TorrentTask | None":
        if not isinstance(raw, Mapping):
            return None
        task_id = str(raw.get("task_id") or "").strip()
        uri = str(raw.get("magnet") or "").strip()
        if not task_id or not uri:
            return None
        link = magnetlib.magnet_from_entry(uri, raw.get("infohash"))
        if link is None:
            return None
        status = str(raw.get("status") or STATUS_QUEUED)
        if status not in STATUSES:
            status = STATUS_QUEUED
        return cls(
            task_id=task_id,
            magnet=link.uri,
            infohash=link.infohash,
            title=str(raw.get("title") or "")[:300],
            save_dir=str(raw.get("save_dir") or ""),
            status=status,
            added_at=float(raw.get("added_at") or 0.0),
            updated_at=float(raw.get("updated_at") or 0.0),
            total=_as_int(raw.get("total")),
            completed=_as_int(raw.get("completed")),
            speed=float(raw.get("speed") or 0.0),
            peers=_as_int(raw.get("peers")),
            error=str(raw.get("error") or "")[:400],
            folder=str(raw.get("folder") or ""),
        )


def _new_task_id(infohash: str) -> str:
    return f"{infohash[:12]}-{secrets.token_hex(3)}"


def _default_native_engine(directory: Path) -> Any:
    """Start the built-in engine; imported lazily so startup stays cheap."""
    from .bittorrent import NativeEngine

    return NativeEngine(directory)


# --------------------------------------------------------------------------
# Manager
# --------------------------------------------------------------------------


class TorrentManager:
    """One-at-a-time torrent queue with persistence.

    The queue survives restarts, but nothing restarts by itself: unfinished
    tasks come back as paused, exactly like the HTTP downloader.
    """

    def __init__(
        self,
        path: Path | str | None = None,
        download_root: Path | str | None = None,
        *,
        engine_path: str = "",
        engine_info: EngineInfo | None = None,
        engine_factory: Callable[[str, Path], Any] | None = None,
        native_factory: Callable[[Path], Any] | None = None,
        clock: Callable[[], float] = time.time,
        on_complete: Callable[[TorrentTask], None] | None = None,
    ) -> None:
        self.path = Path(path).expanduser() if path is not None else None
        self.download_root = (
            Path(download_root).expanduser() if download_root is not None else None
        )
        self._engine_path = engine_path
        self._engine_info: EngineInfo | None = engine_info
        #: An explicitly injected result (tests, or a caller that already
        #: probed the engine) is not rediscovered on every poll.
        self._engine_fixed = engine_info is not None
        self._engine_factory = engine_factory or (
            lambda path, directory: Aria2Engine(path, directory)
        )
        self._native_factory = native_factory or _default_native_engine
        self._clock = clock
        self._on_complete = on_complete
        self._lock = threading.RLock()
        self._tasks: list[TorrentTask] = []
        self._engine: Any = None
        #: When each task first went peerless, so a stall can be attributed.
        self._starved: dict[str, float] = {}
        self._load()

    # -- persistence --------------------------------------------------------

    def _load(self) -> None:
        if self.path is None:
            return
        data = read_json(self.path, None)
        if not isinstance(data, dict):
            return
        raw_tasks = data.get("tasks")
        if not isinstance(raw_tasks, list):
            return
        restored: list[TorrentTask] = []
        seen: set[str] = set()
        for raw in raw_tasks:
            task = TorrentTask.from_dict(raw)
            if task is None or task.task_id in seen:
                continue
            seen.add(task.task_id)
            if task.status in (STATUS_DOWNLOADING, STATUS_QUEUED):
                # A queue entry from a previous process never resumes itself.
                task.status = STATUS_PAUSED
                task.speed = 0.0
                task.peers = 0
            restored.append(task)
        self._tasks = restored[:MAX_TASKS]

    def save(self) -> bool:
        if self.path is None:
            return False
        try:
            atomic_write_json(
                self.path,
                {"version": QUEUE_VERSION, "tasks": [t.to_dict() for t in self._tasks]},
            )
        except OSError:
            return False
        return True

    # -- inspection ---------------------------------------------------------

    def tasks(self) -> list[TorrentTask]:
        with self._lock:
            return list(self._tasks)

    def get(self, task_id: str) -> TorrentTask | None:
        with self._lock:
            return next((t for t in self._tasks if t.task_id == task_id), None)

    def has_unfinished(self) -> bool:
        with self._lock:
            return any(not t.finished for t in self._tasks)

    def active(self) -> TorrentTask | None:
        with self._lock:
            return next(
                (t for t in self._tasks if t.status == STATUS_DOWNLOADING), None
            )

    def engine_status(self, refresh: bool = False) -> EngineInfo:
        if self._engine_fixed and self._engine_info is not None:
            return self._engine_info
        if self._engine_info is None or refresh:
            self._engine_info = find_engine(self._engine_path)
        return self._engine_info

    def set_engine_path(self, path: str) -> EngineInfo:
        self._engine_path = path or ""
        self._engine_info = None
        self._engine_fixed = False
        return self.engine_status(refresh=True)

    # -- queue --------------------------------------------------------------

    def add(
        self,
        magnet_uri: str,
        *,
        title: str = "",
        save_dir: Path | str | None = None,
    ) -> TorrentTask:
        """Queue one magnet link.  Duplicate infohashes are refused."""
        link = magnetlib.parse_magnet(magnet_uri)
        target = self._resolve_dir(save_dir)
        with self._lock:
            if any(t.infohash == link.infohash and not t.finished for t in self._tasks):
                existing = next(
                    t for t in self._tasks if t.infohash == link.infohash and not t.finished
                )
                raise TorrentError(
                    f"That magnet is already in the torrent queue ({existing.title or existing.infohash[:12]})."
                )
            if len(self._tasks) >= MAX_TASKS:
                raise TorrentError(
                    f"The torrent queue is full ({MAX_TASKS} entries). Remove some first."
                )
            now = self._clock()
            task = TorrentTask(
                task_id=_new_task_id(link.infohash),
                magnet=link.uri,
                infohash=link.infohash,
                title=(title or link.display_name or link.infohash[:12])[:300],
                save_dir=str(target),
                status=STATUS_QUEUED,
                added_at=now,
                updated_at=now,
            )
            self._tasks.append(task)
            self.save()
            return task

    def _resolve_dir(self, save_dir: Path | str | None) -> Path:
        base = Path(save_dir) if save_dir else self.download_root
        if base is None:
            base = Path.home() / "Downloads"
        return check_download_dir(base)

    def _ensure_engine(self) -> Any:
        if self._engine is not None and getattr(self._engine, "running", False):
            return self._engine
        info = self.engine_status(refresh=True)
        if not info.available:
            raise EngineUnavailable(info.reason or _install_hint())
        directory = self.download_root or Path.home() / "Downloads"
        ensure_directory(directory)
        if info.kind == "native":
            engine = self._native_factory(directory)
        else:
            engine = self._engine_factory(info.path, directory)
        engine.start()
        self._engine = engine
        return engine

    def start(self, task_id: str) -> TorrentTask:
        """Start one task, pausing whatever else is downloading."""
        with self._lock:
            task = self.get(task_id)
            if task is None:
                raise TorrentError("That torrent task is no longer in the queue.")
            if task.status == STATUS_COMPLETED:
                raise TorrentError("That torrent has already finished.")
            for other in self._tasks:
                if other is not task and other.status == STATUS_DOWNLOADING:
                    self._pause_locked(other)
            engine = self._ensure_engine()
            directory = self._resolve_dir(task.save_dir or None)
            ensure_directory(directory)
            try:
                gid = engine.client.add_uri(task.magnet, str(directory))
            except TorrentError as exc:
                task.status = STATUS_FAILED
                task.error = str(exc)
                task.updated_at = self._clock()
                self.save()
                raise TorrentError(str(exc)) from exc
            task.gid = gid
            task.save_dir = str(directory)
            task.status = STATUS_DOWNLOADING
            task.error = ""
            task.updated_at = self._clock()
            self.save()
            return task

    def _pause_locked(self, task: TorrentTask) -> None:
        engine = self._engine
        if engine is not None and task.gid:
            try:
                engine.client.pause(task.gid)
            except TorrentError:
                pass
        task.status = STATUS_PAUSED
        task.speed = 0.0
        task.peers = 0
        task.updated_at = self._clock()

    def pause(self, task_id: str) -> TorrentTask:
        with self._lock:
            task = self.get(task_id)
            if task is None:
                raise TorrentError("That torrent task is no longer in the queue.")
            if task.status != STATUS_DOWNLOADING:
                return task
            self._pause_locked(task)
            self.save()
            return task

    def resume(self, task_id: str) -> TorrentTask:
        """Continue a paused task, re-adding the magnet if the engine lost it."""
        with self._lock:
            task = self.get(task_id)
            if task is None:
                raise TorrentError("That torrent task is no longer in the queue.")
            if task.status != STATUS_PAUSED:
                return self.start(task_id)
            engine = self._engine
            if engine is not None and task.gid:
                for other in self._tasks:
                    if other is not task and other.status == STATUS_DOWNLOADING:
                        self._pause_locked(other)
                try:
                    engine.client.unpause(task.gid)
                    task.status = STATUS_DOWNLOADING
                    task.error = ""
                    task.updated_at = self._clock()
                    self.save()
                    return task
                except TorrentError:
                    # The engine restarted and no longer knows this GID; fall
                    # through and add the magnet again so existing files resume.
                    task.gid = ""
            return self.start(task_id)

    def remove(self, task_id: str) -> bool:
        """Forget a queue entry.  Downloaded files are never deleted."""
        with self._lock:
            task = self.get(task_id)
            if task is None:
                return False
            if task.gid and self._engine is not None and task.status == STATUS_DOWNLOADING:
                try:
                    self._engine.client.remove(task.gid)
                except TorrentError:
                    pass
            self._tasks.remove(task)
            self.save()
            return True

    def clear_finished(self) -> int:
        with self._lock:
            before = len(self._tasks)
            self._tasks = [t for t in self._tasks if not t.finished]
            removed = before - len(self._tasks)
            if removed:
                self.save()
            return removed

    # -- polling ------------------------------------------------------------

    def refresh(self) -> list[TorrentTask]:
        """Poll the engine for the active task.  Safe to call from a timer."""
        completed: list[TorrentTask] = []
        with self._lock:
            engine = self._engine
            if engine is None or not getattr(engine, "running", False):
                return self.tasks()
            for task in self._tasks:
                if task.status != STATUS_DOWNLOADING or not task.gid:
                    continue
                try:
                    raw = engine.client.tell(task.gid)
                except TorrentError as exc:
                    task.status = STATUS_FAILED
                    task.error = str(exc)
                    task.updated_at = self._clock()
                    continue
                # A magnet first downloads a small metadata torrent, then
                # aria2 hands over to a new GID for the real payload.  Following
                # that switch is what makes progress and completion real.
                followed = raw.get("followedBy")
                if isinstance(followed, Sequence) and followed:
                    next_gid = str(followed[0] or "").strip()
                    if next_gid and next_gid != task.gid:
                        task.gid = next_gid
                        task.updated_at = self._clock()
                        try:
                            raw = engine.client.tell(next_gid)
                        except TorrentError:
                            continue
                before = task.completed
                progress = progress_from_status(raw, default_status=task.status)
                task.total = progress.total
                task.completed = progress.completed
                task.speed = progress.speed
                task.peers = progress.peers
                task.updated_at = self._clock()
                if (
                    progress.status == STATUS_DOWNLOADING
                    and progress.peers == 0
                    and progress.completed == before
                ):
                    first = self._starved.setdefault(task.task_id, task.updated_at)
                    if task.updated_at - first >= NO_PEER_TIMEOUT:
                        task.status = STATUS_FAILED
                        task.error = (
                            "No peers for {:d} minutes: DHT and this magnet's own "
                            "trackers found nobody to download from, so the swarm "
                            "is dead or unseeded. Try another release."
                        ).format(int(NO_PEER_TIMEOUT // 60))
                        task.speed = 0.0
                        self._starved.pop(task.task_id, None)
                else:
                    self._starved.pop(task.task_id, None)
                if progress.name and (
                    not task.folder or task.folder.startswith("[METADATA]")
                ):
                    task.folder = progress.name
                if progress.status in (STATUS_COMPLETED, STATUS_FAILED):
                    task.status = progress.status
                    task.speed = 0.0
                    task.peers = 0
                    task.error = progress.error if task.status == STATUS_FAILED else ""
                    if task.status == STATUS_COMPLETED and progress.total:
                        task.completed = progress.total
                    if task.status == STATUS_COMPLETED:
                        completed.append(task)
            if self._tasks:
                self.save()
        for task in completed:
            if self._on_complete is not None:
                try:
                    self._on_complete(task)
                except Exception:
                    # A UI callback must not break polling.
                    pass
        return self.tasks()

    def close(self) -> None:
        engine, self._engine = self._engine, None
        if engine is not None:
            try:
                engine.stop()
            except Exception:
                pass


__all__ = [
    "Aria2Client",
    "Aria2Engine",
    "EngineInfo",
    "EngineUnavailable",
    "TorrentError",
    "TorrentManager",
    "TorrentProgress",
    "TorrentTask",
    "find_engine",
    "progress_from_status",
    "STATUS_COMPLETED",
    "STATUS_DOWNLOADING",
    "STATUS_FAILED",
    "STATUS_PAUSED",
    "STATUS_QUEUED",
]
