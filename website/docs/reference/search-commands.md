---
sidebar_position: 4
title: Search Commands
description: The *arr API command Houndarr sends for each search pass, per app type.
---

# Search Commands

The API command Houndarr issues when the engine triggers a search,
per *arr app type. "Default" is the mode shipped with a fresh
install; the "Advanced" mode, when available, is selected in the
instance's search mode field.

## Per-app command contract

| App | Default command | Advanced command (missing pass) | Upgrade pass |
|-----|-----------------|----------------------------------|--------------|
| Radarr | `MoviesSearch` with `movieIds` | n/a (always movie-level) | `MoviesSearch` |
| Sonarr | `EpisodeSearch` with `episodeIds` | `SeasonSearch` with `seriesId` + `seasonNumber` | `EpisodeSearch` or `SeasonSearch` |
| Lidarr | `AlbumSearch` with `albumIds` | `ArtistSearch` with `artistId` | `AlbumSearch` or `ArtistSearch` |
| Readarr | `BookSearch` with `bookIds` | `AuthorSearch` with `authorId` | `BookSearch` or `AuthorSearch` |
| Whisparr v2 | `EpisodeSearch` with `episodeIds` | `SeasonSearch` with `seriesId` + `seasonNumber` | `EpisodeSearch` or `SeasonSearch` |
| Whisparr v3 | `MoviesSearch` with `movieIds` | n/a (always movie-level) | `MoviesSearch` |

Wanted-list reads are restricted to monitored items
(`monitored=true`) for both missing and cutoff passes across every
app type.

## Source of truth

The *arr API contract is vendored in the repo at
`docs/api/*_openapi.json` (Sonarr v3, Radarr v3, Whisparr v2,
Whisparr v3, Lidarr v1, Readarr v1). See `docs/api/README.md` for
how to use the vendored specs.

## Upgrade pass endpoint differences

The upgrade pass reuses the same search commands above but reads
the full library endpoint rather than the `wanted/*` APIs, because
upgrade candidates are items that already have files and meet
cutoff. They are not "wanted" by the *arr definition.
