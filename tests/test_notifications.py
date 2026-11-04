"""Notification switches, defaults and de-duplication."""

from __future__ import annotations

import lgl.notifications as notifications
from lgl.notifications import (
    NOTIFY_DOWNLOADS,
    NOTIFY_EPISODES,
    Deduper,
    download_failed,
    download_finished,
    enabled,
    episode_released,
    set_enabled,
)
from lgl.settings import Settings


def settings(tmp_path):
    return Settings(tmp_path / "settings.json", keyring=False)


def test_defaults(tmp_path):
    store = settings(tmp_path)
    assert enabled(store, NOTIFY_DOWNLOADS) is True
    assert enabled(store, NOTIFY_EPISODES) is False
    assert enabled(None, NOTIFY_DOWNLOADS) is False


def test_switch_is_respected(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        notifications, "notify", lambda *a, **k: calls.append((a, k)) or True
    )
    store = settings(tmp_path)
    set_enabled(store, NOTIFY_DOWNLOADS, False)
    assert download_finished(store, "game.tar.gz", at=1.0, dedupe=Deduper()) is False
    set_enabled(store, NOTIFY_DOWNLOADS, True)
    assert download_finished(store, "game.tar.gz", at=2.0, dedupe=Deduper()) is True
    assert len(calls) == 1


def test_episodes_are_quiet_until_enabled(tmp_path, monkeypatch):
    monkeypatch.setattr(notifications, "notify", lambda *a, **k: True)
    store = settings(tmp_path)
    assert episode_released(store, "Frieren", at=1.0, dedupe=Deduper()) is False
    set_enabled(store, NOTIFY_EPISODES, True)
    assert episode_released(store, "Frieren", at=2.0, dedupe=Deduper()) is True


def test_dedupe_window(tmp_path, monkeypatch):
    monkeypatch.setattr(notifications, "notify", lambda *a, **k: True)
    store = settings(tmp_path)
    guard = Deduper(window=60)
    assert download_finished(store, "a.zip", at=100.0, dedupe=guard) is True
    assert download_finished(store, "a.zip", at=130.0, dedupe=guard) is False
    assert download_finished(store, "a.zip", at=200.0, dedupe=guard) is True


def test_failure_reports_once(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(
        notifications, "notify", lambda title, message, **k: seen.append(message) or True
    )
    store = settings(tmp_path)
    guard = Deduper(window=60)
    download_failed(store, "a.zip", "disk full", at=1.0, dedupe=guard)
    download_failed(store, "a.zip", "disk full", at=2.0, dedupe=guard)
    assert len(seen) == 1 and "disk full" in seen[0]
