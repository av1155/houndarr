# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 1.x.x   | Yes       |
| < 1.0   | No        |

Security fixes target the latest release on `main` and the most recent
published container image tag.

For details on how Houndarr handles credentials, encryption, and network
behavior, see [Trust & Security](https://av1155.github.io/houndarr/docs/security/trust-and-security).

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
