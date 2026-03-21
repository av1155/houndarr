---
sidebar_position: 3
title: Reverse Proxy
description: How to run Houndarr behind Nginx, Caddy, Traefik, or other reverse proxies.
---

# Reverse Proxy

Houndarr serves plain HTTP and does not terminate TLS. If you access Houndarr
over a network (rather than localhost), you should run it behind a reverse proxy
that terminates HTTPS.

## Required settings

When running behind a reverse proxy with HTTPS:

1. Set `HOUNDARR_SECURE_COOKIES=true` so session cookies require HTTPS.
2. Set `HOUNDARR_TRUSTED_PROXIES` to your proxy's IP or subnet (e.g. `172.18.0.1`
   or `172.18.0.0/16`) so the login rate limiter sees real client IPs via
   `X-Forwarded-For`.
3. Proxy all traffic to `http://houndarr:8877`.

:::warning
Without `HOUNDARR_SECURE_COOKIES=true`, session cookies and login credentials are
transmitted in cleartext on the network.
:::

## Example: Nginx

```nginx
server {
    listen 443 ssl;
    server_name houndarr.example.com;

    ssl_certificate /etc/nginx/ssl/cert.pem;
    ssl_certificate_key /etc/nginx/ssl/key.pem;

    location / {
        proxy_pass http://houndarr:8877;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## Example: Caddy

```text
houndarr.example.com {
    reverse_proxy houndarr:8877
}
```

Caddy handles HTTPS automatically and sets appropriate forwarding headers.

## Example: Traefik (Docker labels)

```yaml
services:
  houndarr:
    image: ghcr.io/av1155/houndarr:latest
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.houndarr.rule=Host(`houndarr.example.com`)"
      - "traefik.http.routers.houndarr.entrypoints=websecure"
      - "traefik.http.routers.houndarr.tls.certresolver=letsencrypt"
      - "traefik.http.services.houndarr.loadbalancer.server.port=8877"
    environment:
      - HOUNDARR_SECURE_COOKIES=true
      - HOUNDARR_TRUSTED_PROXIES=172.18.0.0/16
```

## Trusted proxies

The `HOUNDARR_TRUSTED_PROXIES` variable accepts a comma-separated list of IP
addresses or CIDR subnets (e.g. `172.18.0.1` or `172.18.0.0/16`). When set,
Houndarr honors the `X-Forwarded-For` header from matching IPs to determine
the real client IP for rate limiting.

When no trusted proxies are configured (the default), the `X-Forwarded-For`
header is ignored entirely, preventing IP spoofing.

---

## SSO proxy authentication

If you run Houndarr behind an SSO reverse proxy (Authelia, Authentik,
oauth2-proxy, Traefik ForwardAuth), you can configure Houndarr to accept
the authenticated username from a proxy-supplied HTTP header instead of
managing its own login sessions. This eliminates the double-login that would
otherwise occur.

### How it works

In proxy auth mode, Houndarr does not show a login page. The proxy
authenticates the user and sets a header with the username before forwarding
the request. Houndarr reads the username from that header — but only after
verifying the request originates from a trusted proxy IP. Requests that
bypass the proxy get `403 Forbidden`.

### Required configuration

Three settings must all be set together:

| Variable | Description |
|----------|-------------|
| `HOUNDARR_AUTH_MODE=proxy` | Switch from built-in session auth to proxy auth |
| `HOUNDARR_AUTH_PROXY_HEADER` | Header name your proxy sets with the authenticated username |
| `HOUNDARR_TRUSTED_PROXIES` | Your proxy's IP or subnet — requests from other IPs are blocked |

Houndarr refuses to start if `AUTH_MODE=proxy` is set without both of the
other two variables. The auth header name cannot be a reserved HTTP header
(`Cookie`, `Authorization`, `Host`, etc.).

:::warning
**Expose only the port your proxy sits in front of.** In proxy mode,
Houndarr trusts that the proxy has already authenticated the user. Any client
that reaches port 8877 directly — bypassing the proxy — can forge the auth
header. Do not expose port 8877 to the internet without the proxy in front.
:::

### Example: Authelia

Authelia sets `Remote-User` on authenticated requests.

```yaml
services:
  houndarr:
    image: ghcr.io/av1155/houndarr:latest
    environment:
      - HOUNDARR_AUTH_MODE=proxy
      - HOUNDARR_AUTH_PROXY_HEADER=Remote-User
      - HOUNDARR_TRUSTED_PROXIES=172.18.0.0/16
      - HOUNDARR_SECURE_COOKIES=true
```

### Example: Authentik

Authentik's proxy provider sets `X-authentik-username`.

```yaml
services:
  houndarr:
    image: ghcr.io/av1155/houndarr:latest
    environment:
      - HOUNDARR_AUTH_MODE=proxy
      - HOUNDARR_AUTH_PROXY_HEADER=X-authentik-username
      - HOUNDARR_TRUSTED_PROXIES=172.18.0.0/16
      - HOUNDARR_SECURE_COOKIES=true
```

### Example: oauth2-proxy

oauth2-proxy sets `X-Auth-Request-User`.

```yaml
services:
  houndarr:
    image: ghcr.io/av1155/houndarr:latest
    environment:
      - HOUNDARR_AUTH_MODE=proxy
      - HOUNDARR_AUTH_PROXY_HEADER=X-Auth-Request-User
      - HOUNDARR_TRUSTED_PROXIES=172.18.0.0/16
      - HOUNDARR_SECURE_COOKIES=true
```

### Example: Traefik ForwardAuth

Traefik ForwardAuth typically sets `X-Forwarded-User`.

```yaml
services:
  houndarr:
    image: ghcr.io/av1155/houndarr:latest
    environment:
      - HOUNDARR_AUTH_MODE=proxy
      - HOUNDARR_AUTH_PROXY_HEADER=X-Forwarded-User
      - HOUNDARR_TRUSTED_PROXIES=172.18.0.0/16
      - HOUNDARR_SECURE_COOKIES=true
```

### What changes in proxy mode

| Area | Behavior |
|------|---------|
| Login / setup | Redirected to `/` — no local credentials needed |
| Logout | Clears the CSRF cookie, redirects to `/` |
| Account settings | Password change section hidden |
| `/api/health` | Still public, no auth required |
| CSRF | Still enforced on mutations via double-submit cookie |
| Startup | Logs the configured auth mode and trusted proxy range |

### Switching back to built-in auth

Remove `HOUNDARR_AUTH_MODE=proxy` and restart. Built-in auth takes effect
immediately. If no admin password was ever created, the setup page appears
as on first run.
