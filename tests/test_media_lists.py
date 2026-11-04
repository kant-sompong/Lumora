"""Media favourites and the Continue Watching shelf."""

from __future__ import annotations

from lgl.media_lists import (
    CONTINUE_KEY,
    FAVORITES_KEY,
    dismiss,
    favorites,
    is_favorite,
    record_position,
    set_favorite,
    shelf,
    stored,
    toggle_favorite,
)
from lgl.settings import Settings


def settings(tmp_path):
    return Settings(tmp_path / "settings.json", keyring=False)


def test_favorites_toggle_and_dedupe(tmp_path):
    store = settings(tmp_path)
    assert favorites(store) == []
    assert toggle_favorite(store, "movie:603") is True
    assert is_favorite(store, "movie:603") is True
    assert toggle_favorite(store, "movie:603") is False
    assert favorites(store) == []


def test_favorites_ignore_unusable_keys(tmp_path):
    store = settings(tmp_path)
    assert set_favorite(store, "not-a-key", True) is False
    assert set_favorite(store, "movie:0", True) is False
    store.set(FAVORITES_KEY, ["movie:1", "movie:1", "junk", 7])
    assert favorites(store) == ["movie:1"]


def test_favorites_persist(tmp_path):
    store = settings(tmp_path)
    set_favorite(store, "tv:1399", True)
    assert favorites(Settings(tmp_path / "settings.json", keyring=False)) == ["tv:1399"]


def test_continue_watching_keeps_real_progress_only(tmp_path):
    store = settings(tmp_path)
    assert record_position(store, key="movie:1", position=2.0, duration=600) is None
    assert record_position(store, key="movie:1", position=120.0, duration=600) is not None
    assert [entry["key"] for entry in shelf(store)] == ["movie:1"]
    store.set(CONTINUE_KEY, [])
    assert record_position(store, key="movie:2", position=595.0, duration=600) is None
    assert stored(store) == []


def test_continue_watching_unknown_duration_is_kept(tmp_path):
    store = settings(tmp_path)
    record_position(store, key="tv:99", position=90.0, title="Some Show")
    assert [entry["title"] for entry in shelf(store)] == ["Some Show"]


def test_shelf_orders_newest_first_and_removes(tmp_path):
    store = settings(tmp_path)
    record_position(store, key="movie:1", position=60, duration=600, updated_at=100)
    record_position(store, key="movie:2", position=60, duration=600, updated_at=200)
    assert [entry["key"] for entry in shelf(store)] == ["movie:2", "movie:1"]
    assert dismiss(store, "movie:2") is True
    assert [entry["key"] for entry in shelf(store)] == ["movie:1"]
    assert dismiss(store, "movie:2") is False
