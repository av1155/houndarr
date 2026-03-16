# Trust & Security

This document explains how Houndarr handles sensitive credentials, network
behavior, and trust boundaries. Every claim below is based on the actual source
code. Where limitations exist, they are stated plainly.

If you find a discrepancy between this document and the code, please
[report it](../SECURITY.md).

---

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
these requests. The HTTP client library used is `httpx`; no other HTTP library
is present in the source tree.

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
browser, as is normal for any CDN-hosted resource. The Houndarr server itself
never contacts these CDNs.

A footer link to the Houndarr GitHub repository is present in the UI but is
only loaded if you explicitly click it.

---

## How API keys are protected

### Encryption at rest

API keys are encrypted before being written to the database using
[Fernet](https://cryptography.io/en/latest/fernet/) symmetric encryption from
the `cryptography` library. Fernet uses AES-128 in CBC mode for
confidentiality and HMAC-SHA256 for integrity and tamper detection.

The database column is named `encrypted_api_key`. Plaintext API keys are never
stored on disk.

Relevant source files:
- `src/houndarr/crypto.py` -- `encrypt()` and `decrypt()` functions
- `src/houndarr/services/instances.py` -- instance CRUD with encryption on
  write and decryption on read

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

**The master key file is critical.** Anyone with access to both
`houndarr.masterkey` and `houndarr.db` can decrypt all stored API keys. See
[Protecting your data directory](#protecting-your-data-directory) below.

### API keys in the web UI

When you edit an existing instance, the API key field displays a placeholder
sentinel value (`__UNCHANGED__`), not the actual key. The input field uses
`type="password"` so the placeholder is masked.

When the form is submitted:
- If the sentinel is still present, the server keeps the existing encrypted key
  unchanged.
- If a new value was entered, the server encrypts and stores the new key.

The decrypted API key is never included in any HTTP response body, HTML template
output, or JSON API payload. Specifically:

- The `/api/status` endpoint constructs its response by selecting specific
  fields; `api_key` is excluded.
- The settings page templates render instance name, type, URL, and scheduling
  parameters but never reference the `api_key` field.
- The `/api/health` endpoint returns only `{"status": "ok"}`.

Relevant source files:
- `src/houndarr/routes/settings.py` -- sentinel constant and resolution logic
- `src/houndarr/templates/partials/instance_form.html` -- form pre-fills
  sentinel, not the key
- `src/houndarr/routes/api/status.py` -- field-level selection omitting
  `api_key`

---

## Authentication and session security

### Password storage

Houndarr uses a single-admin authentication model. The admin password is hashed
with **bcrypt at cost factor 12** and stored in the SQLite `settings` table.
Plaintext passwords are never stored.

### Sessions

Sessions use signed tokens via `itsdangerous.URLSafeTimedSerializer` with an
HMAC signature. The signing secret is a 64-character hex string generated from
`os.urandom(32)` on first setup, stored in the database.

The session token payload contains only a creation timestamp and a CSRF nonce.
It does not contain the username, password, API keys, or any other sensitive
data.

Session tokens expire after **24 hours**, enforced server-side during
validation.

### Cookies

| Cookie | HttpOnly | SameSite | Secure | Purpose |
|--------|----------|----------|--------|---------|
| `houndarr_session` | Yes | Strict | Configurable | Session authentication |
| `houndarr_csrf` | No | Strict | Configurable | CSRF token for HTMX/JS |

The CSRF cookie is intentionally not `HttpOnly` because HTMX needs to read it
to include the token in request headers.

The `Secure` flag on both cookies is controlled by the `HOUNDARR_SECURE_COOKIES`
environment variable. It defaults to `false` because Houndarr serves plain HTTP
and expects HTTPS to be terminated by a reverse proxy. Set it to `true` when
running behind HTTPS.

### CSRF protection

All state-changing requests (POST, PUT, PATCH, DELETE) require a valid CSRF
token. The expected token is embedded inside the HMAC-signed session cookie, so
it cannot be forged without the signing secret. Token comparison uses
`hmac.compare_digest()` to prevent timing attacks.

The only intentional CSRF exemption is `POST /logout`, which allows stale
sessions to be cleared even when the CSRF token has expired.

### Login rate limiting

A brute-force limiter allows **5 failed login attempts per IP address within a
60-second sliding window**. After the limit is reached, further attempts return
HTTP 429.

Login error messages are generic ("Invalid credentials") and do not reveal
whether the username or password was incorrect.

`X-Forwarded-For` is only honored when the connecting IP is listed in
`HOUNDARR_TRUSTED_PROXIES`. When no trusted proxies are configured (the
default), the header is ignored entirely, preventing IP spoofing.

### Unauthenticated routes

Only these paths are accessible without authentication:

| Path | Purpose |
|------|---------|
| `/setup` | First-run setup (disabled after setup completes) |
| `/login` | Login form |
| `/api/health` | Health check (returns only `{"status": "ok"}`) |
| `/static/*` | Static assets (CSS, JS, images) |

No unauthenticated route exposes configuration, API keys, instance data, or
any information beyond a static health status.

---

## Container security posture

### Non-root execution

The Docker container starts as root solely to perform PUID/PGID remapping
(matching container file ownership to host user IDs). After remapping, the
entrypoint uses `gosu` to drop to the non-root `appuser` and exec the
application. The Houndarr process itself never runs as root.

### No added capabilities

The Dockerfile does not add any Linux capabilities. No `--privileged` flag. No
`CAP_ADD`.

### PUID/PGID

The container supports `PUID` and `PGID` environment variables (defaulting to
`1000`) to remap file ownership, following the same pattern used by
Linuxserver.io and similar self-hosted container images.

### Health check

The Docker `HEALTHCHECK` polls `http://localhost:8877/api/health` inside the
container. This endpoint is intentionally unauthenticated and returns only
`{"status": "ok"}`.

---

## Network trust boundaries

### Plain HTTP

Houndarr serves plain HTTP. It does not terminate TLS. If you access Houndarr
over a network (rather than localhost), you should run it behind a reverse
proxy (Nginx, Caddy, Traefik, etc.) that terminates HTTPS.

Without HTTPS, session cookies and login credentials are transmitted in
cleartext on the network.

### Reverse proxy configuration

When running behind a reverse proxy with HTTPS:

1. Set `HOUNDARR_SECURE_COOKIES=true` so cookies are only sent over HTTPS.
2. Set `HOUNDARR_TRUSTED_PROXIES` to your proxy's IP so the rate limiter sees
   real client IPs via `X-Forwarded-For`.

### Instance URL validation (SSRF protection)

When adding or editing an instance URL, Houndarr validates the target:

- **Blocked:** `localhost`, loopback IPs (`127.0.0.0/8`, `::1`), link-local
  (`169.254.0.0/16`), and unspecified addresses (`0.0.0.0`)
- **Allowed:** RFC-1918 private ranges (`10.0.0.0/8`, `172.16.0.0/12`,
  `192.168.0.0/16`) because Sonarr/Radarr typically run on the same LAN or
  Docker network

Hostnames are resolved via DNS and each resolved IP is checked against the
blocked ranges to prevent DNS rebinding to loopback addresses.

---

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
  You will need to re-enter the API key for each instance. No other data is
  affected.
- **Treat the data directory as sensitive.** It contains the master encryption
  key, the bcrypt password hash, and the session signing secret.

---

## Deployment hardening checklist

- [ ] Run behind a reverse proxy with TLS termination
- [ ] Set `HOUNDARR_SECURE_COOKIES=true`
- [ ] Set `HOUNDARR_TRUSTED_PROXIES` to your proxy IP(s)
- [ ] Do not expose port 8877 directly to the internet without a proxy
- [ ] Do not run with `HOUNDARR_DEV=true` in production (it exposes Swagger UI
      at `/api/docs`)
- [ ] Back up the `/data` volume regularly
- [ ] Restrict file permissions on the data directory to the container user
- [ ] Keep the Docker image updated for security patches

---

## Known limitations and caveats

These are honest trade-offs in the current implementation. They are appropriate
for the single-admin self-hosted deployment model but should be understood.

**Stateless sessions.** Sessions are signed tokens with no server-side session
store. Logout deletes the cookie client-side, but a stolen token remains valid
until its 24-hour expiry. There is no server-side revocation mechanism.

**In-memory rate limiter.** The login brute-force limiter resets when the
application restarts. It is not persisted to disk.

**Decrypted keys in process memory.** When the application loads instance data
(for the settings page, status polling, or search execution), the decrypted API
key exists in Python process memory as part of the `Instance` object. Templates
never render this value and no HTTP response includes it, but a process memory
dump could theoretically expose it. This is inherent to any application that
uses secrets at runtime.

**CDN dependency.** The Tailwind CSS Play CDN script is not pinned to a
specific version. The HTMX script is pinned to version 2.0.4. If either CDN is
unavailable, the UI will not render correctly. Both are loaded by the browser,
not the server.

**Private network ranges allowed.** Instance URL validation intentionally
permits RFC-1918 private addresses because Sonarr and Radarr are typically on
the same LAN or Docker network. This means Houndarr can be directed at any
reachable host on your private network.

---

## CI security pipeline

Every pull request runs automated security checks before merge:

| Tool | Purpose |
|------|---------|
| [Bandit](https://bandit.readthedocs.io/) | Static application security testing (SAST) for Python |
| [pip-audit](https://pypi.org/project/pip-audit/) | Known vulnerability scanning for Python dependencies |
| [Trivy](https://github.com/aquasecurity/trivy) | Vulnerability scanning for the filesystem and Docker image (CRITICAL/HIGH, fixable only) |
| [Dependency Review](https://github.com/actions/dependency-review-action) | PR-time check of new dependencies against the GitHub Advisory Database |
| [Hadolint](https://github.com/hadolint/hadolint) | Dockerfile best-practice linting |
| [actionlint](https://github.com/rhysd/actionlint) | GitHub Actions workflow linting |

These checks are required to pass before any code is merged to `main`.
