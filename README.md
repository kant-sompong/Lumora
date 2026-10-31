# Rust Media Library

A native Linux desktop media library built with Rust. The project aims to bring movies, TV shows, and anime discovery into one convenient place, with metadata, cover artwork, and playback integration.

> **Project status:** Early-stage starter repository. Features listed below are goals, not claims that they are already implemented.

## Planned features

- Browse movies and TV shows
- Discover anime and view anime metadata
- Display posters, cover art, and backdrops
- Keep a local library and user preferences
- Integrate playback where a supported source is available
- Work well on Linux desktops

## Planned technology

- Rust stable
- GTK4 + Relm4 for the desktop interface
- Serde for data serialization
- Reqwest for HTTP requests
- SQLite for local metadata and library data

The exact stack may change as the project develops.

## Development

Install Rust using the official instructions at <https://www.rust-lang.org/tools/install>, then install the GTK4 development packages required by your Linux distribution.

Build and test:

```bash
cargo check
cargo test
cargo run
```

These commands become usable once the application dependencies and implementation are added.

## Safety and privacy

- Do not commit API keys, access tokens, passwords, cookies, or private account data.
- Keep local configuration and cache files out of version control.
- Use APIs and playback sources only in accordance with their terms and applicable law.

## Contributing

Issues and pull requests are welcome. Describe bugs with reproduction steps and include relevant logs after removing secrets.

## License

No license has been selected yet. Until one is added, assume that the code is not licensed for reuse beyond the rights granted by applicable law.
