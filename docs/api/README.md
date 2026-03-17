# API context for Houndarr

The files in this directory are the API reference when working on *arr integrations:

- `sonarr_openapi.json`
- `radarr_openapi.json`
- `whisparr_openapi.json`
- `lidarr_openapi.json`
- `readarr_openapi.json`

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

See also: [arr-search-commands.md](arr-search-commands.md) for the confirmed upstream command
classes and POST body formats used by Houndarr's search engine.
