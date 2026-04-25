---
sidebar_position: 2
title: Credential Handling
description: How Houndarr stores API keys, the master key, passwords, session tokens, and CSRF state.
---

# Credential Handling

Reference for every credential Houndarr handles: your stored *arr
API keys, the Fernet master key that encrypts them, the admin
password, session tokens, and the CSRF state.

## API keys on disk

API keys are encrypted before being written to the database using
[Fernet](https://cryptography.io/en/latest/fernet/) symmetric
encryption from the `cryptography` library. Fernet uses AES-128 in
CBC mode for confidentiality and HMAC-SHA256 for integrity and
tamper detection.

The database column is named `encrypted_api_key`. Plaintext API
keys are never written to disk.

Relevant source files:

- `src/houndarr/crypto.py`: `encrypt()` and `decrypt()`
- `src/houndarr/services/instances.py`: instance CRUD with
  encryption on write and decryption on read

## The master key

A Fernet master key is generated on first startup using
`Fernet.generate_key()`, which calls `os.urandom(32)` (the operating
system's cryptographically secure random number generator).

The key is stored at `<data_dir>/houndarr.masterkey` (typically
`/data/houndarr.masterkey` in Docker). It is:

- Created atomically with `O_CREAT | O_EXCL` to prevent race
  conditions
- Written with file permissions `0o600` (owner read/write only)
- Permission-enforced at runtime even when the process umask would
  allow broader access

The master key is loaded into memory once at application startup and
passed explicitly to service functions. It is never written to logs,
HTTP responses, or template output.

:::danger[Master key is critical]

Anyone with access to both `houndarr.masterkey` and `houndarr.db`
can decrypt all stored API keys. See
[Backup and Restore](/docs/guides/backup-and-restore) for how to
back up the data directory safely.

:::

## API keys in the web UI

When you edit an existing instance, the API key field shows a
placeholder sentinel (`__UNCHANGED__`), not the actual key. The
input uses `type="password"` so the placeholder is masked.

On submit:

- If the sentinel is still present, the server keeps the existing
  encrypted key.
- If a new value was entered, the server encrypts and stores the
  new key.

The decrypted API key never appears in any HTTP response body, HTML
template output, or JSON API payload. Specifically:

- `/api/status` constructs its response by selecting specific
  fields; `api_key` is excluded
- Settings page templates render instance name, type, URL, and
  scheduling parameters but never reference the `api_key` field
- `/api/health` returns only `{"status": "ok"}`

Relevant source files:

- `src/houndarr/services/instance_validation.py`: `API_KEY_UNCHANGED`
  sentinel definition
- `src/houndarr/services/instance_submit.py`: resolution logic that
  keeps the stored key when the form submits the sentinel unchanged
- `src/houndarr/templates/partials/instance_form.html`: form
  pre-fills sentinel, not the key
- `src/houndarr/routes/api/status.py`: field-level selection
  omitting `api_key`

## Authentication modes

Houndarr supports two authentication modes, configured via
`HOUNDARR_AUTH_MODE`.

### Built-in auth (default)

Houndarr manages its own login session.

**Password storage.** The admin password is hashed with bcrypt at
cost factor 12 and stored in the SQLite `settings` table. Plaintext
passwords are never stored.

**Sessions.** Signed tokens via
`itsdangerous.URLSafeTimedSerializer` with an HMAC signature. The
signing secret is a 64-character hex string generated from
`os.urandom(32)` on first setup and stored in the database. Token
payload contains a creation timestamp and a CSRF nonce only: no
username, no password, no API keys. Session tokens expire after 24
hours, enforced server-side.

**Login rate limiting.** 5 failed attempts per IP per 60-second
sliding window. After that, further attempts return HTTP 429. Error
messages are generic and never reveal whether the username or
password was the wrong part. The same IP bucket also guards the
post-auth password endpoints (`POST /settings/account/password`
and `POST /settings/admin/factory-reset`), so a stolen session
cookie cannot brute-force the current password through those
surfaces either.

**Password rotation.** Changing the admin password rotates the
session signing secret. Every cookie signed with the previous
secret stops validating, so any session the admin wants to revoke
(another tab, another device, a suspected theft) is invalidated
by the password change itself. The tab that made the change is
reissued a fresh cookie and reloaded automatically so it stays
signed in without a manual refresh.

**X-Forwarded-For.** Honored only when the connecting IP is listed
in `HOUNDARR_TRUSTED_PROXIES`. With no trusted proxies configured
(the default), the header is ignored entirely, which prevents IP
spoofing.

### Proxy auth mode

When `HOUNDARR_AUTH_MODE=proxy`, Houndarr delegates authentication
to the reverse proxy (Authelia, Authentik, oauth2-proxy, etc.) and
reads the authenticated username from a configured HTTP header. The
header is only read after the request is verified to originate from
a trusted proxy IP; untrusted IPs get `403 Forbidden` with no
fallback to a login form.

Both `HOUNDARR_AUTH_PROXY_HEADER` and `HOUNDARR_TRUSTED_PROXIES`
must be set; the app refuses to start without them. See
[SSO Proxy Auth](/docs/guides/sso-proxy-auth) for setup.

## Cookies

| Cookie | HttpOnly | SameSite | Secure | Purpose |
|--------|----------|----------|--------|---------|
| `houndarr_session` | Yes | Lax (configurable) | Configurable | Session authentication (built-in mode only) |
| `houndarr_csrf` | No | Lax (configurable) | Configurable | CSRF token for HTMX / JS (both modes) |

The CSRF cookie is intentionally not `HttpOnly` because HTMX needs
to read it to include the token in request headers.

`SameSite` defaults to `Lax`, which allows cookies on top-level
navigations from external links (dashboard apps, bookmarks) while
blocking cross-site form submissions. This matches the default used
by Django, Rails, Flask, Laravel, and ASP.NET Core. Set
`HOUNDARR_COOKIE_SAMESITE=strict` to withhold cookies on all
cross-site requests; note this prevents access via links from
dashboard apps like Homepage or Homarr.

The `Secure` flag is controlled by `HOUNDARR_SECURE_COOKIES`. It
defaults to `false` because Houndarr serves plain HTTP and expects
HTTPS to be terminated by a reverse proxy. Set it to `true` when
running behind HTTPS.

## CSRF protection

All state-changing requests (POST, PUT, PATCH, DELETE) require a
valid CSRF token in both auth modes.

In built-in mode, the expected token is embedded in the
HMAC-signed session cookie and cannot be forged without the signing
secret.

In proxy mode, the double-submit cookie pattern applies: a
`houndarr_csrf` cookie with `SameSite=Lax` (configurable) is set on
authenticated responses and must be echoed back in the
`X-CSRF-Token` header or `csrf_token` form field.

Token comparison uses `hmac.compare_digest()` in both modes to
prevent timing attacks.

The only intentional CSRF exemption is `POST /logout`.

## Unauthenticated routes

Only these paths are accessible without authentication:

| Path | Purpose | Proxy mode |
|------|---------|------------|
| `/setup` | First-run setup (disabled after setup completes) | Redirects to `/` |
| `/login` | Login form | Redirects to `/` |
| `/api/health` | Health check (returns only `{"status": "ok"}`) | Public (unchanged) |
| `/static/*` | Static assets (CSS, JS, images) | Public (unchanged) |

No unauthenticated route exposes configuration, API keys, instance
data, or any information beyond a static health status.
