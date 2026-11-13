"""Legacy mirror records are normalised once, in the shared catalogue store.

``verified`` means "a probe saw a file response" and is always stored beside
``url_kind == "file"``.  Catalogues written by older builds, or imported with a
status and no kind, carry the word alone.  The rule lives in
``MirrorRecord.from_dict``, which is the only reader used by ``CatalogStore.load``
— so the Qt and GTK frontends both receive the same normalised records and
neither needs its own copy of the rule.

The store-level tests below therefore go through a real file on disk: they are
the frontend-independent proof.
"""

from __future__ import annotations

import json

from lgl.catalog_store import CatalogStore, MirrorRecord

GAME_KEY = "kryoto:w-w-d-d"
URL = "https://kryo.to/dl/WWDD-setup.zip"


def _raw(**fields) -> dict:
    base = {
        "source_id": "kryoto",
        "game_key": GAME_KEY,
        "url": URL,
        "filename": "WWDD-setup.zip",
    }
    base.update(fields)
    return base


def _load(tmp_path, mirrors: list[dict]) -> CatalogStore:
    """A catalogue file as an older build (or a scraper import) left it."""
    path = tmp_path / "catalog.json"
    path.write_text(
        json.dumps(
            {
                "games": [
                    {"source_id": "kryoto", "game_id": "w-w-d-d", "title": "W.W.D.D."}
                ],
                "mirrors": mirrors,
            }
        ),
        encoding="utf-8",
    )
    return CatalogStore(path)


def _by_name(store: CatalogStore) -> dict[str, MirrorRecord]:
    return {record.filename: record for record in store.mirrors_for(GAME_KEY)}


# -- the record reader ------------------------------------------------------


def test_verified_with_the_file_kind_survives_a_round_trip():
    record = MirrorRecord.from_dict(_raw(check_status="verified", url_kind="file"))

    assert record is not None
    assert record.check_status == "verified"
    assert record.url_kind == "file"


def test_verified_without_a_kind_loads_as_unchecked():
    record = MirrorRecord.from_dict(_raw(check_status="verified"))

    assert record is not None
    assert record.check_status == "unchecked"
    assert record.url_kind == ""


def test_verified_with_a_page_kind_loads_as_unchecked():
    record = MirrorRecord.from_dict(_raw(check_status="verified", url_kind="page"))

    assert record is not None
    assert record.check_status == "unchecked"


def test_verified_with_a_candidate_kind_loads_as_unchecked():
    record = MirrorRecord.from_dict(
        _raw(check_status="verified", url_kind="file-candidate")
    )

    assert record is not None
    assert record.check_status == "unchecked"
    assert record.url_kind == "file-candidate"


def test_the_other_statuses_are_kept_as_stored():
    for status in ("unchecked", "page", "external", "blocked", "failed"):
        record = MirrorRecord.from_dict(_raw(check_status=status))
        assert record is not None
        assert record.check_status == status


def test_an_unknown_status_still_falls_back_to_unchecked():
    record = MirrorRecord.from_dict(_raw(check_status="banana", url_kind="file"))

    assert record is not None
    assert record.check_status == "unchecked"


# -- the shared store, which is what both frontends read --------------------


def test_the_store_normalises_incoherent_records_on_load(tmp_path):
    store = _load(
        tmp_path,
        [
            _raw(filename="file.zip", check_status="verified", url_kind="file"),
            _raw(
                filename="page.zip", url="https://kryo.to/dl/page.zip",
                check_status="verified", url_kind="page",
            ),
            _raw(
                filename="bare.zip", url="https://kryo.to/dl/bare.zip",
                check_status="verified",
            ),
            _raw(
                filename="candidate.zip", url="https://kryo.to/dl/candidate.zip",
                check_status="verified", url_kind="file-candidate",
            ),
        ],
    )

    records = _by_name(store)

    assert records["file.zip"].check_status == "verified"
    assert records["file.zip"].url_kind == "file"
    assert records["page.zip"].check_status == "unchecked"
    assert records["bare.zip"].check_status == "unchecked"
    assert records["candidate.zip"].check_status == "unchecked"


def test_the_store_keeps_every_other_status_untouched(tmp_path):
    statuses = ("unchecked", "page", "external", "blocked", "failed", "missing")
    store = _load(
        tmp_path,
        [
            _raw(
                filename=f"{status}.zip",
                url=f"https://kryo.to/dl/{status}.zip",
                check_status=status,
            )
            for status in statuses
        ],
    )

    records = _by_name(store)

    for status in statuses:
        assert records[f"{status}.zip"].check_status == status


def test_a_saved_and_reloaded_catalogue_is_stable(tmp_path):
    store = _load(
        tmp_path,
        [
            _raw(check_status="verified", url_kind="file"),
            _raw(
                filename="page.zip", url="https://kryo.to/dl/page.zip",
                check_status="page",
            ),
        ],
    )
    assert store.save() is True

    reloaded = CatalogStore(tmp_path / "catalog.json")
    records = _by_name(reloaded)

    assert records["WWDD-setup.zip"].check_status == "verified"
    assert records["WWDD-setup.zip"].url_kind == "file"
    assert records["page.zip"].check_status == "page"
