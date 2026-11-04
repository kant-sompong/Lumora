import pytest

from lgl import providers
from lgl.settings import Settings


@pytest.fixture()
def settings(tmp_path):
    return Settings(tmp_path / "settings.json", keyring=lambda *a, **k: None)


def test_registry_splits_metadata_and_playback():
    meta = {p.key for p in providers.registry() if p.kind == providers.METADATA}
    play = {p.key for p in providers.registry() if p.kind == providers.PLAYBACK}
    assert "tmdb" in meta and "rawg" in meta
    assert "torrent" in play and "framextv" in play
    assert not meta & play


def test_unknown_provider_is_handled():
    assert providers.get("nope") is None
    assert providers.is_enabled(None, "nope") is False
    assert providers.explain(None, "nope") == ""


def test_missing_key_reports_missing_key_state(settings):
    states = {s.key: s for s in providers.status(settings)}
    assert states["tmdb"].state == providers.MISSING_KEY
    assert states["catalog"].state == providers.READY
    assert "TMDB" in states["tmdb"].detail


def test_valid_key_turns_ready_and_is_never_echoed(settings):
    key = "a" * 32
    settings.set("api_keys", {"tmdb": key})
    item = {s.key: s for s in providers.status(settings)}["tmdb"]
    assert item.state == providers.READY
    report = "\n".join(providers.summary_lines(settings))
    assert key not in report
    assert providers.masked(key) == "set (32 characters)"
    assert providers.masked("") == "not set"


def test_short_or_malformed_keys_are_rejected_without_quoting_them(settings):
    bad_values = ("sh0rtkey", "z" * 32, "a" * 32 + " " + "b", "x" * 300)
    for bad in bad_values:
        ok, message = providers.validate_credential("tmdb", bad)
        assert ok is False
        assert bad.strip() not in message, "the key was echoed back"
        assert "z" * 32 not in message and "x" * 300 not in message
    ok, message = providers.validate_credential("tmdb", "b" * 32)
    assert ok and "verified once a request succeeds" in message


def test_invalid_stored_key_is_reported_as_invalid(settings):
    settings.set("api_keys", {"mal": "1234"})
    item = {s.key: s for s in providers.status(settings)}["mal"]
    assert item.state == providers.INVALID_KEY
    assert item.ready is False


def test_disable_and_enable_roundtrip(settings):
    assert providers.is_enabled(settings, "torrent") is True
    assert providers.set_enabled(settings, "torrent", False) is True
    assert providers.is_enabled(settings, "torrent") is False
    assert {s.key: s for s in providers.status(settings)}["torrent"].state == providers.DISABLED
    assert "torrent" not in providers.ready_keys(settings, kind=providers.PLAYBACK)
    providers.set_enabled(settings, "torrent", True)
    assert "torrent" in providers.ready_keys(settings, kind=providers.PLAYBACK)
    assert providers.set_enabled(settings, "nope", True) is False


def test_blocked_lists_only_problems(settings):
    keys = {b.key for b in providers.blocked(settings)}
    assert "tmdb" in keys and "catalog" not in keys


def test_every_provider_has_a_label_and_limitation():
    for provider in providers.registry():
        assert provider.label and provider.summary
        assert provider.limitation
        if provider.requires_key:
            assert provider.key_setting in ("rawg", "tmdb", "mal")
        else:
            assert provider.key_setting is None
