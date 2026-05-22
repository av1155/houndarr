---
sidebar_position: 3
title: Threat Model
description: What Houndarr's security design protects against, what it does not, and the known limitations.
---

# Threat Model

Security boundaries and assumed trust. What Houndarr defends
against, what it does not, and limitations to be aware of before
exposing a deployment.

## Assumed trust model

Houndarr is a self-hosted tool, designed to run on a private network
alongside your *arr stack. The design assumes:

- The host machine and the Docker / Kubernetes control plane are
  trusted
- Everyone with network access to the Houndarr port is either
  authenticated or intentionally trusted (and HTTPS is terminated
  upstream if the traffic leaves that network)
- Your *arr instances are reachable on the same LAN or container
  network

## Credential classes

Houndarr has three independent credential lanes:

| Credential | Authorizes | Mode |
|------------|------------|------|
| Built-in session cookie | Browser UI, Settings, logs, and session-scoped routes | Built-in auth |
| Proxy auth header | Browser UI, Settings, logs, and session-scoped routes after the request comes from a trusted proxy IP | Proxy auth |
| Houndarr API key | `GET /api/v1/widget` only | Built-in auth and proxy auth |

The Houndarr API key lane is independent of browser sessions and proxy
headers. A proxy-auth header without `X-Api-Key` does not authorize
`/api/v1/widget`, and a Houndarr API key does not authorize Settings,
logs, instance configuration, or write actions.

Regenerate and revoke are immediate because Houndarr checks the stored
`widget_api_key` row on every widget request. A stolen Houndarr API key
exposes only the widget summary contract until it is rotated or revoked.

This split is captured in
[ADR-0001](https://github.com/av1155/houndarr/blob/main/docs/adr/0001-auth-mode-agnostic-api-key-lane.md).

## Network trust boundaries

### Plain HTTP

Houndarr serves plain HTTP and does not terminate TLS. When you
access Houndarr over a network rather than localhost, put it behind
a reverse proxy that handles HTTPS.

Without HTTPS, session cookies and login credentials travel in
cleartext on the wire.

See [Reverse Proxy](/docs/guides/reverse-proxy) for
configuration examples.

### Reverse proxy configuration

When running behind a reverse proxy with HTTPS:

1. Set `HOUNDARR_SECURE_COOKIES=true` so cookies only go over HTTPS.
2. Set `HOUNDARR_TRUSTED_PROXIES` to your proxy's IP or subnet so
   the rate limiter sees real client IPs via `X-Forwarded-For`.

### Instance URL validation (SSRF protection)

When adding or editing an instance URL, Houndarr validates the
target:

- **Blocked**: `localhost`, loopback IPs (`127.0.0.0/8`, `::1`),
  link-local (`169.254.0.0/16`), and unspecified addresses
  (`0.0.0.0`)
- **Allowed**: RFC-1918 private ranges (`10.0.0.0/8`,
  `172.16.0.0/12`, `192.168.0.0/16`) because *arr instances
  typically run on the same LAN or Docker network
- **Hostname format**: standard DNS labels with letters, digits,
  hyphens, and underscores inside label segments (for example
  `radarr_hd`, `sonarr-4k`); must start and end with a letter or
  digit

Hostnames resolve via DNS and each resolved IP is checked against
the blocked ranges to prevent DNS rebinding to loopback addresses.

The container-to-host bridge hostnames `host.docker.internal` and
`host.containers.internal` are exempt from the link-local check.
Docker and Podman inject these names into the container's
`/etc/hosts` as the documented way to reach services on the host.
They do not overlap with cloud metadata service names or IPs.

## Known limitations

### Stateless sessions

Sessions are signed tokens with no server-side session store. Logout
deletes the cookie client-side, but a stolen token remains valid
until its 24-hour expiry. There is no server-side revocation
mechanism.

### In-memory rate limiter

The login brute-force limiter resets when the application restarts.
It is not persisted to disk.

### Decrypted keys in process memory

When Houndarr loads instance data (settings page, status polling,
search execution), the decrypted Instance API key lives in Python
process memory as part of the `Instance` object. Templates never render
this value and no HTTP response carries it, but a process memory dump
could theoretically expose it. This is inherent to any application
that uses secrets at runtime.

### Browser-side font dependency

The production UI serves compiled CSS and HTMX from Houndarr's own
`/static/*` assets. The compiled stylesheet imports Google Fonts. If
Google Fonts is unavailable, the UI falls back to system fonts.

### Private network ranges allowed

Instance URL validation intentionally permits RFC-1918 private
addresses because *arr instances typically sit on the same LAN or
Docker network. Houndarr can therefore be directed at any reachable
host on your private network. This is the trade-off behind allowing
things like `http://sonarr:8989`.
