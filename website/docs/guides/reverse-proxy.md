---
sidebar_position: 3
title: Reverse Proxy
description: Run Houndarr behind Nginx, Caddy, or Traefik with HTTPS termination.
---

# Reverse Proxy

Plain HTTP only: TLS termination is the reverse proxy's job. For any
network access beyond localhost, run Houndarr behind a proxy that
handles HTTPS.

For SSO setups (Authentik, Authelia, oauth2-proxy), see
[SSO Proxy Auth](/docs/guides/sso-proxy-auth). This page covers the
plain-TLS case.

## Required settings

When running behind a reverse proxy with HTTPS:

1. Set `HOUNDARR_SECURE_COOKIES=true` so session cookies require
   HTTPS.
2. Set `HOUNDARR_TRUSTED_PROXIES` to your proxy's IP or subnet
   (e.g. `172.18.0.1` or `172.18.0.0/16`) so the login rate limiter
   sees real client IPs via `X-Forwarded-For`.
3. Proxy all traffic to `http://houndarr:8877`.

Without `HOUNDARR_SECURE_COOKIES=true`, session cookies and login
credentials travel in cleartext on the wire. Full details in
[Credential Handling](/docs/security/credential-handling#cookies).

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

Caddy handles HTTPS automatically and sets appropriate forwarding
headers.

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

## Serving from a sub-path

To reach Houndarr at `https://example.com/houndarr` instead of its
own hostname, set `HOUNDARR_URL_BASE=/houndarr`. Pages, links,
redirects and assets then carry the prefix. Houndarr answers with or
without it, so the proxy can pass the prefix through or strip it, and
requests straight to port 8877 (the container health check,
Kubernetes probes, the [Homepage widget](/docs/guides/homepage-integration))
keep working unprefixed.

Nginx, passing the prefix through:

```nginx
location /houndarr/ {
    proxy_pass http://houndarr:8877;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

To strip the prefix, write `proxy_pass http://houndarr:8877/;` with
the trailing slash. Keep the slash at the end of `location /houndarr/`
too, so the block doesn't also catch paths such as `/houndarr-old`.

Caddy, passing the prefix through, or stripping it with `handle_path`:

```text
example.com {
    redir /houndarr /houndarr/
    reverse_proxy /houndarr/* houndarr:8877
}
```

```text
example.com {
    redir /houndarr /houndarr/
    handle_path /houndarr/* {
        reverse_proxy houndarr:8877
    }
}
```

Traefik, passing the prefix through:

```yaml
labels:
  - "traefik.http.routers.houndarr.rule=Host(`example.com`) && PathPrefix(`/houndarr`)"
```

To strip it, add a `StripPrefix` middleware to the same router:

```yaml
labels:
  - "traefik.http.routers.houndarr.rule=Host(`example.com`) && PathPrefix(`/houndarr`)"
  - "traefik.http.middlewares.houndarr-strip.stripprefix.prefixes=/houndarr"
  - "traefik.http.routers.houndarr.middlewares=houndarr-strip"
```

The prefix comes only from `HOUNDARR_URL_BASE`; Houndarr ignores
`X-Forwarded-Prefix`. It can't start with a path Houndarr already
serves, such as `/api` or `/settings`, and Houndarr refuses to start
when the value isn't a plain path. A sub-path shares cookies with every other app on the hostname;
see [Credential Handling](/docs/security/credential-handling#cookies).

## Trusted proxies

The `HOUNDARR_TRUSTED_PROXIES` variable accepts a comma-separated
list of IP addresses or CIDR subnets (for example `172.18.0.1` or
`172.18.0.0/16`). When set, Houndarr honors the `X-Forwarded-For`
header from matching IPs to determine the real client IP for rate
limiting.

When no trusted proxies are configured (the default), the
`X-Forwarded-For` header is ignored entirely, preventing IP
spoofing.
