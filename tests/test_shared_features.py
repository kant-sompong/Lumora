"""Tests for the shared modules the two frontends sit on top of."""

from __future__ import annotations

import datetime as dt

from lgl import (
    content_filters,
    discovery,
    library_tools,
    palette,
    season_compare,
    shelves,
    stats,
    suggestions,
    trailers,
    view_prefs,
)


class FakeSettings:
    """The slice of the settings API these modules use."""

    def __init__(self, values=None):
        self.values = dict(values or {})
        self.saves = 0

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value

    def save(self):
        self.saves += 1


# --- palette ---------------------------------------------------------------


def test_palette_finds_page_before_toolbar_entry():
    found = palette.search("down")
    assert found and found[0].key == "page.downloads"


def test_palette_empty_query_lists_commands_and_maps_pages():
    found = palette.search("", limit=5)
    assert len(found) == 5
    assert palette.page_for("page.library") == "library"
    assert palette.action_for(palette.commands()[0]) == "archive"
    assert palette.search("zzzz-nothing") == []


# --- view preferences ------------------------------------------------------


def test_accent_and_transitions_fall_back_to_defaults():
    assert view_prefs.normalize_accent("3B82F6") == "#3b82f6"
    assert view_prefs.normalize_accent("not a colour") == view_prefs.DEFAULT_ACCENT
    settings = FakeSettings({"page_transitions": "wobble"})
    assert view_prefs.transitions(settings) == "subtle"
    assert view_prefs.set_transitions(settings, "off") == "off"
    assert view_prefs.transitions(settings) == "off"


def test_interface_preferences_round_trip_and_report_readability():
    settings = FakeSettings()
    assert view_prefs.compact(settings) is False
    assert view_prefs.artwork_backgrounds(settings) is True
    assert view_prefs.set_compact(settings, 1) is True
    assert view_prefs.set_accent(settings, "#ffffff") == "#ffffff"
    assert settings.saves == 2
    values = view_prefs.effective(settings)
    assert values[view_prefs.COMPACT] is True
    assert values[view_prefs.ACCENT] == "#ffffff"
    assert view_prefs.accent_is_readable(settings) is False


# --- suggestions -----------------------------------------------------------


def test_suggestions_rank_prefix_first_and_drop_duplicates():
    corpus = ["Heat", "heat wave", "The Heat", "Heathers"]
    found = suggestions.suggest("heat", corpus)
    assert found[0].text == "Heat"
    assert len([item for item in found if item.text.casefold() == "heat"]) == 1
    assert suggestions.suggest("  ", corpus) == []
    assert suggestions.complete("heathe", corpus) == "Heathers"
    assert suggestions.complete("heat", corpus) is None


# --- content filters -------------------------------------------------------


def test_content_filters_parse_junk_and_match_fields():
    parsed = content_filters.parse(
        {
            "min_runtime": 240,
            "max_runtime": 90,
            "languages": ["EN", "en"],
            "min_rating": 7,
            "genres": "Drama",
            "exclude_genres": ["horror"],
        }
    )
    assert (parsed.min_runtime, parsed.max_runtime) == (90, 240)
    assert parsed.languages == ("en",)
    assert parsed.min_rating == 7.0
    assert parsed.genres == ("drama",)
    item = {"runtime": 120, "language": "en", "rating": 7.5, "genres": ["Drama"]}
    assert content_filters.matches(item, parsed) is True
    assert content_filters.matches({**item, "genres": ["Drama", "Horror"]}, parsed) is False
    assert content_filters.matches({**item, "runtime": 400}, parsed) is False
    assert content_filters.apply([item, {"runtime": 400}], parsed) == [item]
    assert "horror" in content_filters.describe(parsed)


# --- discovery -------------------------------------------------------------


def test_discovery_ranks_by_overlap_and_never_recommends_owned():
    items = [
        {"key": "a", "genres": ["drama", "crime"]},
        {"key": "b", "genres": ["drama"]},
        {"key": "c", "genres": ["comedy"]},
        {"key": "d", "genres": ["drama", "crime", "thriller"]},
    ]
    assert [item["key"] for item in discovery.similar(items, "a", limit=2)] == ["d", "b"]
    recs = discovery.recommend(items, ["a"])
    assert recs and all(item["key"] != "a" for item in recs)
    assert discovery.similar(items, "missing") == []


def test_hidden_gems_need_low_popularity_and_a_good_rating():
    items = [
        {"key": "gem", "rating": 8.5, "popularity": 5},
        {"key": "popular", "rating": 9.0, "popularity": 500},
        {"key": "weak", "rating": 5.0, "popularity": 2},
    ]
    assert [item["key"] for item in discovery.hidden_gems(items)] == ["gem"]
    picked = discovery.random_pick(items, rng=__import__("random").Random(3))
    assert picked in items
    assert discovery.random_pick([]) is None


def test_collection_suggestions_cover_missing_genres():
    items = [
        {"key": "owned", "genres": ["drama"]},
        {"key": "also", "title": "Also", "genres": ["drama"]},
        {"key": "none", "genres": ["comedy"]},
    ]
    found = discovery.suggestions(items, ["owned"])
    assert [item["key"] for item in found] == ["also"]


# --- season comparison -----------------------------------------------------


def test_season_compare_reports_shared_and_new_titles():
    items = [
        {"key": "keep", "title": "Keep", "year": 2024, "season": "winter"},
        {"key": "keep", "title": "Keep", "year": 2024, "season": "spring"},
        {"key": "new", "title": "New", "year": 2024, "season": "spring"},
    ]
    buckets = season_compare.group(items)
    assert sorted(buckets) == ["2024 spring", "2024 winter"]
    result = season_compare.compare(items, "2024 winter", "2024 spring")
    assert [item["title"] for item in result["shared"]] == ["Keep"]
    assert [item["title"] for item in result["only_second"]] == ["New"]
    assert "1 in both" in season_compare.summary(result)


# --- statistics ------------------------------------------------------------


def test_heatmap_counts_days_and_totals_track_streaks():
    today = dt.date(2024, 5, 15)
    events = [
        {"ts": "2024-05-15T10:00:00Z"},
        {"ts": "2024-05-13T10:00:00Z"},
        {"ts": "2024-05-14T10:00:00Z"},
        {"ts": "garbage"},
    ]
    grid = stats.heatmap(events, weeks=2, today=today)
    assert len(grid) == 7 and all(len(row) == 2 for row in grid)
    assert grid[today.weekday()][1] == 1
    assert sum(sum(row) for row in grid) == 3
    assert stats.totals(events) == {"events": 4, "days": 3, "streak": 3}
    assert stats.busiest_day(events) is not None


def test_statistics_split_and_metadata_coverage():
    rows = [
        {"kind": "game", "title": "Game"},
        {"kind": "movie", "title": "Film"},
        {"title": "Unknown"},
    ]
    buckets = stats.split(rows)
    assert len(buckets["game"]) == 1 and len(buckets["media"]) == 1
    assert stats.timeline([{"ts": 1700000000, "title": "Seen"}])[0][1] == "Seen"
    assert stats.coverage([{"poster": "p"}])["poster"] == 1
    assert stats.coverage([{"poster": "p"}])["titles"] == 1
    assert stats.favourite_genres([{"genres": ["Drama"]}]) == [("Drama", 1)]


# --- library tools ---------------------------------------------------------


def test_tags_are_normalised_and_persisted():
    settings = FakeSettings()
    assert library_tools.add_tag(settings, {"key": "a"}, "  Cozy, Games ") == ("cozy games",)
    assert library_tools.add_tag(settings, "a", "cozy games") == ("cozy games",)
    assert library_tools.add_tag(settings, "a", "Favourite") == ("cozy games", "favourite")
    assert library_tools.remove_tag(settings, "a", "Favourite") == ("cozy games",)
    assert library_tools.remove_tag(settings, "a", "cozy games") == ()
    assert not settings.values.get(library_tools.TAGS_KEY)
    assert settings.saves >= 1


def test_duplicates_and_edits_only_touch_the_target():
    rows = [
        {"key": "1", "title": " Dune ", "size": 10},
        {"key": "2", "title": "dune", "size": 10},
        {"key": "3", "title": "Arrival", "size": 10},
    ]
    groups = library_tools.duplicates(rows)
    assert len(groups) == 1 and len(groups[0]) == 2
    edited, changed = library_tools.bulk_update(rows, ["2"], "year", 2021)
    assert changed == 1
    assert [row.get("year") for row in edited] == [None, 2021, None]
    assert library_tools.bulk_update(rows, ["1"], "size", 5)[1] == 0
    assert library_tools.apply_edit(rows[2], "year", "not a year").get("year") is None
    assert library_tools.apply_edit(rows[2], "title", "  Arrival  ")["title"] == "Arrival"


# --- trailers --------------------------------------------------------------


def test_trailers_never_autoplay_without_permission():
    item = {"trailer_url": "https://www.youtube.com/watch?v=1"}
    plan = trailers.plan(item)
    assert plan["autoplay"] is False and plan["external"] is True
    assert trailers.autoplay_allowed(FakeSettings({"trailer_autoplay": True})) is True
    assert trailers.plan({"trailer": "https://example.test/t.mp4"})["external"] is False
    assert trailers.trailer_url({"trailer": "javascript:alert(1)"}) == ""
    assert trailers.plan({})["reason"] == "No trailer for this title"
    assert trailers.summary({}) == "No trailer"


# --- shelves ---------------------------------------------------------------


def test_shelves_store_filters_and_render_matching_titles():
    settings = FakeSettings()
    assert shelves.add(settings, "Short films", {"max_runtime": 100}) == "short-films"
    assert [row["id"] for row in shelves.shelves(settings)] == ["short-films"]
    items = [{"title": "Short", "runtime": 80}, {"title": "Long", "runtime": 180}]
    rendered = shelves.render(settings, items)
    assert rendered == [("Short films", [items[0]])]
    assert shelves.add(settings, "Second", {}) == "second"
    assert shelves.remove(settings, "second") is True
    assert shelves.add(settings, "", {}) == ""


def test_shelves_refuse_to_grow_past_the_limit():
    settings = FakeSettings()
    for index in range(shelves.MAX_SHELVES):
        assert shelves.add(settings, f"Shelf {index}") != ""
    assert shelves.add(settings, "One too many") == ""
    assert len(shelves.shelves(settings)) == shelves.MAX_SHELVES


# --- panel wiring ----------------------------------------------------------


def test_interface_section_actions_write_preferences():
    from lgl import tool_panels

    settings = FakeSettings()
    panel = tool_panels.build(settings)
    actions = panel.actions()
    assert {"view.compact", "view.accent", "view.transitions"} <= set(actions)

    assert tool_panels.apply("view.compact", settings, True) == "Compact layout on."
    assert view_prefs.compact(settings) is True
    assert "Accent set" in tool_panels.apply("view.accent", settings, "#0f9d8f")
    assert view_prefs.accent(settings) == "#0f9d8f"
    assert tool_panels.apply("view.transitions", settings, "off") == "Page transitions off."
    assert view_prefs.transitions(settings) == "off"
    assert tool_panels.apply("view.spoilers", settings, True) == "Spoiler-free mode on."
    assert view_prefs.spoiler_free(settings) is True

def test_with_accent_keeps_labels_readable():
    from lgl import theme

    for _mode, palette in theme.PALETTES.items():
        for accent, _name in view_prefs.ACCENT_CHOICES:
            tinted = theme.with_accent(palette, accent)
            assert tinted["accent"] == accent
            assert theme.contrast_ratio(tinted["on_accent"], tinted["accent_fill"]) >= 4.5
            assert theme.contrast_ratio(tinted["accent_text"], palette["surface"]) >= 4.5
            # the rest of the palette is untouched
            assert tinted["bg"] == palette["bg"]
            assert tinted["fg" if "fg" in palette else "text"] == palette.get("text")


def test_with_accent_ignores_junk():
    from lgl import theme

    palette = theme.palette("dark")
    assert theme.with_accent(palette, "not a colour") == palette
    assert theme.with_accent(palette, "#12345") == palette


def test_css_variables_carry_the_custom_accent():
    from lgl import theme

    sheet = theme.css_variables("light", accent="#0f9d8f")
    assert "@define-color accent #0f9d8f;" in sheet
    assert "@define-color accent_text #0f9d8f;" not in sheet  # pushed darker for light
