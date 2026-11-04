# Backup format

A backup is a single zip file. Nothing outside the archive is touched until a
restore has verified every part, and API keys are never written into it.

```
lumora-backup.zip
├── manifest.json      format version, app version, one entry per part
├── library.json       library metadata and installed games
├── collections.json   custom and smart collections
├── settings.json      preferences, with "api_keys" removed
└── history.json       watch history and continue-watching positions
```

## manifest.json

```json
{
  "version": 1,
  "app_version": "0.6.6",
  "created": 1794000000.0,
  "parts": {
    "library.json": {"size": 20480, "sha256": "9f2c…"},
    "settings.json": {"size": 1024, "sha256": "41ad…"}
  }
}
```

Every part carries its own size and SHA-256. `backup.inspect()` reads this
manifest without extracting anything, so the restore dialog can show what a file
contains before the user commits to it.

## What is deliberately absent

- `api_keys` is stripped from `settings.json` by `backup.sanitize_settings()`.
- Torrent session state, partial downloads and artwork caches are not included;
  they are reproducible and can be large.
- Logs are not included. Use the support report for those.

## Restoring

`backup.load()` verifies the manifest, the size of each part and its hash before
returning anything. A part that fails verification aborts the whole restore
instead of half-applying it.

`backup.restore_plan()` then labels each part `add` or `overwrite`, which is what
the frontends show before asking for confirmation.

## Compatibility

The format version is `1`. A backup written by a newer format version is
refused, with the version named in the message, rather than being partially
read. Backups do not depend on the app version: a file from 0.6.5 restores into
0.6.6 as long as the format version matches.

Settings keys that a newer release no longer knows are dropped on restore; keys
it has added keep their defaults. That is the same rule the settings migration
uses on startup, so restoring an old backup cannot resurrect a stale structure.
