"""A support bundle the user can read before sending it.

Nothing here reads documents, browsing history or library titles: the report is
versions, counts, settings *names* and a redacted log tail.  Redaction runs twice
by design — credentials are recognised by key name and long tokens by shape — so
a key pasted into a log line does not survive into the file the user shares.
"""

from __future__ import annotations

import platform
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

from .settings import API_KEY_PROVIDERS

#: Tokens this long without spaces look like credentials rather than prose.
_TOKEN = re.compile(r"[A-Za-z0-9_\-]{24,}")
#: Home directories are personal data, not diagnostics.
_HOME = re.compile(r"(/home/[^/\s]+|[A-Za-z]:\\Users\\[^\\\s]+)")
#: ``api_key=…``, ``Token: …``, ``client-secret: …`` — the label is matched with
#: its prefix so an underscore or a dash inside the name still counts.
_LABEL = re.compile(
    r"(?i)([\w-]*(?:key|token|secret|password|cookie|authorization)[\w-]*\s*[:=]\s*)([^\s,;]+)"
)
_MAX_LOG_LINES = 200
_MAX_LINE = 400


def redact(text: str) -> str:
    """Remove credentials and home-directory paths from one string."""
    if not text:
        return ""
    cleaned = _LABEL.sub(r"\1<redacted>", str(text))
    cleaned = _TOKEN.sub("<redacted>", cleaned)
    return _HOME.sub("~", cleaned)


def _settings_lines(settings: Any) -> list[str]:
    if settings is None:
        return ["settings: unavailable"]
    lines: list[str] = []
    for provider in API_KEY_PROVIDERS:
        try:
            present = bool(settings.api_key(provider))
        except Exception:
            present = False
        state = "configured" if present else "not configured"
        lines.append(f"provider {provider}: {state}")
    try:
        download_dir = str(settings.get("download_dir") or "")
    except Exception:
        download_dir = ""
    lines.append(f"download dir: {redact(download_dir) if download_dir else 'default'}")
    return lines


def _log_tail(paths: Iterable[Path]) -> list[str]:
    lines: list[str] = []
    for path in paths:
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            lines.append(f"log {Path(path).name}: unreadable")
            continue
        tail = text.splitlines()[-_MAX_LOG_LINES:]
        lines.append(f"--- {Path(path).name} ({len(tail)} lines) ---")
        lines.extend(redact(line[:_MAX_LINE]) for line in tail)
    return lines


def build_report(
    *,
    app_version: str,
    settings: Any = None,
    library_entries: int | None = None,
    collections: int | None = None,
    continue_watching: int | None = None,
    log_paths: Iterable[Path] = (),
    extra: Mapping[str, Any] | None = None,
    now: float | None = None,
) -> str:
    """Build the report as plain text, newest information first."""
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now or time.time()))
    lines = [
        "Lumora support report",
        f"generated: {stamp}",
        f"app version: {app_version}",
        f"python: {sys.version.split()[0]}",
        f"platform: {platform.platform()}",
    ]
    if library_entries is not None:
        lines.append(f"library entries: {library_entries}")
    if collections is not None:
        lines.append(f"collections: {collections}")
    if continue_watching is not None:
        lines.append(f"continue watching: {continue_watching}")
    lines.append("")
    lines.append("settings")
    lines.extend(_settings_lines(settings))
    if extra:
        lines.append("")
        lines.append("details")
        for name in sorted(extra):
            lines.append(f"{name}: {redact(str(extra[name]))}")
    logs = _log_tail(log_paths)
    if logs:
        lines.append("")
        lines.append("log tail")
        lines.extend(logs)
    return "\n".join(lines) + "\n"


def write_report(path: Path, **kwargs: Any) -> Path:
    """Write the report and return where it went."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build_report(**kwargs), encoding="utf-8")
    return target


__all__ = ["build_report", "redact", "write_report"]
