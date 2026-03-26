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

If you use an identity provider like Authentik, Authelia, or oauth2-proxy,
you can configure Houndarr to accept the authenticated username from a
proxy-supplied HTTP header instead of managing its own login sessions. This
eliminates the double-login that would otherwise occur.

Your identity provider does **not** need to act as the reverse proxy itself.
Most IdPs support a **forward auth** mode: your existing reverse proxy
(Traefik, Nginx, Caddy) stays in place and checks each request with the
IdP before forwarding it to Houndarr. The IdP handles the OIDC/SSO login
flow and injects a header with the authenticated username.

### How it works

1. A request arrives at your reverse proxy (e.g. Traefik).
2. The proxy asks your IdP (e.g. Authentik) whether the user is authenticated.
3. If not, the IdP redirects the user to its login page (OIDC, SAML, etc.).
4. Once authenticated, the IdP tells the proxy to forward the request with
   a header containing the username (e.g. `X-authentik-username`).
5. Houndarr reads that header, but only after verifying the request comes
   from a trusted proxy IP. Requests that bypass the proxy get `403 Forbidden`.

### Required configuration

Three settings must all be set together:

| Variable | Description |
|----------|-------------|
| `HOUNDARR_AUTH_MODE=proxy` | Switch from built-in session auth to proxy auth |
| `HOUNDARR_AUTH_PROXY_HEADER` | Header name your proxy sets with the authenticated username |
| `HOUNDARR_TRUSTED_PROXIES` | Your proxy's IP or subnet; requests from other IPs are blocked |

Houndarr refuses to start if `AUTH_MODE=proxy` is set without both of the
other two variables. The auth header name cannot be a reserved HTTP header
(`Cookie`, `Authorization`, `Host`, etc.).

:::warning
**Expose only the port your proxy sits in front of.** In proxy mode,
Houndarr trusts that the proxy has already authenticated the user. Any client
that reaches port 8877 directly (bypassing the proxy) can forge the auth
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

### Example: Authentik with Traefik (forward auth)

This is the most common setup for users who run Traefik as their reverse
proxy and Authentik as their identity provider. Authentik does not need to
be the reverse proxy; Traefik stays in front and delegates authentication
to Authentik via forward auth.

**Step 1: Create a Proxy Provider in Authentik.**
In the Authentik admin panel, go to **Applications > Providers > Create**
and choose **Proxy Provider**. Set the mode to **Forward auth (single
application)**. Set the **External host** to `https://houndarr.example.com`
(your Houndarr URL). Create an **Application** and assign this provider to it.

**Step 2: Configure the Traefik ForwardAuth middleware.**
This middleware tells Traefik to check each request with the Authentik
outpost before forwarding it. Replace `authentik-proxy` with your Authentik
outpost container name, and `houndarr.example.com` with your domain.

**Step 3: Apply the middleware to Houndarr and configure Houndarr's env vars.**

```yaml
services:
  authentik-proxy:
    image: ghcr.io/goauthentik/proxy
    environment:
      AUTHENTIK_HOST: https://authentik.example.com
      AUTHENTIK_TOKEN: <token-generated-by-authentik>
    labels:
      traefik.enable: "true"
      traefik.port: 9000
      traefik.http.routers.authentik.rule: >-
        Host(`houndarr.example.com`) && PathPrefix(`/outpost.goauthentik.io/`)
      traefik.http.middlewares.authentik.forwardauth.address: >-
        http://authentik-proxy:9000/outpost.goauthentik.io/auth/traefik
      traefik.http.middlewares.authentik.forwardauth.trustForwardHeader: "true"
      traefik.http.middlewares.authentik.forwardauth.authResponseHeaders: >-
        X-authentik-username,X-authentik-groups,X-authentik-email
    restart: unless-stopped

  houndarr:
    image: ghcr.io/av1155/houndarr:latest
    labels:
      traefik.enable: "true"
      traefik.http.routers.houndarr.rule: Host(`houndarr.example.com`)
      traefik.http.routers.houndarr.entrypoints: websecure
      traefik.http.routers.houndarr.tls.certresolver: letsencrypt
      traefik.http.routers.houndarr.middlewares: authentik@docker
      traefik.http.services.houndarr.loadbalancer.server.port: 8877
    environment:
      - HOUNDARR_AUTH_MODE=proxy
      - HOUNDARR_AUTH_PROXY_HEADER=X-authentik-username
      - HOUNDARR_TRUSTED_PROXIES=172.18.0.0/16
      - HOUNDARR_SECURE_COOKIES=true
```

The `authentik@docker` middleware reference on the Houndarr router tells
Traefik to run the forward auth check before forwarding each request.
Authentik handles the OIDC login flow; once authenticated, it injects
`X-authentik-username` into the forwarded request, and Houndarr reads it.

For more details on Authentik's forward auth configuration, see the
[Authentik Traefik docs](https://docs.goauthentik.io/add-secure-apps/providers/proxy/server_traefik/).

### Example: Authelia with Traefik

Authelia uses `Remote-User` as its default header. The forward auth
middleware pattern is similar to Authentik. See the
[Authelia Traefik integration docs](https://www.authelia.com/integration/proxies/traefik/)
for the middleware configuration.

```yaml
services:
  houndarr:
    image: ghcr.io/av1155/houndarr:latest
    labels:
      traefik.enable: "true"
      traefik.http.routers.houndarr.rule: Host(`houndarr.example.com`)
      traefik.http.routers.houndarr.middlewares: authelia@docker
      traefik.http.services.houndarr.loadbalancer.server.port: 8877
    environment:
      - HOUNDARR_AUTH_MODE=proxy
      - HOUNDARR_AUTH_PROXY_HEADER=Remote-User
      - HOUNDARR_TRUSTED_PROXIES=172.18.0.0/16
      - HOUNDARR_SECURE_COOKIES=true
```

### Example: oauth2-proxy

oauth2-proxy sets `X-Auth-Request-User`. Configure it as a Traefik
ForwardAuth middleware the same way, pointing at your oauth2-proxy instance.

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

### Example: Nginx with any IdP

If you use Nginx instead of Traefik, configure `auth_request` to call your
IdP's forward auth endpoint and pass the authenticated header to Houndarr.
Set `HOUNDARR_AUTH_PROXY_HEADER` to whichever header your IdP injects.

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
| Login / setup | Redirected to `/`; no local credentials needed |
| Logout | Clears the CSRF cookie, redirects to `/` |
| Account settings | Password change section hidden |
| `/api/health` | Still public, no auth required |
| CSRF | Still enforced on mutations via double-submit cookie |
| Startup | Logs the configured auth mode and trusted proxy range |

### Switching back to built-in auth

Remove `HOUNDARR_AUTH_MODE=proxy` and restart. Built-in auth takes effect
immediately. If no admin password was ever created, the setup page appears
as on first run.
