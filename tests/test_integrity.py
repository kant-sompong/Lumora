"""Checksums, cancellation and the SHA-256 / legacy distinction."""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path

import pytest

from lgl.integrity import (
    IntegrityCancelled,
    IntegrityError,
    IntegrityResult,
    IntegrityStatus,
    expected_checksums,
    hash_file,
    is_verified,
    verify_file,
)

DATA = b"lumora test payload\n" * 500


@pytest.fixture()
def sample(tmp_path):
    path = tmp_path / "game.zip"
    path.write_bytes(DATA)
    return path


def test_expected_checksums_reads_mappings_and_objects():
    digest = hashlib.sha256(DATA).hexdigest()
    assert expected_checksums({"sha256": digest.upper(), "md5": ""}) == {
        "sha256": digest
    }

    class File:
        sha256 = digest
        sha1 = ""
        md5 = " abc123 "

    assert expected_checksums(File()) == {"sha256": digest, "md5": "abc123"}
    assert expected_checksums({}) == {}


def test_hash_file_matches_hashlib_for_every_algorithm(sample):
    digests = hash_file(sample, ("sha256", "sha1", "md5"), chunk_size=7)
    assert digests["sha256"] == hashlib.sha256(DATA).hexdigest()
    assert digests["sha1"] == hashlib.sha1(DATA).hexdigest()
    assert digests["md5"] == hashlib.md5(DATA).hexdigest()


def test_progress_starts_at_zero_and_ends_at_the_file_size(sample):
    seen: list[tuple[int, int]] = []
    hash_file(sample, ("sha256",), chunk_size=1024, progress=lambda d, t: seen.append((d, t)))
    assert seen[0] == (0, len(DATA))
    assert seen[-1] == (len(DATA), len(DATA))
    assert all(total == len(DATA) for _done, total in seen)


def test_cancellation_is_raised_not_swallowed(sample):
    event = threading.Event()
    event.set()
    with pytest.raises(IntegrityCancelled):
        hash_file(sample, ("sha256",), cancel_event=event)


def test_empty_file_hashes_without_special_cases(tmp_path):
    empty = tmp_path / "empty"
    empty.write_bytes(b"")
    assert hash_file(empty, ("sha256",)) == {
        "sha256": hashlib.sha256(b"").hexdigest()
    }


def test_unreadable_input_is_reported_as_an_error(tmp_path):
    with pytest.raises(IntegrityError):
        hash_file(tmp_path / "missing.bin", ("sha256",))
    with pytest.raises(IntegrityError):
        hash_file(tmp_path, ("sha256",))
    with pytest.raises(IntegrityError):
        hash_file(tmp_path / "missing.bin", ("sha256",))


def test_unsupported_algorithm_and_bad_chunk_size(sample):
    with pytest.raises(IntegrityError):
        hash_file(sample, ("not-a-hash",))
    with pytest.raises(IntegrityError):
        hash_file(sample, ("sha256",), chunk_size=0)


def test_sha256_match_is_authoritative(sample):
    digest = hashlib.sha256(DATA).hexdigest()
    result = verify_file(sample, {"sha256": digest})
    assert result.status == IntegrityStatus.VERIFIED
    assert result.authoritative and result.verified
    assert is_verified(result)
    assert result.bytes_hashed == len(DATA)


def test_sha256_mismatch_is_reported_not_hidden(sample):
    result = verify_file(sample, {"sha256": "0" * 64})
    assert result.status == IntegrityStatus.MISMATCH
    assert not result.verified and not result.authoritative
    assert "mismatch" in result.message.lower()


def test_legacy_digest_is_verified_but_never_called_sha256(sample):
    sha1 = hashlib.sha1(DATA).hexdigest()
    result = verify_file(sample, {"sha1": sha1})
    assert result.status == IntegrityStatus.VERIFIED_LEGACY
    assert result.verified and not result.authoritative
    assert "not SHA-256" in result.message

    md5 = hashlib.md5(DATA).hexdigest()
    both = verify_file(sample, {"sha1": sha1, "md5": md5})
    assert both.status == IntegrityStatus.VERIFIED_LEGACY


def test_a_bad_legacy_digest_fails(sample):
    result = verify_file(sample, {"sha1": "0" * 40})
    assert result.status == IntegrityStatus.MISMATCH


def test_no_published_digest_makes_no_claim(sample):
    result = verify_file(sample, {})
    assert result.status == IntegrityStatus.UNAVAILABLE
    assert not result.verified


def test_missing_and_non_regular_files_are_errors(tmp_path):
    missing = verify_file(tmp_path / "nope", {"sha256": "0" * 64})
    assert missing.status == IntegrityStatus.ERROR
    folder = verify_file(tmp_path, {"sha256": "0" * 64})
    assert folder.status == IntegrityStatus.ERROR


def test_verified_property_covers_both_kinds():
    assert IntegrityResult(IntegrityStatus.VERIFIED, algorithm="sha256").authoritative
    assert IntegrityResult(IntegrityStatus.VERIFIED_LEGACY, algorithm="sha1").verified
    assert not IntegrityResult(IntegrityStatus.VERIFIED_LEGACY, algorithm="sha1").authoritative
