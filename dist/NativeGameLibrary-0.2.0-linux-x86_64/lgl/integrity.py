"""File integrity checks for downloaded archive files.

The Internet Archive publishes an MD5 and a SHA-1 digest for essentially every
file in an item.  SHA-256 is only present when an uploader supplied it in the
item metadata, so it must not be assumed.  This module therefore distinguishes
between a real SHA-256 verification (:attr:`IntegrityStatus.VERIFIED`) and a
legacy check based on MD5/SHA-1 (:attr:`IntegrityStatus.VERIFIED_LEGACY`), and
never presents the latter as SHA-256 verification.

Everything here is read-only: files are opened for reading and never written,
renamed, or deleted.  Only the Python standard library is used.
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

__all__ = [
    "IntegrityStatus",
    "IntegrityResult",
    "IntegrityError",
    "IntegrityCancelled",
    "expected_checksums",
    "hash_file",
    "verify_file",
    "is_verified",
]

#: Preferred order when only legacy digests are available.
LEGACY_ALGORITHMS = ("sha1", "md5")

#: Human-readable names used in user-facing messages.
_ALGORITHM_LABELS = {"sha256": "SHA-256", "sha1": "SHA-1", "md5": "MD5"}

_EMIT_INTERVAL = 0.1  # progress is reported at most ~10 times per second


class IntegrityError(Exception):
    """A file could not be read for hashing."""


class IntegrityCancelled(Exception):
    """Hashing was cancelled through the caller's event."""


class IntegrityStatus(str, Enum):
    """Outcome of comparing a file against published checksums."""

    VERIFIED = "verified_sha256"
    VERIFIED_LEGACY = "verified_legacy"
    MISMATCH = "mismatch"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


@dataclass(frozen=True)
class IntegrityResult:
    """Outcome of verifying one file.

    ``authoritative`` is true only for a successful SHA-256 comparison.  A
    legacy MD5/SHA-1 match is useful but weaker and must not be described as
    SHA-256 verification.
    """

    status: IntegrityStatus
    algorithm: str | None = None
    expected: str | None = None
    actual: str | None = None
    message: str = ""
    bytes_hashed: int = 0

    @property
    def verified(self) -> bool:
        """True when the file matched a published digest (any algorithm)."""
        return self.status in (
            IntegrityStatus.VERIFIED,
            IntegrityStatus.VERIFIED_LEGACY,
        )

    @property
    def authoritative(self) -> bool:
        """True only when the file matched a published SHA-256 digest."""
        return (
            self.status == IntegrityStatus.VERIFIED
            and self.algorithm == "sha256"
        )


def is_verified(result: IntegrityResult) -> bool:
    """Convenience helper mirroring :attr:`IntegrityResult.verified`."""
    return bool(result.verified)


# --------------------------------------------------------------------------
# Published checksums
# --------------------------------------------------------------------------

def _read_field(source: Any, name: str) -> str:
    """Read one checksum field from a mapping or an attribute-style object."""
    value: Any = None
    if isinstance(source, Mapping):
        value = source.get(name)
    else:
        value = getattr(source, name, None)
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return str(value)


def expected_checksums(archive_file: Any) -> dict[str, str]:
    """Return the checksums published for *archive_file*, keyed by algorithm.

    ``archive_file`` may be a mapping or any object with ``sha256``/``sha1``/
    ``md5`` attributes (archive.org metadata usually has SHA-1 and MD5, and
    only occasionally SHA-256).  Digests are stripped and lowercased; empty
    values are omitted, so the result can be tested with ``in``.
    """
    checksums: dict[str, str] = {}
    for algorithm in ("sha256", "sha1", "md5"):
        value = _read_field(archive_file, algorithm).strip().lower()
        if value:
            checksums[algorithm] = value
    return checksums


# --------------------------------------------------------------------------
# Hashing
# --------------------------------------------------------------------------

def hash_file(
    path: Path | str,
    algorithms: Iterable[str] = ("sha256",),
    chunk_size: int = 1024 * 1024,
    progress: Callable[[int, int], None] | None = None,
    cancel_event: Any = None,
) -> dict[str, str]:
    """Hash *path* once and return a digest for every requested algorithm.

    A single pass reads the file in ``chunk_size`` blocks so large archives are
    not loaded into memory.  ``progress(done, total)`` is called at most about
    ten times per second, always ends with ``done == total``, and ``total``
    comes from ``os.stat``.

    If ``cancel_event`` (a :class:`threading.Event`) is set, hashing stops and
    :class:`IntegrityCancelled` is raised.  Unreadable or missing files raise
    :class:`IntegrityError`.  The file is only ever opened for reading.
    """
    if cancel_event is not None and cancel_event.is_set():
        raise IntegrityCancelled("Hashing was cancelled.")

    names: list[str] = []
    hashers: dict[str, Any] = {}
    for algorithm in algorithms:
        name = str(algorithm).strip().lower()
        if not name or name in hashers:
            continue
        try:
            hashers[name] = hashlib.new(name)
        except (ValueError, TypeError) as exc:
            raise IntegrityError(f"Unsupported hash algorithm: {algorithm!r}") from exc
        names.append(name)
    if not names:
        raise IntegrityError("No hash algorithms were requested.")

    if chunk_size <= 0:
        raise IntegrityError("chunk_size must be positive.")

    target = Path(path)
    try:
        total = os.stat(target).st_size
    except OSError as exc:
        raise IntegrityError(f"Could not read file: {target} ({exc.strerror or exc})") from exc

    last_done = -1
    last_emit = 0.0

    def emit(done: int) -> None:
        nonlocal last_done, last_emit
        if progress is None or done == last_done:
            return
        now = time.monotonic()
        if done == total or now - last_emit >= _EMIT_INTERVAL:
            last_done = done
            last_emit = now
            progress(done, total)

    emit(0)
    done = 0
    try:
        with open(target, "rb") as handle:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise IntegrityCancelled("Hashing was cancelled.")
                block = handle.read(chunk_size)
                if not block:
                    break
                done += len(block)
                for hasher in hashers.values():
                    hasher.update(block)
                emit(done)
    except IntegrityCancelled:
        raise
    except OSError as exc:
        raise IntegrityError(f"Could not read file: {target} ({exc.strerror or exc})") from exc

    # Always report the final state, even for an empty file or a very fast run.
    emit(done)
    return {name: hashers[name].hexdigest() for name in names}


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------

def _error_result(message: str) -> IntegrityResult:
    return IntegrityResult(status=IntegrityStatus.ERROR, message=message)


def verify_file(
    path: Path | str,
    archive_file: Any,
    progress: Callable[[int, int], None] | None = None,
    cancel_event: Any = None,
) -> IntegrityResult:
    """Compare a local file with the checksums published for *archive_file*.

    * With a published SHA-256 digest the file is reported ``VERIFIED`` or
      ``MISMATCH`` and the result is authoritative.
    * With only SHA-1/MD5 (the common case on archive.org) the matching
      digests are checked and a match yields ``VERIFIED_LEGACY``; the message
      states which algorithm was used and that it is not SHA-256.
    * With no published digest the result is ``UNAVAILABLE``; no claim of
      integrity is made.
    * A missing or unreadable file yields ``ERROR``.

    Cancellation raises :class:`IntegrityCancelled` instead of returning a
    result.  The file is never modified.
    """
    target = Path(path)
    try:
        info = os.stat(target)
    except OSError as exc:
        return _error_result(f"File is missing or unreadable: {target} ({exc.strerror or exc})")
    if not os.path.isfile(target):
        return _error_result(f"Not a regular file: {target}")

    checksums = expected_checksums(archive_file)
    total = info.st_size

    if "sha256" in checksums:
        expected = checksums["sha256"]
        try:
            actual = hash_file(
                target,
                ("sha256",),
                progress=progress,
                cancel_event=cancel_event,
            )["sha256"]
        except IntegrityCancelled:
            raise
        except IntegrityError as exc:
            return _error_result(str(exc))
        if actual == expected:
            return IntegrityResult(
                status=IntegrityStatus.VERIFIED,
                algorithm="sha256",
                expected=expected,
                actual=actual,
                message="SHA-256 checksum verified.",
                bytes_hashed=total,
            )
        return IntegrityResult(
            status=IntegrityStatus.MISMATCH,
            algorithm="sha256",
            expected=expected,
            actual=actual,
            message=f"SHA-256 mismatch: expected {expected}, got {actual}.",
            bytes_hashed=total,
        )

    legacy = [name for name in LEGACY_ALGORITHMS if name in checksums]
    if not legacy:
        return IntegrityResult(
            status=IntegrityStatus.UNAVAILABLE,
            message="No authoritative checksum is published for this file.",
        )

    try:
        computed = hash_file(
            target,
            legacy,
            progress=progress,
            cancel_event=cancel_event,
        )
    except IntegrityCancelled:
        raise
    except IntegrityError as exc:
        return _error_result(str(exc))

    labels = " and ".join(_ALGORITHM_LABELS[name] for name in legacy)
    for name in legacy:
        actual = computed[name]
        if actual != checksums[name]:
            label = _ALGORITHM_LABELS[name]
            return IntegrityResult(
                status=IntegrityStatus.MISMATCH,
                algorithm=name,
                expected=checksums[name],
                actual=actual,
                message=(
                    f"{label} mismatch: expected {checksums[name]}, got {actual}. "
                    "This is not a SHA-256 comparison."
                ),
                bytes_hashed=total,
            )

    # Prefer SHA-1 in the algorithm field; the message names everything checked.
    algorithm = legacy[0]
    return IntegrityResult(
        status=IntegrityStatus.VERIFIED_LEGACY,
        algorithm=algorithm,
        expected=checksums[algorithm],
        actual=computed[algorithm],
        message=(
            f"{labels} checksum matched, but this is not SHA-256 verification. "
            "Archive.org normally publishes only MD5 and SHA-1 digests."
        ),
        bytes_hashed=total,
    )
