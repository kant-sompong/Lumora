"""What a catalogue link must prove before it reaches the download manager.

``verified`` on its own is not proof: the probe writes the status and the file
classification together, so a record carrying the word alone is treated as
unclassified, never queued, and never silently re-probed behind the user's
back.  Each case here drives the real ``download_mirror`` with a recorder for
the manager and a stub for ``kryoto.check_link``, so the assertions are about
what was queued and what was probed.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402

from lgl import kryoto, qt_catalog_links  # noqa: E402
from lgl.catalog_store import CatalogStore, MirrorRecord  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class Recorder:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def add(self, **kwargs) -> None:
        self.calls.append(kwargs)


def _store(tmp_path) -> CatalogStore:
    store = CatalogStore(tmp_path / "catalog.json")
    store.save()
    return store


def _record(**fields) -> MirrorRecord:
    base = dict(
        source_id="kryoto", game_key="kryoto:w-w-d-d",
        url="https://kryo.to/dl/WWDD-setup.zip", filename="WWDD-setup.zip",
        host="kryo.to", label="Main mirror",
    )
    base.update(fields)
    return MirrorRecord(**base)


class Probe:
    """Counts the probes and answers with whatever the test asked for."""

    def __init__(self, kind: str = "file", status: str = "verified") -> None:
        self.urls: list[str] = []
        self.kind = kind
        self.status = status

    def __call__(self, url: str, allow_external: bool = True):
        self.urls.append(url)
        return kryoto.LinkCheck(
            url=url, status=self.status, kind=self.kind,
            content_type="application/zip", filename="WWDD-setup.zip", size=4096,
        )


def _download(app, tmp_path, monkeypatch, record, answer=True, probe=None):
    store = _store(tmp_path)
    manager = Recorder()
    messages: list[tuple[str, bool]] = []
    asked: list[str] = []
    monkeypatch.setattr(
        qt_catalog_links, "confirm_download",
        lambda parent, rec, folder: asked.append(rec.filename) or answer,
    )
    probe = probe or Probe()
    monkeypatch.setattr(kryoto, "check_link", probe)

    qt_catalog_links.download_mirror(
        None, store, manager, tmp_path, "catalog-kryoto-w-w-d-d", record,
        lambda text, error=False: messages.append((text, error)),
    )
    for _ in range(400):
        app.processEvents()
        if manager.calls or any("Check" in text or "verified" in text for text, _ in messages):
            break
    # A probe answers on a worker thread; give it a moment to land.
    import time

    end = time.time() + 2.0
    while time.time() < end and not manager.calls:
        app.processEvents()
        time.sleep(0.01)
    return manager, messages, asked, probe


def test_verified_and_file_is_downloadable_without_another_probe(app, tmp_path, monkeypatch):
    manager, messages, asked, probe = _download(
        app, tmp_path, monkeypatch, _record(check_status="verified", url_kind="file"),
    )

    assert asked == ["WWDD-setup.zip"]
    assert len(manager.calls) == 1
    assert manager.calls[0]["url"] == "https://kryo.to/dl/WWDD-setup.zip"
    assert probe.urls == []  # the stored proof is enough
    assert any(text.startswith("Queued ") for text, _error in messages)


def test_verified_and_page_is_never_queued(app, tmp_path, monkeypatch):
    manager, messages, asked, probe = _download(
        app, tmp_path, monkeypatch,
        _record(check_status="verified", url_kind="page"),
    )

    assert manager.calls == []
    assert asked == []
    assert probe.urls == []
    assert any(error for _text, error in messages)


def test_verified_with_no_kind_is_never_queued(app, tmp_path, monkeypatch):
    manager, messages, asked, probe = _download(
        app, tmp_path, monkeypatch, _record(check_status="verified", url_kind=""),
    )

    assert manager.calls == []
    assert asked == []
    assert probe.urls == []
    assert any("Press Check" in text for text, _error in messages)


def test_verified_with_a_candidate_kind_is_never_queued(app, tmp_path, monkeypatch):
    manager, messages, asked, probe = _download(
        app, tmp_path, monkeypatch,
        _record(check_status="verified", url_kind="file-candidate"),
    )

    assert manager.calls == []
    assert asked == []
    assert probe.urls == []
    assert any("Press Check" in text for text, _error in messages)


def test_an_unchecked_link_is_probed_first_and_then_queued(app, tmp_path, monkeypatch):
    manager, messages, asked, probe = _download(
        app, tmp_path, monkeypatch,
        _record(check_status="unchecked", url_kind="file-candidate"),
        probe=Probe(kind="file", status="verified"),
    )

    assert probe.urls == ["https://kryo.to/dl/WWDD-setup.zip"]
    assert asked == ["WWDD-setup.zip"]
    assert len(manager.calls) == 1
    assert any(text.startswith("Queued ") for text, _error in messages)


def test_an_unchecked_link_that_answers_with_a_page_is_not_queued(app, tmp_path, monkeypatch):
    manager, messages, asked, probe = _download(
        app, tmp_path, monkeypatch,
        _record(check_status="unchecked", url_kind="file-candidate"),
        probe=Probe(kind="page", status="page"),
    )

    assert probe.urls == ["https://kryo.to/dl/WWDD-setup.zip"]
    assert manager.calls == []
    assert asked == []
    assert any("web page" in text for text, _error in messages)
