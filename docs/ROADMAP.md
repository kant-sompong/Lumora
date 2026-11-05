# Roadmap & known gaps

What Lumora deliberately does not do yet, and what it does instead.

- No macOS frontend.
- No automatic updater. *Check for updates…* reads the latest GitHub release and
  opens its page when a newer version exists; nothing is downloaded, unpacked or
  started by the app. `updates.verify_artifact` only checks a file **you** already
  downloaded against its published SHA-256, and `updates.manual_instructions`
  prints the step that follows.
- No storefront account integration. Steam, Epic and GOG libraries are folders you
  point at (Tools → Local files and game sources, or Browse → filters). Lumora
  never signs in to a storefront, makes no entitlement checks and never touches a
  launcher's database. itch.io is not supported at all — there is no folder
  convention to rely on. Covers can come from Steam's public image CDN, which
  needs no key and no account.
- No automatic game launch when a download finishes. A finished download is a
  finished download; launching stays a click.
- Plugins are declarative JSON: labels, links and suggested folders. They cannot
  execute code, and a rejected file is listed with the reason instead of being
  loaded.
- Watch-history sync ships one adapter, a local JSON file. No third-party account
  is signed in to and no vendor API is called.
- Linux is GTK 3 only. There is no GTK 4 build, and GTK 3 and 4 cannot be loaded
  into one process, so every new Linux window must use the GTK 3 API.
- Windows builds are compiled and smoke-tested in CI, but interactive testing on
  physical hardware is done by users.
