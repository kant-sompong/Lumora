"""Source-neutral catalog adapters with a strict, deny-by-default web policy.

The library only talks to a website that a human declared as a source, and each
declaration must carry a written reason why downloading from that host is
permitted.  This module is the single place that makes such requests, and it
refuses everything else:

  * ``https`` only, default port only, no credentials in the URL;
  * the host must be on the source's host allowlist;
  * every hostname must resolve to public addresses only, so a catalogue entry
    can never point the downloader at loopback, private, link-local, CGNAT,
    multicast, or reserved addresses;
  * redirects are followed by hand and *every hop* is re-validated against the
    same rules, so an allowlisted host cannot bounce us somewhere internal;
  * ``robots.txt`` is fetched once per host and enforced, including
    ``Crawl-delay``; if it cannot be read the host is treated as disallowed;
  * a response counts as a verified file only when the server's own headers say
    so.  A URL that merely *looks* like a download is reported as unverified.

Nothing here decides whether a source may distribute its files: that is a human
judgement recorded in ``SourcePolicy.permitted_reason``, and an adapter refuses
to construct without one.
"""

from __future__ import annotations

import abc
import ipaddress
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from . import __version__
from .util import AppError, validate_identifier

_ALLOWED_SCHEMES = frozenset({"https"})
_ALLOWED_PORTS = frozenset({None, 443})
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_HTML_TYPES = ("text/html", "application/xhtml+xml")
_TEXT_PATTERN_MAGIC = ("text/",)
# Content types that are plainly not a web page.  Anything outside this list is
# still reported, but as an unverified link rather than a confirmed file.
_FILE_TYPES = (
    "application/zip",
    "application/x-zip-compressed",
    "application/x-7z-compressed",
    "application/x-rar-compressed",
    "application/vnd.rar",
    "application/x-tar",
    "application/gzip",
    "application/x-gzip",
    "application/x-bzip2",
    "application/x-iso9660-image",
    "application/octet-stream",
    "application/x-msdownload",
    "application/vnd.microsoft.portable-executable",
    "application/x-msi",
    "application/x-appimage",
    "application/x-executable",
    "application/vnd.appimage",
    "image/x-iso9660-image",
)
_DOWNLOAD_HINTS = (
    ".zip",
    ".7z",
    ".rar",
    ".tar",
    ".gz",
    ".bz2",
    ".iso",
    ".exe",
    ".msi",
    ".appimage",
    ".bin",
)


class CatalogError(AppError):
    """A catalogue request failed or was refused."""


class AccessDeniedError(CatalogError):
    """A URL, host, or robots rule forbids this request."""


# --------------------------------------------------------------------------
# URL and address validation
# --------------------------------------------------------------------------


def _as_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    text = str(value).strip().strip("[]")
    if "%" in text:  # IPv6 zone index
        text = text.split("%", 1)[0]
    try:
        return ipaddress.ip_address(text)
    except ValueError:
        return None


def is_public_address(value: str) -> bool:
    """True only for globally routable unicast addresses."""
    ip = _as_ip(value)
    if ip is None:
        return False
    if ip.is_loopback or ip.is_private or ip.is_link_local:
        return False
    if ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        return False
    return bool(ip.is_global)


def _is_number(host: str) -> bool:
    """Reject the 2130706433 / 0x7f.1 / 0177.0.0.1 spellings of an address."""
    if _as_ip(host) is not None:
        return True
    if re.fullmatch(r"[0-9]+", host):
        return True
    if re.fullmatch(r"0[xX][0-9a-fA-F]+", host):
        return True
    labels = host.split(".")
    if len(labels) > 1 and all(re.fullmatch(r"(0[xX])?[0-9a-fA-F]+", l) for l in labels):
        return True
    return False


def resolve_host(host: str, port: int = 443) -> tuple[str, ...]:
    """Resolve *host* with the system resolver, returning every address."""
    try:
        info = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError) as exc:
        raise AccessDeniedError(f"Could not resolve “{host}”: {exc}") from exc
    addresses: list[str] = []
    for entry in info:
        sockaddr = entry[4] if len(entry) > 4 else ()
        if sockaddr and isinstance(sockaddr[0], str):
            addresses.append(sockaddr[0].split("%", 1)[0])
    if not addresses:
        raise AccessDeniedError(f"“{host}” did not resolve to any address.")
    return tuple(dict.fromkeys(addresses))


def ensure_public_host(host: str, *, resolver: Callable[[str], Sequence[str]] = resolve_host) -> None:
    """Raise unless every address behind *host* is public.

    A DNS name that resolves to a mix of public and private addresses is refused
    outright: which one the socket picks must not be guesswork.
    """
    if not isinstance(host, str) or not host:
        raise AccessDeniedError("A URL without a host cannot be fetched.")
    lowered = host.lower().rstrip(".")
    if lowered in ("localhost",) or lowered.endswith((".localhost", ".local", ".internal")):
        raise AccessDeniedError(f"“{host}” is a local host name; refusing to fetch it.")
    if _is_number(lowered):
        if not is_public_address(lowered):
            raise AccessDeniedError(f"“{host}” is not a public address; refusing to fetch it.")
        return
    addresses = resolver(lowered)
    if not addresses:
        raise AccessDeniedError(f"“{host}” did not resolve to any address.")
    for address in addresses:
        if not is_public_address(address):
            raise AccessDeniedError(
                f"“{host}” resolves to the non-public address {address}; refusing to fetch it."
            )


def check_url(url: str, policy: "SourcePolicy") -> urllib.parse.ParseResult:
    """Validate a URL against the policy. Returns the parsed form."""
    if not isinstance(url, str) or not url.strip():
        raise AccessDeniedError("Empty URL.")
    try:
        parsed = urllib.parse.urlsplit(url.strip())
    except ValueError as exc:
        raise AccessDeniedError(f"Unreadable URL: {url!r}") from exc
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise AccessDeniedError(f"Only https URLs are fetched ({parsed.scheme or 'no scheme'} given).")
    if parsed.username or parsed.password:
        raise AccessDeniedError("URLs with embedded credentials are refused.")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise AccessDeniedError("URL has no host.")
    if not policy.host_allowed(host):
        raise AccessDeniedError(
            f"“{host}” is not an allowlisted host for “{policy.name}”; refusing to fetch it."
        )
    try:
        port = parsed.port
    except ValueError as exc:
        raise AccessDeniedError(f"Invalid port in {url!r}") from exc
    if port not in _ALLOWED_PORTS:
        raise AccessDeniedError(f"Only the default https port is allowed (got {port}).")
    segments = [s for s in parsed.path.split("/") if s]
    if any(s in (".", "..") for s in segments):
        raise AccessDeniedError("URL path traversal is refused.")
    return parsed


# --------------------------------------------------------------------------
# Source policy
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SourcePolicy:
    """The rules a single website source must satisfy before any request."""

    name: str
    hosts: frozenset[str]
    permitted_reason: str
    user_agent: str = f"Lumora/{__version__} (personal desktop client)"
    timeout: float = 20.0
    max_bytes: int = 8 * 1024 * 1024
    max_redirects: int = 4
    min_delay: float = 1.0
    respect_robots: bool = True

    def __post_init__(self) -> None:
        name = self.name.strip()
        if not name:
            raise ValueError("A source policy needs a name.")
        if not self.permitted_reason.strip():
            raise ValueError(
                "A source policy must record why downloading from these hosts is permitted."
            )
        hosts = frozenset(_clean_host(h) for h in self.hosts)
        if not hosts or "" in hosts:
            raise ValueError("A source policy needs at least one host.")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "hosts", hosts)
        object.__setattr__(self, "timeout", max(1.0, float(self.timeout)))
        object.__setattr__(self, "max_bytes", max(1024, int(self.max_bytes)))
        object.__setattr__(self, "max_redirects", max(0, int(self.max_redirects)))
        object.__setattr__(self, "min_delay", max(0.0, float(self.min_delay)))

    def host_allowed(self, host: str) -> bool:
        host = (host or "").lower().rstrip(".")
        return any(host == allowed or host.endswith("." + allowed) for allowed in self.hosts)

    @property
    def product_token(self) -> str:
        return self.user_agent.split("/", 1)[0].strip().lower()


def _clean_host(host: str) -> str:
    value = str(host).strip().lower().rstrip(".")
    if "://" in value or "/" in value:
        raise ValueError(f"Policy hosts must be bare host names, not {host!r}.")
    return value


# --------------------------------------------------------------------------
# robots.txt
# --------------------------------------------------------------------------

_DISALLOW_ALL = "deny-all"


@dataclass(frozen=True)
class RobotsRules:
    """Parsed robots.txt rules relevant to one user agent."""

    rules: tuple[tuple[bool, re.Pattern[str]], ...] = ()
    crawl_delay: float | None = None
    available: bool = True
    reason: str = ""

    @property
    def allow_all(self) -> bool:
        return self.available and not self.rules

    @classmethod
    def allow(cls) -> "RobotsRules":
        return cls()

    @classmethod
    def deny(cls, reason: str) -> "RobotsRules":
        return cls(rules=((False, re.compile("^/")),), available=False, reason=reason)

    @classmethod
    def parse(cls, text: str, product_token: str) -> "RobotsRules":
        groups: list[tuple[list[str], list[tuple[bool, str]], float | None]] = []
        agents: list[str] = []
        rules: list[tuple[bool, str]] = []
        delay: float | None = None
        in_rules = False

        def flush() -> None:
            nonlocal agents, rules, delay
            if agents and (rules or delay is not None):
                groups.append((agents, rules, delay))
            agents, rules, delay = [], [], None

        for raw_line in text.splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if key == "user-agent":
                if in_rules:
                    flush()
                    in_rules = False
                agents.append(value.lower())
            elif key in ("allow", "disallow"):
                in_rules = True
                if not value:
                    continue
                rules.append((key == "allow", value))
            elif key == "crawl-delay":
                in_rules = True
                try:
                    parsed_delay = float(value)
                except ValueError:
                    continue
                if parsed_delay >= 0:
                    delay = max(delay or 0.0, parsed_delay)
        flush()

        matched: list[tuple[bool, str]] = []
        token = (product_token or "").lower()
        best_specificity = -1
        for group_agents, group_rules, group_delay in groups:
            specificity = -1
            for agent in group_agents:
                if agent == "*":
                    specificity = max(specificity, 0)
                elif token and (agent == token or agent in token):
                    specificity = max(specificity, len(agent) + 1)
            if specificity < 0:
                continue
            if specificity > best_specificity:
                best_specificity = specificity
                matched = list(group_rules)
                delay = group_delay
            elif specificity == best_specificity:
                matched.extend(group_rules)
                if group_delay is not None:
                    delay = max(delay or 0.0, group_delay)

        compiled = tuple((allow, _rule_pattern(pattern)) for allow, pattern in matched)
        return cls(rules=compiled, crawl_delay=delay)

    def allows(self, path: str) -> bool:
        if not self.available:
            return False
        target = path or "/"
        best_length = -1
        best_allow = True
        for allow, pattern in self.rules:
            match = pattern.match(target)
            if not match:
                continue
            length = len(match.group(0))
            if length > best_length or (length == best_length and allow):
                best_length = length
                best_allow = allow
        return best_allow


def _rule_pattern(pattern: str) -> re.Pattern[str]:
    anchored_end = pattern.endswith("$")
    body = pattern[:-1] if anchored_end else pattern
    escaped = re.escape(body).replace(r"\*", ".*")
    return re.compile("^" + escaped + ("$" if anchored_end else ""))


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


class _Headers(dict):
    """Case-insensitive header mapping that keeps the first spelling."""

    def __init__(self, raw: Any = None) -> None:
        super().__init__()
        for key, value in (raw or {}).items() if hasattr(raw, "items") else ():
            self.setdefault(str(key).lower(), str(value))

    def get(self, key: str, default: Any = None) -> Any:  # type: ignore[override]
        return super().get(str(key).lower(), default)


@dataclass(frozen=True)
class HttpResponse:
    url: str
    status: int
    headers: Mapping[str, str]
    body: bytes = b""
    truncated: bool = False

    @property
    def content_type(self) -> str:
        return (self.headers.get("content-type") or "").split(";", 1)[0].strip().lower()

    @property
    def content_length(self) -> int | None:
        value = self.headers.get("content-length")
        try:
            return int(str(value).strip()) if value is not None else None
        except ValueError:
            return None

    @property
    def content_range_total(self) -> int | None:
        value = self.headers.get("content-range") or ""
        _, _, total = str(value).partition("/")
        try:
            return int(total.strip()) if total.strip() not in ("", "*") else None
        except ValueError:
            return None

    @property
    def filename(self) -> str:
        disposition = self.headers.get("content-disposition") or ""
        if not disposition:
            return ""
        match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', disposition, re.IGNORECASE)
        if not match:
            return ""
        raw = match.group(1).strip()
        try:
            raw = urllib.parse.unquote(raw)
        except UnicodeError:
            pass
        return raw.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]

    @property
    def is_html(self) -> bool:
        return self.content_type in _HTML_TYPES


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Surface redirects as HTTPError so every hop can be validated."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


class Fetcher:
    """Validating HTTP client for exactly one :class:`SourcePolicy`."""

    def __init__(
        self,
        policy: SourcePolicy,
        *,
        opener: Any = None,
        resolver: Callable[[str], Sequence[str]] = resolve_host,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.policy = policy
        self._opener = opener if opener is not None else urllib.request.build_opener(_NoRedirect())
        self._resolver = resolver
        self._clock = clock
        self._sleep = sleep
        self._robots: dict[str, RobotsRules] = {}
        self._robots_lock = threading.Lock()
        self._last_request: dict[str, float] = {}
        self.requests_made = 0

    # -- policy ------------------------------------------------------------

    def check(self, url: str) -> urllib.parse.ParseResult:
        parsed = check_url(url, self.policy)
        ensure_public_host(parsed.hostname or "", resolver=self._resolver)
        return parsed

    def robots_for(self, host: str) -> RobotsRules:
        host = host.lower().rstrip(".")
        with self._robots_lock:
            cached = self._robots.get(host)
        if cached is not None:
            return cached
        rules = self._fetch_robots(host)
        with self._robots_lock:
            self._robots[host] = rules
        return rules

    def _fetch_robots(self, host: str) -> RobotsRules:
        if not self.policy.respect_robots:
            return RobotsRules.allow()
        url = f"https://{host}/robots.txt"
        self._wait(host)
        try:
            response = self._perform(url, method="GET", check_robots=False)
        except AccessDeniedError:
            raise
        except CatalogError as exc:
            return RobotsRules.deny(f"robots.txt could not be read ({exc}).")
        if response.status in (401, 403):
            return RobotsRules.deny(f"robots.txt returned HTTP {response.status}.")
        if response.status == 404 or response.status == 410:
            return RobotsRules.allow()
        if response.status >= 400:
            return RobotsRules.deny(f"robots.txt returned HTTP {response.status}.")
        if response.content_type and not response.content_type.startswith("text/plain"):
            # A challenge page (for example an interstitial) is not a robots.txt.
            return RobotsRules.deny(
                "robots.txt returned a non-plain-text page; automated access cannot be confirmed."
            )
        try:
            text = response.body.decode("utf-8", "replace")
        except Exception:  # pragma: no cover - decode never raises with replace
            return RobotsRules.deny("robots.txt was unreadable.")
        return RobotsRules.parse(text, self.policy.product_token)

    # -- requests ----------------------------------------------------------

    def fetch(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        max_bytes: int | None = None,
        strict_size: bool = True,
        check_robots: bool = True,
    ) -> HttpResponse:
        return self._perform(
            url, method=method, headers=headers, max_bytes=max_bytes,
            strict_size=strict_size, check_robots=check_robots,
        )

    def _perform(
        self,
        url: str,
        *,
        method: str,
        headers: Mapping[str, str] | None = None,
        max_bytes: int | None = None,
        strict_size: bool = True,
        check_robots: bool = True,
    ) -> HttpResponse:
        current = url
        for hop in range(self.policy.max_redirects + 1):
            parsed = self.check(current)
            host = (parsed.hostname or "").lower()
            if check_robots:
                rules = self.robots_for(host)
                path = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
                if not rules.allows(path):
                    raise AccessDeniedError(
                        f"robots.txt for {host} does not allow {parsed.path or '/'}."
                    )
            response = self._raw_request(current, method, headers, max_bytes, strict_size)
            if response.status in _REDIRECT_STATUSES:
                location = response.headers.get("location")
                if not location:
                    raise CatalogError(f"Redirect without a Location header from {current}.")
                current = urllib.parse.urljoin(current, location)
                method = "GET" if response.status in (301, 302, 303) else method
                headers = None
                continue
            return response
        raise AccessDeniedError(
            f"Refused to follow more than {self.policy.max_redirects} redirects from {url}."
        )

    def _raw_request(
        self,
        url: str,
        method: str,
        headers: Mapping[str, str] | None,
        max_bytes: int | None,
        strict_size: bool,
    ) -> HttpResponse:
        parsed = self.check(url)
        host = (parsed.hostname or "").lower()
        self._wait(host)
        request_headers = {
            "User-Agent": self.policy.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "identity",
        }
        if headers:
            request_headers.update({str(k): str(v) for k, v in headers.items()})
        request = urllib.request.Request(url, headers=request_headers, method=method)
        self.requests_made += 1
        try:
            response = self._opener.open(request, timeout=self.policy.timeout)
        except urllib.error.HTTPError as exc:  # redirects and error statuses
            response = exc
        except urllib.error.URLError as exc:
            raise CatalogError(f"Could not reach {host}: {exc.reason}") from exc
        except (socket.timeout, TimeoutError) as exc:
            raise CatalogError(f"{host} did not respond in time.") from exc
        except ValueError as exc:
            raise CatalogError(f"Invalid request to {host}: {exc}") from exc

        try:
            status = int(getattr(response, "status", None) or getattr(response, "code", 0) or 0)
            raw_headers = _Headers(getattr(response, "headers", None))
            limit = self.policy.max_bytes if max_bytes is None else max(0, int(max_bytes))
            body = b""
            truncated = False
            # An error response carries nothing worth reading, and urllib's
            # HTTPError may expose a body stream that is already closed.
            if method != "HEAD" and status < 400:
                declared = None
                try:
                    declared = int(str(raw_headers.get("content-length") or "").strip())
                except ValueError:
                    declared = None
                if strict_size and declared is not None and declared > limit:
                    raise CatalogError(
                        f"{host} offered {declared} bytes, more than the "
                        f"{limit}-byte limit for this source."
                    )
                try:
                    body = response.read(limit + 1) or b""
                except (OSError, ValueError) as exc:
                    raise CatalogError(f"The response body from {host} was unreadable: {exc}") from exc
                if len(body) > limit:
                    body = body[:limit]
                    truncated = True
                    if strict_size:
                        raise CatalogError(
                            f"{host} sent more than the {limit}-byte limit for this source."
                        )
            final_url = getattr(response, "geturl", lambda: url)() or url
        finally:
            try:
                response.close()
            except Exception:  # pragma: no cover - closing is best effort
                pass
        return HttpResponse(
            url=str(final_url), status=status, headers=raw_headers,
            body=body, truncated=truncated,
        )

    def _wait(self, host: str) -> None:
        delay = self.policy.min_delay
        rules = self._robots.get(host)
        if rules is not None and rules.crawl_delay:
            delay = max(delay, rules.crawl_delay)
        if delay <= 0:
            return
        last = self._last_request.get(host)
        now = self._clock()
        if last is not None:
            remaining = delay - (now - last)
            if remaining > 0:
                self._sleep(remaining)
                now = self._clock()
        self._last_request[host] = now


# --------------------------------------------------------------------------
# Link classification
# --------------------------------------------------------------------------


class LinkKind:
    """How a URL behaved when it was actually requested."""

    PAGE = "page"                    # ordinary HTML page
    LANDING = "landing"              # HTML page that only offers further links
    VERIFIED_FILE = "verified-file"  # server confirmed a file response
    UNVERIFIED = "unverified"        # non-HTML response we cannot confirm
    EXTERNAL = "external"            # left the allowlisted host
    UNREACHABLE = "unreachable"


@dataclass(frozen=True)
class LinkProbe:
    """What a single request actually established about a URL."""

    url: str
    final_url: str
    status: int
    kind: str
    content_type: str = ""
    filename: str = ""
    size: int | None = None
    notes: tuple[str, ...] = ()

    @property
    def verified_file(self) -> bool:
        return self.kind == LinkKind.VERIFIED_FILE

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "final_url": self.final_url,
            "status": self.status,
            "kind": self.kind,
            "content_type": self.content_type,
            "filename": self.filename,
            "size": self.size,
            "notes": list(self.notes),
        }


def _looks_like_file(url: str) -> bool:
    path = urllib.parse.urlsplit(url).path.lower()
    return any(path.endswith(suffix) for suffix in _DOWNLOAD_HINTS)


def classify(
    response: HttpResponse, *, origin_host: str, method: str, requested: str = ""
) -> LinkProbe:
    """Turn a response into a probe result without guessing."""
    final_host = (urllib.parse.urlsplit(response.url).hostname or "").lower()
    notes: list[str] = []
    external = bool(final_host) and final_host != origin_host.lower()
    content_type = response.content_type
    # A ranged response reports the real file size in Content-Range and the size
    # of the slice in Content-Length, so the range total wins when present.
    size = response.content_range_total
    if size is None:
        size = response.content_length
    filename = response.filename

    if response.status == 0:
        kind = LinkKind.UNREACHABLE
        notes.append("No HTTP status was received.")
    elif response.status >= 400:
        kind = LinkKind.UNREACHABLE
        notes.append(f"The server answered HTTP {response.status}.")
    elif method == "HEAD" and size is None:
        kind = LinkKind.UNVERIFIED
        notes.append("The response did not state a size.")
    elif content_type.startswith(_HTML_TYPES):
        kind = LinkKind.EXTERNAL if external else LinkKind.LANDING
        if _looks_like_file(response.url):
            notes.append(
                "The URL looks like a file but the server returned an HTML page."
            )
    elif content_type.startswith(_TEXT_PATTERN_MAGIC[0]):
        kind = LinkKind.EXTERNAL if external else LinkKind.UNVERIFIED
        notes.append(f"Text response ({content_type}), not a binary file.")
    elif any(content_type.startswith(t) for t in _FILE_TYPES) or (not content_type and filename):
        kind = LinkKind.EXTERNAL if external else LinkKind.VERIFIED_FILE
        if not filename:
            filename = urllib.parse.unquote(
                urllib.parse.urlsplit(response.url).path.rsplit("/", 1)[-1]
            )
        if size is None:
            notes.append("The size is unknown (chunked or ranged response).")
    else:
        kind = LinkKind.EXTERNAL if external else LinkKind.UNVERIFIED
        notes.append(f"Unrecognised content type “{content_type or 'none'}”.")
    return LinkProbe(
        url=requested or response.url, final_url=response.url, status=response.status,
        kind=kind, content_type=content_type, filename=filename, size=size,
        notes=tuple(notes),
    )


def probe_link(fetcher: Fetcher, url: str) -> LinkProbe:
    """Ask the server about one URL without downloading its content.

    A ``HEAD`` request is tried first.  When the server refuses it, a one-byte
    ranged ``GET`` is used instead so no file content is transferred.
    """
    parsed = fetcher.check(url)
    origin_host = (parsed.hostname or "").lower()
    try:
        head = fetcher.fetch(url, method="HEAD")
    except CatalogError as exc:
        if isinstance(exc, AccessDeniedError):
            raise
        head = None
    if head is not None and head.status < 400 and (
        head.content_length is not None or head.content_type
    ):
        return classify(head, origin_host=origin_host, method="HEAD", requested=url)
    ranged = fetcher.fetch(
        url, method="GET", headers={"Range": "bytes=0-0"}, max_bytes=1, strict_size=False
    )
    notes = ()
    if head is not None and head.status >= 400:
        notes = (f"HEAD returned HTTP {head.status}; a ranged GET was used.",)
    probe = classify(ranged, origin_host=origin_host, method="GET", requested=url)
    if notes:
        probe = LinkProbe(
            url=probe.url, final_url=probe.final_url, status=probe.status, kind=probe.kind,
            content_type=probe.content_type, filename=probe.filename, size=probe.size,
            notes=notes + probe.notes,
        )
    return probe


# --------------------------------------------------------------------------
# Catalog entries and adapters
# --------------------------------------------------------------------------

_SLUG = re.compile(r"[^a-z0-9]+")


def identifier_from_url(url: str, fallback: str = "entry") -> str:
    """Derive a stable, filesystem-safe identifier from a page URL."""
    path = urllib.parse.urlsplit(url).path.rstrip("/")
    slug = urllib.parse.unquote(path.rsplit("/", 1)[-1]).lower()
    slug = _SLUG.sub("-", slug).strip("-")
    limit = 180
    if len(slug) > limit:
        slug = slug[:limit].rstrip("-")
    if not slug:
        slug = fallback
    if not slug[0].isalnum():
        slug = "x-" + slug.lstrip("-")
    try:
        validate_identifier(slug)
    except ValueError:
        slug = "entry-" + __import__("hashlib").sha1(url.encode("utf-8")).hexdigest()[:12]
    return slug


@dataclass(frozen=True)
class CatalogEntry:
    """One downloadable thing discovered on a permitted source."""

    source: str
    identifier: str
    title: str
    page_url: str
    files: tuple[str, ...] = ()
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_identifier(self.identifier)
        if not self.page_url.lower().startswith("https://"):
            raise ValueError(f"Catalog entry page must be https: {self.page_url!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "identifier": self.identifier,
            "title": self.title,
            "page_url": self.page_url,
            "files": list(self.files),
            "metadata": dict(self.metadata),
        }


class CatalogAdapter(abc.ABC):
    """List entries from one permitted source."""

    def __init__(self, policy: SourcePolicy, fetcher: Fetcher | None = None) -> None:
        self.policy = policy
        self.fetcher = fetcher if fetcher is not None else Fetcher(policy)

    @abc.abstractmethod
    def list_entries(self) -> list[CatalogEntry]:
        """Return catalog entries. Implementations must stay read-only."""

    def probe(self, url: str) -> LinkProbe:
        return probe_link(self.fetcher, url)


def _title_from_url(url: str) -> str:
    path = urllib.parse.urlsplit(url).path.rstrip("/")
    name = urllib.parse.unquote(path.rsplit("/", 1)[-1])
    name = re.sub(r"[-_]+", " ", name).strip()
    return name.title() if name else url


class SitemapCatalogAdapter(CatalogAdapter):
    """Enumerate entries from a standard XML sitemap on a permitted source.

    Sitemaps are the only discovery mechanism implemented here on purpose: they
    are published for crawlers, need no login, and contain nothing but links.
    """

    def __init__(
        self,
        policy: SourcePolicy,
        sitemap_url: str,
        *,
        include: Callable[[str], bool] | None = None,
        fetcher: Fetcher | None = None,
        max_entries: int = 500,
        max_sitemaps: int = 5,
    ) -> None:
        super().__init__(policy, fetcher)
        self.sitemap_url = sitemap_url
        self.include = include
        self.max_entries = max(1, int(max_entries))
        self.max_sitemaps = max(1, int(max_sitemaps))

    def list_entries(self) -> list[CatalogEntry]:
        locations = self._locations(self.sitemap_url, depth=0)
        entries: list[CatalogEntry] = []
        seen: set[str] = set()
        for loc in locations:
            if len(entries) >= self.max_entries:
                break
            if loc == self.sitemap_url:
                continue
            host = (urllib.parse.urlsplit(loc).hostname or "").lower()
            if not self.policy.host_allowed(host):
                continue
            if self.include is not None and not self.include(loc):
                continue
            if loc in seen:
                continue
            seen.add(loc)
            entries.append(
                CatalogEntry(
                    source=self.policy.name,
                    identifier=identifier_from_url(loc),
                    title=_title_from_url(loc),
                    page_url=loc,
                )
            )
        return entries

    def _locations(self, url: str, *, depth: int) -> list[str]:
        if depth > 1 or self.max_sitemaps <= 0:
            return []
        response = self.fetcher.fetch(url)
        if response.status >= 400:
            raise CatalogError(f"{url} returned HTTP {response.status}.")
        text = response.body.decode("utf-8", "replace")
        if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
            raise CatalogError("Sitemap documents with a DTD are refused.")
        try:
            root = ElementTree.fromstring(text)
        except ElementTree.ParseError as exc:
            raise CatalogError(f"{url} is not a readable sitemap: {exc}") from exc
        tag = root.tag.rsplit("}", 1)[-1]
        locs = [elem.text.strip() for elem in root.iter() if elem.tag.rsplit("}", 1)[-1] == "loc" and elem.text]
        if tag == "sitemapindex":
            nested: list[str] = []
            for child in locs[: self.max_sitemaps]:
                nested.extend(self._locations(child, depth=depth + 1))
            return nested
        return locs


def build_fetcher(policy: SourcePolicy) -> Fetcher:
    """Convenience constructor used by the application."""
    return Fetcher(policy)


def describe_policy(policy: SourcePolicy) -> str:
    hosts = ", ".join(sorted(policy.hosts))
    return f"{policy.name} ({hosts}) — permitted: {policy.permitted_reason}"
