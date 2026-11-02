"""Resumable, cancellable downloader for archive.org files.

Large files are fetched with a small number of parallel byte-range requests,
because archive.org throttles each connection rather than the whole transfer:
measured on this project's own hardware, one connection reached ~11 MiB/s while
16 connections on the same file reached ~30 MiB/s.  Small files, servers that
ignore ``Range``, and partial files left by an older sequential run keep using
the single-stream path unchanged.

Both paths share the same guarantees: partial data goes to ``<file>.part``, is
renamed only after a complete transfer, and an existing final file is never
overwritten.  Parallel progress is recorded in ``<file>.part.segments.json`` so
a paused or crashed transfer resumes the ranges it still needs instead of
starting over.
"""

from __future__ import annotations

import collections
import json
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Sequence

from . import __version__
from .util import (
    DownloadError,
    atomic_write_json,
    check_download_dir,
    ensure_directory,
    human_size,
    is_within,
    safe_relpath,
    validate_identifier,
)

CHUNK_SIZE = 256 * 1024
PROGRESS_INTERVAL = 0.2
#: Below this size a parallel transfer costs more in requests than it saves.
SEGMENT_THRESHOLD = 8 * 1024 * 1024
#: Ranges smaller than this waste connections on handshake overhead.
MIN_SEGMENT_BYTES = 256 * 1024
#: archive.org tolerates a handful of connections per client; do not raise this
#: without re-measuring, since it is a public archive and the gain flattens.
DEFAULT_CONNECTIONS = 8
MAX_CONNECTIONS = 16
MAX_RETRIES = 2
#: How many times the user may put a failed/cancelled task back in the queue.
#: Automatic backoff inside one attempt is separate (see ``MAX_RETRIES``).
DEFAULT_RETRY_LIMIT = 3
USER_AGENT = f"Lumora/{__version__} (personal desktop client)"
DISK_RESERVE_BYTES = 64 * 1024 * 1024
STATE_SAVE_INTERVAL = 2.0
QUEUE_VERSION = 1
_CONTENT_RANGE = re.compile(r"^bytes\s+(\d+)-(\d+)/(\d+|\*)$")


class DownloadStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in (DownloadStatus.COMPLETED, DownloadStatus.FAILED, DownloadStatus.CANCELLED)


class _HttpStatusError(DownloadError):
    """A DownloadError that remembers the HTTP status code."""

    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.code = code


class QueueStore:
    """Small atomic JSON store for the download queue.

    The store is deliberately dumb: it only reads/writes a list of plain
    dictionaries.  Validation and recovery rules live in :class:`DownloadManager`
    so a hand-edited or older state file can never inject unsafe paths.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def load(self) -> list[dict[str, Any]]:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError, TypeError):
            # A missing, unreadable, or corrupt queue must not stop startup.
            return []
        if not isinstance(data, dict):
            return []
        tasks = data.get("tasks")
        if not isinstance(tasks, list):
            return []
        return [entry for entry in tasks if isinstance(entry, dict)]

    def save(self, tasks: list[dict[str, Any]]) -> None:
        atomic_write_json(self.path, {"version": QUEUE_VERSION, "tasks": tasks})


def _free_space(path: Path | str) -> int | None:
    """Free bytes on the filesystem containing *path*, or None if unknown."""
    try:
        return int(shutil.disk_usage(str(path)).free)
    except OSError:
        return None


def _parse_content_range(value: str) -> tuple[int, int, int | None] | None:
    match = _CONTENT_RANGE.match(value.strip())
    if not match:
        return None
    start, end = int(match.group(1)), int(match.group(2))
    raw_total = match.group(3)
    total = int(raw_total) if raw_total.isdigit() else None
    if end < start:
        return None
    return start, end, total


@dataclass(frozen=True)
class TaskSnapshot:
    id: str
    identifier: str
    filename: str
    folder: str
    final_path: str
    part_path: str
    url: str
    downloaded: int
    total: int | None
    status: DownloadStatus
    speed: float
    eta: float | None
    error: str | None
    created_at: float
    #: Manual retries used so far, and the manager's configured ceiling.
    attempts: int = 0
    retry_limit: int = DEFAULT_RETRY_LIMIT

    @property
    def progress(self) -> float | None:
        if not self.total:
            return None
        return max(0.0, min(1.0, self.downloaded / self.total))

    @property
    def can_retry(self) -> bool:
        """True when retrying this task is still within the configured limit."""
        return (
            self.status in (DownloadStatus.FAILED, DownloadStatus.CANCELLED)
            and self.attempts < self.retry_limit
        )


class _PauseRequested(Exception):
    pass


class _SegmentAborted(Exception):
    """Another worker hit a fatal error, so this range stopped early."""


class _CancelRequested(Exception):
    pass


class _Task:
    def __init__(
        self,
        *,
        task_id: str,
        identifier: str,
        filename: str,
        url: str,
        folder: Path,
        final_path: Path,
        total: int | None,
    ) -> None:
        self.id = task_id
        self.identifier = identifier
        self.filename = filename
        self.url = url
        self.folder = folder
        self.final_path = final_path
        self.part_path = final_path.with_name(final_path.name + ".part")
        self.total = total
        self.downloaded = 0
        if self.part_path.exists():
            try:
                self.downloaded = self.part_path.stat().st_size
            except OSError:
                self.downloaded = 0
        self.status = DownloadStatus.QUEUED
        self.speed = 0.0
        self.eta: float | None = None
        self.error: str | None = None
        self.created_at = time.time()
        self.attempts = 0
        self.retry_limit = DEFAULT_RETRY_LIMIT
        self.pause_event = threading.Event()
        self.cancel_event = threading.Event()

    def snapshot(self) -> TaskSnapshot:
        return TaskSnapshot(
            id=self.id,
            identifier=self.identifier,
            filename=self.filename,
            folder=str(self.folder),
            final_path=str(self.final_path),
            part_path=str(self.part_path),
            url=self.url,
            downloaded=self.downloaded,
            total=self.total,
            status=self.status,
            speed=self.speed,
            eta=self.eta,
            error=self.error,
            created_at=self.created_at,
            attempts=max(0, int(self.attempts)),
            retry_limit=max(0, int(self.retry_limit)),
        )


def default_url_validator(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    return (
        parsed.scheme == "https"
        and (host == "archive.org" or host.endswith(".archive.org"))
        and parsed.path.startswith("/download/")
    )


def host_url_validator(url: str, allowed_hosts: Sequence[str]) -> bool:
    """Allow an HTTPS download only on a host the caller explicitly named."""
    try:
        parsed = urllib.parse.urlparse(url)
        port = parsed.port
    except ValueError:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host or parsed.scheme != "https" or parsed.username or parsed.password:
        return False
    if port not in (None, 443):
        return False
    for allowed in allowed_hosts:
        name = str(allowed).strip().lower().rstrip(".")
        if name and (host == name or host.endswith("." + name)):
            return True
    return False


class DownloadManager:
    """Queue downloads, one at a time, with pause/resume/cancel/retry."""

    def __init__(
        self,
        observer: Callable[[], None] | None = None,
        *,
        validate_urls: bool = True,
        timeout: float = 30.0,
        queue_path: Path | None = None,
        url_resolver: Callable[[TaskSnapshot], str | None] | None = None,
        retry_limit: int = DEFAULT_RETRY_LIMIT,
        connections: int = DEFAULT_CONNECTIONS,
        segment_threshold: int = SEGMENT_THRESHOLD,
    ) -> None:
        self._observer = observer
        self._validate_urls = validate_urls
        self._timeout = timeout
        self._url_resolver = url_resolver
        try:
            self._connections = max(1, min(MAX_CONNECTIONS, int(connections)))
        except (TypeError, ValueError):
            self._connections = DEFAULT_CONNECTIONS
        try:
            self._segment_threshold = max(1, int(segment_threshold))
        except (TypeError, ValueError):
            self._segment_threshold = SEGMENT_THRESHOLD
        try:
            self._retry_limit = max(0, min(20, int(retry_limit)))
        except (TypeError, ValueError):
            self._retry_limit = DEFAULT_RETRY_LIMIT
        self.state_path: Path | None = Path(queue_path) if queue_path is not None else None
        self._store = QueueStore(self.state_path) if self.state_path is not None else None
        self._last_save = 0.0
        self._tasks: dict[str, _Task] = {}
        self._order: list[str] = []
        self._pending: collections.deque[str] = collections.deque()
        self._lock = threading.RLock()
        self._cv = threading.Condition(self._lock)
        self._worker: threading.Thread | None = None
        self._stop = False
        self._load_state()

    # -- public API ---------------------------------------------------------

    def _duplicate_locked(self, final_path: Path, identifier: str,
                          url: str) -> _Task | None:
        """An unfinished or queued task that would duplicate this download."""
        for task in self._tasks.values():
            if task.status.terminal:
                continue
            if task.final_path == final_path:
                return task
            if task.identifier == identifier and task.url == url:
                return task
        return None

    def add(
        self,
        *,
        identifier: str,
        filename: str,
        url: str,
        download_dir: Path | str,
        total_size: int | None = None,
        folder_name: str | None = None,
        allowed_hosts: Sequence[str] | None = None,
    ) -> str:
        validate_identifier(identifier)
        relpath = safe_relpath(filename)
        if self._validate_urls:
            if allowed_hosts is None:
                if not default_url_validator(url):
                    raise DownloadError("Refusing a download URL outside archive.org.")
            elif not host_url_validator(url, allowed_hosts):
                raise DownloadError(
                    "Refusing a download URL outside the hosts approved for "
                    "this catalog source."
                )
        root = check_download_dir(download_dir)
        folder = ensure_directory(root / (folder_name or identifier))
        if not is_within(folder, root):
            raise DownloadError("Refusing to write outside the chosen download directory.")
        final_path = (folder / relpath).resolve()
        if not is_within(final_path, folder):
            raise DownloadError("Refusing an unsafe archive file path.")
        if final_path.exists():
            raise DownloadError(
                f"“{final_path.name}” already exists in this folder. "
                "It was not overwritten."
            )

        with self._lock:
            duplicate = self._duplicate_locked(final_path, identifier, url)
            if duplicate is not None:
                if duplicate.final_path == final_path:
                    raise DownloadError(
                        f"“{final_path.name}” is already in the download queue "
                        f"({duplicate.status.value})."
                    )
                raise DownloadError(
                    "This download is already in the queue "
                    f"({duplicate.status.value})."
                )
            final_path.parent.mkdir(parents=True, exist_ok=True)
            task_id = uuid.uuid4().hex[:10]
            task = _Task(
                task_id=task_id,
                identifier=identifier,
                filename=filename,
                url=url,
                folder=folder,
                final_path=final_path,
                total=total_size,
            )
            task.retry_limit = self._retry_limit
            self._tasks[task_id] = task
            self._order.append(task_id)
            self._pending.append(task_id)
            self._ensure_worker_locked()
        self._save_state()
        self._notify()
        return task_id

    def pause(self, task_id: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task or task.status != DownloadStatus.RUNNING:
                return
            task.pause_event.set()
            task.status = DownloadStatus.PAUSED
            task.speed = 0.0
            task.eta = None
        self._save_state()
        self._notify()

    def resume(self, task_id: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task or task.status != DownloadStatus.PAUSED:
                return
            task.pause_event.clear()
            task.status = DownloadStatus.QUEUED
            task.error = None
            self._pending.append(task_id)
            self._ensure_worker_locked()
        self._save_state()
        self._notify()

    def cancel(self, task_id: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task or task.status.terminal:
                return
            task.cancel_event.set()
            task.pause_event.clear()
            task.status = DownloadStatus.CANCELLED
            task.speed = 0.0
            task.eta = None
        self._save_state()
        self._notify()

    def retry(self, task_id: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task or task.status not in (DownloadStatus.FAILED, DownloadStatus.CANCELLED):
                return
            task.retry_limit = self._retry_limit
            if task.attempts >= task.retry_limit:
                task.error = (
                    f"Retry limit reached ({task.retry_limit} attempt"
                    f"{'s' if task.retry_limit != 1 else ''}). "
                    "The task was left as it is."
                )
                self._save_state()
                self._notify()
                return
            if task.final_path.exists():
                task.error = "A file with this name already exists; not overwriting it."
                self._save_state()
                self._notify()
                return
            task.attempts += 1
            task.cancel_event.clear()
            task.pause_event.clear()
            task.error = None
            task.speed = 0.0
            task.eta = None
            if task.part_path.exists():
                try:
                    task.downloaded = task.part_path.stat().st_size
                except OSError:
                    task.downloaded = 0
            task.status = DownloadStatus.QUEUED
            self._pending.append(task_id)
            self._ensure_worker_locked()
        self._save_state()
        self._notify()

    def remove(self, task_id: str) -> bool:
        """Remove a finished task from the queue (does not delete files)."""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task or not task.status.terminal:
                return False
            self._tasks.pop(task_id, None)
            try:
                self._order.remove(task_id)
            except ValueError:
                pass
        self._save_state()
        self._notify()
        return True

    def clear_finished(self) -> int:
        with self._lock:
            finished = [tid for tid, t in self._tasks.items() if t.status.terminal]
            for tid in finished:
                self._tasks.pop(tid, None)
                try:
                    self._order.remove(tid)
                except ValueError:
                    pass
        if finished:
            self._save_state()
            self._notify()
        return len(finished)

    def snapshots(self) -> list[TaskSnapshot]:
        with self._lock:
            return [self._tasks[tid].snapshot() for tid in self._order if tid in self._tasks]

    def get(self, task_id: str) -> TaskSnapshot | None:
        with self._lock:
            task = self._tasks.get(task_id)
            return task.snapshot() if task else None

    def set_retry_limit(self, value: int) -> None:
        """Apply a new manual-retry ceiling to existing and future tasks."""
        try:
            limit = max(0, min(20, int(value)))
        except (TypeError, ValueError):
            return
        with self._lock:
            self._retry_limit = limit
            for task in self._tasks.values():
                task.retry_limit = limit
        self._save_state()
        self._notify()

    def has_unfinished(self) -> bool:
        """True when at least one queued/running/paused task remains."""
        with self._lock:
            return any(not task.status.terminal for task in self._tasks.values())

    def set_url(self, task_id: str, url: str) -> bool:
        """Replace a task's URL (used when an archive.org link expires)."""
        if self._validate_urls and not default_url_validator(url):
            raise DownloadError("Refusing a download URL outside archive.org.")
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False
            task.url = url
        self._save_state()
        self._notify()
        return True

    def refresh_urls(
        self, resolver: Callable[[TaskSnapshot], str | None]
    ) -> int:
        """Ask *resolver* for fresh URLs for every unfinished task.

        The downloader itself performs no network calls; the caller supplies a
        resolver that may talk to archive.org.  Returns the number of tasks whose
        URL changed.
        """
        snapshots = [snap for snap in self.snapshots() if not snap.status.terminal]
        refreshed = 0
        for snap in snapshots:
            try:
                candidate = resolver(snap)
            except Exception:
                continue
            if not isinstance(candidate, str) or not candidate:
                continue
            if candidate == snap.url:
                continue
            if self._validate_urls and not default_url_validator(candidate):
                continue
            with self._lock:
                task = self._tasks.get(snap.id)
                if not task or task.status.terminal:
                    continue
                task.url = candidate
            refreshed += 1
        if refreshed:
            self._save_state()
            self._notify()
        return refreshed

    def shutdown(self) -> None:
        with self._lock:
            self._stop = True
            for task in self._tasks.values():
                if not task.status.terminal:
                    task.cancel_event.set()
                    task.pause_event.clear()
            self._cv.notify_all()
        worker = self._worker
        if worker and worker.is_alive():
            worker.join(timeout=5.0)

    # -- persistence --------------------------------------------------------

    def _serialize_locked(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for task_id in self._order:
            task = self._tasks.get(task_id)
            if task is None:
                continue
            entries.append(
                {
                    "id": task.id,
                    "identifier": task.identifier,
                    "filename": task.filename,
                    "url": task.url,
                    "folder": str(task.folder),
                    "final_path": str(task.final_path),
                    "part_path": str(task.part_path),
                    "downloaded": int(task.downloaded),
                    "total": task.total,
                    "status": task.status.value,
                    "error": task.error,
                    "created_at": float(task.created_at),
                    "attempts": int(task.attempts),
                }
            )
        return entries

    def _save_state(self) -> None:
        if self._store is None:
            return
        with self._lock:
            self._last_save = time.monotonic()
            payload = self._serialize_locked()
            try:
                self._store.save(payload)
            except OSError:
                # Persistence is best-effort; a full disk must not kill a
                # download that is otherwise progressing.
                pass

    def _maybe_save_state(self) -> None:
        if self._store is None:
            return
        with self._lock:
            if time.monotonic() - self._last_save < STATE_SAVE_INTERVAL:
                return
        self._save_state()

    def _load_state(self) -> None:
        if self._store is None:
            return
        recovered: dict[str, _Task] = {}
        order: list[str] = []
        for entry in self._store.load():
            task = self._recover_task(entry)
            if task is None or task.id in recovered:
                continue
            recovered[task.id] = task
            order.append(task.id)
        with self._lock:
            self._tasks = recovered
            self._order = order
            self._pending.clear()

    def _recover_task(self, entry: dict[str, Any]) -> _Task | None:
        try:
            task_id = entry["id"]
            identifier = entry["identifier"]
            filename = entry["filename"]
            url = entry["url"]
            folder = Path(entry["folder"])
            final_path = Path(entry["final_path"])
            raw_part = entry.get("part_path")
            part_path = Path(raw_part) if raw_part else final_path.with_name(final_path.name + ".part")
            status = DownloadStatus(str(entry["status"]))
        except (KeyError, TypeError, ValueError):
            return None
        if not isinstance(task_id, str) or not task_id:
            return None
        if not all(isinstance(value, str) for value in (identifier, filename, url)):
            return None
        if not (folder.is_absolute() and final_path.is_absolute() and part_path.is_absolute()):
            return None
        if final_path == folder or not is_within(final_path, folder):
            return None
        if not is_within(part_path, folder):
            return None
        try:
            validate_identifier(identifier)
        except ValueError:
            return None

        raw_total = entry.get("total")
        if raw_total is not None:
            if isinstance(raw_total, bool) or not isinstance(raw_total, int) or raw_total < 0:
                return None
            total: int | None = raw_total
        else:
            total = None

        task = _Task(
            task_id=task_id,
            identifier=identifier,
            filename=filename,
            url=url,
            folder=folder,
            final_path=final_path,
            total=total,
        )
        task.part_path = part_path
        task.status = status
        task.created_at = _as_float(entry.get("created_at"), time.time())
        task.retry_limit = self._retry_limit
        task.attempts = _as_int_value(entry.get("attempts"), 0)
        error = entry.get("error")
        task.error = error if isinstance(error, str) else None

        saved_downloaded = entry.get("downloaded", 0)
        if isinstance(saved_downloaded, bool) or not isinstance(saved_downloaded, int) or saved_downloaded < 0:
            saved_downloaded = 0
        task.downloaded = saved_downloaded

        part_size: int | None = None
        if part_path.exists():
            try:
                part_size = part_path.stat().st_size
            except OSError:
                part_size = None

        if status == DownloadStatus.QUEUED or status == DownloadStatus.RUNNING:
            task.status = DownloadStatus.PAUSED
            task.error = None

        if task.status != DownloadStatus.COMPLETED:
            if part_size is not None:
                task.downloaded = part_size
            if total is not None and part_size is not None and part_size > total:
                try:
                    part_path.unlink()
                except OSError:
                    pass
                task.downloaded = 0

        if task.status == DownloadStatus.COMPLETED:
            if not final_path.exists():
                task.status = DownloadStatus.FAILED
                task.error = "The downloaded file is no longer present at its saved location."
        elif final_path.exists():
            # Never delete or overwrite a final file during recovery.
            task.status = DownloadStatus.FAILED
            task.error = "A file with this name already exists; not overwriting it."

        task.speed = 0.0
        task.eta = None
        return task

    def _refresh_task_url(self, task: _Task) -> bool:
        """Resolve one fresh URL for *task*.  Returns True when it changed."""
        resolver = self._url_resolver
        if resolver is None:
            return False
        snap = task.snapshot()
        try:
            candidate = resolver(snap)
        except Exception:
            return False
        if not isinstance(candidate, str) or not candidate or candidate == snap.url:
            return False
        if self._validate_urls and not default_url_validator(candidate):
            return False
        with self._lock:
            task.url = candidate
        self._save_state()
        return True

    # -- worker -------------------------------------------------------------

    def _ensure_worker_locked(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stop = False
        self._worker = threading.Thread(
            target=self._loop, name="lgl-downloader", daemon=True
        )
        self._worker.start()

    def _loop(self) -> None:
        while True:
            with self._cv:
                task = None
                while not self._stop:
                    if not self._pending:
                        self._cv.wait(timeout=1.0)
                        continue
                    task_id = self._pending.popleft()
                    candidate = self._tasks.get(task_id)
                    if candidate and candidate.status == DownloadStatus.QUEUED:
                        task = candidate
                        break
                if self._stop:
                    return
                if task is None:
                    continue
            self._download(task)

    def _download(self, task: _Task) -> None:
        with self._lock:
            if task.cancel_event.is_set():
                task.status = DownloadStatus.CANCELLED
                self._save_state()
                self._notify()
                return
            task.status = DownloadStatus.RUNNING
            task.error = None
            task.speed = 0.0
            task.eta = None
        self._save_state()
        self._notify()

        attempts = 0
        url_refreshed = False
        while attempts <= MAX_RETRIES:
            try:
                self._download_once(task)
                return
            except _CancelRequested:
                with self._lock:
                    task.status = DownloadStatus.CANCELLED
                    task.speed = 0.0
                    task.eta = None
                self._save_state()
                self._notify()
                return
            except _PauseRequested:
                with self._lock:
                    task.speed = 0.0
                    task.eta = None
                    if task.cancel_event.is_set():
                        task.status = DownloadStatus.CANCELLED
                        self._save_state()
                        self._notify()
                        return
                    if task.status == DownloadStatus.QUEUED:
                        # Resumed before the old connection fully closed.
                        continue
                    task.status = DownloadStatus.PAUSED
                self._save_state()
                self._notify()
                return
            except (DownloadError, OSError, urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                if (
                    isinstance(exc, _HttpStatusError)
                    and exc.code in (403, 404)
                    and not url_refreshed
                ):
                    # Archive.org download links expire; resolve one fresh URL
                    # and retry this attempt before treating it as fatal.
                    url_refreshed = True
                    if self._refresh_task_url(task):
                        continue
                retryable = _is_retryable(exc)
                attempts += 1
                if retryable and attempts <= MAX_RETRIES:
                    delay = 1.0 * (2 ** (attempts - 1))
                    if not self._sleep_interruptible(task, delay):
                        resumed = False
                        with self._lock:
                            if task.cancel_event.is_set():
                                task.status = DownloadStatus.CANCELLED
                            elif task.status == DownloadStatus.QUEUED:
                                # Paused and resumed while backing off; retry now.
                                resumed = True
                            else:
                                task.status = DownloadStatus.PAUSED
                            task.speed = 0.0
                            task.eta = None
                        self._save_state()
                        self._notify()
                        if resumed:
                            continue
                        return
                    continue
                with self._lock:
                    task.status = DownloadStatus.FAILED
                    task.error = _friendly_error(exc)
                    task.speed = 0.0
                    task.eta = None
                self._save_state()
                self._notify()
                return

    def _sleep_interruptible(self, task: _Task, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if task.cancel_event.is_set() or task.pause_event.is_set():
                return False
            time.sleep(0.1)
        return not (task.cancel_event.is_set() or task.pause_event.is_set())

    def _check_disk_space(self, task: _Task, offset: int) -> None:
        total = task.total
        if not total:
            return
        remaining = max(0, total - offset)
        free = _free_space(task.part_path.parent)
        if free is None:
            # Unknown free space must not block downloads.
            return
        needed = remaining + DISK_RESERVE_BYTES
        if free < needed:
            raise DownloadError(
                "Not enough free disk space: "
                f"need {human_size(needed)}, have {human_size(free)}."
            )

    def _discard_partial(self, task: _Task) -> None:
        try:
            task.part_path.unlink()
        except OSError:
            pass
        with self._lock:
            task.downloaded = 0

    # -- parallel transfer --------------------------------------------------

    def _segments_path(self, task: _Task) -> Path:
        return task.part_path.with_name(task.part_path.name + ".segments.json")

    def _probe_total(self, task: _Task) -> int | None:
        """Return the file size if the server answers byte ranges, else None."""
        headers = {
            "User-Agent": USER_AGENT,
            "Accept-Encoding": "identity",
            "Range": "bytes=0-0",
        }
        request = urllib.request.Request(task.url, headers=headers)
        try:
            response = urllib.request.urlopen(request, timeout=self._timeout)
            with response:
                if getattr(response, "status", 200) != 206:
                    return None
                parsed = _parse_content_range(response.headers.get("Content-Range", ""))
                response.read(1)
        except urllib.error.HTTPError as exc:
            # A refused probe is a real answer from the source, not a reason to
            # fall back: 403/404 must reach the caller so an expired link can be
            # refreshed instead of the same error being retried as a single
            # stream.  Any other status only means "no ranges offered".
            if exc.code in (403, 404):
                raise _HttpStatusError(
                    f"Download failed (HTTP {exc.code}).", exc.code
                ) from exc
            return None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            # A transport failure during the probe says nothing about range
            # support, so the sequential path makes its own attempt.
            return None
        if parsed is None or not parsed[2]:
            return None
        return parsed[2]

    def _segment_plan(self, total: int) -> list[tuple[int, int]]:
        """Split *total* bytes into inclusive ranges of at least MIN_SEGMENT_BYTES."""
        count = max(1, min(self._connections, total // MIN_SEGMENT_BYTES))
        size = total // count
        plan: list[tuple[int, int]] = []
        start = 0
        for index in range(count):
            end = total - 1 if index == count - 1 else start + size - 1
            if end < start:
                break
            plan.append((start, end))
            start = end + 1
        return plan

    def _load_segments(
        self, path: Path, total: int, plan: list[tuple[int, int]]
    ) -> tuple[dict[str, Any], bool]:
        """Load saved range state, or start a fresh plan.

        Returns the state and whether it was reused, so the caller knows if the
        partial file on disk is still meaningful.
        """
        try:
            raw = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            raw = None
        if isinstance(raw, dict) and raw.get("total") == total:
            segments = raw.get("segments")
            done = raw.get("done")
            if (
                isinstance(segments, list)
                and isinstance(done, list)
                and segments
                and len(segments) == len(done)
                and all(
                    isinstance(item, list)
                    and len(item) == 2
                    and all(isinstance(value, int) for value in item)
                    for item in segments
                )
                and sum(e - s + 1 for s, e in segments) == total
            ):
                return (
                    {
                        "total": total,
                        "segments": [[s, e] for s, e in segments],
                        "done": [bool(flag) for flag in done],
                    },
                    True,
                )
        return (
            {"total": total, "segments": [[s, e] for s, e in plan], "done": [False] * len(plan)},
            False,
        )

    def _download_parallel(self, task: _Task, offset: int) -> bool:
        """Fetch *task* over parallel byte ranges.

        Returns False, having changed nothing, when the server or the file is
        not suitable, so the caller can fall back to a single stream.
        """
        segments_path = self._segments_path(task)
        resuming = segments_path.exists()
        if offset and not resuming:
            # A partial written by the sequential path: reinterpreting it as
            # ranges could corrupt it, so finish it the way it started.
            return False
        if not resuming and self._connections < 2:
            return False

        total = self._probe_total(task)
        if not total:
            return False
        if not resuming and total < self._segment_threshold:
            return False

        plan = self._segment_plan(total)
        if len(plan) < 2 and not resuming:
            return False

        state, reused = self._load_segments(segments_path, total, plan)
        with self._lock:
            task.total = total
        if not reused:
            try:
                task.part_path.unlink()
            except OSError:
                pass
            # Record the plan before the file is created, so a crash can never
            # leave a full-size partial that looks finished.
            atomic_write_json(segments_path, state)

        self._check_disk_space(task, 0)
        task.part_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(task.part_path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            if os.fstat(fd).st_size != total:
                os.ftruncate(fd, total)
            self._run_segments(task, fd, state, segments_path)
        finally:
            os.close(fd)

        try:
            size = task.part_path.stat().st_size
        except OSError:
            size = -1
        if size != total:
            raise DownloadError(
                f"Downloaded size mismatch ({size} of {total} bytes); "
                "the partial file was kept and the file was not finalized."
            )
        try:
            segments_path.unlink()
        except OSError:
            pass
        if task.final_path.exists():
            raise DownloadError(
                f"“{task.final_path.name}” appeared during the download; refusing to overwrite it."
            )
        os.replace(task.part_path, task.final_path)
        with self._lock:
            task.downloaded = total
            task.total = total
            task.status = DownloadStatus.COMPLETED
            task.speed = 0.0
            task.eta = 0.0
        self._save_state()
        self._notify()
        return True

    def _run_segments(
        self, task: _Task, fd: int, state: dict[str, Any], segments_path: Path
    ) -> None:
        ranges = [(int(s), int(e)) for s, e in state["segments"]]
        done = state["done"]
        base = sum(e - s + 1 for (s, e), flag in zip(ranges, done) if flag)
        progress: dict[str, Any] = {
            "bytes": base,
            "last_emit": time.monotonic(),
            "last_speed_time": time.monotonic(),
            "last_speed_bytes": base,
        }
        lock = threading.Lock()
        stop = threading.Event()
        errors: list[BaseException] = []
        pending: collections.deque[int] = collections.deque(
            index for index, flag in enumerate(done) if not flag
        )
        with self._lock:
            task.downloaded = base
            task.total = state["total"]

        def worker() -> None:
            while True:
                with lock:
                    if stop.is_set() or not pending:
                        return
                    index = pending.popleft()
                start, end = ranges[index]
                try:
                    self._fetch_segment(task, fd, start, end, progress, lock, stop)
                except BaseException as exc:  # re-raised on the worker thread's behalf
                    with lock:
                        errors.append(exc)
                        stop.set()
                    return
                with lock:
                    done[index] = True
                    snapshot = {
                        "total": state["total"],
                        "segments": [list(item) for item in ranges],
                        "done": list(done),
                    }
                try:
                    atomic_write_json(segments_path, snapshot)
                except OSError:
                    pass

        count = max(1, min(self._connections, len(pending)))
        threads = [
            threading.Thread(target=worker, name=f"lgl-segment-{index}", daemon=True)
            for index in range(count)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        if errors:
            first = errors[0]
            if isinstance(first, (_CancelRequested, _PauseRequested, _SegmentAborted)):
                raise first
            if isinstance(first, DownloadError):
                raise first
            raise DownloadError(str(first) or "The parallel download failed.") from first

    def _fetch_segment(
        self,
        task: _Task,
        fd: int,
        start: int,
        end: int,
        progress: dict[str, Any],
        lock: threading.Lock,
        stop: threading.Event,
    ) -> None:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept-Encoding": "identity",
            "Accept": "application/octet-stream",
            "Range": f"bytes={start}-{end}",
        }
        request = urllib.request.Request(task.url, headers=headers)
        try:
            response = urllib.request.urlopen(request, timeout=self._timeout)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise DownloadError(
                    "The source is rate-limiting this download (HTTP 429). It will be "
                    "retried; if it keeps happening, lower the connection count in Settings."
                ) from exc
            if exc.code in (403, 404):
                raise _HttpStatusError(f"Download failed (HTTP {exc.code}).", exc.code) from exc
            raise DownloadError(f"Range request failed (HTTP {exc.code}).") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise DownloadError(
                "The connection was interrupted; the partial file was kept."
            ) from exc

        with response:
            if getattr(response, "status", 200) != 206:
                raise DownloadError(
                    "The server stopped honouring byte ranges during the transfer."
                )
            parsed = _parse_content_range(response.headers.get("Content-Range", ""))
            if parsed is None or parsed[0] != start:
                raise DownloadError("The server returned an unexpected byte range.")
            position = start
            remaining = end - start + 1
            while remaining > 0:
                if task.cancel_event.is_set():
                    raise _CancelRequested
                if task.pause_event.is_set():
                    raise _PauseRequested
                if stop.is_set():
                    raise _SegmentAborted
                try:
                    chunk = response.read(min(CHUNK_SIZE, remaining))
                except (TimeoutError, ConnectionError, OSError) as exc:
                    raise DownloadError(
                        "The connection was interrupted; the partial file was kept."
                    ) from exc
                if not chunk:
                    raise DownloadError(
                        "The server closed the connection before the byte range was complete."
                    )
                view = memoryview(chunk)
                written = 0
                while written < len(chunk):
                    written += os.pwrite(fd, view[written:], position + written)
                position += len(chunk)
                remaining -= len(chunk)
                with lock:
                    progress["bytes"] += len(chunk)
                    downloaded = progress["bytes"]
                self._note_segment_progress(task, downloaded, progress)

    def _note_segment_progress(
        self, task: _Task, downloaded: int, progress: dict[str, Any]
    ) -> None:
        now = time.monotonic()
        emit = False
        with self._lock:
            task.downloaded = downloaded
            elapsed = now - progress["last_speed_time"]
            if elapsed >= 0.5:
                instant = (downloaded - progress["last_speed_bytes"]) / elapsed
                task.speed = instant if task.speed <= 0 else (0.65 * task.speed + 0.35 * instant)
                progress["last_speed_time"] = now
                progress["last_speed_bytes"] = downloaded
            if task.total and task.speed > 0:
                task.eta = max(0.0, (task.total - downloaded) / task.speed)
            else:
                task.eta = None
            if now - progress["last_emit"] >= PROGRESS_INTERVAL:
                progress["last_emit"] = now
                emit = True
        if emit:
            self._notify()

    def _download_once(self, task: _Task) -> None:
        with self._lock:
            if task.cancel_event.is_set():
                raise _CancelRequested
            if task.pause_event.is_set():
                raise _PauseRequested
            offset = task.part_path.stat().st_size if task.part_path.exists() else 0

        if self._download_parallel(task, offset):
            return

        if offset and self._segments_path(task).exists():
            # A parallel download pre-sizes its .part to the full length, so the
            # size checks below would mistake it for a finished file.  If the
            # source has stopped honouring ranges, refuse rather than finalize
            # a file full of unwritten bytes.
            raise DownloadError(
                "This partial download was started in parallel, but the source no "
                "longer supports byte ranges. Delete its .part and .part.segments.json "
                "files to restart it from zero."
            )

        if task.total and offset > task.total:
            # A stale/oversized part file cannot be resumed safely.
            self._discard_partial(task)
            offset = 0

        if task.total and offset == task.total and offset > 0:
            if task.final_path.exists():
                raise DownloadError("Target file already exists; refusing to overwrite it.")
            os.replace(task.part_path, task.final_path)
            with self._lock:
                task.downloaded = offset
                task.status = DownloadStatus.COMPLETED
                task.speed = 0.0
                task.eta = 0.0
            self._save_state()
            self._notify()
            return

        self._check_disk_space(task, offset)

        headers = {
            "User-Agent": USER_AGENT,
            "Accept-Encoding": "identity",
            "Accept": "application/octet-stream",
        }
        range_sent = offset > 0
        if range_sent:
            headers["Range"] = f"bytes={offset}-"
        request = urllib.request.Request(task.url, headers=headers)

        try:
            response = urllib.request.urlopen(request, timeout=self._timeout)
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and task.total and offset >= task.total:
                raise DownloadError("The partial file is already complete; press Retry to finalize.") from exc
            if exc.code == 404:
                raise _HttpStatusError("File not found on archive.org (404).", 404) from exc
            if exc.code == 403:
                raise _HttpStatusError("archive.org denied access to this file (403).", 403) from exc
            if exc.code == 429:
                raise DownloadError("archive.org is rate-limiting downloads (429). Try again later.") from exc
            if 500 <= exc.code < 600:
                raise DownloadError(f"archive.org server error (HTTP {exc.code}).") from exc
            raise DownloadError(f"Download failed (HTTP {exc.code}).") from exc

        with response:
            status = getattr(response, "status", 200)
            content_range = response.headers.get("Content-Range", "")
            parsed_range = _parse_content_range(content_range) if content_range else None

            if range_sent and status == 206:
                if parsed_range is None or parsed_range[0] != offset:
                    # Never append bytes that belong at a different offset.
                    self._discard_partial(task)
                    raise DownloadError(
                        "archive.org returned an unexpected byte range; the partial file "
                        "was kept and the download will restart from zero."
                    )
                mode = "ab"
            elif range_sent and status == 200:
                # Server ignored Range: restart cleanly rather than duplicate bytes.
                offset = 0
                mode = "wb"
                with self._lock:
                    task.downloaded = 0
            elif status == 206:
                raise DownloadError(
                    "archive.org returned a partial response for a download that was "
                    "not resumed."
                )
            else:
                mode = "wb"

            total = task.total
            if parsed_range is not None:
                if parsed_range[2]:
                    total = parsed_range[2]
                elif offset and total is None:
                    length = response.headers.get("Content-Length")
                    if length and length.isdigit():
                        total = offset + int(length)
            else:
                length = response.headers.get("Content-Length")
                if length and length.isdigit():
                    if offset == 0:
                        total = int(length)
                    elif total is None:
                        total = offset + int(length)
            with self._lock:
                if total:
                    task.total = total
                task.downloaded = offset

            task.part_path.parent.mkdir(parents=True, exist_ok=True)
            last_emit = time.monotonic()
            last_speed_time = last_emit
            last_speed_bytes = offset

            with open(task.part_path, mode) as fh:
                while True:
                    if task.cancel_event.is_set():
                        raise _CancelRequested
                    if task.pause_event.is_set():
                        raise _PauseRequested
                    try:
                        chunk = response.read(CHUNK_SIZE)
                    except (TimeoutError, ConnectionError, OSError) as exc:
                        raise DownloadError("The connection was interrupted; the partial file was kept.") from exc
                    if not chunk:
                        break
                    fh.write(chunk)
                    offset += len(chunk)
                    now = time.monotonic()
                    with self._lock:
                        task.downloaded = offset
                        elapsed = now - last_speed_time
                        if elapsed >= 0.5:
                            instant = (offset - last_speed_bytes) / elapsed
                            task.speed = instant if task.speed <= 0 else (0.65 * task.speed + 0.35 * instant)
                            last_speed_time = now
                            last_speed_bytes = offset
                        if task.total and task.speed > 0:
                            task.eta = max(0.0, (task.total - offset) / task.speed)
                        else:
                            task.eta = None
                    if now - last_emit >= PROGRESS_INTERVAL:
                        last_emit = now
                        self._notify()
                fh.flush()
                os.fsync(fh.fileno())

        if task.total and offset < task.total:
            raise DownloadError(
                f"Transfer ended early ({offset} of {task.total} bytes). Press Retry to resume."
            )

        if task.cancel_event.is_set():
            raise _CancelRequested
        if task.final_path.exists():
            raise DownloadError(
                f"“{task.final_path.name}” appeared during the download; refusing to overwrite it."
            )

        if task.total:
            try:
                final_size = task.part_path.stat().st_size
            except OSError:
                final_size = offset
            if final_size != task.total:
                raise DownloadError(
                    f"Downloaded size mismatch ({final_size} of {task.total} bytes); "
                    "the partial file was kept and the file was not finalized."
                )

        os.replace(task.part_path, task.final_path)
        with self._lock:
            task.downloaded = offset
            task.total = task.total or offset
            task.status = DownloadStatus.COMPLETED
            task.speed = 0.0
            task.eta = 0.0
        self._save_state()
        self._notify()

    def _notify(self) -> None:
        # Periodic persistence reuses the progress-notification cadence so a
        # crash loses at most a couple of seconds of progress.
        self._maybe_save_state()
        if not self._observer:
            return
        try:
            self._observer()
        except Exception:
            # A UI callback must never kill the downloader.
            pass


def _as_float(value: Any, default: float) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    return default


def _as_int_value(value: Any, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(0, value)


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, DownloadError):
        text = str(exc).lower()
        return any(
            marker in text
            for marker in (
                "server error",
                "interrupted",
                "ended early",
                "rate-limit",
                "unexpected byte range",
                "closed the connection",
            )
        )
    return True


def _friendly_error(exc: BaseException) -> str:
    if isinstance(exc, DownloadError):
        return str(exc)
    if isinstance(exc, urllib.error.HTTPError):
        return f"Download failed (HTTP {exc.code})."
    if isinstance(exc, urllib.error.URLError):
        return "Could not reach archive.org. Check your network connection."
    if isinstance(exc, PermissionError):
        return "Permission denied while writing the download."
    if isinstance(exc, OSError) and exc.errno:
        return f"Disk or network error: {exc.strerror or exc}"
    return f"Download failed: {exc}"
