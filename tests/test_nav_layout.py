"""The navigation map: rail order, page tabs and the trimmed header menu."""

from __future__ import annotations

from lgl import nav_layout

#: Every entry the 22-item header menu used to hold.
OLD_ENTRIES = (
    "palette", "sources", "kryoto", "queue", "maintenance", "folder", "updates",
    "calendar", "seasons", "viewed", "surprise", "statistics", "tags", "details",
    "bulk", "duplicates", "discover", "favourites", "activity", "collections",
    "shortcuts", "profiles", "tools", "settings", "about",
)


def test_the_rail_lists_four_pages_without_discover():
    assert nav_layout.rail_keys() == ("browse", "library", "media", "downloads")
    assert nav_layout.RAIL_BOTTOM == (("settings", "Settings"), ("about", "About"))


def test_discover_is_not_a_page_of_its_own_any_more():
    assert "discover" not in nav_layout.page_keys()
    assert nav_layout.tabs_for("discover") == ()
    assert nav_layout.place("discover") == ("page", "browse", None)


def test_library_has_seven_tabs_without_tags():
    assert [label for _key, label in nav_layout.tabs_for("library")] == [
        "Games", "Favourites", "Collections", "Activity", "Statistics",
        "Recently viewed", "Complete the set",
    ]


def test_media_keeps_its_six_tabs_and_adds_calendar_and_compare():
    assert [label for _key, label in nav_layout.tabs_for("media")] == [
        "Home", "Movies", "TV Shows", "Anime", "My List", "History",
        "Calendar", "Compare seasons",
    ]


def test_the_header_menu_holds_only_the_allowed_ten_entries():
    assert nav_layout.menu_labels() == (
        "Command palette…", "Profiles…", "Keyboard shortcuts…", "Tools…",
        "Website Sources…", "Kryoto Catalog…", "Show Download Queue",
        "Maintenance", "Settings…", "About Lumora",
    )


def test_every_old_menu_entry_has_exactly_one_place():
    """No entry of the old menu may be lost, and none may be invented."""
    assert [entry for entry in OLD_ENTRIES if entry not in nav_layout.PLACES] == []
    assert [entry for entry in nav_layout.PLACES if entry not in OLD_ENTRIES] == []


def test_every_page_place_points_at_a_real_tab():
    for entry, (kind, page, tab) in nav_layout.PLACES.items():
        assert kind in ("menu", "page", "dialog", "action"), entry
        if kind == "page":
            assert page in nav_layout.page_keys(), entry
        if kind == "action":
            assert page in nav_layout.page_keys() or page == "surprise", entry
        if tab:
            assert nav_layout.has_tab(page or "", tab), entry


def test_no_two_old_entries_land_on_the_same_tab():
    seen: dict[tuple[str, str], str] = {}
    for entry, (kind, page, tab) in nav_layout.PLACES.items():
        if kind != "page" or not tab:
            continue
        key = (page or "", tab)
        assert key not in seen, f"{entry} and {seen[key]} both open {page}/{tab}"
        seen[key] = entry


def test_entries_on_lists_what_a_page_absorbed():
    assert nav_layout.entries_on("library") == [
        "activity", "bulk", "collections", "favourites", "statistics", "viewed",
    ]
    assert nav_layout.entries_on("media") == ["calendar", "seasons"]
    assert nav_layout.entries_on("browse") == ["discover"]
    assert nav_layout.place("tags") == ("dialog", "tags", None)


def test_menu_for_keeps_the_menus_order():
    assert nav_layout.menu_for(["about", "palette", "tools"]) == [
        "Command palette…", "Tools…", "About Lumora",
    ]
