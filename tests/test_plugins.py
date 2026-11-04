import json

import pytest

from lgl import plugins


GOOD = {
    "key": "tmdb-extras",
    "name": "TMDB extras",
    "version": "1.0.0",
    "api": 1,
    "kind": "metadata-source",
    "summary": "Adds extra artwork",
    "endpoint": "https://example.invalid/api",
    "permissions": ["network"],
}


def test_valid_manifest_parses():
    plugin, problems = plugins.parse(dict(GOOD))
    assert problems == []
    assert plugin.key == "tmdb-extras"
    assert plugin.compatible is True
    assert plugin.needs_network is True


@pytest.mark.parametrize(
    "mutate,expected",
    [
        (lambda m: m.pop("key"), "key"),
        (lambda m: m.update(key="Bad Key!"), "lowercase"),
        (lambda m: m.pop("name"), "name"),
        (lambda m: m.update(version="1.0"), "1.2.3"),
        (lambda m: m.update(api=2), "API 1"),
        (lambda m: m.update(api="1"), "must be a number"),
        (lambda m: m.update(kind="theme"), "kind"),
        (lambda m: m.update(endpoint="http://insecure.invalid"), "https"),
        (lambda m: m.update(permissions=[], endpoint=GOOD["endpoint"]), "network"),
        (lambda m: m.update(permissions=["root"]), "Unknown permission"),
        (lambda m: m.pop("endpoint"), "endpoint"),
    ],
)
def test_invalid_manifests_are_explained(mutate, expected):
    manifest = dict(GOOD)
    mutate(manifest)
    plugin, problems = plugins.parse(manifest)
    assert plugin is None
    assert any(expected in problem for problem in problems)


def test_executable_fields_are_refused():
    for field in plugins.EXECUTABLE_FIELDS:
        manifest = dict(GOOD, **{field: "./run.sh"})
        plugin, problems = plugins.parse(manifest)
        assert plugin is None
        assert any("declarative" in problem for problem in problems)


def test_load_file_handles_bad_input(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", "utf-8")
    assert plugins.load_file(bad)[0] is None
    huge = tmp_path / "huge.json"
    huge.write_text(json.dumps(dict(GOOD, blob="x" * (plugins.MAX_MANIFEST_BYTES + 10))), "utf-8")
    plugin, problems = plugins.load_file(huge)
    assert plugin is None and "exceed" in problems[0]


def test_discover_skips_duplicates_and_reports_rejections(tmp_path):
    one = tmp_path / "one"
    one.mkdir()
    (one / plugins.MANIFEST_NAME).write_text(json.dumps(GOOD), "utf-8")
    two = tmp_path / "two"
    two.mkdir()
    (two / plugins.MANIFEST_NAME).write_text(json.dumps(GOOD), "utf-8")
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / plugins.MANIFEST_NAME).write_text(json.dumps({"key": "x"}), "utf-8")
    found, rejected = plugins.discover([tmp_path])
    assert len(found) == 1
    assert {r.path.name for r in rejected} == {plugins.MANIFEST_NAME}
    assert any("Duplicate" in r.reason or "required" in r.reason for r in rejected)


def test_permission_summary_and_security_notes():
    plugin, _ = plugins.parse(dict(GOOD))
    text = plugins.permission_summary(plugin)
    assert "contacts" in text and "example.invalid" in text
    local, _ = plugins.parse({"key": "k1", "name": "K", "version": "1.0.0", "api": 1, "kind": "catalogue"})
    assert plugins.permission_summary(local) == "Needs no access."
    notes = " ".join(plugins.security_notes()).lower()
    assert "declarative" in notes and "refused" in notes
