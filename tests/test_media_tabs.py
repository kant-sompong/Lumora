"""The shared tab model: deduplication, subtitles and closing rules.

The Qt and GTK strips both draw this store, so these rules are checked once,
without a toolkit or a display.
"""

from __future__ import annotations

from lgl import media_tabs


def test_subtitles_name_the_selection_or_the_kind():
    assert media_tabs.subtitle_for("tv", 2, 5) == "S2 · EP5"
    assert media_tabs.subtitle_for("anime", 1, 12) == "S1 · EP12"
    assert media_tabs.subtitle_for("movie") == "Movie"
    assert media_tabs.subtitle_for("tv") == "TV Show"
    assert media_tabs.subtitle_for("anime") == "Anime"
    assert media_tabs.subtitle_for("", None, None) == "Media"


def test_subtitles_count_a_bare_season_as_a_selection():
    assert media_tabs.subtitle_for("tv", 3) == "S3"


def test_opening_the_same_key_twice_keeps_one_tab():
    store = media_tabs.TabStore()
    first, created = store.open("tmdb:movie:1", "Interstellar", "movie")
    second, again = store.open("tmdb:movie:1", "Interstellar", "movie")
    assert created is True and again is False
    assert first is second
    assert len(store) == 1
    assert store.active_id == first.tab_id


def test_opening_a_new_key_activates_its_tab():
    store = media_tabs.TabStore()
    store.open("tmdb:movie:1", "One", "movie")
    second, _ = store.open("tmdb:tv:2", "Two", "tv")
    assert len(store) == 2
    assert store.active is second


def test_closing_the_active_tab_selects_its_right_neighbour():
    store = media_tabs.TabStore()
    store.open("a", "A", "movie")
    middle, _ = store.open("b", "B", "movie")
    right, _ = store.open("c", "C", "movie")
    store.activate(middle.tab_id)

    successor = store.close(middle.tab_id)

    assert successor is right
    assert store.active is right


def test_closing_the_last_tab_selects_its_left_neighbour():
    store = media_tabs.TabStore()
    left, _ = store.open("a", "A", "movie")
    right, _ = store.open("b", "B", "movie")
    store.activate(right.tab_id)

    successor = store.close(right.tab_id)

    assert successor is left
    assert store.active is left


def test_closing_an_inactive_tab_keeps_the_active_one():
    store = media_tabs.TabStore()
    first, _ = store.open("a", "A", "movie")
    active, _ = store.open("b", "B", "movie")

    successor = store.close(first.tab_id)

    assert successor is active
    assert store.active is active
    assert len(store) == 1


def test_closing_the_only_tab_leaves_nothing_active():
    store = media_tabs.TabStore()
    tab, _ = store.open("a", "A", "movie")
    assert store.close(tab.tab_id) is None
    assert store.empty
    assert store.active is None


def test_update_stores_restorable_state_without_touching_identity():
    store = media_tabs.TabStore()
    tab, _ = store.open("a", "A", "tv")
    store.update(tab.tab_id, season=2, episode=5, scroll=140, playing=True,
                 position=754.0, url="https://provider.test/x")
    stored = store.find("a")
    assert stored.title == "A"
    assert stored.season == 2 and stored.episode == 5
    assert stored.subtitle == "S2 · EP5"
    assert stored.scroll == 140
    assert stored.playing is True
    assert stored.position == 754.0
    assert stored.url == "https://provider.test/x"


def test_tabs_copy_their_payload_when_read():
    store = media_tabs.TabStore()
    tab, _ = store.open("a", "A")
    store.update(tab.tab_id, payload={"item": object()})
    copied = store.tabs[0]
    copied.payload["extra"] = 1
    assert "extra" not in store.tabs[0].payload


def test_positions_format_for_the_resume_note():
    assert media_tabs.format_position(754) == "12:34"
    assert media_tabs.format_position(3723) == "1:02:03"
    assert media_tabs.format_position(0) == ""
    assert media_tabs.format_position("nonsense") == ""


def test_a_resumed_url_carries_its_start_only_once():
    assert media_tabs.with_start("https://x.test/a.m3u8", 754) == (
        "https://x.test/a.m3u8#t=754"
    )
    assert media_tabs.with_start("https://x.test/a.m3u8#t=12", 754) == (
        "https://x.test/a.m3u8#t=754"
    )
    assert media_tabs.with_start("https://x.test/a.m3u8", 0) == (
        "https://x.test/a.m3u8"
    )


def test_a_resume_keeps_the_urls_own_fragment():
    assert media_tabs.with_start("https://x.test/e#foo", 754) == (
        "https://x.test/e#foo&t=754"
    )
    assert media_tabs.with_start("https://x.test/e#t=10&foo", 754) == (
        "https://x.test/e#foo&t=754"
    )
