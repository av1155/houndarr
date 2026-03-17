# \*arr search command reference

This file documents the confirmed upstream command classes and C# property names for search commands in:

1. Sonarr
2. Radarr
3. Lidarr
4. Readarr
5. Whisparr

## Confidence

- 100% confirmed from upstream source:
    - command class names
    - derived API command names
    - exact C# property names
- Very high confidence / expected API form:
    - lower-camel JSON field names in POST bodies

The API `name` value is derived by the shared base command logic that removes the `Command` suffix from the class name.

Examples:

- `EpisodeSearchCommand` -> `EpisodeSearch`
- `MoviesSearchCommand` -> `MoviesSearch`
- `AlbumSearchCommand` -> `AlbumSearch`

---

## 1. Sonarr

### Episode search

Confirmed:

- Command class: `EpisodeSearchCommand`
- API command name: `EpisodeSearch`
- C# property: `EpisodeIds`
- C# property type: `List<int>`

Source URLs:

- `https://raw.githubusercontent.com/Sonarr/Sonarr/develop/src/NzbDrone.Core/IndexerSearch/EpisodeSearchCommand.cs`
- `https://raw.githubusercontent.com/Sonarr/Sonarr/develop/src/NzbDrone.Core/Messaging/Commands/Command.cs`

Expected POST body:

```json
{ "name": "EpisodeSearch", "episodeIds": [123] }
```

### Season search

Confirmed:

- Command class: `SeasonSearchCommand`
- API command name: `SeasonSearch`
- C# properties:
    - `SeriesId`
    - `SeasonNumber`
- C# property types:
    - `int`
    - `int`

Source URLs:

- `https://raw.githubusercontent.com/Sonarr/Sonarr/develop/src/NzbDrone.Core/IndexerSearch/SeasonSearchCommand.cs`
- `https://raw.githubusercontent.com/Sonarr/Sonarr/develop/src/NzbDrone.Core/Messaging/Commands/Command.cs`

Expected POST body:

```json
{ "name": "SeasonSearch", "seriesId": 45, "seasonNumber": 2 }
```

Sonarr summary:

- `EpisodeSearch` + `EpisodeIds`
- `SeasonSearch` + `SeriesId` / `SeasonNumber`

---

## 2. Radarr

### Movie search

Confirmed:

- Command class: `MoviesSearchCommand`
- API command name: `MoviesSearch`
- C# property: `MovieIds`
- C# property type: `List<int>`

Source URLs:

- `https://raw.githubusercontent.com/Radarr/Radarr/develop/src/NzbDrone.Core/IndexerSearch/MoviesSearchCommand.cs`
- `https://raw.githubusercontent.com/Radarr/Radarr/develop/src/NzbDrone.Core/Messaging/Commands/Command.cs`

Expected POST body:

```json
{ "name": "MoviesSearch", "movieIds": [123] }
```

Radarr summary:

- `MoviesSearch` + `MovieIds`

---

## 3. Lidarr

### Album search

Confirmed:

- Command class: `AlbumSearchCommand`
- API command name: `AlbumSearch`
- C# property: `AlbumIds`
- C# property type: `List<int>`

Source URLs:

- `https://raw.githubusercontent.com/Lidarr/Lidarr/develop/src/NzbDrone.Core/IndexerSearch/AlbumSearchCommand.cs`
- `https://raw.githubusercontent.com/Lidarr/Lidarr/develop/src/NzbDrone.Core/Messaging/Commands/Command.cs`

Expected POST body:

```json
{ "name": "AlbumSearch", "albumIds": [123] }
```

### Artist search

Confirmed:

- Command class: `ArtistSearchCommand`
- API command name: `ArtistSearch`
- C# property: `ArtistId`
- C# property type: `int`

Source URLs:

- `https://raw.githubusercontent.com/Lidarr/Lidarr/develop/src/NzbDrone.Core/IndexerSearch/ArtistSearchCommand.cs`
- `https://raw.githubusercontent.com/Lidarr/Lidarr/develop/src/NzbDrone.Core/Messaging/Commands/Command.cs`

Expected POST body:

```json
{ "name": "ArtistSearch", "artistId": 45 }
```

Lidarr summary:

- `AlbumSearch` + `AlbumIds`
- `ArtistSearch` + `ArtistId`

---

## 4. Readarr

### Book search

Confirmed:

- Command class: `BookSearchCommand`
- API command name: `BookSearch`
- C# property: `BookIds`
- C# property type: `List<int>`

Source URLs:

- `https://raw.githubusercontent.com/Readarr/Readarr/develop/src/NzbDrone.Core/IndexerSearch/BookSearchCommand.cs`
- `https://raw.githubusercontent.com/Readarr/Readarr/develop/src/NzbDrone.Core/Messaging/Commands/Command.cs`

Expected POST body:

```json
{ "name": "BookSearch", "bookIds": [123] }
```

### Author search

Confirmed:

- Command class: `AuthorSearchCommand`
- API command name: `AuthorSearch`
- C# property: `AuthorId`
- C# property type: `int`

Source URLs:

- `https://raw.githubusercontent.com/Readarr/Readarr/develop/src/NzbDrone.Core/IndexerSearch/AuthorSearchCommand.cs`
- `https://raw.githubusercontent.com/Readarr/Readarr/develop/src/NzbDrone.Core/Messaging/Commands/Command.cs`

Expected POST body:

```json
{ "name": "AuthorSearch", "authorId": 45 }
```

Readarr summary:

- `BookSearch` + `BookIds`
- `AuthorSearch` + `AuthorId`

---

## 5. Whisparr

### Episode search

Confirmed:

- Command class: `EpisodeSearchCommand`
- API command name: `EpisodeSearch`
- C# property: `EpisodeIds`
- C# property type: `List<int>`

Source URLs:

- `https://raw.githubusercontent.com/Whisparr/Whisparr/develop/src/NzbDrone.Core/IndexerSearch/EpisodeSearchCommand.cs`
- `https://raw.githubusercontent.com/Whisparr/Whisparr/develop/src/NzbDrone.Core/Messaging/Commands/Command.cs`

Expected POST body:

```json
{ "name": "EpisodeSearch", "episodeIds": [123] }
```

### Season search

Confirmed:

- Command class: `SeasonSearchCommand`
- API command name: `SeasonSearch`
- C# properties:
    - `SeriesId`
    - `SeasonNumber`
- C# property types:
    - `int`
    - `int`

Source URLs:

- `https://raw.githubusercontent.com/Whisparr/Whisparr/develop/src/NzbDrone.Core/IndexerSearch/SeasonSearchCommand.cs`
- `https://raw.githubusercontent.com/Whisparr/Whisparr/develop/src/NzbDrone.Core/Messaging/Commands/Command.cs`

Expected POST body:

```json
{ "name": "SeasonSearch", "seriesId": 45, "seasonNumber": 2 }
```

Whisparr summary:

- `EpisodeSearch` + `EpisodeIds`
- `SeasonSearch` + `SeriesId` / `SeasonNumber`

---

## Final consolidated matrix

```json
{
    "Sonarr": {
        "EpisodeSearch": {
            "commandClass": "EpisodeSearchCommand",
            "csharpProperties": ["EpisodeIds"],
            "body": { "name": "EpisodeSearch", "episodeIds": [123] }
        },
        "SeasonSearch": {
            "commandClass": "SeasonSearchCommand",
            "csharpProperties": ["SeriesId", "SeasonNumber"],
            "body": { "name": "SeasonSearch", "seriesId": 45, "seasonNumber": 2 }
        }
    },
    "Radarr": {
        "MoviesSearch": {
            "commandClass": "MoviesSearchCommand",
            "csharpProperties": ["MovieIds"],
            "body": { "name": "MoviesSearch", "movieIds": [123] }
        }
    },
    "Lidarr": {
        "AlbumSearch": {
            "commandClass": "AlbumSearchCommand",
            "csharpProperties": ["AlbumIds"],
            "body": { "name": "AlbumSearch", "albumIds": [123] }
        },
        "ArtistSearch": {
            "commandClass": "ArtistSearchCommand",
            "csharpProperties": ["ArtistId"],
            "body": { "name": "ArtistSearch", "artistId": 45 }
        }
    },
    "Readarr": {
        "BookSearch": {
            "commandClass": "BookSearchCommand",
            "csharpProperties": ["BookIds"],
            "body": { "name": "BookSearch", "bookIds": [123] }
        },
        "AuthorSearch": {
            "commandClass": "AuthorSearchCommand",
            "csharpProperties": ["AuthorId"],
            "body": { "name": "AuthorSearch", "authorId": 45 }
        }
    },
    "Whisparr": {
        "EpisodeSearch": {
            "commandClass": "EpisodeSearchCommand",
            "csharpProperties": ["EpisodeIds"],
            "body": { "name": "EpisodeSearch", "episodeIds": [123] }
        },
        "SeasonSearch": {
            "commandClass": "SeasonSearchCommand",
            "csharpProperties": ["SeriesId", "SeasonNumber"],
            "body": { "name": "SeasonSearch", "seriesId": 45, "seasonNumber": 2 }
        }
    }
}
```

## Practical implementation note

If your integration sends POST requests to the command endpoint, these are the request bodies to use:

```json
{"name":"EpisodeSearch","episodeIds":[123]}
{"name":"SeasonSearch","seriesId":45,"seasonNumber":2}
{"name":"MoviesSearch","movieIds":[123]}
{"name":"AlbumSearch","albumIds":[123]}
{"name":"ArtistSearch","artistId":45}
{"name":"BookSearch","bookIds":[123]}
{"name":"AuthorSearch","authorId":45}
```

## Final caveat

This file is fully definitive for:

- command class names
- derived command names
- C# property names

The JSON field casing shown here is the expected API-facing lower-camel form corresponding to those confirmed C# properties.
