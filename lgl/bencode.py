"""Bencode (BEP 3) encode/decode.

Bencode is the serialization format of the BitTorrent protocol: torrent
metainfo files, tracker replies, DHT messages and peer wire payloads are all
bencoded.  The decoder here is total and bounds-checked - a hostile or
truncated payload raises :class:`BencodeError` instead of consuming memory or
returning a half-parsed structure.

Two details matter for correctness and are easy to get wrong:

* Dictionary keys are **byte strings**, and keys that are not bytes are refused
  on encode.  Re-encoding an ``info`` dictionary with rewritten keys changes
  the infohash, which is a SHA-1 over those exact bytes.
* Decoding keeps offsets available through :func:`decode`, so the original
  bytes of the ``info`` dictionary can be hashed without re-serializing them.
"""

from __future__ import annotations

from typing import Any

#: Deepest nesting accepted.  Real torrents nest a handful of levels; the limit
#: only exists so a malicious payload cannot exhaust the Python stack.
MAX_DEPTH = 64
#: Longest byte string accepted (1 MiB).  ``pieces`` is a string of 20-byte
#: hashes and stays far below this even for very large torrents.
MAX_STRING = 1024 * 1024


class BencodeError(ValueError):
    """A bencoded payload could not be encoded or decoded."""


# --------------------------------------------------------------------------
# Encoding
# --------------------------------------------------------------------------


def encode(value: Any) -> bytes:
    """Serialize *value* to bencode bytes.

    Supports ``bytes``, ``str`` (encoded as UTF-8), ``int``, ``bool`` as 0/1
    (never as the strings ``true``/``false``), ``list``/``tuple`` and ``dict``
    with ``bytes`` or ``str`` keys.  Anything else raises :class:`BencodeError`
    rather than silently stringifying - a torrent built from wrong types is
    rejected by every client.
    """
    out: list[bytes] = []
    _encode_into(value, out, 0)
    return b"".join(out)


def _encode_into(value: Any, out: list[bytes], depth: int) -> None:
    if depth > MAX_DEPTH:
        raise BencodeError("Value nests too deeply to encode.")
    if isinstance(value, bool):
        out.append(b"i1e" if value else b"i0e")
    elif isinstance(value, int):
        out.append(b"i" + str(int(value)).encode("ascii") + b"e")
    elif isinstance(value, bytes):
        out.append(b"%d:" % len(value))
        out.append(value)
    elif isinstance(value, bytearray):
        _encode_into(bytes(value), out, depth)
    elif isinstance(value, str):
        _encode_into(value.encode("utf-8"), out, depth)
    elif isinstance(value, (list, tuple)):
        out.append(b"l")
        for item in value:
            _encode_into(item, out, depth + 1)
        out.append(b"e")
    elif isinstance(value, dict):
        out.append(b"d")
        items = []
        for key, item in value.items():
            if isinstance(key, str):
                raw = key.encode("utf-8")
            elif isinstance(key, bytes):
                raw = key
            else:
                raise BencodeError("Dictionary keys must be bytes or text.")
            items.append((raw, item))
        # Bencode requires keys in lexicographic byte order.
        for raw, item in sorted(items, key=lambda pair: pair[0]):
            out.append(b"%d:" % len(raw))
            out.append(raw)
            _encode_into(item, out, depth + 1)
        out.append(b"e")
    else:
        raise BencodeError(f"Cannot bencode {type(value).__name__}.")


# --------------------------------------------------------------------------
# Decoding
# --------------------------------------------------------------------------


def decode(data: bytes, start: int = 0) -> tuple[Any, int]:
    """Decode one value at *start*; return ``(value, next_offset)``."""
    if not isinstance(data, (bytes, bytearray)):
        raise BencodeError("Bencode input must be bytes.")
    return _decode(bytes(data), start, 0)


def _decode(data: bytes, index: int, depth: int) -> tuple[Any, int]:
    if depth > MAX_DEPTH:
        raise BencodeError("Value nests too deeply to decode.")
    if index >= len(data):
        raise BencodeError("Bencode input ended unexpectedly.")
    marker = data[index : index + 1]

    if marker == b"i":
        end = data.find(b"e", index + 1)
        if end < 0:
            raise BencodeError("Unterminated integer.")
        body = data[index + 1 : end]
        if not body or body in (b"-0", b"00") or (body[:1] == b"0" and len(body) > 1):
            raise BencodeError("Malformed integer.")
        if body[:1] == b"-" and (len(body) == 1 or body[1:2] == b"0"):
            raise BencodeError("Malformed negative integer.")
        try:
            return int(body), end + 1
        except ValueError as exc:
            raise BencodeError("Malformed integer.") from exc

    if marker == b"l":
        items: list[Any] = []
        index += 1
        while True:
            if index >= len(data):
                raise BencodeError("Unterminated list.")
            if data[index : index + 1] == b"e":
                return items, index + 1
            item, index = _decode(data, index, depth + 1)
            items.append(item)

    if marker == b"d":
        result: dict[bytes, Any] = {}
        index += 1
        while True:
            if index >= len(data):
                raise BencodeError("Unterminated dictionary.")
            if data[index : index + 1] == b"e":
                return result, index + 1
            if not data[index : index + 1].isdigit():
                raise BencodeError("Dictionary key is not a byte string.")
            key, index = _decode(data, index, depth + 1)
            if not isinstance(key, bytes):
                raise BencodeError("Dictionary key is not a byte string.")
            if key in result:
                raise BencodeError("Duplicate dictionary key.")
            value, index = _decode(data, index, depth + 1)
            result[key] = value

    if marker.isdigit():
        colon = data.find(b":", index)
        if colon < 0:
            raise BencodeError("Byte string has no length prefix.")
        length_text = data[index:colon]
        if not length_text.isdigit() or (length_text[:1] == b"0" and len(length_text) > 1):
            raise BencodeError("Malformed byte string length.")
        length = int(length_text)
        if length > MAX_STRING:
            raise BencodeError("Byte string is unreasonably long.")
        start = colon + 1
        end = start + length
        if end > len(data):
            raise BencodeError("Byte string runs past the end of the input.")
        return data[start:end], end

    raise BencodeError(f"Unexpected byte {marker!r} in bencode input.")


def decode_all(data: bytes) -> Any:
    """Decode exactly one value and require that it consumes the input."""
    value, offset = decode(data, 0)
    if offset != len(data):
        raise BencodeError("Trailing bytes after the bencoded value.")
    return value


def text(value: Any, default: str = "") -> str:
    """Decode a bencoded text field, tolerating bad UTF-8."""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, str):
        return value
    return default


def integer(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    return default


__all__ = [
    "BencodeError",
    "MAX_DEPTH",
    "MAX_STRING",
    "decode",
    "decode_all",
    "encode",
    "integer",
    "text",
]
