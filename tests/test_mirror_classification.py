"""Stored mirror records: a status is only kept with its classification.

``verified`` means "a probe saw a file response" and is always stored beside
``url_kind == "file"``.  Catalogues written by older builds, or imported with a
status and no kind, carry the word alone; those load as ``unchecked`` so the
next check re-establishes both fields rather than the download manager being
handed a link nobody proved.
"""

from __future__ import annotations

from lgl.catalog_store import MirrorRecord


def _raw(**fields) -> dict:
    base = {
        "source_id": "kryoto",
        "game_key": "kryoto:w-w-d-d",
        "url": "https://kryo.to/dl/WWDD-setup.zip",
        "filename": "WWDD-setup.zip",
    }
    base.update(fields)
    return base


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
