# Houndarr

<p align="center">
  <img src="src/houndarr/static/img/houndarr-logo-dark.png" alt="Houndarr logo" width="220" />
</p>

> A focused, self-hosted companion for Sonarr and Radarr that automatically searches for missing media in polite, controlled batches.

**Status:** Under active development — not yet production-ready.

---

## What it does

Sonarr and Radarr monitor RSS feeds for new releases, but they don't go back and actively search for content already in your library that's missing or below your quality cutoff. Their "Search All Missing" button fires every item at once, overwhelming indexer API limits.

Houndarr searches slowly, politely, and automatically: small batches, configurable sleep intervals, per-item cooldowns, hourly API caps, and quiet hours.

## Quick Start

```yaml
services:
  houndarr:
    image: ghcr.io/av1155/houndarr:latest
    container_name: houndarr
    restart: unless-stopped
    ports:
      - "8877:8877"
    volumes:
      - ./data:/data
    environment:
      - TZ=America/New_York
      - PUID=1000
      - PGID=1000
```

Full documentation coming in v1.0.0.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development workflow and quality gates.

## Security

See [SECURITY.md](SECURITY.md) to report vulnerabilities responsibly.

## License

MIT — see [LICENSE](LICENSE).
