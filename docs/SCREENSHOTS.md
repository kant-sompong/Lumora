# Screenshots

Every screen of the current build, captured on Linux (GTK 3) unless marked
otherwise. Files live in [`screens`](screens); regenerate them for a UI change so
review stays easy.

## Browse

The default view: cover grid, filters on the left, details on the right.

![Browse grid](screens/browse-grid.png)

| Wide covers | Compact list | Details panel with a selected game |
| :---: | :---: | :---: |
| ![Wide covers](screens/browse-covers.png) | ![List view](screens/browse-list.png) | ![Library detail](screens/library-detail.png) |

## Library and downloads

| Library grid | Download queue | Website sources |
| :---: | :---: | :---: |
| ![Library](screens/library.png) | ![Downloads](screens/downloads.png) | ![Website sources](screens/website-sources.png) |

## Movies & TV

| Home | Movies | TV Shows |
| :---: | :---: | :---: |
| ![Media home](screens/media-home.png) | ![Movies](screens/media-movies.png) | ![TV Shows](screens/media-tv.png) |

| Anime | My List | History |
| :---: | :---: | :---: |
| ![Anime](screens/media-anime.png) | ![My List](screens/media-list.png) | ![History](screens/media-history.png) |

## Settings

| General | Locations | Providers |
| :---: | :---: | :---: |
| ![General](screens/settings-general.png) | ![Locations](screens/settings-locations.png) | ![Providers](screens/settings-providers.png) |

| Media & playback | Shortcuts |
| :---: | :---: |
| ![Media settings](screens/settings-media.png) | ![Shortcuts](screens/settings-shortcuts.png) |

## Catalogue import

![Kryoto catalogue](screens/kryoto-catalog.png)

## Windows build

The Qt 6 frontend shares the whole Python core with the Linux build, so the rail,
header, sections and theme tokens match.

| Browse with a selected game | Library with a selected game | Providers |
| :---: | :---: | :---: |
| ![Windows browse](screens/qt-browse-catalogue.png) | ![Windows library](screens/qt-library-selection.png) | ![Windows settings](screens/windows-settings.png) |

![Downloads](screens/qt-downloads.png)

## Tools window (Windows)

`docs/screens/qt-tools.png` — the shared tools window, captured from the Qt build
with `QT_QPA_PLATFORM=offscreen`. Every platform renders the same sections from
`lgl/tool_panels.py`.
