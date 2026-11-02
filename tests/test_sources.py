"""Source adapters: URL rules, poster choice, config validation."""

from __future__ import annotations

import pytest

from lgl.sources import (
    CONFIG_FIELDS,
    SourceError,
    _as_int,
    _as_text,
    _dig,
    _error_detail,
    _poster_url,
    _text_list,
    _url_allowed,
    archive_collection_from_url,
    available_sources,
    normalize_source_config,
    source_for_url,
    supported_hosts,
)


# --------------------------------------------------------------------------
# URL allow-listing
# --------------------------------------------------------------------------


def test_url_allow_list_requires_https_and_the_named_host():
    assert _url_allowed("https://archive.org/details/x", ["archive.org"])
    assert _url_allowed("https://ia800.us.archive.org/x", ["archive.org"])
    assert not _url_allowed("http://archive.org/details/x", ["archive.org"])
    assert not _url_allowed("https://archive.org.evil.test/x", ["archive.org"])
    assert not _url_allowed("https://user:pw@archive.org/x", ["archive.org"])
    assert not _url_allowed("not a url", ["archive.org"])


def test_collection_url_parsing():
    assert archive_collection_from_url("https://archive.org/details/demo") == "demo"
    root = archive_collection_from_url("https://archive.org")
    assert isinstance(root, str) and root  # the site root means the default collection
    with pytest.raises(SourceError):
        archive_collection_from_url("https://archive.org/browse/demo")
    with pytest.raises(SourceError):
        archive_collection_from_url("https://example.org/details/demo")
    with pytest.raises(SourceError):
        archive_collection_from_url("https://archive.org/details/../escape")


# --------------------------------------------------------------------------
# provider error bodies
# --------------------------------------------------------------------------


def test_error_detail_only_echoes_provider_explanations():
    assert _error_detail(b'{"error": "The monthly API limit reached"}') == (
        "The monthly API limit reached"
    )
    assert _error_detail(b'{"detail": "  two   spaces "}') == "two spaces"
    assert _error_detail(b"plain explanation\n") == "plain explanation"
    assert _error_detail(b"") == ""
    assert _error_detail(b'{"unknown": 1}') == ""
    assert _error_detail(b'["a", "b"]') == ""


# --------------------------------------------------------------------------
# normalising provider values
# --------------------------------------------------------------------------


def test_scalar_helpers_are_conservative():
    assert _as_text("x") == "x"
    assert _as_text(5) == "5"
    assert _as_text(["", "y"]) == "y"
    assert _as_text(True) == ""
    assert _as_int(7) == 7
    assert _as_int("8") == 8
    assert _as_int("8.5") is None
    assert _as_int(True) is None
    assert _text_list("Action, Adventure") == ("Action", "Adventure")
    assert _text_list(["Action", 5, None]) == ("Action",)


def test_dig_follows_paths_through_lists():
    data = {"a": {"b": [{"c": 1}, {"c": 2}]}}
    assert _dig(data, "a.b.1.c") == 2
    assert _dig(data, "a.missing") is None


def test_poster_prefers_a_logo_and_never_a_screenshot():
    files = [
        {"name": "screenshot.png"},
        {"name": "game_meta.xml"},
        {"name": "logo.png"},
    ]
    url = _poster_url("demo", files)
    assert url.startswith("https://archive.org/download/demo/")
    assert "screenshot" not in url
    assert url.endswith("logo.png")
    assert _poster_url("demo", [{"name": "logo.png", "private": True}]) == ""


# --------------------------------------------------------------------------
# user-supplied config
# --------------------------------------------------------------------------


def test_config_accepts_only_known_fields_and_pagination():
    field = sorted(CONFIG_FIELDS)[0]
    config = normalize_source_config({
        "records": "games",
        "fields": {field: "value"},
        "pagination": {
            "type": "page", "param": "page", "size_param": "limit",
            "start": 1, "total_path": "count",
        },
    })
    assert config["records"] == "games"
    assert config["fields"] == {field: "value"}
    assert config["pagination"]["type"] == "page"
    assert config["pagination"]["start"] == 1


def test_config_accepts_json_text_and_blank_input():
    assert normalize_source_config("") == {}
    assert normalize_source_config(None) == {}
    assert normalize_source_config('{"records": "games"}') == {"records": "games"}


@pytest.mark.parametrize("bad", [
    "{not json",
    [1, 2],
    {"fields": {"not_a_field": "x"}},
    {"fields": {"id": ""}},
    {"pagination": {"type": "sideways"}},
    {"pagination": {"type": "page", "param": "bad name!"}},
    {"pagination": {"type": "page", "start": -1}},
    {"pagination": {"type": "page", "start": True}},
    {"records": 5},
])
def test_config_refuses_what_it_cannot_honour(bad):
    with pytest.raises(SourceError):
        normalize_source_config(bad)


# --------------------------------------------------------------------------
# source registry
# --------------------------------------------------------------------------


def test_registry_exposes_sources_and_hosts():
    sources = available_sources()
    assert sources and all(hasattr(s, "page") for s in sources)
    hosts = supported_hosts()
    assert "archive.org" in hosts


def test_unknown_urls_are_refused_with_a_reason():
    with pytest.raises(SourceError):
        source_for_url("https://example.org/not-a-catalogue")
    with pytest.raises(SourceError):
        source_for_url("not a url")
