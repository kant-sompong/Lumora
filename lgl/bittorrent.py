"""A BitTorrent client that ships inside the application.

Magnet downloads used to require an external ``aria2c`` binary.  This module
implements the same job in-process, so a packaged build works with nothing else
installed: BitTorrent v1 magnet links (BEP 9 metadata exchange), HTTP and UDP
trackers (BEP 3 / BEP 15), the mainline DHT (BEP 5), peer exchange (BEP 11),
and the peer wire protocol (BEP 3) with SHA-1 piece verification.

What it deliberately does **not** do:

* It does not upload.  There is no seeding, no upload slot, and no
  ``announce_peer`` on the DHT.  Requests from peers are left choked.
* It does not accept ``.torrent`` files, v2 (``btmh``) torrents, web seeds,
  ``magnet`` links whose ``xt`` is not ``urn:btih``, or UDP peer transports
  (uTP).  Those are reported as unsupported rather than half-working.
* It does not verify licensing, availability, or legality.  A completed
  download only proves the pieces matched the infohash the user supplied.

The public surface is :class:`NativeEngine`, whose ``client`` attribute exposes
an aria2-shaped ``add_uri``/``tell``/``pause``/``unpause``/``remove`` API so the
queue in :mod:`lgl.torrent` drives either engine without special cases.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import os
import random
import socket
import struct
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import parse_qs, quote, unquote_to_bytes, urlsplit

from . import bencode as bencodelib
from . import magnet as magnetlib

#: Largest block a peer may be asked for, and the size every client uses.
BLOCK_LENGTH = 16 * 1024
#: Peer connections per torrent.  Measured on a 756 MiB Debian torrent: 80
#: connections averaged 2.9 MiB/s, 200 averaged 8.5 MiB/s, and 300 gained
#: nothing further, so the useful limit is the swarm, not this number.
MAX_PEERS = 200
#: Pieces held in memory at once, and the byte cap that overrides it.  A piece
#: of a large torrent is often 4 MiB, so the byte cap keeps a wide swarm from
#: holding a gigabyte of fragments: 4 MiB pieces give 24 pieces (96 MiB), while
#: 256 KiB pieces give the full 160.
MAX_INFLIGHT_PIECES = 160
MAX_BUFFERED_BYTES = 96 * 1024 * 1024
#: Blocks requested ahead of arrivals on one connection (64 x 16 KiB = 1 MiB).
REQUEST_PIPELINE = 64
#: How long a block request may go unanswered before the block is requested
#: again.  Peers choke, stall, and silently drop requests; without this the
#: last few pieces of a torrent stay assigned to sockets that never answer.
BLOCK_TIMEOUT = 20.0
#: How long a peer may own a piece without delivering a single block before
#: another peer is allowed to take that piece over.  Lossless: the blocks that
#: did arrive are kept and only the missing ones are requested again.
STEAL_AFTER = 30.0
CONNECT_TIMEOUT = 12.0
HANDSHAKE_TIMEOUT = 20.0
#: Between tracker announces.  Trackers ask for 30 minutes; shorter only costs
#: us requests.
ANNOUNCE_INTERVAL = 1800.0
#: Discovered peers tried before a torrent is considered unseeded.
MAX_CANDIDATES = 2000
METADATA_PIECE_SIZE = 16 * 1024
#: Socket listen range, matching what the README documents for forwarding.
DEFAULT_PORTS = (6881, 6889)

#: Well-known DHT bootstrap nodes.  These are not trackers and hold no content;
#: they only answer "who is near this infohash".
DHT_BOOTSTRAP = (
    ("router.bittorrent.com", 6881),
    ("dht.transmissionbt.com", 6881),
    ("router.utorrent.com", 6881),
    ("dht.libtorrent.org", 25401),
)

#: Reported in the UI as the engine version.  It tracks the module's own
#: features, not aria2's, so the two backends are never confused.
__version__ = "1.0"

_UT_METADATA = 1
_UT_PEX = 2
_PROTOCOL = b"\x13BitTorrent protocol"
_EXTENSION_BIT = 0x10


#: All protocol activity is logged at DEBUG.  Nothing is written unless the
#: application configures logging, so a normal run stays silent.
log = logging.getLogger(__name__)


class BitTorrentError(Exception):
    """A torrent could not be parsed, transferred, or completed."""


# --------------------------------------------------------------------------
# Metainfo
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TorrentFile:
    """One file inside a torrent, placed at ``offset`` in the logical stream."""

    path: tuple[str, ...]
    length: int
    offset: int

    @property
    def relative(self) -> Path:
        return Path(*self.path) if self.path else Path("unnamed")


@dataclass
class TorrentMeta:
    """The decoded ``info`` dictionary of a BitTorrent v1 torrent."""

    infohash: str
    name: str
    piece_length: int
    pieces: tuple[bytes, ...]
    files: tuple[TorrentFile, ...]
    total_length: int
    raw_info: bytes = b""

    @property
    def piece_count(self) -> int:
        return len(self.pieces)

    def piece_length_at(self, index: int) -> int:
        if index < 0 or index >= self.piece_count:
            raise IndexError("Piece index out of range.")
        start = index * self.piece_length
        return min(self.piece_length, self.total_length - start)

    @classmethod
    def from_info_bytes(cls, raw_info: bytes) -> "TorrentMeta":
        """Build metadata from the exact bencoded ``info`` bytes.

        The infohash is the SHA-1 of these bytes, never of a re-encoding.
        """
        try:
            info = bencodelib.decode(bytes(raw_info), 0)[0]
        except bencodelib.BencodeError as exc:
            raise BitTorrentError(f"The torrent metadata is not valid bencode: {exc}") from exc
        if not isinstance(info, Mapping):
            raise BitTorrentError("The torrent metadata is not a dictionary.")
        return cls._from_mapping(info, bytes(raw_info))

    @classmethod
    def from_torrent_bytes(cls, data: bytes) -> "TorrentMeta":
        """Build metadata from a full ``.torrent`` file (top-level ``info``)."""
        try:
            document = bencodelib.decode_all(bytes(data))
        except bencodelib.BencodeError as exc:
            raise BitTorrentError(f"That is not a valid .torrent file: {exc}") from exc
        if not isinstance(document, Mapping) or b"info" not in document:
            raise BitTorrentError("That .torrent file has no info dictionary.")
        span = _info_span(bytes(data))
        if span is None:
            raise BitTorrentError("The info dictionary could not be located.")
        return cls.from_info_bytes(bytes(data)[span[0] : span[1]])

    @classmethod
    def _from_mapping(cls, info: Mapping[bytes, Any], raw_info: bytes) -> "TorrentMeta":
        name = bencodelib.text(info.get(b"name.utf-8") or info.get(b"name"), "")
        piece_length = bencodelib.integer(info.get(b"piece length"))
        pieces_blob = info.get(b"pieces")
        if piece_length <= 0:
            raise BitTorrentError("The torrent declares no piece length.")
        if not isinstance(pieces_blob, bytes) or not pieces_blob:
            raise BitTorrentError("The torrent carries no piece hashes.")
        if len(pieces_blob) % 20:
            raise BitTorrentError("The torrent's piece hash list is truncated.")
        pieces = tuple(
            pieces_blob[i : i + 20] for i in range(0, len(pieces_blob), 20)
        )

        files: list[TorrentFile] = []
        offset = 0
        entries = info.get(b"files")
        if isinstance(entries, list) and entries:
            for entry in entries:
                if not isinstance(entry, Mapping):
                    raise BitTorrentError("A torrent file entry is malformed.")
                length = bencodelib.integer(entry.get(b"length"))
                if length < 0:
                    raise BitTorrentError("A torrent file declares a negative length.")
                raw_path = entry.get(b"path.utf-8") or entry.get(b"path") or []
                parts = [
                    bencodelib.text(part, "")
                    for part in raw_path
                    if isinstance(part, bytes) and part not in (b"", b".", b"..")
                ]
                if not parts:
                    raise BitTorrentError("A torrent file has no usable path.")
                files.append(
                    TorrentFile(path=tuple(parts), length=length, offset=offset)
                )
                offset += length
        else:
            length = bencodelib.integer(info.get(b"length"))
            if length <= 0:
                raise BitTorrentError("The torrent declares no content.")
            files.append(
                TorrentFile(
                    path=(name or "download",), length=length, offset=0
                )
            )
            offset = length

        if not files:
            raise BitTorrentError("The torrent lists no files.")
        expected = math.ceil(offset / piece_length) if offset else 0
        if expected != len(pieces):
            raise BitTorrentError(
                "The torrent's piece count does not match its declared size."
            )
        return cls(
            infohash=hashlib.sha1(raw_info).hexdigest(),
            name=name or files[0].path[0],
            piece_length=piece_length,
            pieces=pieces,
            files=tuple(files),
            total_length=offset,
            raw_info=raw_info,
        )


def _info_span(data: bytes) -> tuple[int, int] | None:
    """Return the byte range of the top-level ``info`` value."""
    if not data[:1] == b"d":
        return None
    index = 1
    try:
        while index < len(data) and data[index : index + 1] != b"e":
            key, index = bencodelib.decode(data, index)
            start = index
            _value, index = bencodelib.decode(data, index)
            if key == b"info":
                return start, index
    except bencodelib.BencodeError:
        return None
    return None


def _compact_peers(blob: bytes) -> list[tuple[str, int]]:
    """Decode a compact peer list (4-byte address + 2-byte port per entry)."""
    peers: list[tuple[str, int]] = []
    if not isinstance(blob, bytes):
        return peers
    for offset in range(0, len(blob) - 5, 6):
        address = blob[offset : offset + 4]
        port = struct.unpack("!H", blob[offset + 4 : offset + 6])[0]
        if not port or address == b"\x00\x00\x00\x00":
            continue
        peers.append((socket.inet_ntoa(address), port))
    return peers


def _compact_peers6(blob: bytes) -> list[tuple[str, int]]:
    peers: list[tuple[str, int]] = []
    if not isinstance(blob, bytes):
        return peers
    for offset in range(0, len(blob) - 17, 18):
        address = blob[offset : offset + 16]
        port = struct.unpack("!H", blob[offset + 16 : offset + 18])[0]
        if not port:
            continue
        peers.append((socket.inet_ntop(socket.AF_INET6, address), port))
    return peers


def _peer_id() -> bytes:
    """A fresh peer id with the conventional ``-LG0001-`` client prefix."""
    alphabet = b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    suffix = bytes(random.choice(alphabet) for _ in range(12))
    return b"-LG0001-" + suffix


# --------------------------------------------------------------------------
# Trackers
# --------------------------------------------------------------------------


def _announce_query(
    infohash: bytes,
    peer_id: bytes,
    port: int,
    *,
    left: int,
    downloaded: int,
    uploaded: int,
    event: str = "",
) -> str:
    parts = [
        "info_hash=" + quote(infohash, safe=""),
        "peer_id=" + quote(peer_id, safe=""),
        f"port={port}",
        f"uploaded={uploaded}",
        f"downloaded={downloaded}",
        f"left={left}",
        "compact=1",
        "numwant=200",
        "key=" + hashlib.sha1(peer_id).hexdigest()[:8],
    ]
    if event:
        parts.append("event=" + event)
    return "&".join(parts)


def _http_announce(url: str, query: str, timeout: float = 12.0) -> list[tuple[str, int]]:
    """One blocking HTTP(S) announce.  Runs in a worker thread."""
    import urllib.error
    import urllib.request

    separator = "&" if "?" in url else "?"
    request = urllib.request.Request(
        url + separator + query,
        headers={"User-Agent": "Lumora/1.0", "Accept": "*/*"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                return []
            body = response.read(4 * 1024 * 1024)
    except (urllib.error.URLError, OSError, ValueError):
        return []
    try:
        document = bencodelib.decode(body, 0)[0]
    except bencodelib.BencodeError:
        return []
    if not isinstance(document, Mapping):
        return []
    peers: list[tuple[str, int]] = []
    compact = document.get(b"peers")
    if isinstance(compact, bytes):
        peers.extend(_compact_peers(compact))
    elif isinstance(compact, list):
        for entry in compact:
            if not isinstance(entry, Mapping):
                continue
            host = bencodelib.text(entry.get(b"ip"), "")
            port = bencodelib.integer(entry.get(b"port"))
            if host and 0 < port < 65536:
                peers.append((host, port))
    peers.extend(_compact_peers6(document.get(b"peers6") or b""))
    return peers


def _udp_announce(url: str, query: str, timeout: float = 12.0) -> list[tuple[str, int]]:
    """One blocking UDP announce (BEP 15).  Runs in a worker thread."""
    parsed = urlsplit(url)
    host = parsed.hostname
    if not host:
        return []
    port = parsed.port or 80
    params = parse_qs(query, keep_blank_values=True)
    # The announce query percent-encodes both 20-byte identifiers, so they are
    # read back as raw bytes rather than as text.
    info_hash = unquote_to_bytes(params.get("info_hash", [""])[0])
    peer_id = unquote_to_bytes(params.get("peer_id", [""])[0])
    if len(info_hash) != 20 or len(peer_id) != 20:
        return []

    def integer(name: str) -> int:
        try:
            return int(params.get(name, ["0"])[0])
        except (TypeError, ValueError):
            return 0

    transaction = random.randrange(0, 1 << 32)
    connection_id = 0x41727101980
    packet = struct.pack("!QII", connection_id, 0, transaction)
    sock = None
    try:
        family, socktype, proto, _canon, address = socket.getaddrinfo(
            host, port, socket.AF_INET, socket.SOCK_DGRAM
        )[0]
        sock = socket.socket(family, socktype, proto)
        sock.settimeout(timeout)
        sock.sendto(packet, address)
        reply, _ = sock.recvfrom(2048)
        if len(reply) < 16 or struct.unpack("!I", reply[:4])[0] != 0:
            return []
        if struct.unpack("!I", reply[4:8])[0] != transaction:
            return []
        connection_id = struct.unpack("!Q", reply[8:16])[0]

        event = {"started": 2, "stopped": 3, "completed": 1}.get(
            params.get("event", [""])[0], 0
        )
        transaction = random.randrange(0, 1 << 32)
        packet = struct.pack(
            "!QII20s20sQQQIIIiH",
            connection_id,
            1,
            transaction,
            info_hash,
            peer_id,
            integer("downloaded"),
            integer("left"),
            integer("uploaded"),
            event,
            0,
            random.randrange(0, 1 << 32),
            -1,
            integer("port"),
        )
        sock.sendto(packet, address)
        reply, _ = sock.recvfrom(65535)
        if len(reply) < 20 or struct.unpack("!I", reply[:4])[0] != 1:
            return []
        if struct.unpack("!I", reply[4:8])[0] != transaction:
            return []
        return _compact_peers(reply[20:])
    except (OSError, struct.error, ValueError):
        return []
    finally:
        if sock is not None:
            sock.close()


async def announce(
    trackers: Iterable[str],
    infohash: bytes,
    peer_id: bytes,
    port: int,
    *,
    left: int = 0,
    downloaded: int = 0,
) -> list[tuple[str, int]]:
    """Ask every tracker for peers, tolerating individual failures."""
    query = _announce_query(
        infohash,
        peer_id,
        port,
        left=left,
        downloaded=downloaded,
        uploaded=0,
        event="started" if not downloaded else "",
    )
    loop = asyncio.get_running_loop()
    jobs = []
    for tracker in trackers:
        scheme = urlsplit(tracker).scheme.lower()
        if scheme in ("http", "https"):
            jobs.append(loop.run_in_executor(None, _http_announce, tracker, query))
        elif scheme == "udp":
            jobs.append(loop.run_in_executor(None, _udp_announce, tracker, query))
        # wss:// needs a websocket tracker implementation; skipped, not faked.
    if not jobs:
        return []
    results = await asyncio.gather(*jobs, return_exceptions=True)
    peers: list[tuple[str, int]] = []
    for result in results:
        if isinstance(result, list):
            peers.extend(result)
    return peers


# --------------------------------------------------------------------------
# DHT (BEP 5)
# --------------------------------------------------------------------------


class _DHTServer(asyncio.DatagramProtocol):
    def __init__(self, owner: "DHT") -> None:
        self.owner = owner
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: Any) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.owner.handle(data, (addr[0], addr[1]))

    def error_received(self, exc: Exception) -> None:  # pragma: no cover - OS level
        return


class DHT:
    """A minimal mainline DHT node used only for peer discovery.

    It answers ``ping``/``find_node``/``get_peers`` so other nodes keep it in
    their routing tables, and never sends ``announce_peer``: this client does
    not want to advertise downloads beyond the trackers that already know them.
    """

    def __init__(self, port: int = 0) -> None:
        self.my_id = os.urandom(20)
        self.port = port
        self.nodes: dict[tuple[str, int], bytes] = {}
        self.peers: dict[bytes, set[tuple[str, int]]] = {}
        self._transactions: dict[bytes, asyncio.Future] = {}
        self._tokens: dict[tuple[str, int], bytes] = {}
        self._transport: asyncio.DatagramTransport | None = None
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        loop = asyncio.get_running_loop()
        try:
            self._transport, _protocol = await loop.create_datagram_endpoint(
                lambda: _DHTServer(self), local_addr=("0.0.0.0", self.port)
            )
        except OSError:
            self._transport = None
            return
        self._started = True
        for host, port in DHT_BOOTSTRAP:
            try:
                infos = await loop.getaddrinfo(host, port, type=socket.SOCK_DGRAM)
            except OSError:
                continue
            for info in infos[:1]:
                self.nodes[(info[4][0], info[4][1])] = b""
        await self._bootstrap()

    def close(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        self._started = False

    # -- wire ---------------------------------------------------------------

    def _send(self, address: tuple[str, int], payload: Mapping[str, Any]) -> None:
        if self._transport is None:
            return
        try:
            self._transport.sendto(bencodelib.encode(payload), address)
        except (OSError, bencodelib.BencodeError):
            pass

    async def _query(
        self, address: tuple[str, int], name: str, args: dict[str, Any], timeout: float = 4.0
    ) -> Mapping[str, Any] | None:
        if self._transport is None:
            return None
        transaction = os.urandom(4)
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._transactions[transaction] = future
        self._send(
            address,
            {
                b"t": transaction,
                b"y": b"q",
                b"q": name.encode(),
                b"a": {k.encode(): v for k, v in args.items()},
            },
        )
        try:
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            self._transactions.pop(transaction, None)

    def handle(self, data: bytes, address: tuple[str, int]) -> None:
        try:
            message = bencodelib.decode(data, 0)[0]
        except bencodelib.BencodeError:
            return
        if not isinstance(message, Mapping):
            return
        kind = message.get(b"y")
        if kind == b"r":
            future = self._transactions.get(message.get(b"t") or b"")
            if future is not None and not future.done():
                result = message.get(b"r")
                future.set_result(result if isinstance(result, Mapping) else {})
            return
        if kind != b"q":
            return
        # Answer the queries other nodes send us.
        query = message.get(b"q") or b""
        transaction = message.get(b"t") or b""
        args = message.get(b"a") or {}
        if not isinstance(args, Mapping):
            args = {}
        reply: dict[bytes, Any] = {b"t": transaction, b"y": b"r", b"r": {b"id": self.my_id}}
        if query == b"ping":
            pass
        elif query in (b"find_node", b"get_peers"):
            if query == b"get_peers":
                target = args.get(b"info_hash")
                if isinstance(target, bytes):
                    token = os.urandom(8)
                    self._tokens[address] = token
                    reply[b"r"][b"token"] = token
                    known = self.peers.get(target)
                    if known:
                        compact = b"".join(
                            socket.inet_aton(host) + struct.pack("!H", port)
                            for host, port in list(known)[:50]
                            if ":" not in host
                        )
                        if compact:
                            reply[b"r"][b"values"] = compact
            reply[b"r"][b"nodes"] = self._compact_nodes(8)
        else:
            return
        self._send(address, reply)

    def _compact_nodes(self, limit: int) -> bytes:
        blob = b""
        for (host, port), node_id in list(self.nodes.items())[:limit]:
            if ":" in host or len(node_id) != 20:
                continue
            try:
                blob += node_id + socket.inet_aton(host) + struct.pack("!H", port)
            except OSError:
                continue
        return blob

    def _absorb(self, compact: Any) -> None:
        if not isinstance(compact, bytes):
            return
        for offset in range(0, len(compact) - 25, 26):
            node_id = compact[offset : offset + 20]
            host = socket.inet_ntoa(compact[offset + 20 : offset + 24])
            port = struct.unpack("!H", compact[offset + 24 : offset + 26])[0]
            if port and len(self.nodes) < 2000:
                self.nodes[(host, port)] = node_id

    async def _bootstrap(self) -> None:
        nodes = list(self.nodes)
        await asyncio.gather(
            *[
                self._query(address, "find_node", {"id": self.my_id, "target": self.my_id})
                for address in nodes[:8]
            ],
            return_exceptions=True,
        )

    async def get_peers(
        self, info_hash: bytes, *, timeout: float = 20.0, limit: int = 200
    ) -> set[tuple[str, int]]:
        """Iteratively walk the DHT towards *info_hash* and collect peers."""
        found: set[tuple[str, int]] = set()
        if self._transport is None:
            return found
        deadline = asyncio.get_running_loop().time() + timeout
        queried: set[tuple[str, int]] = set()
        while asyncio.get_running_loop().time() < deadline and len(found) < limit:
            batch = [node for node in self.nodes if node not in queried][:8]
            if not batch:
                break
            results = await asyncio.gather(
                *[
                    self._query(
                        address,
                        "get_peers",
                        {"id": self.my_id, "info_hash": info_hash},
                    )
                    for address in batch
                ],
                return_exceptions=True,
            )
            queried.update(batch)
            for result in results:
                if not isinstance(result, Mapping):
                    continue
                values = result.get(b"values")
                if isinstance(values, bytes):
                    found.update(_compact_peers(values))
                elif isinstance(values, list):
                    for entry in values:
                        if isinstance(entry, bytes):
                            found.update(_compact_peers(entry))
                self._absorb(result.get(b"nodes"))
                if len(found) >= limit:
                    break
        self.peers[info_hash] = set(found)
        return found


# --------------------------------------------------------------------------
# Piece scheduling
# --------------------------------------------------------------------------


class PieceScheduler:
    """Assigns verified pieces to peers and tracks outstanding blocks.

    One peer owns a piece at a time.  That keeps the in-memory fragment set
    small and makes a disconnect cheap to undo: the piece is simply released
    and another peer picks it up.  Duplicating a piece across peers would waste
    the very bandwidth the swarm is short of.

    Ownership is never permanent.  A block that is not delivered within
    :data:`BLOCK_TIMEOUT` becomes requestable again, and a piece whose owner
    stops delivering for :data:`STEAL_AFTER` may be taken over by another peer
    that has it.  Both matter on a real swarm: trackers hand out hundreds of
    peers that choke, go idle, or accept requests they never answer, and a
    torrent that keeps whatever it was first given never finishes.
    """

    def __init__(self, meta: TorrentMeta) -> None:
        self.meta = meta
        self.count = meta.piece_count
        self.done = bytearray(self.count)
        self.done_bytes = 0
        self.owners: dict[int, int] = {}
        self.fragments: dict[int, dict[int, bytes]] = {}
        #: Bytes received per piece, so progress does not need re-summing.
        self.received: dict[int, int] = {}
        #: ``(piece, begin) -> (peer_key, monotonic send time)``.
        self.outstanding: dict[tuple[int, int], tuple[int, float]] = {}
        #: When each owned piece last made progress, for takeover decisions.
        self.progress_at: dict[int, float] = {}
        #: How many pieces may be in flight, bounded by memory rather than count.
        self.max_pieces = max(
            4,
            min(
                MAX_INFLIGHT_PIECES,
                MAX_BUFFERED_BYTES // max(1, meta.piece_length),
            ),
        )
        #: Where the next search starts.  Rescanning thousands of finished
        #: pieces on every block request was the difference between 0.7 and
        #: 20 MiB/s on a 3024-piece torrent.
        self._cursor = 0

    @property
    def complete(self) -> bool:
        return self.done_bytes >= self.meta.total_length

    def has_piece(self, index: int) -> bool:
        return 0 <= index < self.count and bool(self.done[index])

    def acquire(
        self,
        peer_key: int,
        has_piece: Callable[[int], bool],
        now: float | None = None,
    ) -> tuple[int, int, int] | None:
        """Return the next ``(piece, begin, length)`` for one peer, if any."""
        now = time.monotonic() if now is None else now
        self.expire(now)
        # Finish a piece this peer already started before taking a new one.
        # Otherwise a single peer could hold a fragment of every piece and
        # nothing would ever complete.
        for index in sorted(self.owners):
            if self.owners[index] != peer_key:
                continue
            item = self._missing_block(index)
            if item is not None:
                return self._reserve(peer_key, item, now)
        if len(self.owners) >= self.max_pieces:
            # Start nothing new, so fragment memory stays bounded on very
            # large-piece torrents.  Taking over a stalled piece does not add
            # to this count, so it is still allowed.
            index = self._takeover(peer_key, has_piece, now)
        else:
            index = self._open_piece(has_piece)
            if index is not None:
                self.owners[index] = peer_key
                self.fragments.setdefault(index, {})
                self.progress_at[index] = now
            else:
                index = self._takeover(peer_key, has_piece, now)
        if index is None:
            return None
        item = self._missing_block(index)
        if item is None:
            return None
        return self._reserve(peer_key, item, now)

    def expire(self, now: float | None = None) -> None:
        """Forget block requests old enough that no answer is coming."""
        now = time.monotonic() if now is None else now
        stale = [
            key
            for key, (_owner, sent_at) in self.outstanding.items()
            if now - sent_at > BLOCK_TIMEOUT
        ]
        for key in stale:
            self.outstanding.pop(key, None)

    def _takeover(
        self, peer_key: int, has_piece: Callable[[int], bool], now: float
    ) -> int | None:
        """Hand a stalled piece to *peer_key*, keeping the blocks received."""
        for index in sorted(self.owners):
            owner = self.owners[index]
            if owner == peer_key or self.done[index]:
                continue
            if now - self.progress_at.get(index, now) < STEAL_AFTER:
                continue
            if not has_piece(index):
                continue
            self.owners[index] = peer_key
            self.progress_at[index] = now
            # The previous owner's requests must not block the new one, and any
            # block it sends later is refused because the reservation is gone.
            for key in [k for k in self.outstanding if k[0] == index]:
                self.outstanding.pop(key, None)
            return index
        return None

    def _open_piece(self, has_piece: Callable[[int], bool]) -> int | None:
        """Lowest index at or after the cursor that this peer can serve."""
        if self.count <= 0:
            return None
        index = self._cursor
        if index >= self.count:
            index = 0
        for _ in range(self.count):
            if not self.done[index] and index not in self.owners and has_piece(index):
                self._cursor = index
                return index
            index += 1
            if index >= self.count:
                index = 0
        return None

    def _missing_block(self, index: int) -> tuple[int, int, int] | None:
        """Lowest block of *index* that is neither stored nor in flight."""
        length = self.meta.piece_length_at(index)
        have = self.fragments.get(index, {})
        for begin in range(0, length, BLOCK_LENGTH):
            if begin in have or (index, begin) in self.outstanding:
                continue
            return index, begin, min(BLOCK_LENGTH, length - begin)
        return None

    def _reserve(
        self, peer_key: int, item: tuple[int, int, int], now: float
    ) -> tuple[int, int, int]:
        index, begin, _length = item
        self.outstanding[(index, begin)] = (peer_key, now)
        return item

    def receive(
        self, peer_key: int, index: int, begin: int, data: bytes
    ) -> tuple[bool, bytes | None]:
        """Accept a block.  Returns ``(accepted, completed_piece_bytes)``."""
        if not (0 <= index < self.count) or self.done[index]:
            return False, None
        entry = self.outstanding.pop((index, begin), None)
        if entry is None or entry[0] != peer_key:
            return False, None
        length = self.meta.piece_length_at(index)
        if not data or begin + len(data) > length:
            return False, None
        fragments = self.fragments.setdefault(index, {})
        if begin in fragments:
            return True, None
        fragments[begin] = data
        self.received[index] = self.received.get(index, 0) + len(data)
        self.progress_at[index] = time.monotonic()
        if self.received[index] < length:
            return True, None
        buffer = bytearray(length)
        for offset, chunk in fragments.items():
            buffer[offset : offset + len(chunk)] = chunk
        return True, bytes(buffer)

    def complete_piece(self, index: int) -> bool:
        self.done[index] = 1
        if index == self._cursor:
            self._cursor += 1
        self.done_bytes += self.meta.piece_length_at(index)
        self.fragments.pop(index, None)
        self.received.pop(index, None)
        self.owners.pop(index, None)
        self.progress_at.pop(index, None)
        for key in [k for k in self.outstanding if k[0] == index]:
            self.outstanding.pop(key, None)
        return self.complete

    def reject(self, index: int) -> None:
        """Throw away a piece whose hash did not match."""
        self.fragments.pop(index, None)
        self.received.pop(index, None)
        self.progress_at.pop(index, None)
        self.owners.pop(index, None)
        for key in [k for k in self.outstanding if k[0] == index]:
            self.outstanding.pop(key, None)
        if index < self._cursor:
            self._cursor = index

    def release_peer(self, peer_key: int) -> None:
        for index, owner in list(self.owners.items()):
            if owner == peer_key:
                self.owners.pop(index, None)
                # The piece is only part-downloaded, so it goes back to the
                # pool; keeping fragments would hold memory for a piece nobody
                # is working on.
                self.fragments.pop(index, None)
                self.received.pop(index, None)
                self.progress_at.pop(index, None)
        for key, (owner, _sent) in list(self.outstanding.items()):
            if owner == peer_key:
                self.outstanding.pop(key, None)


# --------------------------------------------------------------------------
# Peer connections
# --------------------------------------------------------------------------


class _Peer:
    """One TCP peer connection speaking the BitTorrent wire protocol."""

    _counter = 0

    def __init__(self, task: "_Torrent", host: str, port: int) -> None:
        _Peer._counter += 1
        self.key = _Peer._counter
        self.task = task
        self.host = host
        self.port = port
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.bitfield = bytearray()
        self.have: set[int] = set()
        self.choked = True
        self.interested = False
        self.closed = False
        self.metadata_size = 0
        self.ut_metadata = 0
        self.ut_pex = 0
        self.downloaded = 0
        self.requests: set[tuple[int, int]] = set()
        self._wake = asyncio.Event()

    # -- capability ---------------------------------------------------------

    def has_piece(self, index: int) -> bool:
        if index in self.have:
            return True
        byte = index >> 3
        if byte >= len(self.bitfield):
            return False
        return bool(self.bitfield[byte] & (0x80 >> (index & 7)))

    def _set_bitfield(self, payload: bytes) -> None:
        self.bitfield = bytearray(payload)

    def _send(self, message_id: int, payload: bytes = b"") -> None:
        if self.writer is None or self.closed or self.writer.is_closing():
            return
        try:
            self.writer.write(
                struct.pack("!I", len(payload) + 1) + bytes([message_id]) + payload
            )
        except (OSError, RuntimeError):
            self.closed = True

    def _send_extended(self, extended_id: int, payload: bytes) -> None:
        self._send(20, bytes([extended_id]) + payload)

    def _send_extended_handshake(self) -> None:
        self._send_extended(
            0,
            bencodelib.encode(
                {
                    b"m": {b"ut_metadata": _UT_METADATA, b"ut_pex": _UT_PEX},
                    b"v": b"Lumora/1.0",
                    b"p": self.task.port,
                }
            ),
        )

    # -- lifecycle ----------------------------------------------------------

    async def run(self) -> None:
        try:
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), CONNECT_TIMEOUT
            )
            log.debug("%s: connected to %s:%s", self.task.infohash[:8], self.host, self.port)
            await self._handshake()
            log.debug("%s: handshake done with %s:%s", self.task.infohash[:8], self.host, self.port)
            await asyncio.gather(self._read_loop(), self._request_loop())
        except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError, BitTorrentError) as exc:
            log.debug("%s: peer %s:%s ended: %s", self.task.infohash[:8], self.host, self.port, exc)
        except asyncio.CancelledError:
            raise
        finally:
            await self.close()
            self.task.on_peer_closed(self.host, self.port)

    async def _handshake(self) -> None:
        assert self.reader is not None and self.writer is not None
        reserved = bytearray(8)
        reserved[5] |= _EXTENSION_BIT
        self.writer.write(
            _PROTOCOL + bytes(reserved) + bytes.fromhex(self.task.infohash) + self.task.peer_id
        )
        log.debug("%s: handshake sent, waiting for reply", self.task.infohash[:8])
        header = await asyncio.wait_for(self.reader.readexactly(68), HANDSHAKE_TIMEOUT)
        log.debug("%s: handshake reply %s bytes", self.task.infohash[:8], len(header))
        if header[:20] != _PROTOCOL:
            raise BitTorrentError("The peer does not speak BitTorrent.")
        # 20-byte protocol header + 8 reserved bytes precede the infohash.
        if header[28:48] != bytes.fromhex(self.task.infohash):
            raise BitTorrentError("The peer answered for a different torrent.")
        self._send_extended_handshake()
        if self.task.meta is not None:
            self._send(5, self.task.local_bitfield())
            self._send_interested()

    def _send_interested(self) -> None:
        if not self.interested:
            self.interested = True
            self._send(2)

    async def close(self) -> None:
        self.closed = True
        self.task.scheduler and self.task.scheduler.release_peer(self.key)
        if self.writer is not None:
            try:
                self.writer.close()
            except (OSError, RuntimeError):
                pass
            self.writer = None
        self.reader = None

    # -- reading ------------------------------------------------------------

    async def _read_loop(self) -> None:
        assert self.reader is not None
        while not self.closed:
            length = struct.unpack("!I", await self.reader.readexactly(4))[0]
            if length == 0:
                continue
            if length > 2 * 1024 * 1024:
                raise BitTorrentError("The peer sent an oversized message.")
            payload = await self.reader.readexactly(length)
            await self._handle(payload[0], payload[1:])

    async def _handle(self, message_id: int, payload: bytes) -> None:
        if message_id == 0:  # choke
            self.choked = True
            self._release_requests()
        elif message_id == 1:  # unchoke
            self.choked = False
            self._wake.set()
        elif message_id == 2:  # interested (we never upload)
            return
        elif message_id == 4:  # have
            if len(payload) >= 4:
                self.have.add(struct.unpack("!I", payload[:4])[0])
                self._wake.set()
        elif message_id == 5:  # bitfield
            self._set_bitfield(payload)
            if self.task.meta is not None:
                self._send_interested()
        elif message_id == 7:  # piece
            if len(payload) >= 8:
                index, begin = struct.unpack("!II", payload[:8])
                block = payload[8:]
                self.requests.discard((index, begin))
                self.downloaded += len(block)
                await self.task.got_block(self, index, begin, block)
        elif message_id == 8:  # cancel
            return
        elif message_id == 20:  # extended
            await self._handle_extended(payload)

    def _release_requests(self) -> None:
        scheduler = self.task.scheduler
        if scheduler is not None:
            for key in self.requests:
                # A piece taken over by another peer already dropped this
                # reservation; only our own entries may be released.
                entry = scheduler.outstanding.get(key)
                if entry is not None and entry[0] == self.key:
                    scheduler.outstanding.pop(key, None)
        self.requests.clear()

    async def _handle_extended(self, payload: bytes) -> None:
        if not payload:
            return
        extended_id, body = payload[0], payload[1:]
        log.debug(
            "%s: extended message id=%s (%s bytes) from %s:%s",
            self.task.infohash[:8],
            extended_id,
            len(body),
            self.host,
            self.port,
        )
        if extended_id == 0:
            try:
                document = bencodelib.decode(body, 0)[0]
            except bencodelib.BencodeError:
                return
            if not isinstance(document, Mapping):
                return
            mapping = document.get(b"m") or {}
            if isinstance(mapping, Mapping):
                self.ut_metadata = bencodelib.integer(mapping.get(b"ut_metadata"))
                self.ut_pex = bencodelib.integer(mapping.get(b"ut_pex"))
            self.metadata_size = bencodelib.integer(document.get(b"metadata_size"))
            log.debug(
                "%s: extended handshake ut_metadata=%s size=%s",
                self.task.infohash[:8],
                self.ut_metadata,
                self.metadata_size,
            )
            if self.task.meta is not None:
                self._send_interested()
            elif self.ut_metadata and self.metadata_size:
                self.task.request_metadata(self, self.metadata_size)
            return
        # Incoming extension messages are addressed with the ids *we* announced
        # in our own handshake, not the ids the peer advertised for us to use.
        if extended_id == _UT_METADATA:
            try:
                document = bencodelib.decode(body, 0)[0]
            except bencodelib.BencodeError:
                return
            if not isinstance(document, Mapping):
                return
            if bencodelib.integer(document.get(b"msg_type")) != 1:
                return
            piece = bencodelib.integer(document.get(b"piece"))
            total = bencodelib.integer(document.get(b"total_size"), self.metadata_size)
            header_length = len(bencodelib.encode(document))
            data = body[header_length:]
            self.task.got_metadata_piece(piece, data, total)
            return
        if extended_id == _UT_PEX:
            try:
                document = bencodelib.decode(body, 0)[0]
            except bencodelib.BencodeError:
                return
            if not isinstance(document, Mapping):
                return
            for peer in _compact_peers(document.get(b"added") or b""):
                self.task.add_candidate(peer)
            for peer in _compact_peers6(document.get(b"added6") or b""):
                self.task.add_candidate(peer)

    # -- requesting ---------------------------------------------------------

    async def _request_loop(self) -> None:
        while not self.closed:
            if self.task.meta is None or self.choked:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), 1.0)
                except asyncio.TimeoutError:
                    pass
                continue
            scheduler = self.task.scheduler
            if scheduler is None:
                await asyncio.sleep(0.2)
                continue
            # Blocks that timed out or were taken over no longer count against
            # this connection's pipeline, so a silent peer cannot pin all 64
            # slots forever.
            scheduler.expire()
            if self.requests:
                self.requests = {
                    key
                    for key in self.requests
                    if scheduler.outstanding.get(key, (None, 0.0))[0] == self.key
                }
            slots = REQUEST_PIPELINE - len(self.requests)
            if slots <= 0:
                try:
                    await asyncio.wait_for(self._wake.wait(), 1.0)
                except asyncio.TimeoutError:
                    pass
                self._wake.clear()
                continue
            progressed = False
            for _ in range(slots):
                item = scheduler.acquire(self.key, self.has_piece)
                if item is None:
                    break
                index, begin, length = item
                self.requests.add((index, begin))
                self._send(
                    6,
                    struct.pack("!III", index, begin, length),
                )
                progressed = True
            if not progressed:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), 1.0)
                except asyncio.TimeoutError:
                    pass


# --------------------------------------------------------------------------
# One torrent
# --------------------------------------------------------------------------


def _report_task_failure(task: asyncio.Task) -> None:
    """Surface a crashed helper task instead of letting it vanish."""
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        log.debug("torrent helper task failed: %r", error)


class _Torrent:
    """Drives one infohash to completion: discovery, metadata, pieces."""

    def __init__(
        self,
        gid: str,
        infohash: str,
        directory: Path,
        trackers: Sequence[str],
        *,
        port: int = 0,
        display_name: str = "",
        peer_hints: Sequence[tuple[str, int]] = (),
    ) -> None:
        self.gid = gid
        self.infohash = infohash
        self.directory = directory
        self.trackers = tuple(trackers)
        self.port = port
        self.display_name = display_name
        self.peer_id = _peer_id()
        self.meta: TorrentMeta | None = None
        self.scheduler: PieceScheduler | None = None
        self.status = "waiting"
        self.error = ""
        self.name = display_name
        self.peers: dict[int, _Peer] = {}
        self.candidates: list[tuple[str, int]] = list(peer_hints)
        self._seen: dict[tuple[str, int], float] = {}
        self._attempts: dict[tuple[str, int], int] = {}
        self._metadata = bytearray()
        self._metadata_have: set[int] = set()
        self._metadata_size = 0
        self._metadata_done = False
        self._metadata_requested: set[tuple[int, int]] = set()
        self._speed = 0.0
        self._last_bytes = 0
        self._last_time = time.monotonic()
        self._stopped = False
        self._paused = False
        self.task_dht: DHT | None = None
        self._tasks: list[asyncio.Task] = []
        self._peer_tasks: list[asyncio.Task] = []
        self._files: dict[Path, int] = {}
        self._done_event = asyncio.Event()

    # -- public state -------------------------------------------------------

    @property
    def completed(self) -> int:
        if self.scheduler is not None:
            return self.scheduler.done_bytes
        return 0

    @property
    def total(self) -> int:
        return self.meta.total_length if self.meta is not None else 0

    def snapshot(self) -> dict[str, Any]:
        peers = len(self.peers)
        speed = int(self._speed)
        return {
            "gid": self.gid,
            "status": self.status,
            "totalLength": str(self.total),
            "completedLength": str(self.completed),
            "downloadSpeed": str(speed),
            "uploadSpeed": "0",
            "connections": str(peers),
            "numSeeders": "0",
            "errorMessage": self.error,
            "bittorrent": {"info": {"name": self.name}},
            "files": [{"path": "/" + str(f.relative)} for f in (self.meta.files if self.meta else ())],
        }

    def local_bitfield(self) -> bytes:
        if self.scheduler is None:
            return b""
        blob = bytearray((self.scheduler.count + 7) // 8)
        for index in range(self.scheduler.count):
            if self.scheduler.done[index]:
                blob[index >> 3] |= 0x80 >> (index & 7)
        return bytes(blob)

    # -- orchestration ------------------------------------------------------

    async def run(self) -> None:
        # A task paused before the loop started must stay paused.
        self.status = "paused" if self._paused else "active"
        self._tasks = [
            asyncio.create_task(self._maintain_peers()),
            asyncio.create_task(self._discover()),
            asyncio.create_task(self._tick()),
        ]
        for task in self._tasks:
            task.add_done_callback(_report_task_failure)
        try:
            await self._done_event.wait()
        except asyncio.CancelledError:
            raise
        finally:
            for task in self._tasks:
                task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
            await self._close_peers()
            self._close_files()

    async def stop(self) -> None:
        self._stopped = True
        self._done_event.set()

    def pause(self) -> None:
        self._paused = True
        self.status = "paused"
        for peer in list(self.peers.values()):
            asyncio.ensure_future(peer.close())
        self.peers.clear()

    def resume(self) -> None:
        if self._paused:
            self._paused = False
            self.status = "active"

    async def _tick(self) -> None:
        """Update the reported speed once a second."""
        while not self._stopped:
            await asyncio.sleep(1.0)
            now = time.monotonic()
            current = self.completed
            elapsed = max(0.05, now - self._last_time)
            delta = max(0, current - self._last_bytes)
            instant = delta / elapsed
            self._speed = instant if self._speed <= 0 else (self._speed * 0.6 + instant * 0.4)
            self._last_bytes = current
            self._last_time = now
            if self.scheduler is not None and self.scheduler.complete:
                log.debug("%s: all pieces verified, finalising", self.infohash[:8])
                await self._finish()

    async def _finish(self) -> None:
        if self.status == "complete":
            return
        self.status = "complete"
        self._speed = 0.0
        await self._close_peers()
        self._done_event.set()

    async def _close_peers(self) -> None:
        peers, self.peers = list(self.peers.values()), {}
        for peer in peers:
            await peer.close()
        tasks, self._peer_tasks = list(self._peer_tasks), []
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _close_files(self) -> None:
        for handle in self._files.values():
            try:
                os.close(handle)
            except OSError:
                pass
        self._files.clear()

    # -- discovery ----------------------------------------------------------

    def add_candidate(self, peer: tuple[str, int]) -> None:
        if self._stopped or not peer[1]:
            return
        seen = self._seen.get(peer)
        if seen is not None and time.monotonic() - seen < 3.0:
            return
        # A peer that keeps dropping is not worth another socket, but a peer
        # that served most of a file and then went away is: the swarm is small
        # and a reconnect is often all that the missing pieces need.
        if self._attempts.get(peer, 0) >= 8:
            return
        if peer in self.candidates:
            return
        if len(self.candidates) < MAX_CANDIDATES:
            self.candidates.append(peer)

    def on_peer_closed(self, host: str, port: int) -> None:
        """Queue one delayed reconnect for a peer that just disconnected."""
        if self._stopped:
            return
        try:
            asyncio.get_running_loop().call_later(
                7.0, self.add_candidate, (host, port)
            )
        except RuntimeError:
            pass

    async def _discover(self) -> None:
        loop = asyncio.get_running_loop()
        while not self._stopped:
            info_hash = bytes.fromhex(self.infohash)
            jobs: list[Any] = []
            if self.trackers:
                jobs.append(
                    announce(
                        self.trackers,
                        info_hash,
                        self.peer_id,
                        self.port,
                        left=max(0, self.total - self.completed),
                        downloaded=self.completed,
                    )
                )
            dht = self.task_dht
            if dht is not None:
                jobs.append(dht.get_peers(info_hash, timeout=20.0))
            if jobs:
                results = await asyncio.gather(*jobs, return_exceptions=True)
                for result in results:
                    if isinstance(result, (list, set)):
                        for peer in result:
                            self.add_candidate(peer)
            # Stop asking when the swarm is already feeding this torrent.
            delay = ANNOUNCE_INTERVAL if len(self.peers) >= MAX_PEERS // 2 else 90.0
            deadline = loop.time() + delay
            while not self._stopped and loop.time() < deadline:
                await asyncio.sleep(1.0)

    async def _maintain_peers(self) -> None:
        while not self._stopped:
            if self._peer_tasks:
                self._peer_tasks = [task for task in self._peer_tasks if not task.done()]
            if not self._paused:
                capacity = MAX_PEERS - len(self.peers)
                for _ in range(max(0, capacity)):
                    if not self.candidates:
                        break
                    host, port = self.candidates.pop(0)
                    self._seen[(host, port)] = time.monotonic()
                    self._attempts[(host, port)] = self._attempts.get((host, port), 0) + 1
                    peer = _Peer(self, host, port)
                    self.peers[peer.key] = peer

                    def _forget(key: int = peer.key) -> None:
                        self.peers.pop(key, None)

                    task = asyncio.create_task(peer.run())
                    self._peer_tasks.append(task)
                    task.add_done_callback(lambda _t, forget=_forget: forget())
            await asyncio.sleep(0.5)
            if self.meta is not None and self.scheduler is not None:
                if self.scheduler.complete and self.status != "complete":
                    await self._finish()

    # -- metadata -----------------------------------------------------------

    def request_metadata(self, peer: _Peer, size: int) -> None:
        if self._metadata_done or self.meta is not None:
            return
        if size <= 0 or size > 8 * 1024 * 1024:
            return
        if not self._metadata:
            self._metadata = bytearray(size)
        self._metadata_size = size
        count = math.ceil(size / METADATA_PIECE_SIZE)
        for index in range(count):
            if index in self._metadata_have or (peer.key, index) in self._metadata_requested:
                continue
            self._metadata_requested.add((peer.key, index))
            log.debug("%s: requesting metadata piece %s", self.infohash[:8], index)
            peer._send_extended(
                peer.ut_metadata, bencodelib.encode({b"msg_type": 0, b"piece": index})
            )

    def got_metadata_piece(self, index: int, data: bytes, total: int) -> None:
        log.debug("%s: metadata piece %s (%s bytes)", self.infohash[:8], index, len(data))
        if self._metadata_done or self.meta is not None:
            return
        if total and not self._metadata:
            self._metadata = bytearray(total)
            self._metadata_size = total
        if not self._metadata:
            return
        start = index * METADATA_PIECE_SIZE
        if start + len(data) > len(self._metadata):
            return
        self._metadata[start : start + len(data)] = data
        self._metadata_have.add(index)
        expected = math.ceil(len(self._metadata) / METADATA_PIECE_SIZE)
        if len(self._metadata_have) < expected:
            return
        raw = bytes(self._metadata)
        try:
            meta = TorrentMeta.from_info_bytes(raw)
        except BitTorrentError as exc:
            self.status = "error"
            self.error = f"The metadata from peers was invalid: {exc}"
            self._done_event.set()
            return
        if meta.infohash != self.infohash:
            self.status = "error"
            self.error = "The metadata from peers does not match the infohash."
            self._done_event.set()
            return
        self._metadata_done = True
        log.debug("%s: metadata complete (%s)", self.infohash[:8], meta.name)
        self.set_meta(meta)
        for peer in list(self.peers.values()):
            peer._send_interested()
            peer._wake.set()

    def set_meta(self, meta: TorrentMeta) -> None:
        if self.meta is not None:
            return
        self.meta = meta
        self.name = meta.name or self.name
        self.scheduler = PieceScheduler(meta)
        self._last_time = time.monotonic()
        self._last_bytes = 0
        # Peers that connected while the metadata was still unknown were never
        # sent ``interested``; a piece could not be requested before now.
        for peer in list(self.peers.values()):
            peer._send_interested()
            peer._wake.set()
        if meta.total_length == 0:
            asyncio.ensure_future(self._finish())

    # -- data ---------------------------------------------------------------

    async def got_block(self, peer: _Peer, index: int, begin: int, data: bytes) -> None:
        scheduler = self.scheduler
        if scheduler is None:
            return
        accepted, piece = scheduler.receive(peer.key, index, begin, data)
        if not accepted:
            return
        peer._wake.set()
        if piece is None:
            return
        expected = self.meta.pieces[index] if self.meta else b""
        if hashlib.sha1(piece).digest() != expected:
            log.debug(
                "%s: piece %s failed verification from %s:%s",
                self.infohash[:8],
                index,
                peer.host,
                peer.port,
            )
            scheduler.reject(index)
            # A peer that served a corrupt piece is dropped; the piece is
            # requested again from whoever else has it.
            await peer.close()
            self.peers.pop(peer.key, None)
            return
        try:
            self._write_piece(index, piece)
        except OSError as exc:
            self.status = "error"
            self.error = f"The download folder rejected the data: {exc}"
            self._done_event.set()
            return
        scheduler.complete_piece(index)
        log.debug(
            "%s: piece %s ok (%s/%s bytes)",
            self.infohash[:8],
            index,
            scheduler.done_bytes,
            self.total,
        )
        for other in list(self.peers.values()):
            try:
                other._send(4, struct.pack("!I", index))
                other._wake.set()
            except (OSError, RuntimeError):
                pass

    def _write_piece(self, index: int, data: bytes) -> None:
        if self.meta is None:
            return
        piece_start = index * self.meta.piece_length
        piece_end = piece_start + len(data)
        for entry in self.meta.files:
            file_start = entry.offset
            file_end = entry.offset + entry.length
            if file_end <= piece_start or file_start >= piece_end:
                continue
            overlap_start = max(piece_start, file_start)
            overlap_end = min(piece_end, file_end)
            source = overlap_start - piece_start
            length = overlap_end - overlap_start
            if length <= 0:
                continue
            target = (self.directory / entry.relative).resolve()
            try:
                target.relative_to(self.directory.resolve())
            except ValueError as exc:
                raise OSError("A torrent path escapes the download folder.") from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            handle = self._files.get(target)
            if handle is None:
                handle = os.open(target, os.O_WRONLY | os.O_CREAT, 0o644)
                self._files[target] = handle
            os.pwrite(
                handle, data[source : source + length], overlap_start - file_start
            )


# --------------------------------------------------------------------------
# Engine facade
# --------------------------------------------------------------------------


class NativeEngine:
    """Runs BitTorrent inside this process, with an aria2-shaped client API.

    The queue in :mod:`lgl.torrent` only knows ``add_uri``, ``tell``, ``pause``,
    ``unpause`` and ``remove``.  Implementing those here means the app needs no
    external binary, and switching engines is invisible to the UI.
    """

    def __init__(
        self,
        directory: Path | str,
        *,
        listen_port: int | None = None,
        max_peers: int = MAX_PEERS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.directory = Path(directory).expanduser()
        self.listen_port = int(listen_port) if listen_port else _pick_port()
        self.max_peers = max(1, int(max_peers))
        self.clock = clock
        self.client = self
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stop_event: asyncio.Event | None = None
        self._torrents: dict[str, _Torrent] = {}
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._launches: dict[str, Any] = {}
        self._dht: DHT | None = None
        self._server: asyncio.AbstractServer | None = None
        self._error = ""
        self.version = __version__
        self._dht_task: asyncio.Task | None = None

    # -- lifecycle ----------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(
            target=self._thread_main, name="lgl-bittorrent", daemon=True
        )
        self._thread.start()
        if not self._ready.wait(20.0):
            self.stop()
            raise BitTorrentError("The built-in torrent engine did not start in time.")
        if self._error:
            self.stop()
            raise BitTorrentError(self._error)

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._serve())
        except Exception as exc:  # pragma: no cover - startup failure path
            self._error = f"The built-in torrent engine stopped: {exc}"
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()

    async def _serve(self) -> None:
        self._stop_event = asyncio.Event()
        try:
            self._server = await asyncio.start_server(
                self._accept, host="0.0.0.0", port=self.listen_port
            )
        except OSError:
            # Listening helps, but a busy port must not stop downloads.
            self._server = None
        self._ready.set()
        # DHT bootstrap resolves hostnames, which can block for seconds on a
        # slow or offline network; it must not delay the engine being usable.
        self._dht_task = asyncio.ensure_future(self._start_dht())
        try:
            await self._stop_event.wait()
        finally:
            if self._server is not None:
                self._server.close()
            if self._dht is not None:
                self._dht.close()

    async def _start_dht(self) -> None:
        dht = DHT(port=0)
        try:
            # Bootstrap resolution is best-effort; without it DHT discovery is
            # simply unavailable, which a magnet with trackers does not need.
            await asyncio.wait_for(dht.start(), 25.0)
        except asyncio.CancelledError:
            dht.close()
            raise
        except Exception:
            dht.close()
            return
        self._dht = dht
        for torrent in self._torrents.values():
            torrent.task_dht = dht

    async def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Accept an inbound peer for whichever torrent it names."""
        try:
            header = await asyncio.wait_for(reader.readexactly(68), HANDSHAKE_TIMEOUT)
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, OSError):
            writer.close()
            return
        if header[:20] != _PROTOCOL:
            writer.close()
            return
        infohash = header[28:48].hex()
        torrent = next(
            (t for t in self._torrents.values() if t.infohash == infohash), None
        )
        if torrent is None or torrent.meta is None:
            writer.close()
            return
        peername = writer.get_extra_info("peername") or ("", 0)
        peer = _Peer(torrent, str(peername[0]), int(peername[1]))
        peer.reader, peer.writer = reader, writer
        torrent.peers[peer.key] = peer
        try:
            reserved = bytearray(8)
            reserved[5] |= _EXTENSION_BIT
            writer.write(
                _PROTOCOL + bytes(reserved) + bytes.fromhex(infohash) + torrent.peer_id
            )
            peer._send_extended_handshake()
            peer._send(5, torrent.local_bitfield())
            await asyncio.gather(peer._read_loop(), peer._request_loop())
        except (OSError, asyncio.IncompleteReadError, asyncio.TimeoutError, BitTorrentError):
            pass
        finally:
            await peer.close()
            torrent.peers.pop(peer.key, None)

    def stop(self) -> None:
        loop = self._loop
        if loop is not None and loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(self._shutdown(), loop).result(timeout=10.0)
            except Exception:
                pass
            try:
                loop.call_soon_threadsafe(loop.stop)
            except RuntimeError:
                pass
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=10.0)
        self._torrents.clear()

    async def _shutdown(self) -> None:
        if self._dht_task is not None:
            self._dht_task.cancel()
            self._dht_task = None
        torrents = list(self._torrents.values())
        for torrent in torrents:
            torrent._stopped = True
            torrent._done_event.set()
        # Give each torrent a moment to close its sockets and file handles
        # before the event loop is torn down.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 3.0
        while loop.time() < deadline:
            if all(future.done() for future in self._launches.values()):
                break
            await asyncio.sleep(0.05)
        for future in self._launches.values():
            future.cancel()
        self._launches.clear()
        if self._server is not None:
            self._server.close()
        if self._dht is not None:
            self._dht.close()
        if self._stop_event is not None:
            self._stop_event.set()

    # -- client API (aria2 shaped) ------------------------------------------

    def _submit(self, coro: Any) -> Any:
        loop = self._loop
        if loop is None or not loop.is_running():
            raise BitTorrentError("The built-in torrent engine is not running.")
        return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=30.0)

    def add_uri(self, uri: str, directory: str) -> str:
        link = magnetlib.parse_magnet(uri)
        target = Path(directory).expanduser() if directory else self.directory
        gid = link.infohash[:16]
        existing = self._torrents.get(gid)
        if existing is not None and existing.status not in ("complete", "error"):
            return gid
        trackers = list(link.trackers)
        hints = _peer_hints(uri)
        torrent = _Torrent(
            gid=gid,
            infohash=link.infohash,
            directory=target,
            trackers=trackers,
            port=self.listen_port,
            display_name=link.display_name,
            peer_hints=hints,
        )
        torrent.task_dht = self._dht
        self._torrents[gid] = torrent
        self._snapshots[gid] = torrent.snapshot()

        async def _launch() -> None:
            try:
                await torrent.run()
            finally:
                self._snapshots[gid] = torrent.snapshot()

        self._launches[gid] = asyncio.run_coroutine_threadsafe(_launch(), self._loop)
        return gid

    def tell(self, gid: str) -> dict[str, Any]:
        torrent = self._torrents.get(gid)
        if torrent is None:
            raise BitTorrentError("The built-in engine does not know that download.")
        snapshot = torrent.snapshot()
        self._snapshots[gid] = snapshot
        return snapshot

    def pause(self, gid: str) -> None:
        torrent = self._torrents.get(gid)
        if torrent is None:
            return
        loop = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(torrent.pause)

    def unpause(self, gid: str) -> None:
        torrent = self._torrents.get(gid)
        if torrent is None:
            raise BitTorrentError("The built-in engine does not know that download.")
        loop = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(torrent.resume)

    def remove(self, gid: str) -> None:
        torrent = self._torrents.pop(gid, None)
        self._snapshots.pop(gid, None)
        if torrent is None:
            return
        loop = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(lambda: asyncio.ensure_future(torrent.stop()))


def _peer_hints(uri: str) -> list[tuple[str, int]]:
    """Read ``x.pe`` peer hints from a magnet URI.

    These are ordinary clues a link's publisher included; they are tried before
    or alongside tracker results, and a wrong one simply fails to connect.
    """
    hints: list[tuple[str, int]] = []
    try:
        query = parse_qs(urlsplit(uri).query, keep_blank_values=True)
    except ValueError:
        return hints
    for value in query.get("x.pe", []):
        text = value.strip()
        if not text:
            continue
        if text.startswith("["):
            host, _, tail = text[1:].partition("]")
            port_text = tail.lstrip(":")
        else:
            host, _, port_text = text.rpartition(":")
            if not host:
                continue
        try:
            port = int(port_text)
        except ValueError:
            continue
        if host and 0 < port < 65536:
            hints.append((host, port))
    return hints


def _pick_port() -> int:
    """Choose a stable-ish listen port inside the documented range."""
    for port in range(DEFAULT_PORTS[0], DEFAULT_PORTS[1] + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("0.0.0.0", port))
            except OSError:
                continue
            return port
    return 0


__all__ = [
    "BitTorrentError",
    "BLOCK_LENGTH",
    "DHT",
    "MAX_PEERS",
    "NativeEngine",
    "PieceScheduler",
    "TorrentFile",
    "TorrentMeta",
    "__version__",
    "announce",
]
