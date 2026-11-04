import json

import pytest

from lgl import history_sync as hs


class Store(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)

    def set(self, key, value):
        self[key] = value


class BoomAdapter:
    name = "boom"
    label = "Boom"
    requires_token = False

    def push(self, entries):
        raise OSError("connection reset by peer")

    def pull(self):
        raise OSError("connection reset by peer")


@pytest.fixture()
def settings():
    return Store()


def test_sync_is_disabled_until_consented(settings, tmp_path):
    outbox = hs.Outbox(tmp_path / "outbox.json")
    syncer = hs.Syncer(settings, outbox, adapters={})
    assert hs.config(settings).enabled is False
    result = syncer.run([{"title": "Heat", "kind": "movie"}])
    assert result.status == hs.DISABLED
    assert hs.enable(settings, "local-file", consent=False) is False
    assert hs.config(settings).enabled is False
    assert hs.enable(settings, "", consent=True) is False
    assert hs.enable(settings, "local-file", consent=True) is True
    assert hs.config(settings).enabled is True


def test_status_lines_are_honest(settings):
    assert "off" in hs.status_line(hs.config(settings))
    hs.enable(settings, "local-file", consent=True)
    assert "has not run yet" in hs.status_line(hs.config(settings))
    hs.disable(settings)
    assert "off" in hs.status_line(hs.config(settings))


def test_missing_adapter_is_an_error_not_a_silent_success(settings, tmp_path):
    hs.enable(settings, "some-service", consent=True)
    syncer = hs.Syncer(settings, hs.Outbox(tmp_path / "o.json"), adapters={})
    result = syncer.run()
    assert result.status == hs.ERROR and "no adapter" in result.message.lower()


def test_local_adapter_roundtrip_records_last_sync(settings, tmp_path):
    target = tmp_path / "history.json"
    adapter = hs.LocalFileAdapter(target)
    hs.enable(settings, "local-file", consent=True)
    syncer = hs.Syncer(settings, hs.Outbox(tmp_path / "o.json"), adapters={"local-file": adapter}, clock=lambda: 5000.0)
    result = syncer.run([{"title": "Heat", "kind": "movie", "watched_at": 100}])
    assert result.status == hs.OK
    assert result.pushed == 1
    assert hs.config(settings).last_sync == 5000.0
    stored = json.loads(target.read_text("utf-8"))["entries"]
    assert stored[0]["title"] == "Heat"
    # The outbox is cleared once the adapter accepted the records.
    assert len(syncer.outbox) == 0
    # A second run pulls what the file holds without duplicating it.
    again = syncer.run()
    assert again.pulled == 1 and again.merged == 1


def test_merge_deduplicates_and_prefers_newest():
    local = [
        {"title": "Heat", "kind": "movie", "watched_at": 100, "position": 10},
        {"title": "Alien", "kind": "movie", "watched_at": 50},
    ]
    remote = [
        {"title": "heat", "kind": "movie", "watched_at": 200, "position": 90},
        {"title": "Arrival", "kind": "movie", "watched_at": 10},
    ]
    merged, conflicts = hs.merge(local, remote)
    by_title = {entry["title"].lower(): entry for entry in merged}
    assert len(merged) == 3
    assert by_title["heat"]["position"] == 90
    assert conflicts == 1


def test_merge_ignores_rows_without_identity():
    merged, conflicts = hs.merge([{"position": 5}, "nonsense"], [])
    assert merged == [] and conflicts == 0


def test_outbox_persists_deduplicates_and_acknowledges(tmp_path):
    path = tmp_path / "outbox.json"
    outbox = hs.Outbox(path)
    assert outbox.add([{"title": "Heat", "kind": "movie"}]) == 1
    assert outbox.add([{"title": "Heat", "kind": "movie"}]) == 0
    reopened = hs.Outbox(path)
    assert len(reopened) == 1
    assert reopened.acknowledge([{"title": "Heat", "kind": "movie"}]) == 1
    assert len(hs.Outbox(path)) == 0
    reopened.clear()


def test_outbox_is_bounded(tmp_path):
    outbox = hs.Outbox(tmp_path / "o.json")
    for i in range(hs.OUTBOX_LIMIT + 20):
        outbox.add([{"title": f"Title {i}", "kind": "movie"}])
    assert len(outbox) == hs.OUTBOX_LIMIT


def test_transport_failure_keeps_records_queued(settings, tmp_path):
    hs.enable(settings, "boom", consent=True)
    outbox = hs.Outbox(tmp_path / "o.json")
    syncer = hs.Syncer(settings, outbox, adapters={"boom": BoomAdapter()})
    result = syncer.run([{"title": "Heat", "kind": "movie"}])
    assert result.status == hs.OFFLINE
    assert "connection" in result.message.lower()
    assert len(outbox) == 1
    assert hs.config(settings).last_sync == 0.0


def test_tokens_never_belong_in_settings():
    assert hs.storable_token(None, "") is True
    assert hs.storable_token(None, "abc123") is False
    assert "token" not in hs.Config.__dataclass_fields__
