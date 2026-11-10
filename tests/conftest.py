"""Shared test wiring: Qt worker-pool isolation.

``lgl.qt_widgets.run_async`` hands its work to ``QThreadPool.globalInstance()``,
which is only as wide as the machine's CPU count.  A test that leaves blocked
jobs behind - a real provider ping or a TMDB/AniList shelf load, for instance -
makes the next test's work queue behind them, and that test then fails on a
timeout instead of on its own assertions.

These fixtures keep that from turning into flakiness:

* the global pool always has at least eight worker threads (a 1-CPU machine
  would otherwise give it one);
* a test that finishes with jobs still running is failed by name, with the
  jobs still in flight, instead of quietly poisoning the next one.  Stub the
  network in that test rather than raising the timeout.
"""

from __future__ import annotations

import sys

import pytest

#: How long a finished test may take to drain its Qt worker jobs.
DRAIN_SECONDS = 3000


def _pool():
    """The global QThreadPool, but only once a Qt test has imported QtCore."""
    core = sys.modules.get("PySide6.QtCore")
    return core.QThreadPool.globalInstance() if core is not None else None


def _outstanding() -> int:
    """Jobs that ``run_async`` still considers in flight, if it is loaded."""
    widgets = sys.modules.get("lgl.qt_widgets")
    return len(widgets._JOBS) if widgets is not None else 0


@pytest.fixture(scope="session", autouse=True)
def qt_pool_threads():
    """Give the pool more threads than the CPU count, so queued jobs drain."""
    try:
        from PySide6.QtCore import QThreadPool
    except ImportError:  # GTK-only checkout: nothing to widen
        yield
        return
    pool = QThreadPool.globalInstance()
    pool.setMaxThreadCount(max(8, pool.maxThreadCount()))
    yield


@pytest.fixture(autouse=True)
def qt_pool_drained(request):
    """Wait for this test's Qt worker jobs; fail it if they do not finish."""
    yield
    pool = _pool()
    if pool is None:
        return
    if not pool.waitForDone(DRAIN_SECONDS):
        pytest.fail(
            f"{request.node.nodeid} left Qt worker jobs running: "
            f"{_outstanding()} still in flight after {DRAIN_SECONDS / 1000:.0f} s "
            f"on a {pool.maxThreadCount()}-thread pool. Stub the network in this "
            "test (inject its loaders and health results); do not wait longer.",
            pytrace=False,
        )
