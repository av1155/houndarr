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
