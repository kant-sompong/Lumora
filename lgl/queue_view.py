"""Queue grouping and row text for the download dashboard.

The downloader reports one flat list of tasks; people think in four buckets —
waiting, running, finished, broken.  Mapping raw statuses to those buckets in one
place keeps the GTK and Qt dashboards labelled the same way, and keeps the speed
and remaining-time text identical on both.
"""

from __future__ import annotations

from typing import Any, Iterable

from .downloader import DownloadStatus
from .util import human_eta, human_size, human_speed

GROUPS = ("active", "queued", "completed", "failed")
GROUP_LABELS = {
    "active": "Active",
    "queued": "Queued",
    "completed": "Completed",
    "failed": "Failed",
}
_STATUS_GROUP = {
    DownloadStatus.RUNNING: "active",
    DownloadStatus.PAUSED: "active",
    DownloadStatus.QUEUED: "queued",
    DownloadStatus.COMPLETED: "completed",
    DownloadStatus.FAILED: "failed",
    DownloadStatus.CANCELLED: "failed",
}


def group_of(task: Any) -> str:
    """The dashboard bucket for one task, defaulting to queued."""
    return _STATUS_GROUP.get(getattr(task, "status", None), "queued")


def counts(tasks: Iterable[Any]) -> dict[str, int]:
    """Every bucket, always present, so counters read 0 instead of vanishing."""
    tally = {group: 0 for group in GROUPS}
    for task in tasks:
        tally[group_of(task)] += 1
    return tally


def filter_tasks(
    tasks: Iterable[Any],
    *,
    group: str | None = None,
    search: str = "",
) -> list[Any]:
    """Tasks in one bucket whose filename matches ``search``."""
    needle = " ".join(str(search or "").split()).casefold()
    selected: list[Any] = []
    for task in tasks:
        if group and group in GROUPS and group_of(task) != group:
            continue
        if needle:
            name = str(getattr(task, "filename", "") or "").casefold()
            if needle not in name:
                continue
        selected.append(task)
    return selected


def progress_text(task: Any) -> str:
    """``45 % · 12.3 MB / 27.1 MB · 1.2 MB/s · 3m`` — the line under a task name."""
    parts: list[str] = []
    progress = getattr(task, "progress", None)
    if progress is not None:
        parts.append(f"{int(round(progress * 100))} %")
    downloaded = int(getattr(task, "downloaded", 0) or 0)
    total = getattr(task, "total", None)
    if total:
        parts.append(f"{human_size(downloaded)} / {human_size(int(total))}")
    elif downloaded:
        parts.append(human_size(downloaded))
    speed = float(getattr(task, "speed", 0.0) or 0.0)
    if speed > 0:
        parts.append(human_speed(speed))
        eta = getattr(task, "eta", None)
        if eta is not None:
            parts.append(human_eta(float(eta)))
    return " · ".join(parts)


__all__ = [
    "GROUPS",
    "GROUP_LABELS",
    "counts",
    "filter_tasks",
    "group_of",
    "progress_text",
]
