# Lumora 0.6.29

The phone's Settings page grows the rest of the desktop's playback choices: the
provider modes, a default audio track and autoplay. Nothing changes on the
desktop, and the phone's catalogue, search, calendar and player behave as
before.

## New

### Provider modes

- **Two switches, one setting.** "Choose the fastest provider automatically" and
  "I pick the provider myself" are the same choice seen twice, as in the desktop
  settings: switching one on switches the other off, and the stored preference
  is the one the player reads.
- **Picking a provider yourself turns the automatic choice off.** Choosing a
  default with the provider list's radio control keeps that provider for
  playback instead of letting a health check move it.

### Playback

- **Default audio: Sub, Dub or VOSTFR.** The anime provider's embed is asked for
  the chosen track, and an unknown or missing value falls back to Sub. Providers
  whose embed has no language segment are unaffected.
- **Autoplay on or off,** under the same name as the desktop setting. With
  autoplay off the phone no longer lets the provider's page start the video by
  itself: playback waits for a tap on the provider's own play button. The
  player, its panel, fullscreen and the provider list are unchanged.

## Changed

- **The About row reads 0.6.29,** and the app version follows the desktop
  release.

## Known limits

- **The audio track reaches only the anime provider.** It is the one embed route
  that carries a language segment; the other providers play whatever track their
  embed serves.
- **Autoplay is the phone's own gesture rule,** not a second player: with it off
  a provider that ignores the rule can still start playing.
- **The Android build is signed with a debug key.** It is installed by
  sideloading the APK, not through a store, so updates have to be sideloaded the
  same way.

## Install

**Windows** — download `Lumora-Setup-x64.exe` (per-user installer, no
administrator prompt) or the portable `Lumora-Windows-x64.zip` and run
`Lumora.exe` from the unzipped folder. The executables are unsigned, so
SmartScreen may warn: choose **More info → Run anyway**.

**Linux** — download `Lumora-0.6.29-linux-x86_64.tar.gz`, extract it and run
`./run.sh`. It needs Python 3.10+, GTK 3 and PyGObject; WebKit2GTK and the
GStreamer plugins are optional and only used for embedded playback.

**Android** — download `Lumora-0.6.29-android.apk` and open it on the phone,
allowing installs from your browser or file manager when Android asks. The APK
is signed with a debug key, so Play Protect may ask you to confirm the install.
