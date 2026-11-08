"""Provider health checks, caching and automatic selection.

No real provider is contacted anywhere in this file: every check goes through a
stub HTTP function that returns canned timings.  The sandbox cannot reach the
providers, and the tests are written so they never need to.
"""

from __future__ import annotations

import json
import socket
import threading
import time

import pytest

from lgl import provider_health as ph
from lgl import streaming


class Memory:
    def __init__(self, **values):
        self.data = dict(values)

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


def stub(*timings, status=200):
    """Return an http function that replays ``timings`` in order."""
    seen = list(timings)

    def fetch(url, timeout):
        ms = seen.pop(0) if seen else timings[-1]
        return status, ms

    return fetch


def slow_stub(ms=120, delay=0.02):
    def fetch(url, timeout):
        time.sleep(delay)
        return 200, ms

    return fetch


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


# --- one provider ---------------------------------------------------------


def test_median_of_three_attempts():
    result = ph.check("cinesrc", stub(900, 100, 200))
    assert result["ms"] == 200
    assert result["status"] == ph.FAST
    assert result["ok"] is True
    assert result["provider"] == "cinesrc"


def test_status_words_follow_the_documented_bands():
    assert ph.check("vidy", stub(120))["status"] == ph.FAST
    assert ph.check("vidy", stub(300))["status"] == ph.OK
    assert ph.check("vidy", stub(999))["status"] == ph.OK
    assert ph.check("vidy", stub(1000))["status"] == ph.SLOW


def test_dns_failure_is_unreachable():
    def fetch(url, timeout):
        raise socket.gaierror(8, "nodename nor servname provided")

    result = ph.check("moonflix", fetch)
    assert (result["ok"], result["status"], result["reason"]) == (
        False, ph.UNREACHABLE, "DNS failed"
    )


def test_timeout_is_reported_as_timed_out():
    def fetch(url, timeout):
        raise socket.timeout("timed out")

    result = ph.check("afterdusk", fetch)
    assert (result["ok"], result["status"]) == (False, ph.TIMED_OUT)


def test_connection_refused_is_unreachable():
    def fetch(url, timeout):
        raise ConnectionRefusedError("refused")

    result = ph.check("velarium", fetch)
    assert result["status"] == ph.UNREACHABLE
    assert result["reason"] == "Connection refused"


def test_http_403_is_a_response_not_a_network_failure():
    result = ph.check("vidcore", stub(180, 180, 180, status=403))
    assert result["ok"] is True
    assert result["status"] == ph.FAST
    assert result["reason"] == "HTTP 403"


def test_never_more_than_three_requests_per_provider():
    calls = []

    def fetch(url, timeout):
        calls.append(url)
        return 200, 100

    ph.check("chillflix", fetch)
    assert len(calls) == ph.ATTEMPTS == 3
    assert calls[0] == "https://chillflix.lol/"


def test_unknown_provider_is_refused():
    with pytest.raises(streaming.EmbedUrlError):
        ph.check("not-a-provider", stub(100))


# --- all providers --------------------------------------------------------


def test_results_are_sorted_fastest_first_with_unreachable_last():
    results = [
        {"provider": "slowpoke", "ok": True, "ms": 900, "status": "ok", "reason": None},
        {"provider": "quick", "ok": True, "ms": 90, "status": "fast", "reason": None},
        {"provider": "gone", "ok": False, "ms": None, "status": "unreachable", "reason": "DNS failed"},
        {"provider": "middling", "ok": True, "ms": 420, "status": "ok", "reason": None},
    ]
    order = [item["provider"] for item in ph.sort_results(results)]
    assert order == ["quick", "middling", "slowpoke", "gone"]


def test_check_all_uses_at_most_four_workers(tmp_path):
    lock = threading.Lock()
    state = {"active": 0, "peak": 0}

    def fetch(url, timeout):
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        time.sleep(0.05)
        with lock:
            state["active"] -= 1
        return 200, 100

    results = ph.check_all(streaming.PROVIDERS, http=fetch, cache=tmp_path / "h.json")
    assert len(results) == len(streaming.PROVIDERS)
    assert state["peak"] <= ph.MAX_CONCURRENCY
    assert all(item["ok"] for item in results)


def test_cache_is_reused_and_force_bypasses_it(tmp_path):
    cache = tmp_path / "health.json"
    calls = []

    def fetch(url, timeout):
        calls.append(url)
        return 200, 120

    ph.check_all(["vidy", "moonflix"], http=fetch, cache=cache)
    assert len(calls) == 2 * ph.ATTEMPTS

    ph.check_all(["vidy", "moonflix"], http=fetch, cache=cache)
    assert len(calls) == 2 * ph.ATTEMPTS, "fresh cache must be reused"

    ph.check_all(["vidy", "moonflix"], http=fetch, cache=cache, force=True)
    assert len(calls) == 4 * ph.ATTEMPTS, "force must check again"


def test_stale_cache_is_ignored(tmp_path):
    cache = tmp_path / "health.json"
    cache.write_text(json.dumps({
        "checked_at": time.time() - ph.CACHE_TTL - 60,
        "results": {"vidy": {"provider": "vidy", "ok": True, "ms": 1,
                             "status": "fast", "reason": None}},
    }))
    calls = []
    ph.check_all(["vidy"], http=lambda url, timeout: (calls.append(url), (200, 500))[1],
                 cache=cache)
    assert len(calls) == ph.ATTEMPTS


def test_corrupt_cache_does_not_crash(tmp_path):
    cache = tmp_path / "health.json"
    cache.write_text("{ not json")
    results = ph.check_all(["vidy"], http=stub(100), cache=cache)
    assert results[0]["ok"] is True


def test_unwritable_cache_does_not_crash(tmp_path):
    target = tmp_path / "nested" / "health.json"  # parent does not exist
    results = ph.check_all(["vidy"], http=stub(100), cache=target)
    assert results[0]["provider"] == "vidy"


def test_only_requested_providers_appear(tmp_path):
    results = ph.check_all(["vidy", "toflix", "bogus"], http=stub(100),
                           cache=tmp_path / "h.json")
    assert sorted(item["provider"] for item in results) == ["toflix", "vidy"]


def test_fastest_and_describe():
    results = [
        {"provider": "vidy", "ok": True, "ms": 180, "status": "fast", "reason": None},
        {"provider": "moonflix", "ok": False, "ms": None, "status": "unreachable",
         "reason": "DNS failed"},
    ]
    assert ph.fastest(results, ("moonflix", "vidy")) == "vidy"
    assert ph.fastest(results, ("moonflix",)) is None
    assert ph.describe(results[0]) == "Vidy · 180 ms"
    assert ph.describe(results[1]) == "Moonflix · unreachable"


# --- automatic selection --------------------------------------------------


def test_auto_apply_needs_every_condition():
    assert ph.should_auto_apply(enabled=True, playing=False, manual=False) is True
    assert ph.should_auto_apply(enabled=False, playing=False, manual=False) is False
    assert ph.should_auto_apply(enabled=True, playing=True, manual=False) is False
    assert ph.should_auto_apply(enabled=True, playing=False, manual=True) is False


def test_autoselect_starts_once_and_reuses_a_fresh_cache(tmp_path):
    cache = tmp_path / "health.json"
    cache.write_text(json.dumps({
        "checked_at": time.time(),
        "results": {"vidy": {"provider": "vidy", "ok": True, "ms": 90,
                             "status": "fast", "reason": None}},
    }))
    calls = []
    seen = []
    engine = ph.AutoSelect(
        Memory(), on_results=seen.append, http=lambda u, t: (calls.append(u), (200, 5))[1],
        cache=cache,
    )
    assert engine.start() is False, "a fresh cache finishes without a thread"
    assert seen and seen[0][0]["provider"] == "vidy"
    assert calls == []


def test_autoselect_runs_one_background_check(tmp_path):
    seen = []
    engine = ph.AutoSelect(
        Memory(), on_results=seen.append, http=slow_stub(), cache=tmp_path / "h.json"
    )
    assert engine.start(force=True) is True
    assert engine.start(force=True) is False, "a second check must not start"
    assert wait_for(lambda: bool(seen))
    assert len(seen[0]) == len(streaming.PROVIDERS)


def test_shutdown_stops_callbacks(tmp_path):
    seen = []
    engine = ph.AutoSelect(
        Memory(), on_results=seen.append, http=slow_stub(delay=0.15),
        cache=tmp_path / "h.json",
    )
    engine.start(force=True)
    engine.shutdown()
    time.sleep(0.6)
    assert seen == []


def test_manual_pick_disables_automatic_selection():
    settings = Memory()
    engine = ph.AutoSelect(settings)
    assert engine.enabled() is True
    engine.mark_manual()
    assert engine.manual is True
    assert settings.get(ph.AUTO_KEY) is False
    assert ph.should_auto_apply(
        enabled=engine.enabled(), playing=False, manual=engine.manual
    ) is False


def test_messages_are_the_documented_ones():
    assert ph.MANUAL_MESSAGE == (
        "Automatic selection disabled because you selected a provider manually."
    )
    assert ph.NO_PROVIDER_MESSAGE == "No provider reachable right now"
