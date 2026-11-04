import hashlib

import pytest

from lgl import updates


class Store(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)

    def set(self, key, value):
        self[key] = value


def test_checks_are_opt_in():
    settings = Store()
    assert updates.check_enabled(settings) is False
    assert updates.due(settings, now=1_000_000) is False
    updates.set_check_enabled(settings, True)
    assert updates.check_enabled(settings) is True
    assert updates.due(settings, now=1_000_000) is True


def test_due_respects_interval_and_last_check():
    settings = Store()
    updates.set_check_enabled(settings, True)
    updates.record_check(settings, when=1_000_000)
    assert updates.due(settings, now=1_000_000 + 60) is False
    assert updates.due(settings, now=1_000_000 + 8 * 86400) is True
    updates.set_interval_days(settings, 30)
    assert updates.due(settings, now=1_000_000 + 8 * 86400) is False


def test_interval_is_clamped():
    settings = Store()
    assert updates.interval_days(settings) == updates.DEFAULT_INTERVAL_DAYS
    assert updates.set_interval_days(settings, 0) == updates.MIN_INTERVAL_DAYS
    assert updates.set_interval_days(settings, 999) == updates.MAX_INTERVAL_DAYS
    assert updates.set_interval_days(settings, "junk") == updates.DEFAULT_INTERVAL_DAYS


def test_artifact_verification(tmp_path):
    payload = b"installer bytes"
    good = hashlib.sha256(payload).hexdigest()
    assert updates.verify_artifact(payload, good)[0] is True
    path = tmp_path / "setup.exe"
    path.write_bytes(payload)
    assert updates.verify_artifact(path, good)[0] is True
    ok, message = updates.verify_artifact(payload, "0" * 64)
    assert ok is False and "does not match" in message
    ok, message = updates.verify_artifact(payload, "")
    assert ok is False and "not trusted" in message
    ok, message = updates.verify_artifact(tmp_path / "missing", good)
    assert ok is False and "could not be read" in message


def test_version_and_instructions_mention_releases():
    assert updates.version_line().startswith("Lumora ")
    for platform in ("Windows", "Linux"):
        text = updates.manual_instructions(platform)
        assert updates.RELEASES_PAGE in text
    assert updates.notes_url("0.6.6").endswith("/tag/v0.6.6")
    assert updates.notes_url("v1.2.3").endswith("/tag/v1.2.3")
    assert updates.notes_url("") == updates.RELEASES_PAGE
