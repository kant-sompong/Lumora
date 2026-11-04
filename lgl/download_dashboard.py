"""Download dashboard extras: concurrency, row text and error details.

Roadmap section 17 asks for configurable concurrency limits, visible retry and
error details, and queue state that survives a restart.  The downloader already
persists its queue and tracks retry counts, so this module only adds the policy
and the presentation pieces both frontends share.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from . import queue_view

LIMIT_KEY = "max_concurrent_downloads"
MIN_LIMIT = 1
MAX_LIMIT = 8
DEFAULT_LIMIT = 3


def limit(settings: Any) -> int:
    try:
        value = int(settings.get(LIMIT_KEY))
    except (AttributeError, TypeError, ValueError):
        return DEFAULT_LIMIT
    return max(MIN_LIMIT, min(MAX_LIMIT, value))


def set_limit(settings: Any, value: Any) -> int:
    try:
        wanted = int(value)
    except (TypeError, ValueError):
        wanted = DEFAULT_LIMIT
    wanted = max(MIN_LIMIT, min(MAX_LIMIT, wanted))
    settings.set(LIMIT_KEY, wanted)
    return wanted


def can_start(running: int, settings: Any) -> bool:
    try:
        active = int(running)
    except (TypeError, ValueError):
        active = 0
    return active < limit(settings)


def retryable(task: Any) -> bool:
    flag = getattr(task, "can_retry", None)
    if isinstance(flag, bool):
        return flag
    status = str(getattr(getattr(task, "status", None), "value", getattr(task, "status", ""))).lower()
    attempts = int(getattr(task, "attempts", 0) or 0)
    ceiling = int(getattr(task, "retry_limit", 0) or 0)
    return status in ("failed", "cancelled") and attempts < ceiling


def error_details(task: Any) -> dict[str, Any]:
    """Everything the dashboard shows under a failed row."""
    error = str(getattr(task, "error", "") or "").strip()
    return {
        "id": str(getattr(task, "id", "") or ""),
        "title": str(getattr(task, "filename", "") or getattr(task, "identifier", "") or ""),
        "error": error or "No error was recorded.",
        "attempts": int(getattr(task, "attempts", 0) or 0),
        "retry_limit": int(getattr(task, "retry_limit", 0) or 0),
        "retryable": retryable(task),
        "source": str(getattr(task, "url", "") or ""),
    }


def status_text(task: Any) -> str:
    status = getattr(task, "status", None)
    return str(getattr(status, "value", status) or "").upper()


def row(task: Any) -> dict[str, str]:
    """One line of the dashboard: group, title, progress or error."""
    details = error_details(task)
    progress = queue_view.progress_text(task)
    group = queue_view.group_of(task)
    if group == "failed":
        # For a failure the error matters more than the last known percentage.
        body = details["error"]
    else:
        body = progress
    return {
        "id": details["id"],
        "group": group,
        "group_label": queue_view.GROUP_LABELS.get(group, group),
        "title": details["title"],
        "status": status_text(task),
        "detail": body,
    }


def rows(tasks: Iterable[Any], *, group: str | None = None) -> list[dict[str, str]]:
    selected = queue_view.filter_tasks(tasks, group=group) if group else list(tasks)
    return [row(task) for task in selected]


def summary(tasks: Iterable[Any]) -> dict[str, Any]:
    items = list(tasks)
    counts = queue_view.counts(items)
    failed = counts.get("failed", 0)
    return {
        "counts": counts,
        "failed": failed,
        "headline": f"{counts.get('active', 0)} downloading · {counts.get('queued', 0)} queued"
        + (f" · {failed} failed" if failed else ""),
    }


def queue_file(config_dir: Path | str) -> Path:
    """Where the downloader keeps its queue; shown in the diagnostics report."""
    return Path(config_dir) / "downloads.json"


def load_queue(store: Any) -> list[dict[str, Any]]:
    """Read the persisted queue through the downloader's own store object."""
    load = getattr(store, "load", None)
    if not callable(load):
        return []
    try:
        items = load()
    except Exception:
        return []
    return [item for item in items if isinstance(item, Mapping)]


__all__ = [
    "DEFAULT_LIMIT",
    "LIMIT_KEY",
    "MAX_LIMIT",
    "MIN_LIMIT",
    "can_start",
    "error_details",
    "limit",
    "load_queue",
    "queue_file",
    "retryable",
    "row",
    "rows",
    "set_limit",
    "status_text",
    "summary",
]
