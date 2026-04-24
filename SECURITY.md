# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 1.x.x   | Yes       |
| < 1.0   | No        |

Security fixes target the latest release on `main` and the most recent
published container image tag.

For details on how Houndarr handles credentials, encryption, and network
behavior, see [Security Overview](https://av1155.github.io/houndarr/docs/security/overview).

## Transparency

- No telemetry, analytics, or phone-home endpoints. The only outbound
  connections Houndarr initiates are to the \*arr URLs you configure.
- Source is fully open. Every release tag on GitHub maps to a container
  image published on GHCR; no binary-only distribution channel.
- Code reviewed through the standard PR + required-check flow. No
  direct pushes to `main`; no unreviewed diffs in release branches.
- API keys for every configured \*arr are Fernet-encrypted at rest
  (AES-128-CBC + HMAC-SHA256) and never exposed to the browser.
- Session cookies are signed with a per-install secret that rotates
  on password change.

## Polite by default

- Conservative out-of-the-box cadence: small batch per cycle, long
  sleep interval, tight per-pass hourly caps. Configured caps are
  the ceiling, not the floor.
- Respects each \*arr's configured `queue_limit` as a backpressure
  gate; cycles skip when the download queue is at or above the limit.
- Respects per-item cooldowns (missing / cutoff / upgrade use distinct
  cooldown days) so the same item never gets re-hammered.
- Honors `post_release_grace_hrs` so freshly-released items aren't
  searched the moment their timestamp crosses zero.
- Optional per-instance `allowed_time_window` gates scheduled cycles
  to one or more hour ranges.
- Skip-log throttle (in-memory, 24-hour per-key LRU) suppresses
  duplicate cooldown-reason rows so the audit trail stays scannable
  on installs with hundreds of items sharing one cooldown.

## Network surface

- **Outbound**: only to the \*arr instance URLs you configure. No
  third-party service is contacted at runtime.
- **Inbound**: a single HTTP server on the port you configure (8877
  by default). No additional listeners, metrics endpoints, or
  debug ports.
- SSRF guard on the \*arr URL field blocks loopback / link-local /
  unspecified targets at configuration time.
- Container runs as non-root after PUID / PGID remapping.

## Scope

**In scope.** Triggering missing / cutoff-unmet / upgrade searches
against configured \*arr instances, rate-limited per-instance, with
a web UI for status and audit.

**Out of scope (deliberate).** Download-client management, indexer
management, request workflows, multi-user support, media file
manipulation, built-in Usenet or torrent clients, Prowlarr
integration, Plex OAuth, or anything that expands Houndarr beyond
its single-purpose search-companion role.

## Reporting a vulnerability

Please do not open public GitHub issues for suspected vulnerabilities.

Instead, report privately using GitHub's security reporting flow:

- https://github.com/av1155/houndarr/security/advisories/new

Include as much detail as possible:

- affected version/tag or commit
- environment (Docker version, host OS)
- steps to reproduce
- impact assessment
- proof-of-concept (if available)

## Response expectations

- Initial acknowledgement target: **within 72 hours**
- Triage and severity assessment: as soon as reasonably possible
- Fix timeline depends on severity and exploitability

When a fix is available, a release note/changelog entry will document the
resolution.
