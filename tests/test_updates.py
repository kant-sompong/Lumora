"""Update checks: version comparison and failure honesty."""

from __future__ import annotations

import pytest

from lgl.updates import (
    UpdateCheckError,
    check_for_update,
    interpret_release,
    is_newer,
    parse_version,
    release_from_json,
)


@pytest.mark.parametrize("text,expected", [
    ("0.4.0", (0, 4, 0)),
    ("v0.4.1", (0, 4, 1)),
    ("1.2", (1, 2)),
    ("2.0.0-beta.1", (2, 0, 0)),
    ("", ()),
    ("not a version", ()),
    (None, ()),
])
def test_versions_are_read_conservatively(text, expected):
    assert parse_version(text) == expected


@pytest.mark.parametrize("current,latest,newer", [
    ("0.4.0", "0.4.1", True),
    ("0.4.0", "0.5.0", True),
    ("0.4.0", "1.0.0", True),
    ("0.4.0", "0.4.0", False),
    ("0.4.1", "0.4.0", False),
    ("0.4.10", "0.4.9", False),
    ("0.4.0", "garbage", False),
    ("garbage", "0.4.1", False),
])
def test_comparison_never_nags_on_unreadable_versions(current, latest, newer):
    assert is_newer(current, latest) is newer


def test_release_document_is_read_and_checked():
    status = interpret_release(
        {
            "tag_name": "v0.5.0",
            "html_url": "https://github.com/kant-sompong/Lumora/releases/tag/v0.5.0",
            "body": "Notes",
        },
        "0.4.0",
    )
    assert status.newer is True
    assert status.latest == "0.5.0"
    assert status.url.endswith("/v0.5.0")
    assert "0.5.0 is available" in status.message
    assert status.notes == "Notes"


def test_the_running_release_is_not_an_update():
    status = interpret_release({"tag_name": "v0.4.0"}, "0.4.0")
    assert status.newer is False
    assert "latest release" in status.message


def test_a_document_without_a_tag_is_an_error():
    with pytest.raises(UpdateCheckError):
        release_from_json({"html_url": "x"})
    with pytest.raises(UpdateCheckError):
        release_from_json(["not", "an", "object"])


def test_check_for_update_uses_the_injected_fetcher():
    seen = {}

    def fetcher(url, timeout):
        seen["url"] = url
        seen["timeout"] = timeout
        return {"tag_name": "v9.9.9", "html_url": "https://example.org/r"}

    status = check_for_update("0.4.0", fetcher=fetcher, timeout=3.0)
    assert status.newer is True
    assert seen["url"].endswith("/releases/latest")
    assert seen["timeout"] == 3.0


def test_a_failed_fetch_raises_instead_of_claiming_uptodate():
    def fetcher(url, timeout):
        raise UpdateCheckError("Could not reach GitHub: offline")

    with pytest.raises(UpdateCheckError):
        check_for_update("0.4.0", fetcher=fetcher)
