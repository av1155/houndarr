---
sidebar_position: 4
title: Audit
description: The Huntarr vulnerability audit, automated test coverage for security-sensitive code, and the CI pipeline that enforces it.
---

# Audit

Houndarr's security claims do not sit in a file by themselves. They
are verified on every pull request by a dedicated test suite, a live
smoke test against a real Docker container, and a stack of scanners
in CI. This page documents the evidence: what is tested, how, and
where the results live.

## Huntarr vulnerability audit

`tests/test_huntarr_vulns.py` contains 63 integration tests (via
parametrisation; 25 test functions expand to 63 collected cases)
that verify immunity to every vulnerability reported in the
[Huntarr v9.4.2 security review](https://github.com/rfsbraz/huntarr-security-review).
The suite runs in CI on every pull request as part of the standard
pytest run. No code merges unless it passes.

Anyone can run the same tests locally:

```bash
.venv/bin/pytest tests/test_huntarr_vulns.py -v
```

### What the suite covers

Every finding from the Huntarr audit has a corresponding test in
this file. The coverage fans out across these areas:

- **Unauthenticated access.** All 16 protected routes return HTTP
  302 to the login page when called without a session cookie.
- **Secret leakage.** Response bodies of `/api/health`, `/login`,
  and `/setup` contain no API keys, Fernet tokens, or internal
  secrets.
- **Setup lockout.** `/setup` becomes inaccessible after initial
  account creation. No body field bypasses the middleware auth
  check.
- **Absent features.** Paths from the Huntarr attack chain (2FA
  enrollment, setup clear, backup upload, Plex unlink) return HTTP
  404 because those code paths do not exist in Houndarr.
- **Path traversal.** Traversal attempts in URL paths never
  produce a 200 response.
- **X-Forwarded-For spoofing.** The XFF header is ignored without
  trusted proxies configured; IP spoofing attempts fail.
- **API key exposure.** No HTTP response carries an `api_key`
  field or a Fernet-encoded token (the `gAAAAA` prefix).
- **Cookie flags.** Session cookie is `HttpOnly`; CSRF cookie is
  not (HTMX requires reading it); both use `SameSite=Lax` by
  default and 24-hour expiry.
- **CSRF enforcement.** All mutating authenticated routes return
  HTTP 403 without a valid token. Token comparison via
  `hmac.compare_digest()` is verified in source.
- **Rate limiting.** 7 rapid failed login attempts trigger HTTP
  429 from the in-memory limiter. The same IP bucket covers the
  post-auth password surfaces; 6 rapid wrong-password attempts
  against `POST /settings/admin/factory-reset` also trip 429 so a
  stolen session cannot brute-force the admin password through
  the destructive endpoint.

### Live smoke test

`scripts/security_smoke_test.sh` runs curl-based checks against a
real Docker container and executes in CI on every pull request.
Anyone can run it locally:

```bash
bash scripts/security_smoke_test.sh http://localhost:8877
```

The script and the pytest suite together form the security
contract: the tests verify behavior against the application; the
smoke script verifies behavior against a running container. If
either fails, CI blocks the merge.

## Other tested surfaces

Security-sensitive code is not the only thing with end-to-end test
coverage. The broader suite verifies:

- **Search engine**: missing, cutoff, and upgrade passes end to
  end, including how they share a cycle without starving each
  other's caps, and how the engine handles bad or incomplete API
  responses from your *arr instances.
- **Scheduling rules**: cooldown windows, hourly caps, batch sizes,
  post-release grace periods, and the upgrade hard caps exercised
  at their boundary conditions.
- **Supervisor**: graceful shutdown, connection loss and recovery,
  staggered startup, idempotent task management. Connection errors
  produce exactly one log entry per failure sequence; recovery
  produces exactly one.
- **Clients**: all six client types (Sonarr, Radarr, Lidarr,
  Readarr, Whisparr v2, Whisparr v3) tested for correct API paths,
  request payloads, queue status checks, and error propagation.
- **Database**: log purge, settings, and cooldown tracking verified
  at their boundaries, including concurrent access.
- **Routes and auth**: every mutating endpoint tested for CSRF
  enforcement, authentication guards, and correct response codes
  across all outcomes.

No code merges without the full suite passing.

## CI security pipeline

Houndarr's `main` branch has required CI checks on every pull
request. Security coverage in the required set:

| Check | Purpose |
|------|---------|
| [Dependency audit (pip-audit)](https://pypi.org/project/pip-audit/) | Known vulnerability scanning for Python dependencies |
| [SAST (Bandit)](https://bandit.readthedocs.io/) | Static application security testing for Python |
| [Trivy filesystem scan](https://github.com/aquasecurity/trivy) | Vulnerability scan of the repository filesystem (CRITICAL / HIGH with known fixes) |
| [Dependency review](https://github.com/actions/dependency-review-action) | PR dependency diff check against the GitHub Advisory Database |
| [Trivy image scan](https://github.com/aquasecurity/trivy) | Vulnerability scan of the built Docker image (CRITICAL / HIGH with known fixes) |
| Security smoke test | Live-container checks via `scripts/security_smoke_test.sh` |

Additional workflows run conditionally when relevant files change:
Dockerfile linting (`hadolint`) and GitHub Actions workflow linting
(`actionlint`).
