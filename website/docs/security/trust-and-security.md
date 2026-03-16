---
sidebar_position: 1
title: Trust & Security
description: How Houndarr handles credentials, network behavior, and trust boundaries.
---

# Trust & Security

This document explains how Houndarr handles sensitive credentials, network
behavior, and trust boundaries. Every claim is based on the actual source code.
Where limitations exist, they are stated plainly.

If you find a discrepancy between this document and the code, please
[report it](https://github.com/av1155/houndarr/security/advisories/new).

## Does Houndarr call home?

**No.** The Houndarr server makes zero outbound connections to any developer,
analytics, telemetry, or third-party service. There are no update checks, no
version polling, no usage reporting, no crash reporting, and no beacons.

### What the server connects to

The only outbound HTTP connections are to **your own Sonarr and Radarr
instances**, using their standard v3 API. Each request includes:

- An `X-Api-Key` header (the API key you configured for that instance)
- An `Accept: application/json` header
- Standard API parameters (pagination, search command IDs)

Nothing about Houndarr itself, your configuration, or your usage is included in
these requests.

### What the server does not connect to

- No analytics services (Google Analytics, Mixpanel, Amplitude, Segment, etc.)
- No error tracking services (Sentry, Bugsnag, Datadog, etc.)
- No developer-controlled servers
- No package registries, update servers, or version-check endpoints
- No webhooks or notification services

### Browser-side CDN resources

The web UI loads two JavaScript libraries from external CDNs:

- **Tailwind CSS** from `cdn.tailwindcss.com` (Play CDN)
- **HTMX 2.0.4** from `unpkg.com` (pinned version)

These are fetched by **your browser**, not by the Houndarr server. The CDN
providers may log standard request metadata (IP address, User-Agent) from the
browser.

## How API keys are protected

### Encryption at rest

API keys are encrypted before being written to the database using
[Fernet](https://cryptography.io/en/latest/fernet/) symmetric encryption from
the `cryptography` library. Fernet uses AES-128 in CBC mode for
confidentiality and HMAC-SHA256 for integrity and tamper detection.

The database column is named `encrypted_api_key`. Plaintext API keys are never
stored on disk.

### The master key

A Fernet master key is generated automatically on first startup using
`Fernet.generate_key()`, which calls `os.urandom(32)` (the operating system's
cryptographically secure random number generator).

The key is stored at `<data_dir>/houndarr.masterkey` (typically
`/data/houndarr.masterkey` in Docker). It is:

- Created atomically with `O_CREAT | O_EXCL` to prevent race conditions
- Written with file permissions `0o600` (owner read/write only)
- Permissions are enforced explicitly even if the process umask would allow
  broader access

The master key is loaded into memory once at application startup and passed
explicitly to service functions. It is never written to logs, HTTP responses,
or template output.

:::danger
**The master key file is critical.** Anyone with access to both
`houndarr.masterkey` and `houndarr.db` can decrypt all stored API keys. See
[Protecting your data directory](#protecting-your-data-directory) below.
:::

### API keys in the web UI

When you edit an existing instance, the API key field displays a placeholder
sentinel value, not the actual key. The input field uses `type="password"` so
the placeholder is masked.

The decrypted API key is never included in any HTTP response body, HTML template
output, or JSON API payload.

## Authentication and session security

### Password storage

Houndarr uses a single-admin authentication model. The admin password is hashed
with **bcrypt at cost factor 12**. Plaintext passwords are never stored.

### Sessions

Sessions use signed tokens via `itsdangerous.URLSafeTimedSerializer` with an
HMAC signature. The signing secret is a 64-character hex string generated from
`os.urandom(32)` on first setup.

The session token contains only a creation timestamp and a CSRF nonce. It does
not contain the username, password, API keys, or any other sensitive data.

Session tokens expire after **24 hours**, enforced server-side.

### Cookies

| Cookie | HttpOnly | SameSite | Secure | Purpose |
|--------|----------|----------|--------|---------|
| `houndarr_session` | Yes | Strict | Configurable | Session authentication |
| `houndarr_csrf` | No | Strict | Configurable | CSRF token for HTMX/JS |

The CSRF cookie is intentionally not `HttpOnly` because HTMX needs to read it
to include the token in request headers.

The `Secure` flag is controlled by `HOUNDARR_SECURE_COOKIES` (default: `false`).

### CSRF protection

All state-changing requests (POST, PUT, PATCH, DELETE) require a valid CSRF
token. Token comparison uses `hmac.compare_digest()` to prevent timing attacks.

### Login rate limiting

A brute-force limiter allows **5 failed login attempts per IP address within a
60-second sliding window**. After the limit is reached, further attempts return
HTTP 429.

Login error messages are generic ("Invalid credentials") and do not reveal
whether the username or password was incorrect.

## Container security posture

### Non-root execution

The Docker container starts as root solely to perform PUID/PGID remapping.
After remapping, the entrypoint uses `gosu` to drop to the non-root `appuser`.
The Houndarr process itself never runs as root.

### No added capabilities

The Dockerfile does not add any Linux capabilities. No `--privileged` flag. No `CAP_ADD`.

## Network trust boundaries

### Plain HTTP

Houndarr serves plain HTTP and does not terminate TLS. Run it behind a reverse
proxy for HTTPS. See [Reverse Proxy](/docs/configuration/reverse-proxy).

### Instance URL validation (SSRF protection)

When adding or editing an instance URL, Houndarr validates the target:

- **Blocked:** `localhost`, loopback IPs (`127.0.0.0/8`, `::1`), link-local
  (`169.254.0.0/16`), and unspecified addresses (`0.0.0.0`)
- **Allowed:** RFC-1918 private ranges (`10.0.0.0/8`, `172.16.0.0/12`,
  `192.168.0.0/16`) because Sonarr/Radarr typically run on the same LAN or
  Docker network

Hostnames are resolved via DNS and each resolved IP is checked against the
blocked ranges to prevent DNS rebinding.

## Protecting your data directory

The persistent data directory (default `/data` in Docker) contains:

| File | Contents | Sensitivity |
|------|----------|-------------|
| `houndarr.db` | SQLite database with password hash, session secret, encrypted API keys, search logs | High |
| `houndarr.masterkey` | Fernet encryption key for API keys | Critical |

### Backup guidance

- **Back up the entire data directory** (`houndarr.db` and `houndarr.masterkey`
  together). The database cannot be used to decrypt API keys without the
  matching master key.
- **If the master key file is lost**, all stored API keys become unrecoverable.
  You will need to re-enter the API key for each instance.
- **Treat the data directory as sensitive.**

## Deployment hardening checklist

- [ ] Run behind a reverse proxy with TLS termination
- [ ] Set `HOUNDARR_SECURE_COOKIES=true`
- [ ] Set `HOUNDARR_TRUSTED_PROXIES` to your proxy IP(s)
- [ ] Do not expose port 8877 directly to the internet without a proxy
- [ ] Do not run with `HOUNDARR_DEV=true` in production
- [ ] Back up the `/data` volume regularly
- [ ] Restrict file permissions on the data directory to the container user
- [ ] Keep the Docker image updated for security patches

## Known limitations

These are honest trade-offs in the current implementation, appropriate for the
single-admin self-hosted deployment model.

**Stateless sessions.** Sessions are signed tokens with no server-side session
store. Logout deletes the cookie client-side, but a stolen token remains valid
until its 24-hour expiry.

**In-memory rate limiter.** The login brute-force limiter resets when the
application restarts.

**Decrypted keys in process memory.** When the application loads instance data,
the decrypted API key exists in Python process memory. Templates never render
this value and no HTTP response includes it.

**CDN dependency.** The Tailwind CSS Play CDN script is not pinned to a specific
version. The HTMX script is pinned to version 2.0.4. If either CDN is
unavailable, the UI will not render correctly.

**Private network ranges allowed.** Instance URL validation intentionally
permits RFC-1918 private addresses because Sonarr and Radarr are typically on
the same LAN or Docker network.

## CI security pipeline

Every pull request runs automated security checks before merge:

| Tool | Purpose |
|------|---------|
| [Bandit](https://bandit.readthedocs.io/) | Static application security testing (SAST) for Python |
| [pip-audit](https://pypi.org/project/pip-audit/) | Known vulnerability scanning for Python dependencies |
| [Hadolint](https://github.com/hadolint/hadolint) | Dockerfile best-practice linting |
| [actionlint](https://github.com/rhysd/actionlint) | GitHub Actions workflow linting |
