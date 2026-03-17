# API context for Houndarr

Use these files as API reference when working on *arr integrations:

- `docs/api/sonarr_openapi.json`
- `docs/api/radarr_openapi.json`
- `docs/api/whisparr_openapi.json`
- `docs/api/lidarr_openapi.json`
- `docs/api/readarr_openapi.json`

Guidelines:

- Treat the OpenAPI specs as the source of truth for endpoints, parameters, request bodies, and response schemas.
- Preserve existing app behavior unless it is inconsistent with the spec.
- Before changing integration behavior, inspect existing code in `src/` and related tests in `tests/`.
- When adding or changing API calls, align payloads and response handling with the spec.

---

https://raw.githubusercontent.com/Sonarr/Sonarr/develop/src/Sonarr.Api.V3/openapi.json
https://raw.githubusercontent.com/Radarr/Radarr/develop/src/Radarr.Api.V3/openapi.json
https://raw.githubusercontent.com/Whisparr/Whisparr/develop/src/Whisparr.Api.V3/openapi.json
https://raw.githubusercontent.com/lidarr/Lidarr/develop/src/Lidarr.Api.V1/openapi.json
https://raw.githubusercontent.com/Readarr/Readarr/develop/src/Readarr.Api.V1/openapi.json
