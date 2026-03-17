# \*arr search command reference

Reference for Houndarr client code that dispatches search commands to Sonarr,
Radarr, Lidarr, Readarr, and Whisparr.

This file is scoped to the search commands Houndarr needs for missing and
cutoff-unmet items. It documents the minimal request bodies accepted by the
tested app versions and the command endpoint each app uses.

## Tested versions

- Sonarr `4.0.16.2944`
- Radarr `6.0.4.10291`
- Lidarr `3.1.0.4875`
- Bookshelf `0.4.20.129` (Readarr fork; readarr was archived by the owner on Jun 27, 2025)
- Whisparr `2.2.0.108`

## Sources used

- upstream command classes
- local OpenAPI snapshots
- runtime request captures from the tested versions

## What is reliable here

For the tested versions above, the following are established:

- command endpoint path/version
- command `name` values
- request field names and casing
- scalar vs array field shape
- minimal request bodies accepted by the UI-backed API flow

## What OpenAPI does and does not cover

The local OpenAPI snapshots confirm the command endpoint exists and document the
generic command resource. They do not model per-command search payload fields
such as `episodeIds`, `movieIds`, `albumIds`, `bookIds`, `authorId`,
`artistId`, `seriesId`, or `seasonNumber`.

Those command-specific fields come from upstream command classes and runtime
captures.

## Implementation rule

Send the minimal request body shown for the target app and command.

Do not send response-only fields such as:

- `sendUpdatesToClient`
- `updateScheduledTask`
- `requiresDiskAccess`
- `isExclusive`
- `isTypeExclusive`
- `isLongRunning`
- `trigger`
- `suppressMessages`
- `clientUserAgent`

The server includes those in the created command resource. They are not needed
for the request bodies documented here.

---

## Sonarr

**Version tested:** `4.0.16.2944`  
**Command endpoint:** `POST /api/v3/command`

### EpisodeSearch

**Upstream command class:** `EpisodeSearchCommand`  
**C# property:** `EpisodeIds` (`List<int>`)

**Request body:**

```json
{ "name": "EpisodeSearch", "episodeIds": [123] }
```

### SeasonSearch

**Upstream command class:** `SeasonSearchCommand`  
**C# properties:**

- `SeriesId` (`int`)
- `SeasonNumber` (`int`)

**Request body:**

```json
{ "name": "SeasonSearch", "seriesId": 45, "seasonNumber": 2 }
```

---

## Radarr

**Version tested:** `6.0.4.10291`  
**Command endpoint:** `POST /api/v3/command`

### MoviesSearch

**Upstream command class:** `MoviesSearchCommand`  
**C# property:** `MovieIds` (`List<int>`)

**Request body:**

```json
{ "name": "MoviesSearch", "movieIds": [123] }
```

---

## Lidarr

**Version tested:** `3.1.0.4875`  
**Command endpoint:** `POST /api/v1/command`

### AlbumSearch

**Upstream command class:** `AlbumSearchCommand`  
**C# property:** `AlbumIds` (`List<int>`)

**Request body:**

```json
{ "name": "AlbumSearch", "albumIds": [123] }
```

**Note:** albums, singles, and EPs all used the same `AlbumSearch` payload shape
in runtime captures.

### ArtistSearch

**Upstream command class:** `ArtistSearchCommand`  
**C# property:** `ArtistId` (`int`)

**Request body:**

```json
{ "name": "ArtistSearch", "artistId": 45 }
```

---

## Bookshelf

**Version tested:** `0.4.20.129`  
**Fork:** Readarr fork  
**Command endpoint:** `POST /api/v1/command`

### BookSearch

**Readarr-lineage command class:** `BookSearchCommand`  
**C# property:** `BookIds` (`List<int>`)

**Request body:**

```json
{ "name": "BookSearch", "bookIds": [123] }
```

**Note:** books and audiobooks both used the same `BookSearch` payload shape in
runtime captures.

### AuthorSearch

**Readarr-lineage command class:** `AuthorSearchCommand`  
**C# property:** `AuthorId` (`int`)

**Request body:**

```json
{ "name": "AuthorSearch", "authorId": 45 }
```

**Implementation note:** runtime behavior here was captured from Bookshelf, not
from upstream Readarr.

---

## Whisparr

**Version tested:** `2.2.0.108`  
**Command endpoint:** `POST /api/v3/command`

### EpisodeSearch

**Upstream command class:** `EpisodeSearchCommand`  
**C# property:** `EpisodeIds` (`List<int>`)

**Request body:**

```json
{ "name": "EpisodeSearch", "episodeIds": [123] }
```

### SeasonSearch

**Upstream command class:** `SeasonSearchCommand`  
**C# properties:**

- `SeriesId` (`int`)
- `SeasonNumber` (`int`)

**Request body:**

```json
{ "name": "SeasonSearch", "seriesId": 45, "seasonNumber": 2 }
```

**Note:** the UI may present these groupings as years, but the API still uses
`SeasonSearch` with `seasonNumber`.

---

## Consolidated matrix

```json
{
    "sonarr": {
        "version_tested": "4.0.16.2944",
        "command_endpoint": "/api/v3/command",
        "commands": {
            "EpisodeSearch": {
                "command_class": "EpisodeSearchCommand",
                "request_body": {
                    "name": "EpisodeSearch",
                    "episodeIds": [123]
                }
            },
            "SeasonSearch": {
                "command_class": "SeasonSearchCommand",
                "request_body": {
                    "name": "SeasonSearch",
                    "seriesId": 45,
                    "seasonNumber": 2
                }
            }
        }
    },
    "radarr": {
        "version_tested": "6.0.4.10291",
        "command_endpoint": "/api/v3/command",
        "commands": {
            "MoviesSearch": {
                "command_class": "MoviesSearchCommand",
                "request_body": {
                    "name": "MoviesSearch",
                    "movieIds": [123]
                }
            }
        }
    },
    "lidarr": {
        "version_tested": "3.1.0.4875",
        "command_endpoint": "/api/v1/command",
        "commands": {
            "AlbumSearch": {
                "command_class": "AlbumSearchCommand",
                "request_body": {
                    "name": "AlbumSearch",
                    "albumIds": [123]
                }
            },
            "ArtistSearch": {
                "command_class": "ArtistSearchCommand",
                "request_body": {
                    "name": "ArtistSearch",
                    "artistId": 45
                }
            }
        }
    },
    "bookshelf": {
        "version_tested": "0.4.20.129",
        "fork_of": "Readarr",
        "command_endpoint": "/api/v1/command",
        "commands": {
            "BookSearch": {
                "command_class": "BookSearchCommand",
                "request_body": {
                    "name": "BookSearch",
                    "bookIds": [123]
                }
            },
            "AuthorSearch": {
                "command_class": "AuthorSearchCommand",
                "request_body": {
                    "name": "AuthorSearch",
                    "authorId": 45
                }
            }
        }
    },
    "whisparr": {
        "version_tested": "2.2.0.108",
        "command_endpoint": "/api/v3/command",
        "commands": {
            "EpisodeSearch": {
                "command_class": "EpisodeSearchCommand",
                "request_body": {
                    "name": "EpisodeSearch",
                    "episodeIds": [123]
                }
            },
            "SeasonSearch": {
                "command_class": "SeasonSearchCommand",
                "request_body": {
                    "name": "SeasonSearch",
                    "seriesId": 45,
                    "seasonNumber": 2
                }
            }
        }
    }
}
```

## Minimal request bodies

```json
{"name":"EpisodeSearch","episodeIds":[123]}
{"name":"SeasonSearch","seriesId":45,"seasonNumber":2}
{"name":"MoviesSearch","movieIds":[123]}
{"name":"AlbumSearch","albumIds":[123]}
{"name":"ArtistSearch","artistId":45}
{"name":"BookSearch","bookIds":[123]}
{"name":"AuthorSearch","authorId":45}
```

## Endpoint map

- Sonarr: `/api/v3/command`
- Radarr: `/api/v3/command`
- Lidarr: `/api/v1/command`
- Bookshelf: `/api/v1/command`
- Whisparr: `/api/v3/command`

## Notes for Houndarr client code

- Use the vendored OpenAPI snapshots for endpoint-level contracts.
- Use the request bodies in this file for search dispatch.
- Keep payloads minimal.
- Re-check runtime behavior when bumping supported \*arr versions.
- Do not use "Bookshelf" in UI and documentation, use "Readarr"

## Upstream source URLs

Command class definitions and the shared base `Command` class that derives the
API `name` value (strips the `Command` suffix from the class name).

### Sonarr

- [EpisodeSearchCommand.cs](https://raw.githubusercontent.com/Sonarr/Sonarr/develop/src/NzbDrone.Core/IndexerSearch/EpisodeSearchCommand.cs)
- [SeasonSearchCommand.cs](https://raw.githubusercontent.com/Sonarr/Sonarr/develop/src/NzbDrone.Core/IndexerSearch/SeasonSearchCommand.cs)
- [Command.cs](https://raw.githubusercontent.com/Sonarr/Sonarr/develop/src/NzbDrone.Core/Messaging/Commands/Command.cs)

### Radarr

- [MoviesSearchCommand.cs](https://raw.githubusercontent.com/Radarr/Radarr/develop/src/NzbDrone.Core/IndexerSearch/MoviesSearchCommand.cs)
- [Command.cs](https://raw.githubusercontent.com/Radarr/Radarr/develop/src/NzbDrone.Core/Messaging/Commands/Command.cs)

### Lidarr

- [AlbumSearchCommand.cs](https://raw.githubusercontent.com/Lidarr/Lidarr/develop/src/NzbDrone.Core/IndexerSearch/AlbumSearchCommand.cs)
- [ArtistSearchCommand.cs](https://raw.githubusercontent.com/Lidarr/Lidarr/develop/src/NzbDrone.Core/IndexerSearch/ArtistSearchCommand.cs)
- [Command.cs](https://raw.githubusercontent.com/Lidarr/Lidarr/develop/src/NzbDrone.Core/Messaging/Commands/Command.cs)

### Readarr

- [BookSearchCommand.cs](https://raw.githubusercontent.com/Readarr/Readarr/develop/src/NzbDrone.Core/IndexerSearch/BookSearchCommand.cs)
- [AuthorSearchCommand.cs](https://raw.githubusercontent.com/Readarr/Readarr/develop/src/NzbDrone.Core/IndexerSearch/AuthorSearchCommand.cs)
- [Command.cs](https://raw.githubusercontent.com/Readarr/Readarr/develop/src/NzbDrone.Core/Messaging/Commands/Command.cs)

### Whisparr

- [EpisodeSearchCommand.cs](https://raw.githubusercontent.com/Whisparr/Whisparr/develop/src/NzbDrone.Core/IndexerSearch/EpisodeSearchCommand.cs)
- [SeasonSearchCommand.cs](https://raw.githubusercontent.com/Whisparr/Whisparr/develop/src/NzbDrone.Core/IndexerSearch/SeasonSearchCommand.cs)
- [Command.cs](https://raw.githubusercontent.com/Whisparr/Whisparr/develop/src/NzbDrone.Core/Messaging/Commands/Command.cs)
