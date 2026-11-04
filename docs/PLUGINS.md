# Plugins

A Lumora plugin is a **JSON manifest**. It describes where metadata can come
from and what the plugin is allowed to touch. Lumora performs the requests, so no
third-party code runs inside the app — which is also why a manifest that asks to
execute code is refused rather than sandboxed.

## Location

Drop a `lumora-plugin.json` into the plugins folder (or one level below it, one
folder per plugin):

```
<config dir>/plugins/
├── lumora-plugin.json
└── extras/lumora-plugin.json
```

Up to 40 plugins are loaded; anything beyond that is reported as rejected.

## Manifest

```json
{
  "key": "example-extras",
  "name": "Example extras",
  "version": "1.0.0",
  "api": 1,
  "kind": "metadata-source",
  "summary": "Extra artwork for films",
  "endpoint": "https://example.invalid/api",
  "permissions": ["network"],
  "author": "Someone",
  "homepage": "https://example.invalid"
}
```

| Field | Required | Notes |
| --- | --- | --- |
| `key` | yes | lowercase letters, digits, `.`, `-`, `_`; unique |
| `name` | yes | shown in the UI |
| `version` | yes | `1.2.3` style |
| `api` | yes | must equal `1` (`plugins.API_VERSION`) |
| `kind` | yes | `metadata-source`, `catalogue` or `integration` |
| `endpoint` | for metadata sources | must be `https://` |
| `permissions` | no | subset of `network`, `files` |

## Permissions

- `network` — Lumora may call the declared https endpoint. Required whenever
  `endpoint` is present.
- `files` — the plugin may read folders the user explicitly chose.

A plugin with no permissions cannot reach anything.

## What is refused

- `entry`, `entrypoint`, `script`, `command`, `exec`, `module`, `main` — any
  field that asks Lumora to run code.
- Plain `http://` endpoints.
- Unknown kinds or permissions.
- Manifests over 64 KiB.
- Duplicate keys (the first one wins, the rest are listed as rejected).

## Trust model

Plugin responses are untrusted data. Titles, artwork URLs and identifiers are
validated before they reach the library, and a plugin cannot write to the
library, settings or history directly. Removing a plugin is deleting its folder;
nothing else has to be undone.
