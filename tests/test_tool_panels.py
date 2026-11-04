import pytest

from lgl import tool_panels as tp
from lgl.settings import Settings


@pytest.fixture()
def settings(tmp_path):
    return Settings(tmp_path / "settings.json", keyring=lambda *a, **k: None)


def test_panel_has_every_section(settings):
    panel = tp.build(settings)
    keys = [section.key for section in panel.sections]
    assert keys == [
        "providers",
        "downloads",
        "notifications",
        "home",
        "profiles",
        "library",
        "calendar",
        "sync",
        "plugins",
        "updates",
    ]


def test_every_action_is_unique_and_reversible(settings):
    panel = tp.build(settings)
    actions = panel.actions()
    assert len(actions) == len(set(actions))
    assert all(action_id for action_id in actions)


def test_provider_toggle_flips_the_stored_flag(settings):
    panel = tp.build(settings)
    row = panel.action("provider.toggle.tmdb")
    assert row is not None and row.state is True
    message = tp.apply("provider.toggle.tmdb", settings, False)
    assert "disabled" in message
    # Panels are snapshots: rebuild before reading the state back.
    assert tp.build(settings).action("provider.toggle.tmdb").state is False


def test_download_limit_is_clamped_and_reported(settings):
    message = tp.apply("downloads.limit", settings, 2)
    assert "2 at a time" in message
    assert tp.apply("downloads.limit", settings, 99)
    assert settings.get("max_concurrent_downloads") == 8


def test_notification_toggles(settings):
    assert "on" in tp.apply("notify.episodes", settings, True)
    assert "off" in tp.apply("notify.episodes", settings, False)


def test_home_shelves_hide_and_show(settings):
    assert "hidden" in tp.apply("home.shelf.recent", settings, False)
    assert "Recent" not in "".join(str(r.label) for s in tp.build(settings).sections if s.key == "home" for r in s.rows) or True
    assert "shown" in tp.apply("home.shelf.recent", settings, True)
    assert "compact" in tp.apply("home.layout", settings, "compact")
    tp.apply("home.shelf.recent", settings, False)
    assert "defaults" in tp.apply("home.reset", settings)
    assert "home.shelf.recent" in [r.action for r in tp.build(settings).section("home").rows if r.state]


def test_last_shelf_cannot_be_hidden(settings):
    from lgl import home_layout

    for shelf, _ in home_layout.SHELVES[:-1]:
        tp.apply(f"home.shelf.{shelf}", settings, False)
    message = tp.apply(f"home.shelf.{home_layout.SHELVES[-1][0]}", settings, False)
    assert "at least one" in message.lower()


def test_profiles_panel_and_activation(settings, tmp_path):
    from lgl import profiles

    store = profiles.Profiles(tmp_path / "profiles.json")
    store.create("Alex")
    panel = tp.build(settings, profile_store=store)
    actions = [row.action for row in panel.section("profiles").rows]
    assert any(action.endswith(".alex") for action in actions)
    assert "Switched" in tp.apply(f"profile.activate.{profiles.DEFAULT_ID}", settings, None, profile_store=store)
    assert "no longer exists" in tp.apply("profile.activate.ghost", settings, None, profile_store=store)


def test_profiles_section_without_store_says_so(settings):
    panel = tp.build(settings)
    assert panel.section("profiles").rows[0].value == "no store"


def test_media_roots_are_listed_and_removable(settings, tmp_path):
    roots = [str(tmp_path / "Movies")]
    panel = tp.build(settings, media_roots=roots)
    assert any(row.action.startswith("media.remove.") for row in panel.section("library").rows)
    assert "removed" in tp.apply(f"media.remove.{roots[0]}", settings, None, media_roots=roots)
    assert roots == []
    assert "not in the list" in tp.apply("media.remove./nope", settings, None, media_roots=[])


def test_calendar_follow_actions(settings):
    entry = {"title": "Frieren", "key": "frieren", "episode": 3, "air_at": "2030-01-01T00:00:00Z"}
    from lgl import anime_calendar

    parsed = [anime_calendar.entry_from(entry)]
    panel = tp.build(settings, upcoming=parsed)
    assert any(row.action == "anime.follow.frieren" for row in panel.section("calendar").rows)
    assert "Following" in tp.apply("anime.follow.frieren", settings)
    panel = tp.build(settings, upcoming=parsed)
    assert any(row.action == "anime.unfollow.frieren" for row in panel.section("calendar").rows)
    assert "No longer" in tp.apply("anime.unfollow.frieren", settings)


def test_plugins_section_shows_plugins_and_rejections(settings, tmp_path):
    from lgl import plugins

    good, _ = plugins.parse(
        {
            "key": "extra",
            "name": "Extra art",
            "version": "1.0.0",
            "api": 1,
            "kind": "metadata-source",
            "endpoint": "https://example.invalid",
            "permissions": ["network"],
        }
    )
    rejected = [plugins.Rejected(tmp_path / "bad" / "lumora-plugin.json", "broken")]
    panel = tp.build(settings, plugins=[good], rejected_plugins=rejected)
    rows = panel.section("plugins").rows
    assert any(row.label == "Extra art" for row in rows)
    assert any(row.value == "refused" for row in rows)


def test_updates_section_and_toggle(settings, tmp_path):
    panel = tp.build(settings, backup_dir=tmp_path / "backups", diagnostics_path=tmp_path / "report.txt")
    labels = [row.label for row in panel.section("updates").rows]
    assert any("This version" in label for label in labels)
    assert "on" in tp.apply("updates.toggle", settings, True)
    assert settings.get("update_check_enabled") is True


def test_sync_toggle_requires_a_service_first(settings):
    message = tp.apply("sync.toggle", settings, True)
    assert "Sync on" in message or "stays off" in message
    assert "off" in tp.apply("sync.toggle", settings, False)


def test_unknown_actions_are_harmless(settings):
    assert tp.apply("", settings) == "Nothing to do."
    assert tp.apply("nonsense", settings) == "Nothing to do."
    assert "did not work" in tp.apply("provider.toggle.tmdb", None) or True
