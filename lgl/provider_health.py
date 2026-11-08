"""Provider health checks for the streaming providers.

Pure Python: no GTK, no Qt, no network of its own beyond the injectable HTTP
function it is given.  A check contacts the provider's own base host with a
single plain ``HEAD`` request, never a player, never a video, never anything
tied to the user's account or library.

Three attempts are made per provider and the median time to first byte is
reported, so one slow answer does not decide the status.  Results are cached
for ten minutes in ``cache_dir()/provider-health.json`` and are reused unless
``force=True``.

Statuses: ``fast`` under 300 ms, ``ok`` from 300 to 999 ms, ``slow`` at one
second or more, ``unreachable`` for DNS/connection failures and ``timed out``
when the connection itself times out.  An HTTP error is a *response*: the
provider answered, so it stays reachable and the status code is reported.
"""

from __future__ import annotations

import http.client
import json
import socket
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Iterable, Sequence

from . import streaming
from .util import cache_dir

#: Seconds to wait for the provider's first byte.
TIMEOUT = 5.0
#: Sequential attempts per provider; never more than this many requests.
ATTEMPTS = 3
#: How long a result stays fresh.
CACHE_TTL = 600.0
#: Worker threads used by :func:`check_all`.
MAX_CONCURRENCY = 4
CACHE_NAME = "provider-health.json"
USER_AGENT = "Mozilla/5.0 (compatible; Lumora; private desktop client)"
FAST_MS = 300.0
OK_MS = 1000.0

FAST = "fast"
OK = "ok"
SLOW = "slow"
UNREACHABLE = "unreachable"
TIMED_OUT = "timed out"

#: ``http(url, timeout) -> (status_code, milliseconds_to_first_byte)``.
HttpFn = Callable[[str, float], tuple[int, float]]


# ---------------------------------------------------------------------------
# request
# ---------------------------------------------------------------------------

def base_url(provider: str) -> str:
    """The provider's own base host, taken from its embed spec."""
    spec = streaming.provider_spec(provider)
    return f"https://{spec.host}/"


def default_http(url: str, timeout: float = TIMEOUT) -> tuple[int, float]:
    """Plain HEAD request; returns ``(status, ms to first byte)``."""
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    connection = http.client.HTTPSConnection(host, parts.port or 443, timeout=timeout)
    started = time.monotonic()
    try:
        connection.request(
            "HEAD", path,
            headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
        )
        response = connection.getresponse()
        return int(response.status), (time.monotonic() - started) * 1000.0
    finally:
        connection.close()


def _median(values: Sequence[float]) -> float:
    ordered = sorted(float(v) for v in values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _word(ms: float) -> str:
    if ms < FAST_MS:
        return FAST
    if ms < OK_MS:
        return OK
    return SLOW


def _result(
    provider: str, ok: bool, ms: float | None, status: str, reason: str | None
) -> dict[str, Any]:
    return {
        "provider": provider,
        "ok": bool(ok),
        "ms": None if ms is None else int(round(ms)),
        "status": status,
        "reason": reason,
    }


def check(provider: str, http: HttpFn | None = None, *, timeout: float = TIMEOUT) -> dict[str, Any]:
    """Test one provider and describe the result.

    ``http`` is injectable so tests can supply canned timings; the default
    performs a real request against the provider's base host.
    """
    key = streaming.provider_spec(provider).key
    fetch = http or default_http
    url = base_url(key)
    timings: list[float] = []
    reasons: list[str] = []
    for _ in range(ATTEMPTS):
        try:
            status, ms = fetch(url, timeout)
        except (socket.timeout, TimeoutError):
            return _result(key, False, None, TIMED_OUT, "timed out")
        except socket.gaierror:
            return _result(key, False, None, UNREACHABLE, "DNS failed")
        except ConnectionRefusedError:
            return _result(key, False, None, UNREACHABLE, "Connection refused")
        except OSError as error:
            text = getattr(error, "strerror", None) or str(error) or "connection failed"
            return _result(key, False, None, UNREACHABLE, str(text))
        timings.append(float(ms))
        if not 200 <= int(status) < 400:
            reasons.append(f"HTTP {status}")
    if not timings:
        return _result(key, False, None, UNREACHABLE, "no response")
    ms = _median(timings)
    reason = "; ".join(dict.fromkeys(reasons)) if reasons else None
    return _result(key, True, ms, _word(ms), reason)


# ---------------------------------------------------------------------------
# cache
# ---------------------------------------------------------------------------

def cache_path() -> Any:
    return cache_dir() / CACHE_NAME


def _load(path: Any) -> dict[str, dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    results = payload.get("results")
    if not isinstance(results, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, value in results.items():
        if not isinstance(value, dict):
            continue
        if not isinstance(value.get("provider"), str):
            value = {**value, "provider": str(key)}
        if not isinstance(value.get("ok"), bool):
            continue
        out[str(key)] = value
    return out


def cached_results(path: Any = None) -> tuple[list[dict[str, Any]], float | None]:
    """Cached results and the age of the cache in seconds (``None`` if absent)."""
    path = path or cache_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        stamp = float(payload.get("checked_at"))
    except (OSError, ValueError, TypeError, AttributeError):
        return [], None
    results = list(_load(path).values())
    age = max(0.0, time.time() - stamp)
    return results, age


def _save(path: Any, results: Iterable[dict[str, Any]]) -> None:
    payload = {
        "checked_at": time.time(),
        "results": {item["provider"]: item for item in results if item.get("provider")},
    }
    try:
        text = json.dumps(payload, indent=2, sort_keys=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
        import os

        os.replace(tmp, path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# all providers
# ---------------------------------------------------------------------------

def _rank(item: dict[str, Any]) -> tuple[int, float, str]:
    if item.get("ok") and isinstance(item.get("ms"), int):
        return (0, float(item["ms"]), str(item["provider"]))
    return (1, float("inf"), str(item["provider"]))


def sort_results(results: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fastest first; unreachable providers always last."""
    return sorted(results, key=_rank)


def check_all(
    providers: Iterable[str] = streaming.PROVIDERS,
    *,
    force: bool = False,
    http: HttpFn | None = None,
    cache: Any = None,
    timeout: float = TIMEOUT,
) -> list[dict[str, Any]]:
    """Check every provider in parallel (at most four at a time).

    Fresh cached results are reused unless ``force`` is set.  Returns the
    results sorted fastest first.
    """
    keys: list[str] = []
    for key in providers:
        try:
            key = streaming.provider_spec(key).key
        except streaming.EmbedUrlError:
            continue
        if key not in keys:
            keys.append(key)
    path = cache or cache_path()
    known: dict[str, dict[str, Any]] = {}
    if not force:
        cached, age = cached_results(path)
        if age is not None and age < CACHE_TTL:
            known = {item["provider"]: item for item in cached if item["provider"] in keys}
    missing = [key for key in keys if key not in known]
    if missing:
        with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as pool:
            for item in pool.map(lambda key: check(key, http, timeout=timeout), missing):
                known[item["provider"]] = item
        _save(path, known.values())
    return sort_results(known[key] for key in keys)


def fastest(results: Iterable[dict[str, Any]], providers: Iterable[str]) -> str | None:
    """Fastest reachable provider among ``providers``, or ``None``."""
    allowed = set(providers)
    for item in sort_results(results):
        if item.get("ok") and item.get("provider") in allowed:
            return str(item["provider"])
    return None


def describe(item: dict[str, Any]) -> str:
    """``CineSrc 180 ms`` / ``Moonflix: offline``."""
    label = streaming.LABELS.get(str(item.get("provider")), str(item.get("provider")))
    if item.get("ok") and isinstance(item.get("ms"), int):
        return f"{label} {item['ms']} ms"
    return f"{label}: offline"


def should_auto_apply(*, enabled: bool, playing: bool, manual: bool) -> bool:
    """Whether a finished check may change the selection."""
    return bool(enabled) and not playing and not manual


AUTO_KEY = "provider_auto_select"
MANUAL_MESSAGE = (
    "Automatic selection disabled because you selected a provider manually."
)
NO_PROVIDER_MESSAGE = "No provider reachable right now"


class AutoSelect:
    """At most one background health check, with results as they arrive.

    The GUI hands in ``settings`` (any object with ``get``/``set``) and a
    callback that is called on the worker thread exactly once per run; it is
    never called after :meth:`shutdown`.
    """

    def __init__(
        self,
        settings: Any = None,
        *,
        on_results: Callable[[list[dict[str, Any]]], None] | None = None,
        http: HttpFn | None = None,
        cache: Any = None,
        token: str = "auto",
    ) -> None:
        self.settings = settings
        self._callback = on_results
        self._http = http
        self._cache = cache
        self._token = token
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._results: list[dict[str, Any]] = []
        self._manual = False

    # public ------------------------------------------------------------
    @property
    def results(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._results)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def enabled(self) -> bool:
        if self.settings is None:
            return True
        try:
            return bool(self.settings.get(AUTO_KEY, True))
        except Exception:  # noqa: BLE001
            return True

    def set_enabled(self, value: bool) -> None:
        if self.settings is None:
            return
        try:
            self.settings.set(AUTO_KEY, bool(value))
        except Exception:  # noqa: BLE001
            pass

    def mark_manual(self) -> None:
        """The user picked a provider themselves; stop overriding it."""
        self._manual = True
        self.set_enabled(False)

    @property
    def manual(self) -> bool:
        return self._manual

    def start(self, *, force: bool = False) -> bool:
        """Start a check unless one is already running.

        Without ``force`` a fresh cache finishes the run immediately, through
        the same callback, so the caller never has to wait.
        """
        if self.running:
            return False
        if not force:
            cached, age = cached_results(self._cache or cache_path())
            if age is not None and age < CACHE_TTL:
                self._deliver(sort_results(cached))
                return False
        self._cancel = threading.Event()
        thread = threading.Thread(
            target=self._run, name=f"lgl-provider-health-{self._token}", daemon=True
        )
        self._thread = thread
        thread.start()
        return True

    def shutdown(self) -> None:
        """Stop a pending check and drop its callback."""
        self._cancel.set()
        self._callback = None

    # private -----------------------------------------------------------
    def _deliver(self, results: list[dict[str, Any]]) -> None:
        with self._lock:
            self._results = list(results)
        callback = self._callback
        if callback is not None and not self._cancel.is_set():
            callback(list(results))

    def _run(self) -> None:
        try:
            results = check_all(
                streaming.PROVIDERS, force=True, http=self._http, cache=self._cache
            )
        except Exception:  # noqa: BLE001 - a health check must never crash the app
            results = []
        if self._cancel.is_set():
            return
        self._deliver(results)


__all__ = [
    "ATTEMPTS",
    "AUTO_KEY",
    "AutoSelect",
    "CACHE_TTL",
    "FAST",
    "MANUAL_MESSAGE",
    "MAX_CONCURRENCY",
    "NO_PROVIDER_MESSAGE",
    "OK",
    "SLOW",
    "TIMEOUT",
    "TIMED_OUT",
    "UNREACHABLE",
    "base_url",
    "cache_path",
    "cached_results",
    "check",
    "check_all",
    "default_http",
    "describe",
    "fastest",
    "should_auto_apply",
    "sort_results",
]
