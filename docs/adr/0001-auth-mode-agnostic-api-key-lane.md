# 0001 Auth-mode-agnostic Houndarr API key lane

The Houndarr API key is the sole credential authorizing requests to
`/api/v1/widget`, regardless of whether `HOUNDARR_AUTH_MODE` is
`builtin` or `proxy`. This creates a disjoint trust path: session
cookies and proxy auth headers alone do not grant access; a Houndarr
API key alone does. We chose this over a hybrid `session OR key`
fallback so external integrations work consistently across both auth
modes without operators threading their reverse-proxy authentication
through every consumer tool, and so the key's portability cannot
accidentally narrow when the auth mode is switched.

## Considered options

- **Hybrid (`session OR key`)**: any of session, proxy header, or
  Houndarr API key would have authorized `/api/v1/widget`. Rejected:
  three auth paths multiply test surface and create scenarios where
  switching modes silently changes the key's effective scope.
- **Builtin-only key lane**: the key would only work when
  `HOUNDARR_AUTH_MODE=builtin`. Rejected: forces proxy-mode operators
  to either route Homepage through their auth proxy (which not all
  proxies cleanly support per-tool) or abandon proxy mode entirely.
- **Public endpoint (no key)**: `/api/v1/widget` would have been
  added to `_PUBLIC_PATHS`. Rejected: introduces a second
  unauthenticated surface beyond `/api/health` and discloses
  aggregate library metrics to anyone with network reach to port
  8877.

## Consequences

- **Disjoint mental model for proxy-mode operators.** Documentation
  must explicitly call out that the key lane is independent of the
  proxy gate, since the existing threat model frames the proxy as
  the gate. Captured in `website/docs/security/threat-model.md`
  under "Credential classes."
- **Stolen key impact is bounded by the endpoint's narrow contract.**
  A leaked Houndarr API key exposes only the `/api/v1/widget`
  summary fields, not session-scoped routes or per-instance secrets.
  Limits blast radius compared with a hybrid model where a stolen
  key would fall through to broader access.
- **Revocation is immediate.** Deleting the row in `widget_api_key`
  invalidates the key on the next request; no propagation delay
  because the validator hashes the incoming header against the
  stored hash without caching plaintext.
