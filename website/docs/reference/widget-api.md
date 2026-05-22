---
sidebar_position: 7
title: Widget API
description: Request, response, field, and error contract for the Houndarr widget endpoint.
---

# Widget API

`GET /api/v1/widget` returns a read-only summary for external
dashboards. It is the only endpoint authorized by the Houndarr API key.

## Request

| Field | Value |
|-------|-------|
| Method | `GET` |
| Path | `/api/v1/widget` |
| Auth header | `X-Api-Key: hndarr_...` |
| Body | None |
| Auth mode | Works the same in built-in auth and proxy auth modes. |

Session cookies and proxy-auth headers do not authorize this endpoint.
The `X-Api-Key` header is required.

## Success response

```json
{
  "schema": 1,
  "generated_at": "2026-05-22T18:00:00Z",
  "totals": {
    "tracked": 11,
    "eligible": 7,
    "gated": 2,
    "unreleased": 1,
    "searches_7d": 1
  }
}
```

| Field | Type | Meaning |
|-------|------|---------|
| `schema` | integer | Contract version for the widget payload. Current value is `1`. |
| `generated_at` | string | UTC response generation time, formatted as an ISO timestamp ending in `Z`. |
| `totals` | object | Library-health summary across enabled, healthy instances. |
| `totals.tracked` | integer | Items Houndarr is responsible for: eligible, gated, unreleased, and upgrade-cooldown items. |
| `totals.eligible` | integer | Monitored items Houndarr can search now. Disabled instances and instances with active errors do not contribute. |
| `totals.gated` | integer | Missing plus cutoff-unmet items waiting on a per-item cooldown. |
| `totals.unreleased` | integer | Monitored items whose release date has not arrived yet. |
| `totals.searches_7d` | integer | Rolling count of successful search attempts recorded during the last seven days. |

`totals.eligible` is clamped per instance before summing so one
instance with inconsistent source counts cannot make the global value
negative.

## Error responses

| Status | Headers | When |
|--------|---------|------|
| `401 Unauthorized` | `WWW-Authenticate: ApiKey` | The key is missing, invalid, regenerated, revoked, or not configured. |
| `429 Too Many Requests` | `Retry-After: 60` | Too many failed key attempts came from the same client IP inside the rate-limit window. |

Houndarr does not return per-instance secrets, settings, search logs, or
write-action links from this endpoint.

## Curl examples

Successful request:

```bash
curl -sS \
  -H 'X-Api-Key: hndarr_xxxxxxxxxxxxxxxxxxxxxxxxxx' \
  http://houndarr:8877/api/v1/widget
```

Check headers while debugging authentication:

```bash
curl -i \
  -H 'X-Api-Key: hndarr_xxxxxxxxxxxxxxxxxxxxxxxxxx' \
  http://houndarr:8877/api/v1/widget
```

Missing key:

```bash
curl -i http://houndarr:8877/api/v1/widget
```

Expected header on the `401` response:

```http
WWW-Authenticate: ApiKey
```

After repeated failed key attempts, wait for the lockout window named
by the response header before retrying:

```http
Retry-After: 60
```

## Homepage field mapping

Homepage's `customapi` widget can display four compact fields. The
recommended Houndarr block uses these paths:

| Field | Label |
|-------|-------|
| `totals.eligible` | `Eligible` |
| `totals.gated` | `Gated` |
| `totals.unreleased` | `Unreleased` |
| `totals.searches_7d` | `Searches` |

For the full setup, see
[Homepage Integration](/docs/guides/homepage-integration).
