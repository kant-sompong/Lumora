"""Declarative plugin manifests.

Roadmap section 14: define a stable interface, allow extra metadata sources,
validate manifests and versions, document permissions and security, and never
execute untrusted code without an explicit secure design.

The design choice here is the security answer: a plugin is *data*.  It declares
where metadata comes from and what it is allowed to touch; Lumora does the
talking.  A manifest that asks to run code is refused, not sandboxed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

MANIFEST_NAME = "lumora-plugin.json"
API_VERSION = 1
MAX_MANIFEST_BYTES = 64 * 1024
MAX_PLUGINS = 40

KINDS = ("metadata-source", "catalogue", "integration")
PERMISSIONS = ("network", "files")
EXECUTABLE_FIELDS = ("entry", "entrypoint", "script", "command", "exec", "module", "main")

_KEY = re.compile(r"\A[a-z0-9][a-z0-9._-]{1,63}\Z")
_SEMVER = re.compile(r"\A\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?\Z")


@dataclass(frozen=True)
class Plugin:
    key: str
    name: str
    version: str
    api: int
    kind: str
    summary: str = ""
    endpoint: str = ""
    permissions: tuple[str, ...] = ()
    author: str = ""
    homepage: str = ""
    path: Path | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "version": self.version,
            "api": self.api,
            "kind": self.kind,
            "summary": self.summary,
            "endpoint": self.endpoint,
            "permissions": list(self.permissions),
            "author": self.author,
            "homepage": self.homepage,
        }

    @property
    def compatible(self) -> bool:
        return self.api == API_VERSION

    @property
    def needs_network(self) -> bool:
        return "network" in self.permissions


@dataclass
class Rejected:
    path: Path | None
    reason: str


def _clean(value: Any, limit: int = 200) -> str:
    return str(value or "").strip()[:limit]


def validate(manifest: Any) -> list[str]:
    """Every problem with a manifest, in the order a user would fix them."""
    problems: list[str] = []
    if not isinstance(manifest, Mapping):
        return ["The manifest is not a JSON object."]
    for name in EXECUTABLE_FIELDS:
        if name in manifest:
            problems.append(
                f"“{name}” asks Lumora to run code; plugins are declarative and cannot execute anything."
            )
    key = _clean(manifest.get("key"), 64)
    if not key:
        problems.append("A “key” is required.")
    elif not _KEY.match(key):
        problems.append("The “key” may only use lowercase letters, digits, dots, dashes and underscores.")
    if not _clean(manifest.get("name"), 120):
        problems.append("A “name” is required.")
    version = _clean(manifest.get("version"), 40)
    if not version:
        problems.append("A “version” is required.")
    elif not _SEMVER.match(version):
        problems.append("The “version” must look like 1.2.3.")
    api = manifest.get("api")
    if not isinstance(api, int) or isinstance(api, bool):
        problems.append("“api” must be a number.")
    elif api != API_VERSION:
        problems.append(f"This manifest targets API {api}; Lumora speaks API {API_VERSION}.")
    kind = _clean(manifest.get("kind"), 40)
    if kind not in KINDS:
        problems.append(f"“kind” must be one of {', '.join(KINDS)}.")
    permissions = manifest.get("permissions", [])
    if not isinstance(permissions, list):
        problems.append("“permissions” must be a list.")
    else:
        for item in permissions:
            if str(item) not in PERMISSIONS:
                problems.append(f"Unknown permission “{item}”; allowed: {', '.join(PERMISSIONS)}.")
    endpoint = _clean(manifest.get("endpoint"), 300)
    if endpoint:
        if not endpoint.lower().startswith("https://"):
            problems.append("“endpoint” must be an https:// URL.")
        if "network" not in [str(p) for p in permissions if isinstance(permissions, list)]:
            problems.append("A plugin with an endpoint must declare the “network” permission.")
    if kind == "metadata-source" and not endpoint:
        problems.append("A metadata source needs an “endpoint”.")
    return problems


def parse(manifest: Any, path: Path | None = None) -> tuple[Plugin | None, list[str]]:
    problems = validate(manifest)
    if problems:
        return None, problems
    assert isinstance(manifest, Mapping)
    return (
        Plugin(
            key=_clean(manifest.get("key"), 64),
            name=_clean(manifest.get("name"), 120),
            version=_clean(manifest.get("version"), 40),
            api=int(manifest.get("api", API_VERSION)),
            kind=_clean(manifest.get("kind"), 40),
            summary=_clean(manifest.get("summary"), 300),
            endpoint=_clean(manifest.get("endpoint"), 300),
            permissions=tuple(_clean(item, 20) for item in manifest.get("permissions", [])),
            author=_clean(manifest.get("author"), 120),
            homepage=_clean(manifest.get("homepage"), 300),
            path=path,
        ),
        [],
    )


def load_file(path: Path | str) -> tuple[Plugin | None, list[str]]:
    target = Path(path)
    try:
        size = target.stat().st_size
    except OSError:
        return None, [f"{target.name}: the file could not be read."]
    if size > MAX_MANIFEST_BYTES:
        return None, [f"{target.name}: manifests may not exceed {MAX_MANIFEST_BYTES} bytes."]
    try:
        manifest = json.loads(target.read_text("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None, [f"{target.name}: not valid JSON."]
    return parse(manifest, target)


def discover(roots: Iterable[Path | str]) -> tuple[list[Plugin], list[Rejected]]:
    """Find manifests one directory deep under each root."""
    found: list[Plugin] = []
    rejected: list[Rejected] = []
    seen: set[str] = set()
    for root in roots:
        base = Path(root)
        if not base.is_dir():
            continue
        candidates = [base / MANIFEST_NAME] + sorted(base.glob(f"*/{MANIFEST_NAME}"))
        for candidate in candidates:
            if not candidate.is_file():
                continue
            plugin, problems = load_file(candidate)
            if plugin is None:
                rejected.append(Rejected(candidate, "; ".join(problems)))
                continue
            if plugin.key in seen:
                rejected.append(Rejected(candidate, f"Duplicate plugin key “{plugin.key}”."))
                continue
            if len(found) >= MAX_PLUGINS:
                rejected.append(Rejected(candidate, f"Only {MAX_PLUGINS} plugins can be loaded."))
                continue
            seen.add(plugin.key)
            found.append(plugin)
    return found, rejected


def permission_summary(plugin: Plugin) -> str:
    if not plugin.permissions:
        return "Needs no access."
    bits = []
    if "network" in plugin.permissions:
        bits.append(f"contacts {plugin.endpoint or 'a remote service'}")
    if "files" in plugin.permissions:
        bits.append("reads files you point it at")
    return "This plugin " + " and ".join(bits) + "."


def security_notes() -> list[str]:
    return [
        "Plugins are declarative JSON manifests: Lumora performs every request, so no third-party code runs in the app.",
        "A manifest that asks to execute code is refused instead of sandboxed.",
        "“network” lets the plugin reach its declared https endpoint; “files” lets it read folders you choose.",
        "Manifests are capped at 64 KiB and 40 plugins so a broken one cannot stall startup.",
        "Anything a plugin returns is treated as untrusted data: titles and artwork are validated before display.",
    ]


__all__ = [
    "API_VERSION",
    "EXECUTABLE_FIELDS",
    "KINDS",
    "MANIFEST_NAME",
    "MAX_MANIFEST_BYTES",
    "MAX_PLUGINS",
    "PERMISSIONS",
    "Plugin",
    "Rejected",
    "discover",
    "load_file",
    "parse",
    "permission_summary",
    "security_notes",
    "validate",
]
