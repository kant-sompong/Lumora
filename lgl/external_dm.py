"""Hand source links to an external download manager.

The built-in queue downloads one connection at a time and refuses anything it
has not verified.  Large mirror files often resume better in a dedicated
download manager, which is why the Kryoto site itself suggests one.  This module
does the only hand-off it can do honestly:

* **JDownloader 2** runs a documented local interface — ``GET /jdcheck.js``
  reports whether it is running, and a form ``POST /flash/add`` on
  ``127.0.0.1:9666`` adds links (see
  https://jdownloader.org/knowledge/wiki/glossary/cnl2).  That interface is
  used here with the links untouched.
* **AB Download Manager** ships a REST API of its own: with its API enabled it
  listens on ``127.0.0.1:15151`` and accepts
  ``POST /start-headless-download`` with ``{"downloadSource": {"link": …},
  "folder": …, "name": …}`` (see ``REST-API.yml`` in
  https://github.com/amir1376/ab-download-manager).  That endpoint starts the
  file immediately, so it is used here rather than ``/add``, which only fills
  that application's link list.

Neither project is affiliated with this app.  Nothing is sent unless the user
presses the button, and no access control is circumvented: the manager fetches
the link exactly as a browser would.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Iterable

#: JDownloader's Click'n'Load / FlashGot listener, as documented by JDownloader.
JD_HOST = "127.0.0.1"
JD_PORT = 9666
JD_CHECK_PATH = "/jdcheck.js"
JD_ADD_PATH = "/flash/add"
JD_ORIGIN = f"http://{JD_HOST}:{JD_PORT}"

#: AB Download Manager's REST API, from its own REST-API.yml and integration
#: server defaults (apiEnabled=true, apiPort=15151).
AB_HOST = "127.0.0.1"
AB_PORT = 15151
AB_PING_PATH = "/ping"
AB_START_PATH = "/start-headless-download"
AB_ADD_PATH = "/add"
AB_ORIGIN = f"http://{AB_HOST}:{AB_PORT}"

#: Shown next to the button.  Both names are other people's products.
NOT_AFFILIATED = (
    "For large or unstable downloads, AB Download Manager or JDownloader2 "
    "handle these mirrors better. Not affiliated with either."
)

#: What the app knows about each tool.  ``api`` is true only where a documented
#: local interface exists and is actually used.
MANAGERS = {
    "jdownloader": {
        "name": "JDownloader 2",
        "url": "https://jdownloader.org/",
        "api": True,
        "how": "Click'n'Load on 127.0.0.1:9666 (POST /flash/add)",
    },
    "ab_download_manager": {
        "name": "AB Download Manager",
        "url": "https://abdownloadmanager.com/",
        "api": True,
        "how": "REST API on 127.0.0.1:15151 (POST /start-headless-download)",
    },
}

_USER_AGENT = "NativeGameLibrary (external download manager hand-off)"


class ExternalDMError(RuntimeError):
    """The chosen download manager could not be reached or refused the links."""


def _request(url: str, *, data: bytes | None = None, timeout: float) -> tuple[int, str]:
    request = urllib.request.Request(
        url,
        data=data,
        method="POST" if data is not None else "GET",
        headers={"User-Agent": _USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read(4096).decode("utf-8", "replace")


def _json_request(url: str, payload: object, *, timeout: float) -> tuple[int, str]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "User-Agent": _USER_AGENT,
            "Content-Type": "application/json",
            "Accept": "text/plain, application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read(4096).decode("utf-8", "replace")


def jdownloader_running(port: int = JD_PORT, timeout: float = 1.5) -> bool:
    """True when JDownloader answers its documented check script."""
    try:
        status, body = _request(
            f"http://{JD_HOST}:{int(port)}{JD_CHECK_PATH}", timeout=timeout
        )
    except (urllib.error.URLError, OSError, ValueError, TypeError):
        return False
    return status == 200 and "jdownloader" in body.lower()


def ab_download_manager_running(port: int = AB_PORT, timeout: float = 1.5) -> bool:
    """True when AB Download Manager's REST API answers ``/ping``."""
    try:
        status, body = _request(
            f"http://{AB_HOST}:{int(port)}{AB_PING_PATH}", data=b"", timeout=timeout
        )
    except (urllib.error.URLError, OSError, ValueError, TypeError):
        return False
    return status == 200 and "pong" in body.lower()


def _supported(url: object) -> bool:
    """Only ordinary web links may be handed over; never file:// or javascript:."""
    if not isinstance(url, str):
        return False
    try:
        split = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return False
    return split.scheme in ("http", "https") and bool(split.hostname)


def _unique_links(urls: Iterable[object]) -> list[str]:
    """Keep http(s) links, in order, without duplicates."""
    clean: list[str] = []
    for url in urls:
        if not _supported(url):
            continue
        text = url.strip()  # type: ignore[union-attr]
        if text not in clean:
            clean.append(text)
    return clean


def send_to_ab_download_manager(
    urls: Iterable[object],
    *,
    folder: str = "",
    name: str = "",
    port: int = AB_PORT,
    timeout: float = 8.0,
) -> int:
    """Start these links in AB Download Manager through its own REST API.

    Each link is posted to ``/start-headless-download`` exactly as given; that
    application then performs the download with its own engine (multi-connection
    ranges, retries, resume) into *folder* when one is supplied.  Returns how
    many links it accepted; raises :class:`ExternalDMError` when it is not
    running, refuses every link, or has API authentication turned on.
    """
    clean = _unique_links(urls)
    if not clean:
        raise ExternalDMError("There is no downloadable link to send.")

    sent = 0
    last_error = ""
    for url in clean:
        task: dict[str, object] = {"downloadSource": {"link": url}}
        if folder:
            task["folder"] = folder
        if name:
            task["name"] = name
        try:
            status, _body = _json_request(
                f"http://{AB_HOST}:{int(port)}{AB_START_PATH}", task, timeout=timeout
            )
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise ExternalDMError(
                    f"AB Download Manager refused this app (HTTP {exc.code}). "
                    "If its API authentication is enabled, turn it off in that "
                    "application's API settings."
                ) from exc
            last_error = f"AB Download Manager refused a link (HTTP {exc.code})."
            continue
        except (urllib.error.URLError, OSError, ValueError, TypeError) as exc:
            raise ExternalDMError(
                "AB Download Manager is not answering on 127.0.0.1:15151. Start "
                "it and enable its API in Settings."
            ) from exc
        if not 200 <= status < 300:
            last_error = f"AB Download Manager refused a link (HTTP {status})."
            continue
        sent += 1
    if sent == 0:
        raise ExternalDMError(
            last_error or "AB Download Manager did not accept the links."
        )
    return sent


def send_to_jdownloader(
    urls: Iterable[object],
    *,
    source: str = "",
    port: int = JD_PORT,
    timeout: float = 8.0,
) -> int:
    """Post *urls* to JDownloader and return how many links were sent.

    The URLs travel byte for byte (query parameters and fragments included) and
    duplicates are collapsed so a game is not queued twice.  Raises
    :class:`ExternalDMError` when nothing valid was given, the manager is not
    running, or it answers with a refusal.
    """
    clean = _unique_links(urls)
    if not clean:
        raise ExternalDMError("There is no downloadable link to send.")

    payload = urllib.parse.urlencode(
        {
            "urls": "\r\n".join(clean),
            "source": source,
            "passwords": "",
        }
    ).encode("utf-8")
    try:
        status, _body = _request(
            f"http://{JD_HOST}:{int(port)}{JD_ADD_PATH}", data=payload, timeout=timeout
        )
    except urllib.error.HTTPError as exc:
        raise ExternalDMError(
            f"JDownloader refused the links (HTTP {exc.code})."
        ) from exc
    except (urllib.error.URLError, OSError, ValueError, TypeError) as exc:
        raise ExternalDMError(
            "JDownloader is not answering on 127.0.0.1:9666. Start it and make "
            "sure its Click'n'Load/FlashGot interface is enabled."
        ) from exc
    if not 200 <= status < 300:
        raise ExternalDMError(f"JDownloader refused the links (HTTP {status}).")
    return len(clean)


__all__ = [
    "AB_ADD_PATH",
    "AB_HOST",
    "AB_ORIGIN",
    "AB_PING_PATH",
    "AB_PORT",
    "AB_START_PATH",
    "ExternalDMError",
    "JD_ADD_PATH",
    "JD_CHECK_PATH",
    "JD_HOST",
    "JD_ORIGIN",
    "JD_PORT",
    "MANAGERS",
    "NOT_AFFILIATED",
    "ab_download_manager_running",
    "jdownloader_running",
    "send_to_ab_download_manager",
    "send_to_jdownloader",
]
